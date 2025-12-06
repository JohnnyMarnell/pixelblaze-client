"""CLI utilities for Pixelblaze controller."""

from __future__ import annotations

import sys
import socket
import click
import json5
import json
import pathlib
from functools import wraps
from typing import Callable, Optional
from pixelblaze.pixelblaze import Pixelblaze

log = lambda *args, **kwargs: click.echo(*args, err=True, *kwargs)
jsons = lambda x: click.echo(json.dumps(x, separators=(',', ':')))


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


def get_cached_ip():
    """Get the last used IP from cache."""
    try:
        cache_file = get_cache_dir() / 'last_ip.txt'
        if cache_file.exists():
            return cache_file.read_text().strip()
    except Exception:
        pass
    return None


def cache_ip(ip_address):
    """Cache the IP address for future use."""
    try:
        cache_file = get_cache_dir() / 'last_ip.txt'
        cache_file.write_text(ip_address)
    except Exception:
        pass

# Reusable Click options
no_save_option = click.option(
    '--no-save',
    is_flag=True,
    help='Do not save option (vars, sequencer, etc.) changes to flash (temporary only)'
)

# Reusable Click arguments
input_arg = click.argument('input', required=False)

def discover_pixelblaze(ip_address: str) -> str:
    """
    Discovers a Pixelblaze IP address using the specified strategy.

    Args:
        ip_address: Either an explicit IP, "auto", or None

    Returns:
        str: The discovered or specified IP address

    Raises:
        click.ClickException: If no Pixelblaze can be found
    """
    if ip_address and ip_address != "auto":
        cache_ip(ip_address)  # Cache explicitly provided IP
        return ip_address

    # Try cached IP first
    cached_ip = get_cached_ip()
    if cached_ip:
        click.echo(f"Trying cached IP {cached_ip}...", err=True)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((cached_ip, 80))
            sock.close()

            if result == 0:
                click.echo(f"Found Pixelblaze at {cached_ip} (cached)", err=True)
                return cached_ip
        except Exception:
            pass
        click.echo(f"Cached IP {cached_ip} not responding, searching...", err=True)

    # Try ad-hoc mode first (192.168.4.1)
    adhoc_ip = "192.168.4.1"
    click.echo(f"Checking for Pixelblaze in ad-hoc mode at {adhoc_ip}...", err=True)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((adhoc_ip, 80))
        sock.close()

        if result == 0:
            click.echo(f"Found Pixelblaze at {adhoc_ip}", err=True)
            cache_ip(adhoc_ip)
            return adhoc_ip
    except Exception as e:
        click.echo(f"Ad-hoc check failed: {e}", err=True)

    # Fall back to enumerator
    click.echo("Searching for Pixelblazes on network...", err=True)

    try:
        for found_ip in Pixelblaze.EnumerateAddresses(timeout=2000):
            click.echo(f"Found Pixelblaze at {found_ip}", err=True)
            cache_ip(found_ip)
            return found_ip
    except Exception as e:
        click.echo(f"Enumeration failed: {e}", err=True)

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
    import os

    # Check stdin first if it's piped (not a TTY)
    if not sys.stdin.isatty():
        if binary:
            return (sys.stdin.buffer.read(), True)
        else:
            return (sys.stdin.read().strip(), True)

    # No stdin, check value
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
    ip_address = ctx.obj['ip']

    discovered_ip = discover_pixelblaze(ip_address)
    ctx.obj['ip'] = discovered_ip  # Update with actual IP used
    pb = Pixelblaze(discovered_ip)
    ctx.obj['pixelblaze'] = pb
    return pb


def cli(cli_group, **click_kwargs) -> Callable:
    """
    Factory function to create a cli decorator bound to a Click CLI group.

    Returns a decorator that combines @click.command() and @click.pass_context functionality,
    automatically injecting a connected Pixelblaze instance as the first argument
    and wrapping the function body in a context manager.

    Usage:
        @pixelblaze.cli()
        @click.argument('level', type=float, required=False)
        @click.option('--no-save', is_flag=True)
        def brightness(pb, level, no_save):
            pb.setBrightnessSlider(level)

    Args:
        cli_group: The CLI group to add the command to
        **click_kwargs: Additional kwargs to pass to @cli.command()

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(ctx: click.Context, *args, **kwargs):
            with get_pixelblaze(ctx) as pb:
                return func(pb, *args, **kwargs)

        # Apply click.pass_context and cli.command() decorators
        wrapper = click.pass_context(wrapper)
        wrapper = cli_group.command(**click_kwargs)(wrapper)

        return wrapper

    return decorator
