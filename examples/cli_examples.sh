#!/bin/bash
# Pixelblaze CLI Examples
# Collection of common usage patterns for the pb command

# >>>>>>>>>>>
# >>>>>>>>>>> See Also:
# >>>>>>>>>>> test_cli.py examples, and --help strings, and, of course, cli.py
# >>>>>>>>>>>

# Basic Discovery and Connection
# ==============================

# Auto-discover Pixelblaze (checks 192.168.4.1 first, then network scan)
pb pixels

# Use specific IP address
pb --ip 192.168.1.100 pixels


# Basic Controls
# =============

# Turn off all LEDs
pb off

# Turn on at full brightness
pb on

# Turn on / set 50% brightness, do not save to flash
pb on 0.5 --no-save

# Turn off and save state to flash
pb off

# Turn on with sequencer
pb on --play-sequencer

# Print most configs
pb cfg
pb cfg | yq -P


# Pixel Configuration
# ===================

# Get current pixel count
pb pixels

# Set pixel count to 300 (temporary)
pb pixels 300

# Set pixel count and save to flash
pb pixels 144 --save

# Show current pixel mapper coordinates / function
pb map
pb map --csv


# Sequencer Control
# =================

# Start the sequencer
pb seq play

# Pause the sequencer
pb seq pause

# Go to next pattern
pb seq next

# Jump to random pattern
pb seq random

# Set all patterns to 10 seconds
pb seq len 10

# Set all patterns to 30 seconds and save
pb seq len 30 --save


# Pattern Rendering
# =================

# Simple solid color
pb pattern "hsv(0.5, 1, 1)"

# Rainbow wave
pb pattern "hsv(index / pixelCount + time(0.1), 1, 1)"

# Render from file
pb pattern examples/test_pattern.js

# Render with variables
pb pattern examples/test_pattern.js --var speed:0.5

# Render with JSON variables
pb pattern src.js --vars '{speed: 0.5, brightness: 1.0}'

# Render from stdin
echo "rgb(0, 0, 1)" | pb pattern

