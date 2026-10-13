#!/usr/bin/env python3
"""拿已经解好的 living 布局，直接跑 relayout_search，看它能不能把茶几拉回沙发前面。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, HERE)

from hkgeometry.house import load_flat                            # noqa: E402
from hkgeometry.greedy import door_approach_zones                 # noqa: E402
from hkgeometry import cpsat_solver as C                          # noqa: E402
from hkgeometry.types import Placement, Layout                    # noqa: E402
from bench_hk import CASES                                        # noqa: E402

flat = load_flat(CASES["onpak05"]["plan"])
rm = flat.room("living")
d = json.load(open(os.path.join(ROOT, "out", "layout_onpak05.json"), encoding="utf-8"))
ps = [Placement(**{k: p[k] for k in ("inst", "category", "x", "y", "rot", "w", "d", "h")})
      for p in d["living"]["placements"]]
zones = [z for _i, z in door_approach_zones(rm)]
poly = rm.usable_geo()


def where(ps):
    c = next(p for p in ps if p.category == "coffee_table")
    s = next(p for p in ps if p.category == "sofa")
    import math
    return "coffee(%d,%d) sofa(%d,%d) d=%.0f" % (
        c.x, c.y, s.x, s.y, math.hypot(c.center()[0] - s.center()[0],
                                       c.center()[1] - s.center()[1]))


print("BEFORE", where(ps))
print("  cheap_bad", C._cheap_bad(rm, ps, zones, poly))
t0 = time.time()
new = C.relayout_search(rm, Layout(room_id="living", placements=ps))
print("relayout took %.1fs" % (time.time() - t0))
print("AFTER ", where(list(new.placements)))
print("  cheap_bad", C._cheap_bad(rm, list(new.placements), zones, poly))
moved = [(a.inst, (a.x, a.y, a.rot), (b.x, b.y, b.rot))
         for a, b in zip(ps, list(new.placements))
         if (a.x, a.y, a.rot) != (b.x, b.y, b.rot)]
print("  moved:", moved if moved else "nothing")
# 单独问一次：茶几在想去的范围内到底有没有合法格？
c = next(p for p in ps if p.category == "coffee_table")
s = next(p for p in ps if p.category == "sofa")
others = [p for p in new.placements if p.inst != c.inst]
obst = [q.footprint() for q in others]
ok = 0
b = poly.bounds
x = b[0]
while x + c.w <= b[2]:
    y = b[1]
    while y + c.d <= b[3]:
        import shapely
        bx = shapely.box(x, y, x + c.w, y + c.d)
        if poly.covers(bx) and not any(bx.intersects(o) for o in obst):
            cand = Placement(inst=c.inst, category=c.category, x=int(x), y=int(y),
                             rot=0, w=c.w, d=c.d, h=c.h)
            if C._rel_violation(cand, x + c.w / 2, y + c.d / 2, others) == 0.0:
                ok += 1
        y += 150
    x += 150
print("  和沙发关系完全满足的合法格数（150mm 网格，rot=0）:", ok)
