#!/usr/bin/env python3
"""Lightweight CLI tests for pixelblaze-client."""

import json
import shlex
from click.testing import CliRunner
from pixelblaze.cli import pixelblaze


def test_cli():
    """Run basic CLI tests."""
    print("Starting CLI tests...")

    # Get original state
    print("\n1. Getting original state...")
    result = cli(f'''
    
        % pb cfg
    
    ''')
    cfg = get_json_output(result)

    original_pixels = cfg['config'].get('pixelCount', 100)
    original_brightness = cfg['config'].get('brightness', 1.0)
    # Active pattern ID is in the sequencer config, not settings config
    original_pattern_id = cfg.get('sequencer', {}).get('activeProgram', {}).get('activeProgramId')
    original_pattern_name = cfg['patterns'].get(original_pattern_id, 'Unknown') if original_pattern_id else None
    print(f"   Original pixels: {original_pixels}")
    print(f"   Original brightness: {original_brightness}")
    print(f"   Original pattern: {original_pattern_name} ({original_pattern_id})")

    # Change pixels
    test_pixels = original_pixels + 10
    print(f"\n2. Setting pixels to {test_pixels} (+10 from original)...")
    cli(f'''
    
        % pb pixels {test_pixels}
    
    ''')

    # Verify pixels changed
    print("   Verifying pixels changed...")
    result = cli(f'''
    
        % pb pixels
    
    ''')
    new_pixels = int(result.output.strip().split('\n')[-1])
    assert new_pixels == test_pixels, f"Expected {test_pixels} pixels, got {new_pixels}"
    print(f"   ✓ Pixels set to {test_pixels}")

    # Change brightness
    print("\n3. Setting brightness to 0.2...")
    cli(f'''
    
        % pb on 0.2
        
    ''')
    print("   ✓ Brightness set to 0.2")

    # Verify pattern doesn't exist
    print("\n4. Verifying '__pb_cli_test__' doesn't exist...")
    result = cli('''
    
        % pb cfg
        
    ''')
    cfg = get_json_output(result)
    patterns = cfg['patterns']
    test_pattern_id = None
    for pid, name in patterns.items():
        if name == '__pb_cli_test__':
            test_pattern_id = pid
            break
    assert test_pattern_id is None, "Test pattern should not exist yet"
    print("   ✓ Test pattern doesn't exist")

    # Save test pattern
    print("\n5. Saving '__pb_cli_test__' pattern (single color)...")
    cli(f'''

        % pb pattern "rgb(.1, 0, .1)" --write __pb_cli_test__
    
    ''')
    print("   ✓ Pattern saved")

    # Verify pattern exists
    print("\n6. Verifying pattern was created...")
    result = cli(f''' % pb cfg ''')
    cfg = get_json_output(result)
    patterns = cfg['patterns']
    test_pattern_id = None
    for pid, name in patterns.items():
        if name == '__pb_cli_test__':
            test_pattern_id = pid
            break
    assert test_pattern_id is not None, "Test pattern should exist"
    print(f"   ✓ Pattern exists with ID: {test_pattern_id}")

    # Switch to test pattern
    print("\n7. Switching to test pattern...")
    cli(f'''
    
        % pb pattern __pb_cli_test__
    
    ''')
    print("   ✓ Switched to test pattern")

    # Verify we're on the test pattern
    print("\n8. Verifying test pattern is active...")
    result = cli(f''' % pb cfg ''')
    cfg = get_json_output(result)
    active_pattern_id = cfg.get('sequencer', {}).get('activeProgram', {}).get('activeProgramId')
    assert active_pattern_id == test_pattern_id, f"Expected active pattern {test_pattern_id}, got {active_pattern_id}"
    print(f"   ✓ Test pattern is active")

    # Delete test pattern
    print("\n9. Deleting '__pb_cli_test__' pattern...")
    cli(f'''
    
        % pb pattern __pb_cli_test__ --rm
    
    ''')
    print("   ✓ Pattern deleted")

    # Verify pattern doesn't exist anymore
    print("\n10. Verifying pattern was deleted...")
    result = cli(f'''
    
        % pb cfg
    
    ''')
    cfg = get_json_output(result)
    patterns = cfg['patterns']
    for pid, name in patterns.items():
        assert name != '__pb_cli_test__', "Test pattern should be deleted"
    print("   ✓ Pattern no longer exists")

    # Restore original pixels
    print(f"\n11. Restoring original pixels ({original_pixels})...")
    cli(f''' % pb pixels {original_pixels} ''')
    result = cli(''' % pb pixels ''')
    restored_pixels = int(result.output.strip().split('\n')[-1])
    assert restored_pixels == original_pixels, f"Failed to restore pixels"
    print(f"   ✓ Pixels restored to {original_pixels}")

    # Restore original brightness
    print(f"\n12. Restoring original brightness ({original_brightness})...")
    cli(f''' % pb on {original_brightness} ''')
    print(f"   ✓ Brightness restored to {original_brightness}")

    # Restore original pattern
    if original_pattern_id:
        print(f"\n13. Restoring original pattern ({original_pattern_name})...")
        cli(f'% pb pattern {original_pattern_id}')
        result = cli('% pb cfg')
        cfg = get_json_output(result)
        active_pattern_id = cfg.get('sequencer', {}).get('activeProgram', {}).get('activeProgramId')
        assert active_pattern_id == original_pattern_id, f"Failed to restore pattern"
        print(f"   ✓ Pattern restored to {original_pattern_name}")
    else:
        print("\n13. No original pattern to restore (was None)")

    print("\n✅ All tests passed!")

def run_cmd(*args):
    """Run a CLI command and return the result."""
    runner = CliRunner()
    result = runner.invoke(pixelblaze, args)
    if result.exit_code != 0:
        print(f"Command failed: pb {' '.join(args)}")
        print(f"Output: {result.output}")
        raise Exception(f"Command failed with exit code {result.exit_code}")
    return result


def cli(command_str):
    """Run a CLI command from a demo-style string (e.g., '% pb pattern foo').

    Parses commands in the format:
        % pb <command> <args>

    Strips whitespace, removes the prompt and 'pb', then runs the command.
    """
    # Strip whitespace
    command_str = command_str.strip()

    # Remove fake prompt (% pb or just pb)
    if command_str.startswith('% pb '):
        command_str = command_str[5:]  # Remove '% pb '
    elif command_str.startswith('pb '):
        command_str = command_str[3:]  # Remove 'pb '

    # Split into args using shlex to handle quoted strings properly
    args = shlex.split(command_str)

    # Run the command
    return run_cmd(*args)


def get_json_output(result):
    """Extract JSON from result output (ignoring log lines on stderr)."""
    # Output goes to stdout, logs go to stderr - get last line that's JSON
    lines = result.output.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if line.startswith('{') or line.startswith('['):
            return json.loads(line)
    raise ValueError("No JSON found in output")


if __name__ == '__main__':
    test_cli()
