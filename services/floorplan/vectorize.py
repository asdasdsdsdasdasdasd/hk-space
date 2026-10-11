#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vectorize.py — 户型图图片 -> Room JSON
=====================================
经典 OpenCV 管线, 不训练模型、不下载数据集:

    灰度 -> 自适应二值化 -> 去噪
      -> 霍夫变换估计主方向 -> 旋转矫正 (透视只做说明, 见 README)
      -> 形态学方向开运算提取长直线 -> 连通域 -> 墙体带 (Band)
      -> 合并「细双线」=> 间隔墙; 实心粗黑块 => 承重墙
      -> 正交吸附 (0/90 度) + 同一直线上的墙段切分
      -> 墙线上的「缺口」= 开口; 缺口里的符号线判定门 / 窗
      -> 比例尺恢复: ① 尺寸标注线 + OCR 数字   ② 调用者给的 scale_mm_per_px
      -> 栅格化墙体 + 封住开口 -> 连通域填充 -> 房间多边形 -> 正交吸附
      -> Room JSON (+ confidence)

坐标系约定 (与 schema 一致, 见 README):
    单位 = 整数毫米; +x 向右, +y 向下 (图像坐标系);
    原点 = 所有房间多边形包围盒的最小角 (所以坐标从 0 附近开始)。

用法:
    python3 services/floorplan/vectorize.py --image data/floorplans/plan_hk_a.png \
        --out out/plan_hk_a.json [--scale-mm-per-px 5.0] [--truth <truth.json>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DEFAULT_ASSUME_SCALE_MM_PX = 5.0   # OCR 失败且调用者没给比例尺时的兜底假设
DEFAULT_HEIGHT_MM = 2450           # HK 常见层高 (平面图无法反推, 由调用者覆盖)


# ============================================================================
# 1. 预处理
# ============================================================================
def load_gray(path: Path) -> Tuple[np.ndarray, List[str]]:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise SystemExit("[vectorize] 读不到图片: %s" % path)
    notes = []
    h, w = img.shape
    if min(h, w) < 700:                       # 太小的图先放大, 保证线宽够做形态学
        f = 700.0 / min(h, w)
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        notes.append("输入过小, 已放大 %.2fx 到 %dx%d" % (f, img.shape[1], img.shape[0]))
    return img, notes


def binarize(gray: np.ndarray) -> Tuple[np.ndarray, float]:
    """返回 (bw, ink_ratio), bw 里 255 = 墨线/墙体。

    注意: 单用 adaptiveThreshold 会把「实心黑承重墙」掏空 (实心区内部局部均值也是黑,
    pixel < mean - C 不成立) —— 所以这里先用大尺度高斯估背景做光照归一, 再 Otsu,
    最后与 adaptive 的结果取并集 (Otsu 保住实心块, adaptive 补回细淡的线)。
    """
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    sigma = max(12.0, min(gray.shape) / 28.0)
    bg = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), sigma)
    bg = np.maximum(bg, 1.0)
    norm = np.clip(gray.astype(np.float32) / bg * 220.0, 0, 255).astype(np.uint8)
    _, bw = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ad = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 41, 14)
    bw = cv2.bitwise_or(bw, ad)
    ink = float((bw > 0).mean())
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return bw, ink


def estimate_skew_bands(bw: np.ndarray, min_len: int) -> Tuple[float, int]:
    """用「长墙带的中心线斜率」估计整体倾斜 —— 比霍夫更准 (墙线长且厚)。"""
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len, 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len))
    angs: List[Tuple[float, float]] = []
    for axis, mask in (("h", cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)),
                       ("v", cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk))):
        for b in extract_bands(mask, axis, min_len):
            if b.length < 2.0 * min_len or len(b.prof) < 12:
                continue
            xs = np.arange(b.s, b.s + b.step * len(b.prof), b.step, dtype=float)[:len(b.prof)]
            mids = np.array([p[0] for p in b.prof], dtype=float)
            slope = float(np.polyfit(xs, mids, 1)[0])
            ang = math.degrees(math.atan(slope))
            angs.append((-ang if axis == "h" else ang, float(b.length)))
    if not angs:
        return 0.0, 0
    vals = np.array([a for a, _ in angs])
    med = float(np.median(vals))
    keep = [a for a in vals if abs(a - med) <= 1.0]
    return (float(np.median(keep)) if keep else med), len(angs)


def estimate_skew_deg(bw: np.ndarray) -> Tuple[float, int]:
    edges = cv2.Canny(bw, 50, 150)
    minlen = max(40, int(0.15 * min(bw.shape)))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720.0, threshold=60,
                            minLineLength=minlen, maxLineGap=6)
    if lines is None:
        return 0.0, 0
    devs = []
    # OpenCV 5 的 HoughLinesP 返回 (N,4); 4.x 返回 (N,1,4) —— 两者都兼容
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        ang = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1))) % 90.0
        d = ang if ang <= 45.0 else ang - 90.0
        devs.append(d)
    if not devs:
        return 0.0, 0
    return float(np.median(devs)), len(devs)


def deskew(gray: np.ndarray, bw: np.ndarray, min_len: int, max_deg: float = 8.0,
           iters: int = 3):
    """按主方向旋转矫正 (只做旋转; 真实照片的透视残差见 README)。

    迭代做: 每轮估一次角度, 正反两个方向各试一次, 取「旋转后残差更小」的那个,
    没有改善就停。返回 (矫正后灰度, 矫正后二值, 累计旋转角, 初始估计角)。
    """
    raw, n0 = estimate_skew_bands(bw, min_len)
    total = 0.0
    for _ in range(iters):
        est, n = estimate_skew_bands(bw, min_len)
        if n < 2 or abs(est) < 0.12 or abs(est) > max_deg:
            break
        h, w = gray.shape
        best = None
        for cand in (-est, est):
            M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), cand, 1.0)
            g2 = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
            b2, _ = binarize(g2)
            res, _ = estimate_skew_bands(b2, min_len)
            if best is None or abs(res) < abs(best[0]):
                best = (res, cand, g2, b2)
        if best is None or abs(best[0]) > abs(est) - 0.05:
            break                            # 没有改善, 别再转了
        total += best[1]
        gray, bw = best[2], best[3]
    return gray, bw, total, raw


# ============================================================================
# 2. 墙体带提取
# ============================================================================
@dataclass
class Band:
    axis: str                 # 'h' 水平墙 / 'v' 垂直墙
    lo: int                   # 横向(厚度方向)范围
    hi: int
    s: int                    # 沿墙方向起止
    e: int
    area: int
    center: float             # 厚度方向中心 (px)
    thick: float              # 厚度 (px)
    fill: float               # 填充率 (bbox 内墨迹占比)
    n_members: int = 1
    symbol: bool = False
    step: int = 2
    prof: List[Tuple[float, float]] = field(default_factory=list)   # (厚度方向中心, 墨迹长度)

    @property
    def length(self) -> int:
        return self.e - self.s

    def run_at(self, p: int):
        """取位置 p 处的 (中心, 厚度)。位置不在采样网格上或超出范围时返回 None。"""
        if not self.prof or p < self.s or p > self.e:
            return None
        i = (p - self.s) // self.step
        if i < 0 or i >= len(self.prof):
            return None
        return self.prof[i]


