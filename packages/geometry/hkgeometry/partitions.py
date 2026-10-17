"""隔墙方案先过任务书和通道，再按面积、采光、通道宽度排序。

没有睡房的壳，从客厅里切出隔墙和向内开的门。坐标只落在原图的墙角上。
"""
from __future__ import annotations

import copy
import math

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from .circulation import artery_clear, unreachable_rooms
from .house import flat_from_dict, infer_room_functions
from .onpak05_reference import (
    _as_poly, _attach_openings, _portal, _ring, _walls_bare, build_alternate,
    build_blocked, build_reference,
)
from .program import default_program_for_flat, validate_program

WALL = 100
BED_MIN_MM2 = 4.5e6
SPAN_MIN = 2000


def candidate_documents() -> list[dict]:
    return [build_reference(), build_alternate(), build_blocked()]


def assess(doc: dict) -> dict:
    try:
        flat = flat_from_dict(doc)
    except Exception as exc:
        return {"kind": (doc.get("meta") or {}).get("kind", "?"),
                "ok": False, "errors": ["build failed: %s" % exc], "score": None}
    errors = validate_program(flat)
    lost = unreachable_rooms(flat, {}, person_mm=600)
    if lost:
        errors.append("entry cannot reach " + ", ".join(lost))
    wide = artery_clear(flat, {}, min_width=900)
    if not wide:
        errors.append("main artery is below 900mm or is disconnected")
    funcs = infer_room_functions(flat)
    bed_area = sum(r.geo().area for r in flat.rooms if funcs.get(r.id) == "bedroom") / 1e6
    windows = sum(1 for r in flat.rooms if funcs.get(r.id) == "bedroom"
                  for op in r.openings if op.kind == "window")
    return {
        "kind": (doc.get("meta") or {}).get("kind", doc.get("flat_id", "?")),
        "ok": not errors,
        "errors": errors,
        "score": (round(bed_area, 2), windows, 1 if wide else 0) if not errors else None,
        "flat": flat,
    }


def rank_partition_plans(docs: list[dict] | None = None) -> list[dict]:
    """不可行的方案直接丢掉。剩下的按睡房面积、睡房窗、通道是否够宽排。"""
    ranked = []
    for doc in docs if docs is not None else candidate_documents():
        row = assess(doc)
        if row["ok"]:
            ranked.append(row)
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked


def _room(doc: dict, room_id: str) -> dict | None:
    return next((room for room in doc.get("rooms") or [] if room.get("id") == room_id), None)


def _bedroom_ids(doc: dict) -> list[str]:
    return [room["id"] for room in doc.get("rooms") or [] if str(room.get("id", "")).startswith("bedroom")]


def _poly(room: dict) -> Polygon:
    return Polygon([(int(p[0]), int(p[1])) for p in room["polygon"]])


def _world_openings(room: dict) -> list[dict]:
    walls = {wall["id"]: wall for wall in room.get("walls") or []}
    found = []
    for op in room.get("openings") or []:
        wall = walls.get(op.get("wall"))
        if wall is None:
            continue
        ax, ay = wall["a"]
        bx, by = wall["b"]
        length = max(1.0, math.hypot(bx - ax, by - ay))
        t = float(op.get("center_mm") or 0) / length
        found.append(dict(
            id=op["id"], kind=op.get("kind", "door"),
            width_mm=int(op.get("width_mm") or 800),
            height_mm=int(op.get("height_mm") or 2100),
            sill_h_mm=int(op.get("sill_h_mm") or 0),
            swing=op.get("swing"),
            at=(ax + (bx - ax) * t, ay + (by - ay) * t),
        ))
    return found


def _cluster(values) -> list[int]:
    out = []
    for value in sorted(float(v) for v in values):
        snapped = int(round(value / 50.0) * 50)
        if not out or abs(snapped - out[-1]) > 80:
            out.append(snapped)
    return out


def _snapped_axes(living: Polygon) -> tuple[list[int], list[int]]:
    coords = list(living.exterior.coords)
    return _cluster(p[0] for p in coords), _cluster(p[1] for p in coords)


def _end_caps(living: Polygon) -> list[Polygon]:
    minx, miny, maxx, maxy = living.bounds
    caps = []
    strips = (
        box(minx, miny, minx + 2300, maxy),
        box(maxx - 2300, miny, maxx, maxy),
        box(minx, miny, maxx, miny + 2300),
        box(minx, maxy - 2300, maxx, maxy),
    )
    for rect in strips:
        piece = living.intersection(rect)
        if piece.is_empty or piece.area < BED_MIN_MM2:
            continue
        if piece.geom_type != "Polygon":
            piece = max(piece.geoms, key=lambda item: item.area)
        if piece.area >= BED_MIN_MM2:
            caps.append(piece)
    return caps


