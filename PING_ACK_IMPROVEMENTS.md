# Ping/Ack Mechanism Improvements

## Problem

The original implementation of brightness control used arbitrary `time.sleep()` delays:
```python
pb.setBrightnessSlider(0.5)
time.sleep(0.35)  # Hope this is enough time...
actual = pb.getBrightnessSlider()
```

This caused issues:
- Unreliable on slower networks
- Wasted time on fast networks
- No guarantee the device was actually ready
- Intermittent failures

## Solution

Use the Pixelblaze's built-in ping/ack protocol to wait for readiness:
```python
pb.setBrightnessSlider(0.5)
pb.sendPing()  # Waits for ack, returns when device is ready
actual = pb.getBrightnessSlider()
```

## How It Works

The `sendPing()` method (line 563 in pixelblaze.py):
```python
def sendPing(self) -> Union[str, None]:
    """Send a Ping message to the Pixelblaze and wait for the Acknowledgement response."""
    return self.wsSendJson({"ping": True}, expectedResponse="ack")
```

When `expectedResponse="ack"` is set:
1. Command is sent to Pixelblaze
2. `wsSendJson()` waits for a response with key "ack"
3. Returns only when acknowledgment is received
4. Adapts to actual network latency

## Commands Updated

All brightness-related commands now use ping/ack:

### `pb brightness LEVEL`
```python
pb.setBrightnessSlider(level, saveToFlash=save)
pb.sendPing()  # Wait for ready
actual = pb.getBrightnessSlider()  # Now safe to read
```

### `pb on [BRIGHTNESS]`
```python
pb.setBrightnessSlider(brightness, saveToFlash=save)
pb.sendPing()  # Wait for ready
```

### `pb off`
```python
pb.setBrightnessSlider(0.0, saveToFlash=save)
pb.sendPing()  # Wait for ready
```

## New Command: `pb ping`

Test connection latency and verify the ping/ack mechanism:

```bash
$ pb ping -c 5
Pinging Pixelblaze at 192.168.1.157...

Ping 1: 12.34ms
Ping 2: 11.89ms
Ping 3: 13.05ms
Ping 4: 12.56ms
Ping 5: 12.12ms

--- Ping statistics ---
Packets: Sent = 5, Received = 5, Lost = 0 (0% loss)
Round-trip times: min = 11.89ms, max = 13.05ms, avg = 12.39ms
12.39
```

## Benefits

1. **More Reliable**: Waits for actual device readiness, not arbitrary time
2. **Faster**: No unnecessary waiting on fast networks
3. **Adaptive**: Automatically adjusts to network conditions
4. **Debuggable**: `pb ping` shows actual latency
5. **Same as Web UI**: Uses the same mechanism the official web interface uses

## Testing

Run the test script to verify brightness control reliability:
```bash
python3 test_brightness.py
```

The script now measures actual latency for each operation using ping/ack.

## Other Commands That Use expectedResponse

Looking at the codebase, other commands already use proper response handling:

- `setActiveControls()`: uses `expectedResponse="ack"`
- `sendPatternToRenderer()`: uses `expectedResponse="ack"`
- `savePattern()`: uses `expectedResponse="ack"`
- `pauseRenderer()`: uses `expectedResponse="ack"`

But `setBrightnessSlider()` did not! We've now fixed this at the CLI level by adding the ping after the call.

## Future Improvements

Consider modifying `setBrightnessSlider()` in the library itself to accept an optional `waitForAck` parameter:
```python
def setBrightnessSlider(self, brightness: float, *, saveToFlash: bool = False, waitForAck: bool = False):
    self.wsSendJson({"brightness": brightness, "save": saveToFlash},
                    expectedResponse="ack" if waitForAck else None)
```

This would eliminate the need for the CLI to call `sendPing()` separately.
