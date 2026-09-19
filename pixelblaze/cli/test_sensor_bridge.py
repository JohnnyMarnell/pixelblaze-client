#!/usr/bin/env python3
"""Unit tests for the `pb sensor sound` transports.

No Pixelblaze and no audio hardware required — the sinks are driven directly
with a frame of the shape `SoundBridge` produces.

    python3 -m pytest pixelblaze/cli/test_sensor_bridge.py
"""

import socket
import time

import pytest

from pixelblaze.pixelblaze import SensorPacket
from pixelblaze.cli.sensor_bridge import UdpSink, VarsSink


def sampleFrame():
    """A frame in the shape `fft_to_pb_bins()` returns."""
    return {
        "frequencyData": [i / 64 for i in range(32)],
        "energyAverage": 0.0625,
        "maxFrequency": 1170.0,
        "maxFrequencyMagnitude": 0.75,
    }


class FakePixelblaze:
    """Records what a `VarsSink` would send over the websocket."""

    ipAddress = "192.168.0.99"

    def __init__(self):
        self.sent = []

    def setActiveVariables(self, variables):
        self.sent.append(variables)


def test_vars_sink_sends_whole_arrays_and_the_sensor_sentinel():
    """frequencyData must go as one array: the firmware drops "frequencyData[i]" keys."""
    pb = FakePixelblaze()
    sink = VarsSink(pb)

    sink.start()
    sink.send(sampleFrame())
    sink.stop()

    assert pb.sent[0] == {"light": 0}

    frame = pb.sent[1]
    assert set(frame) == {"frequencyData", "energyAverage", "maxFrequency",
                          "maxFrequencyMagnitude", "light"}
    assert frame["frequencyData"] == [i / 64 for i in range(32)]
    assert frame["light"] == 0
    assert not any("[" in key for key in frame)

    # Sentinels put patterns back into simulating sound; nothing else expires them.
    assert pb.sent[2] == {"light": -1, "maxFrequencyMagnitude": -1, "energyAverage": -1}


def test_udp_sink_sends_a_sensor_board_datagram():
    """The same readings arrive as an SB1.0 frame the firmware can consume directly."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5)

        sink = UdpSink(["127.0.0.1"])
        sink.sender.port = receiver.getsockname()[1]
        try:
            sink.start()
            sink.send(sampleFrame())
        finally:
            sink.stop()

        decoded = SensorPacket.unpack(receiver.recv(256))
        assert decoded["frequencyData"] == [i / 64 for i in range(32)]
        assert decoded["energyAverage"] == 0.0625
        assert decoded["maxFrequency"] == 1170.0
        assert decoded["maxFrequencyMagnitude"] == 0.75
    finally:
        receiver.close()


def test_sinks_describe_where_they_send():
    """`pb sensor sound` prints this, so it has to name the actual destination."""
    assert "192.168.0.99" in VarsSink(FakePixelblaze()).describe()

    sink = UdpSink(["127.0.0.1", "255.255.255.255"])
    try:
        described = sink.describe()
    finally:
        sink.stop()
    assert "127.0.0.1" in described and "255.255.255.255" in described and "1889" in described


def toneFrame(hz, sample_rate=48000, n=1024, dc=0.0):
    """A pure tone, optionally riding on a DC offset like a real mic input."""
    import numpy as np
    t = np.arange(n) / sample_rate
    return (0.5 * np.sin(2 * np.pi * hz * t) + dc).astype("float32")


def test_no_frequency_bin_is_structurally_dead():
    """Bands narrower than the FFT resolution used to read a flat 0 forever."""
    from pixelblaze.cli.sensor_bridge import fft_to_pb_bins

    # 1024 samples at 48kHz puts FFT bins 46.9Hz apart; bands 0 (32.5-43.3Hz)
    # and 2 (61.2-86.6Hz) contain none, so they have to be interpolated.
    bins = fft_to_pb_bins(toneFrame(75), 48000)["frequencyData"]

    assert len(bins) == 32
    assert all(v > 0 for v in bins[:6]), f"dead bands: {[i for i, v in enumerate(bins) if v == 0]}"


def test_max_frequency_ignores_dc():
    """A mic with a DC offset used to pin maxFrequency to 0Hz on every frame."""
    from pixelblaze.cli.sensor_bridge import fft_to_pb_bins

    quiet = fft_to_pb_bins(toneFrame(1000, dc=0.0), 48000)
    offset = fft_to_pb_bins(toneFrame(1000, dc=0.4), 48000)

    assert 950 < quiet["maxFrequency"] < 1050
    assert 950 < offset["maxFrequency"] < 1050, "DC won the peak search"


def test_bridge_reports_once_when_frames_are_flowing():
    """UdpSink needs the pattern reloaded after data starts, but only once."""
    from pixelblaze.cli.sensor_bridge import SoundBridge

    calls = []

    class CountingSink(VarsSink):
        pass

    bridge = SoundBridge(CountingSink(FakePixelblaze()), 0, 48000, 1024, fps=8,
                         on_flowing=lambda: calls.append(1))
    for _ in range(20):
        bridge._latest = sampleFrame()
        bridge._push_frame()

    assert calls == [1], f"on_flowing fired {len(calls)} times"
    assert bridge.flowing_after == 2   # fps // 4


def test_udp_sink_sends_silence_on_stop():
    """A pattern holds the last frame forever, so stopping must say 'silence'."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5)

        sink = UdpSink(["127.0.0.1"])
        sink.sender.port = receiver.getsockname()[1]
        sink.start()
        sink.send(sampleFrame())
        sink.stop()

        SensorPacket.unpack(receiver.recv(256))          # the real frame
        for _ in range(UdpSink.SILENCE_FRAMES):
            silence = SensorPacket.unpack(receiver.recv(256))
            assert silence["frequencyData"] == [0.0] * 32
            assert silence["energyAverage"] == 0.0
            assert silence["maxFrequency"] == 0.0
    finally:
        receiver.close()


