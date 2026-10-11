"""压力测试户型 —— 照 Design-MLLM 的 Stress Test 场景做的两个极端户型。

来源：Yuxuan Yang et al., "Design-MLLM", arXiv:2603.13312v3, §5.3。
他们的三个 edge case 里有两个我们直接可以用：
  (i)  Spatial Constriction —— 2.5m x 3m，「barely accommodate the required furniture,
       testing the solver's ability to find valid configurations in a near-zero
       solution space」；
  (iii) 极端长宽比 —— 2m x 5m，正解是「distributes equipment linearly along the walls
       to maintain a clear central pathway」。

我们把它换成香港的家俬清单（同一个几何），因为要修的是香港小户型。
"""
from __future__ import annotations

import json
import os

WALL = 150


def room(rid, label, w, d, walls, openings, h=2750):
    return dict(id=rid, label=label, label_source="stress", label_score=1.0,
                polygon=[[-WALL, -WALL], [w + WALL, -WALL], [w + WALL, d + WALL],
                         [-WALL, d + WALL]],
                holes=[], area_mm2=(w + 2 * WALL) * (d + 2 * WALL), height_mm=h,
                height_field=[], walls=walls, openings=openings)


def build_tiny():
    """2.5m x 3m 极小房（他们的 Small Office 场景，换成香港开放式单位）。"""
    W, D = 2500, 3000
    walls = [
        dict(id="wN", a=[-WALL, -WALL], b=[W + WALL, -WALL], type="load_bearing",
             thickness_mm=WALL, openings=[dict(id="n0", kind="window", center_mm=W // 2 + WALL,
                                               width_mm=1200, height_mm=1350, sill_h_mm=900,
                                               swing=None)]),
        dict(id="wE", a=[W + WALL, -WALL], b=[W + WALL, D + WALL], type="load_bearing",
             thickness_mm=WALL, openings=[]),
        dict(id="wS", a=[W + WALL, D + WALL], b=[-WALL, D + WALL], type="load_bearing",
             thickness_mm=WALL,
             openings=[dict(id="d0", kind="door", center_mm=1700, width_mm=800,
                            height_mm=2100, swing=dict(dir="in", hinge="left"))]),
        dict(id="wW", a=[-WALL, D + WALL], b=[-WALL, -WALL], type="load_bearing",
             thickness_mm=WALL, openings=[]),
    ]
    r = room("studio", "開放式", W, D, walls,
             [dict(o, wall=w["id"]) for w in walls for o in w["openings"]])
    r["walls"] = walls
    return dict(flat_id="plan_stress_tiny", units="mm", rooms=[r], connections=[],
                fixed_services=[],
                entry=dict(room="studio", point=[W // 2, D - 400]),
                meta=dict(source="stress_test_2p5x3m", confidence=1.0,
                          note="照 Design-MLLM §5.3 Spatial Constriction 场景"))


def build_narrow():
    """2m x 5m 极窄房（他们的 Narrow Home Gym 场景，换成香港长厅）。"""
    W, D = 2000, 5000
    walls = [
        dict(id="wN", a=[-WALL, -WALL], b=[W + WALL, -WALL], type="load_bearing",
             thickness_mm=WALL, openings=[]),
        dict(id="wE", a=[W + WALL, -WALL], b=[W + WALL, D + WALL], type="load_bearing",
             thickness_mm=WALL, openings=[dict(id="n0", kind="window", center_mm=D // 2,
                                               width_mm=2000, height_mm=1350, sill_h_mm=900,
                                               swing=None)]),
        dict(id="wS", a=[W + WALL, D + WALL], b=[-WALL, D + WALL], type="load_bearing",
             thickness_mm=WALL, openings=[]),
        dict(id="wW", a=[-WALL, D + WALL], b=[-WALL, -WALL], type="load_bearing",
             thickness_mm=WALL,
             openings=[dict(id="d0", kind="door", center_mm=2600, width_mm=800,
                            height_mm=2100, swing=dict(dir="in", hinge="left"))]),
    ]
    r = room("living", "客廳", W, D, walls,
             [dict(o, wall=w["id"]) for w in walls for o in w["openings"]])
    r["walls"] = walls
    return dict(flat_id="plan_stress_narrow", units="mm", rooms=[r], connections=[],
                fixed_services=[],
                entry=dict(room="living", point=[W // 2, 3500]),
                meta=dict(source="stress_test_2x5m", confidence=1.0,
                          note="照 Design-MLLM §5.3 极端长宽比场景"))


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    outdir = os.path.join(root, "out")
    os.makedirs(outdir, exist_ok=True)
    for name, plan in (("plan_stress_tiny.json", build_tiny()),
                       ("plan_stress_narrow.json", build_narrow())):
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        r = plan["rooms"][0]
        xs = [p[0] for p in r["polygon"]]
        ys = [p[1] for p in r["polygon"]]
        print("  %-24s 净 %d x %d mm = %.2f m2"
              % (name, max(xs) - min(xs) - 2 * WALL, max(ys) - min(ys) - 2 * WALL,
                 (max(xs) - min(xs) - 2 * WALL) * (max(ys) - min(ys) - 2 * WALL) / 1e6))
    print("-> out/plan_stress_tiny.json, out/plan_stress_narrow.json")


if __name__ == "__main__":
    main()
