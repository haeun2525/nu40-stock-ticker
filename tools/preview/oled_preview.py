#!/usr/bin/env python3
"""
oled_preview.py — 보드에 굽기 전에 OLED 화면을 PC에서 미리 보는 도구

펌웨어의 drawScreen() 을 파이썬으로 똑같이 흉내 냅니다.
글꼴도 실제로 쓰는 것을 그대로 읽어옵니다.
  - 영문·숫자 : 아두이노에 설치된 Adafruit GFX 기본 글꼴 (glcdfont.c)
  - 한글       : 갈무리9 픽셀 글꼴 (펌웨어에 심은 것과 같은 글꼴)
그래서 여기 보이는 그림이 실제 화면과 픽셀 단위로 같습니다.

실행:  python3 tools/preview/oled_preview.py
결과:  tools/preview/*.png  (자동으로 열립니다)
"""

import math
import re
import struct
import subprocess
import zlib
from pathlib import Path

# ── 펌웨어와 똑같은 상수 (L1 배치) ────────────────────────
W, H = 128, 64
HEADER_Y = 1        # 종목명(한글 9픽셀) 윗줄
CHG_Y = 2           # 등락률 윗줄. 영문 글꼴이 2픽셀 작아서 맞춰 내린다.
PRICE_Y = 13        # 현재가(큰 글씨) 윗줄
CHART_TOP = 31
CHART_BOTTOM = 57
CHART_HEIGHT = CHART_BOTTOM - CHART_TOP     # 26
INDICATOR_Y = 58
CHART_POINTS = 64
CHART_MAX = 36

HANGUL_ADVANCE = 10     # 한글 글자 간격
ASCII_ADVANCE = 6       # 영문·숫자 글자 간격

GFX_FONT_PATHS = [
    Path.home() / "Documents/Arduino/libraries/Adafruit_GFX_Library/glcdfont.c",
    Path.home() / "Library/Arduino15/libraries/TFT/src/utility/glcdfont.c",
]
GALMURI_PATH = Path.home() / "Library/Fonts/Galmuri9Bitmap-Regular-2.40.3.ttf"
GALMURI_EM = 12         # 이 글꼴은 크기 12에서만 열린다. 실제 글자 높이는 9픽셀.


def load_gfx_font():
    """아두이노에 설치된 Adafruit GFX 기본 글꼴(5x7)을 읽는다.
    256글자 × 5바이트, 세로 한 줄이 1바이트(맨 아래 비트가 맨 위 픽셀)."""
    for p in GFX_FONT_PATHS:
        if p.exists():
            text = p.read_text(errors="replace")
            body = text[text.index("{"): text.rindex("}")]
            data = [int(x, 16) for x in re.findall(r"0[xX]([0-9a-fA-F]{2})", body)]
            if len(data) >= 256 * 5:
                return data[: 256 * 5]
    raise SystemExit("[오류] Adafruit GFX 글꼴 파일(glcdfont.c)을 찾지 못했습니다.")


FONT = load_gfx_font()
_hangul_cache = {}


def hangul_dots(ch):
    """한글 한 글자를 '켜진 픽셀 좌표' 목록으로 바꾼다. (9x9 안쪽)"""
    if ch in _hangul_cache:
        return _hangul_cache[ch]
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(str(GALMURI_PATH), GALMURI_EM)
    img = Image.new("1", (48, 48), 0)
    ImageDraw.Draw(img).text((12, 12), ch, font=font, fill=1)
    box = img.getbbox()
    dots = []
    if box:
        crop = img.crop(box)
        pix = crop.load()
        w, h = crop.size
        dots = [(x, y) for y in range(min(h, 9)) for x in range(min(w, 9)) if pix[x, y]]
    _hangul_cache[ch] = dots
    return dots


def is_hangul(ch):
    return "가" <= ch <= "힣"


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
        """영문·숫자만. 기본 글꼴로 그린다."""
        for i, ch in enumerate(s):
            self.draw_char(x + i * ASCII_ADVANCE * size, y, ch, size)

    def mixed_text(self, x, y, s):
        """한글과 영문이 섞인 글. 펌웨어의 drawMixedText() 와 같은 규칙."""
        for ch in s:
            if is_hangul(ch):
                for dx, dy in hangul_dots(ch):
                    self.pixel(x + dx, y + dy)
                x += HANGUL_ADVANCE
            else:
                self.draw_char(x, y + 1, ch, 1)    # 영문은 1픽셀 내려 눈높이를 맞춘다
                x += ASCII_ADVANCE
        return x

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


def measure(text):
    """그렸을 때 가로로 몇 픽셀을 차지하는지. 오른쪽 정렬에 쓴다."""
    return sum(HANGUL_ADVANCE if is_hangul(c) else ASCII_ADVANCE for c in text)


# ── 펌웨어의 drawScreen() 을 그대로 옮긴 부분 ─────────────
def chart_value_to_y(v):
    v = min(v, CHART_MAX)
    return CHART_BOTTOM - (v * CHART_HEIGHT) // CHART_MAX


def draw_screen(name, price, chg, chart, current_slot, has_data=True):
    c = Canvas()

    if not has_data:
        c.mixed_text(0, HEADER_Y, "WAITING")   # 펌웨어 drawScreen()과 동일
        c.text(0, PRICE_Y + 4, f"SLOT {current_slot + 1}", 1)
    else:
        c.mixed_text(0, HEADER_Y, name)                    # 종목명 (왼쪽, 한글 가능)
        c.text(max(0, W - len(chg) * 6), CHG_Y, chg, 1)    # 등락률 (오른쪽 정렬)
        c.text(0, PRICE_Y, price, 2)                       # 현재가 (큰 글씨)

        prev = (0, chart_value_to_y(chart[0]))             # 차트: 점을 선으로 연결
        for i in range(1, CHART_POINTS):
            cur = (i * 2, chart_value_to_y(chart[i]))
            c.line(prev[0], prev[1], cur[0], cur[1])
            prev = cur

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
            row += bytes(on if canvas.px[y][x] else off) * scale
        for _ in range(scale):        # 세로로도 확대
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
        ("1_apple.png",    dict(name="Apple", price="182.40", chg="+0.85%",
                                chart=make_chart("up"), current_slot=0)),
        ("2_nvidia.png",   dict(name="Nvidia", price="173.50", chg="-0.66%",
                                chart=make_chart("flat"), current_slot=1)),
        ("3_waiting.png",  dict(name="", price="", chg="", chart=make_chart("flat"),
                                current_slot=0, has_data=False)),
        ("4_long_name.png", dict(name="삼성전자", price="268,500", chg="-2.19%",
                                chart=make_chart("down"), current_slot=3)),  # 한글 종목도 렌더됨
    ]
    made = []
    for filename, kwargs in cases:
        save_png(draw_screen(**kwargs), outdir / filename)
        made.append(outdir / filename)
        name = kwargs.get("name") or "(waiting)"
        print(f"만들었습니다: {filename}   이름 폭 {measure(name)}px + 등락률 {len(kwargs['chg'])*6}px")

    subprocess.run(["open"] + [str(p) for p in made], check=False)
    print("\n미리보기 창을 열었습니다. (실제 화면과 픽셀 단위로 동일합니다)")