def _candidate_rects(living: Polygon, limit: int = 24) -> list[Polygon]:
    xs, ys = _snapped_axes(living)
    found = list(_end_caps(living))
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            width = x1 - x0
            if width < SPAN_MIN or width > 4600:
                continue
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1:]:
                    height = y1 - y0
                    if height < SPAN_MIN or height > 4600:
                        continue
                    rect = box(x0, y0, x1, y1)
                    if rect.area < BED_MIN_MM2 or rect.area > living.area * 0.55:
                        continue
                    if living.intersection(rect).area < 0.9 * rect.area:
                        continue
                    piece = _as_poly(living.intersection(rect))
                    if piece.area < BED_MIN_MM2:
                        continue
                    found.append(piece)
    unique = []
    for rect in sorted(found, key=lambda item: -item.area):
        if any(
            rect.intersection(other).area / max(rect.union(other).area, 1) > 0.75
            for other in unique
        ):
            continue
        unique.append(rect)
        if len(unique) >= limit:
            break
    return unique


def _protected_points(doc: dict, living: Polygon) -> list[Point]:
    points = []
    entry = ((doc.get("entry") or {}).get("point") or [])
    if len(entry) >= 2:
        points.append(Point(int(entry[0]), int(entry[1])))
    room = _room(doc, "living")
    if room is None:
        return points
    for op in _world_openings(room):
        if op["kind"] != "door":
            continue
        if str(op["id"]).startswith("d_bed"):
            continue
        points.append(Point(op["at"]))
    return [pt for pt in points if living.buffer(400).covers(pt) or living.boundary.distance(pt) < 400]


def _living_left(living: Polygon, cuts: list[Polygon]) -> Polygon | None:
    remain = living.difference(unary_union(cuts))
    if remain.is_empty:
        return None
    parts = [remain] if remain.geom_type == "Polygon" else [part for part in remain.geoms if part.area > 1e6]
    if len(parts) != 1:
        return None
    remain = parts[0]
    floor = 7.0e6 if living.area >= 18e6 else 6.0e6
    if remain.area < floor:
        return None
    return _as_poly(remain)


def _keeps_access(remain: Polygon, points: list[Point], cuts: list[Polygon] | None = None) -> bool:
    for pt in points:
        if not (remain.buffer(180).covers(pt) or remain.boundary.distance(pt) < 180):
            return False
        if cuts and any(cut.distance(pt) < 850 for cut in cuts):
            return False
    # 门与门之间的直连线（入口到浴室/厨房门）不能被新睡房隔墙截断。
    if cuts and len(points) >= 2:
        union_cuts = unary_union(cuts)
        for i, pa in enumerate(points):
            for pb in points[i + 1:]:
                if pa.distance(pb) < 3200 and LineString([pa, pb]).intersects(union_cuts.buffer(150)):
                    return False
    return True


def _shared_line(a: Polygon, b: Polygon, min_width: float = 950.0) -> LineString | None:
    coords = list(a.exterior.coords)
    segments = []
    for i in range(len(coords) - 1):
        seg = LineString([coords[i], coords[i + 1]])
        if seg.length < 900:
            continue
        overlap = seg.intersection(b.boundary.buffer(40))
        if overlap.length >= 0.8 * seg.length:
            segments.append(seg)
    if not segments:
        return None
    core = b.buffer(-min_width / 2.0)
    if not core.is_empty:
        return max(
            segments,
            key=lambda seg: (round(seg.buffer(min_width / 2.0 + 40).intersection(core).area), seg.length),
        )
    return max(segments, key=lambda seg: seg.length)


def _door_on(line: LineString, bedroom: Polygon) -> dict:
    start, end = line.coords[0], line.coords[-1]
    length = line.length
    center_t = 0.35 if length > 1600 else 0.5
    # 靠近客厅中部的那一端，门扇向睡房里开。
    cx, cy = bedroom.centroid.x, bedroom.centroid.y
    if math.hypot(start[0] - cx, start[1] - cy) < math.hypot(end[0] - cx, end[1] - cy):
        center_t = 1.0 - center_t
    px, py = line.interpolate(center_t, normalized=True).coords[0]
    vertical = abs(end[0] - start[0]) < abs(end[1] - start[1])
    return dict(
        kind="door", width_mm=800, height_mm=2100, sill_h_mm=0,
        at=(float(px), float(py)), axis="v" if vertical else "h",
        swing=dict(dir="in", hinge="left"),
    )


