"""把当前全流程、香港户型的输入、以及各案例的平面图一次过输出成 PNG。

跑法：python3 scripts/show_pipeline.py
产物：out/pipeline.png、out/plan2d_<case>_engine.png
"""
from __future__ import annotations

import json
import os
import shlex
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from render_plan2d import setup_font                          # noqa: E402
setup_font()          # 中文字体（唔设的话全部变豆腐块）

import bench_hk as B                                              # noqa: E402
from hkgeometry.house import load_flat, solve_house               # noqa: E402


def draw_pipeline(outp: str) -> None:
    fig, ax = plt.subplots(figsize=(17, 9))
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")

    def box(x, y, w, h, text, fc, fs=11):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6",
                                    fc=fc, ec="#333", lw=1.6))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, weight="bold", linespacing=1.5)

    def arrow(x1, y1, x2, y2, label="", color="#333"):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                     mutation_scale=18, lw=1.8, color=color))
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 1.2, label, ha="center",
                    fontsize=9, color=color)

    ax.text(50, 97, "hk-space - current pipeline", ha="center",
            fontsize=16, weight="bold")

    box(2, 82, 20, 11, "1. INPUT\nreal floor plan / sales\nbrochure (HKHA vector PDF)", "#e8f0fe", 10)
    box(26, 82, 20, 11, "2. TRANSCRIPTION\nservices/floorplan/\nmake_*.py -> plan JSON", "#e8f0fe", 10)
    box(50, 82, 22, 11, "customer brief\n(natural language)", "#fdf3e3", 10)
    box(76, 82, 22, 11, "furniture library\n150 IKEA HK SKUs\n+ custom pieces", "#fdf3e3", 10)

    box(2, 64, 44, 13, "3. LLM / agent  (the brain)\n"
                       "set_brief: item list + size caps\n"
                       "change_item: shrink / move / drop / add / custom\n"
                       "* NEVER outputs coordinates", "#ffe9e9", 10)
    box(50, 64, 48, 13, "4. L1 room assignment (assign_rooms)\n"
                        "category -> room function\n"
                        "(preference + capacity soft penalty + unassigned exit)", "#e9f7ef", 10)

    box(50, 47, 48, 13,
        "5. L2 per-room CP-SAT  (6 parallel processes)\n"
        "per-cell cost table: exposure / wall-hug / orientation /\n"
        "occlusion / relations / front clearance / doorway\n"
        "sequential anchoring -> batch solve -> rotation repair\n"
        "-> doorway repair -> coordinate descent", "#e9f7ef", 9.5)
    box(2, 47, 44, 13, "5b. three patches in the anchoring stage\n"
                       "- hub top-40 re-rank (table must leave room for chairs)\n"
                       "- max 150 candidate cells per item\n"
                       "  (otherwise CP-SAT silently returns nothing)\n"
                       "- door-swing relax ladder 0.6 / 0.4 / 0.25 (never 0)", "#e9f7ef", 9.5)

    box(50, 31, 48, 12,
        "6. L3 whole-house verification\n"
        "livability (12 checks) + verify_house\n"
        "out-of-bounds / overlap / doorway / reachability / toilet /\n"
        "sofa faces TV / chair-table distance / orientation /\n"
        "main-artery width / SVR, BVC, CS", "#fff4d6", 9.5)
    box(2, 31, 44, 12, "7. advisory\n"
                       "translates each violation into\n"
                       "'how many mm short + N options'\n"
                       "* feasibility only, no layout-quality advice", "#fff4d6", 9.5)

    box(2, 14, 96, 13,
        "8. FEED BACK TO THE LLM:  change_item / re-run generate_design  <-->  back to 4\n"
        "* known gaps, all observed in practice:\n"
        "   (a) the batch stage used to silently return zero items;\n"
        "   (b) relations are only checked between already-anchored items;\n"
        "   (c) local search that optimises one proxy metric necessarily creates\n"
        "       a new violation somewhere else - whack-a-mole", "#f2e6ff", 9.5)

    box(1, 0.5, 98, 11.5,
        "9. NOT IMPLEMENTED YET  (recommended by the two papers we compared against)\n"
        "   propose -> verify -> commit, one item at a time.\n"
        "   Agentic Designer's Generator / Evaluator / Refiner - the only path we have\n"
        "   found that satisfies 'relations correct + corridor wide enough + nothing\n"
        "   facing a wall' at the same time", "#ffe0e0", 9.5)

    arrow(22, 87.5, 26, 87.5); arrow(46, 87.5, 50, 87.5); arrow(98, 82, 98, 77)
    arrow(76, 82, 74, 77); arrow(24, 82, 24, 77)
    arrow(74, 64, 74, 60); arrow(50, 53.5, 46, 53.5)
    arrow(74, 47, 74, 43); arrow(46, 43, 46, 37)
    arrow(24, 31, 24, 27); arrow(74, 31, 74, 27)
    # 8 -> LLM feedback loop, drawn down the far-left margin (x < 1.4) so that it
    # never crosses box 5b / 7 / 9.  Ends on box 3 = "feed back to the LLM / agent".
    arrow(1.4, 20.0, 0.55, 20.0, "", "#c0392b")
    arrow(0.55, 20.0, 0.55, 70.5, "", "#c0392b")
    arrow(0.55, 70.5, 1.9, 70.5, "", "#c0392b")
    ax.text(0.05, 45.0, "8. feeds back to 3 / 4", rotation=90, ha="center",
            va="center", fontsize=9, color="#c0392b", weight="bold")
    fig.tight_layout()
    fig.savefig(outp, dpi=95, bbox_inches="tight")
    print("->", outp)


def render_case(name: str, case: dict):
    flat = load_flat(os.path.join(ROOT, case["plan"]))
    items = B.build_items(case)
    assign = {i: r for i, _c, _w, _d, _h, r in case["items"]}
    house = solve_house(flat, items, time_limit_s=60.0, parallel=True, assignment=assign)
    lay = {}
    funcs = flat.functions()
    for rid, l in house.by_room.items():
        lay[rid] = dict(func=funcs.get(rid, "living"),
                        placements=[dict(inst=p.inst, sku=p.sku, category=p.category,
                                         x=p.x, y=p.y, rot=p.rot, w=p.w, d=p.d, h=p.h)
                                    for p in l.placements])
    laypath = os.path.join(ROOT, "out", "layout_%s.json" % name)
    with open(laypath, "w", encoding="utf-8") as f:
        json.dump(lay, f, ensure_ascii=False)
    rows, metrics = B.check(flat, case, house)
    npass = sum(1 for _a, ok, _d in rows if ok)
    print("  %-14s %2d/%d  SVR %.0f%%  最窄通路 %s" %
          (name, npass, len(rows), metrics["SVR"], metrics["min_corridor"]))
    title = "%s  -  %d/%d passed  SVR %.0f%%  narrowest artery %s mm" % (
        case["label"], npass, len(rows), metrics["SVR"], metrics["min_corridor"])
    return laypath, title


def main() -> None:
    draw_pipeline(os.path.join(ROOT, "out", "pipeline.png"))
    for name, case in B.CASES.items():
        laypath, title = render_case(name, case)
        os.system("cd %s && python3 scripts/render_plan2d.py --plan %s --layout %s "
                  "--out out/plan2d_%s_engine.png --title %s >/dev/null 2>&1"
                  % (ROOT, case["plan"], laypath, name, shlex.quote(title)))
        print("  -> out/plan2d_%s_engine.png" % name)


if __name__ == "__main__":
    main()
