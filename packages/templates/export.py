#!/usr/bin/env python3
"""
hk-space :: packages/templates/export.py
=================================================================
把 catalog 里 30 个模板批量导出成 GLB。

    python3 packages/templates/export.py                 # 30 个 .glb -> out/templates/
    python3 packages/templates/export.py --variants      # 额外导出 3 个 variant
    python3 packages/templates/export.py --jobs 1        # 单进程（调试用）
    python3 packages/templates/export.py --out /tmp/xx   # 换个输出目录

导出后每个文件都会用 trimesh.load() **重新读回来**校验顶点数 > 0，
并写一份 manifest.json（面数/尺寸/材质/节点名），供 App 和布局引擎查。

坐标：Z 向上，原点在占地矩形中心，+Y 是正面。单位：文件里是米（glTF 规范），
对外接口一律 mm。trimesh 会自动做 mm->m 和 Z-up 的处理，load 回来仍是 Z-up。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:                         # 直接当脚本跑 / 被当成包 import，两种都要能用
    from . import catalog as C          # noqa: E402
    from . import templates as T        # noqa: E402
except ImportError:
    import catalog as C                 # noqa: E402
    import templates as T               # noqa: E402

ROOT = HERE.parents[1]                       # .../hk-space
DEFAULT_OUT = ROOT / "out" / "templates"


# ---------------------------------------------------------------------
def _render(job: tuple[str, str, str]) -> dict:
    """子进程：造模型 + 导出 GLB。返回路径给父进程做校验。"""
    kind, tid, out_dir = job
    sc = C.build(tid) if kind == "template" else C.build_variant(tid)
    path = Path(out_dir) / (f"{tid}.glb" if kind == "template" else f"variants/{tid}.glb")
    path.parent.mkdir(parents=True, exist_ok=True)
    sc.export(path)
    return {"kind": kind, "id": tid, "path": str(path)}


def _verify(info: dict) -> dict:
    """父进程：用 trimesh.load 读回来，确认顶点数 > 0。"""
    path = Path(info["path"])
    sc = trimesh.load(path, force="scene")
    geoms = list(sc.geometry.values())
    verts = int(sum(len(g.vertices) for g in geoms))
    tris = int(sum(len(g.faces) for g in geoms))
    if verts <= 0:
        raise RuntimeError(f"{path.name}: reloaded mesh has 0 vertices")
    lo, hi = sc.bounds
    dims = [int(round(float(v))) for v in (hi - lo) * 1000.0]
    mats = sorted({getattr(g.visual.material, "name", None)
                   for g in geoms if getattr(g, "visual", None) is not None} - {None})
    spec = C.CATALOG.get(info["id"])
    return {
        "id": info["id"],
        "kind": info["kind"],
        "path": str(path),
        "name_zh": spec.name_zh if spec else C.VARIANTS[info["id"]]["name_zh"],
        "name_en": spec.name_en if spec else C.VARIANTS[info["id"]]["name_en"],
        "group": spec.group if spec else C.VARIANTS[info["id"]]["group"],
        "file": Path(info["path"]).name,
        "node_names": sorted(sc.geometry.keys()),
        "materials": mats,
        "parts": len(geoms),
        "triangles": tris,
        "vertices": verts,
        "dims_mm": dims,
        "z_min_mm": int(round(float(lo[2]) * 1000.0)),
        "bytes": path.stat().st_size,
    }


def _texture_demo(out_root: Path) -> str | None:
    """顺手验一下「商品主图贴到柜门」这条路真的能跑：造一张假主图，贴到第一扇门上。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGB", (256, 256), (232, 226, 214))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 255, 40], fill=(150, 120, 88))
    for i in range(0, 256, 32):
        d.line([(i, 40), (i, 255)], fill=(206, 196, 180), width=2)
    d.ellipse([96, 116, 160, 180], outline=(120, 100, 80), width=6)
    sc = C.build("cabinet_2door")
    T.apply_texture(sc, "door_0", img, material_key="oak", uv_rect=(0.03, 0.03, 0.97, 0.97))
    path = out_root / "texture_demo.glb"
    path.parent.mkdir(parents=True, exist_ok=True)
    sc.export(path)
    chk = trimesh.load(path, force="scene")
    mat = chk.geometry["door_0"].visual.material
    ok = getattr(mat, "baseColorTexture", None) is not None
    return f"{path}  (baseColorTexture={'yes' if ok else 'NO'})"


