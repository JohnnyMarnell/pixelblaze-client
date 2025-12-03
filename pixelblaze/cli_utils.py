"""CLI utilities for Pixelblaze controller."""

import socket
import click
from functools import wraps
from typing import Callable
from pixelblaze.pixelblaze import Pixelblaze

log = lambda *args, **kwargs: click.echo(*args, err=True, *kwargs)

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
        return ip_address

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
            return adhoc_ip
    except Exception as e:
        click.echo(f"Ad-hoc check failed: {e}", err=True)

    # Fall back to enumerator
    click.echo("Searching for Pixelblazes on network...", err=True)

    try:
        for found_ip in Pixelblaze.EnumerateAddresses(timeout=2000):
            click.echo(f"Found Pixelblaze at {found_ip}", err=True)
            return found_ip
    except Exception as e:
        click.echo(f"Enumeration failed: {e}", err=True)

    raise click.ClickException(
        "No Pixelblaze found. Specify an IP address with --ip or ensure a Pixelblaze is on the network."
    )


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
