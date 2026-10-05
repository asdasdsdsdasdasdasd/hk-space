#!/usr/bin/env python3
"""把 hkgeometry 的布局导出成 3D 娃娃屋场景 JSON，然后交给 Blender 渲染。

Demo 关键路径第 ③ 条：**3D 娃娃屋好看**。
这条链路是：Layout (2D footprint) -> 场景 JSON -> Blender -> PNG

运行：python3 scripts/render_dollhouse.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry import demo as D
from hkgeometry.exposure import exposure_map
from hkgeometry.spaciousness import spaciousness_report

OUT = os.path.join(ROOT, "out")
BLENDER = "/mnt/d/openclaw/tools/blender-4.2.1-linux-x64/blender"
TEMPLATES = os.path.join(ROOT, "out", "templates")

# demo 里的家具 -> 模板 id（packages/templates/catalog.py）
TPL = {
    "wardrobe": "wardrobe_tall",
    "tv_bench": "tv_console",
    "sofa": "sofa_2",
    "coffee_table": "side_table",
    "shelf": "shelf_unit",
}

# 由 SKU 的 visual 属性推颜色：明度决定深浅，color_family 决定色相。
# （真实做法是取商品主图的主色 —— 见方案 §7.3；这里先用规则近似。）
FAMILY_TINT = {
    "warm":    (1.00, 0.78, 0.52),   # 木色偏暖
    "cool":    (0.72, 0.80, 0.95),   # 冷灰蓝
    "neutral": (0.92, 0.90, 0.84),   # 米白
}


def color_for(lightness_L: int, opacity: float, family: str = "neutral"):
    L = max(0.0, min(1.0, lightness_L / 100.0))
    base = 0.16 + 0.68 * L
    t = FAMILY_TINT.get(family, FAMILY_TINT["neutral"])
    rgb = [min(1.0, base * t[0]), min(1.0, base * t[1]), min(1.0, base * t[2])]
    if opacity < 0.35:
        rgb = [min(1.0, c * 1.10 + 0.06) for c in rgb]
        return [rgb[0], rgb[1], rgb[2] * 1.02, 0.30]
    return [rgb[0], rgb[1], rgb[2], 1.0]


def build_scene(room, layout, name: str, title: str) -> dict:
    placements = []
    for p in layout.placements:
        v = p.visual or {}
        tpl = TPL.get(p.category, "cabinet_2door")
        placements.append({
            "inst": p.inst,
            "template": tpl,
            "x": p.x, "y": p.y, "rot": p.rot,
            "w": p.w, "d": p.d, "h": p.h,
            "color": color_for(v.get("lightness_L", 60), v.get("opacity", 1.0),
                               v.get("color_family", "neutral")),
        })
    return {
        "name": name,
        "title": title,
        "units": "mm",
        "room": {
            "polygon": [[int(x), int(y)] for x, y in room.polygon],
            "height_mm": room.height_mm,
            "walls": [{"a": list(w.a), "b": list(w.b), "type": w.type} for w in room.walls],
            "openings": [{"id": o.id, "wall": o.wall, "kind": o.kind,
                          "center_mm": o.center_mm, "width_mm": o.width_mm,
                          "sill_h_mm": o.sill_h_mm} for o in room.openings],
            "height_field": [{"region": [[int(a), int(b)] for a, b in hp.region],
                              "z_max": hp.z_max, "kind": hp.kind}
                             for hp in room.height_field],
        },
        "placements": placements,
        "templates_dir": TEMPLATES,
    }


def render(scene: dict, out_png: str):
    scene_path = os.path.join(OUT, "_scene_%s.json" % scene["name"])
    with open(scene_path, "w", encoding="utf-8") as f:
        json.dump(scene, f, ensure_ascii=False, indent=1)
    cmd = [BLENDER, "--background", "--factory-startup",
           "--python", os.path.join(ROOT, "scripts", "blender_dollhouse.py"),
           "--", scene_path, out_png]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    tail = [ln for ln in r.stdout.splitlines() if "[dollhouse]" in ln or "Error" in ln]
    print("\n".join(tail[-12:]))
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise SystemExit("blender failed rc=%d" % r.returncode)


def main():
    os.makedirs(OUT, exist_ok=True)
    room = D.demo_room()
    emap = exposure_map(room, cell_mm=50)
    variants = [("bad", D.BAD, None, "舊擺法"),
                ("good", D.GOOD, None, "新擺法"),
                ("good_low", D.GOOD, {"#tv_bench": "#tv_bench_low"}, "新擺法+換矮櫃")]
    for tag, poses, swap, title in variants:
        lay = D.build(room.id, poses, swap)
        rep = spaciousness_report(room, lay, emap)
        sc = build_scene(room, lay, tag, "%s · %s" % (title, rep.summary()))
        png = os.path.join(OUT, "dollhouse_%s.png" % tag)
        print("=== %s  %s  ->  %s" % (tag, rep.summary(), png))
        render(sc, png)


if __name__ == "__main__":
    main()
