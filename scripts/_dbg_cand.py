import os, sys
ROOT = "/mnt/d/openclaw/hk-space"
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
import hkgeometry.cpsat_solver as CS
from hkgeometry import demo as D
from hkgeometry.greedy import GreedyWeights, door_approach_zones
from hkgeometry.exposure import exposure_map
from hkgeometry.corridor import default_corridors

room = D.demo_room(); emap = exposure_map(room, cell_mm=50); cor = default_corridors(room)
W = GreedyWeights(exposure=20000.0)
doors = door_approach_zones(room)
spines = CS.circulation_zones(room)
print("DBG-START")
print("room usable area %.2f m2" % (room.usable_geo().area/1e6))
for n, z in doors + spines:
    print("  zone %-14s area %.2f m2  bounds %s" % (n, z.area/1e6, [round(v) for v in z.bounds]))
placed = []
for it in D.item_list():
    grids, nx, ny, ncells = CS._cost_grids(room, [it], emap, W, cor, doors, spines, placed, [])
    g, allowed, wr, dr = grids[it.inst]
    print("  %-14s %-11s %4dx%-4d  可行格 %5d" % (it.inst, it.category, it.w, it.d, len(allowed)))
    p = CS._place_anchor(room, it, grids, nx, ny, ncells)
    if p is None:
        print("       -> 放不下")
    else:
        print("       -> (%d,%d) r=%d" % (p.x, p.y, p.rot))
        placed.append(p)
# 单独看沙发：不带任何已放置家具
grids, nx, ny, ncells = CS._cost_grids(room, [D.item_list()[0]], emap, W, cor, doors, spines, [], [])
print("  沙发 无阻碍时可行格:", len(grids[D.item_list()[0].inst][1]))
print("DBG-END")
