"""实验：不给求解器，直接让 LLM 输出坐标，能不能摆对？

用户的问题（2026-10）：
  「假设没有中间引擎，只有 llm，客户需求，家具 list，能不能让 llm 直接
    操作生成准确的 floor plan？」

这个脚本就是那个实验。给 LLM 的信息和给引擎的**一样多**：
  · 房间精确尺寸、服务核心矩形、入口门位置、层高
  · 22 件家具的精确尺寸
  · 坐标约定讲清楚（原点、方向、单位、旋转定义）
然后要它直接吐 {inst, x, y, rot}，再用**同一套几何代码**检查：
  · 越界 / 落进服务核心
  · 互相重叠
  · 压住门扇扫掠区
  · 前方净空不够
  · 从大门走不到
  · 功能关系距离（餐椅<->餐桌 等）
  · 绝对视觉负荷

跑法：
  python3 scripts/llm_direct_layout_test.py --runs 3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

import shapely                                                        # noqa: E402
from shapely.geometry import Point, box                               # noqa: E402

from hkgeometry.house import load_flat                                # noqa: E402
from hkgeometry.types import Layout, Placement, FRONT_CLEARANCE       # noqa: E402
from hkgeometry.greedy import door_approach_zones                     # noqa: E402
from hkgeometry.livability import check_livability, _unreachable_items  # noqa: E402
from hkgeometry.exposure import exposure_map, layout_visual_load      # noqa: E402
from hkgeometry.llm_advisor import LLMClient                          # noqa: E402


def build_prompt(plan, items) -> str:
    m = plan["meta"]
    iw, idp = m["interior_mm"]
    cx0, cy0, cx1, cy1 = m["core_rect_mm"]
    door = plan["rooms"][0]["openings"][0]
    dc = door["center_mm"]
    dw = door["width_mm"]
    lines = []
    lines.append("你是一位室内设计师。请为下面这个空间，直接给出每一件家具的精确位置。")
    lines.append("")
    lines.append("## 坐标系（务必严格照用）")
    lines.append("单位 mm。原点 = 室内**西北角**（图纸左上角）。")
    lines.append("+x 向**东**（图纸向右），+y 向**南**（图纸向下）。")
    lines.append("室内净尺寸：x 从 0 到 %d，y 从 0 到 %d。这是一整片开放空间。" % (iw, idp))
    lines.append("")
    lines.append("## 房间里拆不走的东西")
    lines.append("中央有一个服务核心（厨房台面 + 两个卫生间 + 机电 + 壁炉），")
    lines.append("是一个实心矩形，从 (x=%d, y=%d) 到 (x=%d, y=%d)。" % (cx0, cy0, cx1, cy1))
    lines.append("**任何家具都不可以放进这个矩形里。**")
    lines.append("剩下可用的部分是一条 U 形通道：西侧 %d mm 宽，" % cx0)
    lines.append("东侧 %d mm 宽，南侧 %d mm 深，北侧 %d mm 深。" %
                 (iw - cx1, idp - cy1, cy0))
    lines.append("")
    lines.append("## 出入口")
    lines.append("唯一的门在西墙上，向外开（不向室内扫），门洞 y 从 %d 到 %d。"
                 % (dc - dw // 2, dc + dw // 2))
    lines.append("四面都是玻璃幕墙，所以**不存在「必须靠实墙」这回事**。")
    lines.append("层高 %d mm，不要建议任何结构改动。" % plan["rooms"][0]["height_mm"])
    lines.append("")
    lines.append("## 要摆的家具（尺寸不可改，数量不可改）")
    lines.append("inst | 品类 | 宽 w | 深 d | 高 h")
    for it in items:
        lines.append("%s | %s | %d | %d | %d" %
                     (it["inst"], it["category"], it["w"], it["d"], it["h"]))
    lines.append("")
    lines.append("## 坐标约定")
    lines.append("给出的是家具**底面矩形中心点**的 (x, y)。")
    lines.append("rot 只能取 0 / 90 / 180 / 270，含义：绕中心顺时针旋转的角度。")
    lines.append("rot = 0 或 180 时，矩形的东西向边长 = w，南北向边长 = d；")
    lines.append("rot = 90 或 270 时，东西向边长 = d，南北向边长 = w。")
    lines.append("")
    lines.append("## 硬要求")
    lines.append("1. 每件家具的矩形必须完整落在室内、且不碰服务核心。")
    lines.append("2. 任意两件家具的矩形**不可以重叠**（碰边可以，重叠不行）。")
    lines.append("3. 家具之间要留出人能走过去的路（至少 600mm），")
    lines.append("   从大门出发要能走到每一件家具旁边。")
    lines.append("4. 功能上该在一起的要放在一起：餐椅要围着餐桌、")
    lines.append("   躺椅要成对、茶几要在沙发前面、床头柜要在床头旁、")
    lines.append("   书桌椅要在书桌前。")
    lines.append("5. 不要把所有东西都推到墙边 —— 这是 Mies 的开放平面，")
    lines.append("   家具应该成组地布置，组与组之间留出开阔的视觉空间。")
    lines.append("")
    lines.append("## 输出格式（最重要）")
    lines.append("**不要写任何推理过程、任何解释、任何标题。**")
    lines.append("你的回答必须**以字符 [ 开头**、以 ] 结尾，中间只有 JSON 对象。")
    lines.append("如果你觉得需要先想，就在心里想，不要写出来。")
    lines.append("格式：")
    lines.append('[{"inst": "#dining_table", "x": 1234, "y": 5678, "rot": 0}, ...]')
    lines.append("必须包含上面列出的全部 %d 件，一件都不能少。" % len(items))
    return "\n".join(lines)


def extents(it, rot):
    w, d = int(it["w"]), int(it["d"])
    return (w, d) if int(rot) in (0, 180) else (d, w)


def to_placements(raw, items):
    by = {it["inst"]: it for it in items}
    out, bad = [], []
    for r in raw:
        inst = r.get("inst")
        if inst not in by:
            bad.append("未知 inst %r" % inst)
            continue
        try:
            x, y, rot = int(r["x"]), int(r["y"]), int(r["rot"])
        except Exception:
            bad.append("%s 坐标不是整数" % inst)
            continue
        ew, ns = extents(by[inst], rot)
        out.append((Placement(inst=inst, sku="llm", category=by[inst]["category"],
                              x=int(round(x - ew / 2.0)), y=int(round(y - ns / 2.0)),
                              rot=rot, w=int(by[inst]["w"]), d=int(by[inst]["d"]),
                              h=int(by[inst]["h"]), visual=dict(by[inst].get("visual", {}))),
                    (x, y)))
    return out, bad


def evaluate(flat, items, placements, emap):
    room = flat.rooms[0]
    poly = room.geo()
    usable = room.usable_geo()
    res = {}
    res["n_out_of_room"] = [p.inst for p in placements
                            if not poly.covers(p.footprint().buffer(0))]
    res["n_in_core"] = [p.inst for p in placements
                        if not usable.covers(p.footprint())]
    ov = []
    for i, a in enumerate(placements):
        for b in placements[i + 1:]:
            inter = a.footprint().intersection(b.footprint()).area
            if inter > 100:
                ov.append((a.inst, b.inst, int(inter / 100)))
    res["overlaps"] = ov
    dz = [z for _i, z in door_approach_zones(room)]
    res["door_blocked"] = [(p.inst, int(sum(p.footprint().intersection(z).area
                                            for z in dz) / 100))
                           for p in placements
                           if sum(p.footprint().intersection(z).area for z in dz) > 300]
    lay = Layout(room_id=room.id, placements=list(placements))
    lv = check_livability(room, lay)
    res["hard"] = len(lv.hard)
    res["soft"] = len(lv.soft)
    res["hard_rules"] = sorted({v.rule for v in lv.hard})
    res["unreachable"] = _unreachable_items(room, lay, 600)
    res["visual_load"] = layout_visual_load(lay, emap, poly.area / 1e6)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--out", default="out/llm_direct_layout.json")
    args = ap.parse_args()

    flat = load_flat(os.path.join(ROOT, "out", "plan_farnsworth.json"))
    plan = json.load(open(os.path.join(ROOT, "out", "plan_farnsworth.json"),
                          encoding="utf-8"))
    tgt = json.load(open(os.path.join(ROOT, "data", "targets", "farnsworth_1955.json"),
                         encoding="utf-8"))
    items = tgt["items"]
    prompt = build_prompt(plan, items)
    print("prompt %d 字符，%d 件家具" % (len(prompt), len(items)))

    room = flat.rooms[0]
    emap = exposure_map(room, cell_mm=250)
    client = LLMClient()
    print("模型:", client.describe())

    all_runs = []
    for k in range(args.runs):
        print("\n===== run %d =====" % (k + 1))
        txt = client.chat(
            "你只会输出一个 JSON 数组。回答的第一个字符必须是 [，最后一个字符必须是 ]。"
            "不要输出任何推理、解释、标题或 markdown 代码块。",
            prompt, temperature=args.temperature, max_tokens=args.max_tokens)
        cleaned = txt.strip()
        if cleaned.startswith("~~~"):
            cleaned = cleaned.strip("~").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        try:
            start = cleaned.index("[")
            end = cleaned.rindex("]") + 1
            raw = json.loads(cleaned[start:end])
            parse_err = ""
        except Exception as e:
            raw, parse_err = [], str(e)
        print("  解析:", "OK %d 件" % len(raw) if not parse_err else "失败 " + parse_err)
        if parse_err:
            all_runs.append({"run": k + 1, "error": parse_err, "raw": txt[:2000]})
            continue
        placements, bad = to_placements(raw, items)
        print("  有效 %d 件，问题 %d 条 %s" % (len(placements), len(bad), bad[:3]))
        ev = evaluate(flat, items, [p for p, _ in placements], emap)
        ev["run"] = k + 1
        ev["n_parsed"] = len(placements)
        ev["placements"] = [{"inst": p.inst, "cx": c[0], "cy": c[1], "rot": p.rot}
                            for p, c in placements]
        all_runs.append(ev)
        print("  越界 %d 件 | 落进核心 %d 件 | 重叠 %d 对 | 压门 %d 件" %
              (len(ev["n_out_of_room"]), len(ev["n_in_core"]),
               len(ev["overlaps"]), len(ev["door_blocked"])))
        print("  硬伤 %d 条 %s | 走不到 %d 件 | 视觉负荷 %.5f" %
              (ev["hard"], ev["hard_rules"], len(ev["unreachable"]), ev["visual_load"]))

    outp = os.path.join(ROOT, args.out)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump({"prompt": prompt, "runs": all_runs}, f, ensure_ascii=False, indent=1)
    print("\n->", outp)
    if client.calls:
        print("token 用量:", json.dumps(client.calls))


if __name__ == "__main__":
    main()
