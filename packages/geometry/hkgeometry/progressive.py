"""先提议、再验收、再改、通过了才放下一件。

不让显大分在通道被沙发切断的方案上计分。几何验收是硬门槛。
"""
from __future__ import annotations

import math

from shapely.geometry import Point, Polygon

from .circulation import approach_zone, artery_clear, blocked_approaches, unreachable_rooms
from .greedy import door_approach_zones
from .house import HouseLayout, infer_room_functions
from .types import Layout, Placement, front_dir

ORDER = (
    "bed", "wardrobe", "toilet", "basin", "kitchen_counter", "fridge",
    "washing_machine", "shower", "tv_bench", "sofa", "dining_table",
    "coffee_table", "desk", "chair",
)


def _rotated(item: Placement, rot: int) -> tuple[int, int]:
    if rot in (90, 270):
        return item.d, item.w
    return item.w, item.d


def dining_slots(table: Placement) -> list[tuple[int, int, int]]:
    """餐桌四周四个固定座位。椅子只能坐这些位子，不能自己漂。"""
    x, y = table.x, table.y
    w, d = table.w, table.d
    if table.rot in (90, 270):
        w, d = d, w
    cw = cd = 450
    gap = 50
    return [
        (int(x + (w - cw) / 2), int(y - cd - gap), 0),
        (int(x + (w - cw) / 2), int(y + d + gap), 180),
        (int(x - cw - gap), int(y + (d - cd) / 2), 270),
        (int(x + w + gap), int(y + (d - cd) / 2), 90),
    ]


def _candidates(room, item: Placement, step: int = 220, placed=None) -> list[tuple[int, int, int]]:
    poly = room.usable_geo()
    if poly.is_empty:
        return []
    if item.category == "chair" and placed:
        table = next((p for p in placed if p.category == "dining_table"), None)
        if table is not None:
            taken = [p for p in placed if p.category == "chair"]
            free = []
            for pose in dining_slots(table):
                x, y, rot = pose
                if any(abs(p.x - x) < 80 and abs(p.y - y) < 80 for p in taken):
                    continue
                w, d = _rotated(item, rot)
                box = Polygon([(x, y), (x + w, y), (x + w, y + d), (x, y + d)])
                if poly.covers(box):
                    free.append(pose)
            return free
    minx, miny, maxx, maxy = poly.bounds
    found = []
    for rot in (0, 90, 180, 270):
        w, d = _rotated(item, rot)
        x = int(minx)
        while x + w <= maxx + 1:
            y = int(miny)
            while y + d <= maxy + 1:
                box = Polygon([(x, y), (x + w, y), (x + w, y + d), (x, y + d)])
                if poly.covers(box):
                    found.append((x, y, rot))
                y += step
            x += step
    return found


def _faces(item: Placement, target) -> tuple[float, float]:
    cx, cy = item.center()
    dx, dy = target[0] - cx, target[1] - cy
    dist = math.hypot(dx, dy) or 1.0
    fx, fy = item.front_dir()
    return (dx / dist) * fx + (dy / dist) * fy, dist


def _pair_ok(a: Placement, b: Placement, near: float, far: float) -> bool:
    """两件家具必须互相朝向，距离合适，正面投影要叠上。"""
    dot_ab, dist = _faces(a, b.center())
    dot_ba, _ = _faces(b, a.center())
    if dot_ab < 0.7 or dot_ba < 0.7 or not (near <= dist <= far):
        return False
    dx = b.center()[0] - a.center()[0]
    dy = b.center()[1] - a.center()[1]
    fx, fy = a.front_dir()
    lateral = abs(dx * fy - dy * fx)
    span = _perp_span(a, fx, fy) / 2.0 + _perp_span(b, fx, fy) / 2.0
    return lateral <= span + 80


def _perp_span(item: Placement, fx: float, fy: float) -> float:
    bw, bd = _rotated(item, item.rot)
    if abs(fx) >= abs(fy):
        return float(bd)
    return float(bw)


def _faces_aisle(room, item: Placement) -> bool:
    """电器背贴墙，正面朝向厨房里面，不能朝墙开。"""
    if item.footprint().distance(room.geo().boundary) > 160:
        return False
    zone = approach_zone(item)
    if zone is None:
        return True
    inside = room.usable_geo().intersection(zone).area
    if inside < 0.65 * max(zone.area, 1):
        return False
    for other_zone in door_approach_zones(room):
        if zone.intersection(other_zone[1]).area > 100:
            return False
    return True


def approaches_conflict(room, items: list[Placement]) -> bool:
    """后放的洁具不能把先放好的使用区堵死。"""
    from .circulation import _front_is_allowed
    for item in items:
        zone = approach_zone(item)
        if zone is None:
            continue
        if room.geo().intersection(zone).area < 0.6 * max(zone.area, 1):
            return True
        for other in items:
            if other.inst == item.inst or _front_is_allowed(item, other):
                continue
            if zone.intersection(other.footprint()).area > 15000:
                return True
    return False


_DOOR_CACHE: dict[tuple, list] = {}


