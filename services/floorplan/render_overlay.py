#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_overlay.py — 人工校正界面原型图
=====================================
把「原图半透明垫底 + 识别出的矢量线叠加」画出来:

    * 房间多边形   : 半透明色块 + 描边 + 中文名 + 面积
    * 承重墙       : 红色实线 (load_bearing)
    * 间隔墙       : 蓝色细线 (partition)
    * 门           : 绿色粗线 + 开启弧线
    * 窗           : 青色双线
    * 入口         : 品红标记
    * 左上角图例   : 比例尺来源 / confidence / 房间墙数

单独运行:
    python3 services/floorplan/render_overlay.py --image <原图> --json <房间json> \
        --out out/floorplan_overlay.png
    (若 json 的 meta 里有 rectified_image, 会优先用那张已做倾斜矫正的图垫底)

也提供一个 render_overlay(image, json, out) 函数给 vectorize.py 调用。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]

ROOM_COLORS = [(196, 168, 255), (170, 226, 190), (255, 214, 170), (176, 214, 255),
               (232, 190, 240), (200, 232, 160), (250, 190, 190), (190, 220, 235)]
C_LOAD = (46, 46, 220)      # BGR 红
C_PART = (208, 120, 32)     # BGR 蓝
C_DOOR = (60, 170, 60)      # 绿
C_WIN = (200, 190, 30)      # 青
C_ENTRY = (200, 40, 200)    # 品红


def _find_cjk_font(size: int):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
              "/usr/share/fonts/truetype/arphic/uming.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "C:/Windows/Fonts/msjh.ttc",
              "C:/Windows/Fonts/msyh.ttc",
              "C:/Windows/Fonts/mingliu.ttc",
              "C:/Windows/Fonts/simsun.ttc"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _txt(img, text, org, size=22, color=(20, 20, 20)):
    """用 PIL 画 UTF-8 文字 (OpenCV 画不了中文)。"""
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    d.text(org, text, font=_find_cjk_font(size), fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)


def _poly_area(poly):
    a = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def load_doc(json_path: Path):
    doc = json.loads(Path(json_path).read_text(encoding="utf-8"))
    meta = doc.get("meta", {})
    mm_px = float(meta.get("mm_per_px") or 1.0)
    ox, oy = (meta.get("origin_px") or [0.0, 0.0])[:2]
    return doc, mm_px, float(ox), float(oy)


