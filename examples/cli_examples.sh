#!/bin/bash
# Pixelblaze CLI Examples
# Collection of common usage patterns for the pb command

# Basic Discovery and Connection
# ==============================

# Auto-discover Pixelblaze (checks 192.168.4.1 first, then network scan)
pb pixels

# Use specific IP address
pb --ip 192.168.1.100 pixels


# Power Control
# =============

# Turn off all LEDs
pb off

# Turn on at full brightness
pb on

# Turn on at 50% brightness
pb on 0.5

# Turn off and save state to flash
pb off --save

# Turn on with sequencer
pb on --play-sequencer


# Pixel Configuration
# ===================

# Get current pixel count
pb pixels

# Set pixel count to 300 (temporary)
pb pixels 300

# Set pixel count and save to flash
pb pixels 144 --save


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
pb render "export function render(index) { hsv(0.5, 1, 1) }"

# Rainbow wave
pb render "export function render(index) { hsv(index/pixelCount + time(0.1), 1, 1) }"

# Render from file
pb render examples/test_pattern.js

# Render with variables
pb render examples/test_pattern.js --var speed:0.5

# Render with JSON variables
pb render pattern.js --vars '{"speed": 0.5, "brightness": 1.0}'

# Render from stdin
echo "export function render(index) { hsv(0, 0, 1) }" | pb render


# Combined Workflows
# ==================

# Setup for performance
pb pixels 300 && pb on 0.8 && pb seq len 15 && pb seq play

# Quick pattern test
pb render test.js --var speed:0.3 && sleep 5 && pb seq random

# Power cycle
pb off && sleep 2 && pb on

# Emergency stop
pb off --pause-sequencer

# Party mode
pb on && pb seq random && pb seq play
