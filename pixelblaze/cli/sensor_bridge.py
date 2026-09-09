"""Virtual Pixelblaze Sensor Board — feed sensor-shaped readings into patterns.

The Pixelblaze Sensor Expansion Board publishes a specific set of pattern
globals (`frequencyData[32]`, `energyAverage`, `maxFrequency`,
`maxFrequencyMagnitude`). This module fills those same globals from
alternative host-side sources, so sound-reactive patterns work with no
physical SB attached.

Currently implemented: `SoundBridge` — captures audio from a system input
device (mic, loopback, etc.), computes 32 log-spaced frequency bins on the
same center-frequency grid the SB uses, and streams them to a Pixelblaze at
a configurable frame rate over one of two transports:

`UdpSink` (the default) sends the same UDP datagrams a sync-group leader
uses to share its sensor board with the group — see `SensorPacket` in
`pixelblaze.pixelblaze` for the wire format. The device does no JSON
parsing, so streaming costs it no pattern framerate, and a single broadcast
feeds every Pixelblaze on the network at once. Suggested by the library's
maintainer (zranger1) on PR #30, after his Java testbed for the same
protocol, https://github.com/zranger1/SoundServerFX.

The one catch, verified on firmware 3.70: the firmware binds a pattern's
sensor globals to their source when the *pattern* loads. A pattern that was
already running when the frames start arriving keeps simulating and ignores
them, which looks exactly like the packets being malformed. So the bridge
reloads the active pattern once the frames are flowing (`on_flowing`), which
is what SoundServerFX's README is getting at when it says to switch to the
audio-reactive pattern last. Nothing else is required — unicast or
broadcast, any `senderTime`, any source port, no sync group.

`VarsSink` sends the readings as pattern variables over the websocket with
`setActiveVariables`. It reaches a Pixelblaze whose sensor sources are
pinned to local, and it works on any pattern that exports the variables by
name whether or not it is a sensor board pattern — but the device parses
JSON on its render thread for every frame, which costs framerate on complex
patterns and gets worse on a marginal wifi link.

Both transports carry the same reading contract:
  frequencyData: [32 floats]  # magnitudes, nominally 0.0-1.0
  energyAverage: float        # overall loudness, nominally 0.0-1.0
  maxFrequency: float         # Hz
  maxFrequencyMagnitude: float

Notes on the vars transport, verified on firmware 3.51 and 3.70 (the same
device, before and after upgrading, 2026-09-08): arrays must be sent whole.
`{"setVars": {"frequencyData": [..32..]}}` lands in the pattern, while the
indexed form `{"setVars": {"frequencyData[3]": v}}` is silently dropped on
both — `light` flips to 0, the bins never move, and the pattern's last
simulated frame sits there frozen. (`getVars` returns arrays whole too.)
The UDP transport has no such wrinkle; it carries the whole frame always.
"""

import threading
import time
import numpy as np
import sounddevice as sd

from pixelblaze.pixelblaze import SensorSender

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

    # Bin into 32 PB bins.
    #
    # The low bands are narrower than the FFT's resolution: at the default
    # 1024 samples / 48kHz the bins are 46.9Hz apart, while band 0 spans
    # 32.5-43.3Hz and band 2 spans 61.2-86.6Hz — neither contains an FFT bin
    # at all, so both used to read a flat 0 forever and the bass end of every
    # spectrum pattern was dead. Interpolate the spectrum at the band's center
    # frequency when that happens. (The real sensor board solves this with a
    # second, downsampled 400Hz FFT for its low six bands.)
    bins = np.zeros(32)
    for b in range(32):
        lo = PB_BIN_EDGES[b]
        hi = PB_BIN_EDGES[b + 1]
        mask = (freqs >= lo) & (freqs < hi)
        if mask.any():
            bins[b] = spectrum[mask].mean()
        else:
            bins[b] = np.interp(PB_BIN_CENTERS[b], freqs, spectrum)

    # Aggregate metrics. The peak search starts above the lowest band edge:
    # DC and sub-audio rumble otherwise win it outright on most inputs (a mic
    # with any DC offset pins maxFrequency to 0Hz), which is why the sensor
    # board's own firmware starts its search at bin 1 rather than bin 0.
    energy_avg = float(spectrum.mean())
    audible = freqs >= PB_BIN_EDGES[0]
    peak_idx = int(np.argmax(np.where(audible, spectrum, 0.0)))
    max_freq = float(freqs[peak_idx])
    max_freq_mag = float(spectrum[peak_idx])

    return {
        "frequencyData": bins.tolist(),
        "energyAverage": energy_avg,
        "maxFrequency": max_freq,
        "maxFrequencyMagnitude": max_freq_mag,
    }


