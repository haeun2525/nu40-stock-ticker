#!/usr/bin/env python3
"""
bridge.py — fetches stock prices from the Toss Securities Open API and sends them to the NU40 DK board

Division of roles (important):
  - This program (PC) : internet calls, JSON parsing, % change math, chart values, text formatting
  - The board (NU40 DK): draws the text it receives, switches stocks with the buttons, drives the LEDs
  The NU40 has no Wi-Fi, so the board can't reach the internet itself. That's why the work is split.

Usage:
    1) Copy host/config.example.env to host/.env and fill in the values
    2) python3 host/bridge.py
    Press Ctrl+C to stop.

Note: this program only calls the price lookup APIs. There is no order (trading) code.
"""

import os
import re
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# Check required libraries (print a hint and exit if one is missing)
# ─────────────────────────────────────────────────────────────
try:
    import requests                     # internet calls
except ImportError:
    print("[Error] The requests library is missing.  pip3 install -r host/requirements.txt")
    sys.exit(1)

try:
    import serial                       # USB serial (pyserial)
    from serial.tools import list_ports
except ImportError:
    print("[Error] The pyserial library is missing.  pip3 install -r host/requirements.txt")
    sys.exit(1)

try:
    from dotenv import load_dotenv      # reads the .env file
except ImportError:
    print("[Error] The python-dotenv library is missing.  pip3 install -r host/requirements.txt")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────
API_BASE = "https://openapi.tossinvest.com"   # Toss Securities Open API

BAUD = 115200          # serial speed. Must match the firmware.
SLOT_COUNT = 4         # number of slots (= buttons)
CHART_POINTS = 64      # number of chart points (fixed by the protocol)
CHART_MAX = 36         # max chart value (protocol: integers 0-36)

CANDLE_REFRESH_SEC = 60      # how often to re-fetch the 1-minute candles for the chart. They only change once a minute.
CANDLE_PAGE_SIZE = 200       # max candles the API returns per request
CANDLE_MAX_PAGES = 7         # A Korean session is 390 min (2 pages), but US data runs through pre/after-market
                             # and a day passes 900 min. Allow enough pages to reach the session start.
SESSION_CACHE_SEC = 1800     # how often to re-ask for market hours (30 min)
BASE_REFRESH_SEC = 600       # how often to re-fetch the % change base (previous close). Changes daily; 10 min is plenty.
HTTP_TIMEOUT = 10            # seconds to wait for an internet call

HANGUL = re.compile(r"[가-힣]")   # detects Korean characters (used for text width)


def parse_timestamp(text):
    """Turn a candle time string (e.g. 2026-03-25T09:00:00+09:00) into seconds.
    Used to pick out today's session from the candles. Returns None on failure."""
    if not text:
        return None
    for candidate in (text, re.sub(r"\.\d+", "", text)):
        try:
            return datetime.fromisoformat(candidate).timestamp()
        except ValueError:
            continue
    return None


