#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_test_floorplan.py
======================
程序化生成 3 张「看起来像香港楼盘平面图」的测试图 + 对应的 ground-truth Room JSON。

绘图要素（按真实香港平面图的画法）:
  * 承重墙 load_bearing : 黑色实心填充的粗墙 (200mm)
  * 间隔墙 partition    : 细双线 (100mm)
  * 门   : 墙上开口 + 门扇线 + 90 度开启弧线
  * 窗   : 墙上开口 + 三道平行细线
  * 尺寸标注链: 细线 + 45 度斜杆 + 数字 (2600 / 3000 / 1200 ...)
  * 房间中文名 (客廳 / 睡房 / 廚房 / 浴室 / 儲物房 / 飯廳)
  * 拍照效果: 高斯噪点 + 椒盐噪点 + 轻微模糊 + 暗角 + 旋转 + 透视

输出:
  data/floorplans/plan_hk_a.png  (+ .truth.json)
  data/floorplans/plan_hk_b.png  (+ .truth.json)
  data/floorplans/plan_hk_c.png  (+ .truth.json)

所有长度单位 = 整数毫米 (int mm)。图片像素尺度 = px_per_mm (默认 0.2 => 5mm/px)。

用法:
  python3 services/floorplan/make_test_floorplan.py
  python3 services/floorplan/make_test_floorplan.py --out-dir /tmp/fp --seed 7
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data" / "floorplans"

SS = 3  # supersampling factor (超采样倍数, 用来抗锯齿)

# ----------------------------------------------------------------------------
# 字体
# ----------------------------------------------------------------------------
DIGIT_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Medium.ttf",
]
CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

DIM_TEXT_MM = 130      # 尺寸数字字高 (mm, 图纸尺度而非真实尺度)
LABEL_TEXT_MM = 300    # 房间名字高 (mm)
LINE_W_MM = 14         # 细线线宽 (mm) -> 5mm/px 下约 3px


def find_font(cands: List[str], size_px: int) -> ImageFont.FreeTypeFont:
    for p in cands:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size_px)
            except Exception:
                continue
    return ImageFont.load_default()


# ----------------------------------------------------------------------------
# 几何小工具
# ----------------------------------------------------------------------------
def _seg_intersect(p1, p2, p3, p4) -> bool:
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cross(p3, p4, p1), cross(p3, p4, p2)
    d3, d4 = cross(p1, p2, p3), cross(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _pt_seg_dist(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    abx, aby = bx - ax, by - ay
    den = abx * abx + aby * aby
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - ax) * abx + (p[1] - ay) * aby) / den))
    return math.hypot(p[0] - (ax + t * abx), p[1] - (ay + t * aby))


def seg_seg_dist(p1, p2, p3, p4) -> float:
    if _seg_intersect(p1, p2, p3, p4):
        return 0.0
    return min(_pt_seg_dist(p1, p3, p4), _pt_seg_dist(p2, p3, p4),
               _pt_seg_dist(p3, p1, p2), _pt_seg_dist(p4, p1, p2))


