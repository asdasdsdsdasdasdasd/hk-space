"""校准脚本：扫曝光度参数 + 显大分常量，看 BAD/GOOD 落在什么分数。"""
import os, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import shapely
from hkgeometry import demo as D
from hkgeometry.exposure import exposure_map, layout_mass_exposure, mass_exposure_breakdown
from hkgeometry.isovist import isovist, key_viewpoints, occlusion_ratio
from hkgeometry.corridor import default_corridors, corridor_reports

room = D.demo_room()
poly = room.usable_geo()
corridors = default_corridors(room)
ent = room.entrance_point()

# 只用品質視點做曝光度：入口 + 幾何中心 + 窗（不含沙發/床位）
vp_env = [ent]
c = poly.representative_point(); vp_env.append((int(c.x), int(c.y)))
vp_env.extend(room.window_centers())

for w_ent, d0 in itertools.product([3.0, 6.0, 12.0], [600.0, 1000.0, 1800.0]):
    em = exposure_map(room, viewpoints=vp_env, cell_mm=50,
                      entrance_weight=w_ent, d0_mm=d0)
    lo = em.low_exposure_mask().sum() / max(1, em.inside.sum())
    me_bad = layout_mass_exposure(D.build("r", D.BAD), em)
    me_good = layout_mass_exposure(D.build("r", D.GOOD), em)
    print("w_ent=%-5.1f d0=%-6.0f  low-exp=%4.1f%%  mean=%.2f  mass_exp bad=%.3f good=%.3f  gap=%+.3f"
          % (w_ent, d0, lo*100, em.mean_exposure(), me_bad, me_good, me_good - me_bad))

print()
print("--- 各方案原始指标（用 w_ent=6, d0=1000）---")
em = exposure_map(room, viewpoints=vp_env, cell_mm=50, entrance_weight=6.0, d0_mm=1000.0)
for name, poses in (("BAD", D.BAD), ("GOOD", D.GOOD)):
    lay = D.build("r", poses)
    fps = lay.footprints()
    isos = [isovist(poly, fps, vp, 360) for vp in key_viewpoints(room, lay)]
    isos = [i for i in isos if not i.is_empty]
    vfr = shapely.union_all(isos).area / poly.area
    occ_ent = occlusion_ratio(poly, lay.placements, ent)
    occ_all = float(np.mean([occlusion_ratio(poly, lay.placements, vp)
                             for vp in key_viewpoints(room, lay)]))
    creps = corridor_reports(corridors, lay.placements)
    cpen = float(np.mean([r["penalty_norm"] for r in creps]))
    best_sight = max(shapely.Point(vp).distance(shapely.union_all(isos).boundary) for vp in [ent])
    sight_ratio = max(i.length for i in isos) / (2 * room.diagonal_mm())
    me = layout_mass_exposure(lay, em)
    print("%-5s vfr=%.3f  occ_ent=%.3f  occ_all=%.3f  cpen=%.4f  mass_exp=%.3f"
          % (name, vfr, occ_ent, occ_all, cpen, me))
    print("      breakdown:", [(r["inst"], r["visual_mass"], r["exposure"]) for r in mass_exposure_breakdown(lay, em)])
