#!/usr/bin/env python3
"""Unit tests for which subnets discovery will sweep.

No Pixelblaze and no network: the interface list and the cache are both patched,
because the whole point of these is the *decision* about where 254 TCP connects
get pointed — and that decision must not depend on the machine running the test.

    python3 -m pytest pixelblaze/test_sweep_subnets.py
"""

import pytest

from pixelblaze.cli import cli_utils


@pytest.fixture
def fake_net(monkeypatch):
    """Patch the two inputs: local interfaces, and what the cache remembers."""
    state = {'ifaces': [], 'cached': []}

    def ifaces():
        return list(state['ifaces'])

    monkeypatch.setattr(cli_utils, 'localIPv4Interfaces', ifaces)
    monkeypatch.setattr(cli_utils, 'cached_addresses', lambda: list(state['cached']))
    return state


def prefixes(pairs):
    """Just the `a.b.c` part of each (label, hosts) pair, in order."""
    return [label.rsplit('.', 1)[0] for label, _ in pairs]


# ── what counts as a sweepable range at all ──────────────────────────────

@pytest.mark.parametrize('spec, expected', [
    ('192.168.1.0/24', '192.168.1'),
    ('192.168.1.0', '192.168.1'),
    ('192.168.1.9', '192.168.1'),      # any host on it names it
    ('192.168.1', '192.168.1'),
    ('10.17.76.0/24', '10.17.76'),
    ('172.16.5.0/24', '172.16.5'),
    ('  192.168.1.0/24  ', '192.168.1'),
])
def test_accepts_a_private_24_however_it_is_spelled(spec, expected):
    assert cli_utils._private_24(spec) == expected


@pytest.mark.parametrize('spec', [
    '192.168.0.0/16',      # wider than a /24 — 65k connects
    '10.0.0.0/8',
    '8.8.8.0/24',          # public
    '1.1.1.1',
    '172.32.0.0/24',       # just outside RFC1918
    '172.15.0.0/24',       # just below it
    '192.169.1.0/24',      # one off from 192.168
    'not-an-address',
    '192.168.1.999',
    '192.168',             # too short to name a /24
    '',
    None,
])
def test_refuses_anything_public_wider_or_nonsense(spec):
    assert cli_utils._private_24(spec) is None


# ── the local sweep, which is what already worked ────────────────────────

def test_sweeps_every_local_interface_not_just_the_default_route(fake_net):
    """The rig's ordinary shape: ethernet out, and its own AP hosting the boards."""
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255'),
                          ('10.17.76.1', '10.17.76.255')]
    got = cli_utils.sweepable_subnets()
    assert prefixes(got) == ['192.168.2', '10.17.76']
    # 1..254, so neither the network address nor the broadcast is probed
    hosts = got[0][1]
    assert hosts[0] == '192.168.2.1' and hosts[-1] == '192.168.2.254'
    assert len(hosts) == 254


def test_leaves_out_an_interface_wider_than_a_24(fake_net):
    """A /8 on the corporate wifi is not a discovery strategy."""
    fake_net['ifaces'] = [('10.128.79.131', '10.255.255.255'),
                          ('192.168.2.4', '192.168.2.255')]
    assert prefixes(cli_utils.sweepable_subnets()) == ['192.168.2']


# ── the new part: reachable, but not ours ────────────────────────────────

def test_sweeps_a_range_named_outright(fake_net):
    """A board on the laptop's LAN while we sit behind that laptop's NAT.

    Nothing in the routing table can tell us about 192.168.1.0/24 — the default
    route names the laptop, not the networks behind it — so it has to be said.
    """
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    got = prefixes(cli_utils.sweepable_subnets(['192.168.1.0/24']))
    assert got == ['192.168.2', '192.168.1']


def test_sweeps_the_24_around_a_cached_address(fake_net):
    """Evidence, not guesswork: a board HAS been seen at 192.168.1.9.

    This is also what keeps working when a lease moves a board within the same
    subnet, which is the common way a cached address goes stale — the exact
    state the Pi was found in, with every board cached at an old AP address.

    Opt-in, because reading the cache makes the answer depend on disk. It is
    `_discover_devices` that turns it on; see the test below for that.
    """
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    fake_net['cached'] = ['192.168.1.9', '192.168.1.24', '10.17.76.205']

    assert prefixes(cli_utils.sweepable_subnets()) == ['192.168.2'], \
        'the cache must not leak into a plain geometry question'

    got = cli_utils.routable_sweep_prefixes(use_cache=True)
    assert got == ['192.168.1', '10.17.76']
    assert got.count('192.168.1') == 1   # once, despite two boards on it


