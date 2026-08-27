"""`pb top` — realtime dashboard of Pixelblaze activity, `top(1)`-style.

Discovers every Pixelblaze on the LAN, then keeps a long-lived websocket
open to each and re-renders the table on a fixed interval. Devices that
drop off the network stay on the table as `down`; if they come back
(same IP, or a beacon rediscovery finds them at a new one), the row
turns green again and stats resume.
"""

from __future__ import annotations

import json
import shutil
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import click

from pixelblaze.pixelblaze import Pixelblaze
from pixelblaze.cli.cli_utils import (
    _fetch_device_config,
    enumerate_pixelblazes,
    log,
    update_device_cache,
)


# ── ANSI helpers ────────────────────────────────────────────────────────────

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
INVERT = "\x1b[7m"
CLEAR_HOME = "\x1b[2J\x1b[H"
CLEAR_LINE_END = "\x1b[K"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
COLORS = {
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "red": "\x1b[31m",
    "cyan": "\x1b[36m",
    "grey": "\x1b[90m",
}


def _c(s: str, color: Optional[str], enabled: bool) -> str:
    if not enabled or not color:
        return s
    return f"{COLORS.get(color, '')}{s}{RESET}"


# ── State ───────────────────────────────────────────────────────────────────

# Health thresholds, in seconds since last successful poll.
HEALTHY_MAX_AGE = 3.0
STALE_MAX_AGE = 15.0

# How often each worker re-fetches config/sequencer (stats stream freely on
# their own; only pattern/name/etc need refreshing).
CONFIG_REFRESH_SECONDS = 10.0

# How often the reconnect loop retries a dead device.
RECONNECT_BACKOFF_SECONDS = 5.0


@dataclass
class Row:
    """Everything one device row on the top table needs to render itself."""
    ip: str
    name: str = ""
    ver: str = ""
    pixel_count: Optional[int] = None
    brightness: Optional[float] = None
    active_pattern_id: str = ""
    active_pattern_name: str = ""
    fps: Optional[float] = None
    mem: Optional[int] = None
    storage_used: Optional[int] = None
    storage_size: Optional[int] = None
    uptime: Optional[int] = None
    last_seen: float = 0.0
    connected: bool = False
    error: str = ""


