#!/usr/bin/env python3
"""「床是不是怎么放都会压到门？」——用穷举回答，不要靠感觉。

对每扇门取不同的净空模型（走道宽 x 深），把床在房间里所有合法位置扫一遍，
报出：最少重叠面积、以及「完全唔踩到走道」的位置有几个。

用途：当一个结论听起来像「几何必然」时，先问「这是我模型的必然，
还是物理的必然？」—— 大多数时候是前者。

运行：python3 scripts/analysis_bed_vs_door.py [--plan out/plan_hk_b.json]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

import shapely
from shapely.geometry import Point

from hkgeometry.house import load_flat

BED_W, BED_D = 990, 2060          # 库裏最細嘅單人床（SLÄKT）


def zone_for(room, poly, op, width, depth):
    wall = next((w for w in room.walls if w.id == op.wall), None)
    if wall is None:
        return None
    ax, ay = wall.a
    bx, by = wall.b
    L = max(1.0, math.hypot(bx - ax, by - ay))
    ux, uy = (bx - ax) / L, (by - ay) / L
    t = max(0.0, min(1.0, op.center_mm / L))
    px, py = ax + ux * L * t, ay + uy * L * t
    nx, ny = -uy, ux
    if not poly.contains(Point(px + nx * 200, py + ny * 200)):
        nx, ny = -nx, -ny
    half = width / 2.0
    zx, zy = px - ux * half, py - uy * half
    return shapely.Polygon([
        (zx, zy), (zx + ux * width, zy + uy * width),
        (zx + ux * width + nx * depth, zy + uy * width + ny * depth),
        (zx + nx * depth, zy + ny * depth)])


def sweep(poly, zones, bw, bd, step=25):
    mnx, mny, mxx, mxy = poly.bounds
    best, clear = None, 0
    for rot in (0, 90, 180, 270):
        w, d = (bw, bd) if rot in (0, 180) else (bd, bw)
        if w > mxx - mnx or d > mxy - mny:
            continue
        for x in range(int(mnx), int(mxx - w) + 1, step):
            for y in range(int(mny), int(mxy - d) + 1, step):
                box = shapely.box(x, y, x + w, y + d)
                if not poly.covers(box):
                    continue
                tot = sum(box.intersection(z).area for z in zones)
                if tot <= 1.0:
                    clear += 1
                if best is None or tot < best[0]:
                    best = (tot / 1e6, rot, x, y)
    return best, clear


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=os.path.join(ROOT, "out", "plan_hk_b.json"))
    ap.add_argument("--room", default="bedroom")
    ap.add_argument("--w", type=int, default=BED_W)
    ap.add_argument("--d", type=int, default=BED_D)
    ap.add_argument("--step", type=int, default=25)
    args = ap.parse_args()

    flat = load_flat(args.plan)
    room = flat.room(args.room)
    poly = room.usable_geo()
    mnx, mny, mxx, mxy = poly.bounds
    doors = [o for o in room.openings if o.kind == "door"]
    print("%s 可用 %.0fx%.0f，門寬 %s，家具 %dx%d"
          % (args.room, mxx - mnx, mxy - mny, [o.width_mm for o in doors],
             args.w, args.d))
    print()
    print("%-40s %-9s %-11s %s" % ("淨空模型（走道寬 x 深）", "走道總面積",
                                   "最少重疊", "完全唔踩的位置"))
    rows = [("900 寬（保守，假設所有門都 900）x 805 深", 900, 805),
            ("實際門寬 x 805 深", None, 805),
            ("實際門寬 x 600 深", None, 600),
            ("實際門寬 x 450 深", None, 450),
            ("實際門寬 x 300 深", None, 300),
            ("實際門寬 x 150 深（只留門扇厚度）", None, 150)]
    for tag, w, d in rows:
        zs = []
        for o in doors:
            z = zone_for(room, poly, o, w if w else o.width_mm, d)
            if z is not None:
                zs.append(z)
        b, clear = sweep(poly, zs, args.w, args.d, args.step)
        print("%-40s %-9.2f %-11.3f %d"
              % (tag, sum(z.area for z in zs) / 1e6, b[0], clear))


if __name__ == "__main__":
    main()
