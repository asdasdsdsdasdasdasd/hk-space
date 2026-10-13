import os, sys, time
sys.path.insert(0, "packages/geometry")
sys.path.insert(0, "scripts")
from hkgeometry.house import load_flat, solve_house
from hkgeometry import cpsat_solver as C
from hkgeometry.greedy import door_approach_zones
from bench_hk import CASES, build_items
case = CASES["onpak05"]
flat = load_flat(case["plan"])
items = build_items(case)
assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
t0 = time.time()
house = solve_house(flat, items, time_limit_s=40.0, parallel=True, assignment=assign)
print("solve %.1fs" % (time.time() - t0))
room = next(r for r in flat.rooms if r.id == "living")
lay = house.by_room["living"]
zones = [z for _i, z in door_approach_zones(room)]
poly = room.usable_geo()
ps = list(lay.placements)
n = 200
t0 = time.time()
for _ in range(n):
    C._cheap_bad(room, ps, zones, poly)
dt = time.time() - t0
print("_cheap_bad x%d = %.3fs (%.2f ms each)" % (n, dt, 1000 * dt / n))
t0 = time.time()
for _ in range(20):
    C._narrow_artery_penalty(room, lay)
print("_narrow_artery_penalty x20 = %.3fs" % (time.time() - t0))
b = poly.bounds
print("zones", len(zones), "cands/pass:", ((b[2]-b[0])//300+1)*((b[3]-b[1])//300+1)*4*len(ps))
print("placements", [(p.inst, p.x, p.y, p.w, p.d, p.rot) for p in ps])
