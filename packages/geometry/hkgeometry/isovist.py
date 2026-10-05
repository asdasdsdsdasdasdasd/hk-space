"""Isovist（等视域）—— 技术方案 v3.2 §2.2

从某个视点 360 度射线投射，不被遮挡的可见区域多边形。
用 shapely 2.x 的向量化 API，整个投射是一次调用。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon

from .units import GRID_MM

TALL_MM = 1200          # 超过这个高度算「高家具」，会挡住坐着的人的视野
EYE_MM = 1500           # 站立视线高度


def _occluder(room_poly: Polygon, obstacles) -> shapely.Geometry:
    """遮挡物 = 房间边界 + 所有家具轮廓的边界（射线打到就停）。"""
    parts = [room_poly.boundary]
    for o in obstacles:
        if o is None or o.is_empty:
            continue
        parts.append(o.boundary)
    return shapely.union_all(parts)


def _cast(occluder, vp, angles: np.ndarray, max_dist: float) -> np.ndarray:
    """向量化射线投射，返回每条射线到最近遮挡物的距离。"""
    n = angles.size
    vx, vy = float(vp[0]), float(vp[1])
    dx, dy = np.cos(angles), np.sin(angles)
    starts = np.stack([np.full(n, vx), np.full(n, vy)], axis=1)
    ends = np.stack([vx + max_dist * dx, vy + max_dist * dy], axis=1)
    rays = shapely.linestrings(np.stack([starts, ends], axis=1))
    hits = shapely.intersection(rays, occluder)
    d = np.asarray(shapely.distance(hits, shapely.points(vx, vy)), dtype=float)
    d[~np.isfinite(d)] = max_dist
    return np.clip(d, 0.0, max_dist)


def _max_dist(room_poly: Polygon) -> float:
    minx, miny, maxx, maxy = room_poly.bounds
    return float(np.hypot(maxx - minx, maxy - miny)) * 1.25 + 100.0


@dataclass
class IsovistMetrics:
    area_mm2: float
    area_m2: float
    perimeter_mm: float
    compactness: float            # 4*pi*A/P^2，越接近 1 越开阔，越小越"堵"
    longest_sightline_mm: float
    visible_floor_ratio: float    # 可见面积 / 房间面积（文献中最强的单项预测因子）
    occlusion_ratio: float        # 被高家具挡住的水平角度占比


def isovist(room_poly: Polygon, obstacles=(), viewpoint=(0, 0),
            n_rays: int = 360) -> Polygon:
    """从 viewpoint 计算等视域多边形。"""
    md = _max_dist(room_poly)
    angles = np.linspace(0.0, 2.0 * np.pi, n_rays, endpoint=False)
    occ = _occluder(room_poly, obstacles)
    d = _cast(occ, viewpoint, angles, md)
    vx, vy = float(viewpoint[0]), float(viewpoint[1])
    pts = np.stack([vx + d * np.cos(angles), vy + d * np.sin(angles)], axis=1)
    # 去掉连续重复点，避免退化多边形
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(pts, axis=0)) > 1e-6, axis=1)
    if keep.sum() < 3:
        return Polygon()
    poly = Polygon(pts[keep])
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def occlusion_ratio(room_poly: Polygon, placements=(), viewpoint=(0, 0),
                    n_rays: int = 360, tall_mm: int = TALL_MM,
                    exclude_inst: str | None = None) -> float:
    """被「高家具」挡住视野的水平角度占比。

    做法：对每条射线，比较「打到高家具的距离」和「打到墙的距离」，
    前者更近就算被挡。复用同一套射线投射，天然处理角度环绕。
    """
    md = _max_dist(room_poly)
    angles = np.linspace(0.0, 2.0 * np.pi, n_rays, endpoint=False)
    wall_d = _cast(room_poly.boundary, viewpoint, angles, md)
    blocked = np.zeros(n_rays, dtype=bool)
    for p in placements:
        if p.h < tall_mm or p.inst == exclude_inst:
            continue
        d = _cast(p.footprint().boundary, viewpoint, angles, md)
        blocked |= (d < wall_d - 1.0)
    return float(blocked.mean())


def isovist_metrics(room_poly: Polygon, obstacles=(), viewpoint=(0, 0),
                    n_rays: int = 360, placements=None,
                    occlusion: float | None = None) -> IsovistMetrics:
    iso = isovist(room_poly, obstacles, viewpoint, n_rays)
    area = float(iso.area)
    perim = float(iso.length)
    md = _max_dist(room_poly)
    angles = np.linspace(0.0, 2.0 * np.pi, n_rays, endpoint=False)
    d = _cast(_occluder(room_poly, obstacles), viewpoint, angles, md)
    half = n_rays // 2
    if half >= 1:
        longest = float(np.max(d[:half] + d[half:2 * half]))
    else:
        longest = float(d.max()) * 2.0
    compact = (4.0 * np.pi * area / (perim ** 2)) if perim > 0 else 0.0
    if occlusion is None:
        occlusion = occlusion_ratio(room_poly, placements or (), viewpoint, n_rays)
    return IsovistMetrics(
        area_mm2=area,
        area_m2=area / 1e6,
        perimeter_mm=perim,
        compactness=float(compact),
        longest_sightline_mm=longest,
        visible_floor_ratio=area / float(room_poly.area) if room_poly.area > 0 else 0.0,
        occlusion_ratio=float(occlusion),
    )


def key_viewpoints(room, layout=None, extra=()) -> list[tuple[int, int]]:
    """默认视点集合 —— 进门第一眼最重要，所以它排第一。"""
    pts: list[tuple[int, int]] = [room.entrance_point()]
    poly = room.usable_geo()
    c = poly.representative_point()
    pts.append((int(c.x), int(c.y)))
    pts.extend(room.window_centers())
    if layout is not None:
        for p in layout.placements:
            if p.category in ("sofa", "bed", "chair", "desk"):
                cx, cy = p.center()
                pts.append((int(cx), int(cy)))
    for p in extra:
        pts.append((int(p[0]), int(p[1])))
    # 去重（同一点不重复算）
    seen, out = set(), []
    for p in pts:
        key = (p[0] // 100, p[1] // 100)
        if key not in seen and poly.contains(shapely.Point(*p)):
            seen.add(key)
            out.append(p)
    return out


GRID_CELL_MM = GRID_MM
