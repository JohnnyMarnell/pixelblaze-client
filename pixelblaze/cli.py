#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Command-line interface for Pixelblaze LED controllers.

This module provides a modern CLI tool for controlling Pixelblazes with
flexible discovery, pattern rendering, and configuration management.
"""

import sys
import socket
import json
import time
import click
from typing import Optional, Dict
from pixelblaze.pixelblaze import Pixelblaze


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

    try:
        discovered_ip = discover_pixelblaze(ip_address)
        pb = Pixelblaze(discovered_ip)
        ctx.obj['pixelblaze'] = pb
        return pb
    except Exception as e:
        raise click.ClickException(f"Failed to connect to Pixelblaze: {e}")


@click.group()
@click.option(
    '--ip',
    default='auto',
    help='IP address of Pixelblaze (default: auto-discover, checks 192.168.4.1 first, then network scan)',
    show_default=True
)
@click.pass_context
def cli(ctx, ip):
    """
    Pixelblaze LED Controller CLI

    Control Pixelblaze devices from the command line with modern, flexible commands.
    """
    ctx.ensure_object(dict)
    ctx.obj['ip'] = ip


@cli.command()
@click.option(
    '--count',
    '-c',
    type=int,
    default=5,
    help='Number of pings to send (default: 5)'
)
@click.pass_context
def ping(ctx, count):
    """
    Test connection latency to the Pixelblaze.

    Sends ping requests and measures round-trip time to determine
    network latency and Pixelblaze responsiveness.

    \b
    Examples:
        pb ping              # Send 5 pings (default)
        pb ping -c 10        # Send 10 pings
        pb ping --count 3    # Send 3 pings
    """
    pb = get_pixelblaze(ctx)

    click.echo(f"Pinging Pixelblaze at {ctx.obj['ip']}...\n", err=True)

    times = []
    successful = 0
    failed = 0

    for i in range(count):
        try:
            start = time.time()
            response = pb.sendPing()
            elapsed = (time.time() - start) * 1000  # Convert to milliseconds

            if response is not None:
                successful += 1
                times.append(elapsed)
                click.echo(f"Ping {i+1}: {elapsed:.2f}ms", err=True)
            else:
                failed += 1
                click.echo(f"Ping {i+1}: timeout", err=True)

            # Small delay between pings
            if i < count - 1:
                time.sleep(0.1)

        except Exception as e:
            failed += 1
            click.echo(f"Ping {i+1}: error - {e}", err=True)

    # Summary
    if times:
        min_time = min(times)
        max_time = max(times)
        avg_time = sum(times) / len(times)

        click.echo(f"\n--- Ping statistics ---", err=True)
        click.echo(f"Packets: Sent = {count}, Received = {successful}, Lost = {failed} ({failed*100//count}% loss)", err=True)
        click.echo(f"Round-trip times: min = {min_time:.2f}ms, max = {max_time:.2f}ms, avg = {avg_time:.2f}ms", err=True)

        # Output machine-readable average for scripting
        click.echo(f"{avg_time:.2f}")
    else:
        click.echo(f"\nAll pings failed", err=True)
        raise click.ClickException("Failed to ping Pixelblaze")


@cli.command()
@click.argument('level', type=float, required=False)
@click.option(
    '--save',
    is_flag=True,
    help='Save the brightness to flash memory (persistent across reboots)'
)
@click.pass_context
def brightness(ctx, level, save):
    """
    Get or set the brightness level.

    LEVEL is a float between 0.0 and 1.0 (optional).

    \b
    Examples:
        pb brightness           # Get current brightness
        pb brightness 0.5       # Set brightness to 50%
        pb brightness 0 --save  # Set to 0 and save to flash
        pb brightness 1         # Set to full brightness
    """
    pb = get_pixelblaze(ctx)

    try:
        if level is None:
            # Get current brightness
            current = pb.getBrightnessSlider()
            click.echo(f"{current:.2f}")
        else:
            # Validate range
            if not 0.0 <= level <= 1.0:
                raise click.ClickException("Brightness must be between 0.0 and 1.0")

            # Set brightness
            click.echo(f"Setting brightness to {level}...", err=True)
            pb.setBrightnessSlider(level, saveToFlash=save)

            # Wait for Pixelblaze to be ready by pinging
            pb.sendPing()

            # Verify
            actual = pb.getBrightnessSlider()
            if abs(actual - level) > 0.02:
                click.echo(f"Warning: Set to {level}, read back {actual:.2f}", err=True)
                # Retry once
                click.echo("Retrying...", err=True)
                pb.setBrightnessSlider(level, saveToFlash=save)
                pb.sendPing()
                actual = pb.getBrightnessSlider()

            action = "saved" if save else "set"
            click.echo(f"Brightness {action} to {actual:.2f}", err=True)

    except Exception as e:
        raise click.ClickException(f"Failed to manage brightness: {e}")


@cli.command()
@click.argument('count', type=int, required=False)
@click.option(
    '--save',
    is_flag=True,
    help='Save the pixel count to flash memory (persistent across reboots)'
)
@click.pass_context
def pixels(ctx, count, save):
    """
    Get or set the number of pixels configured on the Pixelblaze.

    \b
    Examples:
        pb pixels              # Get current pixel count
        pb pixels 300          # Set pixel count to 300 (temporary)
        pb pixels 300 --save   # Set pixel count to 300 (saved to flash)
    """
    pb = get_pixelblaze(ctx)

    try:
        if count is None:
            current_count = pb.getPixelCount()
            click.echo(f"{current_count}")
        else:
            pb.setPixelCount(count, saveToFlash=save)
            action = "saved" if save else "set"
            click.echo(f"Pixel count {action} to {count}", err=True)
    except Exception as e:
        raise click.ClickException(f"Failed to manage pixel count: {e}")


@cli.command()
@click.argument('brightness', type=float, default=1.0, required=False)
@click.option(
    '--play-sequencer',
    is_flag=True,
    help='Also start/resume the pattern sequencer'
)
@click.option(
    '--save',
    is_flag=True,
    help='Save the on state to flash memory (persistent across reboots)'
)
@click.pass_context
def on(ctx, brightness, play_sequencer, save):
    """
    Turn on the Pixelblaze by setting brightness.

    This command sets the brightness to the specified level (default: 1.0).
    Optionally, you can also start/resume the sequencer.

    \b
    Examples:
        pb on                       # Set brightness to 1.0 (full)
        pb on 0.5                   # Set brightness to 50%
        pb on --play-sequencer      # Set brightness to 1.0 and start sequencer
        pb on 0.8 --save            # Set brightness to 80% and save to flash
    """
    pb = get_pixelblaze(ctx)

    # Validate brightness range
    if not 0.0 <= brightness <= 1.0:
        raise click.ClickException("Brightness must be between 0.0 and 1.0")

    try:
        # Set brightness
        click.echo(f"Setting brightness to {brightness}...", err=True)
        pb.setBrightnessSlider(brightness, saveToFlash=save)

        # Wait for Pixelblaze to be ready
        pb.sendPing()

        # Verify the brightness was set
        try:
            actual_brightness = pb.getBrightnessSlider()
            if abs(actual_brightness - brightness) > 0.01:
                click.echo(f"Warning: Brightness verification mismatch (set: {brightness}, actual: {actual_brightness})", err=True)
        except:
            # If verification fails, just continue
            pass

        # Optionally start the sequencer
        if play_sequencer:
            click.echo("Starting sequencer...", err=True)
            pb.playSequencer(saveToFlash=save)
            pb.sendPing()

        action = "saved and turned on" if save else "turned on"
        click.echo(f"Pixelblaze {action} (brightness: {brightness})", err=True)

    except Exception as e:
        raise click.ClickException(f"Failed to turn on Pixelblaze: {e}")


@cli.command()
@click.option(
    '--pause-sequencer',
    is_flag=True,
    help='Also pause the pattern sequencer'
)
@click.option(
    '--save',
    is_flag=True,
    help='Save the off state to flash memory (persistent across reboots)'
)
@click.pass_context
def off(ctx, pause_sequencer, save):
    """
    Turn off the Pixelblaze by setting brightness to zero.

    This command sets the brightness to 0, effectively turning off all LEDs.
    Optionally, you can also pause the sequencer to stop pattern changes.

    \b
    Examples:
        pb off                      # Set brightness to 0
        pb off --pause-sequencer    # Set brightness to 0 and pause sequencer
        pb off --save               # Set brightness to 0 and save to flash
    """
    pb = get_pixelblaze(ctx)

    try:
        # Set brightness to 0
        click.echo("Setting brightness to 0...", err=True)
        pb.setBrightnessSlider(0.0, saveToFlash=save)

        # Wait for Pixelblaze to be ready
        pb.sendPing()

        # Verify the brightness was set
        try:
            actual_brightness = pb.getBrightnessSlider()
            if actual_brightness > 0.01:
                click.echo(f"Warning: Brightness verification mismatch (expected: 0, actual: {actual_brightness})", err=True)
        except:
            # If verification fails, just continue
            pass

        # Optionally pause the sequencer
        if pause_sequencer:
            click.echo("Pausing sequencer...", err=True)
            pb.pauseSequencer(saveToFlash=save)
            pb.sendPing()

        action = "saved and turned off" if save else "turned off"
        click.echo(f"Pixelblaze {action}", err=True)

    except Exception as e:
        raise click.ClickException(f"Failed to turn off Pixelblaze: {e}")


@cli.group()
@click.pass_context
def seq(ctx):
    """
    Sequencer and playlist control commands.

    Control the Pixelblaze pattern sequencer, including play/pause,
    navigation, and playlist management.
    """
    pass


@seq.command()
@click.option(
    '--save',
    is_flag=True,
    help='Save the paused state to flash memory'
)
@click.pass_context
def pause(ctx, save):
    """
    Pause the pattern sequencer.

    \b
    Examples:
        pb seq pause           # Pause sequencer
        pb seq pause --save    # Pause and save to flash
    """
    pb = get_pixelblaze(ctx)

    try:
        click.echo("Pausing sequencer...", err=True)
        pb.pauseSequencer(saveToFlash=save)
        action = "paused and saved" if save else "paused"
        click.echo(f"Sequencer {action}", err=True)
    except Exception as e:
        raise click.ClickException(f"Failed to pause sequencer: {e}")


@seq.command()
@click.option(
    '--save',
    is_flag=True,
    help='Save the playing state to flash memory'
)
@click.pass_context
def play(ctx, save):
    """
    Start/resume the pattern sequencer.

    \b
    Examples:
        pb seq play           # Start/resume sequencer
        pb seq play --save    # Start and save to flash
    """
    pb = get_pixelblaze(ctx)

    try:
        click.echo("Starting sequencer...", err=True)
        pb.playSequencer(saveToFlash=save)
        action = "started and saved" if save else "started"
        click.echo(f"Sequencer {action}", err=True)
    except Exception as e:
        raise click.ClickException(f"Failed to start sequencer: {e}")


@seq.command()
@click.option(
    '--save',
    is_flag=True,
    help='Save the state to flash memory'
)
@click.pass_context
def next(ctx, save):
    """
    Advance to the next pattern in the sequence.

    Works with ShuffleAll or Playlist sequencer modes.

    \b
    Examples:
        pb seq next           # Next pattern
        pb seq next --save    # Next pattern and save
    """
    pb = get_pixelblaze(ctx)

    try:
        click.echo("Advancing to next pattern...", err=True)
        pb.nextSequencer(saveToFlash=save)
        click.echo("Advanced to next pattern", err=True)
    except Exception as e:
        raise click.ClickException(f"Failed to advance to next pattern: {e}")


@seq.command()
@click.pass_context
def random(ctx):
    """
    Jump to a random pattern.

    Selects a random pattern from all available patterns on the Pixelblaze.

    \b
    Examples:
        pb seq random    # Jump to random pattern
    """
    pb = get_pixelblaze(ctx)

    try:
        import random as rand

        click.echo("Getting pattern list...", err=True)
        patterns = pb.getPatternList()

        if not patterns:
            raise click.ClickException("No patterns found on Pixelblaze")

        # Select random pattern
        pattern_id = rand.choice(list(patterns.keys()))
        pattern_name = patterns[pattern_id]

        click.echo(f"Selecting random pattern: {pattern_name}", err=True)
        pb.setActivePattern(pattern_id)
        click.echo(f"Now playing: {pattern_name}", err=True)

    except Exception as e:
        raise click.ClickException(f"Failed to select random pattern: {e}")


@seq.command()
@click.argument('seconds', type=float)
@click.option(
    '--save',
    is_flag=True,
    help='Save the updated playlist to flash memory'
)
@click.pass_context
def len(ctx, seconds, save):
    """
    Set the duration for all patterns in the playlist.

    SECONDS is the duration in seconds for each pattern.

    This modifies the default playlist by setting all pattern durations
    to the specified value.

    \b
    Examples:
        pb seq len 10          # Set all durations to 10 seconds
        pb seq len 30 --save   # Set to 30 seconds and save
    """
    pb = get_pixelblaze(ctx)

    if seconds <= 0:
        raise click.ClickException("Duration must be greater than 0")

    try:
        milliseconds = int(seconds * 1000)

        click.echo("Getting current playlist...", err=True)
        playlist = pb.getSequencerPlaylist()

        if 'playlist' not in playlist or 'items' not in playlist['playlist']:
            raise click.ClickException("Invalid playlist structure")

        items = playlist['playlist']['items']
        if not items:
            raise click.ClickException("Playlist is empty")

        # Update all durations
        original_count = len(items)
        for item in items:
            item['ms'] = milliseconds

        click.echo(f"Setting {original_count} pattern(s) to {seconds} seconds each...", err=True)
        pb.setSequencerPlaylist(playlist)

        if save:
            click.echo("Saving playlist to flash...", err=True)

        click.echo(f"Playlist updated: all patterns set to {seconds}s", err=True)

    except Exception as e:
        raise click.ClickException(f"Failed to update playlist durations: {e}")


@cli.command()
@click.argument('code', required=False)
@click.option(
    '--vars',
    type=str,
    help='JSON dictionary of variables to set (e.g., \'{"speed": 0.5, "brightness": 1.0}\')'
)
@click.option(
    '--var',
    'var_pairs',
    multiple=True,
    help='Individual variable as key:value pair (can be used multiple times, e.g., --var speed:0.5 --var brightness:1.0)'
)
@click.pass_context
def render(ctx, code, vars, var_pairs):
    """
    Send JavaScript code to the Pixelblaze renderer.

    Code can be provided as an argument or piped via stdin.
    Variables can be set using --vars (JSON) or --var (key:value pairs).

    \b
    Examples:
        pb render "export function render(index) { hsv(0.5, 1, 1) }"
        echo "export function render(index) { hsv(0.5, 1, 1) }" | pb render
        pb render code.js --var speed:0.5 --var brightness:1.0
        pb render code.js --vars '{"speed": 0.5, "brightness": 1.0}'
    """
    pb = get_pixelblaze(ctx)

    # Get code from argument or stdin
    if code is None:
        if not sys.stdin.isatty():
            code = sys.stdin.read().strip()
        else:
            raise click.ClickException(
                "No code provided. Supply code as an argument or pipe it via stdin."
            )

    # Parse variables
    variables: Dict[str, float] = {}

    if vars:
        try:
            variables = json.loads(vars)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON in --vars: {e}")

    if var_pairs:
        for pair in var_pairs:
            if ':' not in pair:
                raise click.ClickException(
                    f"Invalid --var format '{pair}'. Expected 'key:value'"
                )
            key, value = pair.split(':', 1)
            try:
                variables[key.strip()] = float(value.strip())
            except ValueError:
                variables[key.strip()] = value.strip()

    try:
        # Compile the pattern code
        click.echo("Compiling pattern...", err=True)
        bytecode = pb.compilePattern(code)

        # Send to renderer
        click.echo("Sending to renderer...", err=True)
        pb.sendPatternToRenderer(bytecode)

        # Set variables if provided
        if variables:
            click.echo(f"Setting variables: {variables}", err=True)
            pb.setActiveVariables(variables)

        click.echo("Pattern rendered successfully", err=True)

    except Exception as e:
        raise click.ClickException(f"Failed to render pattern: {e}")


def main():
    """Entry point for the CLI."""
    cli(obj={})


if __name__ == '__main__':
    main()
