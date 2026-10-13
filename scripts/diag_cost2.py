import os, sys, time
sys.path.insert(0, "packages/geometry")
from hkgeometry.house import load_flat
from hkgeometry import cpsat_solver as C
from hkgeometry.greedy import door_approach_zones
from hkgeometry.types import Placement, Layout

flat = load_flat("out/plan_hk_onpak05.json")
room = next(r for r in flat.rooms if r.id == "living")
zones = [z for _i, z in door_approach_zones(room)]
poly = room.usable_geo()
print("zones", len(zones), "poly area %.1f m2" % (poly.area / 1e6))

spec = [("#bed_main", "bed", 1370, 1900, 1490, 4430, 180),
        ("#wardrobe_main", "wardrobe", 1900, 600, 2490, 1230, 180),
        ("#sofa", "sofa", 1400, 700, 4990, 4930, 90),
        ("#coffee", "coffee_table", 700, 700, 2490, 30, 0),
        ("#tv", "tv_bench", 1200, 400, 2690, 3730, 0),
        ("#dining", "dining_table", 1400, 800, 3590, 4930, 90),
        ("#chair1", "chair", 450, 450, 2090, 3630, 180),
        ("#chair2", "chair", 450, 450, 4490, 5930, 180),
        ("#chair3", "chair", 450, 450, 2890, 5430, 180),
        ("#chair4", "chair", 450, 450, 2890, 5930, 270)]
ps = [Placement(inst=a, category=b, w=c, d=d, x=e, y=f, rot=g, h=500,
                visual={"opacity": 1.0, "lightness_L": 60}) for a, b, c, d, e, f, g in spec]
lay = Layout(room_id="living", placements=ps)
n = 400
t0 = time.time()
for _ in range(n):
    C._cheap_bad(room, ps, zones, poly)
dt = time.time() - t0
print("_cheap_bad  %.3f ms each" % (1000 * dt / n))
t0 = time.time()
for _ in range(20):
    C._narrow_artery_penalty(room, lay)
print("_narrow_artery_penalty %.3f ms each" % (1000 * (time.time() - t0) / 20))
b = poly.bounds
cands = ((b[2] - b[0]) // 300 + 1) * ((b[3] - b[1]) // 300 + 1) * 4
print("cands/item/pass", cands, " -> est pass time %.1f s" % (cands * len(ps) * dt / n))
print("artery evals/pass", C.ARTERY_TOPK * len(ps))