def _apply_cuts(doc: dict, cuts: dict[str, Polygon], scheme: str) -> dict | None:
    living_room = _room(doc, "living")
    if living_room is None or not cuts:
        return None
    living = _poly(living_room)
    pieces = []
    for rid, cut in cuts.items():
        piece = _as_poly(living.intersection(cut))
        if piece.area < BED_MIN_MM2:
            return None
        pieces.append((rid, piece))
    remain = _living_left(living, [piece for _rid, piece in pieces])
    if remain is None or not _keeps_access(remain, _protected_points(doc, living), [piece for _rid, piece in pieces]):
        return None
    doors = []
    for rid, piece in pieces:
        line = _shared_line(piece, remain)
        if line is None:
            return None
        door = _door_on(line, piece)
        door["id"] = "d_%s" % rid
        door["between"] = (rid, "living")
        doors.append(door)
    out = copy.deepcopy(doc)
    kept = [room for room in out["rooms"] if room["id"] != "living"]
    labels = {"bedroom_w": "睡房", "bedroom_e": "客房"}
    polys = {rid: piece for rid, piece in pieces}
    polys["living"] = remain
    outline = _as_poly(unary_union(list(polys.values()) + [_poly(room) for room in kept if len(room.get("polygon") or []) >= 3]))
    pending = []
    for op in _world_openings(living_room):
        if str(op["id"]).startswith("d_bed"):
            continue
        pending.append(op)
    pending.extend(doors)
    bare = {}
    order = list(cuts) + ["living"]
    for rid in order:
        bare[rid] = _walls_bare(rid, polys[rid], outline)
    try:
        _attach_openings(bare, pending)
    except ValueError:
        return None
    height = int(living_room.get("height_mm") or 2450)
    new_rooms = []
    for rid in order:
        walls = bare[rid]
        new_rooms.append(dict(
            id=rid, label=labels.get(rid, living_room.get("label") or "客/飯廳"),
            polygon=_ring(polys[rid]), holes=[],
            area_mm2=int(round(polys[rid].area)), height_mm=height,
            height_field=[], walls=walls,
            openings=[dict(op, wall=wall["id"]) for wall in walls for op in wall["openings"]],
        ))
    out["rooms"] = new_rooms + kept
    links = [dict(item) for item in out.get("connections") or []
             if "bedroom" not in (item.get("a"), item.get("b"))]
    portals = list((out.get("meta") or {}).get("portals") or [])
    for door in doors:
        a, b = door["between"]
        links.append(dict(
            a=a, b=b, type="doorway", width_mm=door["width_mm"],
            door_id=door["id"], swing=door["swing"]))
        at, half = door["at"], door["width_mm"] / 2.0
        if door["axis"] == "v":
            portal = _portal((at[0], at[1] - half), (at[0], at[1] + half))
        else:
            portal = _portal((at[0] - half, at[1]), (at[0] + half, at[1]))
        portals.append([[int(round(x)), int(round(y))] for x, y in portal.exterior.coords[:-1]])
    out["connections"] = links
    meta = dict(out.get("meta") or {})
    meta["portals"] = portals
    meta["scheme"] = scheme
    meta["kind"] = scheme
    meta["program"] = default_program_for_flat(out)
    meta["program"]["doors"] = [
        dict(id=door["id"], between=list(door["between"]), swing=door["swing"], at=list(door["at"]))
        for door in doors
    ]
    out["meta"] = meta
    return out


def propose_bedroom_partitions(doc: dict, target_bedrooms: int | None = None) -> list[dict]:
    """从原客厅切出 1 或 2 间睡房。已经有睡房就原样返回。"""
    if _bedroom_ids(doc) and target_bedrooms is None:
        return [doc]
    living_room = _room(doc, "living")
    if living_room is None:
        return [doc]
    living = _poly(living_room)
    rects = _candidate_rects(living)
    want_two = living.area >= 20.0e6 if target_bedrooms is None else target_bedrooms >= 2
    want_one = target_bedrooms is None or target_bedrooms <= 1 or not want_two
    schemes = []
    if want_two:
        for i, west in enumerate(rects):
            for east in rects[i + 1:]:
                if west.intersection(east).area > 0.15 * min(west.area, east.area):
                    continue
                built = _apply_cuts(doc, {"bedroom_w": west, "bedroom_e": east}, "bed-2-%d" % len(schemes))
                if built is not None:
                    schemes.append(built)
                if len(schemes) >= 4:
                    break
            if len(schemes) >= 4:
                break
    if want_one or not schemes:
        singles = []
        for rect in rects:
            built = _apply_cuts(doc, {"bedroom_w": rect}, "bed-1-%d" % len(singles))
            if built is not None:
                singles.append(built)
        singles.sort(key=lambda item: sum(
            room["area_mm2"] for room in item["rooms"] if str(room["id"]).startswith("bedroom")))
        picked = []
        for item in singles[:2] + singles[-2:]:
            if item not in picked:
                picked.append(item)
        schemes.extend(picked)
    schemes.sort(key=lambda item: (
        len(_bedroom_ids(item)),
        sum(room["area_mm2"] for room in item["rooms"] if str(room["id"]).startswith("bedroom")),
    ), reverse=True)
    return schemes or [doc]


def ensure_bedrooms(doc: dict, critic=None) -> dict:
    """挑一个过了通道检查的隔墙方案。模型只能点方案号，不能写坐标。"""
    schemes = propose_bedroom_partitions(doc)
    if len(schemes) == 1 and _bedroom_ids(schemes[0]) == _bedroom_ids(doc):
        return schemes[0]
    ranked = rank_partition_plans(schemes)
    if not ranked:
        return schemes[0]
    chosen = ranked[0]
    if critic is not None:
        names = [row["kind"] for row in ranked]
        report = critic.critique([], [], diagnostics=names) if hasattr(critic, "critique") else {}
        wanted = None
        for action in report.get("actions") or []:
            if (action.get("type") == "change_partition") and action.get("scheme") in names:
                wanted = action["scheme"]
        if wanted:
            chosen = next(row for row in ranked if row["kind"] == wanted)
    return next(item for item in schemes if (item.get("meta") or {}).get("scheme") == chosen["kind"])
