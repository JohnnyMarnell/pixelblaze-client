#!/usr/bin/env python3
"""Unit tests for the flexible --ip resolution. No Pixelblaze hardware needed."""

import click

from pixelblaze.cli import cli_utils
from pixelblaze.cli.cli_utils import resolve_ip_spec

# Stand-in for ~/.config/pixelblaze/cache.json.
FAKE_CACHE = {
    'lastIp': None,
    'devices': {
        '192.168.1.230': {'ip': '192.168.1.230', 'name': 'Kitchen Strip'},
        '192.168.1.231': {'ip': '192.168.1.231', 'name': 'Kitchen Nook'},
        '192.168.1.240': {'ip': '192.168.1.240', 'name': 'Porch'},
        '192.168.1.241': {'ip': '192.168.1.241', 'name': 'Porch Extra'},
        '192.168.4.1': {'ip': '192.168.4.1'},  # discovered but never named
    },
}

FAKE_HOST_IP = '192.168.1.55'

RESOLVES = [
    # auto / empty means "fall back to discovery"
    ('auto', None),
    ('AUTO', None),
    ('', None),
    ('   ', None),
    (None, None),
    # exact IP — the fast path
    ('192.168.1.230', '192.168.1.230'),
    (' 10.0.0.7 ', '10.0.0.7'),
    # pasted out of a browser
    ('http://192.168.1.230/', '192.168.1.230'),
    ('https://192.168.1.230:8080/edit?x=1#frag', '192.168.1.230'),
    ('HTTP://192.168.1.230', '192.168.1.230'),
    ('http://user:pw@10.0.0.5/', '10.0.0.5'),
    ('http://pixelblaze.local/', 'pixelblaze.local'),
    # bare host number on the local /24
    ('230', '192.168.1.230'),
    ('0', '192.168.1.0'),
    ('255', '192.168.1.255'),
    # cached device name
    ('kitchen strip', '192.168.1.230'),  # exact name beats the substring rule
    ('KITCHEN NOOK', '192.168.1.231'),
    ('nook', '192.168.1.231'),
    ('porch', '192.168.1.240'),  # exact 'Porch' wins over 'Porch Extra'
    ('porch e', '192.168.1.241'),
]

REJECTS = [
    ('999.1.1.1', 'octet'),      # dotted quad, out of range
    ('256', 'out of range'),     # bare number, out of range
    ('kitchen', 'Ambiguous'),    # substring hits two devices
    ('ab', 'at least 3'),        # too short to name-match
    ('nope', 'Could not resolve'),
    ('http://', 'No host'),
]


def test_ip_resolve():
    """Check every accepted --ip form, and that bad ones fail clearly."""
    original_read_cache, original_host_ip = cli_utils._read_cache, cli_utils.get_host_ip
    cli_utils._read_cache = lambda: FAKE_CACHE
    cli_utils.get_host_ip = lambda: FAKE_HOST_IP
    try:
        for spec, expected in RESOLVES:
            actual = resolve_ip_spec(spec)
            assert actual == expected, f"--ip {spec!r}: expected {expected!r}, got {actual!r}"

        for spec, expected_msg in REJECTS:
            try:
                actual = resolve_ip_spec(spec)
            except click.ClickException as e:
                assert expected_msg in e.message, \
                    f"--ip {spec!r}: expected {expected_msg!r} in error, got {e.message!r}"
            else:
                raise AssertionError(f"--ip {spec!r}: expected failure, got {actual!r}")
    finally:
        cli_utils._read_cache, cli_utils.get_host_ip = original_read_cache, original_host_ip

    print(f"✓ {len(RESOLVES)} resolved, {len(REJECTS)} rejected")


if __name__ == '__main__':
    test_ip_resolve()
