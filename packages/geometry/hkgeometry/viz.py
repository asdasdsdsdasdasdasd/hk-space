"""可视化 —— 把 isovist / 曝光度图 / 视廊 / 显大分画成能看的图。

这是 Demo 的核心展示层：
  · 曝光度热力图  让用户直观看到「哪里摆东西不显眼」
  · 视线走廊      虚线显示关键视廊，被挡住变红
  · 显大分        改一次动一下就更新
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon as MplPolygon, Rectangle
from shapely.geometry import Point

from .corridor import default_corridors
from .exposure import ExposureMap
from .isovist import isovist
from .spaciousness import SpaciousnessReport

CJK_CANDIDATES = [
    "Noto Sans CJK TC", "Noto Sans CJK SC", "Source Han Sans TC", "Source Han Sans SC",
    # 完整的 Windows 字体放在 Noto Sans TC 前面。本机的 Noto Sans TC 是区域子集，
    # 缺「关键指标」这类简体字，图上会变成方框。
    "Microsoft JhengHei", "Microsoft YaHei", "MingLiU", "SimSun", "SimHei",
    "Noto Sans TC", "Noto Sans SC",
    "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
    "PingFang TC", "Heiti TC", "Arial Unicode MS",
]


def setup_font() -> tuple[bool, str | None]:
    available = {f.name for f in fm.fontManager.ttflist}
    for name in CJK_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name] + list(plt.rcParams["font.sans-serif"])
            plt.rcParams["axes.unicode_minus"] = False
            return True, name
    plt.rcParams["axes.unicode_minus"] = False
    return False, None


_HAS_CJK, _FONT = setup_font()


def L(zh: str, en: str) -> str:
    return zh if _HAS_CJK else en


def _xy(poly):
    xs, ys = poly.exterior.xy
    return np.asarray(xs), np.asarray(ys)


def _mass_colors(layout):
    ms = np.array([p.visual_mass() for p in layout.placements]) if layout.placements else np.array([0.0])
    lo, hi = float(ms.min()), float(ms.max())
    rng = max(1e-9, hi - lo)
    return (ms - lo) / rng


def _draw_plan(ax, room, layout, corridors, report=None, show_labels=True):
    poly = room.geo()
    ax.add_patch(MplPolygon(np.asarray(poly.exterior.coords), closed=True,
                            facecolor="#f5f3ee", edgecolor="#333333", lw=2.0, zorder=1))
    for ring in poly.interiors:                      # 拆不走的實體（核心 / 柱）
        ax.add_patch(MplPolygon(np.asarray(ring.coords), closed=True,
                                facecolor="#ffffff", edgecolor="#333333", lw=2.0, zorder=2))
    # 门窗
    for op in room.openings:
        wall = next((w for w in room.walls if w.id == op.wall), None)
        if wall is None:
            continue
        ax_, ay_ = wall.a
        bx_, by_ = wall.b
        Lw = max(1.0, ((bx_ - ax_) ** 2 + (by_ - ay_) ** 2) ** 0.5)
        t = op.center_mm / Lw
        px, py = ax_ + (bx_ - ax_) * t, ay_ + (by_ - ay_) * t
        nx, ny = -(by_ - ay_) / Lw, (bx_ - ax_) / Lw
        col = "#2a7ae2" if op.kind == "window" else "#c0392b"
        hw = op.width_mm / 2.0
        ax.plot([px - (bx_ - ax_) / Lw * hw, px + (bx_ - ax_) / Lw * hw],
                [py - (by_ - ay_) / Lw * hw, py + (by_ - ay_) / Lw * hw],
                color=col, lw=4, solid_capstyle="butt", zorder=4)
        if op.kind == "door":
            ax.plot([px, px + nx * op.width_mm], [py, py + ny * op.width_mm],
                    color=col, lw=1.2, ls=":", zorder=4)
    # 高度场
    for patch in room.height_field:
        p = patch.polygon()
        ax.add_patch(MplPolygon(np.asarray(p.exterior.coords), closed=True,
                                facecolor="none", edgecolor="#b07d2b",
                                hatch="///", lw=0.8, alpha=0.5, zorder=2))

    # 视廊
    for c in corridors:
        rep = None
        if report is not None:
            rep = next((r for r in report.get("corridors", []) if r["id"] == c.id), None)
        blocked = rep is not None and not rep["clear"]
        ax.plot([c.a[0], c.b[0]], [c.a[1], c.b[1]],
                color=("#d62728" if blocked else "#2ca02c"),
                lw=1.6, ls="--", alpha=0.85, zorder=3)

    # 家具（按视觉体量着色）
    colors = _mass_colors(layout)
    cmap = plt.get_cmap("YlOrRd")
    for i, p in enumerate(layout.placements):
        fp = p.footprint()
        xs, ys = _xy(fp)
        ax.add_patch(MplPolygon(np.column_stack([xs, ys]), closed=True,
                                facecolor=cmap(0.15 + 0.75 * colors[i]),
                                edgecolor="#222222", lw=1.0, alpha=0.9, zorder=5))
        cx, cy = p.center()
        if show_labels:
            ax.text(cx, cy, f"{i+1}", ha="center", va="center",
                    fontsize=8, color="#111111", weight="bold", zorder=6)
    # 入口
    ent = room.entrance_point()
    ax.plot(*ent, marker="*", ms=16, color="#d62728", zorder=7)
    ax.annotate(L("入口", "ENTRY"), ent, textcoords="offset points",
                xytext=(6, -14), fontsize=8, color="#d62728", weight="bold", zorder=8)
    # 尺寸标注（总宽 / 总高）
    minx, miny, maxx, maxy = poly.bounds
    ax.annotate("", xy=(minx, miny), xytext=(maxx, miny),
                arrowprops=dict(arrowstyle="<->", color="#888888", lw=0.8), zorder=2)
    ax.text((minx + maxx) / 2, miny - 160, "%d mm" % (maxx - minx), ha="center",
            fontsize=8, color="#888888")
    ax.annotate("", xy=(minx, miny), xytext=(minx, maxy),
                arrowprops=dict(arrowstyle="<->", color="#888888", lw=0.8), zorder=2)
    ax.text(minx - 160, (miny + maxy) / 2, "%d" % (maxy - miny), va="center",
            ha="right", fontsize=8, color="#888888")
    # 图例：视觉体量色标
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 1))
    cb = plt.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
    cb.set_label(L("視覺體量", "visual mass"), fontsize=7)
    cb.ax.tick_params(labelsize=6)
    ax.set_aspect("equal")
    ax.set_title(L("平面圖（家具按視覺體量著色，越深越重；虛線=關鍵視線）",
                   "Plan (shaded by visual mass; dashed = key sightline)"), fontsize=10)


def _draw_exposure(ax, emap: ExposureMap, layout=None):
    minx, _, _, maxy = emap.extent()
    ax.imshow(emap.exposure, origin="lower", cmap="RdYlGn_r", vmin=0, vmax=1,
              extent=emap.extent(), alpha=0.85, interpolation="nearest")
    for vp in emap.viewpoints:
        ax.plot(*vp, marker="o", ms=4, color="#1f3d7a", zorder=4)
    if layout is not None:
        for p in layout.placements:
            fp = p.footprint()
            xs, ys = _xy(fp)
            ax.plot(xs, ys, color="#111111", lw=1.4, zorder=5)
            ax.text(*p.center(), p.inst.replace("#", ""), ha="center", va="center",
                    fontsize=6, zorder=6)
    ax.set_aspect("equal")
    ax.set_title(L("曝光度圖（綠=低曝光=該放櫃子 / 紅=顯眼）",
                   "Exposure map (green=hide mass here)"), fontsize=10)


def _draw_isovist(ax, room, layout, vp=None):
    poly = room.usable_geo()
    vp = vp or room.entrance_point()
    iso = isovist(poly, layout.footprints(), vp, 720)
    xs, ys = _xy(poly)
    ax.fill(xs, ys, color="#f5f3ee", edgecolor="#333333", lw=1.5, zorder=1)
    if not iso.is_empty:
        ix, iy = _xy(iso)
        ax.fill(ix, iy, color="#2a7ae2", alpha=0.28, zorder=2,
                label=L("可見區域", "visible"))
    for p in layout.placements:
        fp = p.footprint()
        fx, fy = _xy(fp)
        ax.fill(fx, fy, color="#444444", alpha=0.75, zorder=3)
    ax.plot(*vp, marker="*", ms=15, color="#d62728", zorder=4)
    for wc in room.window_centers():
        ax.plot(*wc, marker="s", ms=7, color="#2a7ae2", zorder=4)
    ax.set_aspect("equal")
    ax.set_title(L("入口等視域（藍=進門能看見的範圍）",
                   "Entrance isovist"), fontsize=10)


def _draw_scorecard(ax, report: SpaciousnessReport, layout, title=""):
    ax.axis("off")
    s = report.score
    col = "#2e9e4f" if s >= 78 else ("#e6a817" if s >= 62 else "#d64545")
    ax.text(0.03, 0.94, title, fontsize=11, weight="bold", va="top")
    ax.text(0.03, 0.80, f"{s:.0f}", fontsize=54, weight="bold", color=col, va="top")
    ax.text(0.20, 0.80, L("/100  顯大分", "/100"), fontsize=13, va="top", color="#555555")

    y = 0.62
    m = report.metrics
    rows = [
        (L("可見地板佔比", "visible floor"), f"{m['visible_floor_ratio']*100:.0f}%"),
        (L("進門可見面積", "entrance isovist"), f"{m['entrance_isovist_area_m2']:.1f} m2"),
        (L("最長視線", "longest sightline"), f"{m['best_longest_sightline_mm']/1000:.1f} m"),
        (L("眼高遮擋", "occlusion"), f"{m['occlusion_blend']*100:.0f}%"),
        (L("體量曝光度", "mass exposure"), f"{m['visual_mass_exposure']:.2f}"),
        (L("視廊懲罰", "corridor penalty"), f"{m['corridor_penalty']:.2f}"),
    ]
    for k, v in rows:
        ax.text(0.03, y, k, fontsize=9, va="top", color="#333333")
        ax.text(0.62, y, v, fontsize=9, va="top", weight="bold", color="#111111")
        y -= 0.045

    y -= 0.02
    ax.text(0.03, y, L("診斷", "DETRACTORS"), fontsize=10, weight="bold",
            color="#d64545", va="top")
    y -= 0.055
    if report.detractors:
        for d in report.detractors[:3]:
            ax.text(0.03, y, f"• {d['issue']}", fontsize=9, weight="bold",
                    color="#222222", va="top")
            y -= 0.042
            wrapped = _wrap(d["detail"], 46)
            for line in wrapped[:3]:
                ax.text(0.06, y, line, fontsize=8, color="#555555", va="top")
                y -= 0.036
            y -= 0.012
    else:
        ax.text(0.06, y, L("沒有明顯問題", "no major issues"), fontsize=9,
                color="#2e9e4f", va="top")


def _wrap(text: str, width: int) -> list[str]:
    out, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= width or ch in "，。；":
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def render_analysis(room, layout, emap, report, out_path: str,
                    corridors=None, title: str = "", cjk_note: bool = True):
    corridors = corridors or default_corridors(room)
    fig = plt.figure(figsize=(15, 10.5), dpi=110, facecolor="white")
    gs = fig.add_gridspec(2, 2, hspace=0.18, wspace=0.12,
                          left=0.05, right=0.97, top=0.92, bottom=0.05)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[1, 0])
    ax4 = fig.add_subplot(gs[1, 1])

    creps = {"corridors": []}
    from .corridor import corridor_reports
    creps["corridors"] = corridor_reports(corridors, layout.placements)

    _draw_plan(ax1, room, layout, corridors, creps)
    _draw_exposure(ax2, emap, layout)
    _draw_isovist(ax3, room, layout)
    _draw_scorecard(ax4, report, layout, title=title)

    fig.suptitle(title or L("視覺顯大分析", "Visual spaciousness analysis"),
                 fontsize=13, weight="bold")
    if cjk_note and not _HAS_CJK:
        fig.text(0.5, 0.005, "no CJK font found - fallback labels",
                 ha="center", fontsize=7, color="#999999")
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path


def render_side_by_side(room, results, out_path: str, suptitle: str = "",
                        corridors=None):
    """results = [(title, layout, emap, report), ...] —— Demo 的对比展示。"""
    corridors = corridors or default_corridors(room)
    from .corridor import corridor_reports
    n = len(results)
    fig = plt.figure(figsize=(7.2 * n, 9.0), dpi=110, facecolor="white")
    for i, (title, layout, emap, report) in enumerate(results):
        ax = fig.add_subplot(2, n, i + 1)
        creps = {"corridors": corridor_reports(corridors, layout.placements)}
        _draw_plan(ax, room, layout, corridors, creps)
        ax.set_title("")
        ax.text(0.5, 1.04, title, transform=ax.transAxes, ha="center",
                fontsize=11, weight="bold")
        ax2 = fig.add_subplot(2, n, n + i + 1)
        _draw_exposure(ax2, emap, layout)
        s = report.score
        ax2.text(0.5, -0.13, f"{L('顯大分', 'score')} {s:.0f}/100",
                 transform=ax2.transAxes, ha="center", fontsize=13, weight="bold",
                 color=("#2e9e4f" if s >= 78 else ("#e6a817" if s >= 62 else "#d64545")))
    fig.suptitle(suptitle, fontsize=14, weight="bold")
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path
