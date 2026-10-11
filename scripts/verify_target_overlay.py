"""把 data/targets/farnsworth_1955.json 抄錄出嚟嘅矩形，畫返喺展覽平面圖上面核對。

為什麼要專門做這一步：對照組嘅座標係我由 raster 上讀出嚟嘅，
「讀錯一格」係最容易發生、又最難察覺嘅錯誤（圖睇落完全正常）。
所以逐個矩形疊返原圖，對唔上就即刻見到。

用法：python3 scripts/verify_target_overlay.py
"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REF = os.path.join(ROOT, "..", "_ref", "farnsworth")

TARGET = os.path.join(ROOT, "data", "targets", "farnsworth_1955.json")
PLAN = os.path.join(ROOT, "out", "plan_farnsworth.json")


def main() -> None:
    tgt = json.load(open(TARGET, encoding="utf-8"))
    g = tgt["page_to_mm"]
    src = os.path.join(REF, "p4hi-04.png")
    if not os.path.exists(src):
        print("missing", src)
        sys.exit(1)
    import numpy as np
    from PIL import Image
    img = np.array(Image.open(src).convert("L"))

    x0p, y0p, mpp = g["x_origin_px"], g["y_origin_px"], g["mm_per_px"]

    def to_px(mx, my):
        return x0p + mx / mpp, y0p + my / mpp

    fig, ax = plt.subplots(figsize=(19, 8))
    ax.imshow(img, cmap="gray")
    for it in tgt["items"]:
        rot = int(it["rot"])
        ew, ns = (it["w"], it["d"]) if rot in (0, 180) else (it["d"], it["w"])
        cx, cy = to_px(it["cx"], it["cy"])
        w_px, h_px = ew / mpp, ns / mpp
        col = {"drawn": "#d62728", "label": "#1f77b4", "inferred": "#2ca02c"}[it["evidence"]]
        ax.add_patch(Rectangle((cx - w_px / 2, cy - h_px / 2), w_px, h_px,
                               facecolor="none", edgecolor=col, lw=2.0, zorder=5))
        ax.text(cx, cy - h_px / 2 - 6, it["inst"].lstrip("#"), color=col, fontsize=8,
                ha="center", va="bottom", zorder=6)
        # 正面箭頭（圖紙座標 +y 係向南，模型 +y 係向北，所以箭頭 y 要反）
        fx = {0: 0, 90: -1, 180: 0, 270: 1}[rot]
        fy = {0: 1, 90: 0, 180: -1, 270: 0}[rot]
        ax.annotate("", xy=(cx + fx * 55, cy + fy * 55), xytext=(cx, cy),
                    arrowprops=dict(arrowstyle="-|>", color=col, lw=1.2), zorder=6)

    # 核心（用生成出嚟嘅 plan_farnsworth.json，獨立於上面嘅讀數）
    pl = json.load(open(PLAN, encoding="utf-8"))
    cx0, cy0, cx1, cy1 = pl["meta"]["core_rect_mm"]
    a = to_px(cx0, cy0); b = to_px(cx1, cy1)
    ax.add_patch(Rectangle(a, b[0] - a[0], b[1] - a[1], facecolor="none",
                           edgecolor="#ff7f0e", lw=2.5, ls="--", zorder=5))
    ax.text(a[0], a[1] - 8, "core (from HABS)", color="#ff7f0e", fontsize=9, zorder=6)
    # 室內邊界
    p0 = to_px(0, 0); p1 = to_px(16840, 8656)
    ax.add_patch(Rectangle(p0, p1[0] - p0[0], p1[1] - p0[1], facecolor="none",
                           edgecolor="#00a0a0", lw=1.5, zorder=5))
    ax.set_xlim(2900, 5334)
    ax.set_ylim(1700, 400)
    ax.set_title("Verification: reconstructed target layout (colours) overlaid on the National Trust "
                 "exhibition plan\nred=drawn  blue=letter-only  green=inferred",
                 fontsize=12, weight="bold")
    ax.axis("off")
    out = os.path.join(ROOT, "out", "farnsworth_target_verify.png")
    fig.tight_layout()
    fig.savefig(out, dpi=95)
    print("->", out)


if __name__ == "__main__":
    main()
