"""可见性频次图（曝光度图）—— 技术方案 v3.2 §2.4

★ 让「体量集中」变成可优化目标的技巧。

公式：
    exposure(c) = normalize( Σ_v  w_v · visible_v(c) · prominence(v, c) )
    prominence(v, c) = 1 / (1 + d(v, c) / D0)

为什么要乘 prominence：**看得见**和**碍眼**是两回事。
一个 2.2m 高的柜子站在门口 1 米处，占据你大半个视野；
同样的柜子放在 4 米外的角落，视觉压迫小得多。
prominence 就是「视觉张角」的廉价代理。

关键：exposure 是**静态**的（只依赖房间形状和视点位置，不含家具），
所以它是预计算的常数表，目标函数保持线性，可以直接进 CP-SAT。

⚠️ 注意：在**凸的矩形空房间**里，任何一点都看得见任何一点，
曝光度会退化成纯 prominence。曝光度真正发挥作用要靠
（a）非凸户型（L 形 / 凹位 / 入門玄關）和（b）家具互相遮挡。
所以演示户型用 L 形 —— 这也更接近香港现实。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon

from .isovist import isovist, key_viewpoints
from .units import GRID_MM

PROMINENCE_D0_MM = 800.0      # 距离衰减尺度（校准值，见 _calibrate.py）
ENTRANCE_W = 6.0              # 入口视点权重：进门第一眼最重要


@dataclass
class ExposureMap:
    xs: np.ndarray
    ys: np.ndarray
    count: np.ndarray           # (ny, nx) 被几个视点看得见
    exposure: np.ndarray        # (ny, nx) 归一化 0..1
    inside: np.ndarray
    cell_mm: int
    n_viewpoints: int
    room_poly: Polygon
    viewpoints: list
    weights: list

    def value_at(self, x: float, y: float) -> float:
        ix = int(np.argmin(np.abs(self.xs - x)))
        iy = int(np.argmin(np.abs(self.ys - y)))
        if not (0 <= iy < self.exposure.shape[0] and 0 <= ix < self.exposure.shape[1]):
            return 0.0
        if not self.inside[iy, ix]:
            return 0.0
        return float(self.exposure[iy, ix])

    def mean_over_polygon(self, poly: Polygon) -> float:
        """按 footprint 覆盖的格子取平均 —— 比只采样中心点准得多。"""
        minx, miny, maxx, maxy = poly.bounds
        sel_x = (self.xs >= minx) & (self.xs <= maxx)
        sel_y = (self.ys >= miny) & (self.ys <= maxy)
        if not sel_x.any() or not sel_y.any():
            c = poly.centroid
            return self.value_at(c.x, c.y)
        X, Y = np.meshgrid(self.xs[sel_x], self.ys[sel_y])
        m = np.asarray(shapely.contains_xy(poly, X, Y)) & \
            self.inside[np.ix_(sel_y, sel_x)]
        if not m.any():
            c = poly.centroid
            return self.value_at(c.x, c.y)
        return float(self.exposure[np.ix_(sel_y, sel_x)][m].mean())

    def low_exposure_mask(self, thr: float = 0.34) -> np.ndarray:
        """低曝光区 = 适合放大体量家具的地方。"""
        return self.inside & (self.exposure <= thr)

    def mean_exposure(self) -> float:
        return float(self.exposure[self.inside].mean()) if self.inside.any() else 0.0

    def extent(self):
        h = self.cell_mm / 2.0
        return (self.xs[0] - h, self.xs[-1] + h, self.ys[0] - h, self.ys[-1] + h)


def exposure_map(room, viewpoints=None, cell_mm: int = GRID_MM,
                 obstacles=(), entrance_weight: float = ENTRANCE_W,
                 d0_mm: float = PROMINENCE_D0_MM) -> ExposureMap:
    """离线预计算一次。房间不变就不用重算（见 §16 性能预算）。"""
    poly = room.usable_geo()
    if viewpoints is None:
        # 曝光度只用品質視點：入口 + 幾何中心 + 窗。
        # 不含沙發/床位 —— 那些是"使用者坐著看"的視角，用来做诊断，不用来定曝光度。
        viewpoints = [room.entrance_point()]
        c = poly.representative_point()
        viewpoints.append((int(c.x), int(c.y)))
        viewpoints.extend(room.window_centers())
    ent = room.entrance_point()
    weights = [entrance_weight if vp == ent else 1.0 for vp in viewpoints]

    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx + cell_mm / 2.0, maxx, cell_mm)
    ys = np.arange(miny + cell_mm / 2.0, maxy, cell_mm)
    if xs.size == 0 or ys.size == 0:
        raise ValueError("room too small for the given grid cell")
    X, Y = np.meshgrid(xs, ys)
    inside = np.asarray(shapely.contains_xy(poly, X, Y))

    raw = np.zeros(X.shape, dtype=float)
    count = np.zeros(X.shape, dtype=np.int32)
    for vp, w in zip(viewpoints, weights):
        iso = isovist(poly, obstacles, vp, n_rays=720)
        if iso.is_empty:
            continue
        vis = np.asarray(shapely.intersects_xy(iso, X, Y)) & inside
        count += vis.astype(np.int32)
        d = np.hypot(X - vp[0], Y - vp[1])
        raw += vis.astype(float) * w / (1.0 + d / d0_mm)

    peak = float(raw.max())
    exposure = (raw / peak) if peak > 0 else raw
    return ExposureMap(xs=xs, ys=ys, count=count, exposure=exposure, inside=inside,
                       cell_mm=cell_mm, n_viewpoints=len(viewpoints),
                       room_poly=poly, viewpoints=list(viewpoints), weights=weights)


def layout_mass_exposure(layout, emap: ExposureMap) -> float:
    """视觉体量加权平均曝光度 —— 目标就是把它压到最低。

    直觉：「把视觉体量大的家具，放到曝光度最低的位置。」
    """
    num = den = 0.0
    for p in layout.placements:
        m = max(p.visual_mass(), 1e-6)
        num += m * emap.mean_over_polygon(p.footprint())
        den += m
    return float(num / den) if den > 0 else 0.0


def layout_visual_load(layout, emap: ExposureMap, room_area_m2: float,
                       ref: float = 0.5) -> float:
    """**绝对**视觉负荷 —— 直接反映「有多少视觉重量堆在你眼前」。

        load = Σ (mass_i x exposure_i) / (房间面积 x ref)

    为什么不能用加权平均：加权平均 Σm·e/Σm 只在**同一套家具**之间可比。
    把深色实木衣柜换成浅色开放架时，分母变小、均值反而上升 ——
    于是「选品让房间显大」这件事**永远测不出来**（实测：换款式后分数不升反降）。
    绝对负荷就没这个问题：东西变轻，负荷一定下降。

    方案 §2.6 的推论「选品对显大的影响甚至大于摆放」，靠这个指标才能验证。
    """
    if room_area_m2 <= 0:
        return 0.0
    s = 0.0
    for p in layout.placements:
        s += p.visual_mass() * emap.mean_over_polygon(p.footprint())
    return float(s / (room_area_m2 * ref))


def mass_exposure_breakdown(layout, emap: ExposureMap) -> list[dict]:
    """逐件家具的「体量 x 曝光度」贡献，用来生成视觉诊断。"""
    rows = []
    for p in layout.placements:
        m = p.visual_mass()
        e = emap.mean_over_polygon(p.footprint())
        rows.append({"inst": p.inst, "category": p.category,
                     "visual_mass": round(m, 4), "exposure": round(e, 3),
                     "contribution": round(m * e, 4)})
    rows.sort(key=lambda r: -r["contribution"])
    return rows
