# Pixelblaze CLI Tool

A modern, flexible command-line interface for controlling Pixelblaze LED controllers.

## Installation

Install the package with CLI support:

```bash
pip install -e .
```

This will install the `pb` command in your PATH.

## Usage

### Global Options

The CLI supports a global `--ip` option that works across all commands:

```bash
pb --ip 192.168.1.100 pixels      # Use specific IP
pb --ip auto pixels               # Auto-discover (default)
pb pixels                         # Same as auto (default behavior)
```

### Auto-Discovery

When using `--ip auto` (the default), the CLI will:

1. First check for a Pixelblaze in ad-hoc mode at `192.168.4.1`
2. If not found, scan the network using the enumerator
3. Use the first Pixelblaze found

### Command Overview

| Command | Purpose |
|---------|---------|
| `pb ping` | Test connection latency and readiness |
| `pb brightness [LEVEL]` | Get or set brightness (0.0-1.0) |
| `pb pixels [COUNT]` | Get or set the number of pixels |
| `pb on [BRIGHTNESS]` | Turn on LEDs (default: full brightness) |
| `pb off` | Turn off LEDs (set brightness to 0) |
| `pb seq play` | Start/resume the pattern sequencer |
| `pb seq pause` | Pause the pattern sequencer |
| `pb seq next` | Advance to next pattern |
| `pb seq random` | Jump to a random pattern |
| `pb seq len SECONDS` | Set all playlist pattern durations |
| `pb render CODE` | Compile and run JavaScript pattern code |

### Commands

#### `ping` - Test Connection Latency

Test the connection to your Pixelblaze and measure latency:
```bash
pb ping              # Send 5 pings (default)
pb ping -c 10        # Send 10 pings
pb ping --count 3    # Send 3 pings
```

Output shows:
- Individual ping times
- Statistics (min/max/avg)
- Packet loss percentage
- Machine-readable average on last line

Example output:
```
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

#### `brightness` - Get or Set Brightness

Get the current brightness:
```bash
pb brightness
# Output: 0.80
```

Set the brightness (0.0 to 1.0):
```bash
pb brightness 0.5       # Set to 50%
pb brightness 0         # Turn off (same as pb off)
pb brightness 1         # Full brightness
pb brightness 0.3 --save  # Set to 30% and save to flash
```

This command uses the Pixelblaze ping/ack mechanism to ensure the device is ready before verification, eliminating the need for arbitrary delays.

#### `pixels` - Get or Set Pixel Count

Get the current pixel count:
```bash
pb pixels
# Output: 300
```

Set the pixel count (temporary, resets on reboot):
```bash
pb pixels 300
```

Set the pixel count and save to flash (persistent):
```bash
pb pixels 300 --save
```

#### `on` - Turn On the Pixelblaze

Turn on the Pixelblaze at full brightness:
```bash
pb on
```

Turn on at a specific brightness level (0.0 to 1.0):
```bash
pb on 0.5      # 50% brightness
pb on 0.8      # 80% brightness
```

Turn on and start the sequencer:
```bash
pb on --play-sequencer
```

Turn on and save the state to flash (persistent across reboots):
```bash
pb on 0.7 --save
```

#### `off` - Turn Off the Pixelblaze

Turn off all LEDs by setting brightness to zero:
```bash
pb off
```

Turn off and pause the sequencer:
```bash
pb off --pause-sequencer
```

Turn off and save the state to flash (persistent across reboots):
```bash
pb off --save
```

#### `seq` - Sequencer Control Commands

Control the Pixelblaze pattern sequencer and playlist. This is a command group with several subcommands:

##### `seq play` - Start/Resume Sequencer
```bash
pb seq play              # Start/resume the sequencer
pb seq play --save       # Start and save state to flash
```

##### `seq pause` - Pause Sequencer
```bash
pb seq pause             # Pause the sequencer
pb seq pause --save      # Pause and save state to flash
```

##### `seq next` - Next Pattern
```bash
pb seq next              # Advance to next pattern
pb seq next --save       # Next and save
```

##### `seq random` - Random Pattern
```bash
pb seq random            # Jump to a random pattern from all available
```

##### `seq len` - Set All Pattern Durations
```bash
pb seq len 10            # Set all playlist patterns to 10 seconds
pb seq len 30 --save     # Set to 30 seconds and save
pb seq len 5.5           # Set to 5.5 seconds (supports decimals)
```

#### `render` - Send JavaScript Code to Renderer

The `render` command compiles and sends JavaScript pattern code to the Pixelblaze renderer.

**Basic usage with inline code:**
```bash
pb render "export function render(index) { hsv(0.5, 1, 1) }"
```

**Using stdin (piped input):**
```bash
echo "export function render(index) { hsv(0.5, 1, 1) }" | pb render

