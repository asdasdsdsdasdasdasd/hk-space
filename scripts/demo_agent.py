#!/usr/bin/env python3
"""LLM 大腦驅動嘅設計流程 —— 客戶講需求，LLM 決定點做。

同 demo_llm_advisor.py 嘅分別：
  · 舊：引擎求解 → 生成顧問意見 → 問 LLM 打補丁（LLM 只係修補者）
  · 新：客戶需求 → **LLM 自己定 brief、自己決定幾時調求解器**、
        自己判斷夠唔夠好、自己決定要唔要換清單重解

運行：
  python3 scripts/demo_agent.py --plan out/plan_hk_b.json --tag b \
      --brief "300呎兩房，想睡房有雙人床..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from hkgeometry.agent import DesignAgent
from hkgeometry.house import load_flat, verify_house
from hkgeometry.llm_advisor import LLMClient
from hkgeometry.viz import setup_font

import demo_house as D

OUT = os.path.join(ROOT, "out")
DEFAULT_BRIEF = """我啱啱上車，買咗個 310 呎嘅兩房單位（L 形客廳）。
想做到：
  1. 睡房要有張床，最好雙人，真係唔夠位細啲都可以，但一定要有得瞓
  2. 客廳要坐到人睇電視，要有食飯嘅位
  3. 廚房要煮到飯（有灶台、雪櫃）
  4. 浴室要有馬桶、洗手盆、沖涼位
  5. 要有位晾衫
  6. 我唔想成日撞到嘢 —— 通道要行得過
預算大概 HK$50,000。層高只有 2.45 米。"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=os.path.join(OUT, "plan_hk_b.json"))
    ap.add_argument("--tag", default="b")
    ap.add_argument("--brief", default="", help="客戶需求（自然語言）；留空用預設")
    ap.add_argument("--brief-file", default="", help="由檔案讀客戶需求")
    ap.add_argument("--budget", type=int, default=44, help="最多幾次工具調用")
    ap.add_argument("--rounds", type=int, default=16)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()

    setup_font()
    flat = load_flat(args.plan)
    cat = json.load(open(D.CATALOG, encoding="utf-8"))["skus"]
    fix = os.path.join(ROOT, "data", "catalog", "fixtures.json")
    if os.path.exists(fix):
        cat = cat + json.load(open(fix, encoding="utf-8"))["skus"]

    brief = args.brief
    if args.brief_file:
        brief = open(args.brief_file, encoding="utf-8").read()
    if not brief:
        brief = DEFAULT_BRIEF

    print("=" * 74)
    print("  LLM 大腦模式 —— 戶型 %s（%d 間房）" % (flat.id, len(flat.rooms)))
    print("=" * 74)
    print("客戶需求：")
    for ln in brief.strip().splitlines():
        print("   " + ln)
    print()

    client = LLMClient()
    print("LLM: %s" % client.describe())
    agent = DesignAgent(flat, cat, client, brief_text=brief,
                        budget=args.budget, rounds=args.rounds)
    t0 = time.time()
    st = agent.run()
    dt = time.time() - t0
    print()
    print("=" * 74)
    print("  完成（%.1fs，%d 次工具調用，LLM tokens %s）"
          % (dt, len(st.calls), [c for c in client.calls]))
    print("=" * 74)
    if st.house is None:
        print("⚠️ LLM 冇叫過 generate_design —— 冇設計產出")
        return
    rep = verify_house(flat, st.house)
    print("就位 %d/%d　整屋視覺負荷 %.4f"
          % (rep.n_placed, len(st.items), rep.house_visual_load))
    for r in flat.rooms:
        lay = st.house.by_room.get(r.id)
        print("   %-9s %-6s %s" % (r.id, r.label,
                                   [p.inst for p in (lay.placements if lay else [])]))
    print()
    print("LLM 交貨總結：")
    print(st.summary or "（冇寫）")
    if st.tradeoffs:
        print("取捨：")
        for t in st.tradeoffs:
            print("   · %s" % t)

    # 存檔
    sfx = ("_" + args.tag) if args.tag else ""
    dump = {}
    for rid, lay in st.house.by_room.items():
        sc = st.house.scores.get(rid)
        dump[rid] = {"func": flat.functions().get(rid, "?"),
                     "score": None if sc is None or sc != sc else sc,
                     "placements": [{"inst": p.inst, "sku": p.sku, "category": p.category,
                                     "x": p.x, "y": p.y, "rot": p.rot,
                                     "w": p.w, "d": p.d, "h": p.h, "price": p.price_hkd}
                                    for p in lay.placements]}
    lp = os.path.join(OUT, "house_layout%s_agent.json" % sfx)
    json.dump(dump, open(lp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"brief": brief, "summary": st.summary, "tradeoffs": st.tradeoffs,
               "calls": st.calls, "usage": client.calls},
              open(os.path.join(OUT, "agent_trace%s.json" % sfx), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("-> %s" % lp)

    if not args.no_render:
        import subprocess
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_plan2d.py"),
                        "--plan", args.plan, "--layout", lp,
                        "--out", os.path.join(OUT, "plan2d%s_agent.png" % sfx),
                        "--title", "LLM 大腦方案 — %s" % flat.id], check=False)
        print("-> out/plan2d%s_agent.png" % sfx)


if __name__ == "__main__":
    main()
