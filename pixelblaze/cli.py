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
import re
import click
from typing import Dict
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

    discovered_ip = discover_pixelblaze(ip_address)
    ctx.obj['ip'] = discovered_ip
    pb = Pixelblaze(discovered_ip)
    ctx.obj['pixelblaze'] = pb
    return pb


@click.group()
@click.option(
    '--ip',
    default='auto',
    help='IP address of Pixelblaze (default: auto-discover, checks 192.168.4.1 first, then network scan)',
    show_default=True
)
@click.option(
    '--timeout',
    type=float,
    default=5.0,
    help='Command timeout in seconds (default: 5.0)',
    show_default=True
)
@click.option(
    '--no-verify',
    is_flag=True,
    help='Skip ping/ack verification (faster but less reliable)'
)
@click.pass_context
def cli(ctx, ip, timeout, no_verify):
    """
    Pixelblaze LED Controller CLI

    Control Pixelblaze devices from the command line with modern, flexible commands.
    """
    ctx.ensure_object(dict)
    ctx.obj['ip'] = ip
    ctx.obj['timeout'] = timeout
    ctx.obj['no_verify'] = no_verify


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
    with get_pixelblaze(ctx) as pb:
        click.echo(f"Pinging Pixelblaze at {ctx.obj['ip']}...\n", err=True)

        times = []
        successful = 0
        failed = 0

        for i in range(count):
            try:
                start = time.time()
                response = pb.sendPing()
                elapsed = (time.time() - start) * 1000

                if response is not None:
                    successful += 1
                    times.append(elapsed)
                    click.echo(f"Ping {i+1}: {elapsed:.2f}ms", err=True)
                else:
                    failed += 1
                    click.echo(f"Ping {i+1}: timeout", err=True)

                if i < count - 1:
                    time.sleep(0.1)

            except Exception as e:
                failed += 1
                click.echo(f"Ping {i+1}: error - {e}", err=True)

        if times:
            min_time = min(times)
            max_time = max(times)
            avg_time = sum(times) / len(times)

            click.echo(f"\n--- Ping statistics ---", err=True)
            click.echo(f"Packets: Sent = {count}, Received = {successful}, Lost = {failed} ({failed*100//count}% loss)", err=True)
            click.echo(f"Round-trip times: min = {min_time:.2f}ms, max = {max_time:.2f}ms, avg = {avg_time:.2f}ms", err=True)

            click.echo(f"{avg_time:.2f}")
        else:
            click.echo(f"\nAll pings failed", err=True)
            raise click.ClickException("Failed to ping Pixelblaze")


@cli.command()
@click.argument('level', type=float, required=False)
@click.option('--no-save', is_flag=True, help='Do not save brightness to flash (temporary change only)')
@click.pass_context
def brightness(ctx, level, no_save):
    """
    Get or set the brightness level.

    LEVEL is a float between 0.0 and 1.0 (optional).

    \b
    Examples:
        pb brightness           # Get current brightness
        pb brightness 0.5       # Set brightness to 50% (saved to flash)
        pb brightness 0 --no-save  # Set to 0 (temporary only)
        pb brightness 1         # Set to full brightness (saved to flash)
    """
    with get_pixelblaze(ctx) as pb:
        if level is None:
            current = pb.getBrightnessSlider()
            click.echo(f"{current:.2f}")
        else:
            if not 0.0 <= level <= 1.0:
                raise click.ClickException("Brightness must be between 0.0 and 1.0")

            # Set brightness (fire-and-forget command)
            click.echo(f"Setting brightness to {level}...", err=True)
            pb.setBrightnessSlider(level, saveToFlash=not no_save)

            action = "set" if no_save else "saved"
            click.echo(f"Brightness {action} to {level}", err=True)


@cli.command()
@click.argument('count', type=int, required=False)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save pixel count to flash (temporary change only)'
)
@click.pass_context
def pixels(ctx, count, no_save):
    """
    Get or set the number of pixels configured on the Pixelblaze.

    \b
    Examples:
        pb pixels              # Get current pixel count
        pb pixels 300          # Set pixel count to 300 (saved to flash)
        pb pixels 300 --no-save   # Set pixel count to 300 (temporary only)
    """
    with get_pixelblaze(ctx) as pb:
        if count is None:
            current_count = pb.getPixelCount()
            click.echo(f"{current_count}")
        else:
            pb.setPixelCount(count, saveToFlash=not no_save)
            action = "set" if no_save else "saved"
            click.echo(f"Pixel count {action} to {count}", err=True)