def extract_bands(mask: np.ndarray, axis: str, min_len: int) -> List[Band]:
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    out: List[Band] = []
    for i in range(1, n):
        x, y, w, h, a = (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]),
                         int(stats[i, 3]), int(stats[i, 4]))
        if axis == "h":
            if w < min_len:
                continue
            lo, hi, s, e = y, y + h - 1, x, x + w
            if h > 0.6 * mask.shape[0]:
                continue
        else:
            if h < min_len:
                continue
            lo, hi, s, e = x, x + w - 1, y, y + h
            if w > 0.6 * mask.shape[1]:
                continue
        step = max(1, (e - s) // 220)
        prof = []
        for p in range(s, e, step):
            line = mask[lo:hi + 1, p] if axis == "h" else mask[p, lo:hi + 1]
            idx = np.flatnonzero(line)
            if idx.size == 0:
                prof.append((float(lo + (hi - lo) / 2.0), 0.0))
                continue
            runs = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
            run = max(runs, key=len)
            prof.append((lo + (run[0] + run[-1]) / 2.0, float(len(run))))
        if len(prof) < 3:
            continue
        centers = np.array([c for c, _ in prof])
        ths = np.array([t for _, t in prof])
        out.append(Band(axis, lo, hi, s, e, a, float(np.median(centers)),
                        float(np.median(ths)), a / float(max(1, w * h)),
                        step=step, prof=prof))
    return out


def merge_double_lines(bands: List[Band], thin_px: float, gap_px: float) -> List[Band]:
    """把「细双线」间隔墙的两条平行线合并成一条带 (厚度 = 两者间距 + 线宽)。"""
    parent = list(range(len(bands)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(bands)):
        for j in range(i + 1, len(bands)):
            a, b = bands[i], bands[j]
            if a.axis != b.axis:
                continue
            if a.thick > thin_px or b.thick > thin_px:
                continue
            ov = min(a.e, b.e) - max(a.s, b.s)
            if ov < 0.55 * min(a.length, b.length):
                continue
            if abs(a.center - b.center) > gap_px:
                continue
            ra, rb = find(i), find(j)
            if ra != rb:
                parent[rb] = ra

    groups: Dict[int, List[Band]] = {}
    for i, b in enumerate(bands):
        groups.setdefault(find(i), []).append(b)

    out: List[Band] = []
    for g in groups.values():
        if len(g) == 1:
            out.append(g[0])
            continue
        g = sorted(g, key=lambda b: b.center)
        cross = [b.center for b in g]
        thick = (max(cross) - min(cross)) + float(np.mean([b.thick for b in g]))
        lo, hi = min(b.lo for b in g), max(b.hi for b in g)
        s, e = min(b.s for b in g), max(b.e for b in g)
        area = sum(b.area for b in g)
        step = max(1, (e - s) // 220)
        prof = [(float(np.mean(cross)), float(thick)) for _ in range(s, e, step)]
        out.append(Band(g[0].axis, lo, hi, s, e, area, float(np.mean(cross)), thick,
                        area / float(max(1, (hi - lo + 1) * (e - s))), len(g), False,
                        step, prof))
    return out


def has_t_junction(b: Band, others: List[Band], tol: float = 4.0) -> bool:
    """b 的端点处是否有别的墙接上来 (判定「孤立细线=文字/标注」用)。"""
    for o in others:
        if o is b or o.axis == b.axis:
            continue
        if not (o.s - tol <= b.center <= o.e + tol):
            continue
        for end in (b.s, b.e):
            if abs(o.center - end) <= max(tol, o.thick):
                return True
    return False


def network_mask(bw: np.ndarray, radius: int) -> np.ndarray:
    """把墙体网络整体拿出来 (最大的膨胀连通域), 尺寸标注链会落在它外面。"""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    d = cv2.dilate(bw, k)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(d, 8)
    if n <= 1:
        return np.zeros_like(bw)
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (lab == i).astype(np.uint8)


def _band_in_network(b: Band, net: np.ndarray) -> bool:
    step = max(1, b.length // 12)
    hit = tot = 0
    for p in range(b.s, b.e, step):
        y = int(round(b.center))
        tot += 1
        hit += int(net[y, p] > 0) if b.axis == "h" else int(net[p, y] > 0)
    return tot > 0 and hit / tot >= 0.6


def cluster_lines(bands: List[Band], tol: float) -> List[List[Band]]:
    """按「同方向 + 厚度方向位置相近」聚类成一条条墙线 (水平/垂直分开处理!)。"""
    groups: List[List[Band]] = []
    for axis in ("h", "v"):
        for b in sorted([x for x in bands if x.axis == axis], key=lambda x: x.center):
            if groups and groups[-1][0].axis == axis:
                cur = groups[-1]
                ref = float(np.mean([x.center for x in cur]))
                if abs(b.center - ref) <= tol:
                    cur.append(b)
                    continue
            groups.append([b])
    return groups


def line_segments(group: List[Band], join_px: int = 4):
    segs = sorted([(b.s, b.e) for b in group])
    merged: List[List[int]] = []
    for s, e in segs:
        if merged and s - merged[-1][1] <= join_px:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def find_gaps(merged: Sequence[Sequence[int]], min_gap: float, max_gap: float):
    gaps = []
    for i in range(len(merged) - 1):
        g = merged[i + 1][0] - merged[i][1]
        if min_gap <= g <= max_gap:
            gaps.append((int(merged[i][1]), int(merged[i + 1][0])))
    return gaps


def _covering_band(group: List[Band], p: int) -> Optional[Band]:
    best = None
    for b in group:
        if b.s - 2 <= p <= b.e + 2:
            if best is None or b.thick > best.thick:
                best = b
    return best


def split_segment_into_walls(group: List[Band], s: int, e: int):
    """同一条线上可能一段是承重墙、一段是间隔墙, 按厚度/填充率切开。"""
    pieces = []
    step = 2
    cur = None
    for p in range(s, e + 1, step):
        b = _covering_band(group, p)
        if b is None:
            continue
        if cur is None:
            cur = dict(s=p, e=p, thick=b.thick, fill=b.fill, center=b.center, ids={id(b)})
            continue
        same = abs(b.thick - cur["thick"]) <= max(3.0, 0.35 * min(b.thick, cur["thick"]))
        same = same and (abs(b.center - cur["center"]) <= max(4.0, 0.5 * cur["thick"]))
        if same:
            cur["e"] = p
            cur["thick"] = 0.5 * (cur["thick"] + b.thick)
            cur["fill"] = max(cur["fill"], b.fill)
            cur["center"] = 0.5 * (cur["center"] + b.center)
        else:
            pieces.append(cur)
            cur = dict(s=p, e=p, thick=b.thick, fill=b.fill, center=b.center, ids=set())
    if cur is not None:
        pieces.append(cur)
    return [p for p in pieces if p["e"] - p["s"] >= 6]


# ============================================================================
# 3. 开口 (门/窗)
# ============================================================================
def classify_gap(gap, line_center: float, line_thick: float, bands: List[Band],
                 axis: str):
    """缺口里若有一条平行细线 -> 窗; 有垂直方向的线从门垛长出来 -> 门。"""
    gs, ge = gap
    win = None
    leaf = None
    tol_cross = 1.6 * line_thick
    for b in bands:
        # 注意: 这里不能跳过已标记的 symbol —— 门扇线在第一轮就被标记了,
        # 第二轮再找它做 swing 判断时必须还能找到。
        if b.axis == axis:      # 平行符号线 = 窗的三/双线
            if b.s >= gs - 4 and b.e <= ge + 4 and abs(b.center - line_center) <= tol_cross:
                win = b
        else:                   # 垂直符号线 = 门扇: 细线 + 长度约等于门洞宽 + 一端贴在墙上
            if b.thick > 8.0:
                continue
            if (gs - line_thick) <= b.center <= (ge + line_thick):
                if b.s - 2 * line_thick <= line_center <= b.e + 2 * line_thick:
                    if 0.45 * (ge - gs) <= b.length <= 1.6 * (ge - gs):
                        if leaf is None or b.length > leaf.length:
                            leaf = b
    if win is not None:
        return "window", win, leaf
    if leaf is not None:
        return "door", None, leaf
    return "door", None, None


# ============================================================================
# 4. 比例尺恢复 (尺寸标注链 + OCR)
# ============================================================================
def _find_font(size: int):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/lato/Lato-Medium.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


_TEMPLATES: Optional[Dict[str, np.ndarray]] = None


def _templates() -> Dict[str, np.ndarray]:
    """用系统字体渲染 0-9 当作模板 (经典模板匹配, 不训练模型)。"""
    global _TEMPLATES
    if _TEMPLATES is not None:
        return _TEMPLATES
    from PIL import Image, ImageDraw
    tpl: Dict[str, np.ndarray] = {}
    for dig in "0123456789":
        best = None
        for font_path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                          "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
                          "/usr/share/fonts/truetype/lato/Lato-Medium.ttf",
                          "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
            if not Path(font_path).exists():
                continue
            try:
                from PIL import ImageFont
                f = ImageFont.truetype(font_path, 160)
            except Exception:
                continue
            im = Image.new("L", (220, 240), 255)
            ImageDraw.Draw(im).text((110, 120), dig, font=f, fill=0, anchor="mm")
            a = np.asarray(im)
            ys, xs = np.where(a < 128)
            if len(ys) == 0:
                continue
            crop = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            g = cv2.resize(crop, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
            g = 255.0 - g          # 墨迹 = 高值
            g = (g - g.min()) / max(1e-6, g.max() - g.min())
            if best is None or g.shape == (32, 24):
                best = g
                break
        if best is not None:
            tpl[dig] = best
    _TEMPLATES = tpl
    return tpl


def _score(a: np.ndarray, b: np.ndarray) -> float:
    best = -1.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            bb = np.roll(np.roll(b, dy, 0), dx, 1)
            best = max(best, float(1.0 - np.abs(a - bb).mean()))
    return best


def ocr_dimension_tokens(bw: np.ndarray) -> Tuple[List[dict], str]:
    """读图上的尺寸数字。优先 pytesseract, 没有就退回内置模板匹配。"""
    engine = "none"
    tokens: List[dict] = []
    try:
        import pytesseract  # noqa
        from PIL import Image
        data = pytesseract.image_to_data(Image.fromarray(255 - bw), config="--psm 11",
                                         output_type=pytesseract.Output.DICT)
        for i, txt in enumerate(data["text"]):
            t = "".join(ch for ch in str(txt) if ch.isdigit())
            if len(t) < 2:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            tokens.append(dict(text=t, value=int(t),
                               center=(x + w / 2.0, y + h / 2.0), h=max(h, 1)))
        engine = "pytesseract"
        if tokens:
            return tokens, engine
    except Exception:
        engine = "none"

    # ---- 内置模板匹配 OCR (pytesseract / tesseract 都没装时的兜底) ----
    tpl = _templates()
    if not tpl:
        return [], engine
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    cands = []
    for i in range(1, n):
        x, y, w, h, a = (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]),
                         int(stats[i, 3]), int(stats[i, 4]))
        if not (9 <= h <= 80 and 2 <= w <= 80):
            continue
        if a < 0.10 * w * h or a > 0.96 * w * h:
            continue
        if h / float(w) > 4.0:
            continue
        crop = (lab[y:y + h, x:x + w] == i).astype(np.uint8) * 255
        g = cv2.resize(crop, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        g = (g - g.min()) / max(1e-6, g.max() - g.min())
        best_d, best_s = None, -1.0
        for d, t in tpl.items():
            s = _score(g, t)
            if s > best_s:
                best_d, best_s = d, s
        if best_s >= 0.70:
            cands.append(dict(digit=best_d, score=best_s, x=x, y=y, w=w, h=h))
    if not cands:
        return [], "template"
    cands.sort(key=lambda c: c["x"])
    # 按行 + 字距聚成数字串
    used = [False] * len(cands)
    for i in range(len(cands)):
        if used[i]:
            continue
        grp = [cands[i]]
        used[i] = True
        for j in range(i + 1, len(cands)):
            if used[j]:
                continue
            a, b = grp[-1], cands[j]
            if abs((b["y"] + b["h"] / 2) - (a["y"] + a["h"] / 2)) > 0.45 * a["h"]:
                continue
            if not (0.6 <= b["h"] / float(a["h"]) <= 1.7):
                continue
            gap = b["x"] - (a["x"] + a["w"])
            if gap < -0.3 * a["w"] or gap > 0.95 * a["h"]:
                continue
            grp.append(b)
            used[j] = True
        if len(grp) < 2:
            continue
        grp.sort(key=lambda c: c["x"])
        txt = "".join(c["digit"] for c in grp)
        x0 = min(c["x"] for c in grp)
        x1 = max(c["x"] + c["w"] for c in grp)
        y0 = min(c["y"] for c in grp)
        y1 = max(c["y"] + c["h"] for c in grp)
        tokens.append(dict(text=txt, value=int(txt),
                           center=((x0 + x1) / 2.0, (y0 + y1) / 2.0),
                           h=(y1 - y0), score=float(np.mean([c["score"] for c in grp]))))
    return tokens, "template"




# ============================================================================
# 4c. 房间标签识别（同样是模板匹配, 不训练模型）
# ============================================================================
# ★ 为什么要做这一步：vectorize 原来**只按面积排名**猜标签
#   （最大=客廳, 第二大=睡房, 第三=廚房, 第四=浴室）。
#   plan_hk_a / plan_hk_c 碰巧都对, 但 plan_hk_b 里**浴室(2.41m²)比廚房(1.72m²)大**,
#   于是厨房和浴室被整间对调 —— 灶台摆进了浴室, 马桶摆进了厨房,
#   而且图看起来完全正常。户型图上是**写着房名的**, 应该去读它。
ROOM_LABEL_VOCAB = ["客廳", "睡房", "廚房", "浴室", "儲物房", "飯廳", "房間"]

_LABEL_TPL: Optional[Dict[str, np.ndarray]] = None
LABEL_TPL_SIZE = (64, 32)          # (宽, 高)


def _label_templates() -> Dict[str, np.ndarray]:
    """用系统 CJK 字体渲染候选房名当模板。"""
    global _LABEL_TPL
    if _LABEL_TPL is not None:
        return _LABEL_TPL
    from PIL import Image, ImageDraw, ImageFont
    font_path = None
    for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
              "/usr/share/fonts/truetype/arphic/uming.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"):
        if Path(p).exists():
            font_path = p
            break
    if font_path is None:
        _LABEL_TPL = {}
        return _LABEL_TPL
    try:
        f = ImageFont.truetype(font_path, 120)
    except Exception:
        _LABEL_TPL = {}
        return _LABEL_TPL
    tpl: Dict[str, np.ndarray] = {}
    for lab in ROOM_LABEL_VOCAB:
        im = Image.new("L", (140 * len(lab) + 80, 200), 255)
        ImageDraw.Draw(im).text((40, 100), lab, font=f, fill=0, anchor="lm")
        a = np.asarray(im)
        ys, xs = np.where(a < 128)
        if len(ys) == 0:
            continue
        crop = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        g = cv2.resize(crop, LABEL_TPL_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
        g = 255.0 - g
        g = (g - g.min()) / max(1e-6, g.max() - g.min())
        tpl[lab] = g
    _LABEL_TPL = tpl
    return tpl


def _score_wide(a: np.ndarray, b: np.ndarray, r: int = 2) -> float:
    """和 _score 一样, 但允许更多像素的平移 (房名比数字大, 对齐误差也大)。"""
    best = -1.0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            bb = np.roll(np.roll(b, dy, 0), dx, 1)
            best = max(best, float(1.0 - np.abs(a - bb).mean()))
    return best


def _read_label_from(mask: np.ndarray, inner: np.ndarray, tpl: Dict[str, np.ndarray],
                     min_score: float) -> Tuple[str, float]:
    """从一个二值图里读房名（已假定笔画是粗的）。"""
    h, w = mask.shape
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    comps = []
    for i in range(1, n):
        x, y, ww, hh, a = (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]),
                           int(stats[i, 3]), int(stats[i, 4]))
        if a < 20 or hh < 8 or ww < 4:
            continue
        if ww / max(1, hh) > 6:                 # 又长又扁 = 墙线 / 尺寸线
            continue
        if ww > 0.6 * w or hh > 0.6 * h:
            continue
        if inner[y:y + hh, x:x + ww].min() == 0:    # 必须整块在房间内部
            if os.environ.get("HKSPACE_DEBUG_LABELS") and \
                    inner[y:y + hh, x:x + ww].max() > 0:
                print("      drop(fractional): (%d,%d,%d,%d,a=%d)" % (x, y, ww, hh, a))
            continue
        comps.append((i, x, y, ww, hh))
    if os.environ.get("HKSPACE_DEBUG_LABELS"):
        print("      comps kept: %d  %s" % (len(comps), comps[:8]))
    if not comps:
        return "", 0.0
    # 同一个词的几个字 y 中心相近、字高相近、水平间距小 (间距按**字高**算,
    # 不能按全图组件中位高 —— 尺寸数字只有房名的 1/2.3 高, 会把容差压得太小)
    comps.sort(key=lambda c: c[1])
    groups: List[List[tuple]] = []
    for c in comps:
        yc, hh = c[2] + c[4] / 2.0, c[4]
        put = False
        for g in groups:
            gh = float(np.mean([q[4] for q in g]))
            gy = float(np.mean([q[2] + q[4] / 2.0 for q in g]))
            gx1 = max(q[1] + q[3] for q in g)
            if abs(hh - gh) > 0.35 * gh:
                continue
            if abs(yc - gy) > 0.30 * max(gh, hh):
                continue
            if (c[1] - gx1) >= 0.55 * max(gh, hh):
                continue
            g.append(c)
            put = True
            break
        if not put:
            groups.append([c])
    if os.environ.get("HKSPACE_DEBUG_LABELS"):
        print("      groups(all): %s" % [len(g) for g in groups])
    groups = [g for g in groups if len(g) >= 2]
    if not groups:
        return "", 0.0
    grp = max(groups, key=lambda g: sum(q[3] * q[4] for q in g))
    ids = [q[0] for q in grp]
    x0 = min(q[1] for q in grp)
    y0 = min(q[2] for q in grp)
    x1 = max(q[1] + q[3] for q in grp)
    y1 = max(q[2] + q[4] for q in grp)
    crop = np.isin(lab[y0:y1 + 1, x0:x1 + 1], ids).astype(np.uint8) * 255
    if crop.size == 0:
        return "", 0.0
    g = cv2.resize(crop, LABEL_TPL_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    g = (g - g.min()) / max(1e-6, g.max() - g.min())
    scored = sorted(((_score_wide(g, t), lab_) for lab_, t in tpl.items()), reverse=True)
    if not scored:
        return "", 0.0
    sc, lab_ = scored[0]
    return (lab_, float(sc)) if sc >= min_score else ("", float(sc))


def read_room_label(bw: np.ndarray, poly_px, tpl: Dict[str, np.ndarray],
                    min_score: float = 0.58) -> Tuple[str, float]:
    """在一间房的轮廓里找出房名文字, 和候选标签做模板匹配。

    返回 (标签, 相似度)。读不到就返回空字符串 —— **宁可退回按面积猜,
    也不要贴一个错标签上去**。

    ★ 两个坑:
      ① 极性: bw 里 255 = 墨, 模板也要转成「墨 = 高值」, 两边同号才比得上。
         第一版多做了一次 255-x, 把图里的字反过来比, 所有房名都失败(0.45-0.55 全是噪声)。
      ② 细线会串字: 门弧横穿房名时（plan_hk_b 的「廚房」就是这样）,
         字被并进一个大组件, 然后因为不完整落在房内被丢掉。
         所以**两种输入都试**: 原图 + 5x5 开运算（去掉 ≈4px 的墙线/门弧, 保住 ≈8px 的笔画）,
         取分高的那个。只试一种的话, 总有一半房间读唔到。
    """
    if not tpl or len(poly_px) < 3:
        return "", 0.0
    h, w = bw.shape
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [np.asarray(poly_px, np.int32)], 255)
    inner = cv2.erode(m, np.ones((15, 15), np.uint8))
    opened = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    best_lab, best_sc = "", 0.0
    for mask in (bw, opened):
        lab_, sc = _read_label_from(mask, inner, tpl, min_score)
        if sc > best_sc:
            best_lab, best_sc = lab_, sc
    return best_lab, best_sc


def _consensus(cands: List[tuple], tol: float = 0.06):
    """把候选比例尺聚类, 取「权重和最大」的那一簇 (比直接取中位数抗误读)。"""
    if not cands:
        return None, []
    cs = sorted(cands, key=lambda c: c[0])
    clusters: List[List[tuple]] = [[cs[0]]]
    for c in cs[1:]:
        if c[0] <= clusters[-1][-1][0] * (1.0 + tol):
            clusters[-1].append(c)
        else:
            clusters.append([c])
    def weight(cl):
        return sum((c[4] if len(c) > 4 else 1) for c in cl)
    best = max(clusters, key=lambda cl: (weight(cl), len(cl)))
    med = float(np.median([c[0] for c in best]))
    return med, best


def scale_from_wall_thickness(walls_px: List[dict], assumed_load_mm: float = 200.0):
    """兜底比例尺: 香港承重墙基本都在 200mm 左右, 拿「粗墙的像素厚度」反推。

    比固定 assume 强得多 —— 至少在图上真的有承重墙的时候。
    """
    ths = sorted(float(w["thick"]) for w in walls_px)
    if len(ths) < 2:
        return None
    p = ths[int(round(0.75 * (len(ths) - 1)))]     # 上四分位 = 承重墙的厚度
    if p < 6.0:
        return None
    return assumed_load_mm / p


def scale_from_walls_and_tokens(walls_px: List[dict], dim_bands: List[Band],
                                tokens: List[dict], max_dist_px: float = 60.0):
    """更稳的比例尺: 标注数字量的是「它两侧最近两道墙之间的距离」。

    比「用尺寸线长度」稳, 因为相邻的标注链线段常常连成一条长线 (那就没法按段取长度了),
    但墙的位置是独立检出来的, 直接夹逼即可。
    """
    vx = sorted({float(w["center"]) for w in walls_px if w["axis"] == "v"})
    hy = sorted({float(w["center"]) for w in walls_px if w["axis"] == "h"})
    cands = []
    for t in tokens:
        cx, cy = t["center"]
        axis = None
        bestd = 1e9
        for b in dim_bands:
            d = abs(cy - b.center) if b.axis == "h" else abs(cx - b.center)
            if d < bestd:
                bestd, axis = d, b.axis
        if axis is None or bestd > max_dist_px:
            continue
        pos = cx if axis == "h" else cy
        arr = vx if axis == "h" else hy
        left = [p for p in arr if p <= pos]
        right = [p for p in arr if p >= pos]
        if not left or not right or right[0] - left[-1] < 25:
            continue
        span = right[0] - left[-1]
        mm_px = t["value"] / span
        if 0.3 <= mm_px <= 120.0:
            # 权重 = 数字位数 (4 位数比 2 位数可信得多, 位数少的多半是 OCR 漏读)
            cands.append((mm_px, t["value"], span, t["text"], len(str(int(t["value"])))))
    return _consensus(cands)


def scale_from_dimension_lines(dim_bands: List[Band], tokens: List[dict],
                               max_dist_px: float = 60.0):
    """比例尺 = 数字(mm) / 该数字所在尺寸线长度(px)。取中位数。"""
    cands = []
    for b in dim_bands:
        if b.length < 40:
            continue
        mid = (b.s + b.e) / 2.0
        for t in tokens:
            cx, cy = t["center"]
            if b.axis == "h":
                d_perp = abs(cy - b.center)
                inside = b.s - 6 <= cx <= b.e + 6
                centred = abs(cx - mid) <= 0.35 * b.length + 60
            else:
                d_perp = abs(cx - b.center)
                inside = b.s - 6 <= cy <= b.e + 6
                centred = abs(cy - mid) <= 0.35 * b.length + 60
            if d_perp <= max_dist_px and inside and centred:
                mm_px = t["value"] / float(b.length)
                if 0.05 <= mm_px <= 200.0:
                    cands.append((mm_px, t["value"], b.length, t["text"],
                                  len(str(int(t["value"])))))
    return _consensus(cands)


# ============================================================================
# 5. 主流程
# ============================================================================
@dataclass
class Params:
    min_wall_mm: float = 400.0
    min_opening_mm: float = 400.0
    max_opening_mm: float = 3200.0
    assume_scale_mm_px: float = DEFAULT_ASSUME_SCALE_MM_PX
    scale_mm_px: Optional[float] = None
    use_ocr: bool = True


def segment_profile(group: List[Band], s: int, e: int, step: int = 2):
    """沿墙线采样每个位置的墨迹厚度 -> (位置数组, 厚度数组, 厚度方向中心数组)。

    这是区分「实心承重墙 / 细双线间隔墙 / 开口」的关键: 实心段厚度 ~200mm,
    间隔墙段 ~100mm (双线合并后), 窗的位置只剩窗符号那几根细线 (~20mm),
    门的位置完全没有墨迹 (0)。
    """
    xs = list(range(s, e + 1, step))
    runs, mids = [], []
    for p in xs:
        best_run, best_mid = 0.0, 0.0
        for b in group:
            r = b.run_at(p)
            if r is None:
                continue
            if r[1] > best_run:
                best_run, best_mid = r[1], r[0]
        runs.append(best_run)
        mids.append(best_mid)
    return xs, np.array(runs, float), np.array(mids, float)


def profile_runs(xs, runs, T, thin_floor):
    """把剖面切成 wall / thin / empty 三类连续段。"""
    labels = []
    for r in runs:
        if r <= 0.5:
            labels.append("empty")
        elif r < max(0.5 * T, thin_floor):
            labels.append("thin")
        else:
            labels.append("wall")
    out = []
    i = 0
    while i < len(labels):
        j = i
        while j + 1 < len(labels) and labels[j + 1] == labels[i]:
            j += 1
        out.append((xs[i], xs[j], labels[i]))
        i = j + 1
    return out


def line_span(g: List[Band], pool: List[Band], max_extend: float):
    """墙线的扫描范围; 两端如果离垂直墙很近就延伸过去。

    没有这一步, 「墙线末端与垂直墙之间的门洞」永远发现不了 (剖面扫描不到那里),
    房间就会连成一片。
    """
    axis = g[0].axis
    cross = float(np.mean([b.center for b in g]))
    s0, e0 = min(b.s for b in g), max(b.e for b in g)
    # 只认「够粗的垂直墙」: 尺寸标注线 / 门扇线都是细线, 不能拿它们当墙来延伸
    min_perp = max(6.0, 50.0 / 1.0) if False else 6.0
    perp = [b for b in pool if b.axis != axis and b.thick >= min_perp
            and b.s - 3 <= cross <= b.e + 3]
    lefts = [b.center for b in perp if b.center < s0 and s0 - b.center <= max_extend]
    rights = [b.center for b in perp if b.center > e0 and b.center - e0 <= max_extend]
    if lefts:
        s0 = int(min(lefts))
    if rights:
        e0 = int(max(rights))
    return s0, e0


def detect_walls(bands: List[Band], scale: float, params: Params):
    """返回 (walls_px, openings_px)。band 的 symbol 标记会被更新。"""
    thin_px = max(3.0, 40.0 / scale)         # 线宽 <= 40mm 视为「细线」
    gap_px = max(5.0, 220.0 / scale)         # 双线间距 <= 220mm 视为同一道间隔墙
    bands = merge_double_lines(bands, thin_px, gap_px)
    if not bands:
        return [], []

    # 丢掉「孤立的细短线」(尺寸文字、箭头等被方向开运算偶然保留下来的碎块):
    # 只有「成对的细线(间隔墙)」/「够粗的线」/「够长的线」/「端点接得上别的墙」才留。
    long_thin = 1500.0 / scale
    keep = []
    for b in bands:
        if (b.n_members >= 2 or b.thick >= max(10.0, 60.0 / scale)
                or b.length >= long_thin or has_t_junction(b, bands)):
            keep.append(b)
    bands = keep

    min_gap = params.min_opening_mm / scale
    max_gap = params.max_opening_mm / scale
    min_wall = params.min_wall_mm / scale
    max_extend = 1800.0 / scale        # 墙线末端延伸到垂直墙的最大距离
    line_tol = max(5.0, 0.75 * float(np.median([b.thick for b in bands])))

    for _ in range(2):                       # 第一轮找门扇/窗符号并剔除, 第二轮用干净集合重建
        groups = cluster_lines([b for b in bands if not b.symbol], line_tol)
        changed = False
        for g in groups:
            thick = float(np.median([b.thick for b in g]))
            center = float(np.mean([b.center for b in g]))
            # 先确认「这条线上确实有墙」 —— 否则它只是门扇线/尺寸线/文字, 不能当墙线用,
            # 更不能拿它的整段剖面去反标记别的墙。
            s0, e0 = line_span(g, bands, max_extend)
            xs, runs, mids = segment_profile(g, s0, e0)
            if len(xs) < 4:
                continue
            T0 = float(np.median(runs[runs > 0])) if np.any(runs > 0) else thick
            pieces0 = profile_runs(xs, runs, T0, thin_px)
            if not any(lab == "wall" and (re - rs) >= min_wall for rs, re, lab in pieces0):
                continue
            for rs, re, lab in pieces0:
                if lab == "wall" or not (min_gap <= (re - rs) <= max_gap):
                    continue
                _k, win, leaf = classify_gap((rs, re), center, thick, bands, g[0].axis)
                for extra in (win, leaf):
                    if extra is not None and not extra.symbol:
                        extra.symbol = True
                        changed = True
        if not changed:
            break

    walls: List[dict] = []
    openings: List[dict] = []
    clean = [b for b in bands if not b.symbol]
    groups = cluster_lines(clean, line_tol)
    for g in groups:
        axis = g[0].axis
        thick0 = float(np.median([b.thick for b in g]))
        center0 = float(np.mean([b.center for b in g]))
        # 整条墙线拉通扫描: 没有 band 覆盖的位置 run=0, 于是门洞自然变成 empty,
        # 窗位置因为还有窗符号细线而变成 thin。
        s_all, e_all = line_span(g, clean, max_extend)
        xs, runs, mids = segment_profile(g, s_all, e_all)
        if len(xs) < 4:
            continue
        T = float(np.median(runs[runs > 0])) if np.any(runs > 0) else thick0
        pieces = profile_runs(xs, runs, T, thin_px)
        if not any(lab == "wall" and (re - rs) >= min_wall for rs, re, lab in pieces):
            continue                # 整条线上没有一段是墙 -> 它不是墙线 (尺寸线/文字/门扇)
        pieces_here = []
        for rs, re, lab in pieces:
            ln = re - rs
            i0 = max(0, (rs - xs[0]) // 2)
            i1 = min(len(runs), (re - xs[0]) // 2 + 1)
            if i1 <= i0:
                continue
            seg_runs = runs[i0:i1]
            seg_mids = mids[i0:i1]
            local_t = float(np.median(seg_runs[seg_runs > 0])) if np.any(seg_runs > 0) else thick0
            local_c = float(np.median(seg_mids[seg_mids > 0])) if np.any(seg_mids > 0) else center0
            if lab == "wall":
                if ln < min_wall:
                    continue
                pieces_here.append(dict(s=int(rs), e=int(re), thick=local_t, center=local_c,
                                        fill=min(1.0, local_t / max(1e-6, T)), ln=int(ln)))
            elif min_gap <= ln <= max_gap:
                leaf = None
                if lab == "thin":
                    kind = "window"      # 缺口里还有细线 -> 窗符号
                else:
                    _k, _w, leaf = classify_gap((rs, re), center0, thick0, bands, axis)
                    kind = "door"
                openings.append(dict(axis=axis, center=local_c, thick=max(local_t, thin_px * 2),
                                     lo=int(rs), hi=int(re), kind=kind,
                                     leaf=leaf, win=None, hinge=None, swing=None))
        # 被门/窗洞隔开的同线墙段其实是「同一道墙」: 合并成一条墙线, 洞做为 wall 上的 opening。
        # (schema 里 opening 就是挂在墙上的洞, 不是把墙切成两截)
        merged: List[dict] = []
        for p in pieces_here:
            if (merged and p["s"] - merged[-1]["e"] <= max_gap + 8
                    and abs(p["thick"] - merged[-1]["thick"])
                    <= max(0.5 * min(p["thick"], merged[-1]["thick"]), 10.0)):
                last = merged[-1]
                if p["ln"] > last["ln"]:
                    last["thick"], last["center"], last["ln"] = p["thick"], p["center"], p["ln"]
                last["e"] = p["e"]
                last["fill"] = max(last["fill"], p["fill"])
            else:
                merged.append(dict(p))
        for m in merged:
            walls.append(dict(axis=axis, center=m["center"], s=m["s"], e=m["e"],
                              thick=m["thick"], fill=m["fill"]))
    openings.sort(key=lambda o: (o["axis"], o["lo"]))
    return walls, openings

def wall_type(thick_px: float, fill: float, scale: float):
    """承重墙 = 填充率高的粗黑块; 其余 = 间隔墙。"""
    t_mm = thick_px * scale
    if fill >= 0.75 and t_mm >= 150.0:
        return "load_bearing", int(round(t_mm / 10.0) * 10)
    if t_mm < 40:
        t_mm = 100.0                       # 单线画的间隔墙, 按 HK 常规补 100mm
    return "partition", int(round(t_mm / 10.0) * 10)


def walls_to_mm(walls, openings, scale, origin_px):
    ox, oy = origin_px
    out = []
    for i, w in enumerate(walls):
        typ, t_mm = wall_type(w["thick"], w["fill"], scale)
        if w["axis"] == "h":
            a = [int(round((w["s"] - ox) * scale)), int(round((w["center"] - oy) * scale))]
            b = [int(round((w["e"] - ox) * scale)), int(round((w["center"] - oy) * scale))]
        else:
            a = [int(round((w["center"] - ox) * scale)), int(round((w["s"] - oy) * scale))]
            b = [int(round((w["center"] - ox) * scale)), int(round((w["e"] - oy) * scale))]
        out.append(dict(id="w%d" % i, a=a, b=b, type=typ, thickness_mm=int(t_mm),
                        axis=w["axis"], _px=w))
    return out


def openings_to_mm(openings, walls_mm, scale, origin_px):
    ox, oy = origin_px
    out = []
    for i, op in enumerate(openings):
        if op["axis"] == "h":
            cx = (op["lo"] + op["hi"]) / 2.0
            cy = op["center"]
        else:
            cx = op["center"]
            cy = (op["lo"] + op["hi"]) / 2.0
        w_mm = int(round((op["hi"] - op["lo"]) * scale))
        center = [int(round((cx - ox) * scale)), int(round((cy - oy) * scale))]
        # 找到这条开口所在的墙: 同轴、同一条线, 取沿墙方向离开口最近的那道
        pos = cx if op["axis"] == "h" else cy
        host, best = None, 1e18
        for w in walls_mm:
            if w["axis"] != op["axis"]:
                continue
            if abs(w["_px"]["center"] - op["center"]) > max(8.0, 0.9 * op["thick"]):
                continue
            d = 0.0 if w["_px"]["s"] - 4 <= pos <= w["_px"]["e"] + 4 else \
                min(abs(pos - w["_px"]["s"]), abs(pos - w["_px"]["e"]))
            if d < best:
                best, host = d, w
        hinge, swing = None, None
        leaf = op.get("leaf")
        if op["kind"] == "door" and leaf is not None:
            hinge = "left" if abs(leaf.center - op["lo"]) < abs(leaf.center - op["hi"]) else "right"
            mid = (leaf.s + leaf.e) / 2.0
            swing = "out" if mid > op["center"] else "in"
        # center_mm = 从墙的 a 端点沿墙量到开口中心的距离 (schema 要求)
        c_mm = 0
        if host is not None:
            c_mm = int(round(abs(center[0] - host["a"][0]))) if host["axis"] == "h" \
                else int(round(abs(center[1] - host["a"][1])))
        out.append(dict(axis=op["axis"], kind=op["kind"], center=center, width_mm=w_mm,
                        wall=host["id"] if host else None,
                        center_mm=c_mm, hinge=hinge, swing=swing,
                        thickness_mm=(host["thickness_mm"] if host else 100)))
    return out


def rasterize(walls_px, opens_px, shape, scale, origin_px):
    """把识别出的墙画成掩膜, 并封住开口 —— 之后做连通域填充就能得到房间。"""
    m = np.zeros(shape, np.uint8)
    for p in walls_px:
        t = max(2.0, p["thick"] / 2.0)
        if p["axis"] == "h":
            cv2.rectangle(m, (int(p["s"]), int(p["center"] - t)),
                          (int(p["e"]), int(p["center"] + t)), 255, -1)
        else:
            cv2.rectangle(m, (int(p["center"] - t), int(p["s"])),
                          (int(p["center"] + t), int(p["e"])), 255, -1)
    # 封住開口，房間才會分開。
    # ★ 封條一定要用**宿主牆**嘅中心線同厚度，唔可以用開口自己嘅值 ——
    #   踩過的坑：門口開口嘅 center 係由門弧推返嚟嘅，同牆嘅中心線
    #   差咗 13px（≈60mm）。封條照開口中心畫，就留低一條 10px 高嘅罅
    #   喺牆嘅一邊 —— 客廳（17m²，最大嘅房）就由嗰條罅漏出圖外，
    #   被當成「貼住圖邊嘅背景」丟掉，輸出少一間房。
    wall_by_id = {w["id"]: w for w in walls_px if w.get("id") is not None}
    for op in opens_px:
        host = wall_by_id.get(op.get("wall"))
        wc = host["center"] if host is not None else op["center"]
        wt = host["thick"] if host is not None else op["thick"]
        p_t = max(3.0, max(wt, op["thick"]) / 2.0 + 6.0)
        lo, hi = int(op["lo"]) - 6, int(op["hi"]) + 6
        if op["axis"] == "h":
            cv2.rectangle(m, (lo, int(wc - p_t)), (hi, int(wc + p_t)), 255, -1)
        else:
            cv2.rectangle(m, (int(wc - p_t), lo), (int(wc + p_t), hi), 255, -1)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return m


def extract_rooms(mask, scale, min_area_mm2=1.2e6):
    free = (mask == 0).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(free, 4)
    H, W = mask.shape
    border = set(int(v) for v in np.unique(np.concatenate(
        [lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]])))
    min_px = min_area_mm2 / (scale * scale)
    rooms = []
    for i in range(1, n):
        if i in border:
            continue
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < min_px or a > 0.95 * H * W:
            continue
        comp = (lab == i).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if isinstance(cnts, tuple):
            cnts = list(cnts)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, max(1.5, 0.004 * math.sqrt(a)), True).reshape(-1, 2)
        rooms.append(dict(px=[(float(p[0]), float(p[1])) for p in ap], area_px=a,
                          centroid=(float(cent[i][0]), float(cent[i][1]))))
    rooms.sort(key=lambda r: -r["area_px"])
    return rooms


def snap_ortho(poly_px, gxs, gys, tol_px):
    """把房间多边形顶点吸附到墙表面网格上, 保证边严格水平/垂直。"""
    out = []
    for (x, y) in poly_px:
        nx, ny = x, y
        if gxs.size:
            k = int(np.argmin(np.abs(gxs - x)))
            if abs(gxs[k] - x) <= tol_px:
                nx = float(gxs[k])
        if gys.size:
            k = int(np.argmin(np.abs(gys - y)))
            if abs(gys[k] - y) <= tol_px:
                ny = float(gys[k])
        out.append((nx, ny))
    clean = []
    for p in out:
        if not clean or abs(p[0] - clean[-1][0]) > 0.5 or abs(p[1] - clean[-1][1]) > 0.5:
            clean.append(p)
    if len(clean) > 2 and abs(clean[0][0] - clean[-1][0]) < 0.5 and abs(clean[0][1] - clean[-1][1]) < 0.5:
        clean.pop()
    # 去掉共线中间点
    res = []
    m = len(clean)
    for i in range(m):
        a, b, c = clean[(i - 1) % m], clean[i], clean[(i + 1) % m]
        if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < 1e-6:
            continue
        res.append(b)
    return res if len(res) >= 4 else clean


def _seg_dist(p1, p2, p3, p4):
    def pt_seg(p, a, b):
        ax, ay = a
        bx, by = b
        abx, aby = bx - ax, by - ay
        den = abx * abx + aby * aby
        t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - ax) * abx + (p[1] - ay) * aby) / den))
        return math.hypot(p[0] - (ax + t * abx), p[1] - (ay + t * aby))
    return min(pt_seg(p1, p3, p4), pt_seg(p2, p3, p4), pt_seg(p3, p1, p2), pt_seg(p4, p1, p2))


