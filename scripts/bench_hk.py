"""引擎「合理性」基准 —— 跑真实香港户型，逐条检查「合理平面」的不变量。

判据来源（见 docs/HK-REAL-CASES.md）：
  A. 用户点名：床不挡门 / 沙发对着电视 / 家具不挡走廊 / 厕所必须有马桶
  B. 项目此前踩过的坑（10 条）
  C. 审计发现的根因（关系覆盖、朝向退化、批量阶段跳过关系）

用法
  python3 scripts/bench_hk.py                    # 跑全部案例
  python3 scripts/bench_hk.py --case onpak05
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

import shapely                                                    # noqa: E402
from shapely.geometry import Point, box                           # noqa: E402

from hkgeometry.house import load_flat, solve_house, HouseLayout   # noqa: E402
from hkgeometry.types import Layout, Placement, FRONT_CLEARANCE    # noqa: E402
from hkgeometry.greedy import door_approach_zones                  # noqa: E402
from hkgeometry.livability import check_livability, _unreachable_items  # noqa: E402
from hkgeometry.exposure import exposure_map, layout_visual_load   # noqa: E402

# ---------------------------------------------------------------- 案例
# 家具清单来自「居屋寶 安柏苑 05 室 擺位設計圖」上实际画出来的家具
ONPAK05 = dict(
    plan="out/plan_hk_onpak05.json",
    label="安柏苑 05 室（真實香港居屋 493 呎）",
    items=[
        ("#bed_main", "bed", 1370, 1900, 500, "bedroom"),
        ("#wardrobe_main", "wardrobe", 1900, 600, 1800, "bedroom"),
        ("#bed_small", "bed", 1200, 1900, 500, "bedroom2"),
        ("#wardrobe_small", "wardrobe", 900, 600, 1800, "bedroom2"),
        ("#sofa", "sofa", 1400, 700, 750, "living"),
        ("#coffee", "coffee_table", 700, 700, 400, "living"),
        ("#tv", "tv_bench", 1200, 400, 500, "living"),
        ("#dining", "dining_table", 1600, 800, 750, "living"),
        ("#chair1", "chair", 450, 450, 780, "living"),
        ("#chair2", "chair", 450, 450, 780, "living"),
        ("#chair3", "chair", 450, 450, 780, "living"),
        ("#chair4", "chair", 450, 450, 780, "living"),
        ("#toilet", "toilet", 700, 400, 750, "bath"),
        ("#basin", "basin", 600, 450, 850, "bath"),
        ("#shower", "shower", 900, 900, 2000, "bath"),
        ("#counter", "kitchen_counter", 1700, 600, 850, "kitchen"),
        ("#fridge", "fridge", 600, 650, 1700, "kitchen"),
        ("#stove", "stove", 600, 500, 850, "kitchen"),
        ("#washer", "washing_machine", 600, 600, 850, "kitchen"),
    ],
)
CASES = {"onpak05": ONPAK05}


def build_items(case):
    out = []
    for inst, cat, w, d, h, room in case["items"]:
        out.append(Placement(inst=inst, sku="bench", category=cat, w=w, d=d, h=h))
    return out


def wanted_rooms(case):
    return {inst: room for inst, _c, _w, _d, _h, room in case["items"]}


# ---------------------------------------------------------------- 检查
def check(flat, case, house):
    """返回 [(编号, 判据, 通过?, 说明)]"""
    rows = []
    want = wanted_rooms(case)
    placed = {p.inst: p for r in house.by_room.values() for p in r.placements}
    rooms = {r.id: r for r in flat.rooms}

    def add(tag, ok, detail=""):
        rows.append((tag, ok, detail))

    # 1 全部放得下
    missing = [i for i, _c, _w, _d, _h, _r in case["items"] if i not in placed]
    add("1 全部家具都放得下", not missing, ("漏放 " + ", ".join(missing)) if missing else "19/19")

    # 2 不重叠
    ov = []
    ps = list(placed.values())
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            if a.footprint().intersection(b.footprint()).area > 100:
                ov.append((a.inst, b.inst))
    add("2 家具互不重叠", not ov, str(ov[:4]))

    # 3 不越界 / 不压在固定障碍
    out = [p.inst for p in ps if not rooms[house.assignment.get(p.inst, "")].geo().covers(p.footprint())
           ] if house.assignment else []
    add("3 完整落在房间内", not out, str(out[:5]))

    # 4 必需品在位（马桶！）
    need = {"toilet", "basin", "shower", "stove", "fridge", "bed"}
    got = {p.category for p in ps}
    add("4 必需品在位（含马桶）", need <= got, "缺 " + str(sorted(need - got)))

    # 5 床不挡门
    bad = []
    for rid, r in rooms.items():
        zones = [z for _i, z in door_approach_zones(r)]
        for p in r.placements_list if hasattr(r, "placements_list") else house.by_room.get(rid, Layout(rid, [])).placements:
            if p.category in ("bed", "bunk_bed", "loft_bed", "storage_bed"):
                a = sum(p.footprint().intersection(z).area for z in zones)
                if a > 3000:
                    bad.append((p.inst, rid, int(a / 100)))
    add("5 床不挡门", not bad, str(bad[:3]))

    # 6 门口走道不被占
    #   阈值对齐引擎自己的 livability 规则（door_blocked，0.03 m2）——
    #   不自己另立一套标准。但无论过不过，都把每件的侵占面积印出来。
    DOOR_TOL_CM2 = 300          # = 0.03 m2，同 livability.door_blocked 一致
    dz, worst = [], []
    for rid, r in rooms.items():
        zones = [z for _i, z in door_approach_zones(r)]
        for p in house.by_room.get(rid, Layout(rid, [])).placements:
            a = sum(p.footprint().intersection(z).area for z in zones)
            if a > 0:
                worst.append((p.inst, rid, int(a / 100)))
            if a > DOOR_TOL_CM2 * 100:
                dz.append((p.inst, rid, int(a / 100)))
    add("6 门口走道侵占不超 0.03m2", not dz,
        "超标 %s；全部侵占 %s" % (dz[:3], sorted(worst, key=lambda t: -t[2])[:4]))

    # 7 走得到每一件家具
    unre = []
    for rid, r in rooms.items():
        lay = house.by_room.get(rid)
        if lay and lay.placements:
            unre += [("%s@%s" % (i, rid)) for i in _unreachable_items(r, lay, 600)]
    add("7 走到每件家具前面", not unre, str(unre[:4]))

    # 8 沙发对着电视
    liv = house.by_room.get("living")
    s8 = "没有沙发或电视柜"
    ok8 = True
    if liv:
        sofa = next((p for p in liv.placements if p.category == "sofa"), None)
        tv = next((p for p in liv.placements if p.category == "tv_bench"), None)
        if sofa and tv:
            sc, tc = sofa.center(), tv.center()
            fx, fy = sofa.front_dir()
            dx, dy = tc[0] - sc[0], tc[1] - sc[1]
            L = math.hypot(dx, dy) or 1
            dot = (dx / L) * fx + (dy / L) * fy
            ok8 = dot >= 0.5
            s8 = "夹角点积 %.2f，距离 %.2fm" % (dot, L / 1000)
    add("8 沙发正面朝电视", ok8, s8)

    # 9 前方净空（柜门/雪柜门打不开）
    blk = []
    for rid, r in rooms.items():
        lay = house.by_room.get(rid)
        if not lay:
            continue
        for p in lay.placements:
            need = FRONT_CLEARANCE.get(p.category, 0)
            if not need:
                continue
            cx, cy = p.center()
            fx, fy = p.front_dir()
            probe = Point(cx + fx * (p.d / 2.0 + need * 0.6), cy + fy * (p.d / 2.0 + need * 0.6))
            if not r.usable_geo().contains(probe):
                blk.append("%s@%s 出界" % (p.inst, rid))
                continue
            for q in lay.placements:
                if q.inst != p.inst and probe.distance(q.footprint()) < need * 0.35:
                    blk.append("%s被%s挡" % (p.inst, q.inst))
                    break
    add("9 柜门/抽屉打得开", not blk, str(blk[:4]))

    # 10 餐椅靠近餐桌（审计根因 C15）
    s10, ok10 = "没有餐椅或餐桌", True
    if liv:
        dt = next((p for p in liv.placements if p.category == "dining_table"), None)
        ch = [p for p in liv.placements if p.category == "chair"]
        if dt and ch:
            ds = []
            for c in ch:
                a, b = dt.center(), c.center()
                ds.append(math.hypot(a[0] - b[0], a[1] - b[1]))
            far = [d for d in ds if d > 1200]
            ok10 = not far
            s10 = "餐椅到餐桌 " + " ".join("%.0f" % d for d in ds) + ("  (超 1200mm %d 张)" % len(far) if far else "")
    add("10 餐椅靠近餐桌", ok10, s10)

    # 11 门的扫掠区没被任何家具压（软硬都算）
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all")
    ap.add_argument("--time-limit", type=float, default=40.0)
    ap.add_argument("--out", default="out/bench_hk.json")
    args = ap.parse_args()

    names = list(CASES) if args.case == "all" else [args.case]
    all_rows = {}
    for name in names:
        case = CASES[name]
        flat = load_flat(os.path.join(ROOT, case["plan"]))
        items = build_items(case)
        print("=" * 76)
        print("案例 %s —— %s" % (name, case["label"]))
        print("  房间:", ", ".join("%s(%.1fm2,%s)" % (r.id, r.geo().area / 1e6, r.label)
                                  for r in flat.rooms))
        # ★ 客人講明邊件擺邊間房（「主床擺主人房」），所以房間分配照用客戶指定。
        #   唔 pin 嘅話，L1 會把兩張床塞埋同一間房 -> 一張放唔落 -> 憑空消失，
        #   量到嘅就變成「分配」問題而唔係「擺位」問題。
        assign = {}
        for inst, cat, _w, _d, _h, room in case["items"]:
            assign[inst] = room
        t0 = time.time()
        house = solve_house(flat, items, time_limit_s=args.time_limit, parallel=True,
                            assignment=assign)
        dt = time.time() - t0
        rows = check(flat, case, house)
        all_rows[name] = [dict(tag=t, ok=o, detail=d) for t, o, d in rows]
        npass = sum(1 for _t, o, _d in rows if o)
        print("  求解 %.1fs" % dt)
        for rid in ("kitchen", "bath", "bedroom", "living"):
            lay = house.by_room.get(rid)
            if lay and lay.placements:
                print("   %s: %s" % (rid, ", ".join(
                    "%s(%dx%d @%d,%d r%d)" % (p.inst, p.w, p.d, p.x, p.y, p.rot)
                    for p in lay.placements)))
            else:
                print("   %s: (空)" % rid)
        for t, o, d in rows:
            print("   [%s] %-22s %s" % ("PASS" if o else "FAIL", t, d[:90]))
        print("  --> %d/%d 通过" % (npass, len(rows)))

    outp = os.path.join(ROOT, args.out)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=1)
    print("->", outp)


if __name__ == "__main__":
    main()
