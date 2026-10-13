#!/usr/bin/env python3
"""追第 11 条「面壁」：打印每件家具正面射线的落点、距离、以及是谁挡的。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, HERE)

import shapely                                                      # noqa: E402
from shapely.geometry import Point                                  # noqa: E402
from hkgeometry.house import load_flat, solve_house                 # noqa: E402
from bench_hk import CASES, build_items                             # noqa: E402


def main() -> None:
    case = CASES["onpak05"]
    flat = load_flat(os.path.join(ROOT, case["plan"]))
    items = build_items(case)
    assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
    t0 = time.time()
    house = solve_house(flat, items, time_limit_s=40.0, parallel=True,
                        assignment=assign)
    print("solve %.0fs" % (time.time() - t0))
    rm = flat.room("living")
    lay = house.by_room["living"]
    poly = rm.geo()
    print("living bounds %s" % (tuple(round(v) for v in poly.bounds),))
    for p in lay.placements:
        if p.category in ("coffee_table", "side_table", "dining_table"):
            continue
        cx, cy = p.center()
        fx, fy = p.front_dir()
        half = p.d / 2.0
        start = (cx + fx * half, cy + fy * half)
        end = (cx + fx * (half + 400), cy + fy * (half + 400))
        ray = shapely.LineString([start, end])
        hit = ray.intersection(poly.boundary)
        d = None if hit.is_empty else hit.distance(Point(start))
        blockers = [(q.inst, ray.intersection(q.footprint()).length)
                    for q in lay.placements if q.inst != p.inst
                    and ray.intersects(q.footprint())]
        flag = ""
        if d is not None and d < 250 and not blockers:
            flag = "   <-- 判第 11 条 FAIL"
        print("  %-18s rot=%3d centre(%6.0f,%6.0f) front=(%2d,%2d) "
              "face=(%6.0f,%6.0f)  wall_dist=%s  blocked_by=%s%s"
              % (p.inst, p.rot, cx, cy, fx, fy, start[0], start[1],
                 "None" if d is None else "%.0f" % d, blockers, flag))


if __name__ == "__main__":
    main()