def pt_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
                inside = not inside
    return inside


LABEL_BY_RANK = ["客廳", "睡房", "廚房", "浴室", "儲物房", "飯廳", "房間"]
ID_BY_RANK = ["living", "bedroom", "kitchen", "bath", "storage", "dining", "room"]


def build_room_json(walls_mm, openings_mm, rooms_px, scale, origin_px, params,
                    meta_extra: dict, labels_px: Optional[List[tuple]] = None):
    ox, oy = origin_px
    gxs = sorted({round(((w["_px"]["center"] - ox) * scale + s * w["thickness_mm"] / 2.0) / 10.0) * 10
                  for w in walls_mm if w["axis"] == "v" for s in (-1, 1)})
    gys = sorted({round(((w["_px"]["center"] - oy) * scale + s * w["thickness_mm"] / 2.0) / 10.0) * 10
                  for w in walls_mm if w["axis"] == "h" for s in (-1, 1)})
    gxs_np = np.array(gxs, dtype=float)
    gys_np = np.array(gys, dtype=float)
    tol = max(120.0, 60.0 * scale)

    rooms = []
    for ri, r in enumerate(rooms_px):
        poly_mm = [((x - ox) * scale, (y - oy) * scale) for (x, y) in r["px"]]
        snapped = snap_ortho(poly_mm, gxs_np, gys_np, tol)
        poly = [[int(round(x)), int(round(y))] for (x, y) in snapped]
        area = 0.0
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            area += x1 * y2 - x2 * y1
        lab, sc = (labels_px[ri] if labels_px and ri < len(labels_px) else ("", 0.0))
        rooms.append(dict(polygon=poly, area_mm2=int(abs(area) / 2.0),
                          _ocr_label=lab, _ocr_score=round(float(sc), 3)))
    rooms.sort(key=lambda r: -r["area_mm2"])
    label_to_id = dict(zip(LABEL_BY_RANK, ID_BY_RANK))
    # ① 圖上寫住房名就信圖
    n_ocr = 0
    for r in rooms:
        ocr = r.pop("_ocr_label", "")
        r["label"] = ocr
        r["label_source"] = "image" if ocr else ""
        r["label_score"] = r.pop("_ocr_score", 0.0)
        if ocr:
            n_ocr += 1
    # ② 讀唔到嘅，由**剩下未用過嘅房名**裡按面積排名補。
    #    唔可以獨立按排名猜：兩間房可能拿到同一個名（實測 plan_hk_c
    #    出現過兩間「浴室」、冇「廚房」）。房名係互斥的。
    pool = [l for l in LABEL_BY_RANK if l not in {r["label"] for r in rooms}]
    for i, r in enumerate(rooms):
        if r["label_source"] == "image":
            continue
        want = LABEL_BY_RANK[i] if i < len(LABEL_BY_RANK) else "房間"
        pick = want if want in pool else (pool[0] if pool else "房間")
        if pick in pool:
            pool.remove(pick)
        r["label"] = pick
        r["label_source"] = "area-rank"
    for i, r in enumerate(rooms):
        r["id"] = label_to_id.get(r["label"], ID_BY_RANK[i] if i < len(ID_BY_RANK) else "room%d" % i)
        r["height_mm"] = DEFAULT_HEIGHT_MM
        r["height_field"] = []
        r["walls"] = []
        r["openings"] = []

    # 墙 -> 房间
    for room in rooms:
        poly = [tuple(p) for p in room["polygon"]]
        for w in walls_mm:
            t = w["thickness_mm"]
            d = min(_seg_dist(tuple(w["a"]), tuple(w["b"]), poly[i], poly[(i + 1) % len(poly)])
                    for i in range(len(poly)))
            mid = ((w["a"][0] + w["b"][0]) / 2.0, (w["a"][1] + w["b"][1]) / 2.0)
            if d <= t / 2.0 + max(150.0, 40 * scale) and not pt_in_poly(mid, poly):
                room["walls"].append(dict(id="w%d" % len(room["walls"]), a=list(w["a"]),
                                          b=list(w["b"]), type=w["type"],
                                          thickness_mm=int(t)))
                room.setdefault("_wall_src", []).append(w["id"])

    # 开口 -> 房间 / 连接
    connections = []
    entry = None
    for op in openings_mm:
        axis = op["axis"]
        host0 = walls_mm_by_id(walls_mm, op.get("wall"))
        if host0 is None:
            continue
        # 用「墙的中心线 + center_mm」算出开口精确位置和法线, 再往两侧各探一步找房间
        A = np.array(host0["a"], float)
        B = np.array(host0["b"], float)
        u = B - A
        nrm = float(np.hypot(u[0], u[1]))
        if nrm < 1:
            continue
        u = u / nrm
        c = A + u * float(op["center_mm"])
        nv = np.array([u[1], -u[0]])
        t = host0["thickness_mm"] / 2.0 + 150.0
        cx, cy = float(c[0]), float(c[1])
        sides = []
        for s in (-1, 1):
            pt = (cx + nv[0] * t * s, cy + nv[1] * t * s)
            for room in rooms:
                poly = [tuple(p) for p in room["polygon"]]
                if pt_in_poly(pt, poly):
                    sides.append((s, room))
                    break
        c_mm = int(op.get("center_mm", 0))
        # ★ 香港住宅默认内开门 —— 唔再靠门弧幾何去猜（实测 plan_hk_b 两扇内开门
        #   都被判成 out，而 truth 是 in）。
        #   ★★ 但一扇门只有**一个**开门方向：门扇只会扫进一间房。
        #   曾经写成「两边都 in」，结果两间房都要求门扇扫掠区 ——
        #   等于凭空把每扇门的净空需求翻倍（睡房那两扇就是这样变成
        #   『无论点摆床都踩到门』的）。所以这里要**选边**：
        #   按房间私密程度排序，门开向更私密的那一间
        #   （香港惯例：睡房/浴室门向内开，唔好阻住走廊）。
        # 越细越「应该开门进去」：浴室 > 睡房 > 厨房 > 储物 > 饭厅 > 客厅。
        # 浴室排第一是因为 HK 浴室通常细小、门要向内开（向外开会撞走廊）；
        # 睡房第二；客厅几乎永远是被开进来的那一间。
        PRIVATE = {"bath": 0, "bedroom": 1, "kitchen": 2, "storage": 3,
                   "dining": 4, "living": 5}
        swing_room = None
        if op["kind"] == "door" and len(sides) == 2:
            def _rank(t):
                _s, _rm = t
                # 房間的 func 用 id 推（id 就是由 label 映射過來的 living/bedroom/…）
                return (PRIVATE.get(_rm.get("id") or "", 9),
                        _rm.get("area_mm2", 0))
            swing_room = min(sides, key=_rank)[1]["id"]
        for s, room in sides:
            wid = None
            for k, src in enumerate(room.get("_wall_src", [])):
                if src == op.get("wall"):
                    wid = "w%d" % k
            wdir = "in" if (swing_room is None or room["id"] == swing_room) else "out"
            room["openings"].append(dict(
                id="%s%d" % ("d" if op["kind"] == "door" else "n", len(room["openings"])),
                wall=wid, kind=op["kind"], center_mm=c_mm, width_mm=int(op["width_mm"]),
                swing=(dict(dir=wdir, hinge=op["hinge"] or "left")
                       if op["kind"] == "door" else None)))
        if len(sides) == 2:
            connections.append(dict(a=sides[0][1]["id"], b=sides[1][1]["id"],
                                    type="doorway" if op["kind"] == "door" else "window",
                                    width_mm=int(op["width_mm"])))
        elif len(sides) == 1 and op["kind"] == "door" and entry is None:
            s, room = sides[0]
            out = 100.0
            pt = (int(round(cx)), int(round(cy + (t + out) * s))) if axis == "h" else \
                 (int(round(cx + (t + out) * s)), int(round(cy)))
            entry = dict(room=room["id"], point=[pt[0], pt[1]])

    if entry is None:
        if rooms:                       # 没找到对外大门: 用最大房间的顶边中点近似
            big = rooms[0]
            xs = [p[0] for p in big["polygon"]]
            ys = [p[1] for p in big["polygon"]]
            entry = dict(room=big["id"], point=[int(np.mean(xs)), int(min(ys) - 100)])
            meta_extra.setdefault("warnings", []).append("未识别到对外大门, entry 用最大房间近似")
        else:
            entry = dict(room="unknown", point=[0, 0])
            meta_extra.setdefault("warnings", []).append(
                "没有识别出任何闭合房间 —— 图片可能不是平面图, 或者墙体太碎, 建议走人工校正")

    for room in rooms:
        room.pop("_wall_src", None)
    # 整体平移到 (0,0) 起点 (JSON 坐标 = 图像坐标 - 这个偏移)
    allp = [p for r in rooms for p in r["polygon"]]
    mx = my = 0.0
    if allp:
        mx = min(p[0] for p in allp)
        my = min(p[1] for p in allp)
        if mx or my:
            for r in rooms:
                r["polygon"] = [[p[0] - mx, p[1] - my] for p in r["polygon"]]
                for w in r["walls"]:
                    w["a"] = [w["a"][0] - mx, w["a"][1] - my]
                    w["b"] = [w["b"][0] - mx, w["b"][1] - my]
            if entry:
                entry["point"] = [entry["point"][0] - mx, entry["point"][1] - my]
            for w in walls_mm:      # 墙也要跟着平移, 后面算 coverage 要用同一套坐标
                w["a"] = [w["a"][0] - mx, w["a"][1] - my]
                w["b"] = [w["b"][0] - mx, w["b"][1] - my]
    return rooms, connections, entry, (mx, my)