@cli.command()
@click.argument('mapfile', type=click.File('r'), required=False)
@click.option(
    '--coords',
    is_flag=True,
    help='Show pixel coordinates instead of map function code'
)
@click.option(
    '--raw',
    is_flag=True,
    help='Output raw JSON instead of pretty-printed'
)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save the map to flash (temporary change only)'
)
@click.pass_context
def map(ctx, mapfile, coords, raw, no_save):
    """
    Get or set the pixel map function.

    MAPFILE is an optional JavaScript file to load as the map function.
    If no file is provided, the current map function is displayed.

    \b
    Examples:
        pb map                       # Get current map function
        pb map map.js                # Set map from file (saved to flash)
        pb map --coords              # Show pixel coordinates (normalized 0-1)
        pb map map.js --no-save      # Set map from file (temporary only)
        pb map --coords --raw        # Show coordinates as raw JSON
    """
    with get_pixelblaze(ctx) as pb:
        if mapfile is not None:
            mapFunction = mapfile.read()
            click.echo(f"Setting map function from {mapfile.name}...", err=True)
            pb.setMapFunction(mapFunction)

            if not no_save:
                click.echo("Saving map to flash...", err=True)

            action = "set" if no_save else "saved"
            click.echo(f"Map function {action}", err=True)
        else:
            if coords:
                click.echo("Fetching pixel coordinates...", err=True)
                coordinates = pb.getMapCoordinates()

                if raw:
                    click.echo(json.dumps(coordinates, separators=(',', ':')))
                else:
                    click.echo(json.dumps(coordinates, indent=2))
            else:
                click.echo("Fetching map function...", err=True)
                mapFunction = pb.getMapFunction()
                click.echo(mapFunction)


@cli.command()
@click.argument('brightness', type=float, default=1.0, required=False)
@click.option(
    '--play-sequencer',
    is_flag=True,
    help='Also start/resume the pattern sequencer'
)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save the on state to flash (temporary change only)'
)
@click.pass_context
def on(ctx, brightness, play_sequencer, no_save):
    """
    Turn on the Pixelblaze by setting brightness.

    This command sets the brightness to the specified level (default: 1.0).
    Optionally, you can also start/resume the sequencer.

    \b
    Examples:
        pb on                       # Set brightness to 1.0 (full, saved to flash)
        pb on 0.5                   # Set brightness to 50% (saved to flash)
        pb on --play-sequencer      # Set brightness to 1.0 and start sequencer (saved)
        pb on 0.8 --no-save         # Set brightness to 80% (temporary only)
    """
    if not 0.0 <= brightness <= 1.0:
        raise click.ClickException("Brightness must be between 0.0 and 1.0")

    with get_pixelblaze(ctx) as pb:
        click.echo(f"Setting brightness to {brightness}...", err=True)
        pb.setBrightnessSlider(brightness, saveToFlash=not no_save)

        if play_sequencer:
            click.echo("Starting sequencer...", err=True)
            pb.playSequencer(saveToFlash=not no_save)

        action = "turned on" if no_save else "saved and turned on"
        click.echo(f"Pixelblaze {action} (brightness: {brightness})", err=True)


@cli.command()
@click.option(
    '--pause-sequencer',
    is_flag=True,
    help='Also pause the pattern sequencer'
)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save the off state to flash (temporary change only)'
)
@click.pass_context
def off(ctx, pause_sequencer, no_save):
    """
    Turn off the Pixelblaze by setting brightness to zero.

    This command sets the brightness to 0, effectively turning off all LEDs.
    Optionally, you can also pause the sequencer to stop pattern changes.

    \b
    Examples:
        pb off                      # Set brightness to 0 (saved to flash)
        pb off --pause-sequencer    # Set brightness to 0 and pause sequencer (saved)
        pb off --no-save            # Set brightness to 0 (temporary only)
    """
    with get_pixelblaze(ctx) as pb:
        click.echo("Setting brightness to 0...", err=True)
        pb.setBrightnessSlider(0.0, saveToFlash=not no_save)

        if pause_sequencer:
            click.echo("Pausing sequencer...", err=True)
            pb.pauseSequencer(saveToFlash=not no_save)

        action = "turned off" if no_save else "saved and turned off"
        click.echo(f"Pixelblaze {action}", err=True)


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
    '--no-save',
    is_flag=True,
    help='Do not save paused state to flash (temporary change only)'
)
@click.pass_context
def pause(ctx, no_save):
    """
    Pause the pattern sequencer.

    \b
    Examples:
        pb seq pause           # Pause sequencer (saved to flash)
        pb seq pause --no-save    # Pause (temporary only)
    """
    with get_pixelblaze(ctx) as pb:
        click.echo("Pausing sequencer...", err=True)
        pb.pauseSequencer(saveToFlash=not no_save)
        action = "paused" if no_save else "paused and saved"
        click.echo(f"Sequencer {action}", err=True)


