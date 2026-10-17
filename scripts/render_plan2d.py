#!/usr/bin/env python3
"""整屋 2D 平面圖 —— 畫得像一張真的建築圖，特別係**門要睇得到**。

為什麼要另寫一個：原本 viz._draw_plan 只畫單一間房，而且門窗係**畫在牆上面
的一條粗線**（紅色 = 門、藍色 = 窗），牆本身冇斷開 —— 睇圖嘅人根本睇唔出
邊度係入口、邊度入得去、門向邊邊開。3D 娃娃屋又遮檔。

一張睇得明嘅平面圖要有：
  · 牆按真實厚度填色，**門窗位置係真正斷開嘅缺口**
  · 門：門扇（由鉸鏈畫出去）+ 四分之一圓掃掠弧 + 開向
  · 窗：缺口 + 三條細線（標準窗符號）
  · 房名 + 面積 + **淨寬 x 淨深**
  · 家具：實心矩形 + 正面箭頭（朝向睇得到）
  · 尺寸鏈（總寬/總深）

運行：
  python3 scripts/render_plan2d.py --plan out/plan_hk_b.json \
      --layout out/house_layout_b.json --out out/plan2d_b.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Polygon as MplPolygon, Rectangle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.house import load_flat
from hkgeometry.types import Placement, front_dir, hinge_at_start
from hkgeometry.viz import setup_font

setup_font()          # 冇這一步所有中文都會變豆腐格

WALL_FILL = {"load_bearing": "#4a4a4a", "partition": "#8d8d8d"}
FLOOR = {"living": "#fbf6ea", "bedroom": "#f6eef6", "kitchen": "#eef7f0",
         "bath": "#eaf2fa", "storage": "#f3f3f0", "dining": "#fdf4ec"}
DOOR = "#c0392b"
WIN = "#2a7ae2"
FURN_EDGE = "#1a1a1a"


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = max(1e-9, math.hypot(dx, dy))
    return dx / L, dy / L, L


def draw_wall_with_gaps(ax, wall, openings, zorder=3, poly=None):
    """把一面牆按開口切成幾段畫出來 —— 開口位置是**真的斷開**。"""
    a, b = tuple(wall.a), tuple(wall.b)
    ux, uy, L = _unit(a, b)
    th = float(getattr(wall, "thickness_mm", 100) or 100)
    # 收集這面牆上的開口區間
    spans = []
    for op in openings:
        if op.wall != wall.id:
            continue
        half = op.width_mm / 2.0
        spans.append((op.center_mm - half, op.center_mm + half, op))
    spans.sort()
    # 牆體段 = 全長減去開口
    segs, cur = [], 0.0
    for s0, s1, op in spans:
        if s0 > cur:
            segs.append((cur, s0, None))
        segs.append((max(cur, s0), s1, op))
        cur = max(cur, s1)
    if cur < L:
        segs.append((cur, L, None))

    for t0, t1, op in segs:
        p0 = (a[0] + ux * t0, a[1] + uy * t0)
        p1 = (a[0] + ux * t1, a[1] + uy * t1)
        if op is None or t1 - t0 < 1:
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=WALL_FILL.get(wall.type, "#666"),
                    lw=max(1.5, th / 12.0), solid_capstyle="butt", zorder=zorder)
            continue
        # 開口段
        if op.kind == "door":
            draw_door(ax, p0, p1, (ux, uy), th, op, poly=poly)
        else:
            draw_window(ax, p0, p1, (ux, uy), th, op)


def _inward_normal(a, b, p, poly):
    """回傳指向「房間內側」的單位法向量（用 mid-span 的內法線判斷）。"""
    ux, uy, _ = _unit(a, b)
    # 用房間幾何的質心判斷內側（比單點探測穩）
    cx, cy = poly.centroid.x, poly.centroid.y
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    nx, ny = -uy, ux
    if (cx - mx) * nx + (cy - my) * ny < 0:
        nx, ny = -nx, -ny
    return nx, ny


def draw_door(ax, p0, p1, u, th, op, poly=None):
    """門：門扇 + 四分之一圓掃掠弧。門扇畫在**開向**那一側。"""
    (x0, y0), (x1, y1) = p0, p1
    w = math.hypot(x1 - x0, y1 - y0)
    ux, uy = u
    nx, ny = -uy, ux
    if poly is not None:
        nx, ny = _inward_normal(p0, p1, None, poly)
    sw = op.swing or {}
    inward = (sw.get("dir") or "in").lower() in ("in", "inward")
    if not inward:
        nx, ny = -nx, -ny
    hx, hy = (x0, y0) if hinge_at_start(sw) else (x1, y1)
    # 门扇另一端，用来画扫掠弧
    ox, oy = (x1, y1) if hinge_at_start(sw) else (x0, y0)
    # 門扇：開盡時垂直於牆、貼向開向那一側
    ax.plot([hx, hx + nx * w], [hy, hy + ny * w], color=DOOR, lw=2.2, zorder=6)
    # 掃掠弧：由門框另一端掃到門扇位置
    a0 = math.degrees(math.atan2(oy - hy, ox - hx))
    a1 = math.degrees(math.atan2(ny, nx))
    # 取較短的那一段弧（<180°）
    d = (a1 - a0) % 360
    if d > 180:
        a0, a1 = a1, a0
        d = 360 - d
    ax.add_patch(Arc((hx, hy), 2 * w, 2 * w, angle=0, theta1=a0, theta2=a0 + d,
                     color=DOOR, lw=0.9, ls=(0, (4, 3)), zorder=5))
    # 門檻
    ax.plot([x0, x1], [y0, y1], color=DOOR, lw=0.8, ls=":", zorder=4)


def draw_window(ax, p0, p1, u, th, op):
    ux, uy = u
    nx, ny = -uy, ux
    for off in (-th * 0.25, 0.0, th * 0.25):
        ax.plot([p0[0] + nx * off, p1[0] + nx * off],
                [p0[1] + ny * off, p1[1] + ny * off],
                color=WIN, lw=0.9, zorder=5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--layout", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    flat = load_flat(args.plan)
    lay = json.load(open(args.layout, encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(15, 10))
    all_x, all_y = [], []

    for r in flat.rooms:
        poly = r.geo()
        ax.add_patch(MplPolygon(list(poly.exterior.coords), closed=True,
                                facecolor=FLOOR.get(lay.get(r.id, {}).get("func", ""), "#f7f7f4"),
                                edgecolor="none", zorder=1))
        # 房間內「拆不走」的實體（服務核心 / 柱）—— 畫成被挖空的洞
        for ring in poly.interiors:
            ax.add_patch(MplPolygon(list(ring.coords), closed=True,
                                    facecolor="#ffffff", edgecolor="#22252b",
                                    lw=2.0, zorder=2))
            hb = ring.bounds
            ax.text((hb[0] + hb[2]) / 2.0, (hb[1] + hb[3]) / 2.0,
                    "服務核心（固定）\n%d x %d mm" % (hb[2] - hb[0], hb[3] - hb[1]),
                    ha="center", va="center", fontsize=9, color="#666",
                    style="italic", zorder=8)
    # ★ 一面牆只畫一次。相鄰兩間房會各自列出同一面牆，
    #   逐間畫會畫兩次（線變粗、門弧畫兩條）。用幾何 key 去重。
    seen_walls = set()
    for r in flat.rooms:
        for w in r.walls:
            key = (round(min(w.a[0], w.b[0])), round(min(w.a[1], w.b[1])),
                   round(max(w.a[0], w.b[0])), round(max(w.a[1], w.b[1])),
                   round(float(getattr(w, "thickness_mm", 100) or 100)))
            if key in seen_walls:
                continue
            seen_walls.add(key)
            draw_wall_with_gaps(ax, w, r.openings, zorder=3, poly=r.geo())

    # 家具 + 正面箭頭
    for rid, h in lay.items():
        for p in h["placements"]:
            pl = Placement(inst=p["inst"], sku=p["sku"], category=p["category"],
                           x=p["x"], y=p["y"], rot=p["rot"], w=p["w"], d=p["d"], h=p["h"])
            fp = pl.footprint()
            xs, ys = fp.exterior.xy
            ax.add_patch(MplPolygon(list(zip(xs, ys)), closed=True,
                                    facecolor="#d9cfc0", edgecolor=FURN_EDGE,
                                    lw=1.1, alpha=0.95, zorder=4))
            cx, cy = pl.center()
            fx, fy = front_dir(pl.rot)
            L = min(pl.w, pl.d) * 0.35
            ax.annotate("", xy=(cx + fx * (min(pl.w, pl.d) / 2 + 120), cy + fy * (min(pl.w, pl.d) / 2 + 120)),
                        xytext=(cx, cy),
                        arrowprops=dict(arrowstyle="-|>", color="#8a5a2b", lw=1.0),
                        zorder=6)
            ax.text(cx, cy, p["inst"].lstrip("#"), ha="center", va="center",
                    fontsize=6.5, color="#222", zorder=7)
            all_x += list(xs); all_y += list(ys)

    # 房名 + 面積 + 淨尺寸
    for r in flat.rooms:
        b = r.usable_geo().bounds
        c = r.usable_geo().representative_point()
        ax.text(c.x, c.y, "%s\n%.1f m²  淨 %dx%d"
                % (r.label or r.id, r.usable_geo().area / 1e6, b[2] - b[0], b[3] - b[1]),
                ha="center", va="center", fontsize=10.5, weight="bold",
                color="#333", zorder=9,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.72))
        all_x += [b[0], b[2]]; all_y += [b[1], b[3]]

    pad = 900
    ax.set_xlim(min(all_x) - pad, max(all_x) + pad)
    ax.set_ylim(min(all_y) - pad, max(all_y) + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(args.title or ("2D 平面圖 — %s" % flat.id), fontsize=15, pad=14)
    ax.text(0.01, 0.01, "牆：深灰=承重 / 淺灰=間隔　紅=門（含開向弧）　藍=窗　褐箭頭=家具正面",
            transform=ax.transAxes, fontsize=8, color="#666")
    fig.tight_layout()
    fig.savefig(args.out, dpi=130, facecolor="white")
    print("-> %s" % args.out)


if __name__ == "__main__":
    main()
