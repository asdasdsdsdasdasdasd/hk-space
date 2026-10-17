"""画出安柏苑参考方案的房间门和入口能走到的范围。"""
from __future__ import annotations

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import Point

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.circulation import _Grid, portal_shapes  # noqa: E402
from hkgeometry.house import flat_from_dict  # noqa: E402
from hkgeometry.onpak05_reference import build_reference  # noqa: E402
from hkgeometry.progressive import solve_progressive  # noqa: E402
from hkgeometry.types import Placement  # noqa: E402
from hkgeometry.viz import setup_font  # noqa: E402


def save_audit(path: str) -> None:
    setup_font()
    doc = build_reference()
    flat = flat_from_dict(doc)
    items, assign = [], {}
    for row in doc["meta"]["program"]["furniture"]:
        items.append(Placement(inst=row["inst"], sku="ref", category=row["category"],
                               w=row["w"], d=row["d"], h=row["h"]))
        assign[row["inst"]] = row["room"]
    house = solve_progressive(flat, items, assign)
    placed = {rid: lay.placements for rid, lay in house.by_room.items()}
    grid = _Grid(flat, placed, person_mm=600)
    seen = grid.reachable_from(flat.entry_point)

    fig, ax = plt.subplots(figsize=(12, 9))
    colors = {"living": "#fbf6ea", "bedroom": "#f6eef6", "kitchen": "#eef7f0", "bath": "#eaf2fa"}
    from hkgeometry.house import infer_room_functions
    funcs = infer_room_functions(flat)
    for room in flat.rooms:
        xy = list(room.geo().exterior.coords)
        ax.add_patch(MplPolygon(xy, closed=True, facecolor=colors.get(funcs.get(room.id), "#f7f7f4"),
                                edgecolor="#222", lw=1.2, zorder=1))
        for wall in room.walls:
            if wall.movable:
                ax.plot([wall.a[0], wall.b[0]], [wall.a[1], wall.b[1]],
                        color="#c0392b", lw=2.4, zorder=3)
    for portal in portal_shapes(flat):
        ax.add_patch(MplPolygon(list(portal.exterior.coords), closed=True,
                                facecolor="#f6c945", edgecolor="none", alpha=0.85, zorder=2))
    xs, ys = [], []
    for ix, iy in seen:
        x, y = grid._point(ix, iy)
        if grid.label(ix, iy) is not None:
            xs.append(x)
            ys.append(y)
    if xs:
        ax.scatter(xs, ys, s=2, c="#2a7ae2", alpha=0.35, zorder=2, label="reachable from entry")
    ax.scatter([flat.entry_point[0]], [flat.entry_point[1]], c="#c0392b", s=30, zorder=4, label="entry")
    for room in flat.rooms:
        c = room.geo().representative_point()
        ax.text(c.x, c.y, room.label or room.id, ha="center", va="center", fontsize=9, zorder=5)
    for plist in placed.values():
        for item in plist:
            fp = item.footprint()
            ax.add_patch(MplPolygon(list(fp.exterior.coords), closed=True,
                                    facecolor="#d9cfc0", edgecolor="#1a1a1a", lw=0.6, zorder=3))
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("onpak05 reference — red = new partition, gold = door, blue = reachable")
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=120, facecolor="white")
    plt.close(fig)
    print("->", path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "plan2d_onpak05_routes.png"))
    args = ap.parse_args()
    save_audit(args.out)


if __name__ == "__main__":
    main()