def _door_zones(room):
    key = (id(room), tuple((op.id, op.wall, str(op.swing)) for op in room.openings))
    cached = _DOOR_CACHE.get(key)
    if cached is None:
        cached = door_approach_zones(room)
        if len(_DOOR_CACHE) > 32:
            _DOOR_CACHE.clear()
        _DOOR_CACHE[key] = cached
    return cached


def _hits_door(room, box: Polygon) -> bool:
    """门扇、门槛、开启区都不许被家具压到。"""
    if box is None or box.is_empty:
        return False
    for _i, zone in _door_zones(room):
        if zone.is_empty:
            continue
        if box.intersection(zone).area > 100:
            return True
    return False


def _overlap(box: Polygon, placed: list[Placement]) -> bool:
    return any(box.intersection(p.footprint()).area > 100 for p in placed)


def _zone_penalty(center, zone: Polygon | None) -> float:
    if zone is None or zone.is_empty:
        return 0.0
    if zone.covers(Point(center)):
        return 0.0
    return 200.0 + zone.distance(Point(center)) * 0.05


def _score(room, item, pose, placed, dining: Polygon | None) -> float:
    x, y, rot = pose
    w, d = _rotated(item, rot)
    box = Polygon([(x, y), (x + w, y), (x + w, y + d), (x, y + d)])
    if not room.usable_geo().covers(box) or _overlap(box, placed) or _hits_door(room, box):
        return 1e12
    cx, cy = x + w / 2.0, y + d / 2.0
    wall = box.distance(room.geo().boundary)
    score = wall
    trial = _commit(item, (x, y, rot))
    zone = approach_zone(trial)
    if zone is not None and _hits_door(room, zone):
        return 1e12
    if item.category in ("fridge", "washing_machine", "kitchen_counter", "wardrobe",
                         "toilet", "basin", "shower"):
        if not _faces_aisle(room, trial):
            return 1e12
    if item.category == "dining_table":
        if not _dining_slots_clear(room, trial, placed):
            return 1e12
        score += _zone_penalty((cx, cy), dining)
    if item.category == "chair" and placed:
        table = next((p for p in placed if p.category == "dining_table"), None)
        if table is None or not _on_unique_slot(trial, table, placed):
            return 1e12
    other = None
    if item.category == "sofa":
        other = next((p for p in placed if p.category == "tv_bench"), None)
    elif item.category == "tv_bench":
        other = next((p for p in placed if p.category == "sofa"), None)
    if other is not None and not _pair_ok(trial, other, 1400, 4000):
        return 1e12
    if item.category == "coffee_table":
        sofa = next((p for p in placed if p.category == "sofa"), None)
        if sofa is not None:
            dot, dist = _faces(sofa, (cx, cy))
            if dot < 0.5 or not (400 <= dist <= 1400):
                return 1e12
    return score


def _dining_slots_clear(room, table: Placement, placed: list[Placement]) -> bool:
    poly = room.usable_geo()
    for sx, sy, _srot in dining_slots(table):
        box_s = Polygon([(sx, sy), (sx + 450, sy), (sx + 450, sy + 450), (sx, sy + 450)])
        if poly.intersection(box_s).area < 0.98 * box_s.area:
            return False
        if _overlap(box_s, placed) or _hits_door(room, box_s):
            return False
    return True


def _on_unique_slot(chair: Placement, table: Placement, placed: list[Placement]) -> bool:
    slots = dining_slots(table)
    match = [s for s in slots if abs(chair.x - s[0]) < 5 and abs(chair.y - s[1]) < 5 and chair.rot == s[2]]
    if len(match) != 1:
        return False
    for other in placed:
        if other.category == "chair" and abs(other.x - chair.x) < 80 and abs(other.y - chair.y) < 80:
            return False
    return True


def _commit(item: Placement, pose) -> Placement:
    x, y, rot = pose
    return Placement(inst=item.inst, sku=item.sku, category=item.category,
                     x=x, y=y, rot=rot, w=item.w, d=item.d, h=item.h,
                     visual=dict(item.visual), price_hkd=item.price_hkd)


def _dining_zone(flat) -> Polygon | None:
    ring = (((flat.meta or {}).get("program") or {}).get("zones") or {}).get("dining")
    if not ring or len(ring) < 3:
        return None
    return Polygon([(int(p[0]), int(p[1])) for p in ring])


OWNED = {
    "bed", "wardrobe", "toilet", "basin", "shower", "kitchen_counter",
    "fridge", "washing_machine", "tv_bench", "sofa", "coffee_table",
    "dining_table", "chair",
}


def _pose_allowed(item: Placement, pose, constraints: dict | None) -> bool:
    if not constraints:
        return True
    rots = constraints.get("rotations") or {}
    if item.inst in rots and int(pose[2]) % 360 != int(rots[item.inst]) % 360:
        return False
    zone = (constraints.get("zones") or {}).get(item.inst)
    if zone:
        w, d = _rotated(item, pose[2])
        cx, cy = pose[0] + w / 2.0, pose[1] + d / 2.0
        if not (zone[0] <= cx <= zone[2] and zone[1] <= cy <= zone[3]):
            return False
    return True


