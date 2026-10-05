"""显大分（perceived spaciousness）—— 技术方案 v3.2 §2.1 / §14.1

L1（几何可见性）+ L2（感知属性）。L3（VLM 成对比较）demo 阶段不做。

★ 核心认知：显大 != 少放东西，而是
    ① 主视线通畅  ② 体量集中到低曝光区  ③ 视觉重量轻

评分公式（校准见 _calibrate.py；M1 阶段会用 VLM 成对比较重新校准）：

    t_vfr     = (visible_floor_ratio - 0.30) / 0.45     可见地板越多越好
    t_sight   = (sightline_ratio     - 0.35) / 0.50     视线越深越好
    t_occ     = 1 - max(0, occ - 0.20) / 0.45           眼高遮挡越少越好
    t_mass    = 1 - max(0, mass_exp - 0.35) / 0.40      ★ 体量曝光越低越好（权重最高）
    t_corridor= 1 - corridor_penalty / 0.50             关键视廊越通畅越好

    score = 25 + 75 * (0.26 t_vfr + 0.14 t_sight + 0.18 t_occ + 0.30 t_mass + 0.12 t_corridor)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely

from .corridor import corridor_reports, default_corridors
from .exposure import (ExposureMap, layout_mass_exposure, layout_visual_load,
                       mass_exposure_breakdown)
from .isovist import isovist, isovist_metrics, key_viewpoints, occlusion_ratio
from .units import clamp01

WEIGHTS = {
    "visible_floor": 0.26,
    "sightline": 0.14,
    "occlusion": 0.18,
    "mass_exposure": 0.30,     # ★ 核心机制，权重最高
    "corridor": 0.12,
}

BASE_SCORE = 25.0      # 地板分：一个「能住但没优化过」的房间大约落在 40-50
SPAN_SCORE = 75.0

# 阈值在 CP-SAT 求解器上线后重新校准过一次 ——
# 原来是在贪心时代定的，对 CP-SAT 出来的解太松，一眼就满分（饱和）。
# 校准目标：CP-SAT 的好方案落在 85-93，基线落在 55-70。
VFR_MIN, VFR_GOOD = 0.35, 0.88
SIGHT_MIN, SIGHT_GOOD = 0.40, 0.98
OCC_FREE, OCC_MAX = 0.10, 0.45
MASS_FREE, MASS_MAX = 0.16, 0.45      # 绝对视觉负荷（Σ mass x exposure / 面积）
CORR_MAX = 0.35

W_OCC_ENTRANCE = 0.6       # 入口遮挡权重（进门第一眼最重要）
W_OCC_ALL = 0.4


@dataclass
class SpaciousnessReport:
    score: float
    metrics: dict
    detractors: list[dict] = field(default_factory=list)
    passes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return "顯大分 %.0f/100" % self.score


def spaciousness_report(room, layout, emap: ExposureMap | None = None,
                        corridors=None, viewpoints=None) -> SpaciousnessReport:
    if viewpoints is None:
        viewpoints = key_viewpoints(room, layout)
    if corridors is None:
        corridors = default_corridors(room)

    poly = room.usable_geo()
    fps = layout.footprints()

    isos = []
    per_vp = []
    for vp in viewpoints:
        iso = isovist(poly, fps, vp, 360)
        if not iso.is_empty:
            isos.append(iso)
        m = isovist_metrics(poly, fps, vp, placements=layout.placements)
        per_vp.append((vp, m))

    ent = room.entrance_point()
    ent_m = next((m for vp, m in per_vp if vp == ent), per_vp[0][1])
    union = shapely.union_all(isos) if isos else poly
    vfr = float(union.area) / float(poly.area) if poly.area > 0 else 0.0

    occ_ent = occlusion_ratio(poly, layout.placements, ent)
    occ_all = float(np.mean([m.occlusion_ratio for _, m in per_vp]))
    occ = W_OCC_ENTRANCE * occ_ent + W_OCC_ALL * occ_all

    best_sight = float(max(m.longest_sightline_mm for _, m in per_vp))
    diag = room.diagonal_mm()
    sight_ratio = best_sight / diag if diag > 0 else 0.0

    room_area = room.geo().area / 1e6
    mass_exp = (layout_visual_load(layout, emap, room_area)
                if emap is not None else MASS_FREE)
    creps = corridor_reports(corridors, layout.placements)
    cpen = float(sum(r["penalty_norm"] for r in creps)) / max(1, len(creps))

    t_vfr = clamp01((vfr - VFR_MIN) / (VFR_GOOD - VFR_MIN))
    t_sight = clamp01((sight_ratio - SIGHT_MIN) / (SIGHT_GOOD - SIGHT_MIN))
    t_occ = clamp01(1.0 - max(0.0, occ - OCC_FREE) / (OCC_MAX - OCC_FREE))
    t_mass = clamp01(1.0 - max(0.0, mass_exp - MASS_FREE) / (MASS_MAX - MASS_FREE))
    t_corr = clamp01(1.0 - cpen / CORR_MAX)

    weighted = (WEIGHTS["visible_floor"] * t_vfr + WEIGHTS["sightline"] * t_sight
                + WEIGHTS["occlusion"] * t_occ + WEIGHTS["mass_exposure"] * t_mass
                + WEIGHTS["corridor"] * t_corr)
    score = float(max(0.0, min(100.0, BASE_SCORE + SPAN_SCORE * weighted)))

    metrics = {
        "visible_floor_ratio": round(vfr, 3),
        "entrance_isovist_area_m2": round(ent_m.area_m2, 2),
        "entrance_longest_sightline_mm": int(round(ent_m.longest_sightline_mm)),
        "best_longest_sightline_mm": int(round(best_sight)),
        "sightline_ratio": round(sight_ratio, 3),
        "occlusion_entrance": round(occ_ent, 3),
        "occlusion_avg": round(occ_all, 3),
        "occlusion_blend": round(occ, 3),
        "visual_load": round(mass_exp, 3),
        "visual_mass_exposure": round(layout_mass_exposure(layout, emap), 3)
                                 if emap is not None else 0.0,
        "corridor_penalty": round(cpen, 3),
        "room_diagonal_mm": int(round(diag)),
        "n_viewpoints": len(viewpoints),
        "terms": {"visible_floor": round(t_vfr, 3), "sightline": round(t_sight, 3),
                  "occlusion": round(t_occ, 3), "mass_exposure": round(t_mass, 3),
                  "corridor": round(t_corr, 3)},
    }

    det, ok = _diagnose(room, layout, emap, creps, mass_exp, vfr, occ_ent, occ_all)
    return SpaciousnessReport(score=score, metrics=metrics, detractors=det, passes=ok)


def _diagnose(room, layout, emap, creps, mass_exp, vfr, occ_ent, occ_all):
    """生成「现在让它显小的 N 个原因」—— 直接给用户看的东西。"""
    det: list[dict] = []
    ok: list[str] = []

    # ① 视觉屏障
    blocked = sorted([r for r in creps if not r["clear"]], key=lambda r: -r["penalty_norm"])
    for r in blocked[:2]:
        b = r["blockers"][0]
        item = layout.by_inst(b["inst"])
        det.append({
            "issue": "视觉屏障", "subject": b["inst"],
            "category": item.category if item else "",
            "detail": "%s 高 %dmm，挡在「%s」视线上，超出视廊高度上限 %dmm" % (
                b["inst"], b["height_mm"], r["label"], b["over_mm"]),
            "severity": "high" if r["penalty_norm"] > 0.10 else "medium",
            "fix_options": ["挪到低曝光区", "换成矮款", "旋转 90 度"],
        })
    if not blocked:
        ok.append("進門一眼能看到窗，主視線通暢")

    # ② 眼高遮挡
    tall = [p for p in layout.placements if p.h >= 1200]
    if tall and occ_ent > 0.15:
        base = occlusion_ratio(room.usable_geo(), layout.placements, room.entrance_point())
        per_item = []
        for p in tall:
            o = occlusion_ratio(room.usable_geo(), layout.placements,
                                room.entrance_point(), exclude_inst=p.inst)
            per_item.append((p, base - o))
        per_item.sort(key=lambda t: -t[1])
        p, contrib = per_item[0]
        if contrib > 0.02:
            det.append({
                "issue": "眼高遮挡", "subject": p.inst, "category": p.category,
                "detail": "高 %dmm 正好在眼高附近，单它就挡掉进门时 %.0f%% 的横向视野" % (
                    p.h, contrib * 100),
                "severity": "high" if contrib > 0.12 else "medium",
                "fix_options": ["换 450mm 矮款", "降低高度", "移到侧墙"],
            })
    else:
        ok.append("沒有高家具擋在進門視野裏")

    # ③ 体量在高曝光区 —— 只有「最重的家具确实放在高于平均曝光度的位置」才算问题
    rows_me = mass_exposure_breakdown(layout, emap) if emap is not None else []
    top_exp = rows_me[0]["exposure"] if rows_me else 0.0
    mean_exp = emap.mean_exposure() if emap is not None else 0.0
    if emap is not None and mass_exp > MASS_FREE and top_exp > mean_exp * 1.05:
        rows = rows_me
        top = rows[0]
        item = layout.by_inst(top["inst"])
        det.append({
            "issue": "体量在高曝光区", "subject": top["inst"],
            "category": item.category if item else "",
            "detail": "%s 视觉体量 %.2f，却放在曝光度 %.2f 的显眼位置（全屋体量曝光贡献最大）" % (
                top["inst"], top["visual_mass"], top["exposure"]),
            "severity": "high" if mass_exp > 0.50 else "medium",
            "fix_options": ["挪到低曝光区（角位 / 凹位）", "换浅色开放格款"],
        })
    else:
        ok.append("大体量家具都藏在低曝光區")

    # ④ 可见地板占比
    if vfr < VFR_GOOD:
        det.append({
            "issue": "可见地板太少", "subject": "", "category": "",
            "detail": "关键视点合计只能看到 %.0f%% 的地板（目标 %.0f%% 以上）"
                      % (vfr * 100, VFR_GOOD * 100),
            "severity": "high" if vfr < 0.45 else "medium",
            "fix_options": ["减少落地家具", "改用带腿 / 挂墙款", "腾出主通道"],
        })
    else:
        ok.append("可見地板佔比 %.0f%%，視野開闊" % (vfr * 100))

    return det, ok
