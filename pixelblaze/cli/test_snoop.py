#!/usr/bin/env python3
"""Unit tests for `pb snoop`. No Pixelblaze hardware needed.

Two layers:

  * Pure construction tests — the BPF capture filter, the Wireshark display
    filter and the jq program, checked as strings.
  * A real round trip — a synthetic websocket capture is built byte by byte
    in-process, then pushed through the actual `tshark | jq` pipeline the
    command would run. That covers the parts a string comparison can't:
    that the filters compile, that `-T ek` really does keep several frames
    from one packet apart, and that the jq program parses what tshark emits.

`tshark` and `jq` are hard dependencies of the command under test, so their
absence fails the run rather than skipping it.
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import pathlib

import click

from pixelblaze.cli import cli_utils, snoop
from pixelblaze.cli.snoop import _capture_filter, _display_filter, _jq_program, _resolve_others

HOST = '192.168.1.67'
DEV = '192.168.1.230'
DEV2 = '192.168.1.86'

FAKE_CACHE = {
    'lastIp': None,
    'devices': {
        DEV: {'ip': DEV, 'name': 'bike1'},
        DEV2: {'ip': DEV2, 'name': 'bike2'},
    },
}


# ── Synthetic capture ───────────────────────────────────────────────────────

def _cksum(data: bytes) -> int:
    if len(data) % 2:
        data += b'\x00'
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def _ws_frame(payload: bytes, mask: bool) -> bytes:
    """A single unfragmented websocket text frame. Client frames are masked."""
    out = bytearray([0x81])            # FIN + opcode 1 (text)
    length = len(payload)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        out.append(mask_bit | length)
    else:
        out.append(mask_bit | 126)
        out += struct.pack('!H', length)
    if mask:
        key = b'\x01\x02\x03\x04'
        out += key + bytes(c ^ key[i % 4] for i, c in enumerate(payload))
    else:
        out += payload
    return bytes(out)


def _packet(src, dst, sport, dport, seq, ack, payload) -> bytes:
    ip_of = lambda s: bytes(int(o) for o in s.split('.'))
    tcp = struct.pack('!HHIIBBHHH', sport, dport, seq, ack, 5 << 4, 0x18, 8192, 0, 0)
    pseudo = ip_of(src) + ip_of(dst) + struct.pack('!BBH', 0, 6, len(tcp) + len(payload))
    tcp = tcp[:16] + struct.pack('!H', _cksum(pseudo + tcp + payload)) + tcp[18:]
    ip = (struct.pack('!BBHHHBBH', 0x45, 0, 20 + len(tcp) + len(payload), 0x1234,
                      0x4000, 64, 6, 0) + ip_of(src) + ip_of(dst))
    ip = ip[:10] + struct.pack('!H', _cksum(ip)) + ip[12:]
    eth = b'\xaa\xbb\xcc\xdd\xee\x01\xaa\xbb\xcc\xdd\xee\x02\x08\x00'
    return eth + ip + tcp + payload


# The messages the fixture capture carries, in order, as (outbound?, json).
EXPECTED = [
    (True,  '{"getConfig":true}'),
    (False, '{"fps":42.5,"vmerr":0,"mem":10000}'),
    (False, '{"fps":41.0}'),
    (False, '{"activeProgram":{"name":"sparks"}}'),
    (True,  '{"setVars":{"speed":0.5}}'),
]


def _write_fixture(path: pathlib.Path, handshake: bool = True):
    """A pcap of one websocket session between HOST and DEV on port 81.

    Frames 3 and 4 deliberately share a single TCP packet — that is the case
    `-T fields` mangles into `{...},{...}` and `-T ek` keeps separable.
    Omitting the handshake simulates attaching to an already-open connection.
    """
    packets, seq_c, seq_s, base = [], 1, 1, 1700000000

    if handshake:
        req = (b"GET / HTTP/1.1\r\nHost: %s:81\r\nUpgrade: websocket\r\n"
               b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
               b"Sec-WebSocket-Version: 13\r\n\r\n" % DEV.encode())
        resp = (b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n")
        packets.append((base, _packet(HOST, DEV, 54321, 81, seq_c, seq_s, req)))
        seq_c += len(req)
        packets.append((base, _packet(DEV, HOST, 81, 54321, seq_s, seq_c, resp)))
        seq_s += len(resp)

    def send(outbound, msgs, offset):
        nonlocal seq_c, seq_s
        data = b''.join(_ws_frame(m.encode(), mask=outbound) for m in msgs)
        if outbound:
            packets.append((base + offset, _packet(HOST, DEV, 54321, 81, seq_c, seq_s, data)))
            seq_c += len(data)
        else:
            packets.append((base + offset, _packet(DEV, HOST, 81, 54321, seq_s, seq_c, data)))
            seq_s += len(data)

    send(True, [EXPECTED[0][1]], 1)
    send(False, [EXPECTED[1][1]], 2)
    send(False, [EXPECTED[2][1], EXPECTED[3][1]], 3)   # two frames, one packet
    send(True, [EXPECTED[4][1]], 4)

    blob = bytearray(struct.pack('!IHHiIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
    for when, raw in packets:
        blob += struct.pack('!IIII', when, 0, len(raw), len(raw)) + raw
    path.write_bytes(bytes(blob))


# ── Filter construction ─────────────────────────────────────────────────────

def test_capture_filter():
    """BPF filters narrow by port and host, and never by direction."""
    assert _capture_filter([DEV], None, [81]) == 'tcp port 81 and host 192.168.1.230'
    assert _capture_filter([DEV, DEV2], None, [81]) == \
        'tcp port 81 and (host 192.168.1.230 or host 192.168.1.86)'
    assert _capture_filter([DEV], None, [81, 80]) == \
        '(tcp port 81 or tcp port 80) and host 192.168.1.230'
    assert _capture_filter([DEV], HOST, [81]) == \
        'tcp port 81 and host 192.168.1.230 and host 192.168.1.67'
    assert _capture_filter([], None, [81]) == 'tcp port 81'

    # Direction must never reach the capture filter: half a stream means the
    # 101 Switching Protocols reply is dropped and websocket never dissects.
    for devices in ([DEV], [DEV, DEV2], []):
        text = _capture_filter(devices, None, [81])
        assert 'dst host' not in text and 'src host' not in text, text
    print("✓ capture filters")


def test_display_filter():
    """Display filters carry the direction, and always require websocket."""
    both = _display_filter([DEV], None, [81], False, False)
    assert both == 'websocket and (ip.src == 192.168.1.230 or ip.dst == 192.168.1.230)'
    # Passing both flags is the same as passing neither.
    assert _display_filter([DEV], None, [81], True, True) == both

    assert _display_filter([DEV], None, [81], True, False) == \
        'websocket and (ip.dst == 192.168.1.230)'
    assert _display_filter([DEV], None, [81], False, True) == \
        'websocket and (ip.src == 192.168.1.230)'
    assert _display_filter([DEV, DEV2], None, [81], True, False) == \
        'websocket and (ip.dst == 192.168.1.230 or ip.dst == 192.168.1.86)'

    # --any: no device to orient by, so direction keys off the listening port,
    # which keeps "requests" meaning every client's requests rather than ours.
    assert _display_filter([], None, [81], True, False) == 'websocket and tcp.dstport == 81'
    assert _display_filter([], None, [81], False, True) == 'websocket and tcp.srcport == 81'
    assert _display_filter([], None, [81, 80], True, False) == \
        'websocket and (tcp.dstport == 81 or tcp.dstport == 80)'
    assert _display_filter([], None, [81], False, False) == 'websocket'

    assert _display_filter([DEV], HOST, [81], False, False).endswith(
        'and (ip.src == 192.168.1.67 or ip.dst == 192.168.1.67)')
    print("✓ display filters")


def test_jq_envelope_adapts():
    """Constant fields are dropped; --bare and --full override that."""
    minimal = _jq_program(False, False, False, False, False, None, None, None)
    assert minimal.rstrip().endswith('| {msg: $msg}')

    with_dir = _jq_program(False, True, False, False, False, None, None, None)
    assert 'dir: (if $out' in with_dir

    full = _jq_program(True, True, True, True, False, None, None, None)
    for field in ('ts: $clock', 'dir: ', 'peer: $peer', 'src: $src', 'dst: $dst'):
        assert field in full, field

    bare = _jq_program(True, True, True, True, True, None, None, None)
    assert bare.rstrip().endswith('| $msg')
    assert '{msg:' not in bare

    filtered = _jq_program(False, False, False, False, False, 'setVars', 'fps', None)
    assert 'select($raw | test($grep))' in filtered
    assert 'select($raw | test($exclude) | not)' in filtered

    extra = _jq_program(False, False, False, False, False, None, None, 'select(.msg.fps)')
    assert extra.rstrip().endswith('| select(.msg.fps)')

    # $raw has to be bound before the try, or `catch` sees the error message
    # instead of the payload and non-JSON frames vanish.
    assert '| . as $raw' in minimal
    assert 'try ($raw | fromjson) catch $raw' in minimal
    print("✓ jq program")


def test_resolve_others():
    """--others accepts every --ip form, dedupes, and reports its own name."""
    original_read, original_host = cli_utils._read_cache, cli_utils.get_host_ip
    cli_utils._read_cache = lambda: FAKE_CACHE
    cli_utils.get_host_ip = lambda: HOST
    try:
        assert _resolve_others(None) == []
        assert _resolve_others('') == []
        assert _resolve_others(f'{DEV2}') == [DEV2]
        assert _resolve_others('86, bike2') == [DEV2]           # same device twice
        assert _resolve_others(f'bike1,{DEV2}') == [DEV, DEV2]
        assert _resolve_others(f'http://{DEV2}/') == [DEV2]

        for spec, expected in [('auto', 'auto-discover'), ('nope', '--others'),
                               ('999', '--others')]:
            try:
                _resolve_others(spec)
            except click.ClickException as e:
                assert expected in e.message, f"{spec!r}: got {e.message!r}"
            else:
                raise AssertionError(f"--others {spec!r} should have failed")
    finally:
        cli_utils._read_cache, cli_utils.get_host_ip = original_read, original_host
    print("✓ --others resolution")


# ── Real pipeline round trip ────────────────────────────────────────────────

def _run_pipeline(pcap, devices, host, ports, requests, responses,
                  midstream=False, **program_kwargs):
    """Run the exact tshark|jq pair the command builds, over a saved capture."""
    tshark = shutil.which('tshark')
    jq = shutil.which('jq')
    assert tshark, "tshark is required by `pb snoop` and by this test; install it"
    assert jq, "jq is required by `pb snoop` and by this test; install it"

    decode = 'websocket' if midstream else 'http'
    cmd = [tshark, '-r', str(pcap), '-l', '-n', '-q']
    for port in ports:
        cmd += ['-d', f'tcp.port=={port},{decode}']
    cmd += ['-Y', _display_filter(devices, host, ports, requests, responses), '-T', 'ek']
    for field in snoop._TSHARK_FIELDS:
        cmd += ['-e', field]

    kwargs = dict(show_time=False, show_dir=True, show_peer=False,
                  show_endpoints=False, bare=False, grep=None, exclude=None, extra=None)
    kwargs.update(program_kwargs)

    jq_cmd = [jq, '-c', '-M',
              '--argjson', 'devs', '[' + ','.join(f'"{d}"' for d in devices) + ']',
              '--argjson', 'ports', '[' + ','.join(f'"{p}"' for p in ports) + ']',
              '--arg', 'host', host or '']
    if kwargs['grep']:
        jq_cmd += ['--arg', 'grep', kwargs['grep']]
    if kwargs['exclude']:
        jq_cmd += ['--arg', 'exclude', kwargs['exclude']]
    jq_cmd += [_jq_program(**kwargs)]

    captured = subprocess.run(cmd, capture_output=True, timeout=30)
    assert captured.returncode == 0, captured.stderr.decode()
    rendered = subprocess.run(jq_cmd, input=captured.stdout, capture_output=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr.decode()
    return [line for line in rendered.stdout.decode().splitlines() if line.strip()]


def test_pipeline_round_trip():
    """Decode a real capture and check every frame, in order, with direction."""
    with tempfile.TemporaryDirectory() as tmp:
        pcap = pathlib.Path(tmp) / 'ws.pcap'
        _write_fixture(pcap)

        lines = _run_pipeline(pcap, [DEV], None, [81], False, False)
        assert len(lines) == len(EXPECTED), \
            f"expected {len(EXPECTED)} frames, got {len(lines)}: {lines}"
        for line, (outbound, _) in zip(lines, EXPECTED):
            assert ('\\u2192' in line or '→' in line) == outbound, line

        # The two frames that shared one packet must land as two records.
        assert any('"fps":41' in line for line in lines)
        assert any('activeProgram' in line for line in lines)

        # Direction filters.
        assert len(_run_pipeline(pcap, [DEV], None, [81], True, False)) == 2
        assert len(_run_pipeline(pcap, [DEV], None, [81], False, True)) == 3

        # --any orients by listening port, so it agrees with the named case.
        assert len(_run_pipeline(pcap, [], None, [81], True, False)) == 2
        assert len(_run_pipeline(pcap, [], None, [81], False, True)) == 3

        # grep / exclude / bare.
        assert _run_pipeline(pcap, [DEV], None, [81], False, False,
                             grep='setVars') == ['{"dir":"\u2192","msg":{"setVars":{"speed":0.5}}}']
        assert len(_run_pipeline(pcap, [DEV], None, [81], False, False, exclude='fps')) == 3
        assert _run_pipeline(pcap, [DEV], None, [81], False, False,
                             bare=True)[0] == '{"getConfig":true}'
    print("✓ pipeline round trip")


def test_midstream_decode():
    """--midstream is what makes an already-open connection decodable.

    Without the HTTP upgrade in the capture, tshark's http dissector never
    hands off to websocket and nothing is decoded — the exact failure the
    flag exists to fix.
    """
    with tempfile.TemporaryDirectory() as tmp:
        pcap = pathlib.Path(tmp) / 'midstream.pcap'
        _write_fixture(pcap, handshake=False)

        assert _run_pipeline(pcap, [DEV], None, [81], False, False) == []
        assert len(_run_pipeline(pcap, [DEV], None, [81], False, False,
                                 midstream=True)) == len(EXPECTED)
    print("✓ midstream decode")


# ── Live capture path ───────────────────────────────────────────────────────

def _run_cli(args, timeout=30):
    """Invoke the real `pb` command in a subprocess.

    A subprocess rather than click's CliRunner because the output we care
    about is written to fd 1 by jq, not by Python — CliRunner would not see
    a byte of it.
    """
    code = 'from pixelblaze.cli.cli import main; main()'
    proc = subprocess.run([sys.executable, '-c', code] + args,
                          capture_output=True, timeout=timeout,
                          cwd=str(pathlib.Path(__file__).resolve().parents[2]))
    return proc


def _feed_fifo(fifo: pathlib.Path, payload: bytes):
    """Push a capture into a FIFO once tshark opens the read end.

    tshark treats a FIFO given to -i as a live interface, which exercises the
    whole live code path -- -i, the BPF capture filter, -w, the process
    plumbing -- on a machine with no capture permissions and no Pixelblaze.
    """
    def writer():
        try:
            with open(fifo, 'wb') as handle:   # blocks until tshark reads
                handle.write(payload)
        except OSError:
            pass
    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    return thread


def test_live_capture_path():
    """Exercise -i / -f / -w against a FIFO standing in for a real interface."""
    assert shutil.which('tshark'), "tshark is required by `pb snoop` and by this test"

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        pcap = tmpdir / 'ws.pcap'
        _write_fixture(pcap)
        blob = pcap.read_bytes()

        fifo = tmpdir / 'fifo'
        os.mkfifo(fifo)
        _feed_fifo(fifo, blob)
        plain = _run_cli(['--ip', DEV, 'snoop', '-i', str(fifo)])
        assert plain.returncode == 0, plain.stderr.decode()
        assert len(plain.stdout.decode().splitlines()) == len(EXPECTED), plain.stdout

        # --write is the case that bites: tshark refuses a display filter while
        # saving a live capture, so the command has to narrow with the BPF
        # filter and move the rest into jq.
        fifo2 = tmpdir / 'fifo2'
        saved = tmpdir / 'saved.pcapng'
        os.mkfifo(fifo2)
        _feed_fifo(fifo2, blob)
        written = _run_cli(['--ip', DEV, 'snoop', '-i', str(fifo2), '-w', str(saved)])
        assert written.returncode == 0, written.stderr.decode()
        assert b"aren't supported when capturing" not in written.stderr, written.stderr
        assert len(written.stdout.decode().splitlines()) == len(EXPECTED), written.stdout

        # The saved file keeps the whole stream, handshake included, so it
        # replays through --read without needing --midstream.
        assert saved.exists() and saved.stat().st_size > 0
        replayed = _run_cli(['--ip', DEV, 'snoop', '--read', str(saved)])
        assert replayed.returncode == 0, replayed.stderr.decode()
        assert len(replayed.stdout.decode().splitlines()) == len(EXPECTED), replayed.stdout

        # Direction still works while saving, now applied by jq.
        fifo3 = tmpdir / 'fifo3'
        os.mkfifo(fifo3)
        _feed_fifo(fifo3, blob)
        only_requests = _run_cli(['--ip', DEV, 'snoop', '-i', str(fifo3),
                                  '-w', str(tmpdir / 'r.pcapng'), '--requests'])
        assert only_requests.returncode == 0, only_requests.stderr.decode()
        lines = only_requests.stdout.decode().splitlines()
        assert len(lines) == 2, lines
        assert all('fps' not in line for line in lines), lines
    print("✓ live capture path")


def main():
    test_capture_filter()
    test_display_filter()
    test_jq_envelope_adapts()
    test_resolve_others()
    test_pipeline_round_trip()
    test_midstream_decode()
    test_live_capture_path()
    print("\nAll snoop tests passed.")


if __name__ == '__main__':
    main()