class TopMonitor:
    """Owns the shared rows dict, one worker thread per device, and the
    rediscovery thread. Main loop just reads snapshots and renders."""

    def __init__(self, initial_devices: list[dict], rediscover_seconds: float):
        self._lock = threading.Lock()
        self._rows: dict[str, Row] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._rediscover_seconds = rediscover_seconds

        for dev in initial_devices:
            ip = dev.get("ip")
            if ip:
                self._ensure_worker(ip)

    def stop(self):
        self._stop.set()

    def snapshot(self) -> list[Row]:
        with self._lock:
            return list(self._rows.values())

    def _ensure_worker(self, ip: str):
        with self._lock:
            if ip in self._rows:
                return
            self._rows[ip] = Row(ip=ip)
        t = threading.Thread(target=self._worker, args=(ip,), daemon=True, name=f"pb-top-{ip}")
        self._workers[ip] = t
        t.start()

    def _set(self, ip: str, **kwargs):
        with self._lock:
            row = self._rows.get(ip)
            if not row:
                return
            for k, v in kwargs.items():
                setattr(row, k, v)

    def _worker(self, ip: str):
        """Per-device: keep the socket open, drain stats frames, refresh
        config every so often. On any error, close, back off, retry.

        We don't use pb.getStatistics() because it loops forever when the
        device stops responding — its inner wsReceive returns None on
        timeout, then getStatistics just tries again. Instead we drive
        wsReceive ourselves so we can bail on staleness."""
        while not self._stop.is_set():
            pb: Optional[Pixelblaze] = None
            try:
                pb = Pixelblaze(ip)
                self._set(ip, connected=True, error="")
                # Pixelblaze streams `{"fps":...}` stats frames on its own
                # every ~1s over an open socket — no explicit sendUpdates
                # request needed (and calling setSendPreviewFrames(True)
                # here would also start binary preview frames, which we'd
                # only throw away and which cost real bandwidth per device).
                self._refresh_config(pb, ip)
                last_config_refresh = time.monotonic()
                last_stats_at = time.monotonic()

                while not self._stop.is_set():
                    # Drains any binary preview frames and stashes stats/
                    # sequencer text frames into pb.latestStats /
                    # pb.latestSequencer. Returns after default_recv_timeout.
                    pb.wsReceive(binaryMessageType=None)
                    now = time.monotonic()
                    if pb.latestStats:
                        try:
                            stats = json.loads(pb.latestStats)
                        except Exception:
                            stats = {}
                        pb.latestStats = None
                        self._set(
                            ip,
                            fps=stats.get("fps"),
                            mem=stats.get("mem"),
                            storage_used=stats.get("storageUsed"),
                            storage_size=stats.get("storageSize"),
                            uptime=stats.get("uptime"),
                            last_seen=now,
                            connected=True,
                            error="",
                        )
                        last_stats_at = now

                    if now - last_stats_at > STALE_MAX_AGE:
                        # Peer went quiet: kick the loop so we reconnect.
                        raise ConnectionError("no stats for >{:.0f}s".format(STALE_MAX_AGE))

                    if now - last_config_refresh > CONFIG_REFRESH_SECONDS:
                        self._refresh_config(pb, ip)
                        last_config_refresh = time.monotonic()

            except Exception as e:
                self._set(ip, connected=False, error=str(e).splitlines()[0][:60])
            finally:
                if pb is not None:
                    try:
                        pb._close()
                    except Exception:
                        pass

            # Backoff before reconnect.
            if self._stop.wait(RECONNECT_BACKOFF_SECONDS):
                return

    def _refresh_config(self, pb: Pixelblaze, ip: str):
        try:
            info = _fetch_device_config(pb, ip=ip, include_patterns=True)
        except Exception:
            return
        active_id = info.get("activePatternId", "") or ""
        patterns = info.get("patterns") or {}
        active_name = patterns.get(active_id, "") or info.get("activePatternName", "") or ""
        self._set(
            ip,
            name=info.get("name") or "",
            ver=str(info.get("ver") or ""),
            pixel_count=info.get("pixelCount"),
            brightness=info.get("brightness"),
            active_pattern_id=active_id,
            active_pattern_name=active_name,
        )
        # Also push the fresh info into the shared JSON cache so `pb find`
        # and friends immediately benefit.
        try:
            update_device_cache([info])
        except Exception:
            pass

    def rediscover_loop(self, timeout_ms: int):
        """Background: periodically re-enumerate to pick up devices that
        rejoined the network at a new IP or came online after startup."""
        while not self._stop.wait(self._rediscover_seconds):
            try:
                # Suppress the enumerate_pixelblazes log spam by swapping stderr
                # briefly — the main loop owns the terminal now.
                new_devices = _quiet_enumerate(timeout_ms)
            except Exception:
                continue
            for dev in new_devices:
                ip = dev.get("ip")
                if ip:
                    self._ensure_worker(ip)


def _quiet_enumerate(timeout_ms: int) -> list[dict]:
    """Run enumerate_pixelblazes with stderr silenced (top owns the screen)."""
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        return enumerate_pixelblazes(timeout=timeout_ms, slow=False)


# ── Rendering ───────────────────────────────────────────────────────────────


def _fmt_bytes(n: Optional[int]) -> str:
    if n is None:
        return "-"
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f}K"
    return f"{n / (1024 * 1024):.1f}M"


def _fmt_storage(row: Row) -> str:
    if row.storage_used is None or not row.storage_size:
        return "-"
    used = _fmt_bytes(row.storage_used)
    total = _fmt_bytes(row.storage_size)
    return f"{used}/{total}"


def _fmt_uptime(secs: Optional[int]) -> str:
    if secs is None:
        return "-"
    m, _ = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def _fmt_seen(row: Row, now: float) -> str:
    if not row.last_seen:
        return "never"
    age = now - row.last_seen
    if age < 1:
        return f"{age * 1000:.0f}ms"
    if age < 60:
        return f"{age:.1f}s"
    m, s = divmod(int(age), 60)
    return f"{m}m{s:02d}s"


