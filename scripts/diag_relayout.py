#!/usr/bin/env python3
"""为什么坐标下降动不了？把 relayout_search 的内部决策打出来。"""
from __future__ import annotations
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, HERE)
from hkgeometry import cpsat_solver as C
from hkgeometry.house import load_flat, solve_house
from hkgeometry.types import Layout
from hkgeometry.greedy import door_approach_zones
from bench_hk import CASES, build_items

_orig = C._narrow_artery_penalty
COUNT = [0]
def counted(room, lay, step=150):
    COUNT[0] += 1
    return _orig(room, lay, step=step)
C._narrow_artery_penalty = counted

case = CASES["onpak05"]
flat = load_flat(os.path.join(ROOT, case["plan"]))
items = build_items(case)
assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
house = solve_house(flat, items, time_limit_s=40.0, parallel=True, assignment=assign)
room = next(r for r in flat.rooms if r.id == "living")
lay = house.by_room["living"]
zones = [z for _i, z in door_approach_zones(room)]
poly = room.usable_geo()
cur = list(lay.placements)
print("items:", [(p.inst, p.x, p.y, p.rot) for p in cur])
print("cur_bad", C._cheap_bad(room, cur, zones, poly))
print("cur_art", _orig(room, lay))
print("min_corridor", C.min_corridor_width(room, lay))
t0 = time.time()
COUNT[0] = 0
new = C.relayout_search(room, lay)
print("relayout took %.1fs, artery evals %d" % (time.time() - t0, COUNT[0]))
print("new_bad", C._cheap_bad(room, list(new.placements), zones, poly))
print("new_art", _orig(room, new))
print("new min_corridor", C.min_corridor_width(room, new))
print("moved:", [(a.inst, (a.x, a.y, a.rot), (b.x, b.y, b.rot))
                 for a, b in zip(cur, list(new.placements)) if (a.x, a.y, a.rot) != (b.x, b.y, b.rot)])
