"""结构有问题时画出给人看的对照，不直接摆家具。"""
from __future__ import annotations

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.house import flat_from_dict  # noqa: E402
from hkgeometry.onpak05_reference import build_reference  # noqa: E402
from hkgeometry.structure import review_status, validate_structure  # noqa: E402
from hkgeometry.viz import setup_font  # noqa: E402


def save_review(path: str) -> list[str]:
    setup_font()
    flat = flat_from_dict(build_reference())
    errors = validate_structure(flat)
    status = review_status(errors)
    fig, ax = plt.subplots(figsize=(12, 9))
    shell = (flat.meta or {}).get("outline") or []
    if len(shell) >= 3:
        ax.add_patch(MplPolygon(shell, closed=True, facecolor="#f7f7f4",
                                edgecolor="#111", lw=2.0, zorder=0))
    for room in flat.rooms:
        ax.add_patch(MplPolygon(list(room.geo().exterior.coords), closed=True,
                                facecolor="#d9ead3" if room.id == "kitchen" else "#fff",
                                edgecolor="#333", lw=0.8, alpha=0.7, zorder=1))
        c = room.geo().representative_point()
        ax.text(c.x, c.y, "%s\n%.1f m²" % (room.label or room.id, room.geo().area / 1e6),
                ha="center", va="center", fontsize=9, zorder=4)
        for op in room.openings:
            if op.kind != "door":
                continue
            wall = next((w for w in room.walls if w.id == op.wall), None)
            if wall is None:
                continue
            ax.plot([wall.a[0], wall.b[0]], [wall.a[1], wall.b[1]], color="#c0392b", lw=2.2, zorder=3)
    xs, ys = [], []
    for room in flat.rooms:
        minx, miny, maxx, maxy = room.geo().bounds
        xs += [minx, maxx]
        ys += [miny, maxy]
    pad = 400
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    title = "structure %s" % status
    if errors:
        title += " — " + "; ".join(errors[:3])
    ax.set_title(title)
    fig.savefig(path, dpi=110, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(status)
    for err in errors:
        print(" ", err)
    print("->", path)
    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "onpak05_structure_review.png"))
    args = ap.parse_args()
    save_review(args.out)


if __name__ == "__main__":
    main()
