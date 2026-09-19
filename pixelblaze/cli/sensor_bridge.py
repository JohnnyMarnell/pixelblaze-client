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

It also measures the input's loudness in LUFS (`loudness.py`) and watches for
the two things a spectrum cannot show you: digital silence, and an input that
opens and then delivers nothing at all. `spectrum.py` draws both, live.

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

from pixelblaze.cli.loudness import SILENT, LoudnessMeter
from pixelblaze.pixelblaze import SensorPacket, SensorSender

# `sounddevice` is imported where it is used, not here: it needs PortAudio, and
# `--tone` is a complete run of this whole module without either. A box with no
# sound stack at all can still exercise the bridge, the meter and the display.

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
    import sounddevice as sd
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

    targets = None
    """One websocket, so there is no address list for the display to count."""

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

    def __init__(self, targets, senderId: int = None):
        self.sender = SensorSender(targets, senderId=senderId)

    @property
    def targets(self) -> list:
        """The addresses frames are going to, live: `add_target` while running."""
        return self.sender.targets

    def add_target(self, address: str) -> bool:
        """Start sending to one more device. True if it wasn't already a target."""
        if address in self.sender.targets:
            return False
        self.sender.targets.append(address)
        return True

    def remove_target(self, address: str) -> bool:
        """Stop sending to a device, after a frame of silence so its pattern
        doesn't latch whatever was playing (see `stop`)."""
        if address not in self.sender.targets:
            return False
        for _ in range(self.SILENCE_FRAMES):
            self.sender.socket.sendto(SensorPacket.pack(senderId=self.sender.senderId),
                                      (address, self.sender.port))
        self.sender.targets.remove(address)
        return True

    def describe(self) -> str:
        targets = self.sender.targets
        shown = ', '.join(targets[:4]) + (f", +{len(targets) - 4} more" if len(targets) > 4 else "")
        return (f"sensor board UDP → {len(targets)} device(s) on :{self.sender.port} "
                f"({shown})")

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




class MicSource:
    """A capture from a system input device, as a context manager."""

    def __init__(self, device_idx: int, sample_rate: int, block_size: int,
                 channels: int, callback):
        self.device_idx = device_idx
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.channels = channels
        self.callback = callback
        self._stream = None

    def __enter__(self):
        import sounddevice as sd
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            device=self.device_idx,
            channels=self.channels,
            dtype='float32',
            callback=self.callback,
        )
        self._stream.__enter__()
        return self

    def __exit__(self, *exc):
        return self._stream.__exit__(*exc)


