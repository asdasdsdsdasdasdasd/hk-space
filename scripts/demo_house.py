#!/usr/bin/env python3
"""整屋 Demo —— 户型图 -> 5 个房间分别选品/摆位 -> 3D 娃娃屋。

三层分解（方案 §3.1）：
    Level 1  房间功能分配（哪件家具进哪个房）
    Level 2  每间房独立 CP-SAT 求解（★ 多进程并行）
    Level 3  跨房间校验（可达性 / 门洞 / 整屋视觉负荷）

运行：python3 scripts/demo_house.py
"""
from __future__ import annotations

import json
import os
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from hkgeometry.exposure import exposure_map
from hkgeometry.greedy import GreedyWeights
from hkgeometry.house import (assign_rooms, load_flat, solve_house, verify_house)
from hkgeometry.livability import check_livability
from hkgeometry.spaciousness import spaciousness_report
from hkgeometry.types import Placement
from hkgeometry.viz import setup_font

OUT = os.path.join(ROOT, "out")
PLAN = os.path.join(OUT, "plan_hk_a.json")
CATALOG = os.path.join(ROOT, "data", "catalog", "seed_skus.json")

# 整屋家具清单：一个 39m² 的两房单位（客廳 / 睡房 / 廚房 / 浴室 / 儲物房）
WISHLIST = [
    # (inst, category, 最大宽, 最大深, 取重/轻)
    ("#sofa", "sofa", 1750, 950, "heavy"),
    ("#tv_bench", "tv_bench", 1800, 450, "light"),
    ("#coffee_table", "coffee_table", 950, 550, "light"),
    ("#shoe_cabinet", "shoe_cabinet", 900, 400, "light"),
    ("#dining_table", "dining_table", 1300, 800, "light"),
    ("#bed", "bed", 1600, 2100, "heavy"),
    ("#wardrobe", "wardrobe", 1250, 650, "heavy"),
    ("#nightstand", "side_table", 500, 450, "light"),
    ("#desk", "desk", 1300, 650, "light"),
    ("#fridge", "fridge", 650, 700, "heavy"),
    ("#washer", "washing_machine", 650, 650, "light"),
    ("#microwave", "microwave", 600, 500, "light"),
    ("#cabinet", "cabinet", 900, 500, "light"),
    # ★ 固定洁具（由來去水位 / 煤氣喉钉死）
    ("#toilet", "toilet", 450, 750, "light"),
    ("#basin", "basin", 650, 500, "light"),
    ("#shower", "shower", 950, 950, "light"),
    ("#stove", "stove", 650, 560, "light"),
    ("#range_hood", "range_hood", 800, 520, "light"),
    ("#counter", "kitchen_counter", 1300, 650, "light"),
    ("#mirror", "mirror", 700, 200, "light"),
    ("#shelf", "shelf", 900, 450, "light"),
    ("#drying_rack", "drying_rack", 1200, 600, "light"),
    ("#storage_box", "storage_box", 700, 500, "light"),
]


def pick_sku(cat, category, mw, md, want):
    cands = []
    for s in cat:
        if s["category"] != category:
            continue
        w, d = s["dims"]["w"], s["dims"]["d"]
        if (w <= mw and d <= md) or (w <= md and d <= mw):
            cands.append(s)
    if not cands:
        return None
    cands.sort(key=lambda s: s["visual"]["visual_mass_index"], reverse=(want == "heavy"))
    return cands[0]


