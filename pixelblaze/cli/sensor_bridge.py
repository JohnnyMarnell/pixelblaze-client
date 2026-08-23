"""Virtual Pixelblaze Sensor Board — feed sensor-shaped vars into patterns.

The Pixelblaze Sensor Expansion Board publishes a specific set of pattern
globals (`frequencyData[32]`, `energyAverage`, `maxFrequency`,
`maxFrequencyMagnitude`). This module fills those same globals from
alternative host-side sources, so sound-reactive patterns work with no
physical SB attached.

Currently implemented: `SoundBridge` — captures audio from a system input
device (mic, loopback, etc.), computes 32 log-spaced frequency bins on the
same center-frequency grid the SB uses, and streams them to a Pixelblaze
via `setActiveVariables` at a configurable frame rate.

Future sources can share the same var-push contract:
  variables = {
    "frequencyData[0..31]": floats,
    "energyAverage": float,
    "maxFrequency": Hz,
    "maxFrequencyMagnitude": float,
    "light": 0,   # sentinel that a real sensor source is active
  }
"""

import threading
import time
import numpy as np
import sounddevice as sd

# PB sensor board bin center frequencies (from stock pattern source)
PB_BIN_CENTERS = np.array([
    37.5, 50, 75, 100, 125, 163, 195, 234, 312, 391, 469, 586,
    703, 859, 976, 1170, 1370, 1560, 1800, 2070, 2380, 2730,
    3120, 3590, 4100, 4650, 5310, 6020, 6840, 7770, 8790, 9960
])

# Bin edges (geometric mean between adjacent centers)
PB_BIN_EDGES = np.zeros(33)
PB_BIN_EDGES[0] = PB_BIN_CENTERS[0] / (PB_BIN_CENTERS[1] / PB_BIN_CENTERS[0]) ** 0.5
for i in range(31):
    PB_BIN_EDGES[i + 1] = (PB_BIN_CENTERS[i] * PB_BIN_CENTERS[i + 1]) ** 0.5
PB_BIN_EDGES[32] = PB_BIN_CENTERS[31] * (PB_BIN_CENTERS[31] / PB_BIN_CENTERS[30]) ** 0.5


def find_device(name_substring: str) -> tuple[int, dict]:
    """Find an input device by name substring."""
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if name_substring.lower() in dev['name'].lower() and dev['max_input_channels'] > 0:
            return i, dev
    raise RuntimeError(
        f"No input device matching '{name_substring}'. Available:\n" +
        "\n".join(f"  [{i}] {d['name']} (in={d['max_input_channels']})"
                  for i, d in enumerate(devices) if d['max_input_channels'] > 0)
    )


def fft_to_pb_bins(samples: np.ndarray, sample_rate: int,
                   gain: float = 1.0, noise_gate: float = 0.0) -> dict:
    """Convert audio samples to PB sensor board format.

    Args:
        samples: Raw audio samples (float32, mono or multi-channel)
        sample_rate: Sample rate in Hz
        gain: Linear gain multiplier applied to spectrum magnitudes
        noise_gate: Values below this threshold are zeroed

    Returns dict with keys matching PB export vars:
        frequencyData: list of 32 floats
        energyAverage: float
        maxFrequency: float (Hz)
        maxFrequencyMagnitude: float
    """
    # Mono mixdown
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    n = len(samples)

    # Hann window + FFT
    window = np.hanning(n)
    spectrum = np.abs(np.fft.rfft(samples * window)) / n
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)

    # Apply gain
    spectrum *= gain

    # Noise gate
    if noise_gate > 0:
        spectrum[spectrum < noise_gate] = 0

    # Bin into 32 PB bins
    bins = np.zeros(32)
    for b in range(32):
        lo = PB_BIN_EDGES[b]
        hi = PB_BIN_EDGES[b + 1]
        mask = (freqs >= lo) & (freqs < hi)
        if mask.any():
            bins[b] = spectrum[mask].mean()

    # Aggregate metrics
    energy_avg = float(spectrum.mean())
    peak_idx = spectrum.argmax()
    max_freq = float(freqs[peak_idx])
    max_freq_mag = float(spectrum[peak_idx])

    return {
        "frequencyData": bins.tolist(),
        "energyAverage": energy_avg,
        "maxFrequency": max_freq,
        "maxFrequencyMagnitude": max_freq_mag,
    }


