"""把抽出的证据收成房间图。坐标只来自墙和填色，模型不能在这里改点。"""
from __future__ import annotations

from shapely.geometry import LineString, Point, Polygon

from plan_graph import Evidence, OpeningHyp, PlanGraph, RoomFace, WallHyp, graph_errors


def build_graphs(extracted: dict, flat_id: str) -> list[PlanGraph]:
    scale, kept = _agreeing_scale(extracted.get("dimensions") or [])
    dimensions = []
    for item in extracted.get("dimensions") or []:
        row = dict(item)
        row["status"] = "observed" if item in kept else "rejected"
        dimensions.append(row)
    if scale is None:
        # 没有两条互相吻合的尺寸时，不发明比例尺。
        scale_for_draw = None
    else:
        scale_for_draw = scale
    graphs = []
    primary = _graph_from_faces(extracted, flat_id, scale_for_draw, dimensions, "wall-cells")
    if primary.rooms:
        graphs.append(primary)
    swapped = _swap_service_labels(primary)
    if swapped is not None:
        graphs.append(swapped)
    return graphs


def _swap_service_labels(graph: PlanGraph):
    ids = {room.id for room in graph.rooms}
    if "bath" not in ids or "kitchen" not in ids:
        return None
    import copy
    other = copy.deepcopy(graph)
    other.hypothesis = "labels-swapped"
    for room in other.rooms:
        if room.id == "bath":
            room.label = "廚房"
        elif room.id == "kitchen":
            room.label = "浴室"
    return other