# Or from a file
cat pattern.js | pb render
```

**Setting variables with `--var`:**
```bash
pb render code.js --var speed:0.5 --var brightness:1.0
```

**Setting variables with `--vars` (JSON):**
```bash
pb render code.js --vars '{"speed": 0.5, "brightness": 1.0}'
```

**Combining multiple options:**
```bash
pb --ip 192.168.1.100 render pattern.js --var speed:0.8 --save
```

## Examples

### Quick rainbow pattern:
```bash
pb render "export function render(index) { hsv(time(0.1), 1, 1) }"
```

### Control a specific Pixelblaze and set pixels:
```bash
pb --ip 192.168.1.50 pixels 144
pb --ip 192.168.1.50 render "export function render(index) { hsv(index/pixelCount, 1, 1) }"
```

### Chain multiple commands:
```bash
pb pixels 300 && pb render pattern.js --var brightness:0.8
```

### Turn off LEDs quickly:
```bash
pb off
```

### Turn off and stop all pattern activity:
```bash
pb off --pause-sequencer
```

### Set brightness directly (most reliable):
```bash
pb brightness 0.5    # Set to 50%
pb brightness 0      # Turn off
pb brightness 1      # Full brightness
```

### Turn on at specific brightness:
```bash
pb on 0.5    # 50% brightness
```

### Quick power cycle with different brightness:
```bash
pb off && sleep 2 && pb on 0.3
```

### Set up sequencer with custom timing:
```bash
pb seq len 15        # Set all patterns to 15 seconds
pb seq play          # Start the sequencer
```

### Quick pattern shuffle:
```bash
pb seq random        # Jump to random pattern
```

### Manual pattern navigation:
```bash
pb seq next          # Skip to next pattern
pb seq next          # And next again
pb seq pause         # Pause on this one
```

## Help

Get help on any command:
```bash
pb --help
pb ping --help
pb brightness --help
pb pixels --help
pb on --help
pb off --help
pb seq --help
pb seq play --help
pb seq len --help
pb render --help
```

## Troubleshooting

### Brightness Commands

If you experience inconsistent brightness control, use the dedicated `pb brightness` command instead of `pb on`:

```bash
# More reliable for direct brightness control
pb brightness 0.5

# Instead of
pb on 0.5
```

The `brightness` command includes:
- Ping/ack handshake to ensure device readiness (no arbitrary delays!)
- Automatic verification of the set value
- Automatic retry if verification fails
- Better error reporting

### How It Works: Ping/Ack Mechanism

Instead of using arbitrary `sleep()` delays, the CLI uses the Pixelblaze's built-in ping/ack protocol:

1. Command is sent (e.g., set brightness)
2. CLI sends a ping request
3. CLI waits for acknowledgment from Pixelblaze
4. Once ack is received, the device is ready
5. Next command can be sent or value verified

This is more reliable than fixed delays because:
- It adapts to actual network latency
- It waits for the device to actually be ready
- It doesn't wait longer than necessary
- It's the same mechanism the web UI uses

## Architecture

The CLI is built with Click, a modern Python CLI framework that provides:

- Automatic help generation
- Type validation
- Flexible option parsing
- Command grouping and nesting
- Shell completion support

The CLI follows best practices from tools like `gcloud`, `aws`, and `modal`:
- Clear command structure
- Consistent global options
- Intuitive defaults
- Helpful error messages