class SoundBridge:
    """Captures audio and pushes FFT results to a Pixelblaze."""

    def __init__(self, pb, device_idx: int, sample_rate: int, block_size: int,
                 fps: int, gain: float = 1.0, noise_gate: float = 0.0,
                 log_scale: bool = False, agc: bool = False):
        self.pb = pb
        self.device_idx = device_idx
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.fps = fps
        self.gain = gain
        self.noise_gate = noise_gate
        self.log_scale = log_scale
        self.agc = agc

        self._latest = None
        self._lock = threading.Lock()
        self._running = False
        self._frame_count = 0
        self._start_time = 0

        # AGC state
        self._agc_level = 1.0       # current auto-gain multiplier
        self._agc_target = 0.15     # target peak level for frequency bins
        self._agc_attack = 0.3      # how fast gain increases (per second)
        self._agc_release = 2.0     # how fast gain decreases (per second)

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            import sys
            print(status, file=sys.stderr)

        effective_gain = self.gain * (self._agc_level if self.agc else 1.0)
        result = fft_to_pb_bins(indata.copy(), self.sample_rate,
                                gain=effective_gain, noise_gate=self.noise_gate)

        # Log scale: compress dynamic range
        if self.log_scale:
            for i in range(32):
                v = result["frequencyData"][i]
                result["frequencyData"][i] = np.log1p(v * 1000) / np.log1p(1000) if v > 0 else 0
            mag = result["maxFrequencyMagnitude"]
            result["maxFrequencyMagnitude"] = float(np.log1p(mag * 1000) / np.log1p(1000)) if mag > 0 else 0
            ea = result["energyAverage"]
            result["energyAverage"] = float(np.log1p(ea * 1000) / np.log1p(1000)) if ea > 0 else 0

        # AGC: adjust gain so peak bin stays near target
        if self.agc:
            peak_val = max(result["frequencyData"])
            if peak_val > 0:
                ratio = self._agc_target / peak_val
                dt = 1.0 / max(self.fps, 1)
                if ratio > 1:
                    # Too quiet — increase gain slowly
                    self._agc_level += (ratio - 1) * self._agc_attack * dt
                else:
                    # Too loud — decrease gain faster
                    self._agc_level += (ratio - 1) * self._agc_release * dt
                self._agc_level = max(0.1, min(self._agc_level, 500.0))

        with self._lock:
            self._latest = result

    def run(self):
        """Start capturing and pushing. Blocks until KeyboardInterrupt."""
        self._running = True
        self._start_time = time.time()
        self._frame_count = 0

        push_interval = 1.0 / self.fps

        # Signal to PB patterns that real sensor data is available
        self.pb.setActiveVariables({"light": 0})

        with sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            device=self.device_idx,
            channels=1,
            dtype='float32',
            callback=self._audio_callback,
        ):
            try:
                while self._running:
                    time.sleep(push_interval)
                    self._push_frame()
            except KeyboardInterrupt:
                pass
            finally:
                self._running = False
                # Reset sentinel so patterns know sensor data stopped
                self.pb.setActiveVariables({
                    "light": -1,
                    "maxFrequencyMagnitude": -1,
                    "energyAverage": -1,
                })

    def _push_frame(self):
        with self._lock:
            data = self._latest
            self._latest = None

        if data is None:
            return

        # Build setVars payload
        variables = {
            "energyAverage": data["energyAverage"],
            "maxFrequency": data["maxFrequency"],
            "maxFrequencyMagnitude": data["maxFrequencyMagnitude"],
            "light": 0,
        }
        for i, val in enumerate(data["frequencyData"]):
            variables[f"frequencyData[{i}]"] = val

        self.pb.setActiveVariables(variables)

        self._frame_count += 1
        if self._frame_count % self.fps == 0:
            elapsed = time.time() - self._start_time
            actual_fps = self._frame_count / elapsed if elapsed > 0 else 0
            agc_str = f" agc={self._agc_level:.1f}x" if self.agc else ""
            import sys
            print(f"\r  {self._frame_count} frames, {actual_fps:.1f} fps, "
                  f"energy={data['energyAverage']:.4f} "
                  f"maxFreqMag={data['maxFrequencyMagnitude']:.4f} "
                  f"maxFreq={data['maxFrequency']:.0f}Hz{agc_str}    ",
                  end="", file=sys.stderr, flush=True)
