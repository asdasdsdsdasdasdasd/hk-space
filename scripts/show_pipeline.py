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

    ax.text(50, 97, "hk-space 当前全流程", ha="center", fontsize=16, weight="bold")

    box(2, 82, 20, 11, "① 输入层\n真实户型图 / 售楼说明书\n(房委会矢量 PDF)", "#e8f0fe", 10)
    box(26, 82, 20, 11, "② 转录\nservices/floorplan/\nmake_*.py → plan JSON", "#e8f0fe", 10)
    box(50, 82, 22, 11, "客户需求\n(自然语言)", "#fdf3e3", 10)
    box(76, 82, 22, 11, "家具库\n150 IKEA HK SKU\n+ 定制家具", "#fdf3e3", 10)

    box(2, 64, 44, 13, "③ LLM / agent（大脑）\n"
                       "set_brief：清单 + 尺寸上限\n"
                       "change_item：shrink / move / drop / add / custom\n"
                       "★ 绝不输出坐标", "#ffe9e9", 10)
    box(50, 64, 48, 13, "④ L1 房间分配 assign_rooms\n"
                        "品类 → 房间功能（偏好 + 容量软罚 + 未分配出口）", "#e9f7ef", 10)

    box(50, 47, 48, 13,
        "⑤ L2 每间房 CP-SAT（并行 6 进程）\n"
        "候选格代价表：曝光度 / 贴墙 / 朝向 / 遮挡 / 关系 / 前景 / 门口\n"
        "顺序锚定 → 批量求解 → 旋转修复 → 门口修复 → 坐标下降", "#e9f7ef", 9.5)
    box(2, 47, 44, 13, "⑤b 锚定阶段的三个补丁\n"
                       "· Hub top-40 重排（餐桌要留位给餐椅）\n"
                       "· 每件最多 150 候选格（否则 CP-SAT 交白卷）\n"
                       "· 门扇区放宽阶梯 0.6/0.4/0.25（永不到 0）", "#e9f7ef", 9.5)

    box(50, 31, 48, 12,
        "⑥ L3 整屋校验 livability（12 条）+ verify_house\n"
        "越界 / 重叠 / 门口 / 走得到 / 马桶 / 沙发朝电视\n"
        "餐椅离餐桌 / 朝向 / 主通路宽度 / SVR·BVC·CS", "#fff4d6", 10)
    box(2, 31, 44, 12, "⑦ advisory 顾问意见\n"
                       "把违规翻译成「差几多 mm + 几个选项」\n"
                       "★ 只有可行性问题，没有布局质量问题", "#fff4d6", 10)

    box(2, 14, 96, 13,
        "⑧ 回灌 LLM：change_item / 重新 generate_design  ←→  回到 ④\n"
        "★ 已知缺口：① 批量阶段曾「静静鸡交白卷」；② 关系只在锚定件之间查；\n"
        "   ③ 局部搜索优化部分代理指标时，必然在别处换出新的违规（实测按下葫芦浮起瓢）", "#f2e6ff", 10)

    box(2, 2, 96, 9,
        "⑨【论文建议、我们未做】逐件提议 → 逐件验证 → 逐件提交\n"
        "   Agentic Designer 的 Generator / Evaluator / Refiner —— 唯一能同时满足"
        "「关系对 + 走廊够阔 + 不面壁」的路径", "#ffe0e0", 10)

    arrow(22, 87.5, 26, 87.5); arrow(46, 87.5, 50, 87.5); arrow(98, 82, 98, 77)
    arrow(76, 82, 74, 77); arrow(24, 82, 24, 77)
    arrow(74, 64, 74, 60); arrow(50, 53.5, 46, 53.5)
    arrow(74, 47, 74, 43); arrow(46, 43, 46, 37)
    arrow(24, 31, 24, 27); arrow(74, 31, 74, 27)
    arrow(50, 14, 50, 11, "⑧ 循环", "#c0392b")
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