def _health(row: Row, now: float) -> tuple[str, str, str]:
    """Returns (status glyph, health label, color name)."""
    age = now - row.last_seen if row.last_seen else 1e9
    if not row.connected and age > STALE_MAX_AGE:
        return "○", "down", "red"
    if age <= HEALTHY_MAX_AGE:
        return "●", "ok", "green"
    if age <= STALE_MAX_AGE:
        return "●", "stale", "yellow"
    return "○", "down", "red"


COLUMNS = [
    # (header, width, right_align) — ordered most-important-first, since
    # terminals narrower than the total drop trailing columns.
    ("STATUS", 7, False),
    ("NAME", 15, False),
    ("IP", 15, False),
    ("FPS", 5, True),
    ("PATTERN", 20, False),
    ("PIXELS", 6, True),
    ("BRIGHT", 6, True),
    ("STORAGE", 12, False),
    ("MEM", 5, True),
    ("UPTIME", 7, True),
    ("VER", 4, True),
    ("SEEN", 6, True),
]


def _pad(s: str, width: int, right: bool) -> str:
    if len(s) > width:
        s = s[: max(0, width - 1)] + "…"
    return s.rjust(width) if right else s.ljust(width)


def _render(rows: list[Row], color: bool, sort_key: str) -> str:
    now = time.monotonic()

    def sort_val(r: Row):
        if sort_key == "fps":
            return -(r.fps or 0)  # descending
        if sort_key == "ip":
            return tuple(int(p) if p.isdigit() else 0 for p in r.ip.split("."))
        if sort_key == "pattern":
            return (r.active_pattern_name or "~").lower()
        return (r.name or "~").lower()  # default: name

    rows = sorted(rows, key=sort_val)

    term_width = shutil.get_terminal_size((120, 40)).columns
    lines = []

    # Header line.
    total = len(rows)
    up = sum(1 for r in rows if _health(r, now)[1] == "ok")
    stale = sum(1 for r in rows if _health(r, now)[1] == "stale")
    down = total - up - stale
    ts = time.strftime("%H:%M:%S")
    banner = (
        f" pb top  {ts}   devices: {total}  "
        f"{_c(f'up {up}', 'green', color)}  "
        f"{_c(f'stale {stale}', 'yellow', color)}  "
        f"{_c(f'down {down}', 'red', color)}"
    )
    lines.append(_c(banner + CLEAR_LINE_END, None, color))
    lines.append("")

    # Column headers.
    header = "  ".join(_pad(h, w, r) for h, w, r in COLUMNS)
    lines.append(_c(header[:term_width], "grey", color))
    lines.append(_c("─" * min(len(header), term_width), "grey", color))

    for row in rows:
        glyph, label, color_name = _health(row, now)
        status = f"{glyph} {label}"
        fps = f"{row.fps:.1f}" if row.fps is not None else "-"
        mem = _fmt_bytes(row.mem)
        storage = _fmt_storage(row)
        pattern = row.active_pattern_name or (row.active_pattern_id[:10] if row.active_pattern_id else "-")
        pixels = str(row.pixel_count) if row.pixel_count is not None else "-"
        bright = f"{int(row.brightness * 100)}%" if row.brightness is not None else "-"
        uptime = _fmt_uptime(row.uptime)
        seen = _fmt_seen(row, now)
        cells = [
            status,
            row.name or "-",
            row.ip,
            fps,
            pattern,
            pixels,
            bright,
            storage,
            mem,
            uptime,
            row.ver or "-",
            seen,
        ]
        line = "  ".join(_pad(c, w, r) for c, (_, w, r) in zip(cells, COLUMNS))
        line = line[:term_width]
        # Color the whole row by status color (down = red, stale = yellow, ok = default).
        row_color = None if label == "ok" else color_name
        lines.append(_c(line, row_color, color) + CLEAR_LINE_END)

        if row.error and label != "ok":
            err_line = _c(f"    ↳ {row.error}", "grey", color)
            lines.append(err_line[: term_width] + CLEAR_LINE_END)

    lines.append("")
    lines.append(_c("  (q or ^C to quit)", "grey", color) + CLEAR_LINE_END)
    return "\n".join(lines)