def walls_mm_by_id(walls_mm, wid):
    if wid is None:
        return None
    for w in walls_mm:
        if w["id"] == wid:
            return w
    return None


def compute_confidence(scale_source: str, rooms, walls_mm, openings_mm,
                       residual_skew: float, ink: float, warnings) -> Tuple[float, dict]:
    det = {}
    c = 0.30
    if scale_source == "ocr":
        c += 0.16
        det["scale"] = 0.16
    elif scale_source == "cli":
        c += 0.08
        det["scale"] = 0.08
    elif scale_source == "wall-thickness":
        c += 0.03
        det["scale"] = 0.03
    else:
        det["scale"] = 0.0
    n = len(rooms)
    if n >= 3:
        c += 0.12
        det["rooms"] = 0.12
    elif n >= 1:
        c += 0.05
        det["rooms"] = 0.05
    else:
        det["rooms"] = 0.0
    if rooms:
        cov2 = _room_wall_coverage(rooms, walls_mm)
        c += 0.16 * cov2
        det["wall_coverage"] = 0.16 * cov2
    if openings_mm:
        k = min(1.0, len(openings_mm) / 4.0)
        c += 0.08 * k
        det["openings"] = 0.08 * k
    skew_pen = min(1.0, abs(residual_skew) / 3.0)
    c += 0.08 * (1.0 - skew_pen)
    det["skew"] = 0.08 * (1.0 - skew_pen)
    if not warnings:
        c += 0.05
        det["clean"] = 0.05
    if not rooms or not walls_mm:                 # 什么都没识别出来, 分数要塌下去
        c *= 0.45
        det["empty"] = -round(c * 0.55, 3)
    return float(max(0.02, min(0.95, c))), det