# ── --peers target assembly ─────────────────────────────────────────────────
# `_peer_targets` owns no discovery of its own: it leans on
# enumerate_pixelblazes (ad-hoc + beacons + each device's peer list) and on
# getPeers for roles. These stub both, to check the assembly, not the network.

class FakeGroupMember:
    """Stands in for a connected Pixelblaze during --peers target assembly."""

    unreachable = set()
    settings = {}
    peers = []

    def __init__(self, ip):
        self.ipAddress = ip
        if ip in FakeGroupMember.unreachable:
            raise OSError(f"no route to {ip}")

    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def getConfigSettings(self): return dict(FakeGroupMember.settings)
    def getPeers(self): return [dict(p) for p in FakeGroupMember.peers]


def withFakeNetwork(found, settings, peers, unreachable=()):
    """Run _peer_targets against a stubbed network."""
    from pixelblaze.cli import cli as cli_module

    FakeGroupMember.settings, FakeGroupMember.peers = settings, peers
    FakeGroupMember.unreachable = set(unreachable)

    real_enum, real_pb = cli_module.enumerate_pixelblazes, cli_module.Pixelblaze
    cli_module.enumerate_pixelblazes = lambda **kw: [{'ip': ip} for ip in found]
    cli_module.Pixelblaze = FakeGroupMember
    try:
        return cli_module._peer_targets()
    finally:
        cli_module.enumerate_pixelblazes, cli_module.Pixelblaze = real_enum, real_pb


def test_peer_targets_labels_the_whole_sync_group():
    """Roles come from one getPeers call — the peer list is a group-wide view."""
    addresses, roles = withFakeNetwork(
        found=['192.168.1.86', '192.168.1.90'],
        settings={'name': 'bike2', 'leaderId': 0, 'nodeId': 2},
        peers=[{'address': '192.168.1.90', 'name': 'staff', 'isFollowing': 1, 'nodeId': 5}])

    assert addresses == ['192.168.1.86', '192.168.1.90']
    assert roles['192.168.1.86'] == {'name': 'bike2', 'role': 'leader/solo', 'nodeId': 2}
    assert roles['192.168.1.90'] == {'name': 'staff', 'role': 'follower', 'nodeId': 5}


