#!/usr/bin/env python3
"""
hk-space :: packages/templates/contact_sheet.py
=================================================================
生成「30 个模板总览图」PNG —— 目的是用眼睛检查**比例对不对**。

    python3 packages/templates/contact_sheet.py
    -> /mnt/d/openclaw/hk-space/out/templates_contact_sheet.png

为什么不用 matplotlib
-----------------------------------------------------------------
先用 matplotlib 的 Poly3DCollection 做过一版，结果柜门上的控制面板、
洗衣机舱门圈这些**贴在大平面上的小零件**会渲染成一堆黑三角。
原因是 matplotlib 只有"按多边形平均深度排序"，没有逐像素 z-buffer：
一块 600x850 的前脸被切成几个大三角形后，它的平均深度会被整个 z 跨度
带偏，于是排到小零件前面去，把零件切成一堆楔形。

所以这里自己写了一个 ~60 行的 numpy **软件 z-buffer 光栅化器**（正交投影），
再加 PIL 拼版和写字。用了之后遮挡关系完全正确，而且可以做 AA（2x 超采样）：
  * 每个模板 3D 渲染 -> 一张小图
  * 正交等轴测投影，elev=22 / azim=+58（看家具正面 +Y）
  * 面颜色 = 材质 baseColor + Lambert 光照 + 每零件常数环境光遮蔽
  * 地面阴影 = 把模型三角形压到 z=0 再光栅化（会被模型正确遮挡）
  * 占地轮廓 + 1 米比例尺（1 米是公共参照，所以可以跨格子比大小）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    from . import catalog as C           # noqa: E402
except ImportError:
    import catalog as C                  # noqa: E402

ROOT = HERE.parents[1]
GLB_DIR = ROOT / "out" / "templates"
DEFAULT_PNG = ROOT / "out" / "templates_contact_sheet.png"

FONT_REG = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/mnt/c/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc"]
FONT_BOLD = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
             "/mnt/c/Windows/Fonts/msyhbd.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"]

BG = np.array([250, 249, 246], dtype=np.uint8)
SHADOW = np.array([231, 229, 223], dtype=np.uint8)
OUTLINE = np.array([176, 173, 168], dtype=np.uint8)
SCALEBAR = np.array([120, 117, 113], dtype=np.uint8)
INK = (26, 26, 26)
INK2 = (92, 90, 88)

# 灯光从镜头左上前方来：顶面最亮、正面次之、侧面最暗
LIGHT = np.array([-0.42, 0.58, 0.70])
LIGHT = LIGHT / np.linalg.norm(LIGHT)

ELEV, AZIM = 22.0, 58.0


def font(paths, size):
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# =====================================================================
# 1. 读 GLB -> 三角形 + 每面颜色
# =====================================================================
def load_parts(path: Path):
    sc = trimesh.load(path, force="scene")
    tris, cols = [], []
    zmin, zmax = sc.bounds[0][2], sc.bounds[1][2]
    for node in sc.graph.nodes_geometry:
        tr, gname = sc.graph.get(node)
        g = sc.geometry[gname].copy()
        g.apply_transform(tr)
        if len(g.faces) == 0:
            continue
        t = g.triangles
        n = g.face_normals
        base = np.array([230, 228, 224], dtype=float) / 255.0
        alpha = 1.0
        try:
            bc = g.visual.material.baseColorFactor
            if bc is not None:
                base = np.asarray(bc[:3], dtype=float) / 255.0
                if len(bc) > 3 and bc[3] < 250:
                    alpha = float(bc[3]) / 255.0
        except Exception:
            pass
        lam = np.abs(n @ LIGHT)
        zc = float(g.bounds.mean(axis=0)[2])
        ao = 0.80 + 0.20 * float(np.clip((zc - zmin) / max(1e-6, zmax - zmin), 0, 1))
        shade = (0.42 + 0.62 * lam) * ao
        c = np.clip(base[None, :] * shade[:, None], 0, 1)
        if alpha < 1.0:                      # 半透明按 alpha 混到背景，凑合当玻璃
            c = c * alpha + (BG / 255.0)[None, :] * (1 - alpha)
        tris.append(t)
        cols.append(c)
    return np.concatenate(tris, 0), np.concatenate(cols, 0)


# =====================================================================
# 2. 软件 z-buffer 光栅化（正交投影）
# =====================================================================
def _basis(elev: float, azim: float):
    e, a = np.radians(elev), np.radians(azim)
    cam = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    right = np.cross([0.0, 0.0, 1.0], cam)
    right /= np.linalg.norm(right)
    up = np.cross(cam, right)
    return cam, right, up


def rasterize(tris: np.ndarray, cols: np.ndarray, W: int, H: int,
              cam, right, up, bg: np.ndarray = BG, cull: bool = True) -> np.ndarray:
    """正交投影 + 逐像素 z-buffer。tris 单位米，返回 (H,W,3) uint8。"""
    P = tris.reshape(-1, 3)
    x = (P @ right).reshape(-1, 3)
    y = (P @ up).reshape(-1, 3)
    z = (P @ cam).reshape(-1, 3)

    xmin, xmax = float(x.min()), float(x.max())
    ymin, ymax = float(y.min()), float(y.max())
    sc = min((W - 1) / max(xmax - xmin, 1e-9), (H - 1) / max(ymax - ymin, 1e-9))
    ox = (W - (xmax - xmin) * sc) / 2.0 - xmin * sc
    oy = (H - (ymax - ymin) * sc) / 2.0 + ymax * sc
    sx = x * sc + ox
    sy = oy - y * sc

    zbuf = np.full((H, W), -1e18, dtype=np.float64)
    img = np.empty((H, W, 3), dtype=np.uint8)
    img[:] = bg
    col8 = np.clip(cols * 255.0, 0, 255).astype(np.uint8)

    fn = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    order = np.argsort(-(fn @ cam))                    # 背面先剔除（正交下法线恒定）
    keep = (fn @ cam) > 0 if cull else np.ones(len(tris), bool)

    for i in order:
        if not keep[i]:
            break                                      # order 已按朝向排序
        x0 = int(np.floor(sx[i].min())); x1 = int(np.ceil(sx[i].max())) + 1
        y0 = int(np.floor(sy[i].min())); y1 = int(np.ceil(sy[i].max())) + 1
        x0 = max(x0, 0); y0 = max(y0, 0); x1 = min(x1, W); y1 = min(y1, H)
        if x1 <= x0 or y1 <= y0:
            continue
        ax, ay = sx[i, 0], sy[i, 0]
        bx, by = sx[i, 1], sy[i, 1]
        cx, cy = sx[i, 2], sy[i, 2]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-12:
            continue
        X = np.arange(x0, x1) + 0.5
        Y = (np.arange(y0, y1) + 0.5)[:, None]
        w0 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / d
        w1 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / d
        w2 = 1.0 - w0 - w1
        m = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not m.any():
            continue
        zz = w0 * z[i, 0] + w1 * z[i, 1] + w2 * z[i, 2]
        sub = zbuf[y0:y1, x0:x1]
        hit = m & (zz > sub)
        if not hit.any():
            continue
        sub[hit] = zz[hit]
        img[y0:y1, x0:x1][hit] = col8[i]
    return img


def ground_extras(tris: np.ndarray, lo, hi):
    """地面阴影（模型投影到 z=0）+ 占地轮廓线，都是真的 3D 几何，交给 z-buffer。"""
    zg = float(lo[2]) + 1e-4
    sh = tris.copy()
    sh[:, :, 2] = zg
    shc = np.tile(SHADOW / 255.0, (len(sh), 1))
    w = max((hi - lo).max() * 0.006, 0.004)
    quads = []
    for (p0, p1) in (((lo[0], lo[1]), (hi[0], lo[1])), ((hi[0], lo[1]), (hi[0], hi[1])),
                     ((hi[0], hi[1]), (lo[0], hi[1])), ((lo[0], hi[1]), (lo[0], lo[1]))):
        a = np.array([p0[0], p0[1], zg]); b = np.array([p1[0], p1[1], zg])
        n = np.array([-(b[1] - a[1]), b[0] - a[0], 0.0])
        n = n / (np.linalg.norm(n) + 1e-12) * w / 2
        quads.append([a - n, b - n, b + n])
        quads.append([a - n, b + n, a + n])
    q = np.array(quads)
    qc = np.tile(OUTLINE / 255.0, (len(q), 1))
    return (np.concatenate([sh, q]), np.concatenate([shc, qc]))


def scale_bar(lo, hi, h_m: float = 1.0):
    """1 米比例尺（一根真的 3D 细柱）。所有格子共用，所以能跨格子比大小。"""
    size = hi - lo
    bx = float(lo[0] - size[0] * 0.075)
    by = float(lo[1] - size[1] * 0.075)
    z0 = float(lo[2])
    r = max(size.max() * 0.008, 0.006)
    z1 = z0 + h_m
    tris = []
    for (zz0, zz1) in ((z0, z1), (z0 - r * 2, z0)):
        for k in range(6):
            a0 = k * np.pi / 3 + np.pi / 6
            a1 = a0 + np.pi / 3
            p0 = np.array([bx + r * np.cos(a0), by + r * np.sin(a0)])
            p1 = np.array([bx + r * np.cos(a1), by + r * np.sin(a1)])
            A = np.array([p0[0], p0[1], zz0]); B = np.array([p1[0], p1[1], zz0])
            C = np.array([p1[0], p1[1], zz1]); D = np.array([p0[0], p0[1], zz1])
            tris += [[A, B, C], [A, C, D]]
    t = np.array(tris)
    return t, np.tile(SCALEBAR / 255.0, (len(t), 1)), (bx, by, z0 + h_m / 2.0)


# =====================================================================
# 3. 拼版
# =====================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb-dir", default=str(GLB_DIR))
    ap.add_argument("--out", default=str(DEFAULT_PNG))
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--tile", type=int, default=440, help="每格渲染宽度(px)")
    ap.add_argument("--ss", type=int, default=2, help="超采样倍数(抗锯齿)")
    ap.add_argument("--elev", type=float, default=ELEV)
    ap.add_argument("--azim", type=float, default=AZIM)
    args = ap.parse_args(argv)

    glb_dir = Path(args.glb_dir)
    ids = [t for t in C.CATALOG if (glb_dir / f"{t}.glb").exists()]
    missing = [t for t in C.CATALOG if t not in ids]
    if missing:
        print(f"[warn] 缺少 {len(missing)} 个 GLB: {missing}（先跑 export.py）", file=sys.stderr)
    if not ids:
        print("[error] 没有 GLB 可渲染", file=sys.stderr)
        return 1

    cam, right, up = _basis(args.elev, args.azim)
    lab_h = 74
    tw, th = args.tile, int(args.tile * 0.86)
    cw, ch = tw, th + lab_h
    cols = args.cols
    rows = (len(ids) + cols - 1) // cols
    head, foot = 92, 40

    f_title = font(FONT_BOLD, 24)
    f_sub = font(FONT_REG, 15)
    f_sub2 = font(FONT_REG, 13)
    f_head = font(FONT_BOLD, 34)
    f_head2 = font(FONT_REG, 17)
    f_foot = font(FONT_REG, 15)
    f_tiny = font(FONT_REG, 14)

    W = cw * cols
    H = head + ch * rows + foot
    sheet = Image.new("RGB", (W, H), (243, 241, 237))
    d0 = ImageDraw.Draw(sheet)
    d0.text((W // 2, 24), "HK Space AI ・ 30 個參數化傢俬模板總覽 / 30 parametric furniture templates",
            font=f_head, fill=INK, anchor="mm")
    d0.text((W // 2, 62), "正交等軸測投影（elev 22° / azim 58°，看正面 +Y）・ 單位 mm ・ Z 向上 ・ 原點=佔地中心 ・ "
                          "門縫 3mm ・ 統一 1 米比例尺 ・ 地面陰影=真實佔地投影",
            font=f_head2, fill=INK2, anchor="mm")

    t0 = time.time()
    for i, tid in enumerate(ids):
        spec = C.CATALOG[tid]
        col, row = i % cols, i // cols
        cx0, cy0 = col * cw, head + row * ch
        try:
            tris, cols8 = load_parts(glb_dir / f"{tid}.glb")
            lo = tris.reshape(-1, 3).min(axis=0)
            hi = tris.reshape(-1, 3).max(axis=0)
            et, ec = ground_extras(tris, lo, hi)
            bt, bc, blabel = scale_bar(lo, hi)
            big = rasterize(np.concatenate([et, tris, bt]),
                            np.concatenate([ec, cols8, bc]),
                            tw * args.ss, th * args.ss, cam, right, up)
            tile = Image.fromarray(big).resize((tw, th), Image.LANCZOS)
        except Exception as e:                       # 单格失败不影响整张图
            tile = Image.new("RGB", (tw, th), (250, 240, 240))
            ImageDraw.Draw(tile).text((10, 10), f"render error: {e}", fill=(160, 0, 0), font=f_tiny)
            blabel = None
        sheet.paste(tile, (cx0, cy0 + lab_h))

        sc = trimesh.load(glb_dir / f"{tid}.glb", force="scene")
        tri_n = sum(len(g.faces) for g in sc.geometry.values())
        dims = [int(round(v)) for v in (sc.bounds[1] - sc.bounds[0]) * 1000]
        mats = sorted({getattr(g.visual.material, "name", "?") for g in sc.geometry.values()})
        d0.text((cx0 + cw // 2, cy0 + 22), f"{spec.name_zh}  {spec.name_en}",
                font=f_title, fill=INK, anchor="mm")
        d0.text((cx0 + cw // 2, cy0 + 45),
                f"{dims[0]}×{dims[1]}×{dims[2]} mm ・ {tri_n} tri ・ {len(sc.geometry)} 件",
                font=f_sub, fill=INK2, anchor="mm")
        d0.text((cx0 + cw // 2, cy0 + 64),
                f"{spec.group} ・ " + "/".join(m[:14] for m in mats[:3]),
                font=f_sub2, fill=(140, 137, 133), anchor="mm")

    d0.text((W // 2, H - 22),
            "variant（同一模板換參數，用 export.py --variants 匯出）："
            + " ・ ".join(f"{v['name_zh']} {v['name_en']}" for v in C.VARIANTS.values()),
            font=f_foot, fill=INK2, anchor="mm")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"{len(ids)}/{len(C.CATALOG)} 个模板 -> {out}  ({out.stat().st_size/1024:.0f} KB, "
          f"{cols}x{rows} 格, {sheet.size[0]}x{sheet.size[1]}px, {time.time()-t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