def render_overlay(image_path: Path, json_path: Path, out_path: Path,
                   alpha: float = 0.55, title: Optional[str] = None) -> Path:
    doc, mm_px, ox, oy = load_doc(json_path)
    base = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if base is None:
        raise SystemExit("[render_overlay] 读不到图片: %s" % image_path)

    def P(p):
        """Room JSON 的 mm 坐标 -> 图片像素。"""
        return (int(round(ox + p[0] / mm_px)), int(round(oy + p[1] / mm_px)))

    def S(v):
        return int(round(v / mm_px))

    img = cv2.addWeighted(base, alpha, np.full_like(base, 255), 1.0 - alpha, 0)

    # ---- 房间色块 ----
    for i, room in enumerate(doc.get("rooms", [])):
        pts = np.array([P(p) for p in room["polygon"]], np.int32)
        col = ROOM_COLORS[i % len(ROOM_COLORS)]
        layer = img.copy()
        cv2.fillPoly(layer, [pts], col)
        img = cv2.addWeighted(img, 0.70, layer, 0.30, 0)
        cv2.polylines(img, [pts], True, (90, 90, 90), 2, cv2.LINE_AA)

    # ---- 墙 ----
    for room in doc.get("rooms", []):
        for w in room.get("walls", []):
            a, b = P(w["a"]), P(w["b"])
            if w.get("type") == "load_bearing":
                cv2.line(img, a, b, C_LOAD, 5, cv2.LINE_AA)
                cv2.line(img, a, b, (255, 255, 255), 1, cv2.LINE_AA)
            else:
                cv2.line(img, a, b, C_PART, 3, cv2.LINE_AA)

    # ---- 开口 ----
    wall_by_room = {}
    for room in doc.get("rooms", []):
        wall_by_room[room["id"]] = {w["id"]: w for w in room.get("walls", [])}
    for room in doc.get("rooms", []):
        for op in room.get("openings", []):
            host = wall_by_room[room["id"]].get(op.get("wall"))
            if host is None:
                continue
            a = np.array(host["a"], float)
            b = np.array(host["b"], float)
            u = b - a
            n = np.linalg.norm(u)
            if n < 1:
                continue
            u = u / n
            # schema 里开口是用「墙 + 沿墙的 center_mm」表示的, 这里换算成图纸坐标
            c = a + u * float(op.get("center_mm", 0))
            hw = op["width_mm"] / 2.0
            p1, p2 = P(c - u * hw), P(c + u * hw)
            if op["kind"] == "window":
                off = np.array([-u[1], u[0]]) * 60.0
                for s in (-1, 1):
                    q1, q2 = P(c - u * hw + off * s), P(c + u * hw + off * s)
                    cv2.line(img, q1, q2, C_WIN, 3, cv2.LINE_AA)
            else:
                entry = op.get("id") == "d_entry"
                color = C_ENTRY if entry else C_DOOR
                thick = 8 if entry else 4
                cv2.line(img, p1, p2, color, thick, cv2.LINE_AA)
                swing = op.get("swing") or {}
                hinge = p1 if swing.get("hinge", "left") == "left" else p2
                other = p2 if hinge is p1 else p1
                # 90 度开启弧: 从另一侧门垛转到门扇末端 (门扇朝房间内摆)
                cen = np.array([np.mean([q[0] for q in room["polygon"]]),
                                np.mean([q[1] for q in room["polygon"]])], float)
                n2 = np.array([-u[1], u[0]])
                h_mm = np.array([(hinge[0] - ox) * mm_px, (hinge[1] - oy) * mm_px])
                side = 1.0 if np.dot(cen - h_mm, n2) > 0 else -1.0
                leaf = np.array([hinge[0] + n2[0] * side * op["width_mm"] / mm_px,
                                 hinge[1] + n2[1] * side * op["width_mm"] / mm_px])
                a0 = math.degrees(math.atan2(other[1] - hinge[1], other[0] - hinge[0]))
                a1 = math.degrees(math.atan2(leaf[1] - hinge[1], leaf[0] - hinge[0]))
                d = (a1 - a0 + 540) % 360 - 180
                radius = max(8, S(op["width_mm"]))
                cv2.ellipse(img, hinge, (radius, radius), 0, a0, a0 + d, color, 3 if entry else 2, cv2.LINE_AA)
                cv2.line(img, hinge, (int(leaf[0]), int(leaf[1])), color, 3 if entry else 2, cv2.LINE_AA)
                if entry:
                    mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
                    pad = 18
                    cv2.rectangle(img, (min(p1[0], p2[0]) - pad, min(p1[1], p2[1]) - pad),
                                  (max(p1[0], p2[0]) + pad, max(p1[1], p2[1]) + pad), C_ENTRY, 3)
                    cv2.circle(img, hinge, 10, C_ENTRY, -1, cv2.LINE_AA)
                    tx = max(12, min(img.shape[1] - 190, mid[0] - 90))
                    ty = max(8, mid[1] - 42)
                    img = _txt(img, "ENTRY DOOR", (tx, ty), 26, C_ENTRY)

    # ---- 入口 ----
    entry = doc.get("entry")
    if entry:
        q = P(entry["point"])
        cv2.circle(img, q, 9, C_ENTRY, -1, cv2.LINE_AA)
        cv2.circle(img, q, 14, (255, 255, 255), 2, cv2.LINE_AA)
        tx = max(12, min(img.shape[1] - 110, q[0] - 80 if q[0] > img.shape[1] * 0.65 else q[0] + 16))
        img = _txt(img, "ENTRY", (tx, q[1] - 10), 22, (150, 20, 150))

    # ---- 房间名 + 面积 ----
    for i, room in enumerate(doc.get("rooms", [])):
        pts = np.array([P(p) for p in room["polygon"]], np.float32)
        c = pts.mean(axis=0)
        area = _poly_area(room["polygon"]) / 1e6
        img = _txt(img, "%s %s" % (room["id"], room.get("label", "")),
                   (int(c[0]) - 40, int(c[1]) - 14), 30, (30, 30, 30))
        img = _txt(img, "%.2f m2" % area, (int(c[0]) - 40, int(c[1]) + 20), 24, (70, 70, 70))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path