def test_peer_targets_picks_up_a_follower_the_sweep_missed():
    """Followers never beacon; the peer list is the only place they appear."""
    addresses, roles = withFakeNetwork(
        found=['192.168.1.86'],
        settings={'name': 'bike2', 'leaderId': 0, 'nodeId': 2},
        peers=[{'address': '192.168.1.99', 'name': 'hidden', 'isFollowing': 1, 'nodeId': 7}])

    assert '192.168.1.99' in addresses, addresses
    assert roles['192.168.1.99']['role'] == 'follower'


def test_peer_targets_falls_through_to_a_device_that_answers():
    """A wedged first device must not cost us the group view."""
    addresses, roles = withFakeNetwork(
        found=['192.168.1.86', '192.168.1.90'],
        settings={'name': 'staff', 'leaderId': 12345, 'nodeId': 5},
        peers=[], unreachable=['192.168.1.86'])

    assert addresses == ['192.168.1.86', '192.168.1.90']
    assert roles['192.168.1.90']['role'] == 'follower'   # leaderId set => following


def test_peer_targets_still_returns_addresses_when_nothing_answers():
    """Unreachable websockets cost the role labels, not the targets."""
    addresses, roles = withFakeNetwork(
        found=['192.168.1.86'], settings={}, peers=[], unreachable=['192.168.1.86'])

    assert addresses == ['192.168.1.86'] and roles == {}


def test_sync_group_is_described_for_the_user():
    """`--peers` prints who it is about to feed, and flags a contending leader."""
    from pixelblaze.cli.cli import _describe_sync_group

    lines = _describe_sync_group(
        ['192.168.1.86', '192.168.1.90'],
        {'192.168.1.86': {'name': 'bike2', 'role': 'leader/solo', 'nodeId': 2},
         '192.168.1.90': {'name': 'staff', 'role': 'follower', 'nodeId': 5}})

    assert '2 device(s)' in lines[0]
    assert 'bike2' in lines[1] and 'leader/solo' in lines[1] and 'node 2' in lines[1]
    assert 'staff' in lines[2] and 'follower' in lines[2]
    assert 'whichever frame arrives last wins' in lines[-1]

    # An address the peer list never named still gets a row, marked unknown,
    # and with no leader among the roles there is no contention note.
    lines = _describe_sync_group(['10.0.0.5'], {'192.168.1.86': {'role': 'follower'}})
    assert '10.0.0.5' in lines[1] and 'unknown' in lines[1]
    assert 'arrives last wins' not in ' '.join(lines)


def test_sender_id_defaults_to_this_host_and_parses_hex():
    """A stable id keeps `pb snoop --sensor` readable; random per-run does not."""
    import socket as _socket, struct
    import click
    from pixelblaze.cli.cli import _host_sender_id, _parse_sender_id

    assert len(_socket.inet_ntoa(struct.pack('<I', _host_sender_id())).split('.')) == 4
    assert _host_sender_id() == _host_sender_id()          # stable across calls

    assert _parse_sender_id('0xD0CAFE') == 0xD0CAFE
    assert _parse_sender_id('42') == 42

    with pytest.raises(click.ClickException, match="must be a number"):
        _parse_sender_id('nope')
    with pytest.raises(click.ClickException, match="32 bits"):
        _parse_sender_id('0x1FFFFFFFF')


# ── streaming to many devices, and knowing when there is nothing to stream ──
# The bridge is driven directly here: `--tone` needs no audio hardware, and a
# source that delivers nothing is the one failure the device side cannot see.

class FakeSink:
    """Records frames the way a transport would send them."""

    def __init__(self, targets=None):
        self.targets = ['127.0.0.1'] if targets is None else targets
        self.frames = []
        self.started = False
        self.stopped = False

    def describe(self):
        return "fake"

    def start(self):
        self.started = True

    def send(self, data):
        self.frames.append(data)

    def stop(self):
        self.stopped = True


