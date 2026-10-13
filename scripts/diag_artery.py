#!/usr/bin/env python3
"""诊断中央通路：找出「最窄的那一刀」在哪、被哪两件家具夹出来。"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from shapely.geometry import LineString                                    # noqa: E402
from hkgeometry.house import load_flat, solve_house                        # noqa: E402
sys.path.insert(0, HERE)
from bench_hk import CASES, build_items                                    # noqa: E402


def profile(room, lay, step=50):
    poly = room.geo()
    b = poly.bounds
    horiz = (b[2] - b[0]) >= (b[3] - b[1])
    lo, hi = (b[0], b[2]) if horiz else (b[1], b[3])
    n = max(2, int((hi - lo) // step))
    out = []
    for k in range(n + 1):
        t = lo + (hi - lo) * k / n
        line = (LineString([(t, b[1] - 20), (t, b[3] + 20)]) if horiz
                else LineString([(b[0] - 20, t), (b[2] + 20, t)]))
        free = poly.intersection(line)
        if free.is_empty:
            continue
        cutters = []
        for p in lay.placements:
            fp = p.footprint()
            if fp.intersects(line):
                cutters.append(p.inst)
            free = free.difference(fp)
        if free.is_empty:
            out.append((t, 0.0, cutters))
            continue
        parts = [free] if free.geom_type == "LineString" else list(getattr(free, "geoms", []))
        best = max((g.length for g in parts if g.geom_type == "LineString"), default=0.0)
        out.append((t, best, cutters))
    return out, horiz


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="onpak05")
    ap.add_argument("--room", default="living")
    ap.add_argument("--time-limit", type=float, default=40.0)
    args = ap.parse_args()
    case = CASES[args.case]
    flat = load_flat(os.path.join(ROOT, case["plan"]))
    items = build_items(case)
    assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
    t0 = time.time()
    house = solve_house(flat, items, time_limit_s=args.time_limit, parallel=True,
                        assignment=assign)
    print("solve %.1fs" % (time.time() - t0))
    room = next(r for r in flat.rooms if r.id == args.room)
    lay = house.by_room.get(args.room)
    prof, horiz = profile(room, lay)
    print("room %s  area %.2f m2  bounds %s  sweep %s"
          % (room.id, room.geo().area / 1e6,
             tuple(round(v) for v in room.geo().bounds),
             "vertical cuts (x)" if horiz else "horizontal cuts (y)"))
    prof.sort(key=lambda r: r[1])
    for t, w, cut in prof[:12]:
        print("   t=%7.0f  free run %7.0f mm   cut by %s" % (t, w, cut))
    print("   ...")
    for t, w, cut in prof[-3:]:
        print("   t=%7.0f  free run %7.0f mm" % (t, w))


if __name__ == "__main__":
    main()
