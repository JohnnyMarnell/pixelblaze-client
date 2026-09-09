#!/usr/bin/env python3
"""Unit tests for the `pb sensor sound` transports.

No Pixelblaze and no audio hardware required — the sinks are driven directly
with a frame of the shape `SoundBridge` produces.

    python3 -m pytest pixelblaze/cli/test_sensor_bridge.py
"""

import socket

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
