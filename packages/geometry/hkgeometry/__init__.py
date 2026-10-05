"""hkgeometry —— 整屋 3D 空间优化的几何内核。

冻结规范（见技术方案 v3.2 §8）：
  · 内部唯一单位 = 整数毫米 (int mm)
  · 角度 = 整数度，限制在 {0, 90, 180, 270}
  · 任何接收 float 长度的地方都是 bug
"""
from .units import (
    EPS_AREA_MM2, EPS_DIST_MM, CLEARANCE_MARGIN_MM, ROOM_MARGIN_MM, GRID_MM,
    ft_in_to_mm, to_mm, as_int_mm,
)
from .types import Room, Opening, Wall, Placement, Layout, HeightPatch, Service
from .isovist import isovist, isovist_metrics, occlusion_ratio, IsovistMetrics
from .exposure import exposure_map, ExposureMap, layout_mass_exposure
from .corridor import Corridor, corridor_penalty, corridor_report, default_corridors
from .spaciousness import spaciousness_report, SpaciousnessReport

__all__ = [
    "EPS_AREA_MM2", "EPS_DIST_MM", "CLEARANCE_MARGIN_MM", "ROOM_MARGIN_MM", "GRID_MM",
    "ft_in_to_mm", "to_mm", "as_int_mm",
    "Room", "Opening", "Wall", "Placement", "Layout", "HeightPatch", "Service",
    "isovist", "isovist_metrics", "occlusion_ratio", "IsovistMetrics",
    "exposure_map", "ExposureMap", "layout_mass_exposure",
    "Corridor", "corridor_penalty", "corridor_report", "default_corridors",
    "spaciousness_report", "SpaciousnessReport",
]
