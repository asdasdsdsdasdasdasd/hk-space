"""视觉走廊（sightline corridor）—— 技术方案 v3.2 §2.5

比完整 isovist 更简单、更鲁棒的近似：手选几条关键视廊，
惩罚落在视廊上、且高度超过阈值的家具。

规则一句话：**让所有关键视廊上的家具高度都 < 1100mm。**
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString

from .units import GRID_MM

CORRIDOR_MAX_H_MM = 1100      # 眼高以下不算挡（坐姿眼高约 1100-1200）


@dataclass
class Corridor:
    id: str
    a: tuple[int, int]
    b: tuple[int, int]
    max_height_mm: int = CORRIDOR_MAX_H_MM
    label: str = ""

    def geom(self) -> LineString:
        return LineString([(float(self.a[0]), float(self.a[1])),
                           (float(self.b[0]), float(self.b[1]))])

    def length_mm(self) -> float:
        return float(self.geom().length)


def _farthest_point(poly, origin, cell: int = 200) -> tuple[int, int]:
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx + cell / 2, maxx, cell)
    ys = np.arange(miny + cell / 2, maxy, cell)
    X, Y = np.meshgrid(xs, ys)
    ok = np.asarray(shapely.contains_xy(poly, X, Y))
    if not ok.any():
        c = poly.representative_point()
        return int(c.x), int(c.y)
    d = (X - origin[0]) ** 2 + (Y - origin[1]) ** 2
    d = np.where(ok, d, -1)
    iy, ix = np.unravel_index(int(np.argmax(d)), d.shape)
    return int(X[iy, ix]), int(Y[iy, ix])


def default_corridors(room, include_deepest: bool = False) -> list[Corridor]:
    """默认视廊：进门 -> 每一扇窗。

    方案 §2.5 定的规则就一条 —— **进门要能看到窗**。
    「进门 -> 房间最深处」是次要的，默认不开，因为它常常和
    「大衣柜必须靠墙」冲突，会把好方案也罚掉。
    """
    ent = room.entrance_point()
    cors: list[Corridor] = []
    for i, wc in enumerate(room.window_centers()):
        cors.append(Corridor(f"entry_to_window_{i}", ent, wc, label="進門→窗"))
    if include_deepest:
        far = _farthest_point(room.usable_geo(), ent)
        cors.append(Corridor("entry_to_deepest", ent, far, label="進門→最深處"))
    return cors


def corridor_report(corridor: Corridor, placements) -> dict:
    """返回该视廊的遮挡报告。penalty_norm ∈ [0,1]，0 = 完全通畅。"""
    seg = corridor.geom()
    blockers = []
    penalty = 0.0
    for p in placements:
        inter = seg.intersection(p.footprint())
        if inter.is_empty:
            continue
        L = float(inter.length)
        if L <= 0:
            continue
        over = max(0, p.h - corridor.max_height_mm)
        if over <= 0:
            continue
        penalty += over * L
        blockers.append({"inst": p.inst, "category": p.category, "height_mm": p.h,
                         "over_mm": over, "overlap_mm": int(round(L))})
    L_cor = max(1.0, corridor.length_mm())
    norm = min(1.0, penalty / (L_cor * 500.0))
    blockers.sort(key=lambda b: -b["over_mm"] * b["overlap_mm"])
    return {"id": corridor.id, "label": corridor.label, "penalty": float(penalty),
            "penalty_norm": float(norm), "clear": penalty <= 0.0, "blockers": blockers}


def corridor_penalty(corridors, placements) -> float:
    """所有视廊的归一化惩罚之和（0 = 全部通畅）。"""
    if not corridors:
        return 0.0
    return float(sum(corridor_report(c, placements)["penalty_norm"] for c in corridors))


def corridor_reports(corridors, placements) -> list[dict]:
    return [corridor_report(c, placements) for c in corridors]