def pt_in_poly(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1
            if x < xin:
                inside = not inside
    return inside


# ----------------------------------------------------------------------------
# 图纸描述
# ----------------------------------------------------------------------------
@dataclass
class WallSpec:
    a: Tuple[int, int]
    b: Tuple[int, int]
    t: int = 100
    kind: str = "partition"   # load_bearing | partition


@dataclass
class OpeningSpec:
    wall: int          # index into walls
    center: int        # 沿墙方向, 从 wall.a 起算 (mm)
    width: int
    kind: str          # door | window
    hinge: str = "left"
    side: int = 1      # 门: 开向墙的哪一侧 (+1 = 左法线侧); 窗: 忽略
    entry: bool = False


@dataclass
class FlatSpec:
    flat_id: str
    name: str
    W: int
    H: int
    walls: List[WallSpec]
    openings: List[OpeningSpec]
    cells: List[Tuple[str, str, Tuple[int, int, int, int]]]  # (id,label,(x0,y0,x1,y1))
    beams: List[dict] = field(default_factory=list)
    services: List[Tuple[str, str, Tuple[int, int]]] = field(default_factory=list)
    px_per_mm: float = 0.2
    rotation_deg: float = 0.6
    noise: float = 0.012
    perspective: float = 0.0
    seed: int = 1


MARGINS = dict(left=1750, top=1700, right=950, bottom=1500)


# ----------------------------------------------------------------------------
# 画布
# ----------------------------------------------------------------------------
class Sheet:
    def __init__(self, spec: FlatSpec):
        self.spec = spec
        self.k = spec.px_per_mm * SS                    # canvas px per mm
        self.ox = MARGINS["left"] * self.k
        self.oy = MARGINS["top"] * self.k
        w = int(round((spec.W + MARGINS["left"] + MARGINS["right"]) * self.k))
        h = int(round((spec.H + MARGINS["top"] + MARGINS["bottom"]) * self.k))
        self.w, self.h = w, h
        self.img = Image.new("L", (w, h), 255)
        self.d = ImageDraw.Draw(self.img)
        self.f_dim = find_font(DIGIT_FONT_CANDIDATES, int(DIM_TEXT_MM * self.k))
        self.f_lab = find_font(CJK_FONT_CANDIDATES, int(LABEL_TEXT_MM * self.k))

    def P(self, x_mm: float, y_mm: float) -> Tuple[float, float]:
        return (self.ox + x_mm * self.k, self.oy + y_mm * self.k)

    def rect_mm(self, x0, y0, x1, y1, fill=None, outline=None, width_mm=None):
        p0 = self.P(x0, y0)
        p1 = self.P(x1, y1)
        w = None if width_mm is None else max(1, int(round(width_mm * self.k)))
        self.d.rectangle([p0, p1], fill=fill, outline=outline, width=w if w else 1)

    def line_mm(self, x0, y0, x1, y1, width_mm=LINE_W_MM, fill=0):
        self.d.line([self.P(x0, y0), self.P(x1, y1)], fill=fill,
                    width=max(1, int(round(width_mm * self.k))))

    def text_mm(self, x, y, s, font, anchor="mm"):
        self.d.text(self.P(x, y), s, font=font, fill=0, anchor=anchor)


# ----------------------------------------------------------------------------
# 墙体 / 门 / 窗 绘制
# ----------------------------------------------------------------------------
def wall_rect(w: WallSpec):
    (ax, ay), (bx, by) = w.a, w.b
    if ay == by:
        return (min(ax, bx), ay - w.t // 2, max(ax, bx), ay + w.t // 2)
    if ax == bx:
        return (ax - w.t // 2, min(ay, by), ax + w.t // 2, max(ay, by))
    raise ValueError("只支持正交墙")


def draw_wall(sheet: Sheet, w: WallSpec):
    x0, y0, x1, y1 = wall_rect(w)
    if w.kind == "load_bearing":
        sheet.rect_mm(x0, y0, x1, y1, fill=0)
    else:
        sheet.rect_mm(x0, y0, x1, y1, outline=0, width_mm=LINE_W_MM)


def _erase(sheet: Sheet, box, pad_mm=6):
    x0, y0, x1, y1 = box
    sheet.rect_mm(x0 - pad_mm, y0 - pad_mm, x1 + pad_mm, y1 + pad_mm, fill=255)


def _arc_pts(cx, cy, r, p_from, p_to, n=28):
    a0 = math.atan2(p_from[1] - cy, p_from[0] - cx)
    a1 = math.atan2(p_to[1] - cy, p_to[0] - cx)
    d = (a1 - a0)
    while d > math.pi:
        d -= 2 * math.pi
    while d < -math.pi:
        d += 2 * math.pi
    return [(cx + r * math.cos(a0 + d * i / n), cy + r * math.sin(a0 + d * i / n))
            for i in range(n + 1)]


def draw_door(sheet: Sheet, wall: WallSpec, op: OpeningSpec):
    (ax, ay), (bx, by) = wall.a, wall.b
    ux, uy = (1, 0) if ay == by else (0, 1)
    if by < ay or bx < ax:
        ux, uy = -ux, -uy
    cxp, cyp = ax + ux * op.center, ay + uy * op.center
    hw = op.width / 2.0
    p1 = (cxp - ux * hw, cyp - uy * hw)        # 近 a 侧门垛
    p2 = (cxp + ux * hw, cyp + uy * hw)        # 近 b 侧门垛
    hinge = p1 if op.hinge == "left" else p2
    other = p2 if op.hinge == "left" else p1
    # 左法线 (y 向下坐标系): 把 u 逆时针转 90 度
    nx, ny = uy * op.side, -ux * op.side
    leaf_end = (hinge[0] + nx * op.width, hinge[1] + ny * op.width)
    sheet.line_mm(hinge[0], hinge[1], leaf_end[0], leaf_end[1], width_mm=LINE_W_MM * 0.8)
    pts = _arc_pts(hinge[0], hinge[1], op.width, other, leaf_end)
    sheet.d.line([sheet.P(*p) for p in pts], fill=0,
                 width=max(1, int(round(LINE_W_MM * 0.65 * sheet.k))))


def draw_window(sheet: Sheet, wall: WallSpec, op: OpeningSpec):
    (ax, ay), (bx, by) = wall.a, wall.b
    ux, uy = (1, 0) if ay == by else (0, 1)
    if by < ay or bx < ax:
        ux, uy = -ux, -uy
    cxp, cyp = ax + ux * op.center, ay + uy * op.center
    hw = op.width / 2.0
    nx, ny = uy, -ux
    off = wall.t * 0.28
    for s in (-1, 1):
        o = off * s
        sheet.line_mm(cxp - ux * hw + nx * o, cyp - uy * hw + ny * o,
                      cxp + ux * hw + nx * o, cyp + uy * hw + ny * o,
                      width_mm=LINE_W_MM * 0.8)
    # 两端封口
    for s in (-1, 1):
        px = cxp + ux * hw * s
        py = cyp + uy * hw * s
        sheet.line_mm(px - nx * off, py - ny * off, px + nx * off, py + ny * off,
                      width_mm=LINE_W_MM * 0.7)


def apply_openings(sheet: Sheet, spec: FlatSpec):
    for op in spec.openings:
        w = spec.walls[op.wall]
        x0, y0, x1, y1 = wall_rect(w)
        (ax, ay), (bx, by) = w.a, w.b
        ux, uy = (1, 0) if ay == by else (0, 1)
        if by < ay or bx < ax:
            ux, uy = -ux, -uy
        cx, cy = ax + ux * op.center, ay + uy * op.center
        hw = op.width / 2.0
        gap = (min(cx - ux * hw, cx + ux * hw), y0, max(cx - ux * hw, cx + ux * hw), y1) \
            if ay == by else (x0, min(cy - uy * hw, cy + uy * hw), x1, max(cy - uy * hw, cy + uy * hw))
        _erase(sheet, gap)
        if op.kind == "door":
            draw_door(sheet, w, op)
        else:
            draw_window(sheet, w, op)


# ----------------------------------------------------------------------------
# 尺寸标注链
# ----------------------------------------------------------------------------
def dimension_chain(sheet: Sheet, spec: FlatSpec, side: str, offset: int,
                    segments=None, draw_ticks=True):
    """在图纸外侧画一条尺寸标注链。segments=None 时自动按墙位置切分。"""
    W, H = spec.W, spec.H
    if segments is None:
        if side in ("top", "bottom"):
            cuts = sorted({0, W} | {w.a[0] for w in spec.walls if w.a[0] == w.b[0]})
        else:
            cuts = sorted({0, H} | {w.a[1] for w in spec.walls if w.a[1] == w.b[1]})
        segments = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]
    for (s, e) in segments:
        if e - s <= 0:
            continue
        val = int(e - s)
        if side in ("top", "bottom"):
            y = (-offset) if side == "top" else (H + offset)
            sheet.line_mm(s, y, e, y, width_mm=LINE_W_MM * 0.55)
            if draw_ticks:
                for x in (s, e):
                    sheet.line_mm(x - 120, y + 120, x + 120, y - 120, width_mm=LINE_W_MM * 0.55)
                ty = y - DIM_TEXT_MM * 0.95 if side == "top" else y + DIM_TEXT_MM * 1.05
                sheet.text_mm((s + e) / 2, ty, str(val), sheet.f_dim)
        else:
            x = (-offset) if side == "left" else (W + offset)
            sheet.line_mm(x, s, x, e, width_mm=LINE_W_MM * 0.55)
            if draw_ticks:
                for y in (s, e):
                    sheet.line_mm(x - 120, y + 120, x + 120, y - 120, width_mm=LINE_W_MM * 0.55)
                # 注: HK 图纸竖排尺寸数字通常是转 90 度的, 这里为了 demo 可读性保持水平,
                #     README 里已说明这是与真实图纸的一处差异。
                tx = x - DIM_TEXT_MM * 1.0 if side == "left" else x + DIM_TEXT_MM * 1.0
                sheet.text_mm(tx, (s + e) / 2, str(val), sheet.f_dim)


# ----------------------------------------------------------------------------
# 真实几何 -> 房间多边形 (用于 ground truth)
# ----------------------------------------------------------------------------
def rasterize_true(spec: FlatSpec, px_per_mm: float) -> np.ndarray:
    W = int(round((spec.W + MARGINS["left"] + MARGINS["right"]) * px_per_mm))
    H = int(round((spec.H + MARGINS["top"] + MARGINS["bottom"]) * px_per_mm))
    m = np.zeros((H, W), np.uint8)
    ox = MARGINS["left"] * px_per_mm
    oy = MARGINS["top"] * px_per_mm

    def px(x, y):
        return int(round(ox + x * px_per_mm)), int(round(oy + y * px_per_mm))

    for w in spec.walls:
        x0, y0, x1, y1 = wall_rect(w)
        cv2.rectangle(m, px(x0, y0), px(x1, y1), 255, -1)
    # 封住所有开口, 房间才会被分隔开
    for op in spec.openings:
        w = spec.walls[op.wall]
        x0, y0, x1, y1 = wall_rect(w)
        (ax, ay), (bx, by) = w.a, w.b
        ux, uy = (1, 0) if ay == by else (0, 1)
        if by < ay or bx < ax:
            ux, uy = -ux, -uy
        cx, cy = ax + ux * op.center, ay + uy * op.center
        hw = op.width / 2.0
        if ay == by:
            gx0, gx1 = cx - hw, cx + hw
            cv2.rectangle(m, px(gx0, y0), px(gx1, y1), 255, -1)
        else:
            gy0, gy1 = cy - hw, cy + hw
            cv2.rectangle(m, px(x0, gy0), px(x1, gy1), 255, -1)
    return m


def true_room_polygons(spec: FlatSpec, px_per_mm: float):
    m = rasterize_true(spec, px_per_mm)
    free = (m == 0).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(free, 4)
    H, W = m.shape
    border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    min_area = (1200 * px_per_mm) ** 2   # >= 1.2m x 1.2m 的空腔才算房间
    out = []
    for i in range(1, n):
        if i in border:
            continue
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            continue
        comp = (lab == i).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cnts, key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, 1.4, True).reshape(-1, 2)
        # px -> mm 是除以 px_per_mm (px_per_mm = 每毫米多少像素)
        poly = [(int(round(p[0] / px_per_mm - MARGINS["left"])),
                 int(round(p[1] / px_per_mm - MARGINS["top"]))) for p in ap]
        cx = cent[i][0] / px_per_mm - MARGINS["left"]
        cy = cent[i][1] / px_per_mm - MARGINS["top"]
        out.append((poly, (cx, cy), int(stats[i, cv2.CC_STAT_AREA] / (px_per_mm ** 2))))
    return out


def _opening_geom(w: WallSpec, op: OpeningSpec):
    """返回 (中心点, 单位方向, 左法线) —— 全部在图纸 mm 坐标系里。"""
    (ax, ay), (bx, by) = w.a, w.b
    ux, uy = (1, 0) if ay == by else (0, 1)
    if by < ay or bx < ax:
        ux, uy = -ux, -uy
    cx, cy = ax + ux * op.center, ay + uy * op.center
    nx, ny = uy, -ux          # y 向下的坐标系里的左法线
    return (cx, cy), (ux, uy), (nx, ny)


def build_truth(spec: FlatSpec) -> dict:
    """用「精确已知的几何」生成 ground truth Room JSON。

    房间多边形不是手写的, 而是把真实墙栅格化 + 封住开口后做连通域填充得到的,
    这样和 vectorize.py 的输出同构 (都取「墙内表面」), 对比才有意义。
    """
    px = spec.px_per_mm
    regs = true_room_polygons(spec, px)
    rooms = []
    for poly, (cx, cy), area_mm2 in regs:
        # 用「网格采样重叠率」判归属, 而不是重心 (L 形房间的重心会跑到别的格子里)
        apt = [tuple(p) for p in poly]
        rid, label, best = "room", "房間", 0.0
        for (i, lb, (x0, y0, x1, y1)) in spec.cells:
            gx = np.linspace(x0 + 40, x1 - 40, 12)
            gy = np.linspace(y0 + 40, y1 - 40, 12)
            hit = sum(1 for a in gx for b in gy if pt_in_poly((a, b), apt))
            frac = hit / (len(gx) * len(gy))
            if frac > best:
                best, rid, label = frac, i, lb
        rooms.append(dict(id=rid, label=label,
                          polygon=[[int(p[0]), int(p[1])] for p in poly],
                          area_mm2=area_mm2))

    # 每个房间挂上它四周的墙 (图纸坐标系)
    def walls_of(poly):
        apt = [tuple(p) for p in poly]
        idx = []
        for wi, w in enumerate(spec.walls):
            tol = w.t / 2 + 140
            d = min(seg_seg_dist(w.a, w.b, apt[i], apt[(i + 1) % len(apt)])
                    for i in range(len(apt)))
            mid = ((w.a[0] + w.b[0]) / 2.0, (w.a[1] + w.b[1]) / 2.0)
            if d <= tol and not pt_in_poly(mid, apt):
                idx.append(wi)
        return idx

    for r in rooms:
        ws = walls_of(r["polygon"])
        r["_wall_idx"] = ws
        r["walls"] = [dict(id="w%d" % k, a=[spec.walls[i].a[0], spec.walls[i].a[1]],
                           b=[spec.walls[i].b[0], spec.walls[i].b[1]],
                           type=spec.walls[i].kind, thickness_mm=int(spec.walls[i].t))
                      for k, i in enumerate(ws)]
        r["openings"] = []
        r["height_mm"] = 2450
        r["height_field"] = [dict(b) for b in spec.beams]

    allp = [p for r in rooms for p in r["polygon"]]
    bminx = min(p[0] for p in allp)
    bminy = min(p[1] for p in allp)
    center = ((bminx + max(p[0] for p in allp)) / 2.0,
              (bminy + max(p[1] for p in allp)) / 2.0)

    connections, entry = [], None
    for op in spec.openings:
        w = spec.walls[op.wall]
        (cx, cy), (ux, uy), (nx, ny) = _opening_geom(w, op)
        side_hit = []
        for s in (-1, 1):
            pt = (cx + nx * (w.t / 2.0 + 60) * s, cy + ny * (w.t / 2.0 + 60) * s)
            for r in rooms:
                if pt_in_poly(pt, [tuple(p) for p in r["polygon"]]):
                    side_hit.append((s, r))
                    break
        for s, r in side_hit:
            k = next((j for j, wi in enumerate(r["_wall_idx"]) if wi == op.wall), None)
            if k is None:
                continue
            r["openings"].append(dict(
                id="%s%d" % ("d" if op.kind == "door" else "n", len(r["openings"])),
                wall="w%d" % k, kind=op.kind,
                center_mm=int(op.center), width_mm=int(op.width),
                swing=(dict(dir="in", hinge=op.hinge) if op.kind == "door" else None)))
        if len(side_hit) == 2:
            connections.append(dict(a=side_hit[0][1]["id"], b=side_hit[1][1]["id"],
                                    type="doorway" if op.kind == "door" else "window",
                                    width_mm=int(op.width)))
        if op.entry:
            away = (cx + nx * 300, cy + ny * 300)
            near = (cx - nx * 300, cy - ny * 300)
            out_s = 1 if math.hypot(away[0] - center[0], away[1] - center[1]) > \
                math.hypot(near[0] - center[0], near[1] - center[1]) else -1
            entry = dict(room=(side_hit[0][1]["id"] if side_hit else rooms[0]["id"]),
                         point=[int(round(cx + nx * 100 * out_s)),
                                int(round(cy + ny * 100 * out_s))])

    services = []
    for (rid, kind, (dx, dy)) in spec.services:
        rm = next((r for r in rooms if r["id"] == rid), None)
        if rm is None:
            continue
        xs = [p[0] for p in rm["polygon"]]
        ys = [p[1] for p in rm["polygon"]]
        x = min(xs) + dx if dx >= 0 else max(xs) + dx
        y = min(ys) + dy if dy >= 0 else max(ys) + dy
        services.append(dict(id="%s_%s" % (kind, rid), kind=kind, room=rid,
                             point=[int(x), int(y)]))

    # ---- 全部几何都算完后, 再整体平移到以 (0,0) 为起点 (与 vectorize.py 约定一致) ----
    def sh(p):
        return [int(p[0]) - int(bminx), int(p[1]) - int(bminy)]

    for r in rooms:
        r.pop("_wall_idx", None)
        r["polygon"] = [sh(p) for p in r["polygon"]]
        for wj in r["walls"]:
            wj["a"] = sh(wj["a"])
            wj["b"] = sh(wj["b"])
        for b in r["height_field"]:
            b["region"] = [sh(p) for p in b["region"]]
    for c in connections:
        pass
    for s in services:
        s["point"] = sh(s["point"])
    if entry:
        entry["point"] = sh(entry["point"])

    return dict(flat_id=spec.flat_id, units="mm", rooms=rooms, connections=connections,
                fixed_services=services,
                entry=entry or dict(room=rooms[0]["id"], point=[0, 0]),
                meta=dict(source="synthetic_truth", confidence=1.0,
                          plan_size_mm=[spec.W, spec.H], px_per_mm=spec.px_per_mm,
                          rotation_deg=spec.rotation_deg))


# ----------------------------------------------------------------------------
# 拍照效果
# ----------------------------------------------------------------------------
def photo_effects(arr: np.ndarray, spec: FlatSpec, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = arr.shape
    # 旋转
    if abs(spec.rotation_deg) > 1e-6:
        M = cv2.getRotationMatrix2D((w / 2, h / 2), spec.rotation_deg, 1.0)
        arr = cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    # 透视 (手机没有正对图纸)
    if spec.perspective > 0:
        d = spec.perspective
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = src + rng.uniform(-d, d, size=(4, 2)).astype(np.float32) * np.float32([w, h])
        M = cv2.getPerspectiveTransform(src, dst)
        arr = cv2.warpPerspective(arr, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    # 轻微模糊 (对焦不完美)
    arr = cv2.GaussianBlur(arr, (3, 3), 0.55 + 1.5 * spec.noise * 20)
    # 暗角
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    vig = 1.0 - 0.20 * np.clip(r / 1.42, 0, 1) ** 2
    arr = arr.astype(np.float32) * vig
    # 高斯噪点
    arr = arr + rng.normal(0, spec.noise * 255, arr.shape)
    # 椒盐噪点
    n_salt = int(0.00016 * h * w)
    ys = rng.integers(0, h, n_salt * 2)
    xs = rng.integers(0, w, n_salt * 2)
    for i in range(n_salt):
        arr[ys[i], xs[i]] = rng.uniform(0, 90)
    for i in range(n_salt, n_salt * 2):
        arr[ys[i], xs[i]] = 255
    return np.clip(arr, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def render(spec: FlatSpec, out_dir: Path) -> Tuple[Path, Path]:
    sheet = Sheet(spec)
    for w in spec.walls:
        draw_wall(sheet, w)
    apply_openings(sheet, spec)
    for (_, label, (x0, y0, x1, y1)) in spec.cells:
        sheet.text_mm((x0 + x1) / 2, (y0 + y1) / 2, label, sheet.f_lab)
    # 尺寸标注链: 上/左 分格链 + 下 总长链
    dimension_chain(sheet, spec, "top", 620)
    dimension_chain(sheet, spec, "left", 620)
    dimension_chain(sheet, spec, "bottom", 700, segments=[(0, spec.W)])
    dimension_chain(sheet, spec, "right", 700, segments=[(0, spec.H)])

    big = np.asarray(sheet.img.convert("L"))
    small = cv2.resize(big, (sheet.w // SS, sheet.h // SS), interpolation=cv2.INTER_AREA)
    img = photo_effects(small, spec, spec.seed)

    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / ("%s.png" % spec.name)
    cv2.imwrite(str(png), img)
    truth = build_truth(spec)
    tpath = out_dir / ("%s.truth.json" % spec.name)
    tpath.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return png, tpath


def _flats() -> List[FlatSpec]:
    # ---------------- Flat A: 8400 x 5200 (约 460 呎), 上横下四分 ----------------
    W, H = 8400, 5200
    wa = [
        WallSpec((0, 0), (W, 0), 200, "load_bearing"),
        WallSpec((W, 0), (W, H), 200, "load_bearing"),
        WallSpec((W, H), (0, H), 200, "load_bearing"),
        WallSpec((0, H), (0, 0), 200, "load_bearing"),
        WallSpec((0, 2000), (W, 2000), 100, "partition"),
        WallSpec((2600, 2000), (2600, H), 100, "partition"),
        WallSpec((5600, 2000), (5600, H), 100, "partition"),
        WallSpec((7200, 2000), (7200, H), 100, "partition"),
    ]
    oa = [
        OpeningSpec(0, 2100, 1800, "window", side=1),              # 客廳落地窗 (北)
        OpeningSpec(0, 4950, 1500, "window", side=1),              # 客廳窗 (北)
        OpeningSpec(1, 4000, 1800, "window", side=1),              # 儲物房窗 (東)
        OpeningSpec(2, 1700, 600, "window", side=1),               # 廚房小窗 (南)
        OpeningSpec(2, 3200, 1200, "window", side=1),              # 睡房窗 (南)
        OpeningSpec(3, 4200, 900, "door", hinge="left", side=1, entry=True),  # 大門 -> 客廳
        OpeningSpec(4, 1300, 800, "door", hinge="left", side=1),   # 客廳 -> 廚房
        OpeningSpec(4, 3600, 850, "door", hinge="right", side=1),  # 客廳 -> 睡房
        OpeningSpec(4, 6400, 750, "door", hinge="left", side=1),   # 客廳 -> 浴室
        OpeningSpec(4, 7800, 700, "door", hinge="right", side=-1), # 客廳 -> 儲物房
    ]
    ca = [("living", "客廳", (0, 0, W, 2000)),
          ("kitchen", "廚房", (0, 2000, 2600, H)),
          ("bedroom", "睡房", (2600, 2000, 5600, H)),
          ("bath", "浴室", (5600, 2000, 7200, H)),
          ("storage", "儲物房", (7200, 2000, W, H))]
    spec_a = FlatSpec("f_demo", "plan_hk_a", W, H, wa, oa, ca,
                      beams=[dict(region=[[0, 1900], [W, 1900], [W, 2100], [0, 2100]],
                                  z_max=2100, kind="beam")],
                      services=[("kitchen", "floor_drain", (400, -400)),
                                ("bath", "floor_drain", (300, -300)),
                                ("bath", "water_heater", (-350, 350))],
                      px_per_mm=0.2, rotation_deg=0.6, noise=0.010,
                      perspective=0.0, seed=11)

    # ---------------- Flat B: 6000 x 4800 (约 310 呎), L 形客廳 ----------------
    W, H = 6000, 4800
    wb = [
        WallSpec((0, 0), (W, 0), 200, "load_bearing"),
        WallSpec((W, 0), (W, H), 200, "load_bearing"),
        WallSpec((W, H), (0, H), 200, "load_bearing"),
        WallSpec((0, H), (0, 0), 200, "load_bearing"),
        WallSpec((2400, 1800), (W, 1800), 100, "partition"),
        WallSpec((2400, 1800), (2400, H), 100, "partition"),
        WallSpec((2400, 3400), (W, 3400), 100, "partition"),
        WallSpec((3900, 3400), (3900, H), 100, "partition"),
    ]
    ob = [
        OpeningSpec(0, 3000, 1500, "window", side=1),
        OpeningSpec(1, 2600, 1400, "window", side=1),
        OpeningSpec(2, 4800, 1200, "window", side=1),
        OpeningSpec(3, 1000, 1000, "window", side=1),
        OpeningSpec(3, 3200, 900, "door", hinge="right", side=1, entry=True),
        OpeningSpec(4, 1800, 850, "door", hinge="left", side=1),   # 客廳 -> 睡房
        OpeningSpec(5, 2300, 750, "door", hinge="left", side=1),   # 客廳 -> 廚房
        OpeningSpec(6, 2400, 700, "door", hinge="right", side=-1), # 睡房 -> 浴室 (套房)  
    ]
    cb = [("living", "客廳", (0, 0, W, 1800)),
          ("bedroom", "睡房", (2400, 1800, W, 3400)),
          ("kitchen", "廚房", (2400, 3400, 3900, H)),
          ("bath", "浴室", (3900, 3400, W, H))]
    spec_b = FlatSpec("f_hk_b", "plan_hk_b", W, H, wb, ob, cb,
                      beams=[dict(region=[[2400, 1700], [W, 1700], [W, 1900], [2400, 1900]],
                                  z_max=2050, kind="beam")],
                      services=[("kitchen", "floor_drain", (350, -350)),
                                ("bath", "floor_drain", (300, -300))],
                      px_per_mm=0.22, rotation_deg=1.4, noise=0.016,
                      perspective=0.0, seed=23)

    # ---------------- Flat C: 5200 x 8000 竖版 + 透视 + 大旋转 ----------------
    W, H = 5200, 8000
    wc = [
        WallSpec((0, 0), (W, 0), 200, "load_bearing"),
        WallSpec((W, 0), (W, H), 200, "load_bearing"),
        WallSpec((W, H), (0, H), 200, "load_bearing"),
        WallSpec((0, H), (0, 0), 200, "load_bearing"),
        WallSpec((0, 2200), (W, 2200), 100, "partition"),
        WallSpec((3200, 2200), (3200, H), 100, "partition"),
        WallSpec((3200, 4400), (W, 4400), 100, "partition"),
        WallSpec((3200, 6200), (W, 6200), 100, "partition"),
        WallSpec((0, 5000), (3200, 5000), 100, "partition"),
    ]
    oc = [
        OpeningSpec(0, 1600, 1800, "window", side=1),
        OpeningSpec(0, 4600, 900, "door", hinge="right", side=1, entry=True),
        OpeningSpec(1, 3000, 1400, "window", side=1),
        OpeningSpec(2, 4200, 1000, "window", side=1),
        OpeningSpec(3, 1500, 1200, "window", side=1),              # 睡房窗 (西)
        OpeningSpec(4, 1600, 1200, "door", hinge="left", side=1),  # 客廳 -> 飯廳
        OpeningSpec(4, 4300, 800, "door", hinge="right", side=1),  # 客廳 -> 廚房
        OpeningSpec(5, 1200, 800, "door", hinge="left", side=1),   # 飯廳 -> 廚房
        OpeningSpec(6, 1000, 700, "door", hinge="left", side=1),   # 廚房 -> 浴室
        OpeningSpec(7, 1000, 700, "door", hinge="right", side=1),  # 浴室 -> 儲物房
        OpeningSpec(8, 1600, 850, "door", hinge="right", side=1),  # 飯廳 -> 睡房
    ]
    cc = [("living", "客廳", (0, 0, W, 2200)),
          ("dining", "飯廳", (0, 2200, 3200, 5000)),
          ("kitchen", "廚房", (3200, 2200, W, 4400)),
          ("bath", "浴室", (3200, 4400, W, 6200)),
          ("bedroom", "睡房", (0, 5000, 3200, H)),
          ("storage", "儲物房", (3200, 6200, W, H))]
    spec_c = FlatSpec("f_hk_c", "plan_hk_c", W, H, wc, oc, cc,
                      beams=[dict(region=[[0, 2100], [W, 2100], [W, 2300], [0, 2300]],
                                  z_max=2100, kind="beam")],
                      services=[("kitchen", "floor_drain", (350, -350)),
                                ("bath", "floor_drain", (300, -300)),
                                ("bath", "water_heater", (350, 300)),
                                ("kitchen", "gas_inlet", (-350, -350))],
                      px_per_mm=0.2, rotation_deg=-2.2, noise=0.020,
                      perspective=0.006, seed=37)
    return [spec_a, spec_b, spec_c]


def main():
    ap = argparse.ArgumentParser(description="生成香港小户型测试平面图")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--seed", type=int, default=0, help="叠加到各图的随机种子偏移")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for spec in _flats():
        spec.seed += args.seed
        png, truth = render(spec, out_dir)
        im = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        d = json.loads(truth.read_text(encoding="utf-8"))
        print("[ok] %s  %dx%d px  %.2f mm/px  rooms=%d walls=%d openings=%d" % (
            png.name, im.shape[1], im.shape[0], 1.0 / spec.px_per_mm,
            len(d["rooms"]), sum(len(r["walls"]) for r in d["rooms"]),
            sum(len(r["openings"]) for r in d["rooms"])))
        print("     truth -> %s" % truth.name)


if __name__ == "__main__":
    main()
