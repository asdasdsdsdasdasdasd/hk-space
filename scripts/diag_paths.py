#!/usr/bin/env python3
"""诊断：整屋俯视图 + 从大门实际走得到的区域（BFS 染色）。

问题：光看 3D 娃娃屋看不出「路通不通」。这张图把
  · 每件家具的占地
  · 每扇门的位置
  · **从大门出发，人（宽 600mm）实际能走到的每一格**
画在一起 —— 红色 = 走不到的地方。
"""
from __future__ import annotations

import json
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import shapely
from matplotlib.patches import Polygon as MplPolygon
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.house import load_flat
from hkgeometry.types import Layout, Placement
from hkgeometry.viz import setup_font

OUT = os.path.join(ROOT, "out")
CELL = 50          # mm
PERSON = 600       # 人宽


def main():
    setup_font()
    flat = load_flat(os.path.join(OUT, "plan_hk_a.json"))
    with open(os.path.join(OUT, "house_layout.json"), encoding="utf-8") as f:
        house = json.load(f)

    # 整屋包围盒
    xs, ys = [], []
    for r in flat.rooms:
        for x, y in r.polygon:
            xs.append(x); ys.append(y)
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    nx = int((maxx - minx) // CELL) + 1
    ny = int((maxy - miny) // CELL) + 1
    X, Y = np.meshgrid(np.arange(nx) * CELL + minx + CELL / 2,
                       np.arange(ny) * CELL + miny + CELL / 2)

    # 所有房间的并集 —— ★ 必须**把门洞也算进去**。
    # 房间多边形取的是墙内表面，相邻房间之间隔着墙厚（100-200mm）。
    # 只用房间并集的话，自由空间在门口被墙角切断，
    # 会误报「门内侧走不到」—— 那是测量方式错了，不是布局错了。
    parts = [r.geo() for r in flat.rooms]
    for r in flat.rooms:
        for o in r.openings:
            if o.kind != "door":
                continue
            wl = next((w for w in r.walls if w.id == o.wall), None)
            if wl is None:
                continue
            ax_, ay_ = wl.a
            bx_, by_ = wl.b
            L = max(1.0, math.hypot(bx_ - ax_, by_ - ay_))
            ux, uy = (bx_ - ax_) / L, (by_ - ay_) / L
            t = o.center_mm / L
            px, py = ax_ + ux * L * t, ay_ + uy * L * t
            nx_, ny_ = -uy, ux
            half = o.width_mm / 2.0
            # 门洞做成一个跨过墙厚的方块
            th = max(220.0, float(wl.thickness_mm or 120) * 2.5)
            parts.append(shapely.Polygon([
                (px - ux * half + nx_ * th, py - uy * half + ny_ * th),
                (px + ux * half + nx_ * th, py + uy * half + ny_ * th),
                (px + ux * half - nx_ * th, py + uy * half - ny_ * th),
                (px - ux * half - nx_ * th, py - uy * half - ny_ * th)]))
    allpoly = shapely.union_all(parts)
    inside = np.asarray(shapely.contains_xy(allpoly, X, Y))

    # 家具缓冲 300mm 之后，人还能站的地方
    blocked = []
    for rid, h in house.items():
        for p in h["placements"]:
            w, d = p["w"], p["d"]
            if p["rot"] in (90, 270):
                w, d = d, w
            blocked.append(shapely.box(p["x"], p["y"], p["x"] + w, p["y"] + d))
    pad = shapely.union_all([b.buffer(PERSON / 2) for b in blocked]) if blocked else None
    free = allpoly.difference(pad) if pad is not None else allpoly

    grid = np.asarray(shapely.contains_xy(free, X, Y)) & inside
    lab, n = ndimage.label(grid)

    ex, ey = flat.entry_point
    ix = int(min(max(0, round((ex - minx - CELL / 2) / CELL)), nx - 1))
    iy = int(min(max(0, round((ey - miny - CELL / 2) / CELL)), ny - 1))
    src = lab[iy, ix]
    print("[diag] 入口 (%d,%d)  连通分量 %d  全屋分量数 %d" % (ex, ey, src, n))
    reach = (lab == src) if src > 0 else np.zeros_like(grid)

    fig, ax = plt.subplots(figsize=(19, 9), dpi=110, facecolor="white")
    # 房间底色
    for r in flat.rooms:
        pts = np.asarray(r.polygon)
        ax.add_patch(MplPolygon(pts, closed=True, facecolor="#f2f2f4",
                                edgecolor="none", zorder=1))
    # 可达 / 不可达
    ax.imshow(np.where(reach, 1.0, np.nan), origin="lower",
              extent=(minx, minx + nx * CELL, miny, miny + ny * CELL),
              cmap="Greens", vmin=0, vmax=1.6, alpha=0.75, zorder=2,
              interpolation="nearest")
    ax.imshow(np.where(inside & ~reach, 1.0, np.nan), origin="lower",
              extent=(minx, minx + nx * CELL, miny, miny + ny * CELL),
              cmap="Reds", vmin=0, vmax=1.6, alpha=0.65, zorder=3,
              interpolation="nearest")
    # 家具
    for rid, h in house.items():
        for p in h["placements"]:
            w, d = p["w"], p["d"]
            if p["rot"] in (90, 270):
                w, d = d, w
            ax.add_patch(MplPolygon([(p["x"], p["y"]), (p["x"] + w, p["y"]),
                                     (p["x"] + w, p["y"] + d), (p["x"], p["y"] + d)],
                                    closed=True, facecolor="#4a4f57",
                                    edgecolor="white", lw=0.8, alpha=0.9, zorder=5))
            ax.text(p["x"] + w / 2, p["y"] + d / 2, p["inst"].strip("#"),
                    ha="center", va="center", fontsize=6, color="white", zorder=6)
    # 墙
    for r in flat.rooms:
        ax.plot(*r.geo().exterior.xy, color="#22252b", lw=2.0, zorder=4)
    # 门
    for r in flat.rooms:
        for o in r.openings:
            if o.kind != "door":
                continue
            wl = next((w for w in r.walls if w.id == o.wall), None)
            if wl is None:
                continue
            ax_, ay_ = wl.a
            bx_, by_ = wl.b
            L = max(1.0, ((bx_ - ax_) ** 2 + (by_ - ay_) ** 2) ** 0.5)
            t = o.center_mm / L
            px, py = ax_ + (bx_ - ax_) * t, ay_ + (by_ - ay_) * t
            ax.plot(px, py, "o", ms=11, mfc="none", mec="#c0392b", mew=2.2, zorder=8)
    ax.plot(*flat.entry_point, marker="*", ms=20, color="#0b6", zorder=9)
    ax.text(flat.entry_point[0], flat.entry_point[1] - 180, "大門", ha="center",
            fontsize=11, color="#0b6", weight="bold", zorder=9)
    ax.set_aspect("equal")
    ax.set_title("整屋俯視圖 —— 綠色 = 從大門走得到；紅色 = 走不到；深灰 = 家具；紅圈 = 門",
                 fontsize=14)
    fig.tight_layout()
    p = os.path.join(OUT, "diag_paths.png")
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    print("->", p)

    # 每扇门能不能走到
    print("[diag] 每扇門 600mm 内側能不能走到：")
    for r in flat.rooms:
        for o in r.openings:
            if o.kind != "door":
                continue
            wl = next((w for w in r.walls if w.id == o.wall), None)
            if wl is None:
                continue
            ax_, ay_ = wl.a
            bx_, by_ = wl.b
            L = max(1.0, ((bx_ - ax_) ** 2 + (by_ - ay_) ** 2) ** 0.5)
            ux, uy = (bx_ - ax_) / L, (by_ - ay_) / L
            t = o.center_mm / L
            px, py = ax_ + ux * L * t, ay_ + uy * L * t
            nx_, ny_ = -uy, ux
            p1 = shapely.Point(px + nx_ * 500, py + ny_ * 500)
            p2 = shapely.Point(px - nx_ * 500, py - ny_ * 500)
            # ⚠️ 必须**逐侧**检查。曾经写成「任一侧可达就算过」——
            # 结果门在客厅那一侧是通的、房间那一侧堵死，也报 ✅。
            sides = []
            for tag, pp in (("室外側", p1), ("室內側", p2)):
                jx = int(min(max(0, round((pp.x - minx - CELL / 2) / CELL)), nx - 1))
                jy = int(min(max(0, round((pp.y - miny - CELL / 2) / CELL)), ny - 1))
                sides.append((tag, src > 0 and lab[jy, jx] == src))
            flag = "✅" if all(v for _t, v in sides) else "❌"
            print("    %-9s %-8s (%5d,%5d)  %s  %s" % (
                r.id, o.id, int(px), int(py), flag,
                "  ".join("%s%s" % (t, "✅" if v else "❌") for t, v in sides)))

    # 洁具正前方
    from hkgeometry.advisory import CLEARANCE, front_clearance
    from hkgeometry.types import Placement as PL
    print("[diag] 潔具 / 櫃子正前方：")
    for rid, h in house.items():
        r = flat.room(rid)
        pls = [PL(inst=p["inst"], category=p["category"], x=p["x"], y=p["y"],
                  rot=p["rot"], w=p["w"], d=p["d"], h=p["h"]) for p in h["placements"]]
        lay = Layout(room_id=rid, placements=pls)
        for p in pls:
            need = CLEARANCE.get(p.category)
            if not need:
                continue
            have, blk = front_clearance(r, lay, p)
            mark = "✅" if have + 20 >= need else "❌"
            print("    %-9s %-16s 需要 %4d  實際 %4d  %s  %s"
                  % (rid, p.inst, need, have, mark,
                     ("被 " + blk + " 擋住") if blk else ""))


if __name__ == "__main__":
    main()
