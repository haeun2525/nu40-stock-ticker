#!/usr/bin/env python3
"""
oled_preview.py — 보드에 굽기 전에 OLED 화면을 PC에서 미리 보는 도구

펌웨어의 drawScreen() 을 파이썬으로 똑같이 흉내 냅니다.
글꼴도 아두이노에 실제로 설치된 Adafruit GFX 기본 글꼴(glcdfont.c)을 그대로 읽어 씁니다.
그래서 여기 보이는 그림이 실제 화면과 픽셀 단위로 같습니다.

실행:  python3 tools/preview/oled_preview.py
결과:  tools/preview/*.png  (자동으로 열립니다)
"""

import re
import zlib
import struct
import subprocess
from pathlib import Path

# ── 펌웨어와 똑같은 상수 ──────────────────────────────────
W, H = 128, 64
HEADER_Y = 2
PRICE_Y = 12
CHART_TOP = 28
CHART_BOTTOM = 57
CHART_HEIGHT = CHART_BOTTOM - CHART_TOP     # 30
INDICATOR_Y = 58
CHART_POINTS = 64
CHART_MAX = 36

FONT_PATHS = [
    Path.home() / "Documents/Arduino/libraries/Adafruit_GFX_Library/glcdfont.c",
    Path.home() / "Library/Arduino15/libraries/TFT/src/utility/glcdfont.c",
]


def load_font():
    """아두이노에 설치된 Adafruit GFX 기본 글꼴(5x7)을 읽는다.
    256글자 × 5바이트, 세로 한 줄이 1바이트(맨 아래 비트가 맨 위 픽셀)."""
    for p in FONT_PATHS:
        if p.exists():
            text = p.read_text(errors="replace")
            body = text[text.index("{"): text.rindex("}")]
            data = [int(x, 16) for x in re.findall(r"0[xX]([0-9a-fA-F]{2})", body)]
            if len(data) >= 256 * 5:
                return data[: 256 * 5]
    raise SystemExit("[오류] Adafruit GFX 글꼴 파일(glcdfont.c)을 찾지 못했습니다.")


FONT = load_font()


# ── 아주 작은 그리기 엔진 (Adafruit_GFX 와 동일한 규칙) ──
class Canvas:
    def __init__(self):
        self.px = [[0] * W for _ in range(H)]

    def pixel(self, x, y, on=1):
        if 0 <= x < W and 0 <= y < H:
            self.px[y][x] = on

    def draw_char(self, x, y, ch, size):
        code = ord(ch)
        if code > 255:
            code = ord("?")
        for col in range(5):                       # 글자 한 칸은 가로 5픽셀
            bits = FONT[code * 5 + col]
            for row in range(8):
                if bits & (1 << row):
                    for dx in range(size):         # size 2면 한 픽셀을 2x2로 키운다
                        for dy in range(size):
                            self.pixel(x + col * size + dx, y + row * size + dy)

    def text(self, x, y, s, size=1):
        for i, ch in enumerate(s):
            self.draw_char(x + i * 6 * size, y, ch, size)   # 글자 간격은 6픽셀

    def line(self, x0, y0, x1, y1):
        """Adafruit_GFX 의 drawLine 과 같은 브레젠험 직선."""
        steep = abs(y1 - y0) > abs(x1 - x0)
        if steep:
            x0, y0, x1, y1 = y0, x0, y1, x1
        if x0 > x1:
            x0, x1, y0, y1 = x1, x0, y1, y0
        dx, dy = x1 - x0, abs(y1 - y0)
        err = dx // 2
        ystep = 1 if y0 < y1 else -1
        y = y0
        for x in range(x0, x1 + 1):
            self.pixel(y, x) if steep else self.pixel(x, y)
            err -= dy
            if err < 0:
                y += ystep
                err += dx

    def rect(self, x, y, w, h, fill=False):
        if fill:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.pixel(xx, yy)
        else:
            for xx in range(x, x + w):
                self.pixel(xx, y); self.pixel(xx, y + h - 1)
            for yy in range(y, y + h):
                self.pixel(x, yy); self.pixel(x + w - 1, yy)


