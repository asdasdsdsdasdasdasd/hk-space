#!/usr/bin/env python3
"""端到端 Demo：一张户型图 → 3D 娃娃屋 + 显大诊断。

完整链路：
    户型图 PNG
      -> services/floorplan 矢量化        -> Room JSON
      -> 取一个房间 + 从 SKU 库选品
      -> 贪心放置（两种目标函数：没优化过 / 优化过）
      -> hkgeometry 显大诊断
      -> Blender 3D 娃娃屋

运行：python3 scripts/demo_end2end.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from hkgeometry.corridor import default_corridors
from hkgeometry.exposure import exposure_map
from hkgeometry.greedy import GreedyWeights, solve, door_approach_zones
from hkgeometry.cpsat_solver import solve_cpsat
from hkgeometry.spaciousness import spaciousness_report
from hkgeometry.types import HeightPatch, Opening, Placement, Room, Wall
from hkgeometry.viz import setup_font

import render_dollhouse as RD

OUT = os.path.join(ROOT, "out")
PLAN_JSON = os.path.join(OUT, "plan_hk_a.json")
CATALOG = os.path.join(ROOT, "data", "catalog", "seed_skus.json")
ROOM_ID = "living"


def load_room(path, room_id):
    """图纸坐标 (+y 向下) -> hkgeometry 坐标 (y 向上, 原点在自己包围盒左下)。"""
    with open(path, encoding="utf-8") as f:
        flat = json.load(f)
    src = next(r for r in flat["rooms"] if r["id"] == room_id)

    raw_poly = [(int(x), int(y)) for x, y in src["polygon"]]
    raw_walls = [(w["id"], (int(w["a"][0]), int(w["a"][1])),
                  (int(w["b"][0]), int(w["b"][1])),
                  w.get("type", "partition"), int(w.get("thickness_mm", 100) or 100))
                 for w in src.get("walls", [])]
    raw_hf = [(hp["z_max"], hp.get("kind", "beam"),
               [(int(x), int(y)) for x, y in hp["region"]])
              for hp in (src.get("height_field") or [])]

    pts = list(raw_poly) + [p for _, a, b, _, _ in raw_walls for p in (a, b)]
    pts += [p for _, _, _, region in raw_hf for p in region]
    if not pts:
        raise ValueError("room has no geometry")
    # 先翻 y（图纸 +y 向下 -> 数学 +y 向上），再平移到自己的包围盒左下角
    flipped = [(p[0], -p[1]) for p in pts]
    fx = min(p[0] for p in flipped)
    fy = min(p[1] for p in flipped)

    def T(p):
        return (p[0] - fx, -p[1] - fy)

    poly = [T(p) for p in raw_poly]
    walls = [Wall(wid, T(a), T(b), typ, th) for wid, a, b, typ, th in raw_walls]
    hf = [HeightPatch([T(p) for p in region], z, kind) for z, kind, region in raw_hf]

    openings = []
    for o in src.get("openings", []):
        openings.append(Opening(o["id"], o.get("wall", ""), o.get("kind", "door"),
                                int(o.get("center_mm", 0)), int(o.get("width_mm", 800)),
                                int(o.get("height_mm", 2000) or 2000),
                                int(o.get("sill_h_mm", 0) or 0),
                                int(o.get("sill_depth_mm", 0) or 0)))
    room = Room(id=src["id"], label=src.get("label", ""), polygon=poly,
                height_mm=int(src.get("height_mm", 2450) or 2450),
                walls=walls, openings=openings, height_field=hf)
    return room, flat


def poly_area_m2(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0 / 1e6


def pick_sku(catalog, category, max_w, max_d, want):
    cands = []
    for s in catalog:
        if s["category"] != category:
            continue
        w, d = s["dims"]["w"], s["dims"]["d"]
        if (w <= max_w and d <= max_d) or (w <= max_d and d <= max_w):
            cands.append(s)
    if not cands:
        return None
    cands.sort(key=lambda s: s["visual"]["visual_mass_index"], reverse=(want == "heavy"))
    return cands[0]


WISHLIST = [
    ("#wardrobe", "wardrobe", 1200, 600, "heavy"),
    ("#sofa", "sofa", 1700, 950, "heavy"),
    ("#tv_bench", "tv_bench", 1800, 420, "heavy"),
    ("#coffee_table", "coffee_table", 950, 550, "heavy"),
    ("#shelf", "shelf", 890, 390, "light"),
]


def main():
    setup_font()
    os.makedirs(OUT, exist_ok=True)
    room, flat = load_room(PLAN_JSON, ROOM_ID)
    corridors = default_corridors(room)
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)["skus"]

    xs = [p[0] for p in room.polygon]
    ys = [p[1] for p in room.polygon]
    print("=== 户型图: %s" % os.path.basename(PLAN_JSON))
    print("    识别置信度 %.3f   比例尺 %.1f mm/px (来源 %s)   倾斜矫正 %.2f deg"
          % (flat["meta"]["confidence"], flat["meta"]["mm_per_px"],
             flat["meta"]["scale_source"], flat["meta"].get("deskew_deg", 0.0)))
    print("    全屋 %d 个房间: %s"
          % (len(flat["rooms"]),
             "  ".join("%s%s %.1fm2" % (r["id"], "(" + r["label"] + ")" if r.get("label") else "",
                                        poly_area_m2([(x, y) for x, y in r["polygon"]]))
                       for r in flat["rooms"])))
    print("=== 取「%s」: %d x %d mm   层高 %d   墙 %d 段   开口 %d"
          % (room.label, max(xs) - min(xs), max(ys) - min(ys), room.height_mm,
             len(room.walls), len(room.openings)))

    items = []
    print("=== 从 SKU 库选品")
    for inst, c, mw, md, want in WISHLIST:
        s = pick_sku(cat, c, mw, md, want)
        if s is None:
            print("    [miss] %-14s 没有塞得下的 SKU" % inst)
            continue
        items.append(Placement(inst=inst, sku=s["id"], category=s["category"],
                               w=s["dims"]["w"], d=s["dims"]["d"], h=s["dims"]["h"],
                               visual=dict(s["visual"]), price_hkd=s.get("price_hkd", 0)))
        print("    %-14s %-22s %4dx%4dx%4d  vmi=%.3f  $%d"
              % (inst, s["name_zh"], s["dims"]["w"], s["dims"]["d"], s["dims"]["h"],
                 s["visual"]["visual_mass_index"], s.get("price_hkd", 0)))
    print("    合计 $%d" % sum(p.price_hkd for p in items))

    emap = exposure_map(room, cell_mm=50)
    print("=== 曝光度图 %s   视点 %d   低曝光格 %.0f%%"
          % (emap.exposure.shape, emap.n_viewpoints,
             100.0 * emap.low_exposure_mask().sum() / max(1, emap.inside.sum())))

    BASE = dict(exposure=0.0, wall_contact=0.0, corridor=0.0, occlusion=0.0)
    variants = []
    for tag, title, w in (
        ("e2e_naive", "沒優化過（只保證放得下 + 朝向）", GreedyWeights(**BASE)),
        ("e2e_opt", "優化過（體量往低曝光區推）",
         GreedyWeights(exposure=20000.0)),
    ):
        print("=== %s" % title)
        lay, info = solve_cpsat(room, items, emap=emap, weights=w,
                                corridors=corridors, time_limit_s=15.0, verbose=True)
        print("    solver: %s" % info)
        for it in lay.placements:
            fx, fy = it.front_dir()
            arrow = {(0, 1): "上", (-1, 0): "左", (0, -1): "下", (1, 0): "右"}[(fx, fy)]
            print("    %-14s (%5d,%5d) rot=%3d  正面朝 %s"
                  % (it.inst, it.x, it.y, it.rot, arrow))
        rep = spaciousness_report(room, lay, emap)
        print("    -> %s    視覺負荷 %.3f    放了 %d/%d 件"
              % (rep.summary(), rep.metrics["visual_load"],
                 len(lay.placements), len(items)))
        zones = door_approach_zones(room)
        blk = sum(p.footprint().intersection(z).area
                  for p in lay.placements for _i, z in zones) / 1e4
        print("    門洞走廊被占: %.0f cm2 %s" % (blk, "✅ 全通" if blk < 1 else "❌ 擋門"))
        for d in rep.detractors[:3]:
            print("       [!] %s: %s" % (d["issue"], d["detail"]))
        variants.append((tag, title, lay, rep))

    best_tag, best_title, best_lay, best_rep = max(variants, key=lambda v: v[3].score)
    for tag, title, lay, rep in variants:
        sc = RD.build_scene(room, lay, tag, "%s · %s" % (title, rep.summary()))
        RD.render(sc, os.path.join(OUT, "dollhouse_%s.png" % tag))
        print("    3D -> out/dollhouse_%s.png" % tag)

    # 一张 2D 平面图给海报用
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from hkgeometry.viz import _draw_exposure, _draw_plan
    from hkgeometry.corridor import corridor_reports
    fig = plt.figure(figsize=(9.0, 4.9), dpi=100, facecolor="white")
    a1 = fig.add_axes([0.02, 0.03, 0.46, 0.94])
    a2 = fig.add_axes([0.51, 0.03, 0.47, 0.94])
    _draw_plan(a1, room, best_lay, corridors,
               {"corridors": corridor_reports(corridors, best_lay.placements)})
    _draw_exposure(a2, emap, best_lay)
    fig.savefig(os.path.join(OUT, "e2e_plan.png"), facecolor="white")
    plt.close(fig)
    print("    2D -> out/e2e_plan.png")

    with open(os.path.join(OUT, "e2e_summary.json"), "w", encoding="utf-8") as f:
        json.dump({t: {"title": ti, "score": round(r.score, 1), "metrics": r.metrics,
                       "n_placed": len(l.placements), "n_items": len(items)}
                   for t, ti, l, r in variants}, f, ensure_ascii=False, indent=1)
    print("-> out/e2e_summary.json")


if __name__ == "__main__":
    main()
