"""端到端 Demo：同一套家具，两种摆法，显大分差多少。

Demo 的钱镜头 —— 家具一件没少，只是把体量挪到看不见的地方、把主视线让出来，
显大分就跳上去。

户型用 L 形（香港常见），因为凸的矩形空房间里任何位置曝光度都一样，
「把柜子藏到低曝光区」这件事根本演不出来。

运行：cd packages/geometry && python3 hkgeometry/demo.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hkgeometry.corridor import default_corridors
from hkgeometry.exposure import exposure_map
from hkgeometry.spaciousness import spaciousness_report
from hkgeometry.types import HeightPatch, Opening, Placement, Room, Wall, Layout
from hkgeometry.viz import _HAS_CJK, _FONT, render_analysis, render_side_by_side

OUT = os.environ.get("HKSPACE_OUT", "/mnt/d/openclaw/hk-space/out")

# L 形：3200 x 2800 的方形，左上角挖掉 1600 x 900 的凹位（入門玄關 / 廚房位）
# 这个凹位在房间内制造出真正的「遮挡阴影」= 低曝光区
POLY = [(0, 0), (3200, 0), (3200, 2800), (1600, 2800), (1600, 1900), (0, 1900)]


def demo_room() -> Room:
    return Room(
        id="living", label="客廳", polygon=POLY, height_mm=2450,
        walls=[
            Wall("w0", (0, 0), (3200, 0), "load_bearing", 200),
            Wall("w1", (3200, 0), (3200, 2800), "partition", 100),
            Wall("w2", (3200, 2800), (1600, 2800), "load_bearing", 200),
            Wall("w3", (1600, 2800), (1600, 1900), "partition", 100),
            Wall("w4", (1600, 1900), (0, 1900), "partition", 100),
            Wall("w5", (0, 1900), (0, 0), "partition", 100),
        ],
        openings=[
            Opening("d0", "w0", "door", 800, 850, 2000,
                    swing={"dir": "in", "hinge": "left"}),
            Opening("win0", "w1", "window", 1400, 1500, 1200,
                    sill_h_mm=900, sill_depth_mm=500),
        ],
        height_field=[
            HeightPatch([(1600, 2600), (3200, 2600), (3200, 2800), (1600, 2800)],
                        2100, "beam"),
        ],
    )


# ★ 家具清单要**符合这个房间的真实用途**。
# 曾经往 7.5m² 的客厅里塞衣柜 + 层架 + 沙发 + 电视柜 + 茶几，
# 结果家具之间到处不足 600mm，人根本走不进去 —— 分数再高也不是人住的。
# 客厅就该是：沙发 + 电视柜 + 茶几 + 鞋柜。衣柜属于睡房。
SKUS = {
    "#sofa":         dict(category="sofa", w=1400, d=700, h=800,
                          visual=dict(opacity=1.0, lightness_L=42, openness=0.0,
                                      reflectivity=0.05, color_family="warm"),
                          price_hkd=3490),
    "#tv_bench":     dict(category="tv_bench", w=1500, d=400, h=450,
                          visual=dict(opacity=1.0, lightness_L=38, openness=0.0,
                                      reflectivity=0.10, color_family="warm"),
                          price_hkd=1290),
    "#coffee_table": dict(category="coffee_table", w=800, d=450, h=420,
                          visual=dict(opacity=0.9, lightness_L=70, openness=0.2,
                                      reflectivity=0.10, color_family="neutral"),
                          price_hkd=690),
    "#shoe_cabinet": dict(category="shoe_cabinet", w=800, d=350, h=1000,
                          visual=dict(opacity=1.0, lightness_L=35, openness=0.0,
                                      reflectivity=0.10, color_family="warm"),
                          price_hkd=890),
    # ★ 選品對照：同一張沙發，換成淺色 + 帶腿的通透款。
    # 方案 §2.6：「因為買成品、尺寸不可調，選品對顯大的影響甚至大於擺放。」
    "#sofa_light":   dict(category="sofa", w=1400, d=700, h=780,
                          visual=dict(opacity=0.55, lightness_L=82, openness=0.45,
                                      reflectivity=0.08, color_family="neutral"),
                          price_hkd=2990),
}



def build(room_id, poses, swap=None):
    """swap = {"#tv_bench": "#tv_bench_low"} 用来演示「换选品」的效果。"""
    swap = swap or {}
    ps = []
    for inst in poses:
        key = swap.get(inst, inst)
        x, y, rot = poses[inst]
        ps.append(Placement(inst=inst, sku=key.strip("#"), x=x, y=y, rot=rot,
                            **dict(SKUS[key])))
    return Layout(room_id=room_id, placements=ps)


# 家具清单：两种摆法用的是**同一套家具**，只是目标函数不同。
# 房间里的家具清单（#tv_bench_low 只是「换款」备选，不是另一件家具）
BASE_ITEMS = ["#sofa", "#tv_bench", "#coffee_table", "#shoe_cabinet"]


def item_list(swap=None):
    swap = swap or {}
    items = []
    for inst in BASE_ITEMS:
        key = swap.get(inst, inst)
        items.append(Placement(inst=inst, sku=key.strip("#"), **dict(SKUS[key])))
    return items


# ⚠️ 两种摆法**都由贪心求解器算出来**，不是手写的。
# 手写布局违反常识（沙发背对电视、家具挡门）会显得整个系统不专业 ——
# 而且掩盖了求解器真正要解决的问题。
OPT_EXPOSURE_W = 20000.0     # 校准值：低于这个数优化器不会动（见 README）


def swap_sku(layout, mapping):
    """只换 SKU，位置和朝向一点不动。"""
    ps = []
    for p in layout.placements:
        key = mapping.get(p.inst)
        if key:
            ps.append(Placement(inst=p.inst, sku=key.strip("#"), x=p.x, y=p.y,
                                rot=p.rot, **dict(SKUS[key])))
        else:
            ps.append(p)
    return Layout(room_id=layout.room_id, placements=ps)


SOLVER = os.environ.get("HKSPACE_SOLVER", "cpsat")   # cpsat | greedy


def solve_pair(room, emap, restarts: int = 8, time_limit_s: float = 45.0,
               solver: str | None = None):
    """返回 (没优化的摆法, 优化后的摆法)。

    两种摆法的**目标函数只差一项** —— 有没有「体量 x 曝光度」。
    其余项（不重叠/门洞走廊/朝向/贴墙/视廊）两边完全一样，
    **所以分数差异 100% 来自「体量有没有往低曝光区推」。**
    """
    from hkgeometry.greedy import GreedyWeights
    mode = solver or SOLVER
    # 「沒優化過」= 只保證硬約束（不重疊/不擋門）和常識朝向，
    # **不帶任何視覺項**（無曝光度、無貼牆獎勵、無視廊、無遮擋）。
    # 這樣兩者的差距才 100% 來自「視覺優化」本身。
    BASE = dict(exposure=0.0, wall_contact=0.0, corridor=0.0, occlusion=0.0)
    if mode == "cpsat":
        from hkgeometry.cpsat_solver import solve_cpsat
        naive, i1 = solve_cpsat(room, item_list(), emap=emap,
                                weights=GreedyWeights(**BASE),
                                time_limit_s=time_limit_s)
        opt, i2 = solve_cpsat(room, item_list(), emap=emap,
                              weights=GreedyWeights(exposure=OPT_EXPOSURE_W),
                              time_limit_s=time_limit_s)
        print("[solver] CP-SAT  不優化: %s" % i1)
        print("[solver] CP-SAT  優化:   %s" % i2)
        return naive, opt
    from hkgeometry.greedy import solve
    print("[solver] 貪心 + 局部精修（兜底）")
    naive = solve(room, item_list(), emap=emap,
                  weights=GreedyWeights(**BASE), restarts=restarts)
    opt = solve(room, item_list(), emap=emap,
                weights=GreedyWeights(exposure=OPT_EXPOSURE_W), restarts=restarts)
    return naive, opt


def main():
    os.makedirs(OUT, exist_ok=True)
    room = demo_room()
    emap = exposure_map(room, cell_mm=50)
    corridors = default_corridors(room)

    font_msg = ("yes: " + _FONT) if _HAS_CJK else "no (fallback EN labels)"
    print("[font] CJK=" + font_msg)
    print("[room] L-shape area=%.2f m2  h=%dmm  diag=%.0fmm"
          % (room.geo().area / 1e6, room.height_mm, room.diagonal_mm()))
    print("[exposure] grid=%s  viewpoints=%d  mean=%.2f  low-exp cells=%d (%.0f%%)"
          % (emap.exposure.shape, emap.n_viewpoints, emap.mean_exposure(),
             int(emap.low_exposure_mask().sum()),
             100.0 * emap.low_exposure_mask().sum() / max(1, emap.inside.sum())))
    print("[corridors] " + ", ".join("%s(%s) len=%.0fmm"
                                     % (c.id, c.label, c.length_mm()) for c in corridors))
    print("[entrance] %s   [windows] %s" % (room.entrance_point(), room.window_centers()))

    print("[solve] 两种摆法都由求解器生成（不是手写的）")
    naive, opt = solve_pair(room, emap)
    # 第三个变体：**位置完全不动，只把电视柜换成矮款** ——
    # 这样差异 100% 来自「选品」，跟摆位优化分开讲。
    opt_low = swap_sku(opt, {"#sofa": "#sofa_light"})
    variants = (("舊擺法", naive), ("新擺法", opt), ("新擺法+通透沙發", opt_low))
    results = []
    for name, lay in variants:
        rep = spaciousness_report(room, lay, emap, corridors)
        results.append((name, lay, emap, rep))
        p = os.path.join(OUT, "demo_%s.png" % name)
        render_analysis(room, lay, emap, rep, p, corridors=corridors,
                        title="%s · %s" % (name, rep.summary()))
        print("")
        print("=== %s -> %s" % (name, p))
        for q in lay.placements:
            fx, fy = q.front_dir()
            arrow = {(0, 1): "上", (-1, 0): "左", (0, -1): "下", (1, 0): "右"}[(fx, fy)]
            print("    %-14s (%5d,%5d) rot=%3d  正面朝 %s" % (q.inst, q.x, q.y, q.rot, arrow))
        for d in rep.detractors:
            print("    [!] %s: %s" % (d["issue"], d["detail"]))
        for g in rep.passes:
            print("    [ok] %s" % g)

    cmp_path = os.path.join(OUT, "demo_compare.png")
    render_side_by_side(room, results, cmp_path,
                        suptitle="同一套家具，兩種擺法 —— 視覺顯大分對比")
    print("")
    print("=== compare -> %s" % cmp_path)
    print("顯大分：" + "  ->  ".join("%.0f" % r[3].score for r in results)
          + "   (+%.0f)" % (results[-1][3].score - results[0][3].score))


if __name__ == "__main__":
    main()
