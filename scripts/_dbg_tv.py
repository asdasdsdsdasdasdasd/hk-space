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
doors = door_approach_zones(room); spines = CS.circulation_zones(room)
items = {p.inst: p for p in D.item_list()}
tv = items["#tv_bench"]
print("DBG2-START")
for label, anchors, strict in (("无锚点+严格", [], True), ("无锚点+软", [], False)):
    grids, nx, ny, nc = CS._cost_grids(room, [tv], emap, W, cor, doors, spines, anchors, [], strict_relations=strict)
    print("  %-14s 可行格 %d" % (label, len(grids[tv.inst][1])))
# 逐条过滤条件看谁在杀它
poly = room.usable_geo(); import shapely; shapely.prepare(poly)
minx, miny, maxx, maxy = poly.bounds
nx = int((maxx-minx)//50)+1; ny = int((maxy-miny)//50)+1
from shapely.geometry import Point, LineString
cnt = dict(total=0, inroom=0, nodoor=0, wall=0, depth=0)
for r in range(4):
    w, d = CS._rot_dims(tv, r)
    fx, fy = CS.front_dir(r*90)
    for j in range(ny):
        for i in range(nx):
            xx = minx + i*50; yy = miny + j*50
            box = shapely.box(xx, yy, xx+w, yy+d)
            cnt["total"] += 1
            if not poly.covers(box): continue
            cnt["inroom"] += 1
            if any(z.intersects(box) for _n, z in doors+spines): continue
            cnt["nodoor"] += 1
            if tv.category in CS.BACK_TO_WALL_HARD and box.distance(poly.boundary) > CS.WALL_HARD_MM: continue
            cnt["wall"] += 1
            need = CS.FRONT_DEPTH_MIN.get(tv.category)
            lcx, lcy = xx+w/2, yy+d/2
            hf = (d if fy != 0 else w)/2
            fpx, fpy = lcx+fx*hf, lcy+fy*hf
            probe = LineString([(fpx,fpy),(fpx+fx*(need+200), fpy+fy*(need+200))])
            hit = probe.intersection(poly.boundary)
            if hit.is_empty or hit.distance(Point(fpx,fpy))+1 < need: continue
            cnt["depth"] += 1
print("  逐条过滤:", cnt)
# 看一个应该可行的位置
for (x, y, rr) in [(0,180,3),(2800,180,1),(0,100,3)]:
    w,d = CS._rot_dims(tv, rr); fx,fy = CS.front_dir(rr*90)
    box = shapely.box(x,y,x+w,y+d)
    lcx,lcy = x+w/2, y+d/2; hf=(d if fy!=0 else w)/2
    fpx,fpy = lcx+fx*hf, lcy+fy*hf
    probe = LineString([(fpx,fpy),(fpx+fx*2600, fpy+fy*2600)])
    hit = probe.intersection(poly.boundary)
    print("  测试 (%d,%d) r=%d: covers=%s doorhit=%s wallgap=%.0f depth=%s" % (
        x,y,rr, poly.covers(box), any(z.intersects(box) for _n,z in doors+spines),
        box.distance(poly.boundary), round(hit.distance(Point(fpx,fpy))) if not hit.is_empty else "no-hit"))
print("DBG2-END")
