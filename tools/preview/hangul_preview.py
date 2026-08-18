#!/usr/bin/env python3
"""
hangul_preview.py — OLED에 한글을 띄우면 어떻게 보이는지 미리보기

기본 글꼴(Adafruit GFX)에는 한글이 없다. 그래서 한글을 띄우려면
'쓸 글자만 골라 비트맵으로 만들어 펌웨어에 심는' 방법을 써야 한다.
이 도구는 그렇게 했을 때 실제로 어떻게 보이는지를 실물 크기로 그려준다.

한글은 갈무리(Galmuri) 픽셀 폰트로 그린다. 작은 화면용으로 만들어진 폰트라
7/9/11/14 픽셀 크기에서 흐려지지 않고 또렷하게 찍힌다.
숫자·차트·인디케이터는 기존 미리보기와 똑같이 GFX 방식 그대로 그린다.
"""
import importlib.util, subprocess, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("prev", here / "oled_preview.py")
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

# 갈무리 비트맵 폰트는 정해진 크기에서만 열린다.
# 아래 숫자는 (글자 실제 높이 → 폰트파일, 열어야 하는 크기) 짝이다.
_FD = Path.home() / "Library/Fonts"
FONTS = {
    7:  (_FD / "Galmuri7Bitmap-Regular-2.40.3.ttf", 10),
    9:  (_FD / "Galmuri9Bitmap-Regular-2.40.3.ttf", 12),
    11: (_FD / "Galmuri11Bitmap-Regular-2.40.3.ttf", 16),
    14: (_FD / "Galmuri14Bitmap-Regular-2.40.3.ttf", 21),
}


def load_font(px):
    path, em = FONTS[px]
    return ImageFont.truetype(str(path), em)


def render_hangul(text, px):
    """글자를 찍어서 '실제로 켜진 픽셀'만 잘라낸 비트맵을 돌려준다.

    폰트는 글자 위아래로 여백을 두고 그리기 때문에, 그대로 쓰면 위치가 어긋난다.
    켜진 픽셀의 경계를 직접 재서 그만큼만 잘라낸다. 그래야 원하는 자리에 정확히 앉는다.
    """
    font = load_font(px)
    pad = 40
    img = Image.new("1", (pad * 6, pad * 3), 0)
    ImageDraw.Draw(img).text((pad, pad), text, font=font, fill=1)
    box = img.getbbox()                     # 켜진 픽셀만 감싸는 사각형
    if box is None:
        return [], 0, 0
    img = img.crop(box)
    w, h = img.size
    pix = img.load()
    dots = [(xx, yy) for yy in range(h) for xx in range(w) if pix[xx, yy]]
    return dots, w, h


def draw_hangul(canvas, x, y, text, px):
    """render_hangul 로 만든 비트맵을 캔버스의 (x, y) 에 왼쪽 위 기준으로 찍는다.
    안티에일리어싱(흐릿한 경계)은 쓰지 않는다. OLED는 켜짐/꺼짐 두 값뿐이다."""
    dots, w, h = render_hangul(text, px)
    for dx, dy in dots:
        canvas.pixel(x + dx, y + dy)
    return w, h


def text_width(text, px):
    return render_hangul(text, px)[1]


def make_chart(shape="down"):
    out = []
    for i in range(64):
        t = i / 63
        v = 30 - 22 * t + 4 * math.sin(t * 11) if shape == "down" else 18 + 12 * math.sin(t * 7)
        out.append(max(0, min(36, int(round(v)))))
    return out


def build(name_kr, price, chg, chart, slot, name_px, price_kr=False):
    """한글 종목명 + 숫자로 한 화면을 구성한다."""
    c = P.Canvas()

    # 종목명(한글) — 왼쪽. 헤더 줄(y 0~11) 안에서 세로 가운데에 맞춘다.
    kh = render_hangul(name_kr, name_px)[2]
    top = max(0, (12 - kh) // 2)
    draw_hangul(c, 0, top, name_kr, name_px)

    # 등락률 — 오른쪽 정렬. 숫자는 기존 GFX 글꼴 그대로.
    c.text(P.W - len(chg) * 6, P.HEADER_Y, chg, 1)

    # 현재가 — 큰 글씨
    if price_kr:
        draw_hangul(c, 0, P.PRICE_Y + 1, price, 14)
    else:
        c.text(0, P.PRICE_Y, price, 2)

    # 차트
    prev = (0, P.chart_value_to_y(chart[0]))
    for i in range(1, 64):
        cur = (i * 2, P.chart_value_to_y(chart[i]))
        c.line(prev[0], prev[1], cur[0], cur[1]); prev = cur

    # 슬롯 인디케이터
    for i in range(4):
        c.rect(i * 10, P.INDICATOR_Y, 6, 5, fill=(i == slot))
    return c


if __name__ == "__main__":
    chart = make_chart("down")
    cases = [
        ("H1_한글11px.png",  dict(name_kr="삼성전자", price="269,500", chg="-1.82%",
                                chart=chart, slot=0, name_px=11)),
        ("H2_한글9px.png",   dict(name_kr="삼성전자", price="269,500", chg="-1.82%",
                                chart=chart, slot=0, name_px=9)),
        ("H3_긴이름11px.png", dict(name_kr="SK하이닉스", price="1,669,000", chg="+0.12%",
                                chart=make_chart("flat"), slot=1, name_px=11)),
        ("H4_긴이름9px.png",  dict(name_kr="SK하이닉스", price="1,669,000", chg="+0.12%",
                                chart=make_chart("flat"), slot=1, name_px=9)),
        ("H5_카카오11px.png", dict(name_kr="카카오", price="38,150", chg="-4.62%",
                                chart=chart, slot=3, name_px=11)),
    ]
    made = []
    print("한글 이름 가로폭 (128픽셀 중):")
    for n in ("삼성전자", "SK하이닉스", "네이버", "카카오"):
        print(f"   {n:<8} 11px → {text_width(n,11):>3}px   9px → {text_width(n,9):>3}px")
    print()
    for filename, kw in cases:
        P.save_png(build(**kw), here / filename)
        made.append(here / filename)
        print("만들었습니다:", filename)
    subprocess.run(["open"] + [str(p) for p in made], check=False)
