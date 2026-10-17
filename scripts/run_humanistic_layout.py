"""用人看图的标准重解安柏苑。默认不联网；--live 才调用 DeepSeek。

密钥只从 DEEPSEEK_API_KEY 或 HKSPACE_LLM_KEY 读取，不写进仓库、日志或审计文件。
几何没通过，或者批评还有严重问题，就不覆盖正式平面图。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.annotate import render_annotated  # noqa: E402
from hkgeometry.house import flat_from_dict, infer_room_functions  # noqa: E402
from hkgeometry.layout_loop import hard_errors, run_layout_loop, scrub_audit  # noqa: E402
from hkgeometry.onpak05_reference import build_reference  # noqa: E402
from hkgeometry.types import Placement  # noqa: E402
from hkgeometry.vision_roles import DeepSeekVisionProvider, FixtureProvider, LayoutCritic  # noqa: E402


def _items(doc):
    items, assign = [], {}
    for row in doc["meta"]["program"]["furniture"]:
        items.append(Placement(inst=row["inst"], sku="ref", category=row["category"],
                               w=row["w"], d=row["d"], h=row["h"]))
        assign[row["inst"]] = row["room"]
    return items, assign


def _layout_json(flat, house) -> dict:
    funcs = infer_room_functions(flat)
    lay = {}
    for rid, layout in house.by_room.items():
        lay[rid] = {
            "func": funcs.get(rid, rid),
            "placements": [dict(inst=p.inst, sku=p.sku, category=p.category,
                                x=p.x, y=p.y, rot=p.rot, w=p.w, d=p.d, h=p.h)
                           for p in layout.placements],
        }
    return lay


def main() -> int:
    live = "--live" in sys.argv
    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)
    if live and not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("HKSPACE_LLM_KEY")):
        print("Set DEEPSEEK_API_KEY or HKSPACE_LLM_KEY before --live. The key is not stored.")
        return 2
    doc = build_reference()
    flat = flat_from_dict(doc)
    items, assign = _items(doc)
    if live:
        critic = LayoutCritic(DeepSeekVisionProvider())
    else:
        critic = LayoutCritic(FixtureProvider({"severe": [], "problems": [], "actions": []}))
    house, report = run_layout_loop(flat, items, assign, critic=critic, max_rounds=2, per_round=2)
    audit = scrub_audit(report)
    blob = json.dumps(audit, ensure_ascii=False)
    for name in ("DEEPSEEK_API_KEY", "HKSPACE_LLM_KEY"):
        secret = os.environ.get(name)
        if secret and secret in blob:
            print("Refusing to write an audit that contains the API key.")
            return 3
    audit_path = os.path.join(out_dir, "onpak05_humanistic_audit.json")
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, ensure_ascii=False, indent=1)
    errors = hard_errors(flat, house) if house is not None else ["no candidate"]
    annotated = os.path.join(out_dir, "plan2d_onpak05_annotated.png")
    with open(annotated, "wb") as f:
        f.write(render_annotated(flat, house, [] if report.get("publishable") else errors))
    print("publishable:", bool(report.get("publishable")))
    print("hard:", "; ".join(errors) if errors else "none")
    print("audit:", audit_path)
    if not report.get("publishable") or house is None:
        print("Formal plan was not replaced.")
        return 0
    plan_path = os.path.join(out_dir, "plan_hk_onpak05_reference.json")
    lay_path = os.path.join(out_dir, "layout_onpak05.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    with open(lay_path, "w", encoding="utf-8") as f:
        json.dump(_layout_json(flat, house), f, ensure_ascii=False, indent=1)
    engine = os.path.join(out_dir, "plan2d_onpak05_engine.png")
    subprocess.check_call([
        sys.executable, os.path.join(ROOT, "scripts", "render_plan2d.py"),
        "--plan", plan_path, "--layout", lay_path, "--out", engine,
        "--title", "On Pak 05 verified layout",
    ], cwd=ROOT)
    print("wrote", engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
