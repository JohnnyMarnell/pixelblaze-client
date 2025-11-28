# Pixelblaze Recovery Guide

## Symptoms of Frozen Pixelblaze

- LEDs stuck on a static color
- Web interface not loading or hanging
- CLI commands hanging indefinitely
- Device not responding to any commands

## Quick Recovery Steps

### 1. Power Cycle
**This is the fastest and most reliable fix:**

1. **Unplug the Pixelblaze** (or cut power to it)
2. **Wait 5-10 seconds**
3. **Plug it back in**
4. **Wait 15-20 seconds** for it to fully boot
5. **Check the web interface** at `http://<ip-address>`
6. **Test with a simple CLI command:**
   ```bash
   pb --ip <ip> ping
   ```

### 2. If Still Frozen After Power Cycle

Sometimes the Pixelblaze can get into a persistent bad state:

1. **Hold the button** on the Pixelblaze for 5+ seconds while powered
2. This may reset it to factory defaults (you'll lose patterns!)
3. Or try unplugging, waiting 30 seconds, then power back on

## Prevention: Safe CLI Usage

### Use the New Safety Options

The CLI now has built-in timeout and recovery options:

```bash
# Set a custom timeout (default is 5 seconds)
pb --timeout 10 brightness 0.5

# Skip verification to avoid hangs (faster, less safe)
pb --no-verify brightness 0.5

# Combine both
pb --timeout 3 --no-verify on
```

### Global Options

These work with ALL commands:

- `--timeout SECONDS` - How long to wait before giving up (default: 5.0)
- `--no-verify` - Skip ping/ack verification (use if device is flaky)

### Examples

```bash
# Safe, with longer timeout for slow networks
pb --ip 192.168.1.157 --timeout 10 brightness 0.5

# Fast mode for rapid commands (less reliable)
pb --no-verify brightness 1
pb --no-verify brightness 0
pb --no-verify brightness 1

# Test if device is responsive
pb ping
```

## What Went Wrong

The ping/ack mechanism is designed to ensure the device is ready, but if the device is in a bad state:

1. **Ping never gets acknowledged** → CLI waits for timeout
2. **Websocket connection breaks** → Commands queue up
3. **Device buffer fills** → Device freezes

The new `--timeout` and `--no-verify` flags give you control over this behavior.

## Testing After Recovery

Once your Pixelblaze is back:

```bash
# Test basic connectivity
pb --ip 192.168.1.157 ping

# Test brightness with safety features
pb --ip 192.168.1.157 --timeout 3 brightness 0.5

# If still having issues, use no-verify mode
pb --ip 192.168.1.157 --no-verify brightness 0.5
```

## Best Practices

1. **Always test with ping first:**
   ```bash
   pb ping
   ```

2. **Use reasonable timeouts:**
   - Local network: `--timeout 3`
   - Slow network: `--timeout 10`
   - Default is 5, which works for most

3. **If device is flaky, use --no-verify:**
   ```bash
   pb --no-verify brightness 0.5
   ```

4. **Don't spam commands rapidly:**
   - Give device time to process
   - The ping/ack mechanism helps with this
   - But rapid fire commands can still overwhelm

5. **Monitor for warnings:**
   - "Warning: Ping failed" means device isn't responding properly
   - "Warning: Could not verify device readiness" means timeout occurred
   - If you see these, power cycle the device

## Emergency Commands

If you need to control the Pixelblaze but it's being flaky:

```bash
# Fast off (no verification)
pb --no-verify off

# Fast on (no verification)
pb --no-verify on

# Check if alive
pb ping -c 1
```

## When to Power Cycle

Power cycle if:
- Commands hang for more than 10 seconds
- You see multiple "Ping failed" warnings
- Web interface is unresponsive
- LEDs are frozen

Don't power cycle if:
- You just see one warning
- Commands are just slow
- Device is mid-pattern render
