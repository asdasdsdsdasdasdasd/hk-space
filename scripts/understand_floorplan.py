"""理解一张向量 PDF 或干净的户型图。

    python scripts/understand_floorplan.py --source plan.pdf --page 23 --unit B36 --out out/plan.json --overlay out/overlay.png

没有 --live 时只跑几何。--live 才调用 DeepSeek，密钥只从环境变量读。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "floorplan"))
sys.path.insert(0, str(ROOT / "packages" / "geometry"))

from understand import understand_floorplan


def main():
    parser = argparse.ArgumentParser(description="理解户型图，几何不过关就标 unverified")
    parser.add_argument("--source", required=True)
    parser.add_argument("--page", type=int, default=None)
    parser.add_argument("--unit", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--overlay", default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=3)
    args = parser.parse_args()
    result = understand_floorplan(
        args.source, args.out, page=args.page, unit_hint=args.unit,
        max_rounds=args.max_rounds, overlay_path=args.overlay, live=args.live)
    audit = result["audit"]
    print("status %s" % result["status"])
    print("hypothesis %s" % audit["hypothesis"])
    print("rooms %s" % ", ".join("%s %s" % (room["id"], room.get("label", "")) for room in result["plan"]["rooms"]))
    if audit["failures"]:
        print("failures")
        for item in audit["failures"]:
            print("  %s" % item)
    print("audit %s" % result["audit_path"])
    print("overlay %s" % (result["plan"]["meta"].get("overlay") or ""))


if __name__ == "__main__":
    main()
