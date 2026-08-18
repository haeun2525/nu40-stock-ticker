#!/usr/bin/env python3
"""
hangul_layouts.py — 한글 종목명을 넣었을 때의 세로 배치 후보들

한글 이름과 큰 숫자가 딱 붙어 보이는 문제를 푸는 방법이 몇 가지 있다.
이름을 작게 하거나, 숫자를 작게 하거나, 차트를 아래로 미는 것이다.
넷 다 실물 크기로 그려서 비교한다.
"""
import importlib.util, subprocess, math
from pathlib import Path

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("prev", here / "oled_preview.py")
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
spec2 = importlib.util.spec_from_file_location("hp", here / "hangul_preview.py")
H = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(H)

W, HGT = 128, 64
INDICATOR_Y = 58


def make_chart(shape="down"):
    out = []
    for i in range(64):
        t = i / 63
        v = 30 - 22 * t + 4 * math.sin(t * 11) if shape == "down" else 18 + 12 * math.sin(t * 7)
        out.append(max(0, min(36, int(round(v)))))
    return out


def draw(name, price, chg, chart, slot, *, name_px, price_mode, price_y, chart_top):
    """한 화면을 그린다.
       name_px    : 한글 이름 높이 (9 또는 11)
       price_mode : 'gfx2'(기존 큰 숫자 16px) 또는 'galmuri14'(갈무리 14px)
       price_y    : 현재가 윗줄 위치
       chart_top  : 차트 영역 맨 위
    """
    c = P.Canvas()

    # 종목명 (한글). 맨 위에 딱 붙으면 답답해 보여서 1픽셀 띄운다.
    TOP = 1
    H.draw_hangul(c, 0, TOP, name, name_px)

    # 등락률 — 오른쪽 정렬, 이름과 같은 줄. 숫자는 기존 글꼴이 더 또렷하다.
    c.text(W - len(chg) * 6, TOP + max(0, (name_px - 7) // 2), chg, 1)

    # 현재가
    if price_mode == "gfx2":
        c.text(0, price_y, price, 2)
    else:
        H.draw_hangul(c, 0, price_y, price, 14)

    # 차트 — 아래쪽 끝은 인디케이터 바로 위(57)로 고정
    bottom = 57
    height = bottom - chart_top
    prev = (0, bottom - chart[0] * height // 36)
    for i in range(1, 64):
        cur = (i * 2, bottom - chart[i] * height // 36)
        c.line(prev[0], prev[1], cur[0], cur[1]); prev = cur

    # 슬롯 인디케이터
    for i in range(4):
        c.rect(i * 10, INDICATOR_Y, 6, 5, fill=(i == slot))
    return c


# price_y = 현재가 윗줄, chart_top = 차트 영역 맨 위. 차트 아래끝은 항상 57.
LAYOUTS = {
    "L1_이름9_숫자그대로": dict(name_px=9,  price_mode="gfx2",      price_y=13, chart_top=31),
    "L2_이름11_차트내림":  dict(name_px=11, price_mode="gfx2",      price_y=15, chart_top=33),
    "L3_이름9_숫자작게":   dict(name_px=9,  price_mode="galmuri14", price_y=13, chart_top=29),
    "L4_이름11_숫자작게":  dict(name_px=11, price_mode="galmuri14", price_y=15, chart_top=31),
}

if __name__ == "__main__":
    print("갈무리 14px 숫자 가로폭 (128픽셀 중):")
    for t in ("269,500", "1,669,000", "38,150"):
        print(f"   {t:<12} {H.render_hangul(t, 14)[1]:>3}px")
    print()

    made = []
    for tag, cfg in LAYOUTS.items():
        for n, (name, price, chg, shape, slot) in enumerate([
            ("삼성전자", "269,500", "-1.82%", "down", 0),
            ("SK하이닉스", "1,669,000", "+0.12%", "flat", 1),
        ], 1):
            c = draw(name, price, chg, make_chart(shape), slot, **cfg)
            f = here / f"{tag}_{n}.png"
            P.save_png(c, f); made.append(f)
        print("만들었습니다:", tag)
    subprocess.run(["open"] + [str(p) for p in made], check=False)
