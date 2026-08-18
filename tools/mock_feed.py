#!/usr/bin/env python3
"""
mock_feed.py — 가짜 주가로 보드를 먼저 검증하는 도구

토스 API를 붙이기 전에, 배선과 펌웨어만 따로 확인하려고 만든 프로그램이다.
진짜처럼 움직이는 가짜 주가를 만들어서 보드로 쏜다.
화면에 숫자와 차트가 뜨고, 버튼 4개로 종목이 바뀌고, LED가 켜지면 하드웨어는 정상이다.

쓰는 법 (터미널에서):
    python3 tools/mock_feed.py                       # 포트 자동 찾기
    python3 tools/mock_feed.py --port /dev/cu.usbmodem1101
    python3 tools/mock_feed.py --alert-after 15      # 15초 뒤 일부러 알림(LED) 켜기

멈추려면 Ctrl+C 를 누른다.

※ 이 파일은 일부러 혼자서도 돌아가게 만들었다(bridge.py 를 불러오지 않는다).
   "API가 문제인지 보드가 문제인지"를 확실히 가르기 위해서다.
   필요한 것은 pyserial 하나뿐이다:  pip install pyserial
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
    import serial                      # pyserial: USB 시리얼 통신 라이브러리
    from serial.tools import list_ports
except ImportError:
    print("[오류] pyserial이 설치되어 있지 않습니다.", flush=True)
    print("       터미널에 다음을 입력하세요:  pip3 install pyserial", flush=True)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────
BAUD = 115200          # 시리얼 속도. 펌웨어와 반드시 같아야 한다.
CHART_POINTS = 64      # 차트 점 개수 (규약상 고정)
CHART_MAX = 36         # 차트 값의 최대치 (규약상 0~36 정수)

# 가짜 종목 4개. host/.env 가 있으면 거기 설정을 그대로 따라가고,
# 없으면 아래 기본값을 쓴다. (설정을 두 군데 고치지 않아도 되게 하려는 것)
#   형식: (표시이름, 시작가, 임계가, 소수점자리, 콤마표시)
DEFAULT_SLOTS = [
    ("Samsung", 270000.0, 268300.0, 0, True),
    ("SKhynix", 1679000.0, 1668200.0, 0, True),
    ("NAVER", 218000.0, 215300.0, 0, True),
    ("Kakao", 38250.0, 37900.0, 0, True),
]

HANGUL = re.compile(r"[가-힣]")


def load_slots_from_env():
    """host/.env 의 SLOT0~SLOT3 을 읽어 가짜 종목 목록을 만든다.

    .env 가 없거나 형식이 다르면 None 을 돌려주고 기본값을 쓰게 한다.
    (bridge.py 와 달리 라이브러리 없이 직접 읽는다. 이 도구는 pyserial 하나면 돌아가야 하니까.)
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

            # 화면 이름 규칙은 펌웨어·bridge.py 와 같다: 한글이면 종목코드를 쓴다.
            name = (symbol if HANGUL.search(display) else display)[:10]

            # 국내 종목(6자리 숫자)은 원화, 나머지는 달러로 본다.
            국내 = symbol.isdigit() and len(symbol) == 6
            start = threshold * 1.01          # 임계가보다 살짝 위에서 시작한다
            slots.append((name, start, threshold, 0 if 국내 else 2, 국내))

        if len(slots) == 4:
            print(f"[안내] {candidate} 의 종목 설정을 사용합니다.", flush=True)
            return slots
    return None


MOCK_SLOTS = load_slots_from_env() or DEFAULT_SLOTS


# ─────────────────────────────────────────────────────────────
# 포트 찾기
# ─────────────────────────────────────────────────────────────
def find_port():
    """연결된 NU40 DK 보드의 시리얼 포트를 찾는다. 못 찾으면 None."""
    candidates = []
    for p in list_ports.comports():
        # nRF52840 보드는 제조사 ID가 0x239A(Adafruit 계열 부트로더)로 잡힌다.
        if p.vid == 0x239A:
            candidates.append(p.device)
        elif "usbmodem" in p.device or "USB" in (p.description or ""):
            candidates.append(p.device)

    # 맥에서는 /dev/cu.* 를 쓰는 편이 안정적이다.
    for c in candidates:
        if c.startswith("/dev/cu."):
            return c
    return candidates[0] if candidates else None


# ─────────────────────────────────────────────────────────────
# 규약대로 한 줄 만들기
#   D|<slot>|<name>|<price>|<chg>|<alert>|<v0>,...,<v63>
# ─────────────────────────────────────────────────────────────
def format_price(value, decimals, use_comma):
    """가격 숫자를 화면용 글자로 바꾼다. 예) 68900.0 → '68,900'"""
    if use_comma:
        return f"{value:,.{decimals}f}"
    return f"{value:.{decimals}f}"


