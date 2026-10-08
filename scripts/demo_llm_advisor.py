#!/usr/bin/env python3
"""LLM 顧問閉環 demo：解算 -> 顧問意見 -> LLM 決定 -> 重解/補丁 -> 重新渲染。

技術方案 v3.2 §5 + §8。這條鏈路的意義：
  求解器只會算，LLM 只會說。把「算」和「說」接起來，才有產品。

運行：
  python3 scripts/demo_llm_advisor.py                 # 真的調本地 LLM
  python3 scripts/demo_llm_advisor.py --mock out/llm_mock.json
  python3 scripts/demo_llm_advisor.py --no-render
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from hkgeometry.advisory import advise, render_for_customer
from hkgeometry.greedy import GreedyWeights
from hkgeometry.house import assign_rooms, load_flat, solve_house, verify_house
from hkgeometry.llm_advisor import (LOCAL_BASE, LLMClient, apply_decisions, try_nudge)
from hkgeometry.livability import check_livability
from hkgeometry.types import Placement
from hkgeometry.viz import setup_font

import demo_house as D

OUT = os.path.join(ROOT, "out")
WEIGHTS = GreedyWeights(exposure=20000.0)
TL = 40.0


def banner(t: str) -> None:
    print("")
    print("=" * 74)
    print("  " + t)
    print("=" * 74)


def load_catalog() -> list[dict]:
    with open(D.CATALOG, encoding="utf-8") as f:
        cat = json.load(f)["skus"]
    fix = os.path.join(ROOT, "data", "catalog", "fixtures.json")
    if os.path.exists(fix):
        with open(fix, encoding="utf-8") as f:
            cat = cat + json.load(f)["skus"]
    return cat


def build_items(cat: list[dict]) -> list[Placement]:
    items = []
    for inst, c, mw, md, want in D.WISHLIST:
        s = D.pick_sku(cat, c, mw, md, want)
        if s is None:
            continue
        items.append(Placement(inst=inst, sku=s["id"], category=s["category"],
                               w=s["dims"]["w"], d=s["dims"]["d"], h=s["dims"]["h"],
                               visual=dict(s["visual"]), price_hkd=s.get("price_hkd", 0)))
    return items


def want_by_room(items: list[Placement], assign: dict) -> dict:
    out: dict = {}
    for it in items:
        rid = assign.get(it.inst)
        if rid:
            out.setdefault(rid, []).append(it)
    return out


def snapshot(flat, house, advices) -> dict:
    lv = {}
    for r in flat.rooms:
        lay = house.by_room.get(r.id)
        if lay is None or not lay.placements:
            lv[r.id] = "空"
            continue
        rep = check_livability(r, lay)
        lv[r.id] = "%d件 硬傷%d" % (len(lay.placements), len(rep.hard))
    rep = verify_house(flat, house)
    return {
        "livability": lv,
        "scores": {k: (None if v != v else round(v, 1)) for k, v in house.scores.items()},
        "n_placed": rep.n_placed, "n_items": rep.n_items,
        "visual_load": round(rep.house_visual_load, 4),
        "reachable": rep.reachable, "unreachable": rep.unreachable,
        "advices": len(advices),
        "advice_kinds": sorted({a.kind for a in advices}),
        "issues": rep.issues,
    }


def all_advice(flat, house, items, assign):
    """顧問意見 = 逐間房嘅「放唔落 / 擺唔好」+ 全屋嘅「冇房放得落」。"""
    unassigned = [p for p in items if p.inst not in assign]
    return advise(flat, house, want_by_room(items, assign), unassigned=unassigned)


def hard_total(flat, house) -> int:
    """全屋硬傷條數 —— 「這次改動有冇變差」的第一把尺。"""
    t = 0
    for r in flat.rooms:
        lay = house.by_room.get(r.id)
        if lay is not None and lay.placements:
            t += len(check_livability(r, lay).hard)
    return t


def placed_set(house) -> set:
    return {p.inst for lay in house.by_room.values() for p in lay.placements}


def dump_layout(flat, house, path: str) -> None:
    funcs = flat.functions()
    dump = {}
    for rid, lay in house.by_room.items():
        sc = house.scores.get(rid)
        dump[rid] = {
            "func": funcs.get(rid, "?"),
            "score": None if sc is None or sc != sc else sc,
            "placements": [{"inst": p.inst, "sku": p.sku, "category": p.category,
                            "x": p.x, "y": p.y, "rot": p.rot,
                            "w": p.w, "d": p.d, "h": p.h, "price": p.price_hkd}
                           for p in lay.placements],
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="", help="戶型 JSON（默認 plan_hk_a）")
    ap.add_argument("--tag", default="", help="輸出檔名後綴，例如 b -> dollhouse_b_llm.png")
    ap.add_argument("--mock", default="",
                    help="用罐頭回應代替真調模型；可給多個路徑用逗號分隔（每輪一個）")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--llm-url", default="",
                    help="OpenAI 兼容端點；留空 = DeepSeek 官方 API")
    ap.add_argument("--llm-model", default="",
                    help="模型 id（deepseek-flash / deepseek-v4-pro）")
    ap.add_argument("--llm-effort", default="low",
                    help="DeepSeek 的 reasoning_effort：low / high / max")
    ap.add_argument("--local", action="store_true",
                    help="改用本機 llama-server（127.0.0.1:8080）")
    ap.add_argument("--max-drops", type=int, default=2,
                    help="整個閉環最多可以丟幾件家具（丟嘢一定會令分數上升，必須設限）")
    ap.add_argument("--max-advices", type=int, default=5,
                    help="每輪最多餵幾條意見俾 LLM（多過就會爆 context，其餘下一輪再問）")
    ap.add_argument("--max-rounds", type=int, default=2,
                    help="顧問閉環跑幾輪（每輪把引擎的反饋再問一次 LLM）")
    args = ap.parse_args()

    setup_font()
    os.makedirs(OUT, exist_ok=True)
    plan_path = args.plan or D.PLAN
    sfx = ("_" + args.tag) if args.tag else ""
    flat = load_flat(plan_path)
    cat = load_catalog()
    items0 = build_items(cat)
    print("=== 戶型 %s  %d 房  總面積 %.1f m²   家具 %d 件  $%d"
          % (flat.id, len(flat.rooms), flat.total_area_m2(),
             len(items0), sum(p.price_hkd for p in items0)))
    allowed = {p.inst for p in items0}

    banner("第 0 步：基線解算（不用 LLM）")
    t0 = time.time()
    assign0, _ = assign_rooms(flat, items0, verbose=True)
    house0 = solve_house(flat, items0, weights=WEIGHTS, time_limit_s=TL,
                         max_workers=5, parallel=True, verbose=True)
    adv0 = all_advice(flat, house0, items0, assign0)
    snap0 = snapshot(flat, house0, adv0)
    print("    用時 %.1fs" % (time.time() - t0))
    print("    宜居: %s" % "  ".join("%s=%s" % kv for kv in snap0["livability"].items()))
    print("    分數: %s" % snap0["scores"])
    print("    就位 %d/%d   視覺負荷 %.4f" % (snap0["n_placed"], snap0["n_items"],
                                            snap0["visual_load"]))
    print("    顧問意見 %d 條: %s" % (snap0["advices"], snap0["advice_kinds"]))
    if adv0:
        print(render_for_customer(adv0))

    if not adv0:
        print("\n    沒有顧問意見 —— 沒有需要 LLM 決定的事，收工。")
        return

    dump_layout(flat, house0, os.path.join(OUT, "house_layout.json"))
    dump_layout(flat, house0, os.path.join(OUT, "house_layout%s.json" % sfx))
    if not args.no_render:
        import render_house
        render_house.main(plan_path=plan_path,
                          layout_path=os.path.join(OUT, "house_layout%s.json" % sfx),
                          out_png=os.path.join(OUT, "dollhouse%s_before.png" % sfx),
                          tag=("before" + sfx))
        print("-> out/dollhouse%s_before.png" % sfx)

    items = copy.deepcopy(items0)
    assign = dict(assign0)
    house = house0
    advices = adv0
    trace = []
    mocks = [m for m in (args.mock or "").split(",") if m]
    accepted: set[str] = set()
    exhausted: set = set()          # (inst, action) 已經試過並且失敗
    drops_used = 0                  # 已經丟咗幾件家具（有預算，見 --max-drops）
    feedback: list[str] = []

    for rnd in range(1, args.max_rounds + 1):
        # 客戶已經說「接受現狀」的，不要再問一次 —— 否則閉環永遠不收斂
        pending = [a for a in advices if not set(a.subjects) <= accepted]
        if not pending:
            print("\n    所有顧問意見都已被接受或解決，閉環收斂。")
            break
        active = pending[:args.max_advices]
        if len(pending) > len(active):
            print("\n    （本輪先問 %d 條，仲有 %d 條下一輪再問）"
                  % (len(active), len(pending) - len(active)))
        banner("第 %d 輪：LLM 決定（%d 條意見）" % (rnd, len(active)))
        extra = {"engine_feedback": feedback} if feedback else None
        if extra:
            print("    上一輪引擎反饋:")
            for n in feedback:
                print("      · " + n)
        if mocks:
            path = mocks[min(rnd - 1, len(mocks) - 1)]
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            from hkgeometry.llm_advisor import parse_decisions
            decisions, dropped = parse_decisions(raw, allowed)
            dbg = {"raw": raw, "dropped": dropped, "system": "", "user": ""}
            print("    [mock] %s" % path)
        else:
            if args.local:
                args.llm_url = args.llm_url or LOCAL_BASE
            client = LLMClient(base_url=args.llm_url or None,
                               model=args.llm_model or None,
                               effort=args.llm_effort)
            print("    LLM: %s" % client.describe())
            if client.api_key in ("", "sk-local"):
                print("    ⚠️ 冇搵到 API key —— 睇 HKSPACE_LLM_KEY / "
                      "DEEPSEEK_API_KEY / ~/.dsh/.credentials.yaml")
                return
            try:
                print("    model list: %s" % client.list_models()[:6])
            except Exception as e:
                print("    ⚠️ 連不上 LLM: %s" % e)
                return
            t1 = time.time()
            decisions, dbg = client.decide(active, flat, house,
                                           allowed_targets=allowed,
                                           extra=extra,
                                           temperature=args.temperature,
                                           catalog=cat,
                                           items_by_inst={p.inst: p for p in items})
            print("    用時 %.1fs   usage=%s" % (time.time() - t1, client.calls))

        print("    模型原始輸出：")
        for ln in (dbg["raw"] or "").strip().splitlines():
            print("      | " + ln[:160])
        if dbg.get("dropped"):
            print("    ⚠️ 被丟掉的輸出: %s" % dbg["dropped"])
        print("    解析出 %d 條決定：" % len(decisions))
        for d in decisions:
            print("      %-10s %-16s %-34s [%s]"
                  % (d.action, d.target or "-", d.reason[:34], d.channel))
        trace.append({"round": rnd, "advices": [a.headline for a in active],
                      "user_prompt": dbg.get("user", ""), "raw": dbg.get("raw", ""),
                      "decisions": [{"action": d.action, "target": d.target,
                                     "params": d.params, "reason": d.reason,
                                     "channel": d.channel} for d in decisions],
                      "dropped": dbg.get("dropped", [])})

        # ★ 動手之前先存檔 —— LLM 的改動要過「幾何 + 產品」雙重裁決，
        #   裁決不過就整輪回滾。LLM 提議，幾何裁決，這條邊界不能讓。
        bak = (copy.deepcopy(items), dict(assign), copy.deepcopy(house))
        h_before, placed_before = hard_total(flat, house), placed_set(house)

        # ---- 分通道
        resolve = [d for d in decisions if d.channel in ("resolve", "accept")]
        patches = [d for d in decisions if d.channel == "patch"]
        for d in decisions:
            if d.action == "keep" and d.target:
                accepted.add(d.target)
        feedback = []
        for d in decisions:
            feedback.append("你上一輪決定 %s %s —— %s"
                            % (d.action, d.target or "", d.reason or "（沒寫理由）"))

        if any(d.channel == "resolve" for d in resolve):
            banner("第 %d 步：通道① 加約束重解" % rnd)
            items, assign, acts, _, notes = apply_decisions(
                items, assign, resolve, cat, flat=flat, house=None,
                drop_budget=max(0, args.max_drops - drops_used))
            drops_used += sum(1 for n in notes if n.startswith("[drop]"))
            for n in notes:
                print("    " + n)
            feedback += ["引擎執行結果：" + n for n in notes]
            for d in resolve:
                for n in notes:
                    if n.startswith(("[miss]", "[near-miss]", "[skip]", "[refuse]")) \
                            and d.target in n:
                        exhausted.add((d.target, d.action))
            if acts:
                t2 = time.time()
                house = solve_house(flat, items, weights=WEIGHTS, time_limit_s=TL,
                                    max_workers=5, parallel=True, verbose=True,
                                    assignment=assign)
                print("    重解用時 %.1fs" % (time.time() - t2))
            else:
                print("    （沒有真正生效的重解動作）")
        else:
            print("\n    （LLM 沒有選重解通道）")

        if patches:
            banner("第 %d 步：通道② 局部補丁（引擎自己搜方向）" % rnd)
            for d in patches:
                rid = assign.get(d.target)
                lay = house.by_room.get(rid) if rid else None
                r = flat.room(rid) if rid else None
                if lay is None or r is None:
                    print("    [skip] %s 沒有已解位置" % d.target)
                    continue
                ok, dx, dy, why = try_nudge(r, lay, d.target)
                print("    [%s] %s  %s" % ("✅" if ok else "❌", d.target, why))
                feedback.append("局部補丁 %s：%s" % (d.target, why))
                if not ok:
                    exhausted.add((d.target, "try_nudge"))
                    accepted.discard(d.target)

        # ---- 裁決 + 把「仲有邊啲動作可行」回灌（引擎唔可以只講唔得，要講仲有咩得）
        from hkgeometry.llm_advisor import feasibility as _feas
        by_inst = {p.inst: p for p in items}
        for a in active[:4]:
            vi = []
            for line in _feas(a, cat, by_inst, flat, house):
                if "：唔可行" in line:
                    continue
                head, _, inst = line.partition(" ")
                inst = inst.split("：")[0].strip()
                if (inst, head) in exhausted:
                    continue
                vi.append(line.split("—")[0].strip())
            if vi:
                feedback.append("【%s】仲可行嘅動作（已剔除試過失敗嘅）：%s"
                                % (a.headline, "；".join(vi)))
            else:
                feedback.append("【%s】所有動作都試過或者唔可行 —— 請 keep，"
                                "並喺 reason 講明要定制 / 跨零售商比價。" % a.headline)

        # ---- 裁決
        h_after, placed_after = hard_total(flat, house), placed_set(house)
        dropped_now = {d.target for d in decisions if d.action == "drop_item"}
        lost = (placed_before - placed_after) - dropped_now
        bad = []
        if h_after > h_before:
            bad.append("硬傷由 %d 條增至 %d 條" % (h_before, h_after))
        if lost:
            bad.append("有家具被靜靜雞擠走（%s）" % "、".join(sorted(lost)))
        if bad:
            banner("第 %d 輪裁決：⛔ 不通過，回滾" % rnd)
            print("    理由：%s" % "；".join(bad))
            print("    （LLM 提議，幾何裁決 —— 這一輪的改動已全部撤銷）")
            items, assign, house = bak
            feedback.append(
                "你上一輪的改動被引擎**駁回**：%s。"
                "原因係「改一間房唔可以整壞另一間房」。請換一個唔會令情況變差嘅做法；"
                "可用動作只有 shrink_sku / try_nudge / move_item / keep。"
                % "；".join(bad))
        else:
            print("\n    ✅ 裁決：通過（硬傷 %d -> %d，就位 %d -> %d）"
                  % (h_before, h_after, len(placed_before), len(placed_after)))

        # ---- 重新評估
        advices = all_advice(flat, house, items, assign)
        snap1 = snapshot(flat, house, advices)
        banner("第 %d 輪結果對照" % rnd)
        for k in snap0["livability"]:
            a, b = snap0["livability"][k], snap1["livability"].get(k, "?")
            flag = "  " if a == b else "->"
            print("    %-9s %-16s %s %s" % (k, a, flag, b))
        print("    分數      %s  ->  %s" % (snap0["scores"], snap1["scores"]))
        print("    視覺負荷  %.4f  ->  %.4f" % (snap0["visual_load"], snap1["visual_load"]))
        print("    就位      %d/%d  ->  %d/%d"
              % (snap0["n_placed"], snap0["n_items"], snap1["n_placed"], snap1["n_items"]))
        print("    顧問意見  %d 條 -> %d 條" % (snap0["advices"], snap1["advices"]))
        if advices:
            print(render_for_customer(advices))
        snap0 = snap1

    with open(os.path.join(OUT, "llm_trace%s.json" % sfx), "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=1)
    print("\n-> out/llm_trace%s.json" % sfx)

    dump_layout(flat, house, os.path.join(OUT, "house_layout.json"))
    dump_layout(flat, house, os.path.join(OUT, "house_layout%s.json" % sfx))
    if not args.no_render:
        import render_house
        render_house.main(plan_path=plan_path,
                          layout_path=os.path.join(OUT, "house_layout%s.json" % sfx),
                          out_png=os.path.join(OUT, "dollhouse%s_llm.png" % sfx),
                          tag=("llm" + sfx))
        print("-> out/dollhouse%s_llm.png" % sfx)


if __name__ == "__main__":
    main()
