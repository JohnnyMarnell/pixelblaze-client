"""CLI utilities for Pixelblaze controller."""

from __future__ import annotations

import os
import sys
import time
import socket
import click
import json5
import json
import pathlib
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from typing import Callable, Optional
from pixelblaze.pixelblaze import Pixelblaze

log = lambda *args, **kwargs: click.echo(*args, err=True, *kwargs)
jsons = lambda x: click.echo(json.dumps(x, separators=(',', ':')))

# Opportunistic cache refresh: skip if the entry was refreshed less than this many seconds ago.
# Override with `pb cache refresh` (force) or env PB_CACHE_TTL.
CACHE_TTL_SECONDS = 60 * 60


def get_cache_dir():
    """Get the cache directory for Pixelblaze CLI, creating it if needed."""
    # Use ~/.config/pixelblaze on Unix-like systems, ~/AppData/Local/pixelblaze on Windows
    if sys.platform == 'win32':
        cache_dir = pathlib.Path.home() / 'AppData' / 'Local' / 'pixelblaze'
    else:
        config_home = pathlib.Path.home() / '.config'
        cache_dir = config_home / 'pixelblaze'

    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_host_ip() -> str:
    """Get the local machine's outbound IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Standard trick: connect() a UDP socket to an unroutable address —
        # no packets are actually sent; the kernel just resolves which local
        # interface WOULD be used, and getsockname() reports its IP.
        # 10.255.255.255 is the RFC 919 limited-broadcast address for the
        # 10.0.0.0/8 private range, so this works even without a default
        # gateway (e.g. when joined only to a PB's AP-mode SSID).
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ''


def _read_cache() -> dict:
    """Read cache.json, returning empty structure on missing/corrupt file."""
    try:
        cache_file = get_cache_dir() / 'cache.json'
        if cache_file.exists():
            return json.loads(cache_file.read_text())
    except Exception:
        pass
    return {'lastIp': None, 'devices': {}}


def _write_cache(cache: dict):
    """Write cache.json atomically."""
    try:
        (get_cache_dir() / 'cache.json').write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


def update_device_cache(devices: list[dict]):
    """Merge device info into cache.json devices map, preserving existing richer data."""
    cache = _read_cache()
    known = cache.setdefault('devices', {})
    host_ip = get_host_ip()
    for dev in devices:
        ip = dev.get('ip')
        if not ip:
            continue
        entry = known.setdefault(ip, {'ip': ip})
        for k, v in dev.items():
            if v is not None and v != '':
                entry[k] = v
        if host_ip:
            entry['hostIp'] = host_ip
    _write_cache(cache)


def get_cached_ip():
    """Get the last used IP from cache."""
    cache = _read_cache()
    return cache.get('lastIp')


def cache_ip(ip_address):
    """Cache the IP address for future use."""
    cache = _read_cache()
    cache['lastIp'] = ip_address
    _write_cache(cache)

# Reusable Click options
no_save_option = click.option(
    '--no-save',
    is_flag=True,
    help='Do not save option (vars, sequencer, etc.) changes to flash (temporary only)'
)

# Reusable Click arguments
input_arg = click.argument('input', required=False)

def _check_ip_reachable(ip: str, timeout: float = 1.0) -> bool:
    """Check if a given IP has port 80 open (quick TCP connect check)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, 80))
        sock.close()
        return result == 0
    except Exception:
        return False


# Top-level keys promoted out of `settings` for quick display in `pb cache ls` / `pb find`.
_SUMMARY_KEYS = (
    'name', 'brandName', 'ver', 'pixelCount', 'ledType', 'dataSpeed',
    'colorOrder', 'brightness', 'maxBrightness', 'cpuSpeed',
    'networkPowerSave', 'sensorInputSource', 'discoveryEnable', 'timezone',
)


