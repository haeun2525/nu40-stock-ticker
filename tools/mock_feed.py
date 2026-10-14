#!/usr/bin/env python3
"""
mock_feed.py — test the board with simulated stock prices first

Checks the wiring and firmware on their own, before any price API is involved.
It makes up realistic-looking prices and sends them to the board.
If numbers and a chart appear, the 4 buttons switch stocks, and the LEDs light up,
the hardware is fine.

Usage (in Terminal):
    python3 tools/mock_feed.py                       # find the port automatically
    python3 tools/mock_feed.py --port /dev/cu.usbmodem1101
    python3 tools/mock_feed.py --alert-after 15      # force an alert (LED) after 15 s

Press Ctrl+C to stop.

Note: this file runs on its own on purpose (it does not import bridge.py),
      so you can tell clearly whether a problem is the API or the board.
      The only requirement is pyserial:  pip install pyserial
"""

import argparse
import math
import random
import re
import sys
import threading
import time
from pathlib import Path

try:
    import serial                      # pyserial: USB serial library
    from serial.tools import list_ports
except ImportError:
    print("[Error] pyserial is not installed.", flush=True)
    print("        Run this in Terminal:  pip3 install pyserial", flush=True)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────
BAUD = 115200          # serial speed. Must match the firmware.
CHART_POINTS = 64      # number of chart points (fixed by the protocol)
CHART_MAX = 36         # max chart value (protocol: integers 0-36)

# 4 simulated stocks. If host/.env exists, its SLOT settings are used;
# otherwise these defaults. (So you only edit your stocks in one place.)
#   Format: (display name, start price, alert level, decimals, use commas)
DEFAULT_SLOTS = [
    ("Apple",     182.40, 180.0, 2, False),
    ("Nvidia",    173.50, 170.0, 2, False),
    ("Tesla",     410.00, 400.0, 2, False),
    ("Microsoft", 410.00, 400.0, 2, False),
]
# Korean example (the firmware's Hangul font renders these too — swap them in if you like):
#   ("삼성전자", 270000.0, 268300.0, 0, True), ("SK하이닉스", 1679000.0, 1668200.0, 0, True),
#   ("네이버", 218000.0, 215300.0, 0, True), ("카카오", 38250.0, 37900.0, 0, True)

HANGUL = re.compile(r"[가-힣]")