def _agreeing_scale(dimensions: list[dict]):
    usable = [item for item in dimensions if 35 <= float(item.get("mm_per_unit") or 0) <= 100]
    best: list[dict] = []
    for item in usable:
        scale = float(item["mm_per_unit"])
        group = [other for other in usable if abs(float(other["mm_per_unit"]) - scale) / scale <= 0.08]
        if len(group) > len(best):
            best = group
    if len(best) < 2:
        return None, []
    ordered = sorted(float(item["mm_per_unit"]) for item in best)
    median = ordered[len(ordered) // 2]
    best = [item for item in best if abs(float(item["mm_per_unit"]) - median) / median <= 0.08]
    if len(best) < 2:
        return None, []
    ordered = sorted(float(item["mm_per_unit"]) for item in best)
    return ordered[len(ordered) // 2], best


def _graph_from_faces(extracted, flat_id, scale, dimensions, hypothesis) -> PlanGraph:
    living = extracted["living"]
    faces = [("living", "客/飯廳", living, "unit-fill")]
    for service in extracted.get("services") or []:
        label = "浴室" if service["kind"] == "bath" else "廚房"
        faces.append((service["kind"], label, service["geom"], "wall-cell"))
    if scale is None:
        # 只用 point 存一个空比例的图，门和面积门槛会让它过不了。
        scale = 1.0
        scaled = False
    else:
        scaled = True
    origin = _origin(faces)
    rooms = []
    for rid, label, geom, method in faces:
        polygon = _to_mm(geom, scale, origin)
        if len(polygon) < 3:
            continue
        rooms.append(RoomFace(
            id=rid, label=label, polygon=polygon,
            evidence=[Evidence(
                kind="polygon", source=method,
                detail="%s from %s" % (label, method),
                confidence=0.8 if method == "unit-fill" else 0.6,
                status="observed" if method == "unit-fill" else "inferred",
            )]))
    hints = _door_hints(extracted, scale, origin)
    walls, openings = _walls_and_doors(rooms, hints)
    entry = _entry(rooms, walls, openings)
    return PlanGraph(
        flat_id=flat_id,
        rooms=rooms,
        walls=walls,
        openings=openings,
        entry=entry,
        services=[],
        dimensions=dimensions if scaled else [],
        scale_mm_per_unit=scale if scaled else None,
        hypothesis=hypothesis,
        origin_pt=origin,
    )


def _origin(faces) -> tuple[float, float]:
    geoms = [geom for _rid, _label, geom, _method in faces]
    return min(geom.bounds[0] for geom in geoms), min(geom.bounds[1] for geom in geoms)


def _to_mm(geom, scale: float, origin) -> list[tuple[int, int]]:
    ox, oy = origin
    ring = list(geom.exterior.coords)[:-1] if geom.geom_type == "Polygon" else []
    points = []
    for x, y in ring:
        point = (int(round((x - ox) * scale)), int(round((y - oy) * scale)))
        if not points or abs(point[0] - points[-1][0]) > 15 or abs(point[1] - points[-1][1]) > 15:
            points.append(point)
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points


def _door_hints(extracted, scale, origin) -> dict:
    ox, oy = origin
    swings = []
    for arc in extracted.get("swings") or []:
        swings.append(dict(
            a=((arc["a"][0] - ox) * scale, (arc["a"][1] - oy) * scale),
            b=((arc["b"][0] - ox) * scale, (arc["b"][1] - oy) * scale),
        ))
    gap = extracted.get("entry_gap")
    entry = None
    if gap and scale:
        entry = dict(
            y=(gap["y"] - oy) * scale,
            x0=(gap["x0"] - ox) * scale,
            x1=(gap["x1"] - ox) * scale,
            notch=((gap["notch"][0] - ox) * scale, (gap["notch"][1] - ox) * scale),
        )
    return dict(swings=swings, entry=entry)


def _hinges(arc) -> list[tuple[float, float]]:
    ax, ay = arc["a"]
    bx, by = arc["b"]
    dx, dy = bx - ax, by - ay
    chord = max(1.0, (dx * dx + dy * dy) ** 0.5)
    mx, my = (ax + bx) / 2, (ay + by) / 2
    px, py = -dy / chord, dx / chord
    reach = chord / 2
    return [(mx + px * reach, my + py * reach), (mx - px * reach, my - py * reach)]


def _walls_and_doors(rooms: list[RoomFace], hints: dict | None = None):
    hints = hints or {}
    walls = []
    for room in rooms:
        ring = room.polygon + [room.polygon[0]]
        for index in range(len(room.polygon)):
            a, b = ring[index], ring[index + 1]
            length = _length(a, b)
            if length < 200:
                continue
            wall_id = "%s_w%d" % (room.id, index)
            walls.append(WallHyp(
                id=wall_id, a=a, b=b, thickness_mm=150, wall_type="partition",
                evidence=[Evidence("wall", "room-edge", wall_id, 0.7, "inferred")]))
    openings = []
    used_arcs = set()
    used_rooms = set()
    ranked = []
    for room in rooms:
        if room.id == "living":
            continue
        shape = Polygon(room.polygon)
        for index, arc in enumerate(hints.get("swings") or []):
            mid = ((arc["a"][0] + arc["b"][0]) / 2.0, (arc["a"][1] + arc["b"][1]) / 2.0)
            if not shape.buffer(80).contains(Point(mid)):
                continue
            placed = _hinge_on_room(room, arc)
            if placed is None:
                continue
            hinge, horizontal = placed
            dist = shape.boundary.distance(Point(hinge))
            ranked.append((dist, room, index, hinge, horizontal, arc))
    ranked.sort(key=lambda item: (item[0], item[1].id))
    for _dist, room, index, hinge, horizontal, arc in ranked:
        if index in used_arcs or room.id in used_rooms:
            continue
        wall = _wall_for_swing(walls, room.id, hinge, horizontal)
        if wall is None:
            continue
        center, side, width = _opening_at_hinge(wall, hinge, arc)
        openings.append(OpeningHyp(
            id="d_%s_living" % room.id, wall_id=wall.id, kind="door",
            center_mm=center, width_mm=width, swing={"hinge": side},
            evidence=[Evidence("door", "swing-arc", room.id, 0.8, "observed")]))
        used_arcs.add(index)
        used_rooms.add(room.id)
    entry_door = _entry_door(walls, rooms, hints, used_arcs)
    if entry_door is not None:
        openings.append(entry_door)
    return walls, openings


def _entry_door(walls, rooms, hints, used_arcs) -> OpeningHyp | None:
    """香港入户门一律向屋里开。找客厅外墙上向内扫的门弧，门前正好对住浴室门。"""
    living = next((room for room in rooms if room.id == "living"), None)
    if living is None:
        return None
    bath = next((room for room in rooms if room.id == "bath"), None)
    shape = Polygon(living.polygon)
    best = None
    for index, arc in enumerate(hints.get("swings") or []):
        if index in used_arcs:
            continue
        mid = ((arc["a"][0] + arc["b"][0]) / 2.0, (arc["a"][1] + arc["b"][1]) / 2.0)
        if not shape.contains(Point(mid)):
            continue
        hinge = min(_hinges(arc), key=lambda point: shape.boundary.distance(Point(point)))
        wall = _nearest_wall(walls, "living", hinge)
        if wall is None or LineString([wall.a, wall.b]).distance(Point(hinge)) > 350:
            continue
        if any(Polygon(room.polygon).distance(Point(hinge)) < 350 for room in rooms if room.id != "living"):
            continue
        # 门扇另一端必须伸进客厅里面，证明是向内开的门。
        leaf = max((arc["a"], arc["b"]), key=lambda point: LineString([wall.a, wall.b]).distance(Point(point)))
        if not shape.contains(Point(leaf)):
            continue
        score = _entry_score(wall, hinge, living, bath)
        if best is None or score > best[0]:
            best = (score, wall, hinge, arc)
    if best is not None:
        _score, wall, hinge, arc = best
        center, side, width = _opening_at_hinge(wall, hinge, arc)
        return OpeningHyp(
            id="d_entry", wall_id=wall.id, kind="door",
            center_mm=center, width_mm=width, swing={"hinge": side},
            evidence=[Evidence("door", "inward-swing", wall.id, 0.9, "observed")])
    wall = _entry_wall(walls, rooms)
    if wall is None:
        return None
    length = _length(wall.a, wall.b)
    width = 900 if length >= 1100 else max(700, int(length * 0.4))
    return OpeningHyp(
        id="d_entry", wall_id=wall.id, kind="door",
        center_mm=int(length / 2), width_mm=width, swing={"hinge": "right"},
        evidence=[Evidence("door", "entry-fallback", wall.id, 0.4, "inferred")])


def _entry_score(wall: WallHyp, hinge, living: RoomFace, bath: RoomFace | None) -> float:
    vertical = 1.0 if abs(wall.b[1] - wall.a[1]) > abs(wall.b[0] - wall.a[0]) else 0.0
    if bath is None:
        span_x = max(1.0, max(p[0] for p in living.polygon) - min(p[0] for p in living.polygon))
        rightness = (hinge[0] - min(p[0] for p in living.polygon)) / span_x
        return vertical + rightness
    dist = Polygon(bath.polygon).distance(Point(hinge))
    near_bath = max(0.0, 1.0 - dist / 2500.0)
    living_cx = sum(p[0] for p in living.polygon) / max(1, len(living.polygon))
    bath_cx = sum(p[0] for p in bath.polygon) / max(1, len(bath.polygon))
    side = 1.0 if (bath_cx - living_cx) * (hinge[0] - living_cx) > 0 else 0.0
    return vertical + near_bath * 2.0 + side * 2.0


def _arc_in_room(room: RoomFace, swings: list, used: set):
    shape = Polygon(room.polygon)
    best = None
    for index, arc in enumerate(swings):
        if index in used:
            continue
        for hinge in _hinges(arc):
            distance = shape.distance(Point(hinge))
            if distance > 500:
                continue
            if best is None or distance < best[0]:
                best = (distance, index)
    return None if best is None else best[1]


def _hinge_on_room(room: RoomFace, arc) -> tuple[tuple[float, float], bool] | None:
    shape = Polygon(room.polygon)
    ranked = sorted(_hinges(arc), key=lambda hinge: shape.boundary.distance(Point(hinge)))
    if not ranked or shape.boundary.distance(Point(ranked[0])) > 500:
        return None
    hinge = ranked[0]
    ends = (arc["a"], arc["b"])
    flatter = min(ends, key=lambda point: abs(point[1] - hinge[1]))
    horizontal = abs(flatter[1] - hinge[1]) <= abs(flatter[0] - hinge[0])
    return hinge, horizontal


def _wall_for_swing(walls, room_id, hinge, horizontal: bool):
    best = None
    point = Point(hinge)
    for wall in walls:
        if not wall.id.startswith(room_id + "_"):
            continue
        dx = abs(wall.b[0] - wall.a[0])
        dy = abs(wall.b[1] - wall.a[1])
        if horizontal and dy > dx:
            continue
        if not horizontal and dx > dy:
            continue
        distance = LineString([wall.a, wall.b]).distance(point)
        if best is None or distance < best[0]:
            best = (distance, wall)
    if best is None or best[0] > 500:
        return _nearest_wall(walls, room_id, hinge)
    return best[1]


def _nearest_wall(walls, room_id, hinge):
    best = None
    point = Point(hinge)
    for wall in walls:
        if not wall.id.startswith(room_id + "_"):
            continue
        distance = LineString([wall.a, wall.b]).distance(point)
        if best is None or distance < best[0]:
            best = (distance, wall)
    if best is None or best[0] > 500:
        return None
    return best[1]


def _opening_at_hinge(wall: WallHyp, hinge, arc=None) -> tuple[int, str, int]:
    length = _length(wall.a, wall.b)
    along = _along(wall, hinge)
    width = 900 if length >= 1100 else max(700, int(length * 0.65))
    if arc is not None:
        line = LineString([wall.a, wall.b])
        jamb = min((arc["a"], arc["b"]), key=lambda point: line.distance(Point(point)))
        jamb_along = _along(wall, jamb)
        if abs(jamb_along - along) > 250:
            if jamb_along < along:
                center = int(min(length - width / 2, max(width / 2, along - width / 2)))
                return center, "right", width
            center = int(min(length - width / 2, max(width / 2, along + width / 2)))
            return center, "left", width
    if along > length / 2:
        center = int(min(length - width / 2, max(width / 2, along - width / 2)))
        side = "right"
    else:
        center = int(min(length - width / 2, max(width / 2, along + width / 2)))
        side = "left"
    return center, side, width


def _along(wall: WallHyp, hinge) -> float:
    ax, ay = wall.a
    bx, by = wall.b
    dx, dy = bx - ax, by - ay
    length2 = max(1.0, dx * dx + dy * dy)
    return max(0.0, min(_length(wall.a, wall.b), ((hinge[0] - ax) * dx + (hinge[1] - ay) * dy) / length2 * _length(wall.a, wall.b)))


def _other_room_along(rooms, room, a, b):
    line = LineString([a, b])
    best = None
    for other in rooms:
        if other.id == room.id:
            continue
        boundary = Polygon(other.polygon).boundary
        shared = boundary.intersection(line.buffer(220))
        if shared.length < 700:
            continue
        distance = boundary.distance(line)
        if best is None or distance < best[0]:
            best = (distance, other)
    return None if best is None else best[1]


def _entry_wall(walls: list[WallHyp], rooms: list[RoomFace]):
    living = next((room for room in rooms if room.id == "living"), None)
    if living is None or not walls:
        return None
    top = min(y for _x, y in living.polygon)
    candidates = [wall for wall in walls if wall.id.startswith("living_") and min(wall.a[1], wall.b[1]) <= top + 30]
    if not candidates:
        return None
    return max(candidates, key=lambda wall: _length(wall.a, wall.b))


def _entry(rooms: list[RoomFace], walls: list[WallHyp], openings: list[OpeningHyp]) -> dict:
    living = next((room for room in rooms if room.id == "living"), rooms[0] if rooms else None)
    if living is None:
        return {}
    door = next((op for op in openings if op.id == "d_entry"), None)
    wall = next((w for w in walls if door is not None and w.id == door.wall_id), None)
    if door is not None and wall is not None:
        ax, ay = wall.a
        bx, by = wall.b
        length = max(1.0, _length(wall.a, wall.b))
        ux, uy = (bx - ax) / length, (by - ay) / length
        cx, cy = ax + ux * door.center_mm, ay + uy * door.center_mm
        nx, ny = -uy, ux
        shape = Polygon(living.polygon)
        probe = (cx + nx * 450, cy + ny * 450)
        if not shape.contains(Point(probe)):
            probe = (cx - nx * 450, cy - ny * 450)
        return dict(room=living.id, point=[int(round(probe[0])), int(round(probe[1]))])
    corner = min(living.polygon, key=lambda point: point[1])
    return dict(room=living.id, point=[int(corner[0]), int(corner[1] + 400)])


def _length(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def acceptance_errors(graph: PlanGraph, overlay: dict | None = None) -> list[str]:
    errors = list(graph_errors(graph))
    if overlay and overlay.get("living_agreement", 1) < 0.45:
        errors.append("living overlay does not cover the source fill")
    if graph.scale_mm_per_unit is None:
        errors.append("scale was not established")
    return errors