def log(msg):
    """Print one line with a timestamp."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────
class SlotConfig:
    """Settings for one slot.  SLOTn = symbol,display name,alert level"""

    def __init__(self, index, symbol, display_name, threshold):
        self.index = index
        self.symbol = symbol                # symbol sent to the API (e.g. 005930, TSLA)
        self.display_name = display_name    # human-readable name (e.g. Apple, 삼성전자)
        self.threshold = threshold          # the LED turns on below this price

    @property
    def market(self):
        """Which market this stock is in. A 6-digit code is Korea (KRX), anything else is US."""
        return "KR" if self.symbol.isdigit() and len(self.symbol) == 6 else "US"

    @property
    def screen_name(self):
        """The name shown on the OLED. Korean names are drawn as is
        (the firmware includes a 2,350-character Hangul font — tools/make_hangul_font.py).

        The % change sits on the right of the same row, so width is limited.
        The screen is 128 px and the % change takes about 48 px, so the name gets 80 px.
        A Korean character is 10 px; a Latin letter or digit is 6 px."""
        limit = 80
        out, width = "", 0
        for ch in self.display_name:
            w = 10 if "\uac00" <= ch <= "\ud7a3" else 6
            if width + w > limit:
                break
            out += ch
            width += w
        return out or self.symbol


def load_config():
    """Read .env and build the settings. On a problem, explain it and return None."""
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            log(f"Loaded settings: {candidate}")
            break
    else:
        log("Settings file (.env) not found. Copy host/config.example.env to host/.env.")
        return None

    client_id = (os.getenv("TOSS_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("TOSS_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        log("TOSS_CLIENT_ID or TOSS_CLIENT_SECRET is empty. Check .env.")
        return None

    port = (os.getenv("SERIAL_PORT") or "").strip()

    try:
        poll_seconds = float(os.getenv("POLL_SECONDS") or 5)
    except ValueError:
        log("POLL_SECONDS is not a number. Using the default, 5 seconds.")
        poll_seconds = 5.0
    if poll_seconds < 1:
        log("POLL_SECONDS is too short. Raising it to 1 second (API rate limits).")
        poll_seconds = 1.0

    chart_range = (os.getenv("CHART_RANGE") or "day").strip().lower()
    if chart_range not in ("day", "hour"):
        log(f"Unknown CHART_RANGE '{chart_range}'. Using the default, day (today's session).")
        chart_range = "day"

    slots = []
    for i in range(SLOT_COUNT):
        raw = (os.getenv(f"SLOT{i}") or "").strip()
        if not raw:
            log(f"SLOT{i} is empty. Fill it in .env as 'symbol,display name,alert level'.")
            return None
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 3:
            log(f"SLOT{i} is malformed: '{raw}' → it needs 3 fields: 'symbol,display name,alert level'.")
            return None
        try:
            threshold = float(parts[2].replace(",", ""))
        except ValueError:
            log(f"SLOT{i} alert level '{parts[2]}' is not a number.")
            return None
        slots.append(SlotConfig(i, parts[0], parts[1], threshold))

    # Never print client_secret here.
    log(f"Slots: " + ", ".join(f"{s.index}={s.symbol}({s.display_name}) alert below {s.threshold:,.2f}" for s in slots))
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "port": port,
        "poll_seconds": poll_seconds,
        "chart_range": chart_range,
        "slots": slots,
    }


# ─────────────────────────────────────────────────────────────
# Toss Securities API
# ─────────────────────────────────────────────────────────────
class TossApiError(Exception):
    """Raised when an API call fails. The message is always one readable line."""


class TossClient:
    """Uses only the lookup ('read') part of the Toss Securities Open API.

    There are deliberately no order (trading) functions.
    Only these endpoints are used:
      POST /oauth2/token       issue a token
      GET  /api/v1/prices      current prices (several symbols at once)
      GET  /api/v1/candles     candles (for the chart)
    """

    def __init__(self, client_id, client_secret):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = None
        self._token_expires_at = 0.0     # get a new token after this time
        self._session = requests.Session()
        self._session_cache = {}     # market hours per market (no need to ask every time)

    # ── Token ──────────────────────────────────────────────
    def _issue_token(self):
        """Get a new access token (OAuth 2.0 Client Credentials)."""
        try:
            resp = self._session.post(
                f"{API_BASE}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=HTTP_TIMEOUT,
            )
        except requests.exceptions.RequestException:
            raise TossApiError("Internet connection failed while getting a token.")

        if resp.status_code != 200:
            # Never print client_secret here either.
            if resp.status_code in (400, 401):
                raise TossApiError("Token refused — check client_id/client_secret.")
            if resp.status_code == 403:
                raise TossApiError("Token blocked (403) — add your current IP to the allowed IPs in Toss Securities Open API settings.")
            raise TossApiError(f"Token request failed (status {resp.status_code}).")

        try:
            body = resp.json()
            self._token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError):
            raise TossApiError("Couldn't understand the token response.")

        # Renew 1 minute before expiry (so we never use it right up to the edge).
        self._token_expires_at = time.time() + max(expires_in - 60, 30)
        log(f"Got an access token (valid for about {expires_in} s).")

    def _ensure_token(self):
        if self._token is None or time.time() >= self._token_expires_at:
            self._issue_token()

    # ── Shared request ─────────────────────────────────────
    def _get(self, path, params):
        """Send one lookup request. On 401, get a new token and retry once."""
        for attempt in (1, 2):
            self._ensure_token()
            try:
                resp = self._session.get(
                    f"{API_BASE}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=HTTP_TIMEOUT,
                )
            except requests.exceptions.Timeout:
                raise TossApiError(f"No response within {HTTP_TIMEOUT} s.")
            except requests.exceptions.RequestException:
                raise TossApiError("Internet connection failed.")

            if resp.status_code == 200:
                try:
                    return resp.json().get("result")
                except ValueError:
                    raise TossApiError("The response was not in the expected format.")

            if resp.status_code == 401 and attempt == 1:
                # The token expired. Get a new one and try once more.
                log("Token expired, getting a new one.")
                self._token = None
                continue

            if resp.status_code == 429:
                # Called too often. Pass the server's wait time up.
                wait = 1.0
                try:
                    wait = float(resp.headers.get("Retry-After", 1))
                except (TypeError, ValueError):
                    pass
                raise TossApiError(f"Rate limited. Retrying in {wait:.0f} s.")

            if resp.status_code == 403:
                raise TossApiError("Access denied (403) — check the allowed IPs in Toss Securities Open API settings.")

            # For any other error, pass on just the server's code.
            code = ""
            try:
                code = resp.json().get("error", {}).get("code", "")
            except (ValueError, AttributeError):
                pass
            raise TossApiError(f"Lookup failed (status {resp.status_code}{', ' + code if code else ''}).")

        raise TossApiError("Lookup failed repeatedly.")

    # ── The 3 lookups main() uses ─────────────────────────
    def get_prices(self, symbols):
        """Current prices, several symbols in one request.
        Returns: { '005930': {'price': 68900.0, 'currency': 'KRW'}, ... }"""
        result = self._get("/api/v1/prices", {"symbols": ",".join(symbols)}) or []
        out = {}
        for item in result:
            try:
                out[item["symbol"]] = {
                    "price": float(item["lastPrice"]),
                    "currency": item.get("currency") or "KRW",
                }
            except (KeyError, TypeError, ValueError):
                continue   # skip one bad item so the rest still work
        return out

    def _fetch_candles(self, symbol, interval, count, before=None):
        """Fetch one page of candles. Returns a list of (time_sec, close) and the next-page marker."""
        params = {"symbol": symbol, "interval": interval, "count": count}
        if before:
            params["before"] = before
        result = self._get("/api/v1/candles", params) or {}

        rows = []
        for c in result.get("candles") or []:
            try:
                rows.append((parse_timestamp(c.get("timestamp")), float(c["closePrice"])))
            except (KeyError, TypeError, ValueError):
                continue
        return rows, result.get("nextBefore")

    def get_candle_closes(self, symbol, interval, count):
        """Closing prices only, oldest first. (Used for the previous close.)"""
        rows, _ = self._fetch_candles(symbol, interval, count)
        rows = [r for r in rows if r[0] is not None]
        rows.sort(key=lambda r: r[0])
        return [price for _, price in rows]

    def get_session_window(self, market):
        """Work out the start and end of the trading 'day' to draw.

        Why this is needed:
          Splitting candles by time gaps works for Korean stocks but not US ones.
          Toss returns 1-minute candles for US stocks around the clock, so there is
          no gap between sessions. So we ask the market-hours API directly.

        Returns: (start_sec, end_sec, end_time_string)
          - today's regular session if it has already opened
          - otherwise the most recent finished regular session (e.g. US stocks viewed in the Korean daytime)
        """
        cached = self._session_cache.get(market)
        if cached and time.time() - cached[0] < SESSION_CACHE_SEC:
            return cached[1]

        result = self._get(f"/api/v1/market-calendar/{market}", {}) or {}

        def pick(day):
            """Pull the regular-session hours out of one day's info.
            Korea has them under 'integrated'; the US has them directly."""
            if not isinstance(day, dict):
                return None
            node = day.get("integrated") or day
            rm = node.get("regularMarket") or {}
            start = parse_timestamp(rm.get("startTime"))
            end = parse_timestamp(rm.get("endTime"))
            if start is None or end is None:
                return None
            return (start, end, rm.get("endTime"))

        today = pick(result.get("today"))
        previous = pick(result.get("previousBusinessDay"))

        now = time.time()
        if today and now >= today[0]:
            window = today          # today's session has opened
        else:
            window = previous or today   # not open yet: use the previous session

        if window:
            self._session_cache[market] = (now, window)
        return window

    def get_today_closes(self, symbol, market):
        """1-minute closes for today's (or the most recent) regular session.

        Pages backward from the session end, so it needs few requests.
        (Passing the close time as 'before' returns candles before that time.)
        """
        window = self.get_session_window(market)
        if not window:
            raise TossApiError(f"Couldn't get {market} market hours.")
        start, end, end_iso = window

        rows = []
        before = end_iso            # walk backward from the session close
        for _ in range(CANDLE_MAX_PAGES):
            page, before = self._fetch_candles(symbol, "1m", CANDLE_PAGE_SIZE, before)
            page = [r for r in page if r[0] is not None]
            if not page:
                break
            rows.extend(page)
            if min(r[0] for r in page) <= start:
                break               # reached the session start
            if not before:
                break

        rows = [(t, price) for t, price in rows if start <= t <= end]
        rows.sort(key=lambda r: r[0])
        return [price for _, price in rows]


# ─────────────────────────────────────────────────────────────
# Build the text for the screen
# ─────────────────────────────────────────────────────────────
def format_price(value, currency):
    """Turn a price into display text.
    Won: no decimals, with commas (68,900). Dollars: 2 decimals (398.20)."""
    if currency == "KRW":
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def downsample(values, points=CHART_POINTS):
    """Reduce any number of values to exactly `points` values.

    A day of 1-minute candles is about 390 values, but the screen fits only 64 points.
    Split the 390 into 64 buckets and keep each bucket's last value.
    Why the last value: the rightmost bucket then ends with the current price,
    so the right edge of the chart follows the live price.
    """
    if not values:
        return []
    if len(values) <= points:
        return list(values)

    out = []
    for i in range(points):
        start = int(i * len(values) / points)
        end = int((i + 1) * len(values) / points)
        if end <= start:
            end = start + 1
        out.append(values[end - 1])       # last value of each bucket
    return out


def normalize_chart(prices):
    """Turn a price list into 64 integers 0-36,
    scaled so the lowest price is 0 and the highest is 36."""
    if not prices:
        return [CHART_MAX // 2] * CHART_POINTS

    values = list(prices)
    if len(values) < CHART_POINTS:
        # If there are too few, pad the front with the first value so there are always 64.
        values = [values[0]] * (CHART_POINTS - len(values)) + values
    values = values[-CHART_POINTS:]

    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [CHART_MAX // 2] * CHART_POINTS   # all the same: flat line

    return [int(round((v - low) / (high - low) * CHART_MAX)) for v in values]


def build_line(slot_index, name, price_text, chg_text, alert, chart):
    """Build one line in protocol format.
    D|<slot>|<name>|<price>|<chg>|<alert>|<v0>,...,<v63>"""
    return "D|{}|{}|{}|{}|{}|{}".format(
        slot_index, name, price_text, chg_text,
        1 if alert else 0,
        ",".join(str(v) for v in chart),
    )


# ─────────────────────────────────────────────────────────────
# Board connection
# ─────────────────────────────────────────────────────────────
def find_port():
    """Find the serial port of the connected NU40 DK. None if not found."""
    candidates = []
    for p in list_ports.comports():
        if p.vid in (0x239A, 0x2FE3):        # vendor IDs this board uses
            candidates.append(p.device)
        elif "usbmodem" in p.device or "USB" in (p.description or ""):
            candidates.append(p.device)
    for c in candidates:
        if c.startswith("/dev/cu."):          # on a Mac, /dev/cu.* is more reliable
            return c
    return candidates[0] if candidates else None


class BoardLink:
    """Sends and receives text with the board."""

    def __init__(self, port):
        self.port = port
        self.ser = None
        self.ready_event = threading.Event()   # set when the board sends READY
        self._stop = False
        self._reader = None

    def open(self):
        try:
            self.ser = serial.Serial(self.port, BAUD, timeout=1)
        except Exception:
            raise TossApiError(
                f"Could not open serial port: {self.port}\n"
                "       If Arduino IDE's Serial Monitor is open, close it and try again.\n"
                "       (Only one program can use the port at a time.)"
            )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        """Keep reading READY / SEL| / ERR| from the board."""
        while not self._stop:
            try:
                raw = self.ser.readline()
            except Exception:
                return   # cable unplugged or port closed
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if line == "READY":
                log("Board ready — sending all 4 slots now.")
                self.ready_event.set()
            elif line.startswith("SEL|"):
                log(f"Board button selected slot {line[4:]}.")
            elif line.startswith("ERR|"):
                log(f"Board error: {line[4:]}")
            else:
                log(f"Board: {line}")

    def send(self, line):
        try:
            # Names may be Korean, so send UTF-8. The firmware decodes UTF-8.
            self.ser.write((line + "\n").encode("utf-8"))
            self.ser.flush()
            return True
        except Exception:
            log("Couldn't send to the board. Check that the USB cable is still connected.")
            return False

    def close(self):
        self._stop = True
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(" NU40 DK Live Stock Ticker — Toss Securities Open API")
    print("=" * 60)

    config = load_config()
    if config is None:
        return 1

    port = config["port"] or find_port()
    if not port:
        log("Board not found. Check the USB cable or set SERIAL_PORT in .env.")
        return 1

    slots = config["slots"]
    symbols = [s.symbol for s in slots]

    board = BoardLink(port)
    try:
        board.open()
    except TossApiError as e:
        log(f"Error: {e}")
        return 1
    log(f"Connected to the board: {port} ({BAUD} baud)")

    client = TossClient(config["client_id"], config["client_secret"])

    # Keep the last successful values for each slot.
    # If the internet drops, the board keeps showing these last good values.
    last_good = {}          # symbol -> (price_text, chg_text, alert, chart)
    minute_closes = {}      # symbol -> 1-minute closes (for the chart)
    base_close = {}         # symbol -> previous close (base for % change)
    currency = {}           # symbol -> 'KRW' or 'USD'

    last_candle_at = 0.0
    last_base_at = 0.0
    backoff = 0.0           # pause after an error

    log("Waiting for READY from the board... (up to 3 s)")
    if not board.ready_event.wait(timeout=3.0):
        log("No READY, continuing anyway. (The board was probably already running.)")

    range_label = "today's whole session" if config["chart_range"] == "day" else "last 64 minutes"
    log(f"Chart range: {range_label}")
    log(f"Fetching prices every {config['poll_seconds']:.0f} s. Press Ctrl+C to stop.")
    print()

    try:
        while True:
            now = time.time()

            # 1) Refresh the % change base (previous close) now and then.
            if now - last_base_at > BASE_REFRESH_SEC:
                for s in slots:
                    try:
                        closes = client.get_candle_closes(s.symbol, "1d", 2)
                        if len(closes) >= 2:
                            base_close[s.symbol] = closes[-2]   # second to last = previous close
                        elif closes:
                            base_close[s.symbol] = closes[-1]
                    except TossApiError as e:
                        log(f"{s.symbol} previous close lookup failed: {e}")
                    time.sleep(0.15)   # small gap to stay under the rate limit
                last_base_at = now

            # 2) Refresh the chart's 1-minute candles now and then (they change once a minute).
            if now - last_candle_at > CANDLE_REFRESH_SEC:
                for s in slots:
                    try:
                        if config["chart_range"] == "day":
                            closes = client.get_today_closes(s.symbol, s.market)   # today's whole regular session
                        else:
                            closes = client.get_candle_closes(s.symbol, "1m", CHART_POINTS)  # last 64 minutes
                        if closes:
                            minute_closes[s.symbol] = closes
                    except TossApiError as e:
                        log(f"{s.symbol} chart lookup failed: {e}")
                    time.sleep(0.15)
                last_candle_at = now

            # 3) Fetch current prices every time. One request covers all 4 stocks.
            try:
                prices = client.get_prices(symbols)
                backoff = 0.0
            except TossApiError as e:
                log(f"{e}  (the board keeps showing the last values)")
                # If errors repeat, wait longer each time. Up to 60 s.
                backoff = min(backoff * 2 if backoff else 2.0, 60.0)
                time.sleep(backoff)
                continue

            # 4) Build the line to send for each slot.
            for s in slots:
                info = prices.get(s.symbol)

                if info is None:
                    # No value this time. Resend the last good one if we have it.
                    if s.symbol in last_good:
                        price_text, chg_text, alert, chart = last_good[s.symbol]
                        board.send(build_line(s.index, s.screen_name, price_text, chg_text, alert, chart))
                    continue

                price = info["price"]
                currency[s.symbol] = info["currency"]

                # % change = (current - previous close) / previous close × 100
                base = base_close.get(s.symbol)
                if base:
                    chg_text = f"{(price - base) / base * 100:+.2f}%"
                else:
                    chg_text = "--%"     # base not received yet

                # Chart: append the current price after the stored 1-minute closes.
                # Downsampling to 64 points happens after that, so the rightmost point is the current price.
                # So even a full-day chart moves at its right edge every 5 seconds.
                series = list(minute_closes.get(s.symbol) or [])
                series.append(price)
                chart = normalize_chart(downsample(series))

                alert = price < s.threshold
                price_text = format_price(price, info["currency"])

                last_good[s.symbol] = (price_text, chg_text, alert, chart)
                board.send(build_line(s.index, s.screen_name, price_text, chg_text, alert, chart))

            # 5) One readable summary line
            summary = []
            for s in slots:
                if s.symbol in last_good:
                    p, c, a, _ = last_good[s.symbol]
                    summary.append(f"{s.screen_name} {p} {c}{' [ALERT]' if a else ''}")
            if summary:
                log("  |  ".join(summary))

            time.sleep(config["poll_seconds"])

    except KeyboardInterrupt:
        print()
        log("Stopped by user.")
    except Exception as e:
        # Turn unexpected problems into one readable line instead of a stack trace.
        log(f"Unexpected problem, exiting: {type(e).__name__}")
    finally:
        board.close()
        log("Port closed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
