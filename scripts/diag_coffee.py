#!/usr/bin/env python3
"""茶几为什么离沙发 3.36m？把 living 的 _cheap_bad 拆开看，并打印位置。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, HERE)

from hkgeometry.house import load_flat                            # noqa: E402
from hkgeometry.greedy import door_approach_zones                 # noqa: E402
from hkgeometry import cpsat_solver as C                          # noqa: E402
from hkgeometry.types import Placement, Layout, front_dir         # noqa: E402
from bench_hk import CASES                                        # noqa: E402

flat = load_flat(CASES["onpak05"]["plan"])
rm = flat.room("living")
d = json.load(open(os.path.join(ROOT, "out", "layout_onpak05.json"), encoding="utf-8"))
ps = [Placement(**{k: p[k] for k in ("inst", "category", "x", "y", "rot", "w", "d", "h")})
      for p in d["living"]["placements"]]
print("living bounds", tuple(round(v) for v in rm.geo().bounds))
for p in ps:
    fx, fy = front_dir(p.rot)
    print("  %-18s cat=%-16s centre(%6.0f,%6.0f) front=(%2d,%2d)"
          % (p.inst, p.category, p.center()[0], p.center()[1], fx, fy))
zones = [z for _i, z in door_approach_zones(rm)]
poly = rm.usable_geo()
print("_cheap_bad =", C._cheap_bad(rm, ps, zones, poly))
print("  (n_rel, n_door, a_door_cm2, n_front, n_wall)")
coffee = next(p for p in ps if p.category == "coffee_table")
others = [p for p in ps if p is not coffee]
print("coffee _rel_violation vs all others =",
      C._rel_violation(coffee, *coffee.center(), others))
sofa = next(p for p in ps if p.category == "sofa")
print("sofa front zone 600mm blocking items:",
      [q.inst for q in ps if q is not sofa
       and C.front_zone(sofa, 600).intersects(q.footprint())])
# 沙发正前方 300-900 的环带里有多少空位？
from shapely.geometry import Point
fz = C.front_zone(sofa, 900)
inner = C.front_zone(sofa, 300)
ring = fz.difference(inner).intersection(poly)
for q in ps:
    if q is not sofa:
        ring = ring.difference(q.footprint())
print("sofa 前方 300-900 环带内可用面积 = %.2f m2" % (ring.area / 1e6))
