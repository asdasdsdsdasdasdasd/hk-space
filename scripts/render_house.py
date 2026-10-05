#!/usr/bin/env python3
"""整屋 3D 娃娃屋 —— 把一个户型的所有房间一起渲染。

运行：python3 scripts/render_house.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.house import load_flat

OUT = os.path.join(ROOT, "out")
BLENDER = "/mnt/d/openclaw/tools/blender-4.2.1-linux-x64/blender"
TEMPLATES = os.path.join(OUT, "templates")

TPL = {
    "sofa": "sofa_2", "chaise_longue": "chaise_longue", "tv_bench": "tv_console",
    "coffee_table": "side_table", "dining_table": "dining_table",
    "side_table": "side_table", "chair": "chair", "shoe_cabinet": "shoe_cabinet",
    "cabinet": "cabinet_2door", "wardrobe": "wardrobe_tall", "shelf": "shelf_unit",
    "desk": "desk", "bed": "bed_double", "storage_bed": "bed_storage",
    "bunk_bed": "bunk_bed", "loft_bed": "bed_loft", "fridge": "fridge",
    "washing_machine": "washer", "microwave": "microwave", "mirror": "mirror",
    "storage_box": "storage_box", "drying_rack": "drying_rack",
    "drawer_chest": "drawer_chest", "wall_shelf": "wall_shelf",
    "screen": "screen_divider", "pegboard": "pegboard", "aircon": "air_conditioner",
    "open_shelf": "open_shelf",
    "toilet": "toilet", "basin": "basin", "bathtub": "bathtub",
    "shower": "shower", "stove": "stove", "range_hood": "range_hood",
    "kitchen_counter": "kitchen_counter",
}
FAMILY_TINT = {"warm": (1.00, 0.68, 0.35), "cool": (0.55, 0.72, 1.00),
               "neutral": (0.96, 0.92, 0.82)}

# 按品类再做一点色相偏移 + 明度阻尼 —— 否则全屋都是一个米白，
# 渲染出来像一堆纸盒，看不出哪件是什么。
# ★ 品类专属色板。
# 为什么需要：目录里「雪柜 / 洗衣機 / 微波爐」三件的 lightness_L 都是 80、
# color_family 都是 neutral —— 算出来 rgb 分别是 (0.55,0.54,0.50)、
# (0.56,0.55,0.50)、(0.58,0.56,0.50)，肉眼完全分不出。
# 渲染要的是「一眼看得出这是冰箱、那是洗衣机」，所以按品类给差异化的色调。
CAT_TINT = {
    # 客廳
    "sofa":           (0.62, 0.58, 0.54),   # 布艺暖灰
    "chaise_longue":  (0.60, 0.57, 0.55),
    "tv_bench":       (0.44, 0.30, 0.20),   # 胡桃木
    "coffee_table":   (0.72, 0.62, 0.46),   # 浅木
    "dining_table":   (0.68, 0.58, 0.44),
    "side_table":     (0.66, 0.56, 0.42),
    "shoe_cabinet":   (0.56, 0.42, 0.28),
    "drying_rack":    (0.78, 0.80, 0.83),   # 金属灰
    "screen":         (0.74, 0.66, 0.52),
    # 睡房
    "bed":            (0.52, 0.50, 0.48),
    "wardrobe":       (0.38, 0.26, 0.16),   # 深胡桃
    "desk":           (0.70, 0.61, 0.46),
    "drawer_chest":   (0.64, 0.54, 0.40),
    # 廚房
    "fridge":         (0.90, 0.91, 0.93),   # 亮白
    "washing_machine": (0.72, 0.75, 0.79),  # 中灰
    "microwave":      (0.36, 0.37, 0.40),   # 深灰
    "stove":          (0.30, 0.31, 0.34),   # 灶台深色
    "range_hood":     (0.68, 0.70, 0.74),   # 不锈钢
    "kitchen_counter": (0.60, 0.63, 0.67),
    "cabinet":        (0.62, 0.50, 0.36),
    # 浴室
    "toilet":         (0.92, 0.93, 0.95),
    "basin":          (0.90, 0.92, 0.94),
    "shower":         (0.70, 0.84, 0.90),   # 玻璃蓝
    "bathtub":        (0.91, 0.93, 0.95),
    "mirror":         (0.78, 0.86, 0.92),
    # 收納
    "shelf":          (0.72, 0.64, 0.50),
    "storage_box":    (0.66, 0.62, 0.56),
    "wall_shelf":     (0.74, 0.66, 0.52),
    "pegboard":       (0.70, 0.62, 0.48),
}
# 明度阻尼：原来的 0.18+0.66L 让明度 80-94 的家具全落在 0.71-0.79，
# 全挤成一团白。改成 0.16+0.56L，并把上限压到 0.72，拉开层次。
def color_for(v, cat):
    """品类色板为主，SKU 的明度只做微调 —— 保证同类之间一眼可分。"""
    base = CAT_TINT.get(cat)
    if base is None:
        L = max(0.0, min(1.0, float(v.get("lightness_L", 60)) / 100.0))
        b = min(0.72, 0.16 + 0.56 * L)
        t = FAMILY_TINT.get(v.get("color_family", "neutral"),
                            FAMILY_TINT["neutral"])
        base = (b * t[0], b * t[1], b * t[2])
    else:
        # 明度做 ±12% 的微调，同品类的不同 SKU 也有差别
        L = float(v.get("lightness_L", 70)) / 100.0
        k = 0.88 + 0.24 * L
        base = (base[0] * k, base[1] * k, base[2] * k)
    rgb = [min(1.0, c) for c in base]
    if float(v.get("opacity", 1.0)) < 0.35:
        rgb = [min(1.0, c * 1.15 + 0.10) for c in rgb]
        return [rgb[0], rgb[1], rgb[2], 0.32]
    return [rgb[0], rgb[1], rgb[2], 1.0]


def main(plan_path: str = "", layout_path: str = "", out_png: str = "",
         tag: str = ""):
    """★ 戶型同布局都必須可以指定。

    踩過的坑（致命）：這裡以前寫死 plan_hk_a.json，而 house_layout.json 卻係
    由呼叫方寫入嘅。於是跑 plan_hk_b 嘅時候，渲染用嘅係 **plan_hk_a 嘅牆**，
    配上 plan_hk_b 嘅家具坐標 —— 出來嗰張圖睇落一切正常（有房有家具有標籤），
    實際上係一個唔存在嘅平面圖。**錯得嚟仲要好靚，係最危險嘅一種 bug。**
    """
    flat = load_flat(plan_path or os.path.join(OUT, "plan_hk_a.json"))
    with open(layout_path or os.path.join(OUT, "house_layout.json"),
              encoding="utf-8") as f:
        house = json.load(f)
    print("[render] 戶型 %s  布局 %s" % (os.path.basename(flat.id or ""),
                                        os.path.basename(layout_path or "house_layout.json")))
    if flat.id:
        print("[render] flat id = %s，%d 間房" % (flat.id, len(flat.rooms)))

    rooms = []
    for r in flat.rooms:
        h = house.get(r.id)
        if not h:
            continue
        pls = []
        for p in h["placements"]:
            v = {}
            pls.append({"inst": p["inst"], "template": TPL.get(p["category"], "cabinet_2door"),
                        "x": p["x"], "y": p["y"], "rot": p["rot"],
                        "w": p["w"], "d": p["d"], "h": p["h"],
                        "color": color_for(v, p["category"])})  # 颜色稍后按真实 SKU 覆盖
        rooms.append({
            "id": r.id, "label": r.label, "func": h["func"],
            "polygon": [[int(x), int(y)] for x, y in r.polygon],
            "height_mm": r.height_mm,
            "walls": [{"a": list(w.a), "b": list(w.b), "type": w.type} for w in r.walls],
            "openings": [{"id": o.id, "wall": o.wall, "kind": o.kind,
                          "center_mm": o.center_mm, "width_mm": o.width_mm,
                          "sill_h_mm": o.sill_h_mm} for o in r.openings],
            "placements": pls,
        })

    # 颜色：从 catalog 取真实 SKU 的 visual 属性
    cat = {s["id"]: s for s in json.load(open(os.path.join(ROOT, "data/catalog/seed_skus.json"),
                                              encoding="utf-8"))["skus"]}
    for r in rooms:
        for p in r["placements"]:
            pass
    for rid, h in house.items():
        for i, p in enumerate(h["placements"]):
            s = cat.get(p["sku"])
            if not s:
                continue
            for r in rooms:
                if r["id"] == rid:
                    r["placements"][i]["color"] = color_for(s["visual"], p["category"])
                    r["placements"][i]["name"] = s.get("name_zh", "")

    scene = {"name": "house", "title": "整屋", "units": "mm",
             "rooms": rooms, "templates_dir": TEMPLATES}
    sp = os.path.join(OUT, "_scene_house%s.json" % (("_" + tag) if tag else ""))
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(scene, f, ensure_ascii=False, indent=1)

    out_png = out_png or os.path.join(OUT, "dollhouse_house.png")
    cmd = [BLENDER, "--background", "--factory-startup", "--python",
           os.path.join(ROOT, "scripts", "blender_dollhouse.py"), "--", sp, out_png]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    for ln in r.stdout.splitlines():
        if "[dollhouse]" in ln or "Error" in ln:
            print(ln)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise SystemExit("blender failed")
    print("->", out_png)


if __name__ == "__main__":
    main()
