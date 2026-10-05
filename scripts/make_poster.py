#!/usr/bin/env python3
"""Demo 海报：把 3D 娃娃屋 + 2D 分析图 + 显大分拼成一张。

这是 Demo 的最终展示图 —— 「家具一件没少，显大分从 52 跳到 89」。

运行：python3 scripts/make_poster.py
"""
from __future__ import annotations

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

CJK_FONT = None
for _p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
           "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
           "/usr/share/fonts/truetype/arphic/uming.ttc"):
    if os.path.exists(_p):
        CJK_FONT = _p
        break


def F(size):
    if CJK_FONT:
        try:
            return ImageFont.truetype(CJK_FONT, size)
        except Exception:
            pass
    return ImageFont.load_default()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry import demo as D
from hkgeometry.corridor import corridor_reports, default_corridors
from hkgeometry.exposure import exposure_map
from hkgeometry.spaciousness import spaciousness_report
from hkgeometry.viz import _draw_exposure, _draw_plan, setup_font

OUT = os.path.join(ROOT, "out")


def panel_png(room, layout, emap, corridors, path, size=(860, 470)):
    fig = plt.figure(figsize=(size[0] / 100.0, size[1] / 100.0), dpi=100, facecolor="white")
    ax1 = fig.add_axes([0.02, 0.03, 0.46, 0.94])
    ax2 = fig.add_axes([0.51, 0.03, 0.47, 0.94])
    creps = {"corridors": corridor_reports(corridors, layout.placements)}
    _draw_plan(ax1, room, layout, corridors, creps)
    _draw_exposure(ax2, emap, layout)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def main():
    setup_font()
    room = D.demo_room()
    emap = exposure_map(room, cell_mm=50)
    corridors = default_corridors(room)

    # 两种摆法都由求解器生成（目标函数有没有曝光度项的区别）
    naive, opt = D.solve_pair(room, emap)
    opt_low = D.swap_sku(opt, {"#wardrobe": "#wardrobe_light"})
    cols = []
    for tag, title, lay in (("bad", "舊擺法（沒優化）", naive),
                            ("good", "新擺法（體量往低曝光區推）", opt),
                            ("good_low", "新擺法 + 換開放式衣櫃（位置不動，只換款式）", opt_low)):
        rep = spaciousness_report(room, lay, emap, corridors)
        p2 = panel_png(room, lay, emap, corridors, os.path.join(OUT, "_panel_%s.png" % tag))
        p3 = os.path.join(OUT, "dollhouse_%s.png" % tag)
        cols.append((title, rep, p2, p3))
        print("%-18s %s" % (title, rep.summary()))

    W = 900
    H3D = 620
    H2D = 492
    HEAD = 196
    PAD = 16
    total_w = W * len(cols) + PAD * (len(cols) + 1)
    total_h = HEAD + H3D + H2D + PAD * 2 + 10

    sheet = Image.new("RGB", (total_w, total_h), (246, 246, 248))
    dr = ImageDraw.Draw(sheet)
    t1 = "同一套家具，兩種擺法 —— 視覺顯大分對比"
    t2 = "家具一件沒少。只是把體量挪到看不見的地方、把主視線讓出來。"
    f1, f2 = F(38), F(24)
    dr.text(((total_w - dr.textlength(t1, font=f1)) / 2, 26), t1,
            fill=(20, 20, 24), font=f1)
    dr.text(((total_w - dr.textlength(t2, font=f2)) / 2, 78), t2,
            fill=(95, 95, 102), font=f2)
    dr.line([(PAD, HEAD - 62), (total_w - PAD, HEAD - 62)], fill=(222, 222, 228), width=1)

    for i, (title, rep, p2, p3) in enumerate(cols):
        x = PAD + i * (W + PAD)
        y = HEAD
        im3 = Image.open(p3).convert("RGB")
        im3 = im3.resize((W, int(im3.height * W / im3.width)), Image.LANCZOS)
        im3 = im3.crop((0, max(0, (im3.height - H3D) // 2),
                        W, min(im3.height, max(0, (im3.height - H3D) // 2) + H3D)))
        sheet.paste(im3, (x, y))
        y += H3D + PAD
        im2 = Image.open(p2).convert("RGB").resize((W, H2D), Image.LANCZOS)
        sheet.paste(im2, (x, y))
        y += H2D + 2

        s = rep.score
        col = (46, 158, 79) if s >= 78 else ((230, 168, 23) if s >= 62 else (214, 69, 69))
        ft, fs, fu = F(30), F(46), F(22)
        dr.rectangle([x, HEAD - 56, x + W, HEAD - 4], fill=(255, 255, 255))
        dr.text((x + 14, HEAD - 46), title, fill=(28, 28, 32), font=ft)
        dr.text((x + W - 200, HEAD - 38), "顯大分", fill=(110, 110, 116), font=fu)
        dr.text((x + W - 128, HEAD - 56), "%d" % round(s), fill=col, font=fs)
        dr.text((x + W - 62, HEAD - 38), "/100", fill=(125, 125, 130), font=fu)

    out = os.path.join(OUT, "poster.png")
    sheet.save(out)
    print("-> %s  %s" % (out, sheet.size))
    for p in os.listdir(OUT):
        if p.startswith("_panel_"):
            os.remove(os.path.join(OUT, p))


if __name__ == "__main__":
    main()