# ── Command ────────────────────────────────────────────────────────────────


def register(cli_group):
    """Attach `top` command to the given click group.

    Kept as a function so cli.py can import and register without pulling the
    entire implementation into module import at click's decoration time."""

    @cli_group.command()
    @click.option("--interval", "-n", type=float, default=1.0, show_default=True,
                  help="Redraw interval in seconds.")
    @click.option("--once", is_flag=True,
                  help="Do a single discovery + render pass, then exit (scripting mode).")
    @click.option("--rediscover", "-r", type=float, default=30.0, show_default=True,
                  help="Re-run beacon discovery every N seconds to pick up new devices.")
    @click.option("--scan-timeout", type=int, default=2000, show_default=True,
                  help="Beacon listen timeout for each discovery round (ms).")
    @click.option("--sort", "sort_key", type=click.Choice(["name", "ip", "fps", "pattern"]),
                  default="name", show_default=True, help="Sort rows by this column.")
    @click.option("--no-color", is_flag=True, help="Disable ANSI color output.")
    @click.option("--json", "json_out", is_flag=True,
                  help="Emit one JSON snapshot to stdout and exit (with --once).")
    def top(interval, once, rediscover, scan_timeout, sort_key, no_color, json_out):
        """
        Realtime dashboard of every Pixelblaze on the network.

        Like Linux `top`, but for Pixelblazes: renders a live table with
        FPS, free storage, active pattern, uptime, and more. Devices that
        drop off the WiFi stay on the table as `down` and pop back to green
        when they rejoin.

        \b
        Examples:
            pb top                        # Live dashboard, redraws every 1s
            pb top -n 0.5                 # Faster redraw
            pb top --sort fps             # Sort by frames-per-second
            pb top --once                 # One snapshot, exit
            pb top --once --json          # Machine-readable snapshot
            pb top -r 10                  # Rediscover every 10s (default 30)
        """
        color = sys.stdout.isatty() and not no_color

        log("Discovering Pixelblazes…")
        initial = enumerate_pixelblazes(timeout=scan_timeout, slow=False)
        if not initial:
            raise click.ClickException("No Pixelblazes found on the network.")

        monitor = TopMonitor(initial, rediscover_seconds=rediscover)

        if once:
            # Config fetch takes a beat, then stats stream at ~1 Hz — give
            # every worker enough headroom to publish at least one frame.
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                if all(r.last_seen for r in monitor.snapshot()):
                    break
                time.sleep(0.1)
            snap = monitor.snapshot()
            monitor.stop()
            if json_out:
                click.echo(json.dumps([_row_to_dict(r) for r in snap], separators=(",", ":")))
            else:
                click.echo(_render(snap, color=color, sort_key=sort_key))
            return

        # Rediscovery thread.
        threading.Thread(
            target=monitor.rediscover_loop, args=(scan_timeout,),
            daemon=True, name="pb-top-rediscover",
        ).start()

        # SIGINT / SIGTERM → clean exit that still runs the finally block
        # below, so we always restore the cursor before returning.
        stop_flag = {"stop": False}

        def handle_signal(_signum, _frame):
            stop_flag["stop"] = True

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        if color:
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()

        try:
            while not stop_flag["stop"]:
                frame = _render(monitor.snapshot(), color=color, sort_key=sort_key)
                sys.stdout.write(CLEAR_HOME + frame)
                sys.stdout.flush()
                time.sleep(interval)
        finally:
            monitor.stop()
            if color:
                sys.stdout.write(SHOW_CURSOR + "\n")
                sys.stdout.flush()

    return top


def _row_to_dict(r: Row) -> dict:
    return {
        "ip": r.ip,
        "name": r.name,
        "ver": r.ver,
        "pixelCount": r.pixel_count,
        "brightness": r.brightness,
        "activePatternId": r.active_pattern_id,
        "activePatternName": r.active_pattern_name,
        "fps": r.fps,
        "mem": r.mem,
        "storageUsed": r.storage_used,
        "storageSize": r.storage_size,
        "uptime": r.uptime,
        "lastSeenMonotonic": r.last_seen,
        "connected": r.connected,
        "error": r.error,
    }
