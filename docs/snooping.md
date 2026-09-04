# Snooping the wire

`pb snoop` decodes live Pixelblaze websocket traffic to JSON. It shells out to
`tshark` for capture and dissection and pipes the result through `jq`, so
everything below composes with the rest of your shell.

```console
$ pb snoop
{"dir":"→","msg":{"getConfig":true}}
{"dir":"←","msg":{"fps":42.5,"vmerr":0,"mem":10000}}
{"dir":"←","msg":{"activeProgram":{"name":"sparks"}}}
{"dir":"→","msg":{"setVars":{"speed":0.5}}}
```

`→` is a frame going **to** a Pixelblaze (a request), `←` one coming **from**
it (a response).

## Installing the dependencies

Both `tshark` and `jq` must be on `PATH`. `pb snoop` checks for them up front
and prints the right command for your platform rather than failing with
tshark's own error.

=== "macOS"

    ```bash
    brew install wireshark          # CLI only — tshark, no GUI
    brew install --cask wireshark   # GUI app, also ships tshark
    brew install jq
    ```

=== "Linux"

    ```bash
    sudo apt install tshark jq              # Debian/Ubuntu
    sudo dnf install wireshark-cli jq       # Fedora/RHEL
    ```

=== "Windows"

    ```powershell
    choco install wireshark jq              # Chocolatey
    winget install WiresharkFoundation.Wireshark; winget install jqlang.jq
    scoop install wireshark jq
    ```

    The Wireshark installer does not always add `tshark` to `PATH`; it lives at
    `C:\Program Files\Wireshark\tshark.exe`.

### Capture permissions

Capturing raw packets is privileged on every platform.

| Platform | Mechanism | Fix |
|---|---|---|
| macOS | `/dev/bpf*` is root-only | `brew install --cask wireshark-chmodbpf`, then log out and back in — or `pb snoop --sudo` |
| Linux | `dumpcap` capabilities | `sudo usermod -aG wireshark $USER`, or `sudo setcap cap_net_raw,cap_net_admin+eip $(which dumpcap)` — or `pb snoop --sudo` |
| Windows | Npcap driver | Install Npcap (bundled with Wireshark). If it was installed with *Restrict to Administrators*, run from an elevated terminal — `--sudo` does nothing on Windows |

`pb snoop` checks macOS BPF readability before starting and tells you which of
these applies. `--read` needs no permissions at all.

## What you can and cannot see

!!! warning "You only see traffic that crosses this machine"

    A switch or access point forwards a unicast frame only to the port owning
    the destination MAC. `--others` and `--any` widen the **filter**; they do
    not widen what the **interface** can physically see.

| Traffic | Visible |
|---|---|
| this machine ↔ a Pixelblaze | :material-check: yes |
| this machine ↔ an emulator on localhost | :material-check: yes (auto-selects `lo0`) |
| anyone's broadcast/multicast (UDP:1889 beacons) | :material-check: yes |
| Pixelblaze ↔ Pixelblaze unicast (sync-group leader/follower) | :material-close: **no** |

To see traffic between two other devices you need WiFi monitor mode with WPA2
decryption, a mirrored switch port, or to make this machine the access point.

## Timing matters for decoding

tshark identifies a websocket stream by watching for the HTTP upgrade
handshake, so **start `pb snoop` before the traffic you want to see**.

To attach to a connection that is already open — a browser tab you left
running — pass `--midstream`, which decodes the port as websocket directly.
Do *not* use it when the handshake is in the capture; tshark will try to parse
the handshake as frames and produce garbage.

```bash
pb snoop                 # start first, then run commands / click the UI
pb snoop --midstream     # attach to a connection already established
```

## Choosing what to watch

Targeting reuses the same flexible resolution as the global `--ip`, so
addresses, pasted URLs, bare host octets and cached name fragments all work.

```bash
pb snoop                                # the resolved device
pb --ip kitchen snoop                   # cached name fragment
pb snoop --others 231,bike2             # several devices in one capture
pb snoop --others http://192.168.1.5/   # pasted from a browser
pb snoop --any                          # every websocket on the wire, no resolution
```