def _seg_overlap_len(a, b, c, d, tol):
    """a-b 是房间边, c-d 是墙。返回 a-b 上距离墙 <= tol 的长度。"""
    # 投影到 a-b 参数空间, 用采样近似
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1:
        return 0.0
    n = max(8, int(L / 60.0))
    cnt = 0
    for i in range(n + 1):
        t = i / n
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if _pt_seg(p, c, d) <= tol:
            cnt += 1
    return L * cnt / (n + 1)


def _pt_seg(p, a, b):
    ax, ay = a
    bx, by = b
    abx, aby = bx - ax, by - ay
    den = abx * abx + aby * aby
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - ax) * abx + (p[1] - ay) * aby) / den))
    return math.hypot(p[0] - (ax + t * abx), p[1] - (ay + t * aby))


def _room_wall_coverage(rooms, walls_mm) -> float:
    tot = 0.0
    cov = 0.0
    for r in rooms:
        poly = [tuple(p) for p in r["polygon"]]
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L < 1:
                continue
            tot += L
            best = 0.0
            for w in walls_mm:
                best = max(best, _seg_overlap_len(a, b, tuple(w["a"]), tuple(w["b"]),
                                                  w["thickness_mm"] / 2.0 + 120.0))
            cov += min(best, L)
    return float(cov / tot) if tot > 0 else 0.0