# ---------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出 hk-space 家具模板 GLB")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 out/templates/）")
    ap.add_argument("--variants", action="store_true", help="额外导出 VARIANTS（書桌+吊櫃/茶几/地台收納）")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                    help="并行进程数（默认 min(8, CPU)）")
    ap.add_argument("--texture-demo", action="store_true", help="额外跑一遍贴图链路自检")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    jobs = [("template", tid, str(out_root)) for tid in C.CATALOG]
    if args.variants:
        jobs += [("variant", vid, str(out_root)) for vid in C.VARIANTS]

    t0 = time.time()
    n_tpl = len(C.CATALOG)
    results: list[dict] = []
    if args.jobs > 1 and len(jobs) > 1:
        try:
            with ProcessPoolExecutor(max_workers=args.jobs) as ex:
                rendered = list(ex.map(_render, jobs, chunksize=1))
        except Exception as e:                       # 兜底：退回单进程
            print(f"[warn] 并行失败（{e}），退回单进程", file=sys.stderr)
            rendered = [_render(j) for j in jobs]
    else:
        rendered = [_render(j) for j in jobs]

    for info in rendered:
        results.append(_verify(info))
    dt = time.time() - t0

    # ---- 报告 ----
    tpl = [r for r in results if r["kind"] == "template"]
    var = [r for r in results if r["kind"] == "variant"]
    if not args.quiet:
        print(f"{'file':34s} {'zh':12s} {'tri':>7s} {'verts':>7s} {'parts':>5s} "
              f"{'dims_mm':>20s} {'KB':>7s}")
        print("-" * 100)
        for r in tpl + var:
            rel = Path(r["path"]).name if r["kind"] == "template" else f"variants/{Path(r['path']).name}"
            print(f"{rel:34s} {r['name_zh']:12s} {r['triangles']:7d} {r['vertices']:7d} "
                  f"{r['parts']:5d} {str(tuple(r['dims_mm'])):>20s} {r['bytes']/1024:7.1f}")

    tris = [r["triangles"] for r in tpl]
    total_bytes = sum(r["bytes"] for r in results)
    print("-" * 100)
    print(f"模板 {len(tpl)} 个" + (f" + variant {len(var)} 个" if var else "")
          + f"  ->  {out_root}")
    if tpl:
        print(f"面数范围 {min(tris)} ~ {max(tris)} (中位 {int(np.median(tris))})，"
              f"总三角面 {sum(tris)}，总体积 {total_bytes/1024:.0f} KB，耗时 {dt:.1f}s "
              f"({args.jobs} 进程)")

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "units": "mm (int) for the API; glTF file stores meters; Z-up, origin at footprint centre, +Y = front",
        "out_dir": str(out_root),
        "counts": {"templates": len(tpl), "variants": len(var)},
        "triangles": {"min": min(tris) if tris else 0, "max": max(tris) if tris else 0,
                      "sum": sum(tris)},
        "total_bytes": total_bytes,
        "items": tpl + var,
    }
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.quiet:
        print(f"清单 -> {out_root/'manifest.json'}")

    if args.texture_demo:
        msg = _texture_demo(out_root.parent)
        print(f"贴图链路自检 -> {msg}")

    # ---- 退出码：少于 30 个模板算失败 ----
    if len(tpl) != n_tpl or len(tpl) < 30:
        print(f"[error] 期望 {n_tpl} 个模板 GLB，实际 {len(tpl)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
