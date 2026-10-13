#!/usr/bin/env python3
"""读 out/layout_<case>.json，把关键家具关系用数字打出来（不用重解）。"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.types import Placement, front_dir          # noqa: E402


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "onpak05"
    d = json.load(open(os.path.join(ROOT, "out", "layout_%s.json" % name),
                       encoding="utf-8"))
    ps = {}
    for rid, lay in d.items():
        for p in lay["placements"]:
            ps[p["inst"]] = (rid, Placement(**{k: p[k] for k in
                             ("inst", "category", "x", "y", "rot", "w", "d", "h")}))
    print("case %s: %d items" % (name, len(ps)))
    for a, b in (("#sofa", "#tv"), ("#sofa", "#coffee"), ("#dining", "#chair1"),
                 ("#dining", "#chair2"), ("#dining", "#chair3"), ("#dining", "#chair4"),
                 ("#bed_main", "#wardrobe_main")):
        if a not in ps or b not in ps:
            print("  %-14s %-14s  (missing)" % (a, b))
            continue
        ra, pa = ps[a]
        rb, pb = ps[b]
        ax, ay = pa.center()
        bx, by = pb.center()
        dist = math.hypot(bx - ax, by - ay)
        fx, fy = front_dir(pa.rot)
        # b 相对 a 的方向 与 a 的正前方是否一致
        if dist > 1:
            dot = ((bx - ax) * fx + (by - ay) * fy) / dist
        else:
            dot = float("nan")
        print("  %-14s -> %-14s  dist %6.0f mm   front_dot %5.2f  %s"
              % (a, b, dist, dot,
                 "在正前方" if dot > 0.6 else ("侧后方" if dot < 0 else "侧边/后方")))


if __name__ == "__main__":
    main()