# ============================================================================
# 6. 入口
# ============================================================================
def vectorize(image_path: Path, out_path: Path, params: Params,
              overlay_path: Optional[Path] = None, verbose: bool = True) -> dict:
    gray, notes = load_gray(image_path)
    bw, ink = binarize(gray)
    H, W = gray.shape
    scale_hint = params.scale_mm_px or params.assume_scale_mm_px
    gray, bw, angle, raw_angle = deskew(gray, bw, max(20, int(400.0 / scale_hint)))
    residual, _ = estimate_skew_bands(bw, max(20, int(400.0 / scale_hint)))
    H, W = gray.shape
    result = None
    for attempt in range(2):
        min_len = max(18, int(params.min_wall_mm / scale_hint))
        hk = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len, 1))
        vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len))
        hb = extract_bands(cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk), "h", min_len)
        vb = extract_bands(cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk), "v", min_len)
        allb = hb + vb
        net = network_mask(bw, int(max(8, 0.012 * min(H, W))))
        in_net = [b for b in allb if _band_in_network(b, net)]
        out_net = [b for b in allb if not _band_in_network(b, net)]
        dim_bands = [b for b in out_net if b.length >= 0.05 * W]
        walls_px, opens_px = detect_walls(in_net, scale_hint, params)

        scale_source = "assume"
        scale = params.scale_mm_px or params.assume_scale_mm_px
        tokens, engine = ([], "off")
        if params.use_ocr:
            try:
                tokens, engine = ocr_dimension_tokens(bw)
            except Exception as exc:                     # OCR 永远不该让管线挂掉
                notes.append("OCR 失败, 已降级: %r" % (exc,))
                tokens, engine = [], "error"
        ocr_scale, matches = (None, [])
        if tokens:
            # ① 优先: 标注数字 + 它两侧最近的墙 (最稳)
            ocr_scale, matches = scale_from_walls_and_tokens(walls_px, dim_bands, tokens)
            method = "wall-span"
            if ocr_scale is None:
                # ② 退而求其次: 标注数字 + 它所在的尺寸线段长度
                ocr_scale, matches = scale_from_dimension_lines(dim_bands, tokens)
                method = "dim-line"
        else:
            method = "-"
        thick_scale = scale_from_wall_thickness(walls_px)
        if ocr_scale is not None and thick_scale is not None \
                and not (0.5 * thick_scale <= ocr_scale <= 2.0 * thick_scale):
            notes.append("OCR 反推的比例尺 %.3f mm/px 与墙厚不一致 (墙体推断 %.3f), 已弃用"
                         % (ocr_scale, thick_scale))
            ocr_scale, matches = None, []
        if params.scale_mm_px is not None:
            scale, scale_source = params.scale_mm_px, "cli"
        elif ocr_scale is not None:
            scale, scale_source = ocr_scale, "ocr"
        elif thick_scale is not None:
            scale, scale_source = thick_scale, "wall-thickness"
            notes.append("按承重墙≈200mm 反推比例尺 %.3f mm/px (未经尺寸标注校验)" % scale)
        else:
            scale, scale_source = params.assume_scale_mm_px, "assume"
            if params.use_ocr:
                notes.append("没有可用的尺寸标注/OCR, 比例尺回退到 %.3f mm/px" % scale)

        result = dict(bw=bw, gray=gray, walls_px=walls_px, opens_px=opens_px,
                      dim_bands=dim_bands, tokens=tokens, engine=engine,
                      matches=matches, scale=scale, scale_source=scale_source, method=method,
                      in_net=in_net, out_net=out_net, net=net)
        if scale_source == "assume" or (params.scale_mm_px is not None):
            break
        if abs(scale - scale_hint) / scale_hint <= 0.15 or attempt == 1:
            break
        scale_hint = scale

    scale = result["scale"]
    walls_px, opens_px = result["walls_px"], result["opens_px"]
    if not walls_px:
        notes.append("没有检测到任何墙体")

    # 先以图像原点算 mm, 再统一平移
    walls_mm = walls_to_mm(walls_px, opens_px, scale, (0.0, 0.0))
    opens_mm = openings_to_mm(opens_px, walls_mm, scale, (0.0, 0.0))

    mask = rasterize(walls_px, opens_px, gray.shape, scale, (0.0, 0.0))
    rooms_px = extract_rooms(mask, scale, min_area_mm2=max(0.8e6, (params.min_wall_mm * 2) ** 2))

    warnings: List[str] = list(notes)
    meta_extra = dict(warnings=warnings)
    labels_px = []
    tpl = _label_templates()
    for r in rooms_px:
        lab, sc = read_room_label(bw, r["px"], tpl)
        labels_px.append((lab, sc))
        if os.environ.get("HKSPACE_DEBUG_LABELS"):
            xs = [p[0] for p in r["px"]]
            ys = [p[1] for p in r["px"]]
            print("   [dbg] room%d bbox=(%d,%d,%d,%d) poly=%s -> %r %.2f"
                  % (len(labels_px), min(xs), min(ys), max(xs) - min(xs),
                     max(ys) - min(ys), [[int(a), int(b)] for a, b in r["px"]], lab, sc))
    if tpl:
        print("   房名辨識: %s" % (["%s(%s)" % (l or "?", round(s, 2)) for l, s in labels_px]))
    rooms, connections, entry, offset_mm = build_room_json(
        walls_mm, opens_mm, rooms_px, scale, (0.0, 0.0), params, meta_extra,
        labels_px=labels_px)

    conf, det = compute_confidence(result["scale_source"], rooms, walls_mm, opens_mm,
                                   residual, ink, meta_extra.get("warnings", []))
    doc = dict(flat_id=image_path.stem, units="mm", rooms=rooms,
               connections=connections, fixed_services=[], entry=entry,
               meta=dict(source="floorplan_upload", confidence=round(conf, 3)))
    doc["meta"].update(dict(
        mm_per_px=round(scale, 4),
        origin_px=[round(offset_mm[0] / scale, 2), round(offset_mm[1] / scale, 2)],
        scale_source=result["scale_source"],
        scale_method=result.get("method", "-"),
        ocr_engine=result["engine"],
        ocr_tokens=[t["text"] for t in result["tokens"]][:20],
        scale_votes=[[round(m[0], 3), m[1], round(float(m[2]), 1), m[3]]
                     for m in result["matches"]],
        image_size_px=[int(W), int(H)],
        deskew_deg=round(angle, 2),
        residual_skew_deg=round(residual, 3),
        walls_detected=len(walls_mm),
        openings_detected=len(opens_mm),
        confidence_terms={k: round(v, 3) for k, v in det.items()},
        warnings=meta_extra.get("warnings", []),
        rectified_image=None,
        overlay=None,
    ))
    doc = _sanitize(doc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if overlay_path is not None:
        try:
            from render_overlay import render_overlay, side_by_side
            rect = out_path.with_name(out_path.stem + "_rectified.png")
            cv2.imwrite(str(rect), gray)
            doc["meta"]["rectified_image"] = str(rect)
            doc["meta"]["overlay"] = str(overlay_path)
            render_overlay(rect, out_path, overlay_path)
            side_by_side(image_path, overlay_path,
                         overlay_path.with_name(overlay_path.stem + "_sidebyside.png"))
            out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            warnings.append("叠加图渲染失败: %r" % (exc,))
            doc["meta"]["warnings"] = warnings
            out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        print("[vectorize] %s -> %s" % (image_path.name, out_path))
        print("  图像 %dx%d  ink=%.3f  倾斜矫正=%+.2f deg (残差 %+.3f deg)"
              % (W, H, ink, angle, residual))
        print("  比例尺 %.4f mm/px  (来源: %s, OCR 引擎: %s, 命中标注 %d 条)"
              % (scale, result["scale_source"], result["engine"], len(result["matches"])))
        if result["tokens"]:
            print("  OCR 读到的数字: %s" % ", ".join(t["text"] for t in result["tokens"][:20]))
        if result["matches"]:
            print("  标定明细: %s" % ", ".join("%s->%.2fmm/px(%.0fpx)" %
                                              (m[3], m[0], m[2]) for m in result["matches"][:8]))
        lb = sum(1 for r in rooms for w in r["walls"] if w["type"] == "load_bearing")
        pt = sum(1 for r in rooms for w in r["walls"] if w["type"] == "partition")
        print("  墙 %d 段 (承重 %d / 间隔 %d 次出现)   开口 %d" %
              (len(walls_mm), lb, pt, len(opens_mm)))
        print("  房间 %d 个: %s" % (len(rooms), ", ".join(
            "%s(%s) %dm2" % (r["id"], r["label"], round(r["area_mm2"] / 1e6, 1)) for r in rooms)))
        print("  confidence = %.3f  %s" % (conf, det))
        for w in meta_extra.get("warnings", []):
            print("  [warn] %s" % w)
    return doc


def _sanitize(o):
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return _sanitize(o.tolist())
    return o


# ============================================================================
# 7. 与 ground truth 对比 (demo 自测用)
# ============================================================================
def compare(pred: dict, truth: dict, grid_mm: int = 40) -> dict:
    def norm(doc):
        pts = [p for r in doc["rooms"] for p in r["polygon"]]
        mx = min(p[0] for p in pts)
        my = min(p[1] for p in pts)
        xx = max(p[0] for p in pts) - mx
        yy = max(p[1] for p in pts) - my
        return mx, my, xx, yy

    def to_grid(doc, size):
        w = int(size[0] / grid_mm) + 3
        h = int(size[1] / grid_mm) + 3
        mx, my, _, _ = norm(doc)
        out = []
        for r in doc["rooms"]:
            m = np.zeros((h, w), np.uint8)
            poly = np.array([[int((p[0] - mx) / grid_mm) + 1, int((p[1] - my) / grid_mm) + 1]
                             for p in r["polygon"]], np.int32)
            cv2.fillPoly(m, [poly], 1)
            out.append(dict(id=r["id"], mask=m, area=int(m.sum())))
        return out

    size = (max(norm(pred)[2], norm(truth)[2]), max(norm(pred)[3], norm(truth)[3]))
    a = to_grid(pred, size)
    b = to_grid(truth, size)
    used = set()
    ious = []
    for ra in a:
        best = (0.0, None)
        for j, rb in enumerate(b):
            if j in used:
                continue
            inter = int(np.logical_and(ra["mask"], rb["mask"]).sum())
            union = int(np.logical_or(ra["mask"], rb["mask"]).sum())
            iou = inter / union if union else 0.0
            if iou > best[0]:
                best = (iou, j)
        if best[1] is not None:
            used.add(best[1])
        ious.append((ra["id"], best[0], b[best[1]]["id"] if best[1] is not None else None))
    pred_open = {}
    truth_open = {}
    for r in pred["rooms"]:
        for o in r["openings"]:
            pred_open[o["kind"]] = pred_open.get(o["kind"], 0) + 1
    for r in truth["rooms"]:
        for o in r["openings"]:
            truth_open[o["kind"]] = truth_open.get(o["kind"], 0) + 1
    sc = pred["meta"].get("mm_per_px")
    true_sc = None
    if truth["meta"].get("px_per_mm"):
        true_sc = 1.0 / truth["meta"]["px_per_mm"]
    return dict(
        pred_rooms=len(pred["rooms"]), truth_rooms=len(truth["rooms"]),
        mean_iou=round(float(np.mean([i for _, i, _ in ious])) if ious else 0.0, 4),
        ious=[(a_, round(i, 3), b_) for a_, i, b_ in ious],
        pred_openings=pred_open, truth_openings=truth_open,
        scale_mm_px=sc, scale_err_pct=(round(100.0 * (sc - true_sc) / true_sc, 2)
                                       if sc and true_sc else None),
        pred_walls=sum(len(r["walls"]) for r in pred["rooms"]),
        truth_walls=sum(len(r["walls"]) for r in truth["rooms"]),
    )


def main():
    ap = argparse.ArgumentParser(description="户型图 -> Room JSON 矢量化管线")
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale-mm-per-px", type=float, default=None,
                    help="已知比例尺 (mm/px)。给了就优先用它, 否则先试 OCR")
    ap.add_argument("--assume-scale", type=float, default=DEFAULT_ASSUME_SCALE_MM_PX,
                    help="OCR 失败时的兜底比例尺, 默认 %.1f mm/px" % DEFAULT_ASSUME_SCALE_MM_PX)
    ap.add_argument("--no-ocr", action="store_true", help="跳过 OCR")
    ap.add_argument("--min-wall-mm", type=float, default=400.0)
    ap.add_argument("--min-opening-mm", type=float, default=400.0)
    ap.add_argument("--max-opening-mm", type=float, default=3200.0)
    ap.add_argument("--overlay", default=str(ROOT / "out" / "floorplan_overlay.png"))
    ap.add_argument("--no-overlay", action="store_true")
    ap.add_argument("--truth", default=None, help="给出 ground truth JSON 时打印精度对比")
    args = ap.parse_args()

    params = Params(min_wall_mm=args.min_wall_mm, min_opening_mm=args.min_opening_mm,
                    max_opening_mm=args.max_opening_mm, assume_scale_mm_px=args.assume_scale,
                    scale_mm_px=args.scale_mm_per_px, use_ocr=not args.no_ocr)
    out = Path(args.out)
    overlay = None if args.no_overlay else Path(args.overlay)
    doc = vectorize(Path(args.image), out, params, overlay_path=overlay)

    if args.truth:
        truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
        rep = compare(doc, truth)
        print("\n[compare vs ground truth]")
        print("  房间数 pred=%d truth=%d   平均 IoU=%.3f" %
              (rep["pred_rooms"], rep["truth_rooms"], rep["mean_iou"]))
        for rid, iou, tid in rep["ious"]:
            print("    %-10s <-> %-10s IoU=%.3f" % (rid, tid, iou))
        print("  开口 pred=%s truth=%s" % (rep["pred_openings"], rep["truth_openings"]))
        print("  比例尺 pred=%s mm/px truth=%s mm/px 误差=%s%%" %
              (rep["scale_mm_px"], rep["scale_mm_px"] and round(1 / truth["meta"]["px_per_mm"], 4)
               if truth["meta"].get("px_per_mm") else None, rep["scale_err_pct"]))


if __name__ == "__main__":
    main()
