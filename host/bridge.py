#!/usr/bin/env python3
"""
bridge.py — 토스증권 오픈 API에서 주가를 가져와 NU40 DK 보드로 보내는 프로그램

역할 분담(중요):
  - 이 프로그램(PC)이  : 인터넷 호출, JSON 해석, 등락률 계산, 차트 값 만들기, 글자 꾸미기
  - 보드(NU40 DK)가    : 받은 글자를 화면에 그리기, 버튼으로 종목 바꾸기, LED 켜기
  NU40에는 WiFi가 없어서 보드가 직접 인터넷을 쓸 수 없다. 그래서 이렇게 나눈다.

쓰는 법:
    1) host/config.example.env 를 복사해서 host/.env 로 만들고 값을 채운다
    2) python3 host/bridge.py
    멈추려면 Ctrl+C.

※ 이 프로그램은 시세 조회 API만 부른다. 주문(매매) 기능은 만들지 않았다.
"""

import os
import re
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 필요한 라이브러리 확인 (없으면 한국어로 안내하고 끝낸다)
# ─────────────────────────────────────────────────────────────
try:
    import requests                     # 인터넷 호출용
except ImportError:
    print("[오류] requests 라이브러리가 없습니다.  pip3 install -r host/requirements.txt")
    sys.exit(1)

try:
    import serial                       # USB 시리얼 통신용 (pyserial)
    from serial.tools import list_ports
except ImportError:
    print("[오류] pyserial 라이브러리가 없습니다.  pip3 install -r host/requirements.txt")
    sys.exit(1)

try:
    from dotenv import load_dotenv      # .env 파일 읽기용
except ImportError:
    print("[오류] python-dotenv 라이브러리가 없습니다.  pip3 install -r host/requirements.txt")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# 고정값
# ─────────────────────────────────────────────────────────────
API_BASE = "https://openapi.tossinvest.com"   # 토스증권 오픈 API 주소

BAUD = 115200          # 시리얼 속도. 펌웨어와 반드시 같아야 한다.
SLOT_COUNT = 4         # 슬롯(=버튼) 개수
CHART_POINTS = 64      # 차트 점 개수 (통신 규약상 고정)
CHART_MAX = 36         # 차트 값의 최대치 (통신 규약상 0~36 정수)

CANDLE_REFRESH_SEC = 60      # 차트용 1분봉을 다시 받아오는 주기. 1분봉은 1분에 한 번만 바뀐다.
CANDLE_PAGE_SIZE = 200       # 캔들 API가 한 번에 주는 최대 개수
CANDLE_MAX_PAGES = 7         # 국내 장은 390분(2페이지)이면 되지만, 미국은 프리·애프터마켓까지
                             # 이어져 하루가 900분을 넘는다. 장 경계를 확실히 만나도록 넉넉히 잡는다.
SESSION_CACHE_SEC = 1800     # 장 운영 시간을 다시 물어보는 주기 (30분)
BASE_REFRESH_SEC = 600       # 등락률 기준(전일 종가)을 다시 받아오는 주기. 하루에 한 번 바뀌지만 넉넉히 10분.
HTTP_TIMEOUT = 10            # 인터넷 호출을 몇 초까지 기다릴지

HANGUL = re.compile(r"[가-힣]")   # 한글이 섞였는지 검사용


def parse_timestamp(text):
    """캔들의 시각 문자열(예: 2026-03-25T09:00:00+09:00)을 초 단위 숫자로 바꾼다.
    캔들 사이의 시간 간격을 재서 '오늘 장'만 골라내는 데 쓴다. 실패하면 None."""
    if not text:
        return None
    for candidate in (text, re.sub(r"\.\d+", "", text)):
        try:
            return datetime.fromisoformat(candidate).timestamp()
        except ValueError:
            continue
    return None