def test_a_cached_address_on_a_local_subnet_adds_nothing(fake_net):
    """It is already swept; listing it twice would double the connects."""
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    fake_net['cached'] = ['192.168.2.1', '192.168.2.77']
    assert cli_utils.routable_sweep_prefixes(use_cache=True) == []


def test_a_public_cached_address_is_never_swept(fake_net):
    """A cache can hold anything that was once typed at it. 254 connects at a
    public /24 is the accident this is bounded to avoid."""
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    fake_net['cached'] = ['8.8.8.8', '203.0.113.5', '192.168.1.9']
    assert cli_utils.routable_sweep_prefixes(use_cache=True) == ['192.168.1']


def test_named_ranges_come_before_cached_ones_and_never_duplicate(fake_net):
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    fake_net['cached'] = ['192.168.5.10', '192.168.1.9']
    got = cli_utils.routable_sweep_prefixes(['192.168.1.0/24', '192.168.9.0/24'],
                                            use_cache=True)
    assert got == ['192.168.1', '192.168.9', '192.168.5']


def test_routable_prefixes_alone(fake_net):
    """The helper on its own, since callers may want to report what it found."""
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    fake_net['cached'] = ['192.168.1.9']
    assert cli_utils.routable_sweep_prefixes() == [], 'no cache unless asked'
    assert cli_utils.routable_sweep_prefixes(use_cache=True) == ['192.168.1']
    assert cli_utils.routable_sweep_prefixes(['172.16.0.0/24'], use_cache=True) \
        == ['172.16.0', '192.168.1']


def test_no_interfaces_and_no_cache_sweeps_nothing(fake_net):
    """Windows has no getifaddrs, so this is a real state, not a hypothetical."""
    assert cli_utils.sweepable_subnets() == []


# ── the integration: discovery is what turns the cache on ────────────────

def test_discovery_hands_the_sweep_its_cache_derived_ranges(fake_net, monkeypatch):
    """`_discover_devices` already probes every cached address whatever subnet
    it is on, so the /24 around one is a natural extension — and it is the only
    caller that opts in. Without this the opt-in would be dead code.
    """
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    fake_net['cached'] = ['192.168.1.9']

    handed = []

    def spy(extra=()):
        handed.append(list(extra))
        return []                      # sweep nothing; we only want the decision

    monkeypatch.setattr(cli_utils, 'sweepable_subnets', spy)
    # Everything that would touch the network, stubbed out.
    monkeypatch.setattr(cli_utils, '_read_cache', lambda: {'devices': {}})
    monkeypatch.setattr(cli_utils, 'get_host_ip', lambda: '192.168.2.4')
    monkeypatch.setattr(cli_utils, '_tcp_ports_open',
                        lambda ip, ports=(80, 81), timeout=1.0: {80: False, 81: False})

    class _NoBeacons:
        packetTypes: dict = {}

        def __init__(self, **kw):
            pass

        def __iter__(self):
            return iter(())

    monkeypatch.setattr(cli_utils.Pixelblaze, 'EnumerateAddresses',
                        staticmethod(lambda **kw: _NoBeacons(**kw)))

    cli_utils._discover_devices(timeout=1, probe=False, sweep=True,
                                sweep_extra=['172.16.9.0/24'])

    assert handed, 'the sweep was never asked what to probe'
    # Named first, then what the cache implies — and the local /24 is not in
    # the extras: sweepable_subnets finds that one itself.
    assert handed[0] == ['172.16.9', '192.168.1']


def test_discovery_with_sweep_off_asks_for_no_ranges(fake_net, monkeypatch):
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    fake_net['cached'] = ['192.168.1.9']
    called = []
    monkeypatch.setattr(cli_utils, 'sweepable_subnets',
                        lambda extra=(): called.append(list(extra)) or [])
    monkeypatch.setattr(cli_utils, '_read_cache', lambda: {'devices': {}})
    monkeypatch.setattr(cli_utils, 'get_host_ip', lambda: '192.168.2.4')
    monkeypatch.setattr(cli_utils, '_tcp_ports_open',
                        lambda ip, ports=(80, 81), timeout=1.0: {80: False, 81: False})

    class _NoBeacons:
        packetTypes: dict = {}

        def __init__(self, **kw):
            pass

        def __iter__(self):
            return iter(())

    monkeypatch.setattr(cli_utils.Pixelblaze, 'EnumerateAddresses',
                        staticmethod(lambda **kw: _NoBeacons(**kw)))

    cli_utils._discover_devices(timeout=1, probe=False, sweep=False)
    assert called == [], 'sweep=False must not even compute a range'


# ── deriving the range from our own route out, with no configuration ─────

