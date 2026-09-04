<!-- ==========================================================================
     DO NOT COMMIT / DO NOT MERGE — delete this whole block before this
     branch (cli-snoop) is merged anywhere. Scratch notes only.
     ========================================================================== -->

> # 🚧 DO NOT COMMIT — `pb snoop` needs revisiting
>
> **Delete this whole section before merging.** Notes-to-self on the `cli-snoop` branch.
>
> ### 1. The big one: we can only snoop OUR OWN traffic
>
> I built `--others` and `--any` assuming we could watch traffic between *any*
> two Pixelblazes on the LAN — e.g. a sync-group **leader talking to its
> followers**. That is basically **not possible** as written, and the options
> currently oversell it. A switch/AP only forwards a unicast frame to the port
> that owns the destination MAC, so this laptop never sees it.
>
> What actually works today:
>
> | Traffic | Snoopable? | Why |
> |---|---|---|
> | laptop ↔ Pixelblaze on the LAN | ✅ yes | we are an endpoint |
> | laptop ↔ emulator on localhost | ✅ yes | verified: auto-iface already resolves `127.0.0.1` → `lo0`. Still needs BPF perms, and the emulator may not be on port 81 (`-p`) |
> | anyone's **broadcast/multicast** | ✅ yes | flooded to every port — this is why UDP:1889 beacons work (see #2) |
> | Pixelblaze ↔ Pixelblaze unicast (leader/follower sync, `getPeers` peer-to-peer) | ❌ **no** | switched unicast, never reaches us |
>
> Escape hatches worth investigating before we either fix or de-scope this:
> - **WiFi monitor mode** — `tshark -I -i en0` + WPA2 decryption
>   (`-o wlan.enable_decryption:TRUE` and a `uat:80211_keys` `wpa-pwd` entry).
>   Needs the PSK *and* a captured 4-way handshake for each device, so devices
>   must be made to re-associate. Also kicks en0 off the network on macOS.
>   Verified the tshark flags exist; **not verified end to end.**
> - **Port mirroring / SPAN** on a managed switch.
> - **Make the laptop the AP** (macOS Internet Sharing, or join the Pixelblaze's
>   own SoftAP) so the traffic genuinely transits us.
>
> **TODO:** at minimum, warn in `--others`/`--any` help that widening the
> *filter* does not widen what the *interface can see*. Possibly add
> `--monitor`. Decide whether leader/follower snooping is in scope at all.
>
> ### 2. UDP beacons — look into snooping these!
>
> These are **broadcast**, so unlike the above they are visible from anywhere on
> the LAN, including beacons from Pixelblazes we have never talked to. Great fit.
>
> - `PixelblazeEnumerator`, `pixelblaze/pixelblaze.py` — `PORT = 1889`,
>   `BEACON_PACKET = 42`, `TIMESYNC_PACKET = 43`.
> - Payload is 12 bytes, `struct.unpack("<LLL")` → `(packetType, senderId/chipId, senderTimeMs)`.
> - Filter would be `udp port 1889`, nothing like the current websocket path.
> - **Open design question:** tshark has no dissector for this, so we would get
>   raw hex via `-e data.data`, and jq cannot byte-swap little-endian hex
>   without something grim. Options: (a) a jq hex-decode helper, (b) let
>   Python decode this one case and break the "everything goes through jq"
>   rule, (c) write a tiny Wireshark Lua dissector and ship it with `-X lua_script:`.
>   (c) is the most fun and gives real named fields.
> - Would surface: devices appearing/disappearing, chipIds, and clock skew /
>   jitter between leader and followers via the timesync packets (43) —
>   genuinely useful for debugging out-of-sync patterns.
>
> ### 3. Other power-user snooping to try
>
> - **Binary websocket frames** (opcode 2) — currently invisible; we only pull
>   `websocket.payload.text`. That hides preview pixel frames, pattern binary
>   uploads and `.epe` transfers. Add `--binary` emitting `{op, len, hex}`.
> - **HTTP on port 80** — pattern/file uploads, and the firmware `.stfu` POST to
>   `/update`. `-p 80` widens the capture today but the jq program still expects
>   websocket fields, so it decodes nothing useful. Needs its own mode.
> - **Emulator** — interface selection already works (`127.0.0.1` → `lo0`), but
>   it is untested against a real emulator, and the port is probably not 81.
>   Worth a `--local` shortcut and an examples entry once confirmed.
> - **Timing** — `--delta` (ms since previous frame) and request→response
>   round-trip pairing. jq `foreach` can carry the state.
> - **`--stats`** — frames/sec, bytes/sec, histogram of message keys, instead of
>   a raw stream. Either `tshark -z` or a jq `reduce`.
> - **ARP / DHCP / mDNS** — catch a Pixelblaze joining the network or changing
>   IP, which is the usual reason a cached address goes stale.
>
> ### 4. Housekeeping
>
> - Live capture against real hardware is **untested** — no Pixelblaze was
>   reachable and sudo needed a password. The test suite substitutes a FIFO as
>   the capture interface, which covers `-i`, `-f`, `-w` and the process
>   plumbing but not an actual BPF device.

# pixelblaze-client
A Python library that presents a simple, synchronous interface for communicating with and
controlling one or more Pixelblaze LED controllers. 

## Requirements
- Python 3.9 or newer
- websocket-client (installable via `pip install websocket-client`, or from https://github.com/websocket-client/websocket-client)
- requests (installable via `pip install requests`, or from https://github.com/psf/requests)
- pytz (installable via `pip install pytz`, or from https://github.com/stub42/pytz)
- mini-racer (installable via `pip install mini-racer`

## Installation
Install pixelblaze-client with all required packages using pip:

```pip install pixelblaze-client```

Or, if you prefer, drop a copy of [pixelblaze.py](pixelblaze/pixelblaze.py) into your project directory and reference it within your project:

```from pixelblaze import *```

## <a name="documentation"></a>Documentation

API and other documention is available in [Markdown](docs/index.md) and [HTML](https://zranger1.github.io/pixelblaze-client/).

Sample code illustrating usage is provided in the [examples directory](examples/).

**Please note that version 1.0.0 was a major refactoring and enhancement of the library, with many new features and significant changes.** The API surface is completely new. See the the [API documentation](#documentation) for details.  

## Current Version [**v1.1.8**] - 2026-07-22

#### Fixed

* gh issue #28: Fixed a bug in the lightweight enumerator that caused it to fail to enumerate devices when called multiple times.  The enumerator now resets its internal list of seen devices each time it is called.

#### Added
* No new functionality in this release.

### Older Versions

See [CHANGELOG.md](CHANGELOG.md) for complete version history.

## Known Issues
- Check our github repo; if you find something, [let us know](/../../issues/new/choose)!