class VarsSink:
    """Sends sensor readings as pattern variables over the websocket.

    The original transport for this bridge, kept as an alternative to
    `UdpSink`: it reaches a Pixelblaze whose sensor sources are pinned to
    local, and it works with any pattern that exports the variables by name.
    The cost is that the device parses a JSON frame on its render thread every
    time, which shows up as lost framerate on complex patterns.
    """

    def __init__(self, pb):
        self.pb = pb

    def describe(self) -> str:
        return f"setVars over websocket → {self.pb.ipAddress}"

    def start(self):
        # Signal to PB patterns that real sensor data is available
        self.pb.setActiveVariables({"light": 0})

    def send(self, data: dict):
        # frequencyData goes as one array: firmware applies whole arrays and
        # silently ignores "frequencyData[i]" keys (see module docstring).
        # Round to 6 decimals — finer than the device's 16.16 fixed point, a
        # third the JSON of a full double at `fps` frames per second.
        self.pb.setActiveVariables({
            "frequencyData": [round(v, 6) for v in data["frequencyData"]],
            "energyAverage": round(data["energyAverage"], 6),
            "maxFrequency": round(data["maxFrequency"], 6),
            "maxFrequencyMagnitude": round(data["maxFrequencyMagnitude"], 6),
            "light": 0,
        })

    def stop(self):
        # Reset sentinels so patterns know sensor data stopped and go back to
        # simulating sound. Nothing expires a variable we set, so we must.
        self.pb.setActiveVariables({
            "light": -1,
            "maxFrequencyMagnitude": -1,
            "energyAverage": -1,
        })


class UdpSink:
    """Sends sensor readings as sensor-board UDP datagrams (the default transport).

    This is the protocol a Pixelblaze sync group leader uses to share its own
    Sensor Expansion Board with the rest of the group, so the readings arrive
    the way the firmware already expects them: no JSON, no websocket, no
    parsing on the render thread, and one broadcast feeds every Pixelblaze on
    the network at once.

    A Pixelblaze only uses the readings if its source preference for them is
    "prefer remote" (`pb sensor sources --prefer remote`), or if it has no
    local sensor board to prefer — and if its pattern was loaded *after* the
    frames started arriving, which is what `--rebind` takes care of.
    """

    SILENCE_FRAMES = 3

    def __init__(self, targets):
        self.sender = SensorSender(targets)

    def describe(self) -> str:
        return f"sensor board UDP → {', '.join(self.sender.targets)}:{self.sender.port}"

    def start(self):
        pass

    def send(self, data: dict):
        self.sender.send(
            frequencyData=data["frequencyData"],
            energyAverage=data["energyAverage"],
            maxFrequency=data["maxFrequency"],
            maxFrequencyMagnitude=data["maxFrequencyMagnitude"],
        )

    def stop(self):
        # A pattern bound to remote sensor data keeps the last frame it was
        # sent — on 3.70 it was still showing it 100s after the stream stopped,
        # and reloading the pattern doesn't put it back to simulating either.
        # So say "silence" on the way out instead of freezing mid-spectrum.
        # Repeated because this is UDP and the last packet may not arrive.
        for _ in range(self.SILENCE_FRAMES):
            self.sender.send()
        self.sender.close()


class SoundBridge:
    """Captures audio and pushes FFT results to a Pixelblaze through a transport sink."""

    def __init__(self, sink, device_idx: int, sample_rate: int, block_size: int,
                 fps: int, gain: float = 1.0, noise_gate: float = 0.0,
                 log_scale: bool = False, agc: bool = False, on_flowing=None):
        self.sink = sink
        # Called once, after a few frames have gone out. The UDP transport
        # needs this: a Pixelblaze binds a pattern's sensor globals when the
        # pattern loads, so the pattern has to be reloaded once data is
        # actually arriving or it goes on simulating (see UdpSink).
        self.on_flowing = on_flowing
        self.device_idx = device_idx
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.fps = fps
        self.gain = gain
        self.noise_gate = noise_gate
        self.log_scale = log_scale
        self.agc = agc

        # Enough frames that the device has certainly seen some, but a small
        # fraction of a second so the pattern reload isn't a visible pause.
        self.flowing_after = max(1, fps // 4)

        self._latest = None
        self._lock = threading.Lock()
        self._running = False
        self._frame_count = 0
        self._start_time = 0

        # Peak-hold across each log interval so transients (claps) are visible
        self._peak_energy = 0.0
        self._peak_mag = 0.0
        self._peak_freq = 0.0

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

        self.sink.start()

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
                self.sink.stop()

    def _push_frame(self):
        with self._lock:
            data = self._latest
            self._latest = None

        if data is None:
            return

        self.sink.send(data)

        if self.on_flowing is not None and self._frame_count >= self.flowing_after:
            callback, self.on_flowing = self.on_flowing, None
            callback()

        if data["energyAverage"] > self._peak_energy:
            self._peak_energy = data["energyAverage"]
        if data["maxFrequencyMagnitude"] > self._peak_mag:
            self._peak_mag = data["maxFrequencyMagnitude"]
            self._peak_freq = data["maxFrequency"]

        self._frame_count += 1
        if self._frame_count % self.fps == 0:
            elapsed = time.time() - self._start_time
            actual_fps = self._frame_count / elapsed if elapsed > 0 else 0
            agc_str = f" agc={self._agc_level:.1f}x" if self.agc else ""
            import sys
            print(f"\r  {self._frame_count} frames, {actual_fps:.1f} fps, "
                  f"energy_peak={self._peak_energy:.4f} "
                  f"maxFreqMag_peak={self._peak_mag:.4f} "
                  f"maxFreq={self._peak_freq:.0f}Hz{agc_str}    ",
                  end="", file=sys.stderr, flush=True)
            self._peak_energy = 0.0
            self._peak_mag = 0.0
            self._peak_freq = 0.0
