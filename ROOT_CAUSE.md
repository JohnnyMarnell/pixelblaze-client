# Root Cause Analysis: Pixelblaze Soft-Bricking

## The Problem

Running brightness commands causes the Pixelblaze to freeze in a "soft-bricked" state:
- LEDs stuck on static color
- Web interface hangs
- Device requires power cycle

## Root Cause

**We were flooding the device with websocket messages.**

### What We Were Doing Wrong

When running `pb brightness 0.5`, the CLI was sending **3 rapid-fire websocket commands:**

```python
pb.setBrightnessSlider(0.5)      # 1. Set brightness (fire-and-forget)
pb.sendPing()                     # 2. Send ping (wait for ack)
actual = pb.getBrightnessSlider() # 3. Fetch entire config (wait for response)
```

All three happening in **milliseconds**, faster than the Pixelblaze can process.

### The Design Flaw

Looking at the library source (line 969):

```python
def setBrightnessSlider(self, brightness: float, *, saveToFlash: bool = False):
    self.wsSendJson({"brightness": brightness, "save": saveToFlash},
                    expectedResponse=None)  # ← FIRE AND FORGET!
```

**`expectedResponse=None` means the command returns immediately without waiting.**

Compare to other commands that DO wait for acknowledgment:

```python
# Line 1242 - setActiveControls WAITS for ack
self.wsSendJson({"setControls": dictControls, "save": saveToFlash},
                expectedResponse="ack")

# Line 2396 - pauseRenderer WAITS for ack
self.wsSendJson({"pause": doPause},
                expectedResponse="ack")
```

**Brightness commands are designed to be fire-and-forget, but we were treating them like they wait!**

## Why Ping/Ack Made It Worse

The ping/ack mechanism is great for commands that ALREADY wait for acknowledgment. But for fire-and-forget commands like brightness:

1. Brightness command sends and returns instantly
2. Immediately send ping (creates second websocket message)
3. Immediately fetch config (creates third websocket message)
4. Pixelblaze buffer overflows
5. Device crashes into bad state

The aggressive verification was killing the device.

## The Fix

### Before (Broken):
```python
pb.setBrightnessSlider(0.5)      # Send
pb.sendPing()                     # Verify
actual = pb.getBrightnessSlider() # Read back
# Result: 3 messages in <10ms → CRASH
```

### After (Fixed):
```python
pb.setBrightnessSlider(0.5)      # Send
time.sleep(0.15)                  # Give device time to process
# Done!
# Result: 1 message, 150ms delay → STABLE
```

## Key Changes

1. **Removed ping/ack after brightness commands** - not needed, causes harm
2. **Removed verification reads** - not needed, causes harm
3. **Added short, smart delays** - 150ms is enough for device to process
4. **Made --no-verify skip delays** - for when you need speed and trust the device

## Smart Delay Strategy

```python
def safe_wait(ctx: click.Context, delay_ms: int = 150):
    """Wait for device to process, unless --no-verify is set."""
    if ctx.obj.get('no_verify', False):
        return  # Skip wait for maximum speed
    time.sleep(delay_ms / 1000.0)
```

- **Default 150ms**: Enough time for most operations
- **Skippable with --no-verify**: For rapid commands or trusted setups
- **No websocket overhead**: Just a simple delay, no extra messages

## Usage Now

```bash
# Normal usage - safe, reliable
pb brightness 0.5

# Fast mode - skip delays (use at your own risk)
pb --no-verify brightness 0.5

# Custom timeout for slow networks (timeout is for websocket operations, not delays)
pb --timeout 10 brightness 0.5
```

## Lessons Learned

1. **Don't verify fire-and-forget commands** - they're designed to be sent and forgotten
2. **Respect the device's processing time** - some delays are necessary
3. **Don't spam websocket messages** - quality over quantity
4. **Read the library source** - understand what `expectedResponse=None` means
5. **Trust the library design** - if it doesn't wait for ack, there's a reason

## Why This Matters

The Pixelblaze is a microcontroller with limited resources:
- Small websocket buffer
- Single-threaded processing
- Real-time LED rendering happening simultaneously

Flooding it with rapid commands causes:
- Buffer overflow
- Message queue backup
- Firmware lockup
- Soft-brick state

The fix: **Send fewer messages, give it time to breathe.**