def log(msg):
    """시각과 함께 한 줄 찍는다."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────
# 설정 읽기
# ─────────────────────────────────────────────────────────────
class SlotConfig:
    """슬롯 하나의 설정.  SLOTn = 종목코드,표시이름,임계가"""

    def __init__(self, index, symbol, display_name, threshold):
        self.index = index
        self.symbol = symbol                # API에 보낼 종목코드 (예: 005930, TSLA)
        self.display_name = display_name    # 사람이 읽는 이름 (예: 삼성전자)
        self.threshold = threshold          # 이 값 아래로 내려가면 LED를 켠다

    @property
    def market(self):
        """이 종목이 어느 시장인지. 6자리 숫자면 국내(KRX), 아니면 미국."""
        return "KR" if self.symbol.isdigit() and len(self.symbol) == 6 else "US"

    @property
    def screen_name(self):
        """OLED에 띄울 이름.
        기본 글꼴에는 한글이 없어서 한글 이름을 그리면 글자가 깨진다.
        그래서 이름에 한글이 섞이면 종목코드를 대신 쓴다. 최대 10자."""
        name = self.symbol if HANGUL.search(self.display_name) else self.display_name
        return name[:10]


def load_config():
    """.env 를 읽어서 설정을 만든다. 문제가 있으면 한국어로 알려주고 None을 돌려준다."""
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            log(f"설정 파일을 읽었습니다: {candidate}")
            break
    else:
        log("설정 파일(.env)을 찾지 못했습니다. host/config.example.env 를 복사해 host/.env 로 만드세요.")
        return None

    client_id = (os.getenv("TOSS_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("TOSS_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        log("TOSS_CLIENT_ID 또는 TOSS_CLIENT_SECRET 이 비어 있습니다. .env를 확인하세요.")
        return None

    port = (os.getenv("SERIAL_PORT") or "").strip()

    try:
        poll_seconds = float(os.getenv("POLL_SECONDS") or 5)
    except ValueError:
        log("POLL_SECONDS 값이 숫자가 아닙니다. 기본값 5초를 씁니다.")
        poll_seconds = 5.0
    if poll_seconds < 1:
        log("POLL_SECONDS가 너무 짧습니다. 최소 1초로 올립니다. (API 호출 제한 때문)")
        poll_seconds = 1.0

    chart_range = (os.getenv("CHART_RANGE") or "day").strip().lower()
    if chart_range not in ("day", "hour"):
        log(f"CHART_RANGE 값 '{chart_range}' 을 알 수 없습니다. 기본값 day(오늘 하루)를 씁니다.")
        chart_range = "day"

    slots = []
    for i in range(SLOT_COUNT):
        raw = (os.getenv(f"SLOT{i}") or "").strip()
        if not raw:
            log(f"SLOT{i} 이 비어 있습니다. .env에 '종목코드,표시이름,임계가' 형식으로 채우세요.")
            return None
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 3:
            log(f"SLOT{i} 형식이 잘못됐습니다: '{raw}' → '종목코드,표시이름,임계가' 3칸이어야 합니다.")
            return None
        try:
            threshold = float(parts[2].replace(",", ""))
        except ValueError:
            log(f"SLOT{i} 의 임계가 '{parts[2]}' 가 숫자가 아닙니다.")
            return None
        slots.append(SlotConfig(i, parts[0], parts[1], threshold))

    # 여기서 client_secret 은 절대 화면에 찍지 않는다.
    log(f"슬롯 설정: " + ", ".join(f"{s.index}={s.symbol}({s.display_name}) 임계가 {s.threshold:,.2f}" for s in slots))
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "port": port,
        "poll_seconds": poll_seconds,
        "chart_range": chart_range,
        "slots": slots,
    }


# ─────────────────────────────────────────────────────────────
# 토스증권 API 담당
# ─────────────────────────────────────────────────────────────
class TossApiError(Exception):
    """API 호출이 실패했을 때 쓰는 오류. 메시지는 항상 한국어 한 줄."""


class TossClient:
    """토스증권 오픈 API 중 '조회' 기능만 사용하는 담당자.

    주문(매매) 관련 함수는 일부러 만들지 않았다.
    쓰는 엔드포인트는 아래 3개뿐이다.
      POST /oauth2/token       토큰 발급
      GET  /api/v1/prices      현재가 조회 (여러 종목을 한 번에)
      GET  /api/v1/candles     캔들(차트용) 조회
    """

    def __init__(self, client_id, client_secret):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = None
        self._token_expires_at = 0.0     # 이 시각이 지나면 토큰을 다시 받는다
        self._session = requests.Session()
        self._session_cache = {}     # 시장별 장 운영 시간 캐시 (매번 물어볼 필요 없다)

    # ── 토큰 ───────────────────────────────────────────────
    def _issue_token(self):
        """액세스 토큰을 새로 발급받는다. (OAuth 2.0 Client Credentials 방식)"""
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
            raise TossApiError("토큰 발급 중 인터넷 연결에 실패했습니다.")

        if resp.status_code != 200:
            # 여기서도 client_secret 은 절대 찍지 않는다.
            if resp.status_code in (400, 401):
                raise TossApiError("토큰 발급 거부 — client_id/client_secret 이 맞는지 확인하세요.")
            if resp.status_code == 403:
                raise TossApiError("토큰 발급 차단(403) — 토스증권 설정의 '허용 IP 관리'에 현재 IP를 등록했는지 확인하세요.")
            raise TossApiError(f"토큰 발급 실패 (응답 코드 {resp.status_code}).")

        try:
            body = resp.json()
            self._token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError):
            raise TossApiError("토큰 발급 응답을 이해하지 못했습니다.")

        # 만료 1분 전에 미리 갱신한다(딱 맞춰 쓰다가 실패하지 않게).
        self._token_expires_at = time.time() + max(expires_in - 60, 30)
        log(f"토큰을 발급받았습니다. (약 {expires_in}초 동안 유효)")

    def _ensure_token(self):
        if self._token is None or time.time() >= self._token_expires_at:
            self._issue_token()

    # ── 공통 호출 ──────────────────────────────────────────
    def _get(self, path, params):
        """조회 요청 하나를 보낸다. 401이면 토큰을 다시 받아 한 번만 재시도한다."""
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
                raise TossApiError(f"응답이 {HTTP_TIMEOUT}초 안에 오지 않았습니다.")
            except requests.exceptions.RequestException:
                raise TossApiError("인터넷 연결에 실패했습니다.")

            if resp.status_code == 200:
                try:
                    return resp.json().get("result")
                except ValueError:
                    raise TossApiError("응답이 올바른 형식이 아닙니다.")

            if resp.status_code == 401 and attempt == 1:
                # 토큰이 만료됐다는 뜻. 새로 받고 한 번 더 시도한다.
                log("토큰이 만료되어 다시 발급받습니다.")
                self._token = None
                continue

            if resp.status_code == 429:
                # 너무 자주 불렀다는 뜻. 서버가 알려준 만큼 기다리라고 위로 알린다.
                wait = 1.0
                try:
                    wait = float(resp.headers.get("Retry-After", 1))
                except (TypeError, ValueError):
                    pass
                raise TossApiError(f"호출 횟수 제한에 걸렸습니다. {wait:.0f}초 뒤 다시 시도합니다.")

            if resp.status_code == 403:
                raise TossApiError("접근이 거부됐습니다(403) — 토스증권 설정의 '허용 IP 관리'를 확인하세요.")

            # 그 밖의 오류는 서버가 준 코드만 짧게 옮긴다.
            code = ""
            try:
                code = resp.json().get("error", {}).get("code", "")
            except (ValueError, AttributeError):
                pass
            raise TossApiError(f"조회 실패 (응답 코드 {resp.status_code}{', ' + code if code else ''}).")

        raise TossApiError("조회에 반복 실패했습니다.")

    # ── 실제로 쓰는 조회 3종 ───────────────────────────────
    def get_prices(self, symbols):
        """현재가 조회. 종목 여러 개를 한 번에 물어본다.
        돌려주는 값: { '005930': {'price': 68900.0, 'currency': 'KRW'}, ... }"""
        result = self._get("/api/v1/prices", {"symbols": ",".join(symbols)}) or []
        out = {}
        for item in result:
            try:
                out[item["symbol"]] = {
                    "price": float(item["lastPrice"]),
                    "currency": item.get("currency") or "KRW",
                }
            except (KeyError, TypeError, ValueError):
                continue   # 값이 이상한 종목 하나 때문에 전체가 멈추지 않게 넘어간다
        return out

    def _fetch_candles(self, symbol, interval, count, before=None):
        """캔들 한 페이지를 받아 (시각초, 종가) 목록과 다음 페이지 표시를 돌려준다."""
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
        """캔들의 종가만 뽑아서 '오래된 것 → 최신' 순서로 돌려준다. (전일 종가 조회용)"""
        rows, _ = self._fetch_candles(symbol, interval, count)
        rows = [r for r in rows if r[0] is not None]
        rows.sort(key=lambda r: r[0])
        return [price for _, price in rows]

    def get_session_window(self, market):
        """지금 화면에 그려야 할 '하루'가 언제부터 언제까지인지 알아낸다.

        왜 필요한가:
          간격으로 자르는 방법은 국내 종목엔 통하지만 미국 종목엔 통하지 않는다.
          토스 API가 미국 종목은 24시간 내내 1분봉을 주기 때문에 장과 장 사이의
          '빈 구간'이 없다. 그래서 장 운영 시간 API에 직접 물어본다.

        돌려주는 값: (시작초, 끝초, 끝시각문자열)
          - 오늘 장이 이미 열렸으면 오늘 정규장
          - 아직 안 열렸으면 가장 최근에 끝난 정규장 (예: 낮에 보는 미국 주식)
        """
        cached = self._session_cache.get(market)
        if cached and time.time() - cached[0] < SESSION_CACHE_SEC:
            return cached[1]

        result = self._get(f"/api/v1/market-calendar/{market}", {}) or {}

        def pick(day):
            """하루치 정보에서 정규장 시간만 뽑는다.
            국내는 integrated 아래에, 미국은 바로 아래에 들어 있다."""
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
            window = today          # 오늘 장이 열렸다
        else:
            window = previous or today   # 아직 안 열렸으면 직전 장

        if window:
            self._session_cache[market] = (now, window)
        return window

    def get_today_closes(self, symbol, market):
        """오늘(또는 가장 최근) 정규장 구간의 1분봉 종가를 모아 돌려준다.

        장 끝 시각부터 거꾸로 페이지를 넘기며 받기 때문에 요청 수가 적다.
        (before 파라미터에 장 마감 시각을 주면 그 이전 봉부터 받는다)
        """
        window = self.get_session_window(market)
        if not window:
            raise TossApiError(f"{market} 장 운영 시간을 확인하지 못했습니다.")
        start, end, end_iso = window

        rows = []
        before = end_iso            # 장 마감 시각부터 거꾸로 훑는다
        for _ in range(CANDLE_MAX_PAGES):
            page, before = self._fetch_candles(symbol, "1m", CANDLE_PAGE_SIZE, before)
            page = [r for r in page if r[0] is not None]
            if not page:
                break
            rows.extend(page)
            if min(r[0] for r in page) <= start:
                break               # 장 시작 시각까지 다 받았다
            if not before:
                break

        rows = [(t, price) for t, price in rows if start <= t <= end]
        rows.sort(key=lambda r: r[0])
        return [price for _, price in rows]


# ─────────────────────────────────────────────────────────────
# 화면에 보낼 글자 만들기
# ─────────────────────────────────────────────────────────────
def format_price(value, currency):
    """가격 숫자를 화면용 글자로 바꾼다.
    원화는 소수점 없이 콤마(68,900), 달러는 소수점 둘째 자리까지(398.20)."""
    if currency == "KRW":
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def downsample(values, points=CHART_POINTS):
    """값이 몇 개든 정확히 points 개로 줄인다.

    하루치 1분봉은 약 390개인데 화면에 그릴 수 있는 점은 64개뿐이다.
    390개를 64칸으로 나눠 담고, 각 칸의 '마지막 값'을 대표로 쓴다.
    마지막 값을 쓰는 이유: 맨 오른쪽 칸의 대표가 곧 지금 가격이 되어
    화면의 오른쪽 끝이 실시간 가격을 그대로 따라가기 때문이다.
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
        out.append(values[end - 1])       # 각 칸의 마지막 값
    return out