def side_by_side(orig_path: Path, overlay_path: Path, out_path: Path) -> Path:
    a = cv2.imread(str(orig_path), cv2.IMREAD_COLOR)
    b = cv2.imread(str(overlay_path), cv2.IMREAD_COLOR)
    if a is None or b is None:
        return out_path
    h = max(a.shape[0], b.shape[0])

    def fit(x):
        if x.shape[0] == h:
            return x
        f = h / x.shape[0]
        return cv2.resize(x, (int(x.shape[1] * f), h), interpolation=cv2.INTER_AREA)

    a, b = fit(a), fit(b)
    sep = np.full((h, 12, 3), 255, np.uint8)
    both = np.hstack([a, sep, b])
    both = _txt(both, "original", (20, 12), 26, (60, 60, 60))
    both = _txt(both, "vectorized", (a.shape[1] + 32, 12), 26, (60, 60, 60))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), both)
    return out_path


def measure_alignment(image_path: Path, doc: dict) -> dict:
    """房间多边形盖回原图。客厅内部大部分应该还是填色，而不是白纸。"""
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return dict(per_room={}, living_agreement=0.0, blocking=True)
    meta = doc.get("meta") or {}
    mm = float(meta.get("mm_per_px") or 1.0)
    origin = meta.get("origin_px") or [0.0, 0.0]
    ox, oy = float(origin[0]), float(origin[1])
    scores = {}
    for room in doc.get("rooms") or []:
        pts = np.array([[int(round(ox + p[0] / mm)), int(round(oy + p[1] / mm))]
                        for p in room.get("polygon") or []], np.int32)
        if len(pts) < 3:
            scores[room.get("id", "?")] = 0.0
            continue
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.fillPoly(mask, [pts], 1)
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            scores[room.get("id", "?")] = 0.0
            continue
        step = max(1, len(xs) // 900)
        sample = image[ys[::step], xs[::step]]
        colored = (sample.min(axis=1) < 245)
        not_ink = sample.mean(axis=1) > 45
        scores[room.get("id", "?")] = round(float((colored & not_ink).mean()), 3)
    living = float(scores.get("living", 0.0))
    return dict(per_room=scores, living_agreement=living, blocking=living < 0.45)


def main():
    ap = argparse.ArgumentParser(description="原图 + 矢量线叠加图 (人工校正界面原型)")
    ap.add_argument("--image", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--out", default=str(ROOT / "out" / "floorplan_overlay.png"))
    ap.add_argument("--alpha", type=float, default=0.55, help="底图不透明度, 越小底图越淡")
    ap.add_argument("--side-by-side", action="store_true", help="同时输出原图|叠加图 对比图")
    ap.add_argument("--rectified", action="store_true",
                    help="用 json 里 meta.rectified_image 垫底 (默认就是优先用它)")
    ap.add_argument("--raw", action="store_true", help="强制用 --image 垫底")
    args = ap.parse_args()

    doc, _, _, _ = load_doc(Path(args.json))
    base = Path(args.image)
    if not args.raw:
        rect = doc.get("meta", {}).get("rectified_image")
        if rect and Path(rect).exists():
            base = Path(rect)
    out = Path(args.out)
    render_overlay(base, Path(args.json), out, alpha=args.alpha)
    print("[render_overlay] %s -> %s" % (base.name, out))
    if args.side_by_side:
        sbs = out.with_name(out.stem + "_sidebyside.png")
        side_by_side(Path(args.image), out, sbs)
        print("[render_overlay] side-by-side -> %s" % sbs)


if __name__ == "__main__":
    main()
