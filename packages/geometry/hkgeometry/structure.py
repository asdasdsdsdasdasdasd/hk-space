"""结构是否成立。家具求解之前先过这里。"""
from __future__ import annotations

from shapely.geometry import Point, Polygon

from .program import opening_side


def _shell(flat) -> Polygon | None:
    ring = (flat.meta or {}).get("outline") or []
    if len(ring) < 3:
        return None
    return Polygon([(int(p[0]), int(p[1])) for p in ring])


def validate_structure(flat) -> list[str]:
    """房间必须在壳内，门必须连上两间房。不通过就不要摆家具。"""
    errors = []
    shell = _shell(flat)
    ids = {r.id for r in flat.rooms}
    if shell is not None:
        cover = shell.buffer(120)
        for room in flat.rooms:
            area = room.geo().area
            if area <= 1:
                errors.append("%s has no area" % room.id)
                continue
            inside = cover.intersection(room.geo()).area / area
            if inside < 0.98:
                errors.append("%s falls outside the shell (cover %.2f)" % (room.id, inside))
            if cover.covers(room.geo().representative_point()) is False:
                errors.append("%s centroid is outside the shell" % room.id)
    openings = {}
    for room in flat.rooms:
        walls = {w.id: w for w in room.walls}
        for op in room.openings:
            openings.setdefault(op.id, []).append((room, op))
            wall = walls.get(op.wall)
            if wall is None:
                errors.append("opening %s is not on a wall of %s" % (op.id, room.id))
                continue
            if op.kind == "door" and op.center_mm < 0:
                errors.append("door %s has a negative center" % op.id)
    spec = (flat.meta or {}).get("program") or {}
    for link in spec.get("required_links") or []:
        a, b = link[0], link[1]
        if a not in ids or b not in ids:
            errors.append("link %s-%s names a missing room" % (a, b))
    for conn in flat.connections:
        if conn.a not in ids or conn.b not in ids:
            errors.append("connection %s-%s leaves the plan" % (conn.a, conn.b))
            continue
        if conn.door_id and conn.door_id not in openings:
            errors.append("connection %s has no door %s" % (conn.a, conn.door_id))
        portal_ok = _portal_joins(flat, conn.a, conn.b)
        if not portal_ok:
            errors.append("door between %s and %s does not meet both rooms" % (conn.a, conn.b))
    for door_id, want in (spec.get("door_sides") or {}).items():
        room = next((r for r in flat.rooms if r.id == want["room"]), None)
        if room is None or not any(o.id == door_id for o in room.openings):
            errors.append("missing %s on %s" % (door_id, want["room"]))
            continue
        side = opening_side(room, door_id)
        if side != want["side"]:
            errors.append("%s is on the %s side of %s, expected %s" % (
                door_id, side, want["room"], want["side"]))
    if (flat.meta or {}).get("source") == "floorplan_understanding":
        errors.extend(_upload_errors(flat))
    return errors


def _upload_errors(flat) -> list[str]:
    """上传图比手写样例多几道关：比例尺、墙厚、门要落在墙上、入口要走得通。"""
    errors = []
    dims = [item for item in (flat.meta or {}).get("dimensions") or []
            if isinstance(item, dict) and item.get("status") == "observed"]
    scales = [float(item["mm_per_unit"]) for item in dims if item.get("mm_per_unit")]
    if len(scales) < 2:
        errors.append("fewer than two observed dimensions")
    else:
        median = sorted(scales)[len(scales) // 2]
        if any(abs(scale - median) / median > 0.08 for scale in scales):
            errors.append("dimension scales disagree")
    for room in flat.rooms:
        for wall in room.walls:
            if not (40 <= int(wall.thickness_mm) <= 400):
                errors.append("%s thickness is implausible" % wall.id)
            length = ((wall.b[0] - wall.a[0]) ** 2 + (wall.b[1] - wall.a[1]) ** 2) ** 0.5
            for op in room.openings:
                if op.wall != wall.id:
                    continue
                if op.center_mm < 0 or op.center_mm > length + 1:
                    errors.append("opening %s is outside its wall" % op.id)
                if not (400 <= op.width_mm <= 3200):
                    errors.append("opening %s width is implausible" % op.id)
    if not _entry_reaches_every_room(flat):
        errors.append("entry does not reach every room")
    return errors


def _entry_reaches_every_room(flat) -> bool:
    if not flat.rooms:
        return False
    seen = {flat.entry_room}
    changed = True
    while changed:
        changed = False
        for conn in flat.connections:
            if conn.a in seen and conn.b not in seen:
                seen.add(conn.b)
                changed = True
            elif conn.b in seen and conn.a not in seen:
                seen.add(conn.a)
                changed = True
    return seen == {room.id for room in flat.rooms}


def _portal_joins(flat, a: str, b: str) -> bool:
    ra = flat.room(a).geo()
    rb = flat.room(b).geo()
    portals = []
    for ring in (flat.meta or {}).get("portals") or []:
        if len(ring) >= 3:
            portals.append(Polygon([(int(p[0]), int(p[1])) for p in ring]))
    if not portals:
        return ra.distance(rb) < 500
    for portal in portals:
        if portal.intersects(ra.buffer(40)) and portal.intersects(rb.buffer(40)):
            return True
    return ra.distance(rb) < 80


def review_status(errors: list[str]) -> str:
    """低置信或未闭合的结构必须等人确认，不能直接摆家具。"""
    if not errors:
        return "verified"
    if any("outside" in e or "does not meet" in e or "missing" in e for e in errors):
        return "needs_human"
    return "rejected"
