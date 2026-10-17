"""在已理解的青富 B36 上摆家具，并出 2D / 3D。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from hkgeometry.annotate import render_annotated
from hkgeometry.house import flat_from_dict, infer_room_functions
from hkgeometry.layout_loop import design_from_floorplan, hard_errors, scrub_audit
from hkgeometry.types import Placement
from hkgeometry.vision_roles import FixtureProvider, LayoutCritic

OUT = os.path.join(ROOT, "out")
SOURCE = os.path.join(OUT, "plan_hk_chingfu_b36_understood.json")
PLAN = os.path.join(OUT, "plan_hk_chingfu_b36.json")
LAYOUT = os.path.join(OUT, "layout_chingfu_b36.json")
AUDIT = os.path.join(OUT, "chingfu_b36_design_audit.json")
PLAN2D = os.path.join(OUT, "plan2d_chingfu_b36.png")
ANNOTATED = os.path.join(OUT, "plan2d_chingfu_b36_annotated.png")
DOLLHOUSE = os.path.join(OUT, "dollhouse_chingfu_b36.png")
CATALOG = os.path.join(ROOT, "data", "catalog", "seed_skus.json")
FIXTURES = os.path.join(ROOT, "data", "catalog", "fixtures.json")


def _catalog() -> list[dict]:
    rows = json.load(open(CATALOG, encoding="utf-8"))["skus"]
    if os.path.exists(FIXTURES):
        rows = rows + json.load(open(FIXTURES, encoding="utf-8"))["skus"]
    return rows


def _pick(cat, category, mw, md):
    cands = []
    for sku in cat:
        if sku["category"] != category:
            continue
        w, d = sku["dims"]["w"], sku["dims"]["d"]
        if (w <= mw and d <= md) or (w <= md and d <= mw):
            cands.append(sku)
    if not cands:
        return None
    cands.sort(key=lambda s: s["dims"]["w"] * s["dims"]["d"], reverse=True)
    return cands[0]


def _items(program, cat):
    items, assign = [], {}
    for row in program:
        sku = _pick(cat, row["category"], row["w"], row["d"])
        if sku is None:
            items.append(Placement(inst=row["inst"], sku="ref", category=row["category"],
                                   w=row["w"], d=row["d"], h=row["h"]))
        else:
            items.append(Placement(
                inst=row["inst"], sku=sku["id"], category=sku["category"],
                w=sku["dims"]["w"], d=sku["dims"]["d"], h=sku["dims"]["h"],
                visual=dict(sku.get("visual") or {}), price_hkd=sku.get("price_hkd", 0)))
        assign[row["inst"]] = row["room"]
    return items, assign


def _layout_json(flat, house) -> dict:
    funcs = infer_room_functions(flat)
    lay = {}
    for rid, layout in house.by_room.items():
        lay[rid] = {
            "func": funcs.get(rid, rid),
            "placements": [dict(inst=p.inst, sku=p.sku, category=p.category,
                                x=p.x, y=p.y, rot=p.rot, w=p.w, d=p.d, h=p.h,
                                price=p.price_hkd)
                           for p in layout.placements],
        }
    return lay


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    source = json.load(open(SOURCE, encoding="utf-8"))
    critic = LayoutCritic(FixtureProvider({"severe": [], "problems": [], "actions": []}))
    doc, house, report = design_from_floorplan(source, critic=critic, max_rounds=2)
    flat = flat_from_dict(doc)
    audit = scrub_audit(report)
    json.dump(audit, open(AUDIT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    errors = hard_errors(flat, house) if house is not None else ["no candidate"]
    with open(ANNOTATED, "wb") as f:
        f.write(render_annotated(flat, house, [] if report.get("publishable") else errors))
    print("publishable:", bool(report.get("publishable")))
    print("hard:", "; ".join(errors) if errors else "none")
    print("unplaced:", ", ".join(house.unplaced) if house and house.unplaced else "none")
    if house is None:
        return 1
    json.dump(_layout_json(flat, house), open(LAYOUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(doc, open(PLAN, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    subprocess.check_call([
        sys.executable, os.path.join(ROOT, "scripts", "render_plan2d.py"),
        "--plan", PLAN, "--layout", LAYOUT, "--out", PLAN2D,
        "--title", "Ching Fu B36",
    ], cwd=ROOT)
    print("2d", PLAN2D)
    subprocess.check_call([
        sys.executable, os.path.join(ROOT, "scripts", "render_house.py"),
        "--plan", PLAN, "--layout", LAYOUT, "--out", DOLLHOUSE, "--tag", "chingfu_b36",
    ], cwd=ROOT)
    print("3d", DOLLHOUSE)
    return 0 if report.get("publishable") else 0


if __name__ == "__main__":
    sys.exit(main())