def load_slots_from_env():
    """Build the simulated stock list from SLOT0-SLOT3 in host/.env.

    Returns None (so the defaults are used) if .env is missing or malformed.
    (Unlike bridge.py it parses the file by hand, so this tool only needs pyserial.)
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "host/.env", here.parent / ".env"):
        if not candidate.exists():
            continue

        values = {}
        for line in candidate.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()

        slots = []
        for i in range(4):
            raw = values.get(f"SLOT{i}", "")
            parts = [x.strip() for x in raw.split(",")]
            if len(parts) != 3:
                return None
            symbol, display, threshold = parts
            try:
                threshold = float(threshold.replace(",", ""))
            except ValueError:
                return None

            # Same on-screen name rule as bridge.py. Korean is drawn as is,
            # but names are cut at 80 pixels to leave room for the % change.
            name, width = "", 0
            for ch in display:
                w = 10 if "\uac00" <= ch <= "\ud7a3" else 6
                if width + w > 80:
                    break
                name += ch
                width += w
            name = name or symbol

            # Korean stocks (6-digit code) are in won, everything else in dollars.
            domestic = symbol.isdigit() and len(symbol) == 6
            start = threshold * 1.01          # start just above the alert level
            slots.append((name, start, threshold, 0 if domestic else 2, domestic))

        if len(slots) == 4:
            print(f"[Info] Using the stocks from {candidate}", flush=True)
            return slots
    return None


MOCK_SLOTS = load_slots_from_env() or DEFAULT_SLOTS


# ─────────────────────────────────────────────────────────────
# Find the port
# ─────────────────────────────────────────────────────────────
def find_port():
    """Find the serial port of the connected NU40 DK. None if not found."""
    candidates = []
    for p in list_ports.comports():
        # nRF52840 boards report vendor ID 0x239A (Adafruit-style bootloader).
        if p.vid == 0x239A:
            candidates.append(p.device)
        elif "usbmodem" in p.device or "USB" in (p.description or ""):
            candidates.append(p.device)

    # On a Mac, /dev/cu.* is the more reliable choice.
    for c in candidates:
        if c.startswith("/dev/cu."):
            return c
    return candidates[0] if candidates else None


# ─────────────────────────────────────────────────────────────
# Build one line in protocol format
#   D|<slot>|<name>|<price>|<chg>|<alert>|<v0>,...,<v63>
# ─────────────────────────────────────────────────────────────
def format_price(value, decimals, use_comma):
    """Turn a price into display text. e.g. 68900.0 -> '68,900'"""
    if use_comma:
        return f"{value:,.{decimals}f}"
    return f"{value:.{decimals}f}"


def normalize_chart(history):
    """Turn a price list into 64 integers 0-36, scaled to the chart height."""
    # If there are too few, pad the front with the first value so there are always 64.
    values = list(history)
    if len(values) < CHART_POINTS:
        values = [values[0]] * (CHART_POINTS - len(values)) + values
    values = values[-CHART_POINTS:]

    low, high = min(values), max(values)
    if high - low < 1e-9:
        # If every value is the same, draw a flat line at mid height.
        return [CHART_MAX // 2] * CHART_POINTS

    out = []
    for v in values:
        ratio = (v - low) / (high - low)
        out.append(int(round(ratio * CHART_MAX)))
    return out


def build_line(slot, name, price_text, chg_text, alert, chart):
    chart_text = ",".join(str(v) for v in chart)
    return f"D|{slot}|{name}|{price_text}|{chg_text}|{1 if alert else 0}|{chart_text}"


# ─────────────────────────────────────────────────────────────
# Read lines from the board (READY / SEL / ERR)
# ─────────────────────────────────────────────────────────────
class BoardReader(threading.Thread):
    """Keeps reading what the board sends and prints it."""

    def __init__(self, ser):
        super().__init__(daemon=True)
        self.ser = ser
        self.ready_event = threading.Event()   # set when the board sends READY
        self.stop_flag = False

    def run(self):
        while not self.stop_flag:
            try:
                raw = self.ser.readline()
            except Exception:
                # Cable unplugged or port closed. Exit quietly.
                return
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if line == "READY":
                print("[Board] Ready - sending all 4 slots now.", flush=True)
                self.ready_event.set()
            elif line.startswith("SEL|"):
                print(f"[Board] Button selected slot {line[4:]}", flush=True)
            elif line.startswith("ERR|"):
                print(f"[Board error] {line[4:]}", flush=True)
            else:
                print(f"[Board] {line}", flush=True)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Test the NU40 DK board with simulated stock prices."
    )
    parser.add_argument("--port", help="serial port (e.g. /dev/cu.usbmodem1101 or COM3)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between updates (default 1)")
    parser.add_argument("--alert-after", type=float, default=0,
                        help="after this many seconds, push slots below their alert level to light the LEDs (0 = off)")
    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        print("[Error] Board not found.", flush=True)
        print("        Check the USB cable. If it still fails, set the port", flush=True)
        print("        yourself with --port, e.g. --port /dev/cu.usbmodem1101", flush=True)
        return 1

    print(f"[Info] Connecting to {port} at {BAUD} baud.", flush=True)

    try:
        ser = serial.Serial(port, BAUD, timeout=1)
    except Exception as e:
        print(f"[Error] Could not open port: {port}", flush=True)
        print(f"        Cause: {type(e).__name__}", flush=True)
        print("        If Arduino IDE's Serial Monitor is open, close it and try again.", flush=True)
        print("        (Only one program can use the port at a time.)", flush=True)
        return 1

    reader = BoardReader(ser)
    reader.start()

    # The board sends READY right after it starts. Wait up to 3 s, then go on anyway
    # (a board that was already running won't send READY again).
    print("[Info] Waiting for READY from the board... (up to 3 s)", flush=True)
    if not reader.ready_event.wait(timeout=3.0):
        print("[Info] No READY, sending anyway. (The board was probably already running.)", flush=True)

    # Price history for each slot, starting as 64 copies of the start price.
    histories = [[start] * CHART_POINTS for (_, start, _, _, _) in MOCK_SLOTS]
    base_prices = [start for (_, start, _, _, _) in MOCK_SLOTS]

    started_at = time.time()
    tick = 0

    print("[Info] Sending simulated prices. Press Ctrl+C to stop.", flush=True)
    print(flush=True)

    try:
        while True:
            elapsed = time.time() - started_at
            lines = []

            for slot, (name, start, threshold, decimals, use_comma) in enumerate(MOCK_SLOTS):
                last = histories[slot][-1]

                # Look realistic: a gentle wave plus a little random noise
                wave = math.sin((elapsed / 12.0) + slot) * (start * 0.004)
                noise = random.uniform(-1, 1) * (start * 0.0015)
                price = last + wave * 0.15 + noise

                # With --alert-after, slots 0 and 2 are pushed below their alert level after that time.
                if args.alert_after and elapsed > args.alert_after and slot in (0, 2):
                    price = min(price, threshold * 0.995)

                histories[slot].append(price)
                histories[slot] = histories[slot][-CHART_POINTS:]

                base = base_prices[slot]
                chg_pct = (price - base) / base * 100.0

                line = build_line(
                    slot=slot,
                    name=name,
                    price_text=format_price(price, decimals, use_comma),
                    chg_text=f"{chg_pct:+.2f}%",
                    alert=price < threshold,
                    chart=normalize_chart(histories[slot]),
                )
                lines.append((name, price, chg_pct, price < threshold, line))

            for name, price, chg_pct, alert, line in lines:
                ser.write((line + "\n").encode("utf-8"))   # may contain Korean
            ser.flush()

            tick += 1
            if tick % 5 == 1:   # print a summary every 5th send only (keeps the log readable)
                summary = "  ".join(
                    f"{n}={p:,.2f}{'(!)' if a else ''}" for n, p, _, a, _ in lines
                )
                print(f"[Sent {tick:>4}] {summary}", flush=True)

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(flush=True)
        print("[Info] Stopped by user.", flush=True)
    except Exception as e:
        print(f"[Error] Problem while sending: {type(e).__name__}", flush=True)
        print("        The cable may be unplugged or the port closed.", flush=True)
    finally:
        reader.stop_flag = True
        try:
            ser.close()
        except Exception:
            pass
        print("[Info] Port closed.", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