@seq.command()
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save playing state to flash (temporary change only)'
)
@click.pass_context
def play(ctx, no_save):
    """
    Start/resume the pattern sequencer.

    \b
    Examples:
        pb seq play           # Start/resume sequencer (saved to flash)
        pb seq play --no-save    # Start (temporary only)
    """
    with get_pixelblaze(ctx) as pb:
        click.echo("Starting sequencer...", err=True)
        pb.playSequencer(saveToFlash=not no_save)
        action = "started" if no_save else "started and saved"
        click.echo(f"Sequencer {action}", err=True)


@seq.command()
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save state to flash (temporary change only)'
)
@click.pass_context
def next(ctx, no_save):
    """
    Advance to the next pattern in the sequence.

    Works with ShuffleAll or Playlist sequencer modes.

    \b
    Examples:
        pb seq next           # Next pattern (saved to flash)
        pb seq next --no-save    # Next pattern (temporary only)
    """
    with get_pixelblaze(ctx) as pb:
        click.echo("Advancing to next pattern...", err=True)
        pb.nextSequencer(saveToFlash=not no_save)
        action = "Advanced to next pattern" if no_save else "Advanced to next pattern and saved"
        click.echo(action, err=True)


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
    import random as rand

    with get_pixelblaze(ctx) as pb:
        click.echo("Getting pattern list...", err=True)
        patterns = pb.getPatternList()

        if not patterns:
            raise click.ClickException("No patterns found on Pixelblaze")

        pattern_id = rand.choice(list(patterns.keys()))
        pattern_name = patterns[pattern_id]

        click.echo(f"Selecting random pattern: {pattern_name}", err=True)
        pb.setActivePattern(pattern_id)
        click.echo(f"Now playing: {pattern_name}", err=True)


@seq.command()
@click.argument('seconds', type=float)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save updated playlist to flash (temporary change only)'
)
@click.pass_context
def len(ctx, seconds, no_save):
    """
    Set the duration for all patterns in the playlist.

    SECONDS is the duration in seconds for each pattern.

    This modifies the default playlist by setting all pattern durations
    to the specified value.

    \b
    Examples:
        pb seq len 10          # Set all durations to 10 seconds (saved)
        pb seq len 30 --no-save   # Set to 30 seconds (temporary only)
    """
    if seconds <= 0:
        raise click.ClickException("Duration must be greater than 0")

    with get_pixelblaze(ctx) as pb:
        milliseconds = int(seconds * 1000)

        click.echo("Getting current playlist...", err=True)
        playlist = pb.getSequencerPlaylist()

        if 'playlist' not in playlist or 'items' not in playlist['playlist']:
            raise click.ClickException("Invalid playlist structure")

        items = playlist['playlist']['items']
        if not items:
            raise click.ClickException("Playlist is empty")

        original_count = len(items)
        for item in items:
            item['ms'] = milliseconds

        click.echo(f"Setting {original_count} pattern(s) to {seconds} seconds each...", err=True)
        pb.setSequencerPlaylist(playlist)

        if not no_save:
            click.echo("Saving playlist to flash...", err=True)

        action = "set" if no_save else "saved"
        click.echo(f"Playlist updated and {action}: all patterns set to {seconds}s", err=True)


@cli.command()
@click.argument('search', type=str)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save pattern selection to flash (temporary change only)'
)
@click.option(
    '--exact',
    is_flag=True,
    help='Require exact match instead of partial regex match'
)
@click.pass_context
def pattern(ctx, search, no_save, exact):
    """
    Switch to a pattern by name (case-insensitive partial match).

    SEARCH is a string or regex pattern to match against pattern names.
    The first matching pattern will be activated.

    \b
    Examples:
        pb pattern rainbow              # Match "Rainbow" or "rainbow wave" (saved)
        pb pattern "^glit"              # Match patterns starting with "glit" (saved)
        pb pattern fire --no-save       # Match "fire" (temporary only)
        pb pattern "sound.*react"       # Regex: "sound" followed by "react" (saved)
        pb pattern exact --exact        # Exact match only (case-insensitive, saved)
    """
    with get_pixelblaze(ctx) as pb:
        click.echo("Fetching pattern list...", err=True)
        patterns = pb.getPatternList()

        if not patterns:
            raise click.ClickException("No patterns found on Pixelblaze")

        pattern_regex = re.compile(search, re.IGNORECASE)
        matched_id = None
        matched_name = None

        for pattern_id, pattern_name in patterns.items():
            if exact:
                if pattern_name.lower() == search.lower():
                    matched_id = pattern_id
                    matched_name = pattern_name
                    break
            else:
                if pattern_regex.search(pattern_name):
                    matched_id = pattern_id
                    matched_name = pattern_name
                    break

        if not matched_id:
            click.echo(f"\nNo pattern matching '{search}' found.", err=True)
            click.echo("\nAvailable patterns:", err=True)
            for pattern_name in sorted(patterns.values()):
                click.echo(f"  - {pattern_name}", err=True)
            raise click.ClickException(f"Pattern '{search}' not found")

        click.echo(f"Switching to pattern: {matched_name}", err=True)
        pb.setActivePattern(matched_id, saveToFlash=not no_save)

        action = "activated" if no_save else "saved and activated"
        click.echo(f"Pattern '{matched_name}' {action}", err=True)


