#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Command-line interface for Pixelblaze LED controllers.

This module provides a modern CLI tool for controlling Pixelblazes with
flexible discovery, pattern rendering, and configuration management.
"""

import json as jsonlib
import time
import re
import click
from typing import Dict
from pixelblaze.pixelblaze import Pixelblaze, PBB
from pixelblaze.cli_utils import cli, log, no_save_option, input_arg, read_input, parse_json, jsons

@click.group()
@click.option(
    '--ip',
    default='auto',
    help='IP address of Pixelblaze (default: auto discover mode, checks 192.168.4.1 first for Ad Hoc, then network scan)',
    show_default=True
)
@click.option(
    '--timeout',
    type=float,
    default=5.0,
    help='Command timeout in seconds (default: 5.0)',
    show_default=True
)
@click.pass_context
def pixelblaze(ctx, ip, timeout):
    """
    Pixelblaze LED Controller CLI

    Control Pixelblaze devices from the command line.
    """
    ctx.ensure_object(dict)
    ctx.obj['ip'] = ip
    ctx.obj['timeout'] = timeout


@cli(pixelblaze)
@click.argument('count', type=int, required=False)
@no_save_option
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
@click.argument('brightness', type=float, default=1.0, required=False)
@click.option(
    '--play-sequencer',
    is_flag=True,
    help='Also start/resume the pattern sequencer'
)
@no_save_option
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
@no_save_option
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


@cli(pixelblaze)
@input_arg
@click.option('--csv', is_flag=True, help='Output as csv instead of Pixelblaze 3-arrays')
def map(pb: Pixelblaze, input, csv):
    """
    Get or set the pixel map function.

    INPUT is an optional JavaScript file path or inline code. Can also be
    piped via stdin. If no input is provided, the current map function
    is displayed.

    \b
    Examples:
        pb map                       # Get current map coordinates (normalized 0-1)
        pb map map.js                # Set map from file
        pb map < map.js              # Set map from stdin
    """
    content = read_input(input, "map", required=False)
    setting = content is not None

    if setting:
        if "function" in content:
            log(f"Setting map function...")
            pb.setMapFunction(content)
        else:
            # Also supporting numbers as strings
            log(f"Setting map coordinates...")
            pb.setMapCoordinates(parse_json(jsonlib.dumps(parse_json(content)).replace('"', "")))
    elif csv:
        log(f"Fetching map coordinates as CSV...")
        coords = pb.getMapCoordinates()
        click.echo("index,x,y,z")
        for i in range(0, len(coords[0])):
            click.echo(f"{i},{coords[0][i]},{coords[1][i]},{coords[2][i]}")
    else:
        log(f"Fetching map config...")
        jsons({'coordinates': pb.getMapCoordinates(), 'fn': pb.getMapFunction()})


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
@no_save_option
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
@no_save_option
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
@no_save_option
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


@cli(seq, name='len')
@click.argument('seconds', type=float)
@no_save_option
def set_duration(pb: Pixelblaze, seconds, no_save):
    """
    Set the duration for all patterns in the sequencer playlist.

    SECONDS is the duration in seconds for each pattern.

    Updates the sequencer playlist to change pattern durations.

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
@no_save_option
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


@cli(pixelblaze)
def cfg(pb: Pixelblaze):
    """
    Fetch and display most of the configuration from the Pixelblaze.

    This mimics the web UI's initial configuration fetch, retrieving:
    - Device config
    - Pattern list
    - Playlist
    - Sequencer settings
    - And more

    \b
    Examples:
        pb cfg
        pb cfg | yq -P          # Pretty-printed YAML
    """
    log("Fetching configurations...")
    jsons({
        'config': pb.getConfigSettings(),
        'patterns': pb.getPatternList(),
        'playlist': pb.getSequencerPlaylist(),
        'sequencer': pb.getConfigSequencer()
    })


