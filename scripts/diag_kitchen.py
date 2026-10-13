#!/usr/bin/env python3
"""把厨房/浴室解出来之后，逐件打印 footprint / 朝向 / 门扇区，追第 6、9 条。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, HERE)

from hkgeometry.house import load_flat, solve_house                 # noqa: E402
from hkgeometry.greedy import door_approach_zones                   # noqa: E402
from hkgeometry.cpsat_solver import FRONT_DEPTH_MIN, front_zone      # noqa: E402
from hkgeometry.types import front_dir                              # noqa: E402
from bench_hk import CASES, build_items                             # noqa: E402


def dump(flat, house, rid):
    rm = flat.room(rid)
    lay = house.by_room.get(rid)
    if not lay:
        print("  %s: empty" % rid)
        return
    print("=== %s  bounds %s" % (rid, tuple(round(v) for v in rm.geo().bounds)))
    print("    openings: %s" % [(o.id, o.kind, o.wall, o.center_mm, o.width_mm)
                                for o in rm.openings])
    zs = door_approach_zones(rm)
    for zid, z in zs:
        b = z.bounds
        print("    door zone %-12s x %.0f..%.0f  y %.0f..%.0f"
              % (zid, b[0], b[2], b[1], b[3]))
    for p in lay.placements:
        fp = p.footprint()
        b = fp.bounds
        fx, fy = p.front_dir()
        need = FRONT_DEPTH_MIN.get(p.category, 0)
        print("    %-18s cat=%-16s rot=%3d  x %.0f..%.0f  y %.0f..%.0f  "
              "front=(%d,%d) need=%d"
              % (p.inst, p.category, p.rot, b[0], b[2], b[1], b[3], fx, fy, need))
        for zid, z in zs:
            a = fp.intersection(z).area
            if a > 1:
                print("        !! 侵占门扇区 %-12s %.0f mm2" % (zid, a))
        if need:
            fz = front_zone(p, need)
            for q in lay.placements:
                if q.inst == p.inst:
                    continue
                a = fz.intersection(q.footprint()).area
                if a > 1:
                    print("        !! 前方使用区被 %-18s 挡 %.0f mm2"
                          % (q.inst, a))


def main() -> None:
    case = CASES["onpak05"]
    flat = load_flat(os.path.join(ROOT, case["plan"]))
    items = build_items(case)
    assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
    t0 = time.time()
    house = solve_house(flat, items, time_limit_s=40.0, parallel=True,
                        assignment=assign)
    print("solve %.0fs" % (time.time() - t0))
    for rid in ("kitchen", "bath"):
        dump(flat, house, rid)


if __name__ == "__main__":
    main()
