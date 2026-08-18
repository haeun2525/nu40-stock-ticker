#!/usr/bin/env python3
"""
range_compare.py — 차트 범위에 따라 '5초 동안 얼마나 움직이는지' 비교

같은 하루치 데이터를 두 가지 방식으로 그려서, 5초 뒤 화면과 나란히 비교합니다.
  A. 오늘 장 전체 (390분 → 64점)   CHART_RANGE=day
  B. 최근 64분     (64분 → 64점)   CHART_RANGE=hour
"""
import math, importlib.util, subprocess
from pathlib import Path

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("prev", here / "oled_preview.py")
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

spec2 = importlib.util.spec_from_file_location("bridge", here.parent.parent / "host/bridge.py")
B = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(B)

# 하루치 1분봉 390개를 그럴듯하게 만든다 (09:00~15:30)
base = 69000.0
day = []
v = base
for i in range(390):
    t = i / 389
    v += math.sin(i / 23.0) * 90 + math.sin(i / 7.0) * 35 - 26
    day.append(v)

def frame(series, live, label):
    s = list(series) + [live]
    chart = B.normalize_chart(B.downsample(s))
    chg = (live - base) / base * 100
    return P.draw_screen("삼성전자", f"{live:,.0f}", f"{chg:+.2f}%", chart, 0), chart

live1 = day[-1]
live2 = day[-1] - 120      # 5초 뒤 120원 하락했다고 가정

out = []
for tag, series in (("A_하루전체", day), ("B_최근64분", day[-64:])):
    for n, live in (("1_지금", live1), ("2_5초뒤", live2)):
        c, chart = frame(series, live, tag)
        f = here / f"{tag}_{n}.png"
        P.save_png(c, f); out.append(f)
        print(f"{tag} {n}: 오른쪽 끝 3점 = {chart[-3:]}")

subprocess.run(["open"] + [str(p) for p in out], check=False)
print("\n비교 창을 열었습니다.")