def normalize_chart(history):
    """가격 목록을 0~36 정수 64개로 바꾼다. 화면 높이에 맞추는 작업이다."""
    # 개수가 모자라면 앞쪽을 첫 값으로 채워서 항상 64개를 만든다.
    values = list(history)
    if len(values) < CHART_POINTS:
        values = [values[0]] * (CHART_POINTS - len(values)) + values
    values = values[-CHART_POINTS:]

    low, high = min(values), max(values)
    if high - low < 1e-9:
        # 값이 전부 같으면 가운데 높이로 평평하게 그린다.
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
# 보드가 보내는 줄 읽기 (READY / SEL / ERR)
# ─────────────────────────────────────────────────────────────
class BoardReader(threading.Thread):
    """보드가 보내는 글자를 계속 읽어서 화면에 보여주는 담당."""

    def __init__(self, ser):
        super().__init__(daemon=True)
        self.ser = ser
        self.ready_event = threading.Event()   # 보드가 READY를 보내면 켜지는 깃발
        self.stop_flag = False

    def run(self):
        while not self.stop_flag:
            try:
                raw = self.ser.readline()
            except Exception:
                # 케이블이 빠졌거나 포트가 닫힌 경우. 조용히 끝낸다.
                return
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if line == "READY":
                print("[보드] 준비 완료 — 슬롯 4개를 즉시 보냅니다.", flush=True)
                self.ready_event.set()
            elif line.startswith("SEL|"):
                print(f"[보드] 버튼으로 슬롯 {line[4:]}번 선택", flush=True)
            elif line.startswith("ERR|"):
                print(f"[보드 오류] {line[4:]}", flush=True)
            else:
                print(f"[보드] {line}", flush=True)


# ─────────────────────────────────────────────────────────────
# 본체
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="가짜 주가로 NU40 DK 보드를 검증합니다."
    )
    parser.add_argument("--port", help="시리얼 포트 (예: /dev/cu.usbmodem1101 또는 COM3)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="몇 초마다 새 값을 보낼지 (기본 1초)")
    parser.add_argument("--alert-after", type=float, default=0,
                        help="몇 초 뒤에 일부러 임계가를 깨서 LED를 켤지 (0이면 안 함)")
    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        print("[오류] 보드를 찾지 못했습니다.", flush=True)
        print("       USB 케이블이 꽂혀 있는지 확인하고, 그래도 안 되면", flush=True)
        print("       --port 로 직접 지정하세요. 예: --port /dev/cu.usbmodem1101", flush=True)
        return 1

    print(f"[안내] 포트 {port} (속도 {BAUD}) 로 연결합니다.", flush=True)

    try:
        ser = serial.Serial(port, BAUD, timeout=1)
    except Exception as e:
        print(f"[오류] 포트를 열지 못했습니다: {port}", flush=True)
        print(f"       원인: {type(e).__name__}", flush=True)
        print("       아두이노 IDE의 시리얼 모니터가 켜져 있으면 닫고 다시 시도하세요.", flush=True)
        print("       (포트는 한 번에 한 프로그램만 쓸 수 있습니다.)", flush=True)
        return 1

    reader = BoardReader(ser)
    reader.start()

    # 보드가 켜지자마자 READY를 보낸다. 최대 3초 기다려보고, 안 와도 그냥 진행한다.
    # (이미 켜져 있던 보드는 READY를 다시 보내지 않기 때문이다.)
    print("[안내] 보드의 READY 신호를 기다립니다... (최대 3초)", flush=True)
    if not reader.ready_event.wait(timeout=3.0):
        print("[안내] READY가 안 왔지만 그냥 보냅니다. (보드가 이미 켜져 있던 경우)", flush=True)

    # 각 슬롯의 가격 기록을 만든다. 처음엔 시작가로 64개를 채운다.
    histories = [[start] * CHART_POINTS for (_, start, _, _, _) in MOCK_SLOTS]
    base_prices = [start for (_, start, _, _, _) in MOCK_SLOTS]

    started_at = time.time()
    tick = 0

    print("[안내] 가짜 주가 전송을 시작합니다. 멈추려면 Ctrl+C.", flush=True)
    print(flush=True)

    try:
        while True:
            elapsed = time.time() - started_at
            lines = []

            for slot, (name, start, threshold, decimals, use_comma) in enumerate(MOCK_SLOTS):
                last = histories[slot][-1]

                # 진짜처럼 보이게: 완만한 물결 + 약간의 무작위 흔들림
                wave = math.sin((elapsed / 12.0) + slot) * (start * 0.004)
                noise = random.uniform(-1, 1) * (start * 0.0015)
                price = last + wave * 0.15 + noise

                # --alert-after 를 주면 그 시각 이후 슬롯 0,2의 가격을 임계가 밑으로 끌어내린다.
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
                ser.write((line + "\n").encode("ascii", errors="replace"))
            ser.flush()

            tick += 1
            if tick % 5 == 1:   # 5번에 한 번만 요약해서 보여준다(화면이 지저분해지지 않게)
                summary = "  ".join(
                    f"{n}={p:,.2f}{'(!)' if a else ''}" for n, p, _, a, _ in lines
                )
                print(f"[전송 {tick:>4}회] {summary}", flush=True)

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(flush=True)
        print("[안내] 사용자가 중지했습니다.", flush=True)
    except Exception as e:
        print(f"[오류] 전송 중 문제가 생겼습니다: {type(e).__name__}", flush=True)
        print("       케이블이 빠졌거나 포트가 닫혔을 수 있습니다.", flush=True)
    finally:
        reader.stop_flag = True
        try:
            ser.close()
        except Exception:
            pass
        print("[안내] 포트를 닫았습니다.", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
