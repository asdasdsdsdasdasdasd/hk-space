"""把两房参考方案写成 JSON。几何在 hkgeometry.onpak05_reference。"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.onpak05_reference import build_reference  # noqa: E402


def main() -> None:
    doc = build_reference()
    out = os.path.join(ROOT, "out", "plan_hk_onpak05_reference.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    recon = doc["meta"]["reconciliation"]
    print(recon["status"], "delta_mm", recon["delta_mm"])
    for err in recon["errors"]:
        print(" ", err)
    for room in doc["rooms"]:
        print("  %-12s %6.2f m2" % (room["id"], room["area_mm2"] / 1e6))
    print("->", out)


if __name__ == "__main__":
    main()