class ToneSource:
    """A generated sine instead of a capture.

    No device, no permission prompt, and no PortAudio -- which makes it the one
    way to exercise this whole module (bins, loudness, display, datagrams) on a
    box with no sound stack at all, and a known-good signal to point a
    Pixelblaze at when the question is whether the *device* is listening.
    """

    def __init__(self, hz: float, sample_rate: int, block_size: int,
                 channels: int, callback, amplitude: float = 0.25):
        self.hz = hz
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.channels = channels
        self.callback = callback
        self.amplitude = amplitude
        self._phase = 0.0
        self._running = False
        self._thread = None

    def __enter__(self):
        self._running = True
        self._thread = threading.Thread(target=self._generate, name='pb-tone', daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return False

    def _generate(self):
        period = self.block_size / self.sample_rate
        step = 2 * np.pi * self.hz / self.sample_rate
        due = time.monotonic()
        while self._running:
            phases = self._phase + step * np.arange(self.block_size)
            self._phase = float((phases[-1] + step) % (2 * np.pi))
            block = (self.amplitude * np.sin(phases)).astype(np.float32)
            block = np.repeat(block[:, None], self.channels, axis=1)
            self.callback(block, self.block_size, None, None)
            due += period
            time.sleep(max(0.0, due - time.monotonic()))


class SoundBridge:
    """Captures audio and pushes FFT results to a Pixelblaze through a transport sink.

    Two things here are not about the spectrum at all, and both exist because
    the failure they catch is otherwise indistinguishable from a quiet room:

    **Silence** is the input delivering blocks of digital zeroes. That is not an
    error -- it is what a loopback with nothing playing sounds like, and it is
    the truth about the room, so it is sent. It is reported (`display.silent`)
    because "is anything playing?" is the first question when the lights don't
    move.

    **A stall** is no blocks arriving at all for `stall_timeout` seconds while
    the stream is nominally running: an aggregate device with a missing member,
    an interface unplugged mid-stream, an input another app holds exclusively.
    Nothing goes out, the pattern sits on its last frame, and the device side
    cannot see the difference. So `run()` stops and leaves `stalled_for` set,
    and the CLI fails loudly rather than sitting there looking busy.
    """

    #: The longest the run loop will sleep. It wakes sooner when the next frame
    #: is due; this bounds how late the display's 30 fps and the stall watchdog
    #: can be when the frame rate itself is slow.
    TICK = 1.0 / 60

    def __init__(self, sink, device_idx: int, sample_rate: int, block_size: int,
                 fps: int, gain: float = 1.0, noise_gate: float = 0.0,
                 log_scale: bool = False, agc: bool = False, on_flowing=None,
                 display=None, seconds: float = None, channels: int = 1,
                 tone: float = None, stall_timeout: float = 3.0,
                 silence_timeout: float = 2.0):
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
        self.channels = channels
        self.tone = tone
        self.seconds = seconds
        self.stall_timeout = stall_timeout
        self.silence_timeout = silence_timeout

        #: A spectrum / status display (see `pixelblaze.cli.spectrum`), or None.
        self.display = display

        #: Set when the run ended because no audio arrived -- see the class docs.
        self.stalled_for = None

        # Loudness is measured on the input as captured: before `gain`, which
        # scales what the pattern is sent, not the sound. And on the channels as
        # they came, because BS.1770 sums channel power -- a mono mixdown first
        # reads up to 3 dB low and cancels out-of-phase content outright.
        self.meter = LoudnessMeter(sample_rate)
        self.loudness = SILENT

        # Enough frames that the device has certainly seen some, but a small
        # fraction of a second so the pattern reload isn't a visible pause.
        self.flowing_after = max(1, fps // 4)

        self._latest = None
        self._lock = threading.Lock()
        self._running = False
        self._frame_count = 0
        self._start_time = 0
        self._last_block_at = 0.0
        self._silent_since = None
        self._silent = False

        # AGC state
        self._agc_level = 1.0       # current auto-gain multiplier
        self._agc_target = 0.15     # target peak level for frequency bins
        self._agc_attack = 0.3      # how fast gain increases (per second)
        self._agc_release = 2.0     # how fast gain decreases (per second)

    @property
    def frames_sent(self) -> int:
        return self._frame_count

    @property
    def is_silent(self) -> bool:
        return self._silent

    def _say(self, line: str):
        """Above the live spectrum, or on its own line."""
        if self.display is not None:
            self.display.log(line)
        else:
            import sys
            print(line, file=sys.stderr, flush=True)

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self._say(str(status))

        self._last_block_at = time.monotonic()
        self.meter.process(indata)
        self.loudness = self.meter.loudness
        self._track_silence(indata)

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

    def _track_silence(self, block):
        """Digital silence -- nothing above -120 dBFS -- for `silence_timeout`."""
        quiet = bool(np.max(np.abs(block)) < 1e-6) if block.size else True
        if not quiet:
            self._silent_since = None
            self._silent = False
            return
        if self._silent_since is None:
            self._silent_since = time.monotonic()
        elif time.monotonic() - self._silent_since >= self.silence_timeout:
            self._silent = True

    def _source(self):
        if self.tone:
            return ToneSource(self.tone, self.sample_rate, self.block_size,
                              self.channels, self._audio_callback)
        return MicSource(self.device_idx, self.sample_rate, self.block_size,
                         self.channels, self._audio_callback)

    def stop(self):
        """Ask `run()` to wind up. Safe from another thread."""
        self._running = False

    def run(self):
        """Start capturing and pushing. Blocks until Ctrl-C, `seconds`, a stall
        or `stop()`. Check `stalled_for` afterwards."""
        self._running = True
        self._start_time = time.monotonic()
        self._last_block_at = self._start_time
        self._frame_count = 0
        self.stalled_for = None
        self.meter.reset()

        push_interval = 1.0 / max(self.fps, 1)
        next_push = self._start_time
        fps_at = self._start_time
        fps_frames = 0

        self.sink.start()
        if self.display is not None:
            self.display.start()

        with self._source():
            try:
                while self._running:
                    now = time.monotonic()

                    if now >= next_push:
                        # Advance rather than reset, so the rate is the rate;
                        # but never try to make up a whole backlog at once.
                        next_push = max(now, next_push) + push_interval
                        self._push_frame()

                    if self.display is not None:
                        if now - fps_at >= 1.0:
                            self.display.fps = self._frame_count - fps_frames
                            fps_frames = self._frame_count
                            fps_at = now
                        self.display.loudness = self.loudness
                        self.display.silent = self._silent
                        if self.sink.targets is not None:
                            self.display.targets = len(self.sink.targets)
                        self.display.draw()

                    quiet_for = now - self._last_block_at
                    if quiet_for >= self.stall_timeout:
                        self.stalled_for = quiet_for
                        break

                    if self.seconds is not None and now - self._start_time >= self.seconds:
                        break

                    # Sleep to the *next* thing due, not a fixed tick: a flat
                    # 1/60 s nap rounds a 40 fps push interval up to 34 ms and
                    # the frame rate silently reads 30.
                    deadline = min(next_push, now + self.TICK)
                    time.sleep(max(0.0, deadline - time.monotonic()))
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
        self._frame_count += 1

        if self.display is not None:
            self.display.update(data)

        if self.on_flowing is not None and self._frame_count >= self.flowing_after:
            callback, self.on_flowing = self.on_flowing, None
            callback()
