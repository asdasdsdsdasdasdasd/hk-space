"""证据图。坐标只从墙和填色推出来，每条证据都记下出处。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from shapely.geometry import Polygon


@dataclass
class Evidence:
    kind: str
    source: str
    detail: str
    confidence: float
    status: str = "observed"   # observed | inferred | assumed

    def as_dict(self) -> dict:
        return dict(kind=self.kind, source=self.source, detail=self.detail,
                    confidence=round(float(self.confidence), 3), status=self.status)


@dataclass
class WallHyp:
    id: str
    a: tuple[int, int]
    b: tuple[int, int]
    thickness_mm: int
    wall_type: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class OpeningHyp:
    id: str
    wall_id: str
    kind: str
    center_mm: int
    width_mm: int
    swing: dict | None = None
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class RoomFace:
    id: str
    label: str
    polygon: list[tuple[int, int]]
    evidence: list[Evidence] = field(default_factory=list)

    def area(self) -> float:
        return float(Polygon(self.polygon).area) if len(self.polygon) >= 3 else 0.0


@dataclass
class PlanGraph:
    flat_id: str
    rooms: list[RoomFace]
    walls: list[WallHyp]
    openings: list[OpeningHyp]
    entry: dict
    services: list[dict] = field(default_factory=list)
    dimensions: list[dict] = field(default_factory=list)
    scale_mm_per_unit: float | None = None
    hypothesis: str = "primary"
    origin_pt: tuple[float, float] | None = None

    def signature(self) -> str:
        payload = json.dumps(self.to_room_json(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_room_json(self) -> dict:
        by_room_walls = {room.id: [] for room in self.rooms}
        by_room_openings = {room.id: [] for room in self.rooms}
        for wall in self.walls:
            hit = _rooms_touching_wall(self.rooms, wall)
            if wall.id.startswith("entry") and self.entry.get("room"):
                if self.entry["room"] not in hit:
                    hit.append(self.entry["room"])
            for rid in hit:
                by_room_walls[rid].append(dict(
                    id=wall.id, a=list(wall.a), b=list(wall.b),
                    type=wall.wall_type, thickness_mm=wall.thickness_mm, movable=False))
        for op in self.openings:
            hosts = [r.id for r in self.rooms if any(w["id"] == op.wall_id for w in by_room_walls[r.id])]
            for rid in hosts:
                by_room_openings[rid].append(dict(
                    id=op.id, wall=op.wall_id, kind=op.kind, center_mm=op.center_mm,
                    width_mm=op.width_mm, height_mm=2100 if op.kind == "door" else 1350,
                    sill_h_mm=0 if op.kind == "door" else 900, swing=op.swing))
        rooms = []
        for room in self.rooms:
            poly = [[int(x), int(y)] for x, y in room.polygon]
            rooms.append(dict(
                id=room.id, label=room.label, polygon=poly, holes=[],
                area_mm2=int(round(room.area())), height_mm=2450, height_field=[],
                walls=by_room_walls[room.id], openings=by_room_openings[room.id],
                evidence=[e.as_dict() for e in room.evidence]))
        connections = []
        for op in self.openings:
            if op.kind != "door":
                continue
            hosts = [r.id for r in self.rooms if any(w["id"] == op.wall_id for w in by_room_walls[r.id])]
            if len(hosts) >= 2:
                connections.append(dict(a=hosts[0], b=hosts[1], type="doorway",
                                        width_mm=op.width_mm, door_id=op.id, swing=op.swing))
        outline = _outline(self.rooms)
        portals = []
        for op in self.openings:
            if op.kind != "door":
                continue
            wall = next((w for w in self.walls if w.id == op.wall_id), None)
            if wall is None:
                continue
            portals.append(_portal_ring(wall, op))
        return dict(
            flat_id=self.flat_id,
            units="mm",
            rooms=rooms,
            connections=connections,
            fixed_services=list(self.services),
            entry=self.entry,
            meta=dict(
                source="floorplan_understanding",
                hypothesis=self.hypothesis,
                outline=outline,
                portals=portals,
                mm_per_px=self.scale_mm_per_unit,
                origin_pt=list(self.origin_pt) if self.origin_pt else None,
                dimensions=self.dimensions,
                evidence=[e.as_dict() for r in self.rooms for e in r.evidence],
                program=dict(
                    min_counts=_counts(self.rooms),
                    required_links=[[c["a"], c["b"]] for c in connections],
                    door_sides={},
                    furniture=[],
                ),
            ),
        )


def _counts(rooms: list[RoomFace]) -> dict:
    out = {}
    for room in rooms:
        key = room.id.split("_")[0]
        if key in ("living", "kitchen", "bath", "bedroom"):
            out[key] = out.get(key, 0) + 1
    return out


def _outline(rooms: list[RoomFace]) -> list[list[int]]:
    if not rooms:
        return []
    union = Polygon()
    for room in rooms:
        union = union.union(Polygon(room.polygon))
    # 墙厚会让相邻房间差几十毫米，先合上再收回来。
    union = union.buffer(220).buffer(-220)
    if union.is_empty:
        return []
    poly = union if union.geom_type == "Polygon" else max(union.geoms, key=lambda item: item.area)
    return [[int(round(x)), int(round(y))] for x, y in list(poly.exterior.coords)[:-1]]


def _rooms_touching_wall(rooms: list[RoomFace], wall: WallHyp) -> list[str]:
    from shapely.geometry import LineString
    line = LineString([wall.a, wall.b])
    owner = wall.id.split("_")[0]
    hits = []
    for room in rooms:
        if room.id == owner:
            hits.append(room.id)
            continue
        boundary = Polygon(room.polygon).boundary
        shared = boundary.intersection(line.buffer(wall.thickness_mm))
        if shared.length > 0.45 * line.length:
            hits.append(room.id)
    return hits


def _portal_ring(wall: WallHyp, op: OpeningHyp) -> list[list[int]]:
    ax, ay = wall.a
    bx, by = wall.b
    length = max(1.0, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
    ux, uy = (bx - ax) / length, (by - ay) / length
    nx, ny = -uy, ux
    cx, cy = ax + ux * op.center_mm, ay + uy * op.center_mm
    half = op.width_mm / 2.0
    span = wall.thickness_mm / 2.0 + 180
    p0 = (cx - ux * half, cy - uy * half)
    p1 = (cx + ux * half, cy + uy * half)
    return [[int(round(p0[0] + nx * span)), int(round(p0[1] + ny * span))],
            [int(round(p1[0] + nx * span)), int(round(p1[1] + ny * span))],
            [int(round(p1[0] - nx * span)), int(round(p1[1] - ny * span))],
            [int(round(p0[0] - nx * span)), int(round(p0[1] - ny * span))]]


def graph_errors(graph: PlanGraph) -> list[str]:
    """理解阶段的硬门槛。过不了就只能标 unverified。"""
    errors = []
    if graph.scale_mm_per_unit is None:
        errors.append("scale was not established")
    dims = [d for d in graph.dimensions if d.get("status") == "observed"]
    if len(dims) < 2:
        errors.append("fewer than two observed dimensions")
    polys = []
    for room in graph.rooms:
        if len(room.polygon) < 3 or room.area() < 1e6:
            errors.append("%s has no usable area" % room.id)
            continue
        poly = Polygon(room.polygon)
        if not poly.is_valid or poly.area <= 0:
            errors.append("%s polygon is invalid" % room.id)
        polys.append((room, poly))
    shell = Polygon(_outline(graph.rooms)) if graph.rooms else None
    if shell is not None and not shell.is_empty:
        cover = shell.buffer(80)
        for room, poly in polys:
            if cover.intersection(poly).area < 0.98 * poly.area:
                errors.append("%s falls outside the shell" % room.id)
    for i, (ra, pa) in enumerate(polys):
        for rb, pb in polys[i + 1:]:
            if pa.intersection(pb).area > 5000:
                errors.append("%s overlaps %s" % (ra.id, rb.id))
    for op in graph.openings:
        wall = next((w for w in graph.walls if w.id == op.wall_id), None)
        if wall is None:
            errors.append("opening %s has no wall" % op.id)
            continue
        length = ((wall.b[0] - wall.a[0]) ** 2 + (wall.b[1] - wall.a[1]) ** 2) ** 0.5
        if op.center_mm < 0 or op.center_mm > length + 1:
            errors.append("opening %s is outside its wall" % op.id)
        if not (400 <= op.width_mm <= 3200):
            errors.append("opening %s width is implausible" % op.id)
    if not graph.entry or not graph.entry.get("room"):
        errors.append("entry was not found")
    return errors