@cli.command(
    name='config',
    # aliases=['cfg'] # why is this missing?
)
@click.option(
    '--raw',
    is_flag=True,
    help='Output raw JSON instead of pretty-printed'
)
@click.pass_context
def config_cmd(ctx, raw):
    """
    Fetch and display all configuration from the Pixelblaze.

    This mimics the web UI's initial configuration fetch, retrieving:
    - Device config
    - Pattern list
    - Playlist
    - Sequencer settings
    - And more

    \b
    Examples:
        pb config              # Pretty-printed JSON
        pb config --raw        # Raw JSON for scripting
    """
    with get_pixelblaze(ctx) as pb:
        click.echo("Fetching configuration...", err=True)

        config_data = {}

        try:
            config_data['config'] = pb.getConfigSettings()
        except Exception as e:
            click.echo(f"Warning: Could not fetch config: {e}", err=True)

        try:
            config_data['patterns'] = pb.getPatternList()
        except Exception as e:
            click.echo(f"Warning: Could not fetch patterns: {e}", err=True)

        try:
            config_data['playlist'] = pb.getSequencerPlaylist()
        except Exception as e:
            click.echo(f"Warning: Could not fetch playlist: {e}", err=True)

        try:
            config_data['sequencer'] = pb.getConfigSequencer()
        except Exception as e:
            click.echo(f"Warning: Could not fetch sequencer: {e}", err=True)

        if raw:
            click.echo(json.dumps(config_data, separators=(',', ':')))
        else:
            click.echo(json.dumps(config_data, indent=2))


@cli.command()
@click.argument('json_data', type=str)
@click.option(
    '--expect',
    type=str,
    help='Expected response key (e.g., "ack", "playlist")'
)
@click.option(
    '--timeout',
    type=float,
    default=5.0,
    help='Response timeout in seconds'
)
@click.pass_context
def ws(ctx, json_data, expect, timeout):
    """
    Send arbitrary JSON to the Pixelblaze websocket.

    JSON_DATA is the JSON object to send (as a string).

    \b
    Examples:
        pb ws '{"ping":true}'
        pb ws '{"getConfig":true}' --expect config
        pb ws '{"brightness":0.5,"save":false}'
        pb ws '{"activeProgramId":"abc123","save":true}'
        pb ws '{"getPlaylist":"_defaultplaylist_"}' --expect playlist
    """
    pb = get_pixelblaze(ctx)

    try:
        json_obj = json.loads(json_data)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON: {e}")

    with get_pixelblaze(ctx) as pb:
        click.echo(f"Sending: {json.dumps(json_obj, separators=(',', ':'))}", err=True)

        # Send the websocket message
        # If no --expect is provided, wait for any non-chatty text response
        if expect == "stats":
            expect = pb.messageTypes.specialStats
        response = pb.wsSendJson(json_obj, expectedResponse=expect, waitForAnyResponse=(expect is None))

        if response is None:
            click.echo("No response (fire-and-forget command)", err=True)
        elif isinstance(response, bytes):
            click.echo("Binary response:", err=True)
            click.echo(response.hex())
        else:
            click.echo("Response:", err=True)
            try:
                # Try to pretty-print JSON response
                response_json = json.loads(response)
                click.echo(json.dumps(response_json, indent=2))
            except:
                # Not JSON, just print it
                click.echo(response)


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
    if code is None:
        if not sys.stdin.isatty():
            code = sys.stdin.read().strip()
        else:
            raise click.ClickException(
                "No code provided. Supply code as an argument or pipe it via stdin."
            )

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

    with get_pixelblaze(ctx) as pb:
        click.echo("Compiling pattern...", err=True)
        bytecode = pb.compilePattern(code)

        click.echo("Sending to renderer...", err=True)
        pb.sendPatternToRenderer(bytecode)

        if variables:
            click.echo(f"Setting variables: {variables}", err=True)
            pb.setActiveVariables(variables)

        click.echo("Pattern rendered successfully", err=True)


def main():
    """Entry point for the CLI."""
    cli(obj={})


if __name__ == '__main__':
    main()