def _fetch_device_config(pb: Pixelblaze, ip: str, include_patterns: bool = True) -> dict:
    """Fetch full basic config from a connected Pixelblaze.

    Each sub-call is wrapped so a partial failure still persists what we got.
    `include_patterns=False` skips the heavier getPatternList() call (the existing
    cached pattern list, if any, is preserved by update_device_cache's merge).
    """
    info = {'ip': ip}

    # getConfigSettings primes pb.latestSequencer, so getConfigSequencer below is ~free.
    try:
        settings = pb.getConfigSettings()
        for key in _SUMMARY_KEYS:
            if key in settings:
                info[key] = settings[key]
        info['settings'] = settings
    except Exception as e:
        info['settingsError'] = str(e)

    try:
        seq = pb.getConfigSequencer()
        info['sequencer'] = seq
        active = seq.get('activeProgram', {}) if isinstance(seq, dict) else {}
        info['activePatternId'] = active.get('activeProgramId', '') or ''
    except Exception as e:
        info['sequencerError'] = str(e)

    if include_patterns:
        try:
            patterns = pb.getPatternList()
            info['patterns'] = patterns
            active_id = info.get('activePatternId', '')
            if active_id and active_id in patterns:
                info['activePatternName'] = patterns[active_id]
        except Exception as e:
            info['patternsError'] = str(e)

    info['lastSeenAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return info


def _get_device_info(ip: str) -> dict:
    """Connect to a Pixelblaze and return a full device info dict (parallel-safe)."""
    try:
        with Pixelblaze(ip) as pb:
            return _fetch_device_config(pb, ip=ip, include_patterns=True)
    except Exception:
        return {'ip': ip, 'name': ''}


def _cache_is_fresh(ip: str, ttl_seconds: int = CACHE_TTL_SECONDS) -> bool:
    """Return True if cache entry for ip exists, has a config snapshot, and is within ttl."""
    entry = _read_cache().get('devices', {}).get(ip)
    if not entry or 'settings' not in entry:
        return False
    last_seen = entry.get('lastSeenAt')
    if not last_seen:
        return False
    try:
        dt = datetime.datetime.fromisoformat(last_seen)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
        return age < ttl_seconds
    except Exception:
        return False


def maybe_refresh_cache(pb: Pixelblaze, ip: str, force: bool = False) -> bool:
    """Opportunistically refresh cached config for `ip` using an already-connected pb.

    Silent on any failure. Returns True if a refresh was attempted.
    Skips the patternList fetch on routine refreshes (when patterns are already cached)
    to keep the post-command cost minimal; `force=True` always re-fetches everything.
    """
    try:
        if not ip:
            return False
        if not force and _cache_is_fresh(ip):
            return False
        existing = _read_cache().get('devices', {}).get(ip, {})
        include_patterns = force or 'patterns' not in existing
        info = _fetch_device_config(pb, ip=ip, include_patterns=include_patterns)
        update_device_cache([info])
        return True
    except Exception:
        return False


def lookup_cached_device(query: str) -> tuple[str, dict]:
    """Look up a cached device by exact IP or case-insensitive name substring.

    Raises click.ClickException if not found or ambiguous.
    """
    devices = _read_cache().get('devices', {})
    if not devices:
        raise click.ClickException("No cached devices. Run `pb find` first.")
    if query in devices:
        return query, devices[query]
    q = query.lower()
    matches = [(ip, e) for ip, e in devices.items() if q in (e.get('name') or '').lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ', '.join(f"{e.get('name', '?')} ({ip})" for ip, e in matches)
        raise click.ClickException(f"Ambiguous query '{query}': matches {names}")
    raise click.ClickException(f"No cached device matches '{query}'.")


def _discover_ips(timeout: int = 2000) -> list[str]:
    """
    Discover Pixelblaze IP addresses via ad-hoc check and UDP beacons.

    Fast — no websocket connections, just network probing.

    Args:
        timeout: Beacon listen timeout in milliseconds.

    Returns:
        list[str]: List of discovered IP addresses.
    """
    ips = []
    seen = set()

    # Check ad-hoc mode first
    adhoc_ip = "192.168.4.1"
    log(f"Checking ad-hoc ({adhoc_ip})...")
    if _check_ip_reachable(adhoc_ip):
        ips.append(adhoc_ip)
        seen.add(adhoc_ip)
        log(f"  Found @ {adhoc_ip} (ad-hoc)")

    # Beacon enumeration
    log(f"Listening for beacons ({timeout}ms)...")
    try:
        for found_ip in Pixelblaze.EnumerateAddresses(timeout=timeout):
            if found_ip not in seen:
                seen.add(found_ip)
                ips.append(found_ip)
                log(f"  Found @ {found_ip}")
    except Exception as e:
        log(f"Enumeration error: {e}")

    return ips


def enumerate_pixelblazes(timeout: int = 3000, slow: bool = False) -> list[dict]:
    """
    Discover all Pixelblazes on the network.

    In fast mode (default), returns minimal dicts with just the IP from
    beacon discovery. In slow mode, connects to each device in parallel
    to fetch name, config, version, etc.

    Args:
        timeout: Beacon listen timeout in milliseconds.
        slow: If True, connect to each device to fetch full config info.

    Returns:
        list[dict]: Device info dicts. Fast mode: {'ip': ...} only.
                    Slow mode adds: name, pixelCount, brightness, ver, brandName, hostIp.
    """
    ips = _discover_ips(timeout=timeout)

    if not ips:
        return []

    host_ip = get_host_ip()

    if not slow:
        devices = [{'ip': ip} for ip in ips]
        update_device_cache(devices)
        return devices

    # Slow mode: connect to each device to get full info, in parallel
    log(f"Fetching device info from {len(ips)} device(s)...")
    devices = []
    with ThreadPoolExecutor(max_workers=min(len(ips), 8)) as pool:
        futures = {pool.submit(_get_device_info, ip): ip for ip in ips}
        for future in as_completed(futures):
            info = future.result()
            if info:
                if host_ip:
                    info['hostIp'] = host_ip
                devices.append(info)
                name = info.get('name', '?')
                if name:
                    log(f"  {info['ip']}: {name}")

    # Preserve discovery order
    ip_order = {ip: i for i, ip in enumerate(ips)}
    devices.sort(key=lambda d: ip_order.get(d['ip'], 999))

    update_device_cache(devices)
    return devices


def discover_pixelblaze(ctx: click.Context) -> str:
    """
    Discovers a Pixelblaze IP address using the specified strategy.

    Uses cached IP, ad-hoc check, then beacon enumeration. Returns the
    first reachable IP and caches it.

    Args:
        ctx: Click context containing IP address in ctx.obj['ip']

    Returns:
        str: The discovered or specified IP address

    Raises:
        click.ClickException: If no Pixelblaze can be found
    """
    ip_address = ctx.obj.get('ip', 'auto')

    if ip_address and ip_address != "auto":
        cache_ip(ip_address)  # Cache explicitly provided IP
        return ip_address

    # Try cached IP first
    cached = get_cached_ip()
    if cached:
        log(f"Trying cached IP {cached}...")
        if _check_ip_reachable(cached):
            log(f"Found Pixelblaze at {cached} (cached)")
            return cached
        log(f"Cached IP {cached} not responding, searching...")

    # Full enumeration — return first found (fast, no websocket connects)
    ips = _discover_ips(timeout=2000)
    if ips:
        update_device_cache([{'ip': ip} for ip in ips])
        cache_ip(ips[0])
        return ips[0]

    raise click.ClickException(
        "No Pixelblaze found. Specify an IP address with --ip or ensure a Pixelblaze is on the network."
    )


def read_input(value: Optional[str], name: str = "input", required: bool = True, binary: bool = False) -> tuple[str | bytes, bool]:
    """
    Read input from stdin, file path, or value string.

    Checks stdin first (if piped), then file path, then treats value as inline content.

    Args:
        value: The input value (can be None, a file path, or content string)
        name: Name for error messages (e.g., "code", "map")
        required: Whether input is required (raises if no input provided)
        binary: If True, read files and stdin in binary mode (returns bytes)

    Returns:
        tuple[str | bytes, bool]: (content, is_stdin) where:
            - content: The input data (str in text mode, bytes in binary mode)
            - is_stdin: True if content came from stdin, False otherwise

    Raises:
        click.ClickException: If no input provided and required=True
    """
    # If an explicit value was provided, check file path first (before stdin)
    if value is not None:
        # Check if it's an existing file path
        if os.path.isfile(value):
            mode = 'rb' if binary else 'r'
            with open(value, mode) as f:
                content = f.read()
                return (content if binary else content.strip(), False)
        # Otherwise treat it as the content itself (text mode only)
        if binary:
            raise click.ClickException(
                f"Cannot use inline content in binary mode. Provide a file path or pipe via stdin."
            )
        return (value, False)

    # No explicit value — check stdin if it's piped (not a TTY)
    if not sys.stdin.isatty():
        if binary:
            return (sys.stdin.buffer.read(), True)
        else:
            return (sys.stdin.read().strip(), True)

    # No stdin, no value
    if required:
        raise click.ClickException(
            f"No {name} provided. Supply {name} as text, a file path, or pipe via stdin."
        )
    else:
        return (None, False)


def parse_json(text: str):
    """
    Parse JSON-like text using json5 (supports single quotes, unquoted keys, etc).

    Args:
        text: JSON5 string to parse

    Returns:
        Parsed object/array

    Raises:
        click.ClickException: If parsing fails
    """
    try:
        return json5.loads(text)
    except Exception as e:
        raise click.ClickException(f"Invalid JSON: {e}")


def check(condition, error_message: str):
    """
    Ensure a condition is true, otherwise raise a ClickException.

    Args:
        condition: The condition to check
        error_message: Error message to display if condition is false

    Raises:
        click.ClickException: If condition is false

    Example:
        ensure(len(items) > 0, "No items found")
        ensure(value >= 0 and value <= 1, "Value must be between 0 and 1")
    """
    if not condition:
        raise click.ClickException(error_message)


def parse_vars(args):
    """
    Parse variable arguments in flexible formats.

    Supports:
    - key value pairs: ('foo', 'bar') → {foo: "bar"}
    - colon-separated: ('foo:bar baz',) → {foo: "bar baz"}
    - JSON5 objects: ('{a:1, b:2}',) → {a: 1, b: 2}
    - mixed: ('foo', '2', 'bar:3', '{baz:true}') → {foo: 2, bar: 3, baz: true}

    Args:
        args: Tuple or list of argument strings

    Returns:
        dict: Parsed variables

    Raises:
        click.ClickException: If parsing fails or args are malformed

    Examples:
        parse_vars(['foo', 'bar']) → {'foo': 'bar'}
        parse_vars(['foo', '1']) → {'foo': 1}
        parse_vars(['foo:bar']) → {'foo': 'bar'}
        parse_vars(['{a:1}']) → {'a': 1}
        parse_vars(['foo', '2', 'bar:3']) → {'foo': 2, 'bar': 3}
    """
    variables = {}

    i = 0
    while i < len(args):
        arg = args[i]

        # Try to parse as JSON5 object/array
        if arg.startswith('{') or arg.startswith('['):
            try:
                parsed = parse_json(arg)
                if isinstance(parsed, dict):
                    variables.update(parsed)
                    i += 1
                    continue
            except:
                pass

        # Check for colon-separated key:value
        if ':' in arg:
            key, value = arg.split(':', 1)
            try:
                variables[key] = float(value)
            except ValueError:
                variables[key] = value
            i += 1
            continue

        # Otherwise, treat as key with next arg as value
        check(i + 1 < len(args), f"Missing value for key '{arg}'")
        key = arg
        value = args[i + 1]
        try:
            variables[key] = float(value)
        except ValueError:
            variables[key] = value
        i += 2

    return variables


def get_pixelblaze(ctx: click.Context) -> Pixelblaze:
    """
    Gets a Pixelblaze instance from the context, handling discovery if needed.

    Args:
        ctx: Click context containing the IP address

    Returns:
        Pixelblaze: Connected Pixelblaze instance

    Raises:
        click.ClickException: If connection fails
    """
    discovered_ip = discover_pixelblaze(ctx)
    ctx.obj['ip'] = discovered_ip  # Update with actual IP used
    timeout = ctx.obj.get('timeout', 5.0)
    Pixelblaze.default_recv_timeout = timeout
    pb = Pixelblaze(discovered_ip)
    ctx.obj['pixelblaze'] = pb
    return pb



# Transient errors that warrant a retry
_RETRYABLE = (
    ConnectionError,
    ConnectionResetError,
    TimeoutError,
    OSError,
)

try:
    import websocket
    _RETRYABLE = _RETRYABLE + (websocket._exceptions.WebSocketTimeoutException,
                                websocket._exceptions.WebSocketConnectionClosedException,)
except Exception:
    pass

try:
    import requests as _req
    _RETRYABLE = _RETRYABLE + (_req.ConnectionError, _req.Timeout,)
except Exception:
    pass


def _run_with_retries(ctx: click.Context, fn, *args, **kwargs):
    """Run fn with retry logic on transient connection errors."""
    max_retries = ctx.obj.get('retries', 3)
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except _RETRYABLE as e:
            if attempt >= max_retries:
                raise
            delay = 0.5 * (attempt + 1)
            log(f"Connection error ({type(e).__name__}), retry {attempt + 1}/{max_retries} in {delay:.1f}s...")
            time.sleep(delay)


def cli(cli_group, conn=True, **click_kwargs) -> Callable:
    """
    Factory function to create a cli decorator bound to a Click CLI group.

    Returns a decorator that combines @click.command() and @click.pass_context functionality,
    automatically injecting a connected Pixelblaze instance as the first argument
    and wrapping the function body in a context manager.

    Automatically retries on transient connection errors (ConnectionResetError,
    timeouts, etc.) using the --retries global option.

    Usage:
        @cli(pixelblaze)
        @click.argument('level', type=float, required=False)
        @click.option('--no-save', is_flag=True)
        def brightness(pb, level, no_save):
            pb.setBrightnessSlider(level)

        @cli(pixelblaze, conn=False)
        def reboot(ctx, wait):
            # Handle connection manually
            pass

    Args:
        cli_group: The CLI group to add the command to
        conn: If True (default), automatically connects and passes Pixelblaze instance.
              If False, passes context and lets function handle connection.
        **click_kwargs: Additional kwargs to pass to @cli.command()

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(ctx: click.Context, *args, **kwargs):
            def _run():
                if conn:
                    with get_pixelblaze(ctx) as pb:
                        result = func(pb, *args, **kwargs)
                        maybe_refresh_cache(pb, ctx.obj.get('ip', ''))
                        return result
                else:
                    return func(ctx, *args, **kwargs)
            return _run_with_retries(ctx, _run)

        # Apply click.pass_context and cli.command() decorators
        wrapper = click.pass_context(wrapper)
        wrapper = cli_group.command(**click_kwargs)(wrapper)

        return wrapper

    return decorator
