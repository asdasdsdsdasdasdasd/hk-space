"""把候选图画出来：编号、正面箭头、使用区、失败原因。"""
from __future__ import annotations

from io import BytesIO


def render_annotated(flat, house=None, errors=None) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .circulation import approach_zone
    from .greedy import door_approach_zones

    fig, ax = plt.subplots(figsize=(8, 8))
    xs, ys = [], []
    for room in flat.rooms:
        ring_x, ring_y = room.geo().exterior.xy
        xs.extend(ring_x)
        ys.extend(ring_y)
        ax.fill(ring_x, ring_y, color="#f4f1ea", zorder=0)
        ax.plot(ring_x, ring_y, color="#222", lw=1.2, zorder=2)
        for _door, zone in door_approach_zones(room):
            if zone.is_empty:
                continue
            zx, zy = zone.exterior.xy
            ax.fill(zx, zy, color="#f6c7c7", alpha=0.7, zorder=1)
    if house is not None:
        for lay in house.by_room.values():
            for item in lay.placements:
                ring_x, ring_y = item.footprint().exterior.xy
                ax.fill(ring_x, ring_y, color="#8fb8d6", alpha=0.85, zorder=3)
                cx, cy = item.center()
                fx, fy = item.front_dir()
                ax.annotate(
                    "", xy=(cx + fx * 280, cy + fy * 280), xytext=(cx, cy),
                    arrowprops=dict(arrowstyle="-|>", color="#1d3557", lw=1.0),
                    zorder=4)
                ax.text(cx, cy, item.inst, ha="center", va="center", fontsize=6, zorder=5)
                zone = approach_zone(item)
                if zone is not None and not zone.is_empty:
                    zx, zy = zone.exterior.xy
                    ax.plot(zx, zy, color="#2a9d8f", ls="--", lw=0.7, zorder=3)
    if xs and ys:
        pad = 400
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_aspect("equal")
    failed = list(errors or [])
    ax.set_title("NOT PUBLISHABLE\n" + "; ".join(failed[:3]) if failed else "verified candidate",
                 fontsize=9)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=80, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