# ── 펌웨어의 drawScreen() 을 그대로 옮긴 부분 ─────────────
def chart_value_to_y(v):
    v = min(v, CHART_MAX)
    return CHART_BOTTOM - (v * CHART_HEIGHT) // CHART_MAX


def draw_screen(name, price, chg, chart, current_slot, has_data=True):
    c = Canvas()

    if not has_data:
        c.text(0, HEADER_Y, f"SLOT {current_slot + 1}", 1)
        c.text(0, PRICE_Y, "WAITING", 2)
        c.text(0, CHART_TOP + 8, "PC data...", 1)
    else:
        c.text(0, HEADER_Y, name, 1)                       # 종목명 (왼쪽)
        chg_x = max(0, W - len(chg) * 6)                   # 등락률 (오른쪽 정렬)
        c.text(chg_x, HEADER_Y, chg, 1)
        c.text(0, PRICE_Y, price, 2)                       # 현재가 (큰 글씨)

        prev_x, prev_y = 0, chart_value_to_y(chart[0])     # 차트: 점을 선으로 연결
        for i in range(1, CHART_POINTS):
            x, y = i * 2, chart_value_to_y(chart[i])
            c.line(prev_x, prev_y, x, y)
            prev_x, prev_y = x, y

    for i in range(4):                                      # 슬롯 인디케이터
        c.rect(i * 10, INDICATOR_Y, 6, 5, fill=(i == current_slot))
    return c


# ── PNG 저장 (외부 라이브러리 없이) ───────────────────────
def save_png(canvas, path, scale=6):
    """켜진 픽셀은 흰색, 꺼진 픽셀은 진한 남색으로 그린다(실제 OLED 느낌)."""
    on, off = (235, 245, 255), (8, 12, 24)
    rows = []
    for y in range(H):
        row = bytearray([0])          # PNG 각 줄 앞에 붙는 필터 바이트
        for x in range(W):
            color = on if canvas.px[y][x] else off
            row += bytes(color) * scale
        rows.append(bytes(row) * 1)
        for _ in range(scale - 1):    # 세로로도 확대
            rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def make_chart(shape):
    """미리보기용 차트 값 64개를 만든다 (0~36)."""
    import math
    out = []
    for i in range(CHART_POINTS):
        t = i / (CHART_POINTS - 1)
        if shape == "down":
            v = 30 - 24 * t + 4 * math.sin(t * 11)
        elif shape == "up":
            v = 6 + 26 * t + 4 * math.sin(t * 9)
        else:
            v = 18 + 12 * math.sin(t * 7)
        out.append(max(0, min(CHART_MAX, int(round(v)))))
    return out


if __name__ == "__main__":
    outdir = Path(__file__).resolve().parent
    cases = [
        ("1_국내종목.png",   dict(name="005930", price="68,900", chg="-2.96%",
                                chart=make_chart("down"), current_slot=0)),
        ("2_미국종목.png",   dict(name="TSLA", price="398.20", chg="+1.24%",
                                chart=make_chart("up"), current_slot=2)),
        ("3_대기중.png",     dict(name="", price="", chg="", chart=make_chart("flat"),
                                current_slot=0, has_data=False)),
        ("4_긴가격.png",     dict(name="000660", price="170,500", chg="+12.50%",
                                chart=make_chart("flat"), current_slot=1)),
    ]
    made = []
    for filename, kwargs in cases:
        save_png(draw_screen(**kwargs), outdir / filename)
        made.append(outdir / filename)
        print(f"만들었습니다: {filename}")

    subprocess.run(["open"] + [str(p) for p in made], check=False)
    print("\n미리보기 창을 열었습니다. (실제 화면과 픽셀 단위로 동일합니다)")