def main():
    setup_font()
    os.makedirs(OUT, exist_ok=True)
    flat = load_flat(PLAN)
    funcs = flat.functions()
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)["skus"]
    # 固定洁具单独一份目录（它们不是可移动家具）
    fix_path = os.path.join(ROOT, "data", "catalog", "fixtures.json")
    if os.path.exists(fix_path):
        with open(fix_path, encoding="utf-8") as f:
            cat = cat + json.load(f)["skus"]

    print("=== 戶型: %s   %d 個房間   總面積 %.1f m²"
          % (flat.id, len(flat.rooms), flat.total_area_m2()))
    for r in flat.rooms:
        print("    %-9s %-6s %-8s %5.1f m²   %d x %d"
              % (r.id, r.label, funcs.get(r.id, "?"), r.geo().area / 1e6,
                 max(p[0] for p in r.polygon) - min(p[0] for p in r.polygon),
                 max(p[1] for p in r.polygon) - min(p[1] for p in r.polygon)))

    items = []
    print("=== 從 150 條 SKU 庫選品")
    for inst, c, mw, md, want in WISHLIST:
        s = pick_sku(cat, c, mw, md, want)
        if s is None:
            print("    [miss] %-14s 庫裡沒有塞得下的 %s" % (inst, c))
            continue
        items.append(Placement(inst=inst, sku=s["id"], category=s["category"],
                               w=s["dims"]["w"], d=s["dims"]["d"], h=s["dims"]["h"],
                               visual=dict(s["visual"]), price_hkd=s.get("price_hkd", 0)))
    print("    共 %d 件  合計 $%d" % (len(items), sum(p.price_hkd for p in items)))

    # ---- Level 1
    print("=== Level 1 房間功能分配")
    t0 = time.time()
    assign, _ = assign_rooms(flat, items, verbose=True)
    t1 = time.time()
    print("    用時 %.2fs" % (t1 - t0))

    # ---- Level 2（并行）
    print("=== Level 2 每間房並行求解 CP-SAT")
    t2 = time.time()
    house = solve_house(flat, items, weights=GreedyWeights(exposure=20000.0),
                        time_limit_s=40.0, max_workers=5, parallel=True, verbose=True)
    t3 = time.time()
    house.assignment = assign
    print("    用時 %.1fs（%d 間房並行）" % (t3 - t2, len(flat.rooms)))

    # 宜居检查
    print("=== 宜居檢查（每間房）")
    for r in flat.rooms:
        lay = house.by_room.get(r.id)
        if lay is None or not lay.placements:
            continue
        lv = check_livability(r, lay)
        print("    %-9s %-14s %s" % (r.id, funcs.get(r.id, "?"), lv.summary()))
        for v in lv.hard[:3]:
            print("        [%s] %s" % (v.severity[:4], v.detail))

    # ---- 顧問意見（LLM 下場的地方）
    from hkgeometry.advisory import advise, render_for_customer
    want_by_room = {}
    for it in items:
        rid = assign.get(it.inst)
        if rid:
            want_by_room.setdefault(rid, []).append(it)
    advices = advise(flat, house, want_by_room)
    print("=== 顧問意見（裝不下 / 擺不好的地方）")
    if advices:
        print(render_for_customer(advices))
    else:
        print("    所有家具都放得下，動線也通。")

    # ---- Level 3
    print("=== Level 3 跨房間校驗")
    rep = verify_house(flat, house, verbose=True)
    print("    可達房間: %s" % ", ".join(rep.reachable))
    if rep.unreachable:
        print("    ⚠️ 不可達: %s" % ", ".join(rep.unreachable))
    for rid, blk in rep.door_blocked.items():
        flag = "✅" if blk <= 1 else "❌"
        print("    %-9s 門洞走廊被占 %6.0f cm² %s" % (rid, blk, flag))
    print("    家具 %d/%d 件已就位   整屋視覺負荷 %.3f"
          % (rep.n_placed, rep.n_items, rep.house_visual_load))
    for msg in rep.issues:
        print("    ⚠️ %s" % msg)

    # 保存
    dump = {}
    for rid, lay in house.by_room.items():
        r = flat.room(rid)
        dump[rid] = {
            "func": funcs.get(rid, "?"),
            "score": None if str(house.scores.get(rid)) == "nan" else house.scores.get(rid),
            "placements": [{"inst": p.inst, "sku": p.sku, "category": p.category,
                            "x": p.x, "y": p.y, "rot": p.rot,
                            "w": p.w, "d": p.d, "h": p.h, "price": p.price_hkd}
                           for p in lay.placements],
        }
    with open(os.path.join(OUT, "house_layout.json"), "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)
    print("-> out/house_layout.json")


if __name__ == "__main__":
    main()
