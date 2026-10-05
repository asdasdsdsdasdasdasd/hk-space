"""固定洁具模板 —— 马桶 / 洗手盆 / 浴缸 / 灶台 / 抽油煙機 / 廚櫃檯面。

为什么单独做：这些**不是可移动家具**，是由來去水位和煤氣喉钉死的固定设施
（方案 §3.1 的 wet_zone_anchor）。家具库原来是空的 —— 结果渲染出来的
浴室只有洗衣机、厨房只有雪柜，完全不像一个能用的家。

用法：python3 packages/templates/fixtures.py     -> out/templates/*.glb
"""
from __future__ import annotations

import os
import sys

import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MM = 0.001


def _box(w, d, h, pos=(0, 0, 0), color=(0.95, 0.95, 0.96, 1.0)):
    m = trimesh.creation.box(extents=(w * MM, d * MM, h * MM))
    m.apply_translation([pos[0] * MM, pos[1] * MM, pos[2] * MM + h * MM / 2])
    m.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=color, metallicFactor=0.05, roughnessFactor=0.35))
    return m


def _cyl(r, h, pos=(0, 0, 0), color=(0.95, 0.95, 0.96, 1.0), sections=24):
    m = trimesh.creation.cylinder(radius=r * MM, height=h * MM, sections=sections)
    m.apply_translation([pos[0] * MM, pos[1] * MM, pos[2] * MM + h * MM / 2])
    m.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=color, metallicFactor=0.6, roughnessFactor=0.25))
    return m


def toilet(w=400, d=700, h=780):
    """马桶：水箱 + 座圈 + 底座。正面朝 +Y（模板约定）。"""
    parts = [_box(w, 180, 760, (0, -d / 2 + 90, 0), (0.96, 0.96, 0.97, 1)),      # 水箱
             _box(w * 0.75, d * 0.55, 400, (0, d * 0.12, 0), (0.97, 0.97, 0.98, 1)),  # 底座
             _cyl(w * 0.42, 60, (0, d * 0.12, 400), (0.99, 0.99, 1.0, 1)),        # 座圈
             _box(w * 0.7, d * 0.5, 40, (0, d * 0.12, 400), (0.85, 0.87, 0.9, 1))]
    return trimesh.util.concatenate(parts)


def basin(w=600, d=450, h=850):
    """洗手盆：立柱 + 盆 + 水龙头。"""
    parts = [_box(w * 0.45, d * 0.55, h - 180, (0, 0, 0), (0.96, 0.96, 0.97, 1)),
             _box(w, d, 180, (0, 0, h - 180), (0.98, 0.98, 0.99, 1)),
             _cyl(30, 200, (0, -d * 0.28, h), (0.8, 0.82, 0.85, 1)),
             _box(60, 140, 30, (0, -d * 0.18, h + 170), (0.8, 0.82, 0.85, 1))]
    return trimesh.util.concatenate(parts)


def bathtub(w=1500, d=700, h=550):
    parts = [_box(w, d, h, (0, 0, 0), (0.97, 0.97, 0.98, 1)),
             _box(w - 120, d - 120, 60, (0, 0, h - 60), (0.88, 0.92, 0.95, 1))]
    return trimesh.util.concatenate(parts)


def shower(w=900, d=900, h=1200):
    """淋浴間：底盘 + 三面 1200mm 高的屏板。

    原来屏板做到 2000mm —— 娃娃屋墙体只剖到 1.5m，几块 2m 的薄板立在那里
    像三块大板子，非常出戏。1200mm 是真实淋浴屏高度，看着也合理。
    """
    parts = [_box(w, d, 80, (0, 0, 0), (0.9, 0.93, 0.96, 1)),
             _box(w, 22, h, (0, -d / 2 + 11, 80), (0.80, 0.90, 0.96, 1)),
             _box(22, d, h, (w / 2 - 11, 0, 80), (0.80, 0.90, 0.96, 1)),
             _box(22, d, h, (-w / 2 + 11, 0, 80), (0.80, 0.90, 0.96, 1)),
             _cyl(25, 900, (w * 0.28, -d * 0.28, 80), (0.86, 0.88, 0.90, 1), 18)]
    return trimesh.util.concatenate(parts)


def stove(w=600, d=520, h=850):
    """灶台：柜体 + 台面 + 两个炉头 + 控制旋钮。"""
    parts = [_box(w, d, h - 40, (0, 0, 0), (0.88, 0.88, 0.9, 1)),
             _box(w + 20, d + 20, 40, (0, 0, h - 40), (0.75, 0.77, 0.8, 1)),
             _cyl(90, 18, (-w * 0.22, 0, h), (0.35, 0.36, 0.38, 1), 32),
             _cyl(70, 18, (w * 0.22, 0, h), (0.35, 0.36, 0.38, 1), 32),
             _cyl(22, 26, (-w * 0.22, -d * 0.28, h + 18), (0.8, 0.82, 0.85, 1), 20),
             _cyl(22, 26, (w * 0.22, -d * 0.28, h + 18), (0.8, 0.82, 0.85, 1), 20)]
    return trimesh.util.concatenate(parts)


def range_hood(w=750, d=500, h=150, z=1500):
    parts = [_box(w, d, h, (0, 0, z), (0.82, 0.84, 0.87, 1)),
             _box(w * 0.5, d * 0.6, 420, (0, 0, z + h), (0.82, 0.84, 0.87, 1))]
    return trimesh.util.concatenate(parts)


def kitchen_counter(w=1200, d=600, h=880):
    parts = [_box(w, d, h - 40, (0, 0, 0), (0.9, 0.9, 0.92, 1)),
             _box(w + 20, d + 20, 40, (0, 0, h - 40), (0.72, 0.74, 0.78, 1)),
             _box(w * 0.45, 30, 400, (0, -d / 2 + 6, 150), (0.6, 0.63, 0.68, 1)),
             _cyl(120, 120, (w * 0.28, 0, h - 40), (0.86, 0.89, 0.92, 1), 28)]
    return trimesh.util.concatenate(parts)


FIXTURES = {
    "toilet": (toilet, dict(w_mm=400, d_mm=700, h_mm=780)),
    "basin": (basin, dict(w_mm=600, d_mm=450, h_mm=850)),
    "bathtub": (bathtub, dict(w_mm=1500, d_mm=700, h_mm=550)),
    "shower": (shower, dict(w_mm=900, d_mm=900, h_mm=1200)),
    "stove": (stove, dict(w_mm=600, d_mm=520, h_mm=850)),
    "range_hood": (range_hood, dict(w_mm=750, d_mm=500, h_mm=570)),
    "kitchen_counter": (kitchen_counter, dict(w_mm=1200, d_mm=600, h_mm=880)),
}


def main():
    out = os.environ.get("HKSPACE_TPL_OUT",
                         "/mnt/d/openclaw/hk-space/out/templates")
    os.makedirs(out, exist_ok=True)
    for name, (fn, dims) in FIXTURES.items():
        kw = {k.replace("_mm", ""): v for k, v in dims.items()}
        m = fn(**kw)
        path = os.path.join(out, name + ".glb")
        m.export(path)
        back = trimesh.load(path, force="scene")
        n = sum(len(g.faces) for g in back.geometry.values())
        print("[fixture] %-18s %4dx%4dx%4d  faces=%-6d %.0fKB"
              % (name, dims["w_mm"], dims["d_mm"], dims["h_mm"], n,
                 os.path.getsize(path) / 1024))


if __name__ == "__main__":
    main()
