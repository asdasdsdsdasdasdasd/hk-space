"""设计任务书。求解器先要满足它，才有资格谈显大分。"""
from __future__ import annotations

from .house import infer_room_functions


def default_program_for_flat(doc: dict) -> dict:
    """设计阶段的任务书。没有睡房的壳也会被要求至少一间。"""
    rooms = doc.get("rooms") or []
    ids = [room.get("id") for room in rooms]
    bedrooms = [rid for rid in ids if str(rid).startswith("bedroom")]
    need = max(1, len(bedrooms)) if any(str(rid).startswith("bedroom") for rid in ids) else 1
    if bedrooms:
        need = len(bedrooms)
    links = []
    for other in ("bath", "kitchen"):
        if other in ids and "living" in ids:
            links.append(["living", other])
    for rid in bedrooms:
        links.append(["living", rid])
    furniture = [
        ("#sofa", "sofa", 1800, 800, 750, "living"),
        ("#tv", "tv_bench", 1400, 400, 450, "living"),
        ("#coffee", "coffee_table", 800, 800, 400, "living"),
        ("#dining", "dining_table", 1400, 800, 750, "living"),
        ("#chair1", "chair", 450, 450, 780, "living"),
        ("#chair2", "chair", 450, 450, 780, "living"),
        ("#chair3", "chair", 450, 450, 780, "living"),
        ("#chair4", "chair", 450, 450, 780, "living"),
        ("#toilet", "toilet", 620, 380, 750, "bath"),
        ("#basin", "basin", 520, 400, 850, "bath"),
        ("#shower", "shower", 700, 700, 2000, "bath"),
        ("#counter", "kitchen_counter", 1400, 550, 850, "kitchen"),
        ("#fridge", "fridge", 550, 550, 1700, "kitchen"),
        ("#washer", "washing_machine", 550, 550, 850, "kitchen"),
    ]
    if "bedroom_w" in ids or not bedrooms:
        furniture += [
            ("#bed_w", "bed", 1400, 1900, 500, "bedroom_w"),
            ("#wardrobe_w", "wardrobe", 1200, 500, 2000, "bedroom_w"),
        ]
    if "bedroom_e" in ids:
        furniture.append(("#bed_e", "bed", 900, 1900, 500, "bedroom_e"))
    kept = []
    for inst, category, w, d, h, room in furniture:
        if room in ids:
            kept.append(dict(inst=inst, category=category, w=w, d=d, h=h, room=room))
    return {
        "min_counts": {"living": 1, "bath": 1, "kitchen": 1, "bedroom": need},
        "min_area_m2": {"bedroom": 4.5, "living": 7.0, "kitchen": 2.0, "bath": 2.0},
        "required_links": links,
        "door_sides": {},
        "furniture": kept,
        "artery_mm": 900,
        "terminal_mm": 600,
    }


def validate_program(flat) -> list[str]:
    spec = (flat.meta or {}).get("program") or {}
    if not spec:
        return []
    errors = []
    funcs = infer_room_functions(flat)
    counts: dict[str, int] = {}
    for func in funcs.values():
        counts[func] = counts.get(func, 0) + 1
    for func, need in (spec.get("min_counts") or {}).items():
        got = counts.get(func, 0)
        if got < int(need):
            errors.append("need %d %s room(s), have %d" % (need, func, got))
    by_id = {r.id: r for r in flat.rooms}
    min_area = spec.get("min_area_m2") or {}
    for room in flat.rooms:
        func = funcs.get(room.id, "")
        floor = float(min_area.get(func, 0) or 0)
        area = room.geo().area / 1e6
        if floor and area + 1e-6 < floor:
            errors.append("%s area %.2fm2 is below %.2fm2" % (room.id, area, floor))
    links = set()
    for conn in flat.connections:
        links.add((conn.a, conn.b))
        links.add((conn.b, conn.a))
    for pair in spec.get("required_links") or []:
        a, b = pair[0], pair[1]
        if (a, b) not in links:
            errors.append("missing door between %s and %s" % (a, b))
        if a not in by_id or b not in by_id:
            errors.append("link names unknown room %s-%s" % (a, b))
    openings = {}
    for room in flat.rooms:
        for op in room.openings:
            openings.setdefault(op.id, []).append(op)
    for door in spec.get("doors") or []:
        found = openings.get(door["id"]) or []
        if not found:
            errors.append("missing door %s" % door["id"])
            continue
        want = door.get("swing") or {}
        if any((op.swing or {}).get("dir") != want.get("dir") or
               (op.swing or {}).get("hinge") != want.get("hinge") for op in found):
            errors.append("door %s lost its hinge/swing" % door["id"])
    recon = (flat.meta or {}).get("reconciliation") or {}
    if recon and abs(float(recon.get("scale", 1.0)) - 1.0) > 1e-9:
        errors.append("coordinates were scaled onto the reference drawing")
    return errors


def opening_side(room, opening_id: str) -> str:
    """门装在房间的哪一侧。x 不受图纸翻 y 影响，东/西仍然是东/西。"""
    op = next(o for o in room.openings if o.id == opening_id)
    wall = next(w for w in room.walls if w.id == op.wall)
    xs = [p[0] for p in room.polygon]
    ys = [p[1] for p in room.polygon]
    vertical = abs(wall.a[0] - wall.b[0]) <= abs(wall.a[1] - wall.b[1])
    mx = (wall.a[0] + wall.b[0]) / 2.0
    my = (wall.a[1] + wall.b[1]) / 2.0
    if vertical:
        return "east" if mx >= (min(xs) + max(xs)) / 2.0 else "west"
    return "north" if my >= (min(ys) + max(ys)) / 2.0 else "south"