@cli(pixelblaze)
@click.argument('json_data', type=str)
@click.option(
    '--expect',
    type=str,
    help='Expected response key (e.g., "ack", "playlist")'
)
def ws(pb: Pixelblaze, json, expect):
    """
    Send arbitrary JSON to the Pixelblaze websocket.

    JSON_DATA is the JSON object to send (as a string).

    \b
    Examples:
        pb ws '{ping:true}'
        pb ws '{"getConfig":true}' --expect config
        pb ws '{brightness:0.5, save:false}'
        pb ws '{activeProgramId:"abc123", save:true}'
        pb ws '{'getPlaylist':"_defaultplaylist_"}' --expect playlist
    """
    json_obj = parse_json(json)

    # Send the websocket message, if no --expect is provided, wait for any non-chatty text response
    if expect == "stats":
        expect = pb.messageTypes.specialStats
    response = pb.wsSendJson(json_obj, expectedResponse=expect, waitForAnyResponse=(expect is None))

    if response is None:
        log("No response (fire-and-forget command?)")
    elif isinstance(response, bytes):
        log("Binary response:")
        click.echo(response.hex())
    else:
        log("Response:")
        try:
            jsons(response)
        except:
            # Not JSON, just print it
            click.echo(response)


@cli(pixelblaze)
@input_arg
@click.option(
    '--vars',
    type=str,
    help='JSON dictionary of variables to set (e.g., \'{"speed": 0.5, "patternColor": 1.0}\')'
)
@click.option(
    '--var',
    'var_pairs',
    multiple=True,
    help='Individual variable as key:value pair (can be used multiple times, e.g., --var speed:0.5 --var patternColor:1.0)'
)
def render(pb: Pixelblaze, input, vars, var_pairs):
    """
    Send JavaScript code to the Pixelblaze renderer.

    Code can be provided inline, from a file, or piped via stdin.
    Variables can be set using --vars (JSON5) or --var (key:value pairs).

    \b
    Examples:
        pb render "export function render(index) { hsv(0.5, 1, 1) }"
        pb render code.js                                            # Read from file
        echo "..." | pb render                                       # Pipe from stdin
        pb render code.js --var speed:0.5 --var patternColor:1.0
        pb render code.js --vars '{speed: 0.5, patternColor: 1.0}'   # JSON5: unquoted keys
    """
    code = read_input(input, "code")
    if ("export" not in code):
        code = 'export function render(index) { ' + code + ' ; }'

    variables: Dict[str, float] = {}

    if vars:
        variables = parse_json(vars)

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


