"""Farnsworth House：把「真人設計師做嘅方案」同「本引擎生成嘅方案」放在同一個空間裡比。

為什麼要這樣比
  · 之前所有結果都係喺我自己畫嘅戶型圖上跑 —— 用戶指出那些圖本身就唔合理，
    所以「引擎好唔好」根本無從判斷。
  · Farnsworth House 提供一個真實、尺寸有據、而且**幾何上最難**的測試：
    單一開放空間 + 一個拆唔走嘅中央核心，四周全部相通。
    曝光度／等視域指標喺呢種形狀上才有意義（凸房間裡它會退化成純距離）。
  · 對照組係 National Trust「Edith Farnsworth Reconsidered」展覽復原嘅 c.1955
    實際陳設（見 data/targets/farnsworth_1955.json），唔係我砌出嚟嘅。

用法
  python3 scripts/compare_farnsworth.py --plan out/plan_farnsworth.json \
      --target data/targets/farnsworth_1955.json --out out/farnsworth_compare.png
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.house import load_flat, solve_house, verify_house, HouseLayout   # noqa: E402
from hkgeometry.types import Layout, Placement                                  # noqa: E402
from hkgeometry.exposure import exposure_map, layout_visual_load                # noqa: E402
from hkgeometry.livability import check_livability                              # noqa: E402

CORE_FRAC = 0.21          # 服務核心大約佔室內長度這個比例 —— 用來切 zone


# ---------------------------------------------------------------- 座標
def load_plan_shell(plan_path):
    d = json.load(open(plan_path, encoding="utf-8"))
    pts = [p for r in d["rooms"] for p in r["polygon"]]
    return min(p[0] for p in pts), max(p[1] for p in pts)


def to_model(mx, my, minx, ymax):
    """圖紙 mm（原點 = 室內西北角，+y 向南）-> 引擎模型座標（+y 向北）。"""
    return mx - minx, ymax - my


def target_placements(target, minx, ymax):
    out = []
    for it in target["items"]:
        rot = int(it["rot"])
        ew, ns = (it["w"], it["d"]) if rot in (0, 180) else (it["d"], it["w"])
        cx, cy = to_model(it["cx"], it["cy"], minx, ymax)
        out.append(Placement(inst=it["inst"], sku="farnsworth-1955", category=it["category"],
                             x=int(round(cx - ew / 2.0)), y=int(round(cy - ns / 2.0)),
                             rot=rot, w=int(it["w"]), d=int(it["d"]), h=int(it["h"]),
                             visual=dict(it.get("visual", {}))))
    return out


# ---------------------------------------------------------------- zone
def zone_of(x, y, core):
    """把一個點歸入 4 個 zone。core = (x0, y0, x1, y1) 圖紙 mm。"""
    x0, y0, x1, y1 = core
    if x < x0:
        return "dining/office(西)" if y < (y0 + y1) / 2.0 else "office(西-南)"
    if x > x1:
        return "sleeping(東)"
    if y > y1:
        return "living(南)"
    return "kitchen-walkway(北)"


def zone_counts(pairs, core):
    from collections import Counter
    return Counter(zone_of(*p, core) for p in pairs)


# ---------------------------------------------------------------- 畫圖
def draw_panel(ax, flat, lay, title):
    # ⚠️ 這台機上的 matplotlib 唔會由 add_patch 自動 autoscale
    #    （add_patch 之後 get_xlim() 仍然係 0..1，畫出嚟變成一個正方形）——
    #    所以一定要自己 set_xlim / set_ylim。教訓：唔好靠隱式 autoscale。
    xs_all, ys_all = [], []
    for r in flat.rooms:
        poly = r.geo()
        ax.add_patch(MplPolygon(list(poly.exterior.coords), closed=True,
                                facecolor="#f7f6f2", edgecolor="none", zorder=1))
        xs_all += [p[0] for p in poly.exterior.coords]
        ys_all += [p[1] for p in poly.exterior.coords]
        for ring in poly.interiors:
            ax.add_patch(MplPolygon(list(ring.coords), closed=True,
                                    facecolor="#ffffff", edgecolor="#22252b",
                                    lw=2.0, zorder=2))
            hb = ring.bounds
            ax.text((hb[0] + hb[2]) / 2, (hb[1] + hb[3]) / 2, "SERVICE\nCORE",
                    ha="center", va="center", fontsize=10, color="#888", style="italic", zorder=3)
    for p in lay.placements:
        fp = p.footprint()
        xs, ys = fp.exterior.xy
        ax.add_patch(MplPolygon(list(zip(xs, ys)), closed=True, facecolor="#d8cec0",
                                edgecolor="#6b5b45", lw=1.0, alpha=0.95, zorder=4))
        cx, cy = p.center()
        fx, fy = p.front_dir()
        L = 260
        ax.annotate("", xy=(cx + fx * L, cy + fy * L), xytext=(cx, cy),
                    arrowprops=dict(arrowstyle="-|>", color="#8a5a2b", lw=0.9), zorder=6)
        xs_all += list(xs)
        ys_all += list(ys)
    m = 600
    ax.set_xlim(min(xs_all) - m, max(xs_all) + m)
    ax.set_ylim(min(ys_all) - m, max(ys_all) + m)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title, fontsize=12, weight="bold")
    ax.axis("off")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="out/plan_farnsworth.json")
    ap.add_argument("--target", default="data/targets/farnsworth_1955.json")
    ap.add_argument("--out", default="out/farnsworth_compare.png")
    ap.add_argument("--time-limit", type=float, default=120.0)
    ap.add_argument("--cell", type=int, default=250)
    ap.add_argument("--reuse", action="store_true",
                    help="唔重新求解，重用 out/farnsworth_layouts.json（求解要 6 分鐘）")
    args = ap.parse_args()

    flat = load_flat(os.path.join(ROOT, args.plan) if not os.path.isabs(args.plan) else args.plan)
    target = json.load(open(os.path.join(ROOT, args.target), encoding="utf-8"))
    plan = json.load(open(os.path.join(ROOT, args.plan), encoding="utf-8"))
    minx, ymax = load_plan_shell(os.path.join(ROOT, args.plan))
    core = plan["meta"]["core_rect_mm"]

    room = flat.rooms[0]
    r_area = room.geo().area / 1e6

    # --- 對照組：真人嘅擺法 ---
    tgt = target_placements(target, minx, ymax)
    tgt_house = HouseLayout(flat_id=flat.id)
    tgt_house.by_room[room.id] = Layout(room_id=room.id, placements=tgt)
    tgt_house.scores[room.id] = float("nan")

    # --- 我哋嘅引擎：同一份清單、同一個空間 ---
    items = [Placement(inst=p.inst, category=p.category, w=p.w, d=p.d, h=p.h, visual=p.visual)
             for p in tgt]
    cache = os.path.join(ROOT, "out", "farnsworth_layouts.json")
    if args.reuse and os.path.exists(cache):
        blob = json.load(open(cache, encoding="utf-8"))
        our_house = HouseLayout(flat_id=flat.id)
        our_house.by_room[room.id] = Layout(
            room_id=room.id,
            placements=[Placement(**p) for p in blob["ours"]])
        our_house.scores[room.id] = float("nan")
        dt = blob["solve_seconds"]
        print("（重用 %s 嘅求解結果，耗時 %.1f s）" % (os.path.basename(cache), dt))
    else:
        t0 = time.time()
        our_house = solve_house(flat, items, time_limit_s=args.time_limit, parallel=True)
        dt = time.time() - t0
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump({"solve_seconds": round(dt, 1),
                       "ours": [dict(inst=p.inst, sku=p.sku, category=p.category,
                                     x=p.x, y=p.y, rot=p.rot, w=p.w, d=p.d, h=p.h,
                                     visual=p.visual) for p in
                                our_house.by_room[room.id].placements]}, fh,
                      ensure_ascii=False, indent=1)

    # --- 同一把尺 ---
    emap = exposure_map(room, cell_mm=args.cell)
    rows = []
    for name, house in (("1949-55 原設計 (Edith Farnsworth / 展覽復原)", tgt_house),
                        ("本引擎生成", our_house)):
        lay = house.by_room.get(room.id) or Layout(room_id=room.id, placements=[])
        viv = check_livability(room, lay)
        hard = viv.hard
        soft = viv.soft
        load = layout_visual_load(lay, emap, r_area)
        missed = [p.inst for p in tgt if p.inst not in {q.inst for q in lay.placements}]
        rows.append(dict(name=name, n=len(lay.placements), load=load,
                         hard=len(hard), soft=len(soft),
                         hard_items=["[%s] %s" % (v.rule, v.detail) for v in hard][:5],
                         soft_items=["[%s] %s" % (v.rule, v.detail) for v in soft][:5],
                         live=viv.score, missed=missed,
                         zones=zone_counts([p.center() for p in lay.placements], core)))

    print("=" * 78)
    print("Farnsworth House —— 同真人比（室內 %.1f m²，服務核心 %.2f m²，%d 件家具）"
          % (r_area, (core[2] - core[0]) * (core[3] - core[1]) / 1e6, len(tgt)))
    print("引擎求解耗時 %.1f s" % dt)
    print("=" * 78)
    for r in rows:
        print("\n【%s】" % r["name"])
        print("  放低 %d/%d 件   視覺負荷 %.5f   宜居 %.0f   硬傷 %d  軟傷 %d"
              % (r["n"], len(tgt), r["load"], r["live"], r["hard"], r["soft"]))
        print("  zone 分佈: " + ", ".join("%s %d" % (k, v) for k, v in sorted(r["zones"].items())))
        if r["missed"]:
            print("  冇放低: " + ", ".join(r["missed"]))
        for h in r["hard_items"]:
            print("    !! " + h)
        for h in r["soft_items"]:
            print("    ~  " + h)

    fig, axes = plt.subplots(1, 2, figsize=(24, 8), dpi=80)
    draw_panel(axes[0], flat, tgt_house.by_room[room.id],
               "Target: Dr. Edith Farnsworth c.1955\n(National Trust exhibition plan, measured)")
    draw_panel(axes[1], flat, our_house.by_room.get(room.id) or Layout(room_id=room.id, placements=[]),
               "Ours: hk-space CP-SAT engine\n(visual load %.5f vs %.5f)" % (rows[1]["load"], rows[0]["load"]))
    fig.suptitle("Farnsworth House — real designer target vs our engine, same space, same programme",
                 fontsize=15, weight="bold")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.86, bottom=0.03, wspace=0.02)
    outp = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    fig.savefig(outp, dpi=80)
    print("\n->", outp)


if __name__ == "__main__":
    main()