def normalize_chart(prices):
    """가격 목록을 0~36 정수 64개로 바꾼다.
    가장 싼 값이 0, 가장 비싼 값이 36이 되도록 비율을 맞추는 작업이다."""
    if not prices:
        return [CHART_MAX // 2] * CHART_POINTS

    values = list(prices)
    if len(values) < CHART_POINTS:
        # 개수가 모자라면 앞쪽을 첫 값으로 채워서 항상 64개를 만든다.
        values = [values[0]] * (CHART_POINTS - len(values)) + values
    values = values[-CHART_POINTS:]

    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [CHART_MAX // 2] * CHART_POINTS   # 값이 전부 같으면 평평한 선

    return [int(round((v - low) / (high - low) * CHART_MAX)) for v in values]


def build_line(slot_index, name, price_text, chg_text, alert, chart):
    """통신 규약대로 한 줄을 만든다.
    D|<slot>|<name>|<price>|<chg>|<alert>|<v0>,...,<v63>"""
    return "D|{}|{}|{}|{}|{}|{}".format(
        slot_index, name, price_text, chg_text,
        1 if alert else 0,
        ",".join(str(v) for v in chart),
    )


# ─────────────────────────────────────────────────────────────
# 보드와의 연결
# ─────────────────────────────────────────────────────────────
def find_port():
    """연결된 NU40 DK 보드의 시리얼 포트를 찾는다. 못 찾으면 None."""
    candidates = []
    for p in list_ports.comports():
        if p.vid in (0x239A, 0x2FE3):        # 이 보드가 쓰는 제조사 ID
            candidates.append(p.device)
        elif "usbmodem" in p.device or "USB" in (p.description or ""):
            candidates.append(p.device)
    for c in candidates:
        if c.startswith("/dev/cu."):          # 맥에서는 /dev/cu.* 가 안정적이다
            return c
    return candidates[0] if candidates else None


class BoardLink:
    """보드와 글자를 주고받는 담당."""

    def __init__(self, port):
        self.port = port
        self.ser = None
        self.ready_event = threading.Event()   # 보드가 READY를 보내면 켜지는 깃발
        self._stop = False
        self._reader = None

    def open(self):
        try:
            self.ser = serial.Serial(self.port, BAUD, timeout=1)
        except Exception:
            raise TossApiError(
                f"시리얼 포트를 열지 못했습니다: {self.port}\n"
                "       아두이노 IDE의 시리얼 모니터가 켜져 있으면 닫고 다시 시도하세요.\n"
                "       (포트는 한 번에 한 프로그램만 쓸 수 있습니다.)"
            )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        """보드가 보내는 READY / SEL| / ERR| 를 계속 읽는다."""
        while not self._stop:
            try:
                raw = self.ser.readline()
            except Exception:
                return   # 케이블이 빠지거나 포트가 닫힌 경우
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if line == "READY":
                log("보드 준비 완료 — 슬롯 4개를 즉시 보냅니다.")
                self.ready_event.set()
            elif line.startswith("SEL|"):
                log(f"보드에서 버튼으로 슬롯 {line[4:]}번을 선택했습니다.")
            elif line.startswith("ERR|"):
                log(f"보드 오류: {line[4:]}")
            else:
                log(f"보드: {line}")

    def send(self, line):
        try:
            self.ser.write((line + "\n").encode("ascii", errors="replace"))
            self.ser.flush()
            return True
        except Exception:
            log("보드로 보내지 못했습니다. USB 케이블이 빠지지 않았는지 확인하세요.")
            return False

    def close(self):
        self._stop = True
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# 본체
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(" NU40 DK 실시간 주식 티커 — 토스증권 오픈 API 연결")
    print("=" * 60)

    config = load_config()
    if config is None:
        return 1

    port = config["port"] or find_port()
    if not port:
        log("보드를 찾지 못했습니다. USB 케이블을 확인하거나 .env의 SERIAL_PORT를 채우세요.")
        return 1

    slots = config["slots"]
    symbols = [s.symbol for s in slots]

    board = BoardLink(port)
    try:
        board.open()
    except TossApiError as e:
        log(f"오류: {e}")
        return 1
    log(f"보드에 연결했습니다: {port} (속도 {BAUD})")

    client = TossClient(config["client_id"], config["client_secret"])

    # 슬롯별로 마지막에 성공한 값을 들고 있는다.
    # 인터넷이 끊겨도 이 값이 남아 있어서, 보드는 마지막 정상값을 계속 보여준다.
    last_good = {}          # symbol -> (price_text, chg_text, alert, chart)
    minute_closes = {}      # symbol -> 1분봉 종가 목록 (차트용)
    base_close = {}         # symbol -> 전일 종가 (등락률 계산 기준)
    currency = {}           # symbol -> 'KRW' or 'USD'

    last_candle_at = 0.0
    last_base_at = 0.0
    backoff = 0.0           # 오류가 나면 잠깐 쉬는 시간

    log("보드의 READY 신호를 기다립니다... (최대 3초)")
    if not board.ready_event.wait(timeout=3.0):
        log("READY가 안 왔지만 그냥 진행합니다. (보드가 이미 켜져 있던 경우)")

    범위 = "오늘 장 전체" if config["chart_range"] == "day" else "최근 64분"
    log(f"차트 범위: {범위}")
    log(f"{config['poll_seconds']:.0f}초마다 시세를 가져옵니다. 멈추려면 Ctrl+C.")
    print()

    try:
        while True:
            now = time.time()

            # 1) 등락률 기준값(전일 종가)을 가끔 갱신한다.
            if now - last_base_at > BASE_REFRESH_SEC:
                for s in slots:
                    try:
                        closes = client.get_candle_closes(s.symbol, "1d", 2)
                        if len(closes) >= 2:
                            base_close[s.symbol] = closes[-2]   # 마지막 바로 앞이 전일 종가
                        elif closes:
                            base_close[s.symbol] = closes[-1]
                    except TossApiError as e:
                        log(f"{s.symbol} 전일 종가 조회 실패: {e}")
                    time.sleep(0.15)   # 호출 제한에 걸리지 않게 살짝 띄운다
                last_base_at = now

            # 2) 차트용 1분봉을 가끔 갱신한다. (1분봉은 1분에 한 번만 바뀐다)
            if now - last_candle_at > CANDLE_REFRESH_SEC:
                for s in slots:
                    try:
                        if config["chart_range"] == "day":
                            closes = client.get_today_closes(s.symbol, s.market)   # 오늘 정규장 전체
                        else:
                            closes = client.get_candle_closes(s.symbol, "1m", CHART_POINTS)  # 최근 64분
                        if closes:
                            minute_closes[s.symbol] = closes
                    except TossApiError as e:
                        log(f"{s.symbol} 차트 조회 실패: {e}")
                    time.sleep(0.15)
                last_candle_at = now

            # 3) 현재가는 매번 가져온다. 4종목을 한 번의 호출로 처리한다.
            try:
                prices = client.get_prices(symbols)
                backoff = 0.0
            except TossApiError as e:
                log(f"{e}  (보드는 마지막 값을 계속 보여줍니다)")
                # 오류가 반복되면 점점 더 오래 쉰다. 최대 60초.
                backoff = min(backoff * 2 if backoff else 2.0, 60.0)
                time.sleep(backoff)
                continue

            # 4) 슬롯마다 보드로 보낼 한 줄을 만든다.
            for s in slots:
                info = prices.get(s.symbol)

                if info is None:
                    # 이번엔 값이 안 왔다. 마지막 정상값이 있으면 그걸 다시 보낸다.
                    if s.symbol in last_good:
                        price_text, chg_text, alert, chart = last_good[s.symbol]
                        board.send(build_line(s.index, s.screen_name, price_text, chg_text, alert, chart))
                    continue

                price = info["price"]
                currency[s.symbol] = info["currency"]

                # 등락률 = (현재가 - 전일종가) / 전일종가 × 100
                base = base_close.get(s.symbol)
                if base:
                    chg_text = f"{(price - base) / base * 100:+.2f}%"
                else:
                    chg_text = "--%"     # 기준값을 아직 못 받은 경우

                # 차트: 받아둔 1분봉 뒤에 지금 가격을 한 점 더 붙인다.
                # 붙인 다음에 64점으로 줄이므로, 맨 오른쪽 점이 곧 지금 가격이 된다.
                # 그래서 하루치 차트여도 오른쪽 끝은 5초마다 실시간으로 움직인다.
                series = list(minute_closes.get(s.symbol) or [])
                series.append(price)
                chart = normalize_chart(downsample(series))

                alert = price < s.threshold
                price_text = format_price(price, info["currency"])

                last_good[s.symbol] = (price_text, chg_text, alert, chart)
                board.send(build_line(s.index, s.screen_name, price_text, chg_text, alert, chart))

            # 5) 사람이 보기 좋게 한 줄 요약
            summary = []
            for s in slots:
                if s.symbol in last_good:
                    p, c, a, _ = last_good[s.symbol]
                    summary.append(f"{s.screen_name} {p} {c}{' [알림]' if a else ''}")
            if summary:
                log("  |  ".join(summary))

            time.sleep(config["poll_seconds"])

    except KeyboardInterrupt:
        print()
        log("사용자가 중지했습니다.")
    except Exception as e:
        # 예상 못 한 문제도 스택트레이스 대신 한국어 한 줄로 바꾼다.
        log(f"예상하지 못한 문제가 생겨 종료합니다: {type(e).__name__}")
    finally:
        board.close()
        log("포트를 닫았습니다.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
