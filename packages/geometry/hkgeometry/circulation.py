"""从入户点出发的连通通道。

沿房间长轴扫截面会把一条和入口不相连的空带当成「最窄 930mm」。
这里只承认：入口、每扇必需的门、每件家具的使用点在同一块自由空间里。
"""
from __future__ import annotations

from collections import deque

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points

CELL = 140


def portal_shapes(flat) -> list[Polygon]:
    out = []
    for ring in (flat.meta or {}).get("portals") or []:
        if len(ring) >= 3:
            out.append(Polygon([(int(x), int(y)) for x, y in ring]))
    return out


def _footprints(placements_by_room) -> list:
    out = []
    for items in (placements_by_room or {}).values():
        for item in items:
            out.append(item.footprint())
    return out


class _Grid:
    def __init__(self, flat, placements_by_room, person_mm: int):
        self.flat = flat
        self.portals = portal_shapes(flat)
        self.pad = person_mm / 2.0
        self.furn = _footprints(placements_by_room)
        bounds = [r.geo().bounds for r in flat.rooms]
        self.minx = min(b[0] for b in bounds) - CELL
        self.miny = min(b[1] for b in bounds) - CELL
        self.maxx = max(b[2] for b in bounds) + CELL
        self.maxy = max(b[3] for b in bounds) + CELL
        self.nx = max(1, int((self.maxx - self.minx) // CELL) + 1)
        self.ny = max(1, int((self.maxy - self.miny) // CELL) + 1)
        self._lab = {}

    def _point(self, ix, iy):
        return (self.minx + ix * CELL + CELL / 2.0,
                self.miny + iy * CELL + CELL / 2.0)

    def label(self, ix, iy):
        key = (ix, iy)
        if key in self._lab:
            return self._lab[key]
        if not (0 <= ix < self.nx and 0 <= iy < self.ny):
            self._lab[key] = None
            return None
        x, y = self._point(ix, iy)
        pt = Point(x, y)
        if any(fp.buffer(self.pad).covers(pt) for fp in self.furn):
            self._lab[key] = None
            return None
        for i, portal in enumerate(self.portals):
            if portal.covers(pt):
                self._lab[key] = ("portal", i)
                return self._lab[key]
        for room in self.flat.rooms:
            if room.geo().buffer(-8).covers(pt):
                self._lab[key] = ("room", room.id)
                return self._lab[key]
        self._lab[key] = None
        return None

    def can_step(self, a, b) -> bool:
        if a is None or b is None:
            return False
        if a[0] == "room" and b[0] == "room" and a[1] != b[1]:
            return False
        return True

    def nearest(self, x, y, radius=8):
        ix = int((x - self.minx) // CELL)
        iy = int((y - self.miny) // CELL)
        best = None
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                lab = self.label(ix + dx, iy + dy)
                if lab is None:
                    continue
                dist = dx * dx + dy * dy
                if best is None or dist < best[0]:
                    best = (dist, ix + dx, iy + dy, lab)
        return None if best is None else best[1:]

    def reachable_from(self, start_xy) -> set[tuple[int, int]]:
        start = self.nearest(start_xy[0], start_xy[1])
        if start is None:
            return set()
        seen = {(start[0], start[1])}
        q = deque([(start[0], start[1])])
        while q:
            ix, iy = q.popleft()
            here = self.label(ix, iy)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (ix + dx, iy + dy)
                if nxt in seen:
                    continue
                lab = self.label(*nxt)
                if not self.can_step(here, lab):
                    continue
                seen.add(nxt)
                q.append(nxt)
        return seen

    def reaches_point(self, seen, xy) -> bool:
        hit = self.nearest(xy[0], xy[1], radius=6)
        return hit is not None and (hit[0], hit[1]) in seen


def _targets(flat) -> list[tuple[str, tuple[float, float]]]:
    out = []
    for room in flat.rooms:
        pt = room.geo().representative_point()
        out.append((room.id, (pt.x, pt.y)))
    return out


# 茶几就该在沙发前面，椅子就该贴着餐桌。这两种不算「挡住使用区」。
# 香港细浴室共用一块站人区，马桶/面盆/花洒的正面互相压一点也算过。
_ALLOWED_FRONT = {
    ("sofa", "coffee_table"),
    ("chair", "dining_table"),
    ("dining_table", "chair"),
    ("toilet", "basin"), ("basin", "toilet"),
    ("toilet", "shower"), ("shower", "toilet"),
    ("basin", "shower"), ("shower", "basin"),
}


def _front_is_allowed(item, other) -> bool:
    return (item.category, other.category) in _ALLOWED_FRONT


def approach_zone(item, depth: int | None = None):
    """家具正面要站人的那一块。"""
    need = depth
    if need is None:
        from .types import FRONT_CLEARANCE
        need = FRONT_CLEARANCE.get(item.category, 0)
        if item.category in ("bed", "sofa"):
            need = 500
    if not need:
        return None
    cx, cy = item.center()
    fx, fy = item.front_dir()
    hw = item.d / 2.0
    span = min(item.w, item.d)
    px, py = cx + fx * (hw + need / 2.0), cy + fy * (hw + need / 2.0)
    if abs(fy) > abs(fx):
        return Polygon([
            (px - span / 2, py - need / 2), (px + span / 2, py - need / 2),
            (px + span / 2, py + need / 2), (px - span / 2, py + need / 2),
        ])
    return Polygon([
        (px - need / 2, py - span / 2), (px + need / 2, py - span / 2),
        (px + need / 2, py + span / 2), (px - need / 2, py + span / 2),
    ])


def blocked_approaches(flat, placements_by_room) -> list[str]:
    """使用区出界、被别的家具堵住、或从房门走不到，都算不可用。"""
    errors = []
    grid = _Grid(flat, placements_by_room or {}, person_mm=500)
    seen = grid.reachable_from(flat.entry_point)
    for rid, items in (placements_by_room or {}).items():
        room = flat.room(rid)
        for item in items:
            zone = approach_zone(item)
            if zone is None:
                continue
            if room.geo().intersection(zone).area < 0.6 * zone.area:
                errors.append("%s approach leaves %s" % (item.inst, rid))
                continue
            for other in items:
                if other.inst == item.inst:
                    continue
                if _front_is_allowed(item, other):
                    continue
                if zone.intersection(other.footprint()).area > 15000:
                    errors.append("%s approach blocked by %s" % (item.inst, other.inst))
                    break
            probe = zone.representative_point()
            if not grid.reaches_point(seen, (probe.x, probe.y)):
                errors.append("%s approach is not reachable" % item.inst)
    return errors


def unreachable_rooms(flat, placements_by_room=None, person_mm: int = 600) -> list[str]:
    """入口走不到的房间。空列表才算这层平面是通的。"""
    grid = _Grid(flat, placements_by_room or {}, person_mm)
    seen = grid.reachable_from(flat.entry_point)
    lost = []
    for rid, xy in _targets(flat):
        if rid == flat.entry_room:
            continue
        if not grid.reaches_point(seen, xy):
            lost.append(rid)
    return lost


def _snap(geom, pt: Point):
    if geom.is_empty:
        return None
    if geom.covers(pt):
        return pt
    return nearest_points(geom, pt)[0]


def artery_clear(flat, placements_by_room=None, min_width: float = 900.0) -> bool:
    """客厅里是否还存在一条够宽、并且从入口接到每扇内门的通道。

    把自由空间内缩 min_width/2。入口和每扇门必须落到内缩后的同一块上。
    """
    living = next((r for r in flat.rooms if r.id == flat.entry_room), None)
    if living is None:
        return False
    free = living.geo()
    for item in (placements_by_room or {}).get(living.id, []):
        free = free.difference(item.footprint())
    core = free.buffer(-min_width / 2.0)
    if core.is_empty:
        return False
    mouths = []
    for portal in portal_shapes(flat):
        mouth = portal.intersection(living.geo())
        if not mouth.is_empty:
            mouths.append(mouth.representative_point())
    if not mouths:
        return True

    def piece_id(pt):
        snapped = _snap(core, pt if isinstance(pt, Point) else Point(pt))
        if snapped is None:
            return None
        if core.geom_type == "Polygon":
            return 0 if core.distance(snapped) <= 1 else None
        for i, part in enumerate(core.geoms):
            if part.covers(snapped) or part.distance(snapped) <= 1:
                return i
        return None

    home = piece_id(Point(flat.entry_point))
    if home is None:
        return False
    return all(piece_id(m) == home for m in mouths)


def _opening_xy(room, op):
    wall = next((w for w in room.walls if w.id == op.wall), None)
    if wall is None:
        return None
    ax, ay = wall.a
    bx, by = wall.b
    length = max(1.0, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
    t = max(0.0, min(1.0, op.center_mm / length))
    return (ax + (bx - ax) * t, ay + (by - ay) * t)


def corridor_keepouts(room, entry_xy, target_xys, width: float = 900.0) -> list[Polygon]:
    """从入口沿最短路留出一条通道。旁边仍然可以放家具。"""
    if entry_xy is None or not target_xys:
        return []
    minx, miny, maxx, maxy = room.geo().bounds
    cell = 160.0
    nx = max(1, int((maxx - minx) // cell) + 2)
    ny = max(1, int((maxy - miny) // cell) + 2)

    def center(ix, iy):
        return (minx + ix * cell + cell / 2.0, miny + iy * cell + cell / 2.0)

    def inside(ix, iy):
        if not (0 <= ix < nx and 0 <= iy < ny):
            return False
        return room.geo().buffer(-20).covers(Point(*center(ix, iy)))

    def nearest(xy):
        ix = int((xy[0] - minx) // cell)
        iy = int((xy[1] - miny) // cell)
        best = None
        for dy in range(-6, 7):
            for dx in range(-6, 7):
                if not inside(ix + dx, iy + dy):
                    continue
                dist = dx * dx + dy * dy
                if best is None or dist < best[0]:
                    best = (dist, ix + dx, iy + dy)
        return None if best is None else (best[1], best[2])

    start = nearest(entry_xy)
    if start is None:
        return []
    out = []
    for target in target_xys:
        goal = nearest(target)
        if goal is None:
            continue
        prev = {start: None}
        q = deque([start])
        found = False
        while q:
            cur = q.popleft()
            if cur == goal:
                found = True
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (cur[0] + dx, cur[1] + dy)
                if nxt in prev or not inside(*nxt):
                    continue
                prev[nxt] = cur
                q.append(nxt)
        if not found:
            continue
        path = []
        cur = goal
        while cur is not None:
            path.append(center(*cur))
            cur = prev[cur]
        if len(path) < 2:
            continue
        band = LineString(path).buffer(width / 2.0, cap_style=2, join_style=2)
        piece = band.intersection(room.geo())
        if not piece.is_empty:
            out.append(piece)
    return out


def artery_keepout_zones(room, entry_point, width: float = 900.0):
    """大客厅才预留主通道。小房间硬留 900mm 会把床挤没。"""
    if entry_point is None or room.geo().area < 18e6:
        return []
    targets = []
    entry = Point(entry_point)
    for op in room.openings:
        if op.kind != "door":
            continue
        xy = _opening_xy(room, op)
        if xy is None or Point(xy).distance(entry) < 900:
            continue
        targets.append(xy)
    zones = corridor_keepouts(room, entry_point, targets, width=width)
    return [("artery_%d" % i, zone) for i, zone in enumerate(zones)]