`--host` restricts the *other* end of the conversation. It defaults to `any`,
which shows every client talking to the device — including a phone app, when
the network lets you see it.

```bash
pb snoop --host me                      # only this machine's traffic
pb snoop --host 192.168.1.55            # only that client's traffic
```

Direction, both shown by default:

```bash
pb snoop --requests                     # only what we send
pb snoop --responses                    # only what the Pixelblaze says
```

Under `--any`, direction is decided by the **listening port** rather than by
your own address — a Pixelblaze is always the websocket server — so
`pb snoop --any --requests` means every client's requests, not just yours.

## Filtering and shaping the stream

```bash
pb snoop -v '"fps"'                        # drop the once-a-second status spam
pb snoop -g setVars                        # only variable writes
pb snoop --jq 'select(.msg.activeProgram)' # arbitrary jq over the decoded stream
pb snoop --jq 'select(.msg.fps) | .msg.fps'
```

`-g`/`--grep` and `-v`/`--exclude` are regexes matched against the raw payload;
`--jq` is appended to the pipeline and sees the finished record.

The envelope adapts — fields that would be identical on every line are dropped,
so a single device with `--responses` degrades to bare protocol JSON. Force it
either way with `--bare` or `--full`:

```bash
pb snoop --bare > session.jsonl   # just the messages, no envelope
pb snoop --full                   # always ts, dir, peer, src, dst
pb snoop --time                   # add a local clock timestamp
```

## Saving and replaying

```bash
pb snoop -w capture.pcapng        # stream decoded output AND save raw packets
pb snoop --read capture.pcapng    # replay later; no capture permissions needed
```

A saved capture keeps the whole TCP stream including the handshake, so it
replays without `--midstream`.

!!! note

    `tshark` refuses a display filter while saving a live capture, so with
    `-w` the narrowing is done by the BPF capture filter and the rest moves
    into `jq`. The output is identical; the saved file is simply less
    aggressively filtered, which is what makes it replayable.

## Other options

```bash
pb snoop -i en0                   # pick the interface (default: routed to target)
pb snoop -p 81,80                 # extra ports to decode as websocket
pb snoop -c 200                   # stop after 200 packets (not frames)
pb snoop -d 30                    # stop after 30 seconds
pb snoop --color never            # or --no-color; honors NO_COLOR
pb snoop --sudo                   # run tshark under sudo
```

The interface is auto-detected from the kernel's route to the target, so AP
mode, a second adapter and loopback all work without `-i`.

## Escape hatch: `--dry-run`

Prints the exact pipeline and exits. Copy it, tweak it, run it yourself — this
is the way in to anything `pb snoop` does not expose directly.

```console
$ pb snoop --dry-run
tshark -i en0 -l -n -q -d tcp.port==81,http \
  -f 'tcp port 81 and host 192.168.1.230' \
  -Y 'websocket and (ip.src == 192.168.1.230 or ip.dst == 192.168.1.230)' \
  -T ek -e ip.src -e ip.dst -e tcp.srcport -e tcp.dstport \
        -e frame.time_epoch -e websocket.payload.text \
  | jq -c --unbuffered -C --argjson devs '["192.168.1.230"]' ... '<program>'
```

Two details in there are worth knowing if you write your own:

- **`-T ek`, not `-T fields`.** A single TCP packet can carry several websocket
  frames. `-T fields` comma-joins them into `{"fps":41},{"activeProgram":…}`,
  which is not valid JSON. `-T ek` keeps them as an array so each frame becomes
  its own line.
- **Direction belongs in `-Y`, never in `-f`.** Filtering one direction at
  capture time also drops the server's `101 Switching Protocols` reply, which is
  what primes the websocket dissector — you would get silence.

## Not yet covered

Binary websocket frames (preview pixels, pattern uploads), plain HTTP on port
80, and the UDP:1889 discovery beacons are not decoded today. See the roadmap
notes on the `cli-snoop` branch.
