#!/usr/bin/env python3
"""选品优化 Demo —— 位置完全不动，只换家具，显大分能跳多少？

这是 §2.6 那条推论的实测：
    「因为买成品、尺寸不可调，**选品对显大的影响甚至大于摆放**。」

做法：先固定一个摆法（用 demo.py 的 GOOD），然后对每一件家具，
在真实 SKU 库里找出所有能塞进同一个 footprint 的同类别 SKU，
逐个替换、重算显大分，看分数能拉到多高。

运行：python3 scripts/demo_selection.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry import demo as D
from hkgeometry.exposure import exposure_map, layout_mass_exposure
from hkgeometry.spaciousness import spaciousness_report
from hkgeometry.types import Placement

CATALOG = os.path.join(ROOT, "data", "catalog", "seed_skus.json")
OUT = os.path.join(ROOT, "out")

# 摆法里每件家具占的 footprint（来自 demo.GOOD），选品必须塞得进（可 90 度旋转）
FIT = {
    "#wardrobe":     (1000, 600),
    "#tv_bench":     (1500, 400),
    "#sofa":         (1400, 700),
    "#coffee_table": (800, 450),
    "#shelf":        (800, 300),
}


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        d = json.load(f)
    return d["skus"] if isinstance(d, dict) and "skus" in d else d


def fits(sku, max_w, max_d) -> bool:
    w = sku["dims"]["w"]
    dep = sku["dims"]["d"]
    return (w <= max_w and dep <= max_d) or (w <= max_d and dep <= max_w)


def to_placement(inst, sku, x, y, rot):
    return Placement(inst=inst, sku=sku["id"], category=sku["category"],
                     x=x, y=y, rot=rot,
                     w=sku["dims"]["w"], d=sku["dims"]["d"], h=sku["dims"]["h"],
                     visual=dict(sku["visual"]), price_hkd=sku.get("price_hkd", 0))


def main():
    os.makedirs(OUT, exist_ok=True)
    room = D.demo_room()
    emap = exposure_map(room, cell_mm=50)

    curs = {inst: (None, -1.0) for inst in FIT}
    for inst, (x, y, rot) in D.GOOD.items():
        key = "#tv_bench_low" if inst == "#tv_bench" else inst
        curs[inst] = (D.SKUS[key], None)   # 用 demo 里的手工 SKU 作为起点
    base = D.build(room.id, D.GOOD)
    base_rep = spaciousness_report(room, base, emap)
    print("== 起点（同一摆法，demo 手工 SKU）:", base_rep.summary())

    skus = load_catalog()
    print("== 载入 SKU 库: %d 条" % len(skus))

    chosen = {}
    total_saved = 0
    report = []
    for inst, (mx, my) in FIT.items():
        cat = D.SKUS[inst]["category"]
        same_cat = [s for s in skus if s["category"] == cat]
        cands = [s for s in same_cat if fits(s, mx, my)]
        if not cands:
            # 诚实报告：同类里有货，但都塞不进这个位置。
            # 「最接近能用」= 需要的尺寸超出位置最少的那个（两种朝向都试）
            def excess(s):
                w, dep = s["dims"]["w"], s["dims"]["d"]
                a = max(0, w - mx) + max(0, dep - my)
                b = max(0, w - my) + max(0, dep - mx)
                return min(a, b)
            near = min(same_cat, key=excess) if same_cat else None
            report.append((inst, cat, 0, None, None, None,
                           (near, mx, my, excess(near)) if near else None))
            continue

        cur = next(p for p in base.placements if p.inst == inst)
        best = None
        scores = []
        for s in cands:
            lay = D.Layout(room.id, [p for p in base.placements if p.inst != inst]
                           + [to_placement(inst, s, cur.x, cur.y, cur.rot)])
            r = spaciousness_report(room, lay, emap)
            scores.append((r.score, s))
            if best is None or r.score > best[0]:
                best = (r.score, s)
        scores.sort(key=lambda t: -t[0])
        lo, hi = scores[-1][0], scores[0][0]
        # Pareto 味道：在「不比重分差 2 分」的前提下挑最便宜的
        cheap_ok = min([t for t in scores if t[0] >= hi - 2.0],
                       key=lambda t: t[1].get("price_hkd", 10 ** 9)) if scores else None
        chosen[inst] = cheap_ok[1] if cheap_ok else best[1]
        cur_price = cur.price_hkd
        best_price = chosen[inst].get("price_hkd", 0)
        total_saved += cur_price - best_price
        report.append((inst, cat, len(cands), (lo, hi), chosen[inst],
                       (cur_price, best_price),
                       {"best": best, "value": cheap_ok}))

    print()
    print("== 逐件家具：同类可替换 SKU 数 / 显大分区间 / 当前价 -> 选定款价")
    gaps = []
    for inst, cat, n, rng, sku, prices, extra in report:
        if sku is None:
            if extra:
                near, mx, my, ex = extra
                print("  %-14s %-13s [无可用替代] 位置只有 %dx%d，最接近的是 %s "
                      "%dx%d（还差 %dmm）"
                      % (inst, cat, mx, my, near["name_zh"],
                         near["dims"]["w"], near["dims"]["d"], ex))
                gaps.append((cat, near, mx, my, ex))
            else:
                print("  %-14s %-13s [库里没有这个品类]" % (inst, cat))
            continue
        bscore, bsku = extra["best"]
        vs = extra["value"] or extra["best"]
        print("  %-14s %-13s %2d 款  分 %.0f..%.0f（区间 %.0f 分）"
              % (inst, cat, n, rng[0], rng[1], rng[1] - rng[0]))
        print("      最高分   %s  %d分  $%d"
              % (bsku["name_zh"][:18].ljust(18), round(bscore), bsku.get("price_hkd", 0)))
        if vs is not None and vs[1]["id"] != bsku["id"]:
            print("      性价比   %s  %d分  $%d   <- 少 %.0f 分，省 $%d"
                  % (vs[1]["name_zh"][:18].ljust(18), round(vs[0]),
                     vs[1].get("price_hkd", 0), bscore - vs[0],
                     bsku.get("price_hkd", 0) - vs[1].get("price_hkd", 0)))
        print("      原款 $%d -> 选定 $%d" % (prices[0], prices[1]))

    # 全部换成最优款
    opt_placements = []
    for p in base.placements:
        s = chosen.get(p.inst)
        if s is None:
            opt_placements.append(p)
        else:
            opt_placements.append(to_placement(p.inst, s, p.x, p.y, p.rot))
    opt = D.Layout(room.id, opt_placements)
    opt_rep = spaciousness_report(room, opt, emap)

    print()
    print("== 结果")
    print("   起点            : %s" % base_rep.summary())
    print("   仅换家具(位置不动): %s   (+%.0f)"
          % (opt_rep.summary(), opt_rep.score - base_rep.score))
    print("   体量曝光度      : %.3f -> %.3f"
          % (base_rep.metrics["visual_mass_exposure"],
             opt_rep.metrics["visual_mass_exposure"]))
    print("   总价            : $%d -> $%d (%+d)"
          % (sum(p.price_hkd for p in base.placements),
             sum(p.price_hkd for p in opt.placements),
             sum(p.price_hkd for p in opt.placements)
             - sum(p.price_hkd for p in base.placements)))

    from hkgeometry.viz import render_side_by_side
    p = os.path.join(OUT, "demo_selection.png")
    render_side_by_side(room, [("換款式前 (原擺法)", base, emap, base_rep),
                               ("換款式後 (位置不動)", opt, emap, opt_rep)],
                        p, suptitle="位置完全不動，只換家具款式 —— 顯大分對比")
    print("   -> %s" % p)

    if gaps:
        print()
        print("== ⚠️ SKU 库缺口（这正是产品该给用户提示的东西）")
        for cat, near, mx, my, ex in gaps:
            print("   %s：小户型能用的位置只有 %dx%d，IKEA HK 同类里最接近的 %s 是 "
                  "%dx%d，还差 %dmm —— **市场缺档**"
                  % (cat, mx, my, near["name_zh"], near["dims"]["w"],
                     near["dims"]["d"], ex))
        print("   → 对香港小户型，这个品类要么找别的品牌 / 定制，要么换替代方案"
              "（例如梳化位改放梳化床或懶人椅）")


if __name__ == "__main__":
    main()
