"""比较只靠算法、只靠视觉模型和两者一起的户型理解。

视觉模型就算返回了坐标也不会被写进图里。密钥不会出现在报告里。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "floorplan"))
sys.path.insert(0, str(ROOT / "packages" / "geometry"))

from hkgeometry.vision_roles import FixtureProvider
from understand import understand_floorplan

BROCHURE = ROOT / "out" / "ref" / "Brochure-Ching-Fu.pdf"
OUT = ROOT / "out" / "floorplan_understanding_bench.json"


def _run(name, provider):
    out = ROOT / "out" / ("understand_%s.json" % name)
    started = time.perf_counter()
    result = understand_floorplan(
        BROCHURE, out, page=23, unit_hint="B36", provider=provider, max_rounds=2)
    elapsed = time.perf_counter() - started
    plan = result["plan"]
    overlay = result["audit"].get("overlay_metrics") or {}
    return dict(
        mode=name,
        status=result["status"],
        seconds=round(elapsed, 2),
        api_cost=0,
        rooms=len(plan["rooms"]),
        living_agreement=overlay.get("living_agreement"),
        scale_mm_per_unit=plan["meta"].get("mm_per_px"),
        signature=result["audit"]["signature"],
        failures=result["audit"]["failures"],
    )


def _perturbations():
    """旋转和 JPEG 之后，栅格回退必须还能返回状态，不能把坐标交给模型。"""
    import cv2
    render = ROOT / "out" / "understand_algorithm_render.png"
    if not render.exists():
        return []
    image = cv2.imread(str(render), cv2.IMREAD_COLOR)
    rows = []
    rotated = ROOT / "out" / "understand_rotated.png"
    jpeg = ROOT / "out" / "understand_jpeg.jpg"
    cv2.imwrite(str(rotated), cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE))
    cv2.imwrite(str(jpeg), image, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    for name, path in (("rotated", rotated), ("jpeg", jpeg)):
        out = ROOT / "out" / ("understand_%s.json" % name)
        started = time.perf_counter()
        try:
            result = understand_floorplan(path, out, max_rounds=1)
            rows.append(dict(mode=name, status=result["status"], seconds=round(time.perf_counter() - started, 2),
                             rooms=len(result["plan"]["rooms"])))
        except Exception as exc:
            rows.append(dict(mode=name, status="error", seconds=round(time.perf_counter() - started, 2),
                             rooms=0, error=type(exc).__name__))
    return rows


def main():
    if not BROCHURE.exists():
        raise SystemExit("missing %s" % BROCHURE)
    algorithm = _run("algorithm", None)
    hybrid = _run("hybrid", FixtureProvider({
        "select_hypothesis": "wall-cells",
        "rooms": [{"label": "客/飯廳"}, {"label": "浴室"}, {"label": "廚房"}],
        "adjacency": [["living", "bath"]],
        "entry_room": "living",
    }))
    vlm = _run("vlm", FixtureProvider({
        "select_hypothesis": "not-a-real-hypothesis",
        "polygon": [[0, 0], [100, 0], [100, 100]],
        "scale": 1,
        "rooms": [{"label": "浴室", "x": 1, "y": 2}],
    }))
    report = dict(
        cases=[algorithm, hybrid, vlm],
        geometry_unchanged=algorithm["signature"] == hybrid["signature"] == vlm["signature"],
        perturbations=_perturbations(),
        note="Direct VLM coordinates are discarded. Verification still requires the geometry gates.",
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("mode        status      rooms  fill   seconds")
    for row in report["cases"]:
        print("%-11s %-11s %5d  %s  %6.2f" % (
            row["mode"], row["status"], row["rooms"], row["living_agreement"], row["seconds"]))
    print("geometry unchanged across modes: %s" % report["geometry_unchanged"])
    for row in report["perturbations"]:
        print("perturb %-8s %s  rooms=%s  %.2fs" % (row["mode"], row["status"], row["rooms"], row["seconds"]))
    print("report %s" % OUT)


if __name__ == "__main__":
    main()