def hops(*addresses):
    """A fake TTL probe: hop N is addresses[N-1], None past the end."""
    def probe(ttl):
        return addresses[ttl - 1] if 1 <= ttl <= len(addresses) else None
    return probe


def test_the_laptop_sharing_case_needs_no_configuration():
    """hop 1 is the laptop we are plugged into, hop 2 is its router — and that
    second hop IS the LAN the boards are on. Measured on the rig:
    192.168.2.1, then 192.168.1.1, then a public address.
    """
    got = cli_utils.upstream_private_prefixes(
        probe=hops('192.168.2.1', '192.168.1.1', '24.193.216.1'))
    assert got == ['192.168.2', '192.168.1']


def test_the_walk_stops_at_the_first_public_hop():
    """Past the edge of the private network there is nothing for us, and
    probing onward is just poking at the internet."""
    got = cli_utils.upstream_private_prefixes(
        probe=hops('192.168.2.1', '24.193.216.1', '192.168.50.1'))
    assert got == ['192.168.2'], 'a private hop beyond a public one is not ours'


def test_a_silent_hop_does_not_end_the_walk():
    """Plenty of routers drop the probe rather than answering it. That is not
    the edge of the private network, so keep going."""
    got = cli_utils.upstream_private_prefixes(
        probe=hops('192.168.2.1', None, '10.9.9.1'))
    assert got == ['192.168.2', '10.9.9']


def test_no_hops_answer_at_all():
    assert cli_utils.upstream_private_prefixes(probe=hops()) == []


def test_each_hop_contributes_its_24_once():
    got = cli_utils.upstream_private_prefixes(
        probe=hops('192.168.2.1', '192.168.2.254', '192.168.1.1'))
    assert got == ['192.168.2', '192.168.1']


def test_upstream_is_bounded_by_max_hops():
    probe_calls = []

    def counting(ttl):
        probe_calls.append(ttl)
        return '192.168.%d.1' % ttl

    cli_utils.upstream_private_prefixes(max_hops=2, probe=counting)
    assert probe_calls == [1, 2], 'it must not walk further than asked'


def test_routable_prefixes_folds_upstream_in_behind_its_own_flag(fake_net, monkeypatch):
    """Off unless asked, like the cache — and never duplicating a local subnet."""
    fake_net['ifaces'] = [('192.168.2.4', '192.168.2.255')]
    monkeypatch.setattr(cli_utils, 'upstream_private_prefixes',
                        lambda *a, **k: ['192.168.2', '192.168.1'])

    assert cli_utils.routable_sweep_prefixes() == [], 'no probing unless asked'
    got = cli_utils.routable_sweep_prefixes(use_upstream=True)
    assert got == ['192.168.1'], '192.168.2 is ours already'


def test_a_hop_that_is_not_an_address_is_ignored():
    assert cli_utils.upstream_private_prefixes(probe=hops('', '192.168.1.1')) \
        == ['192.168.1']


def test_a_failed_probe_is_not_cached(monkeypatch):
    """A reboot is exactly when this matters.

    At boot the tap can probe before the default route exists — the rig brings
    up its own network, and the laptop it is plugged into may not be sharing
    yet. Caching that empty answer would leave discovery blind to the upstream
    range for the life of the process, which on the rig means until the next
    reboot.
    """
    monkeypatch.setattr(cli_utils, '_upstream_cache', None)
    calls = []

    def flaky(ttl):
        calls.append(ttl)
        # Nothing answers until the third attempt at the whole walk.
        return '192.168.1.1' if len(calls) > 4 else None

    monkeypatch.setattr(cli_utils, '_next_hop', flaky)

    assert cli_utils.upstream_private_prefixes(max_hops=2) == []
    assert cli_utils._upstream_cache is None, 'an empty answer must not stick'
    assert cli_utils.upstream_private_prefixes(max_hops=2) == []
    got = cli_utils.upstream_private_prefixes(max_hops=2)
    assert got == ['192.168.1'], 'it must keep trying until the network is up'
    assert cli_utils._upstream_cache == ['192.168.1'], 'a useful answer is kept'


def test_a_useful_answer_is_probed_only_once(monkeypatch):
    monkeypatch.setattr(cli_utils, '_upstream_cache', None)
    calls = []
    monkeypatch.setattr(cli_utils, '_next_hop',
                        lambda ttl: calls.append(ttl) or '192.168.1.1')
    assert cli_utils.upstream_private_prefixes(max_hops=1) == ['192.168.1']
    before = len(calls)
    assert cli_utils.upstream_private_prefixes(max_hops=1) == ['192.168.1']
    assert len(calls) == before, 'the second call must not probe again'
