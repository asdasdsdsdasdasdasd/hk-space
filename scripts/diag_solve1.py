import os, sys, time
sys.path.insert(0, "packages/geometry")
from hkgeometry.house import load_flat
from hkgeometry import cpsat_solver as C
from hkgeometry.types import Layout
from bench_hk import CASES, build_items

flat = load_flat(CASES["onpak05"]["plan"])
items = build_items(CASES["onpak05"])
room = next(r for r in flat.rooms if r.id == "living")
mine = [p for p in items if p.inst in
        {"#bed_main", "#wardrobe_main", "#sofa", "#coffee", "#tv", "#dining",
         "#chair1", "#chair2", "#chair3", "#chair4"}]
print("items in living:", len(mine))
for tm in (20.0,):
    t0 = time.time()
    lay, info = C.solve_cpsat(room, mine, time_limit_s=tm)
    print("solve_room(%.0fs) took %.1fs  %s  -> %d placements"
          % (tm, time.time() - t0, info, len(lay.placements)))
    print("   min_corridor", C.min_corridor_width(room, lay))
