#!/usr/bin/env python3
"""逐间房量 min_corridor_width —— 确认基准第 12 条到底是谁在主导。

bench_hk 的 metrics["min_corridor"] 是**所有房间取最小**，
所以一间 3.8m2 的厨房完全可以盖掉 30m2 客厅的成绩。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, HERE)

from hkgeometry.house import load_flat, solve_house          # noqa: E402
from hkgeometry.cpsat_solver import min_corridor_width        # noqa: E402
from bench_hk import CASES, build_items                       # noqa: E402


def main() -> None:
    for name in (sys.argv[1:] or list(CASES)):
        case = CASES[name]
        flat = load_flat(os.path.join(ROOT, case["plan"]))
        items = build_items(case)
        assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
        t0 = time.time()
        house = solve_house(flat, items, time_limit_s=40.0, parallel=True,
                            assignment=assign)
        print("=== %s   (solve %.0fs)" % (name, time.time() - t0))
        worst = None
        for rm in flat.rooms:
            lay = house.by_room.get(rm.id)
            if not lay or not lay.placements:
                print("   %-8s  (empty)" % rm.id)
                continue
            w = min_corridor_width(rm, lay)
            # 找出最窄那一刀在哪
            mark = ""
            if worst is None or w < worst[1]:
                worst = (rm.id, w)
            print("   %-8s area %5.2f m2  n=%2d  min_corridor %7.0f mm%s"
                  % (rm.id, rm.geo().area / 1e6, len(lay.placements), w, mark))
        if worst:
            print("   --> 基准第 12 条取的就是 %s 的 %.0f mm" % (worst[0], worst[1]))


if __name__ == "__main__":
    main()
