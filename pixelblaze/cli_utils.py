"""CLI utilities for Pixelblaze controller."""

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
    help='Do not save changes to flash (temporary only)'
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


def read_input(value: Optional[str], name: str = "input", required: bool = True) -> str:
    """
    Read input from value, file path, or stdin.

    If value is provided:
      - If it's an existing file path, read the file
      - Otherwise, treat it as the content itself
    If value is None:
      - If stdin is available (not a TTY), read from stdin
      - Otherwise, raise an error

    Args:
        value: The input value (can be None, a file path, or content string)
        name: Name for error messages (e.g., "code", "map")

    Returns:
        str: The input content

    Raises:
        click.ClickException: If no input provided
    """
    import os

    if value is not None:
        # Check if it's an existing file path
        if os.path.isfile(value):
            with open(value, 'r') as f:
                return f.read().strip()
        # Otherwise treat it as the content itself
        return value

    # No value provided, try stdin
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()

    if required:
        raise click.ClickException(
            f"No {name} provided. Supply {name} as text, a file path, or pipe via stdin."
        )
    else:
        return None


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