class NullSource:
    """An input that opens and then delivers nothing -- an aggregate device
    with a missing member, an interface unplugged, a device another app holds."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_tone_source_drives_the_whole_path_with_no_audio_hardware():
    """It is also how a headless box can test any of this at all."""
    import math

    from pixelblaze.cli.sensor_bridge import SoundBridge

    sink = FakeSink()
    bridge = SoundBridge(sink, None, 48000, 1024, fps=40, tone=1170, seconds=0.6,
                         channels=2)
    bridge.run()

    assert bridge.stalled_for is None
    assert bridge.frames_sent > 10, f"only {bridge.frames_sent} frames in 0.6 s"
    assert sink.started and sink.stopped

    frame = sink.frames[-1]
    assert frame["maxFrequency"] == pytest.approx(1170, abs=50)
    loudest = max(range(32), key=lambda i: frame["frequencyData"][i])
    assert loudest == 15, "1170 Hz is the centre of bin 15"
    assert math.isfinite(bridge.loudness.momentary), "the meter ran too"
    assert not bridge.is_silent


def test_a_source_that_delivers_nothing_stops_the_run_and_says_so():
    """LOUD FAILURE: no frames go out, the pattern sits on its last one, and
    from the device's side that is indistinguishable from a quiet room."""
    from pixelblaze.cli.sensor_bridge import SoundBridge

    sink = FakeSink()
    bridge = SoundBridge(sink, None, 48000, 1024, fps=40, stall_timeout=0.3)
    bridge._source = NullSource
    bridge.run()

    assert bridge.stalled_for is not None and bridge.stalled_for >= 0.3
    assert bridge.frames_sent == 0
    assert sink.stopped, "the silence frames still go out"


def test_digital_silence_is_reported_but_still_sent():
    """A loopback with nothing playing is the truth about the room, not a
    failure -- so the frames keep flowing and the display just says so."""
    import numpy as np

    from pixelblaze.cli.sensor_bridge import SoundBridge

    sink = FakeSink()
    bridge = SoundBridge(sink, None, 48000, 1024, fps=40, silence_timeout=0.2)
    zeros = np.zeros((1024, 2), dtype=np.float32)

    bridge._audio_callback(zeros, 1024, None, None)
    assert not bridge.is_silent, "not until the timeout has passed"
    bridge._silent_since -= 0.3
    bridge._audio_callback(zeros, 1024, None, None)
    assert bridge.is_silent

    bridge._push_frame()
    assert len(sink.frames) == 1, "silence is a reading, and it is sent"
    assert bridge.stalled_for is None, "blocks ARE arriving; this is not a stall"

    loud = (0.5 * np.sin(np.arange(1024) / 5)).astype(np.float32)[:, None]
    bridge._audio_callback(np.repeat(loud, 2, axis=1), 1024, None, None)
    assert not bridge.is_silent


def test_seconds_stops_the_run():
    import time

    from pixelblaze.cli.sensor_bridge import SoundBridge

    bridge = SoundBridge(FakeSink(), None, 48000, 1024, fps=40, tone=440, seconds=0.4)
    started = time.monotonic()
    bridge.run()
    assert 0.4 <= time.monotonic() - started < 1.5


def test_the_frame_rate_asked_for_is_the_frame_rate_sent():
    """A flat run-loop tick rounds 40 fps down to 30 without anything saying so."""
    from pixelblaze.cli.sensor_bridge import SoundBridge

    bridge = SoundBridge(FakeSink(), None, 48000, 1024, fps=40, tone=440, seconds=1.0)
    bridge.run()
    assert bridge.frames_sent == pytest.approx(40, abs=6)