@click.command()
@click.argument('output_file', required=False)
@click.option('--ip', default='auto', help='IP address of Pixelblaze')
@click.option('--quiet', '-q', is_flag=True, help='Suppress verbose progress output')
@click.option('--decode', '-d', is_flag=True, help='Decode base64 file contents in output')
@click.option('--binary', is_flag=True, help='Include binary content as base64 (with --decode)')
@click.pass_context
def pbb(ctx, output_file, ip, quiet, decode, binary):
    """
    Export a Pixelblaze Binary Backup (.pbb file).

    Backs up the entire Pixelblaze configuration including patterns, settings,
    map data, and all configuration files.

    If no output file is specified, outputs to stdout as JSON.
    If a filename is provided, saves as a .pbb file (auto-appends .pbb extension if missing).
    If the file already exists, uses that file instead of connecting to Pixelblaze.

    \b
    Examples:
        pb --ip 192.168.1.24 pbb                    # Output to stdout
        pb --ip 192.168.1.24 pbb backup             # Save to backup.pbb
        pb --ip 192.168.1.24 pbb my_config.pbb      # Save to my_config.pbb
        pb --ip 192.168.1.24 pbb -q backup          # Quiet mode
        pb pbb -d backup.pbb                        # Decode existing file
        pb pbb -d --binary backup.pbb               # Include binary as base64
    """
    import pathlib
    import base64

    # Determine the actual file path
    if output_file:
        if not output_file.endswith('.pbb'):
            output_file += '.pbb'

    # Check if file exists and should be used
    if output_file and pathlib.Path(output_file).exists():
        log(f"Using existing file: {output_file}")
        temp_file = output_file
        should_cleanup = False
    else:
        # Need to fetch from Pixelblaze
        from pixelblaze.cli_utils import get_pixelblaze
        ctx.obj = ctx.obj or {}
        ctx.obj['ip'] = ip

        log("Creating backup from Pixelblaze...")
        with get_pixelblaze(ctx) as pb:
            backup = PBB.fromPixelblaze(pb, verbose=not quiet)

            if output_file:
                backup.toFile(output_file)
                log(f"Backup saved to {output_file}")
                return
            else:
                temp_file = '/tmp/pixelblaze_backup_temp.pbb'
                backup.toFile(temp_file)
                should_cleanup = True

    # Read and potentially decode the file
    content = pathlib.Path(temp_file).read_text()

    if decode:
        # Decode base64 entries
        import struct
        from pixelblaze.pixelblaze import PBP

        data = jsonlib.loads(content)
        if 'files' in data:
            decoded_files = {}
            for filename, b64_content in data['files'].items():
                try:
                    # Decode base64
                    decoded = base64.b64decode(b64_content)

                    # Check if it's a PBP (Pixelblaze Binary Pattern) file
                    if filename.startswith('/p/') and len(decoded) > 36:
                        try:
                            # Parse PBP format
                            pbp = PBP.fromBytes(filename.split('/')[-1], decoded)
                            decoded_files[filename] = {
                                'name': pbp.name,
                                'sourceCode': jsonlib.loads(pbp.sourceCode),
                                'preview': '<jpeg>' if not binary else base64.b64encode(pbp.jpeg).decode('utf-8'),
                                'byteCode': '<bytecode>' if not binary else base64.b64encode(pbp.byteCode).decode('utf-8')
                            }
                            continue
                        except Exception:
                            # Not a valid PBP, try other formats
                            pass

                    # Try to parse as UTF-8 text
                    try:
                        text = decoded.decode('utf-8')
                        # Try to parse as JSON
                        try:
                            decoded_files[filename] = jsonlib.loads(text)
                        except jsonlib.JSONDecodeError:
                            # Not JSON, just text
                            decoded_files[filename] = text
                    except UnicodeDecodeError:
                        # Binary content (images, etc.)
                        if binary:
                            decoded_files[filename] = b64_content
                        else:
                            # Detect image format
                            if decoded.startswith(b'\xff\xd8\xff'):
                                decoded_files[filename] = '<jpeg>'
                            elif decoded.startswith(b'\x89PNG'):
                                decoded_files[filename] = '<png>'
                            else:
                                decoded_files[filename] = '<binary>'
                except Exception:
                    # If decode fails, keep original
                    decoded_files[filename] = b64_content

            data['files'] = decoded_files
            content = jsonlib.dumps(data, indent=2)

    click.echo(content)
    if should_cleanup:
        pathlib.Path(temp_file).unlink()


@cli(pixelblaze)
@click.argument('input_file')
def restore(pb: Pixelblaze, input_file):
    """
    Restore a Pixelblaze from a Binary Backup (.pbb file).

    Restores the entire Pixelblaze configuration from a .pbb backup file,
    including patterns, settings, map data, and all configuration files.

    WARNING: This will overwrite all current patterns and settings!

    \b
    Examples:
        pb --ip 192.168.1.24 restore backup.pbb
        pb --ip 192.168.1.24 restore my_config.pbb
    """
    if not input_file.endswith('.pbb'):
        input_file += '.pbb'

    log(f"Restoring from {input_file}...")
    backup = PBB.fromFile(input_file)
    backup.toPixelblaze(pb)
    log("Restore complete!")


pixelblaze.add_command(pbb)


def main():
    """Entry point for the CLI."""
    pixelblaze(obj={})


if __name__ == '__main__':
    main()