def humanistic_errors(flat, placed: dict) -> list[str]:
    """人看图就会否定的那些关系。几何过不了，方案就不能发表。"""
    errors = []
    all_items = [p for ps in (placed or {}).values() for p in ps]
    for rid, items in (placed or {}).items():
        room = flat.room(rid)
        for item in items:
            if _hits_door(room, item.footprint()):
                errors.append("%s overlaps a door" % item.inst)
            zone = approach_zone(item)
            if zone is not None and _hits_door(room, zone):
                errors.append("%s operation zone overlaps a door" % item.inst)
            if item.category in ("fridge", "washing_machine", "kitchen_counter"):
                if not _faces_aisle(room, item):
                    errors.append("%s does not face the kitchen aisle" % item.inst)
        if approaches_conflict(room, items):
            errors.append("approach zones in %s block each other" % rid)
    sofas = [p for p in all_items if p.category == "sofa"]
    tvs = [p for p in all_items if p.category == "tv_bench"]
    if sofas or tvs:
        if len(sofas) != 1 or len(tvs) != 1 or not _pair_ok(sofas[0], tvs[0], 1400, 4000):
            errors.append("sofa and tv do not face each other")
        elif sofas:
            coffees = [p for p in all_items if p.category == "coffee_table"]
            if not coffees or not any(
                    _faces(sofas[0], c.center())[0] >= 0.5 and 400 <= _faces(sofas[0], c.center())[1] <= 1400
                    for c in coffees):
                errors.append("coffee table is outside the sofa front zone")
    tables = [p for p in all_items if p.category == "dining_table"]
    chairs = [p for p in all_items if p.category == "chair"]
    if tables or chairs:
        if len(tables) != 1 or len(chairs) != 4:
            errors.append("dining set is incomplete")
        else:
            used = set()
            for chair in chairs:
                match = [s for s in dining_slots(tables[0])
                         if abs(chair.x - s[0]) < 5 and abs(chair.y - s[1]) < 5 and chair.rot == s[2]]
                if len(match) != 1 or match[0] in used:
                    errors.append("dining chairs are not in distinct slots")
                    break
                used.add(match[0])
    return errors


def solve_progressive(flat, items: list[Placement], assignment: dict[str, str],
                      step: int = 220, constraints: dict | None = None,
                      variant: int = 0) -> HouseLayout:
    """先放功能组，再放零散件。每放一件都重查已经放过的使用区。"""
    constraints = constraints or {}
    funcs = infer_room_functions(flat)
    from .groups import place_groups
    grouped, notes = place_groups(flat, items, assignment, constraints, variant)
    by_room: dict[str, list[Placement]] = {r.id: list(grouped.get(r.id, [])) for r in flat.rooms}
    used = {p.inst for plist in by_room.values() for p in plist}
    rooms = {r.id: r for r in flat.rooms}
    dining = _dining_zone(flat)
    ordered = sorted(items, key=lambda it: ORDER.index(it.category) if it.category in ORDER else 50)
    unplaced = []
    for item in ordered:
        if item.inst in used:
            continue
        rid = assignment.get(item.inst)
        room = rooms.get(rid) if rid else None
        if room is None or item.category in OWNED:
            unplaced.append(item.inst)
            continue
        area = room.geo().area
        step_here = 120 if area < 12e6 else step
        poses = [p for p in _candidates(room, item, step=step_here, placed=by_room[rid])
                 if _pose_allowed(item, p, constraints)]
        seed = (constraints.get("seeds") or {}).get(item.inst)
        if seed:
            poses.sort(key=lambda pose: (pose[0] - seed[0]) ** 2 + (pose[1] - seed[1]) ** 2)
        else:
            poses.sort(key=lambda pose: _score(room, item, pose, by_room[rid], dining))
        placed = None
        checked = 0
        for pose in poses:
            if _score(room, item, pose, by_room[rid], dining) >= 1e12:
                continue
            checked += 1
            if checked > 40:
                break
            trial = _commit(item, pose)
            trial_map = {k: list(v) for k, v in by_room.items()}
            trial_map[rid] = trial_map[rid] + [trial]
            if approaches_conflict(room, trial_map[rid]):
                continue
            if unreachable_rooms(flat, trial_map, person_mm=600):
                continue
            if rid == flat.entry_room and not artery_clear(flat, trial_map, min_width=900):
                continue
            fresh = blocked_approaches(flat, trial_map)
            if fresh:
                continue
            placed = trial
            break
        if placed is None:
            unplaced.append(item.inst)
        else:
            by_room[rid].append(placed)
    house = HouseLayout(flat_id=flat.id, assignment=dict(assignment), unplaced=unplaced)
    for rid, plist in by_room.items():
        house.by_room[rid] = Layout(room_id=rid, placements=plist)
    house.reports["functions"] = funcs
    house.reports["groups"] = notes
    house.reports["constraints"] = constraints
    return house