def test_udp_targets_can_be_added_and_dropped_while_streaming():
    """--rescan feeds whatever turns up later, and a dropped device gets
    silence rather than being frozen on its last frame."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5)
        port = receiver.getsockname()[1]

        sink = UdpSink(["127.0.0.1"])
        sink.sender.port = port
        assert sink.targets == ["127.0.0.1"]
        assert sink.add_target("127.0.0.2") is True
        assert sink.add_target("127.0.0.2") is False, "already a target"
        assert sink.targets == ["127.0.0.1", "127.0.0.2"]

        assert sink.remove_target("127.0.0.3") is False
        assert sink.remove_target("127.0.0.1") is True
        assert sink.targets == ["127.0.0.2"]
        for _ in range(UdpSink.SILENCE_FRAMES):
            silence = SensorPacket.unpack(receiver.recv(256))
            assert silence["frequencyData"] == [0.0] * 32
    finally:
        receiver.close()


def test_udp_describe_survives_a_network_full_of_pixelblazes():
    """The default now feeds everything found, so this line has to stay a line."""
    sink = UdpSink([f"192.168.1.{n}" for n in range(20, 40)])
    described = sink.describe()
    assert "20 device(s)" in described
    assert "+16 more" in described
    assert len(described) < 120


# ── playing a file, and deciding whether you hear it ────────────────────────

def writeWav(path, seconds=1.0, rate=8000, hz=440, channels=2):
    """A PCM WAV, so the no-ffmpeg fallback has something real to read."""
    import struct
    import wave

    import numpy as np
    t = np.arange(int(seconds * rate)) / rate
    wave_data = (0.5 * np.sin(2 * np.pi * hz * t) * 32767).astype('<i2')
    frames = np.repeat(wave_data[:, None], channels, axis=1).tobytes()
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
    return path


@pytest.mark.parametrize("text, seconds", [
    ("12.5", 12.5), ("90", 90.0), ("1:30", 90.0), ("0:07.5", 7.5),
    ("1:02:03", 3723.0), ("  45 ", 45.0),
])
def test_times_can_be_seconds_or_mm_ss(text, seconds):
    from pixelblaze.cli.sensor_bridge import parse_time
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["abc", "1:2:3:4", "-5", "1:-2", ""])
def test_a_time_that_is_not_one_says_what_a_time_looks_like(text):
    from pixelblaze.cli.sensor_bridge import parse_time
    with pytest.raises(ValueError, match="m:ss"):
        parse_time(text)


def test_a_wav_decodes_without_ffmpeg_at_its_own_rate(tmp_path):
    """The fallback path: no ffmpeg, so no resampling either."""
    import numpy as np

    from pixelblaze.cli.sensor_bridge import _decode_wav

    samples, rate = _decode_wav(str(writeWav(tmp_path / "a.wav", seconds=2.0, rate=8000)))
    assert rate == 8000
    assert samples.shape == (16000, 2)
    assert samples.dtype == np.float32
    assert 0.4 < float(np.abs(samples).max()) <= 0.5

    section, _ = _decode_wav(str(tmp_path / "a.wav"), start=0.5, end=1.0)
    assert section.shape == (4000, 2)


def test_decode_refuses_a_backwards_section_and_a_missing_file(tmp_path):
    from pixelblaze.cli.sensor_bridge import decode_audio

    with pytest.raises(RuntimeError, match="no such file"):
        decode_audio(tmp_path / "nope.wav")
    with pytest.raises(RuntimeError, match="must come after"):
        decode_audio(writeWav(tmp_path / "a.wav"), start=10, end=2)


def test_a_file_is_beamed_in_real_time_and_loops_seamlessly(tmp_path):
    """A hitch at the seam is exactly what you would notice at an installation."""
    import numpy as np

    from pixelblaze.cli.sensor_bridge import FileSource

    # Half a second of ramp, so a seam would be obvious as a discontinuity.
    samples = np.repeat(np.linspace(0, 1, 4000, dtype=np.float32)[:, None], 2, axis=1)
    blocks = []
    source = FileSource(samples, 8000, 512, lambda b, *a: blocks.append(b.copy()))
    with source:
        time.sleep(0.75)

    assert source.duration == pytest.approx(0.5)
    played = np.concatenate(blocks)[:, 0]
    assert len(played) >= 8 * 512, "delivered at real time, not as fast as it could"
    # Every step is either the ramp's own, or the single wrap back to zero.
    steps = np.diff(played)
    wraps = int((steps < -0.5).sum())
    assert wraps >= 1, "it looped"
    assert np.all(steps[steps > -0.5] < 0.001), "no gap or repeat at the seam"


def test_not_looping_ends_the_run_when_the_file_does():
    import numpy as np

    from pixelblaze.cli.sensor_bridge import SoundBridge

    sink = FakeSink()
    samples = np.zeros((8000, 2), dtype=np.float32) + 0.1
    bridge = SoundBridge(sink, None, 8000, 512, fps=40, samples=samples, loop=False)
    started = time.monotonic()
    bridge.run()
    assert 0.9 < time.monotonic() - started < 2.5, "stopped when the audio did"
    assert bridge.stalled_for is None, "running out of file is not a stall"


def test_a_loopback_input_is_recognised_by_name():
    from pixelblaze.cli.sensor_bridge import is_loopback_name

    for name in ("BlackHole 2ch", "Soundflower (64ch)", "VB-Cable", "Stereo Mix",
                 "Monitor of Built-in Audio", "VoiceMeeter Output"):
        assert is_loopback_name(name), name
    for name in ("MacBook Pro Microphone", "Scarlett 2i2 USB", "Built-in Input"):
        assert not is_loopback_name(name), name


def test_an_output_named_after_the_loopback_is_taken_to_carry_it():
    """People name an aggregate after what is in it: "BlackHole" -> "BH Speakers"."""
    from pixelblaze.cli.sensor_bridge import _name_suggests_routing

    assert _name_suggests_routing("BlackHole 2ch", "BH Speakers")
    assert _name_suggests_routing("BlackHole 2ch", "BlackHole + Speakers")
    assert _name_suggests_routing("Soundflower (2ch)", "Soundflower Monitor")
    assert not _name_suggests_routing("BlackHole 2ch", "MacBook Pro Speakers")
    assert not _name_suggests_routing("BlackHole 2ch", "External Headphones")


def test_the_monitor_decision_is_always_explained(monkeypatch):
    """Whatever it picks, the reason is printed — it is a guess, not an oracle."""
    from pixelblaze.cli import sensor_bridge
    from pixelblaze.cli.sensor_bridge import choose_monitor

    monkeypatch.setattr(sensor_bridge, 'find_output_device',
                        lambda query=None: (7, {'name': 'BH Speakers'}))
    monkeypatch.setattr(sensor_bridge, '_describes_an_aggregate', lambda name: False)

    assert choose_monitor(None, True, 'BlackHole 2ch', True) == (None, '--no-mix')

    device, why = choose_monitor('BH', False, 'BlackHole 2ch', True)
    assert device == 7 and '--mix BH' in why

    device, why = choose_monitor(None, False, 'BlackHole 2ch', True)
    assert device is None and 'already carries' in why, "don't double it"

    device, why = choose_monitor(None, False, 'MacBook Pro Microphone', True)
    assert device == 7 and 'default output' in why

    device, why = choose_monitor(None, False, 'generated 440 Hz tone', False, is_tone=True)
    assert device is None and 'test signal' in why

    device, why = choose_monitor(None, False, 'set.m4a (12.0s, looping)', False)
    assert device == 7, "a file you asked to play is a file you want to hear"

    device, why = choose_monitor(None, False, 'tone-poem.m4a (12.0s, looping)', False)
    assert device == 7, "a FILE that happens to be called tone is still a file"


def test_an_aggregate_the_system_confirms_beats_the_name_guess(monkeypatch):
    from pixelblaze.cli import sensor_bridge
    from pixelblaze.cli.sensor_bridge import choose_monitor

    monkeypatch.setattr(sensor_bridge, 'find_output_device',
                        lambda query=None: (2, {'name': 'Speakers and Recorder'}))
    monkeypatch.setattr(sensor_bridge, '_describes_an_aggregate', lambda name: True)
    device, why = choose_monitor(None, False, 'BlackHole 16ch', True)
    assert device is None and 'aggregate/multi-output' in why


def test_the_monitor_drops_rather_than_stalling_the_capture():
    """A monitor that blocks the audio callback costs frames, and the frames
    are the point."""
    import numpy as np

    from pixelblaze.cli.sensor_bridge import Monitor

    monitor = Monitor(None, 48000, 2)
    monitor._queue = __import__('queue').Queue(maxsize=2)
    monitor._running = True
    block = np.zeros((1024, 2), dtype=np.float32)
    for _ in range(10):
        monitor.write(block)
    assert monitor.dropped == 8
    assert monitor._queue.qsize() == 2


def test_the_monitor_mixes_down_to_what_the_output_took():
    import numpy as np

    from pixelblaze.cli.sensor_bridge import Monitor

    monitor = Monitor(None, 48000, 1)
    monitor._queue = __import__('queue').Queue(maxsize=4)
    monitor._running = True
    stereo = np.stack([np.full(64, 1.0), np.full(64, 0.0)], axis=1).astype(np.float32)
    monitor.write(stereo)
    written = monitor._queue.get_nowait()
    assert written.shape == (64, 1)
    assert written[0, 0] == pytest.approx(0.5)
