import os, sys, json
ROOT = "/mnt/d/openclaw/hk-space"
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
os.chdir(ROOT)
import hkgeometry.cpsat_solver as CS
from hkgeometry.house import load_flat
from hkgeometry.greedy import GreedyWeights, door_approach_zones
from hkgeometry.exposure import exposure_map
from hkgeometry.corridor import default_corridors
from hkgeometry.types import Placement

flat = load_flat("out/plan_hk_a.json")
r = flat.room("bedroom")
print("BED-DBG-START")
xs=[p[0] for p in r.polygon]; ys=[p[1] for p in r.polygon]
print("  polygon", r.polygon)
print("  bbox %dx%d   面积 %.2f m2" % (max(xs)-min(xs), max(ys)-min(ys), r.geo().area/1e6))
for w in r.walls:
    print("  wall %-6s %-13s (%5d,%5d)-(%5d,%5d)" % (w.id, w.type, w.a[0],w.a[1],w.b[0],w.b[1]))
for o in r.openings:
    wl = next((x for x in r.walls if x.id==o.wall), None)
    L = ((wl.b[0]-wl.a[0])**2 + (wl.b[1]-wl.a[1])**2)**0.5 if wl else 0
    print("  open %-6s %-7s wall=%-5s center=%5d width=%4d wall_len=%5.0f %s" % (
        o.id, o.kind, o.wall, o.center_mm, o.width_mm, L,
        "" if (wl and 0<=o.center_mm<=L) else "<-- 超出墙长"))
doors = door_approach_zones(r); spines = CS.circulation_zones(r)
for n,z in doors+spines:
    print("  zone %-12s area %.2f m2 bounds %s" % (n, z.area/1e6, [round(v) for v in z.bounds]))
bed = Placement(inst="#bed", category="bed", w=1600, d=2100, h=400,
                visual=dict(opacity=1.0, lightness_L=60))
emap = exposure_map(r, cell_mm=50); cor = default_corridors(r)
W = GreedyWeights(exposure=20000.0)
for strict in (True, False):
    grids, nx, ny, nc = CS._cost_grids(r, [bed], emap, W, cor, doors, spines, [], [], strict_relations=strict)
    print("  strict=%s 可行格 %d" % (strict, len(grids["#bed"][1])))
# 逐条过滤
import shapely
from shapely.geometry import Point
poly = r.usable_geo(); shapely.prepare(poly)
minx,miny,maxx,maxy = poly.bounds
cnt = dict(total=0, inroom=0, noodoor=0, wall=0)
for rr in range(4):
    w,d = CS._rot_dims(bed, rr)
    nxx = int((maxx-minx)//50)+1; nyy = int((maxy-miny)//50)+1
    for j in range(nyy):
        for i in range(nxx):
            xx=minx+i*50; yy=miny+j*50
            box = shapely.box(xx,yy,xx+w,yy+d)
            cnt["total"]+=1
            if not poly.covers(box): continue
            cnt["inroom"]+=1
            if any(z.intersects(box) for _n,z in doors+spines): continue
            cnt["noodoor"]+=1
            if box.distance(poly.boundary) > CS.WALL_HARD_MM: continue
            cnt["wall"]+=1
print("  逐条:", cnt)
print("BED-DBG-END")
