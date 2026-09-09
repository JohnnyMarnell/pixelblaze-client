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
