#!/usr/bin/env python3
"""
make_hangul_font.py — 펌웨어에 심을 한글 글꼴 데이터를 만든다

아두이노 기본 글꼴(Adafruit GFX)에는 한글이 없다. 그래서 한글을 화면에 띄우려면
글자 모양을 직접 비트맵으로 만들어 펌웨어에 넣어야 한다. 이 도구가 그 일을 한다.

글꼴은 갈무리9(Galmuri9)를 쓴다. 작은 화면용 픽셀 글꼴이라 9픽셀에서도 또렷하다.
조사해보니 한글 글자가 전부 9x9 픽셀, 글자 간격 10픽셀로 균일해서 저장이 간단하다.

실행:
    python3 tools/make_hangul_font.py            # KS X 1001 완성형 2350자 (기본)
    python3 tools/make_hangul_font.py --full     # 현대 한글 11172자 전부

결과:  firmware/nu40_ticker/hangul_font.h
"""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path.home() / "Library/Fonts/Galmuri9Bitmap-Regular-2.40.3.ttf"
FONT_EM = 12          # 이 글꼴은 12에서만 열린다. 실제 글자 높이는 9픽셀.
GLYPH_W, GLYPH_H = 9, 9
ADVANCE = 10
ROW_BYTES = 2         # 가로 9픽셀 → 한 줄에 2바이트


def ks_x_1001_syllables():
    """KS X 1001 완성형 2350자. EUC-KR로 인코딩했을 때 두 바이트가 모두
    0xA1~0xFE 범위에 드는 글자만 고른다. (그 밖은 CP949 확장 영역이다)"""
    out = []
    for cp in range(0xAC00, 0xD7A4):
        ch = chr(cp)
        try:
            b = ch.encode("euc_kr")
        except UnicodeEncodeError:
            continue
        if len(b) == 2 and 0xA1 <= b[0] <= 0xFE and 0xA1 <= b[1] <= 0xFE:
            out.append(cp)
    return out


def all_syllables():
    """현대 한글 음절 전체 (U+AC00 ~ U+D7A3)."""
    return list(range(0xAC00, 0xD7A4))


def render_glyph(draw_img, font, ch):
    """글자 하나를 9x9 비트맵으로 만든다. 켜진 픽셀은 1, 꺼진 픽셀은 0."""
    pad = 12
    img = Image.new("1", (pad * 4, pad * 4), 0)
    ImageDraw.Draw(img).text((pad, pad), ch, font=font, fill=1)
    box = img.getbbox()
    if box is None:
        return [[0] * GLYPH_W for _ in range(GLYPH_H)]

    crop = img.crop(box)
    w, h = crop.size
    pix = crop.load()

    # 글자마다 잉크 크기가 조금씩 달라서(8x9 등) 9x9 칸의 왼쪽 위에 맞춘다.
    rows = []
    for y in range(GLYPH_H):
        row = []
        for x in range(GLYPH_W):
            row.append(1 if (x < w and y < h and pix[x, y]) else 0)
        rows.append(row)
    return rows


def pack(rows):
    """9x9 비트맵을 18바이트로 압축한다.
    한 줄(9픽셀)당 2바이트: 앞 바이트에 왼쪽 8칸, 뒷 바이트의 최상위 비트에 9번째 칸."""
    out = []
    for row in rows:
        b0 = 0
        for x in range(8):
            if row[x]:
                b0 |= 1 << (7 - x)
        b1 = 0x80 if row[8] else 0
        out.append(b0); out.append(b1)
    return out


def main():
    ap = argparse.ArgumentParser(description="한글 글꼴 데이터를 만듭니다.")
    ap.add_argument("--full", action="store_true",
                    help="현대 한글 11172자 전부 넣기 (기본은 KS X 1001 2350자)")
    ap.add_argument("--out", default="firmware/nu40_ticker/hangul_font.h")
    args = ap.parse_args()

    if not FONT_PATH.exists():
        raise SystemExit(f"[오류] 글꼴 파일이 없습니다: {FONT_PATH}")

    codes = all_syllables() if args.full else ks_x_1001_syllables()
    font = ImageFont.truetype(str(FONT_PATH), FONT_EM)
    img = Image.new("1", (8, 8), 0)

    print(f"글자 수: {len(codes)}자")
    packed, blank = [], 0
    for cp in codes:
        rows = render_glyph(img, font, chr(cp))
        if not any(any(r) for r in rows):
            blank += 1
        packed.append(pack(rows))
    if blank:
        print(f"  (모양이 비어 있는 글자 {blank}개 — 글꼴에 없는 글자입니다)")

    total = len(codes) * 2 + len(codes) * ROW_BYTES * GLYPH_H
    lines = []
    lines.append("// 이 파일은 tools/make_hangul_font.py 가 자동으로 만든 것입니다. 직접 고치지 마세요.")
    lines.append("//")
    lines.append("// 한글 글꼴 데이터 (갈무리9 픽셀 글꼴)")
    lines.append(f"//   글자 수   : {len(codes)}자" + ("  (현대 한글 전체)" if args.full else "  (KS X 1001 완성형)"))
    lines.append(f"//   글자 크기 : {GLYPH_W}x{GLYPH_H} 픽셀, 글자 간격 {ADVANCE}픽셀")
    lines.append(f"//   차지 용량 : 약 {total // 1024}KB")
    lines.append("//")
    lines.append("// 저장 방식: 한 글자당 18바이트. 위에서부터 9줄, 한 줄에 2바이트.")
    lines.append("//            앞 바이트에 왼쪽 8칸(최상위 비트가 맨 왼쪽), 뒷 바이트 최상위 비트에 9번째 칸.")
    lines.append("#pragma once")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append(f"#define HANGUL_COUNT   {len(codes)}")
    lines.append(f"#define HANGUL_W       {GLYPH_W}")
    lines.append(f"#define HANGUL_H       {GLYPH_H}")
    lines.append(f"#define HANGUL_ADVANCE {ADVANCE}")
    lines.append("")
    lines.append("// 글자 번호 목록. 작은 것부터 정렬돼 있어서 이진 탐색으로 빠르게 찾는다.")
    lines.append("const uint16_t HANGUL_CODES[HANGUL_COUNT] = {")
    for i in range(0, len(codes), 12):
        lines.append("  " + " ".join(f"0x{c:04X}," for c in codes[i:i + 12]))
    lines.append("};")
    lines.append("")
    lines.append("// 글자 모양. HANGUL_CODES 와 같은 순서다.")
    lines.append("const uint8_t HANGUL_BITS[HANGUL_COUNT][18] = {")
    for row in packed:
        lines.append("  {" + ",".join(f"0x{b:02X}" for b in row) + "},")
    lines.append("};")
    lines.append("")

    out = Path(args.out)
    out.write_text("\n".join(lines))
    print(f"만들었습니다: {out}")
    print(f"  파일 크기 {out.stat().st_size / 1024:.0f}KB, 보드에서 차지할 용량 약 {total / 1024:.0f}KB")


if __name__ == "__main__":
    main()
