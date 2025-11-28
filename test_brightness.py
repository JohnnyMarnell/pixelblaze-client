#!/usr/bin/env python3
"""
Test script for brightness control reliability.

This script tests the brightness commands using the ping/ack mechanism
instead of arbitrary delays. It measures actual latency for each operation.
"""

import time
from pixelblaze import Pixelblaze

# Configuration
IP_ADDRESS = "192.168.1.157"  # Change to your Pixelblaze IP
TEST_VALUES = [0.0, 0.5, 1.0, 0.3, 0.7, 0.0]

def test_brightness(ip):
    """Test brightness setting with ping/ack verification."""
    print(f"Connecting to Pixelblaze at {ip}...")
    pb = Pixelblaze(ip)

    print(f"Initial brightness: {pb.getBrightnessSlider():.2f}\n")

    for i, target in enumerate(TEST_VALUES, 1):
        print(f"Test {i}/{len(TEST_VALUES)}: Setting brightness to {target}...")

        # Set brightness
        start = time.time()
        pb.setBrightnessSlider(target, saveToFlash=False)

        # Wait for device readiness using ping/ack
        pb.sendPing()
        elapsed = (time.time() - start) * 1000

        # Read back
        actual = pb.getBrightnessSlider()

        # Check
        match = abs(actual - target) < 0.02
        status = "✓ OK" if match else "✗ FAIL"
        print(f"  → Set: {target:.2f}, Read: {actual:.2f}, Latency: {elapsed:.2f}ms [{status}]")

        if not match:
            print(f"  ⚠ Mismatch detected! Retrying...")
            pb.setBrightnessSlider(target, saveToFlash=False)
            pb.sendPing()
            retry = pb.getBrightnessSlider()
            retry_match = abs(retry - target) < 0.02
            retry_status = "✓ OK" if retry_match else "✗ STILL FAILS"
            print(f"  → Retry read: {retry:.2f} [{retry_status}]")

        print()
        time.sleep(0.2)  # Small pause between tests

    print("Test complete!")

if __name__ == "__main__":
    try:
        test_brightness(IP_ADDRESS)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
