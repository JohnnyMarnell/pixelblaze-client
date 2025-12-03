#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Command-line interface for Pixelblaze LED controllers.

This module provides a modern CLI tool for controlling Pixelblazes with
flexible discovery, pattern rendering, and configuration management.
"""

import sys
import json
import time
import re
import click
from typing import Dict
from pixelblaze.pixelblaze import Pixelblaze
from pixelblaze.cli_utils import cli, log


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
def pixelblaze(ctx, ip, timeout, no_verify):
    """
    Pixelblaze LED Controller CLI

    Control Pixelblaze devices from the command line.
    """
    ctx.ensure_object(dict)
    ctx.obj['ip'] = ip
    ctx.obj['timeout'] = timeout
    ctx.obj['no_verify'] = no_verify


@cli(pixelblaze)
@click.argument('count', type=int, required=False)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save pixel count to flash (temporary change only)'
)
def pixels(pb: Pixelblaze, count, no_save):
    """
    Get or set the number of pixels configured on the Pixelblaze.

    \b
    Examples:
        pb pixels              # Get current pixel count
        pb pixels 300          # Set pixel count to 300 (saved to flash)
        pb pixels 300 --no-save   # Set pixel count to 300 (temporary only)
    """
    if count is None:
        current_count = pb.getPixelCount()
        click.echo(f"{current_count}")
    else:
        pb.setPixelCount(count, saveToFlash=not no_save)
        action = "set" if no_save else "saved"
        log(f"Pixel count {action} to {count}")


@cli(pixelblaze)
@click.argument('level', type=float, required=False)
@click.option('--no-save', is_flag=True, help='Do not save brightness to flash (temporary change only)')
def brightness(pb: Pixelblaze, level, no_save):
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
    if level is None:
        current = pb.getBrightnessSlider()
        click.echo(f"{current:.2f}")
    else:
        if not 0.0 <= level <= 1.0:
            raise click.ClickException("Brightness must be between 0.0 and 1.0")

        # Set brightness (fire-and-forget command)
        log(f"Setting brightness to {level}...")
        pb.setBrightnessSlider(level, saveToFlash=not no_save)

        action = "set" if no_save else "saved"
        log(f"Brightness {action} to {level}")


@cli(pixelblaze)
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
def map(pb: Pixelblaze, mapfile, coords, raw, no_save):
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
    if mapfile is not None:
        mapFunction = mapfile.read()
        log(f"Setting map function from {mapfile.name}...")
        pb.setMapFunction(mapFunction)

        if not no_save:
            log("Saving map to flash...")

        action = "set" if no_save else "saved"
        log(f"Map function {action}")
    else:
        if coords:
            log("Fetching pixel coordinates...")
            coordinates = pb.getMapCoordinates()

            if raw:
                click.echo(json.dumps(coordinates, separators=(',', ':')))
            else:
                click.echo(json.dumps(coordinates, indent=2))
        else:
            log("Fetching map function...")
            mapFunction = pb.getMapFunction()
            click.echo(mapFunction)


@cli(pixelblaze)
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
def on(pb: Pixelblaze, brightness, play_sequencer, no_save):
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

    log(f"Setting brightness to {brightness}...")
    pb.setBrightnessSlider(brightness, saveToFlash=not no_save)

    if play_sequencer:
        log("Starting sequencer...")
        pb.playSequencer(saveToFlash=not no_save)

    action = "turned on" if no_save else "saved and turned on"
    log(f"Pixelblaze {action} (brightness: {brightness})")


@cli(pixelblaze)
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
def off(pb: Pixelblaze, pause_sequencer, no_save):
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
    log("Setting brightness to 0...")
    pb.setBrightnessSlider(0.0, saveToFlash=not no_save)

    if pause_sequencer:
        log("Pausing sequencer...")
        pb.pauseSequencer(saveToFlash=not no_save)

    action = "turned off" if no_save else "saved and turned off"
    log(f"Pixelblaze {action}")


@pixelblaze.group()
@click.pass_context
def seq(ctx):
    """
    Sequencer and playlist control commands.

    Control the Pixelblaze pattern sequencer, including play/pause,
    navigation, and playlist management.
    """
    pass


@cli(seq)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save paused state to flash (temporary change only)'
)
def pause(pb: Pixelblaze, no_save):
    """
    Pause the pattern sequencer.

    \b
    Examples:
        pb seq pause           # Pause sequencer (saved to flash)
        pb seq pause --no-save    # Pause (temporary only)
    """
    log("Pausing sequencer...")
    pb.pauseSequencer(saveToFlash=not no_save)
    action = "paused" if no_save else "paused and saved"
    log(f"Sequencer {action}")


@cli(seq)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save playing state to flash (temporary change only)'
)
def play(pb: Pixelblaze, no_save):
    """
    Start/resume the pattern sequencer.

    \b
    Examples:
        pb seq play           # Start/resume sequencer (saved to flash)
        pb seq play --no-save    # Start (temporary only)
    """
    log("Starting sequencer...")
    pb.playSequencer(saveToFlash=not no_save)
    action = "started" if no_save else "started and saved"
    log(f"Sequencer {action}")


@cli(seq)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save state to flash (temporary change only)'
)
def next(pb: Pixelblaze, no_save):
    """
    Advance to the next pattern in the sequence.

    Works with ShuffleAll or Playlist sequencer modes.

    \b
    Examples:
        pb seq next           # Next pattern (saved to flash)
        pb seq next --no-save    # Next pattern (temporary only)
    """
    log("Advancing to next pattern...")
    pb.nextSequencer(saveToFlash=not no_save)
    action = "Advanced to next pattern" if no_save else "Advanced to next pattern and saved"
    log(action)


@cli(seq)
def random(pb: Pixelblaze):
    """
    Jump to a random pattern.

    Selects a random pattern from all available patterns on the Pixelblaze.

    \b
    Examples:
        pb seq random    # Jump to random pattern
    """
    import random as rand

    log("Getting pattern list...")
    patterns = pb.getPatternList()

    if not patterns:
        raise click.ClickException("No patterns found on Pixelblaze")

    pattern_id = rand.choice(list(patterns.keys()))
    pattern_name = patterns[pattern_id]

    log(f"Selecting random pattern: {pattern_name}")
    pb.setActivePattern(pattern_id)
    log(f"Now playing: {pattern_name}")


@cli(seq)
@click.argument('seconds', type=float)
@click.option(
    '--no-save',
    is_flag=True,
    help='Do not save updated playlist to flash (temporary change only)'
)
def len(pb: Pixelblaze, seconds, no_save):
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

    milliseconds = int(seconds * 1000)

    log("Getting current playlist...")
    playlist = pb.getSequencerPlaylist()

    if 'playlist' not in playlist or 'items' not in playlist['playlist']:
        raise click.ClickException("Invalid playlist structure")

    items = playlist['playlist']['items']
    if not items:
        raise click.ClickException("Playlist is empty")

    original_count = len(items)
    for item in items:
        item['ms'] = milliseconds

    log(f"Setting {original_count} pattern(s) to {seconds} seconds each...")
    pb.setSequencerPlaylist(playlist)

    if not no_save:
        log("Saving playlist to flash...")

    action = "set" if no_save else "saved"
    log(f"Playlist updated and {action}: all patterns set to {seconds}s")


@cli(pixelblaze)
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
def pattern(pb: Pixelblaze, search, no_save, exact):
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
    log("Fetching pattern list...")
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
        log(f"\nNo pattern matching '{search}' found.")
        log("\nAvailable patterns:")
        for pattern_name in sorted(patterns.values()):
            log(f"  - {pattern_name}")
        raise click.ClickException(f"Pattern '{search}' not found")

    log(f"Switching to pattern: {matched_name}")
    pb.setActivePattern(matched_id, saveToFlash=not no_save)

    action = "activated" if no_save else "saved and activated"
    log(f"Pattern '{matched_name}' {action}")


@cli(pixelblaze, name='config')
@click.option(
    '--raw',
    is_flag=True,
    help='Output raw JSON instead of pretty-printed'
)
def config_cmd(pb: Pixelblaze, raw):
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
    log("Fetching configuration...")

    config_data = {}

    try:
        config_data['config'] = pb.getConfigSettings()
    except Exception as e:
        log(f"Warning: Could not fetch config: {e}")

    try:
        config_data['patterns'] = pb.getPatternList()
    except Exception as e:
        log(f"Warning: Could not fetch patterns: {e}")

    try:
        config_data['playlist'] = pb.getSequencerPlaylist()
    except Exception as e:
        log(f"Warning: Could not fetch playlist: {e}")

    try:
        config_data['sequencer'] = pb.getConfigSequencer()
    except Exception as e:
        log(f"Warning: Could not fetch sequencer: {e}")

    if raw:
        click.echo(json.dumps(config_data, separators=(',', ':')))
    else:
        click.echo(json.dumps(config_data, indent=2))


@cli(pixelblaze)
@click.argument('json_data', type=str)
@click.option(
    '--expect',
    type=str,
    help='Expected response key (e.g., "ack", "playlist")'
)
def ws(pb: Pixelblaze, json_data, expect):
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
    json_obj = json.loads(json_data)

    log(f"Sending: {json.dumps(json_obj, separators=(',', ':'))}")

    # Send the websocket message
    # If no --expect is provided, wait for any non-chatty text response
    if expect == "stats":
        expect = pb.messageTypes.specialStats
    response = pb.wsSendJson(json_obj, expectedResponse=expect, waitForAnyResponse=(expect is None))

    if response is None:
        log("No response (fire-and-forget command)")
    elif isinstance(response, bytes):
        log("Binary response:")
        click.echo(response.hex())
    else:
        log("Response:")
        try:
            # Try to pretty-print JSON response
            response_json = json.loads(response)
            click.echo(json.dumps(response_json, indent=2))
        except:
            # Not JSON, just print it
            click.echo(response)


@cli(pixelblaze)
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
def render(pb: Pixelblaze, code, vars, var_pairs):
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

    log("Compiling pattern...")
    bytecode = pb.compilePattern(code)

    log("Sending to renderer...")
    pb.sendPatternToRenderer(bytecode)

    if variables:
        log(f"Setting variables: {variables}")
        pb.setActiveVariables(variables)

    log("Pattern rendered successfully")


@click.option(
    '--count',
    '-c',
    type=int,
    default=5,
    help='Number of pings to send (default: 5)'
)
@cli(pixelblaze)
def ping(pb: Pixelblaze, count):
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
    log(f"Pinging Pixelblaze...\n")

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
                log(f"Ping {i+1}: {elapsed:.2f}ms")
            else:
                failed += 1
                log(f"Ping {i+1}: timeout")

            if i < count - 1:
                time.sleep(0.1)

        except Exception as e:
            failed += 1
            log(f"Ping {i+1}: error - {e}")

    if times:
        min_time = min(times)
        max_time = max(times)
        avg_time = sum(times) / len(times)

        log(f"\n--- Ping statistics ---")
        log(f"Packets: Sent = {count}, Received = {successful}, Lost = {failed} ({failed*100//count}% loss)")
        log(f"Round-trip times: min = {min_time:.2f}ms, max = {max_time:.2f}ms, avg = {avg_time:.2f}ms")

        click.echo(f"{avg_time:.2f}")
    else:
        log(f"\nAll pings failed")
        raise click.ClickException("Failed to ping Pixelblaze")


def main():
    """Entry point for the CLI."""
    pixelblaze(obj={})


if __name__ == '__main__':
    main()
