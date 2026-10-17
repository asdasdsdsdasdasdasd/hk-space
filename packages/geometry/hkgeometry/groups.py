"""功能组候选。沙发、餐桌、洁具、厨电、床各自成组，缺一件就整组不要。"""
from __future__ import annotations

import math

from shapely.geometry import Polygon

from .circulation import approach_zone, artery_clear, blocked_approaches, unreachable_rooms
from .progressive import (
    _commit, _faces_aisle, _hits_door, _pair_ok, approaches_conflict, dining_slots,
)
from .types import Placement

GROUP_OF = {
    "bed": "bedroom", "wardrobe": "bedroom",
    "toilet": "bath", "basin": "bath", "shower": "bath",
    "kitchen_counter": "kitchen", "fridge": "kitchen", "washing_machine": "kitchen",
    "sofa": "living", "tv_bench": "living", "coffee_table": "living",
    "dining_table": "dining", "chair": "dining",
}


def _box(item: Placement, rot: int) -> tuple[int, int]:
    if rot in (90, 270):
        return item.d, item.w
    return item.w, item.d


def _copy(item: Placement, **kw) -> Placement:
    data = dict(inst=item.inst, sku=item.sku, category=item.category,
                x=item.x, y=item.y, rot=item.rot, w=item.w, d=item.d, h=item.h,
                visual=dict(item.visual), price_hkd=item.price_hkd)
    data.update(kw)
    return Placement(**data)


def _inside(poly, box: Polygon) -> bool:
    if box.is_empty:
        return False
    return poly.intersection(box).area >= 0.97 * box.area


def _piece_ok(room, item: Placement, others: list[Placement], aisle: bool = False) -> bool:
    box = item.footprint()
    if not _inside(room.usable_geo(), box):
        return False
    if any(box.intersection(o.footprint()).area > 100 for o in others):
        return False
    if _hits_door(room, box):
        return False
    zone = approach_zone(item)
    if zone is not None and _hits_door(room, zone):
        return False
    if aisle and not _faces_aisle(room, item):
        return False
    return True


def _group_ok(room, items: list[Placement], aisle_cats: set[str] | None = None) -> bool:
    aisle_cats = aisle_cats or set()
    kept: list[Placement] = []
    for item in items:
        if not _piece_ok(room, item, kept, aisle=item.category in aisle_cats):
            return False
        kept.append(item)
    return not approaches_conflict(room, kept)


def _bounds(room):
    minx, miny, maxx, maxy = room.usable_geo().bounds
    return int(minx), int(miny), int(maxx), int(maxy)


def wall_poses(room, item: Placement, step: int = 120, rots=None) -> list[tuple[int, int, int]]:
    """背贴四边，正面朝进房间。"""
    poly = room.usable_geo()
    minx, miny, maxx, maxy = _bounds(room)
    found = []
    for rot, axis in ((0, "s"), (180, "n"), (270, "w"), (90, "e")):
        if rots is not None and rot not in rots:
            continue
        bw, bd = _box(item, rot)
        if axis in ("s", "n"):
            y = miny if axis == "s" else int(maxy - bd)
            x = minx
            while x + bw <= maxx + 1:
                pose = (int(x), int(y), rot)
                if _inside(poly, _commit(item, pose).footprint()):
                    found.append(pose)
                x += step
        else:
            x = minx if axis == "w" else int(maxx - bw)
            y = miny
            while y + bd <= maxy + 1:
                pose = (int(x), int(y), rot)
                if _inside(poly, _commit(item, pose).footprint()):
                    found.append(pose)
                y += step
    return found


def corner_poses(room, item: Placement) -> list[tuple[int, int, int]]:
    minx, miny, maxx, maxy = _bounds(room)
    poly = room.usable_geo()
    found = []
    for rot in (0, 90, 180, 270):
        bw, bd = _box(item, rot)
        for x, y in ((minx, miny), (minx, maxy - bd), (maxx - bw, miny), (maxx - bw, maxy - bd)):
            pose = (int(x), int(y), rot)
            if _inside(poly, _commit(item, pose).footprint()):
                found.append(pose)
    return found


def snap_seed(room, item: Placement, x: float, y: float, rot: int | None = None,
              placed=None) -> tuple[int, int, int] | None:
    """模型坐标只当种子。落到最近的合法格点，落不了就丢掉。"""
    poses = wall_poses(room, item, step=140)
    if rot is not None:
        same = [p for p in poses if p[2] == int(rot) % 360]
        if same:
            poses = same
    legal = []
    for pose in poses:
        trial = _commit(item, pose)
        if _piece_ok(room, trial, list(placed or [])):
            legal.append(pose)
    if not legal:
        return None
    def dist(pose):
        bw, bd = _box(item, pose[2])
        return (pose[0] + bw / 2.0 - x) ** 2 + (pose[1] + bd / 2.0 - y) ** 2
    return min(legal, key=dist)


def _scale(item: Placement, factor: float) -> Placement:
    factor = max(0.7, min(1.0, float(factor)))
    return _copy(item, w=max(300, int(item.w * factor)), d=max(250, int(item.d * factor)))


def _resize_factor(item: Placement, constraints: dict, group: str) -> float | None:
    spec = (constraints.get("resize") or {}).get(item.inst)
    if spec is None:
        spec = (constraints.get("resize") or {}).get(group)
    if spec is None:
        return None
    if isinstance(spec, dict):
        if spec.get("scale") is not None:
            return float(spec["scale"])
        if spec.get("max_w"):
            return float(spec["max_w"]) / max(item.w, 1)
        return None
    return float(spec)


def _seed_of(constraints: dict, *names):
    seeds = constraints.get("seeds") or {}
    for name in names:
        if name in seeds:
            return seeds[name]
    return None


def _ordered(options: list, variant: int, seed=None, center=None):
    if not options:
        return []
    if seed is not None and center is not None:
        sx, sy = seed[0], seed[1]
        options = sorted(options, key=lambda g: (center(g)[0] - sx) ** 2 + (center(g)[1] - sy) ** 2)
    shift = variant % len(options)
    return options[shift:] + options[:shift]


def _centroid(items: list[Placement]):
    xs = [p.center()[0] for p in items]
    ys = [p.center()[1] for p in items]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _rot_ok(items: list[Placement], constraints: dict, group: str, primary: str) -> bool:
    rots = constraints.get("rotations") or {}
    for item in items:
        if item.inst in rots and item.rot != int(rots[item.inst]) % 360:
            return False
    if group in rots:
        hit = [p for p in items if p.category == primary]
        if hit and hit[0].rot != int(rots[group]) % 360:
            return False
    return True


def _zone_ok(items: list[Placement], constraints: dict, group: str) -> bool:
    zones = constraints.get("zones") or {}
    for item in items:
        zone = zones.get(item.inst) or zones.get(group)
        if not zone:
            continue
        cx, cy = item.center()
        if not (zone[0] <= cx <= zone[2] and zone[1] <= cy <= zone[3]):
            return False
    return True


def search_bedroom(room, beds: list[Placement], wards: list[Placement],
                   variant: int = 0) -> list[list[Placement]]:
    if not beds:
        return []
    bed = beds[0]
    minx, miny, maxx, maxy = _bounds(room)
    found = []
    depths = [None] if not wards else [wards[0].d, 450, 400, 350, 300]
    widths = [None] if not wards else [wards[0].w, 1600, 1400, 1200]
    for depth in depths:
        for width in widths:
            ward = None
            if wards:
                ward_item = _copy(wards[0], w=int(width), d=int(depth))
                if ward_item.w > maxx - minx or ward_item.d > maxy - miny:
                    continue
                ward = _commit(ward_item, (minx, miny, 0))
                if not _group_ok(room, [ward], {"wardrobe"}):
                    continue
            bw, bd = _box(bed, 180)
            y = maxy - bd
            placed_bed = _commit(bed, (minx, int(y), 180))
            group = [placed_bed] + ([ward] if ward is not None else [])
            if _group_ok(room, group, {"wardrobe"}):
                found.append(group)
            # 床头贴北墙，衣柜贴南墙，正面都朝中间那条过道。
            if ward is not None:
                south = _commit(ward, (minx, int(maxy - _box(ward, 180)[1]), 180))
                north_bed = _commit(bed, (minx, miny, 0))
                alt = [north_bed, south]
                if _group_ok(room, alt, {"wardrobe"}):
                    found.append(alt)
            if found and not wards:
                break
        if len(found) >= 3:
            break
    if len(found) < 3:
        bed_poses = wall_poses(room, bed, step=160)[:20]
        ward_item = wards[0] if wards else None
        ward_poses = wall_poses(room, ward_item, step=160)[:16] if ward_item is not None else []
        for pose in bed_poses:
            trial = _commit(bed, pose)
            if not _piece_ok(room, trial, []):
                continue
            if ward_item is None:
                found.append([trial])
            else:
                for wpose in ward_poses:
                    wardrobe = _commit(ward_item, wpose)
                    if _group_ok(room, [trial, wardrobe], {"wardrobe"}):
                        found.append([trial, wardrobe])
                        break
            if len(found) >= 4:
                break
    if not found:
        for factor in (0.85, 0.75):
            smaller = _scale(bed, factor)
            for pose in wall_poses(room, smaller, step=140):
                trial = _commit(smaller, pose)
                if not _piece_ok(room, trial, []):
                    continue
                if wards:
                    added = False
                    for ww in (900, 1000, wards[0].w):
                        if added:
                            break
                        ward_item = _copy(wards[0], w=min(wards[0].w, int(ww)), d=min(wards[0].d, 400))
                        for wpose in wall_poses(room, ward_item, step=140):
                            wardrobe = _commit(ward_item, wpose)
                            if _group_ok(room, [trial, wardrobe], {"wardrobe"}):
                                found.append([trial, wardrobe])
                                added = True
                                break
                else:
                    found.append([trial])
                if len(found) >= 6:
                    break
            if len(found) >= 3 or (not wards and found):
                break
    shift = variant % len(found) if found else 0
    return found[shift:] + found[:shift]


def search_bath(room, toilet, basin, shower, variant: int = 0) -> list[list[Placement]]:
    if not (toilet and basin and shower):
        return []
    showers = corner_poses(room, shower) + wall_poses(room, shower, step=160)
    toilets = wall_poses(room, toilet, step=120)
    basins = wall_poses(room, basin, step=120)
    found = _search_service_trio(room, shower, showers, toilet, toilets, basin, basins,
                                 aisle_cats={"toilet", "basin", "shower"}, limit=8)
    if not found:
        found = _search_service_trio(room, shower, showers, toilet, toilets, basin, basins,
                                     aisle_cats=set(), limit=8)
    found.sort(key=_spread, reverse=True)
    spaced = [group for group in found if _spread(group) >= 250]
    found = spaced or found
    shift = variant % len(found) if found else 0
    return found[shift:] + found[:shift]


def search_kitchen(room, counter, fridge, washer, variant: int = 0) -> list[list[Placement]]:
    if not (counter and fridge and washer):
        return []
    counters = wall_poses(room, counter, step=80)
    fridges = wall_poses(room, fridge, step=80)
    washers = wall_poses(room, washer, step=80)
    found = _search_service_trio(room, counter, counters, fridge, fridges, washer, washers,
                                 aisle_cats={"kitchen_counter", "fridge", "washing_machine"}, limit=3)
    if not found:
        found = _search_service_trio(room, counter, counters, fridge, fridges, washer, washers,
                                     aisle_cats=set(), limit=3)
    shift = variant % len(found) if found else 0
    return found[shift:] + found[:shift]


def _spread(group: list[Placement]) -> float:
    best = 1e9
    for i, item in enumerate(group):
        box = item.footprint()
        for other in group[i + 1:]:
            best = min(best, box.distance(other.footprint()))
    return 0.0 if best == 1e9 else best


def _search_service_trio(room, a, a_poses, b, b_poses, c, c_poses, aisle_cats, limit: int) -> list:
    found = []
    seen = set()
    aisle = bool(aisle_cats)
    for ap in a_poses:
        first = _commit(a, ap)
        if not _piece_ok(room, first, [], aisle=aisle and a.category in aisle_cats):
            continue
        for bp in b_poses:
            second = _commit(b, bp)
            if not _piece_ok(room, second, [first], aisle=aisle and b.category in aisle_cats):
                continue
            for cp in c_poses:
                third = _commit(c, cp)
                group = [first, second, third]
                key = tuple((p.inst, p.x, p.y, p.rot) for p in group)
                if key in seen:
                    continue
                if not _group_ok(room, group, aisle_cats):
                    continue
                seen.add(key)
                found.append(group)
                if len(found) >= limit:
                    return found
    return found


def _dining_at(room, table, chairs, pose, obstacles) -> list[Placement] | None:
    placed = _commit(table, pose)
    if not _piece_ok(room, placed, obstacles):
        return None
    slots = dining_slots(placed)
    if len(chairs) < 4 or len(slots) < 4:
        return None
    group = [placed]
    for chair, slot in zip(chairs[:4], slots):
        seat = _commit(chair, slot)
        if not _piece_ok(room, seat, obstacles + group):
            return None
        group.append(seat)
    if approaches_conflict(room, obstacles + group):
        return None
    return group


def search_dining(room, table, chairs, obstacles, variant: int = 0,
                  scales=(1.0, 0.85, 0.75)) -> list[list[Placement]]:
    if table is None or len(chairs) < 4:
        return []
    minx, miny, maxx, maxy = _bounds(room)
    found = []
    phase = (variant % 5) * 40
    for scale in scales:
        sized = table if scale == 1 else _scale(table, scale)
        for rot in (0, 90, 180, 270):
            bw, bd = _box(sized, rot)
            x = minx + phase
            steps = 0
            while x + bw <= maxx + 1:
                y = miny + (phase if rot % 180 == 0 else 0)
                while y + bd <= maxy + 1:
                    steps += 1
                    if steps > 280:
                        break
                    group = _dining_at(room, sized, chairs, (int(x), int(y), rot), obstacles)
                    if group is not None:
                        found.append(group)
                        if len(found) >= 5:
                            return found
                    y += 220
                if steps > 280:
                    break
                x += 220
            if len(found) >= 5:
                return found
    return found


def search_living(room, sofa, tv, coffee, obstacles, variant: int = 0) -> list[list[Placement]]:
    if not (sofa and tv and coffee):
        return []
    found = []
    tvs = wall_poses(room, tv, step=280)
    if variant % 2:
        tvs = list(reversed(tvs))
    for tp in tvs[:28]:
        tv_p = _commit(tv, tp)
        if not _piece_ok(room, tv_p, obstacles):
            continue
        tx, ty = tv_p.center()
        fx, fy = tv_p.front_dir()
        for dist in (1600, 1900, 2200, 2600, 3000):
            scx, scy = tx + fx * dist, ty + fy * dist
            srot = { (0, -1): 180, (0, 1): 0, (-1, 0): 90, (1, 0): 270 }.get((-fx, -fy))
            if srot is None:
                continue
            bw, bd = _box(sofa, srot)
            sofa_p = _commit(sofa, (int(scx - bw / 2), int(scy - bd / 2), srot))
            if not _piece_ok(room, sofa_p, obstacles + [tv_p]):
                continue
            if not _pair_ok(sofa_p, tv_p, 1400, 4000):
                continue
            mx, my = (scx + tx) / 2.0, (scy + ty) / 2.0
            coffee_p = _commit(coffee, (int(mx - coffee.w / 2), int(my - coffee.d / 2), 0))
            group = [tv_p, sofa_p, coffee_p]
            if not _group_ok(room, group):
                continue
            dot, gap = _pair_faces_coffee(sofa_p, coffee_p)
            if dot < 0.5 or not (400 <= gap <= 1400):
                continue
            found.append(group)
            if len(found) >= 4:
                return found
    return found


def _pair_faces_coffee(sofa, coffee) -> tuple[float, float]:
    from .progressive import _faces
    return _faces(sofa, coffee.center())


def _overlaps(a: list[Placement], b: list[Placement]) -> bool:
    for left in a:
        for right in b:
            if left.footprint().intersection(right.footprint()).area > 100:
                return True
    return False


def _routes_ok(flat, placed: dict) -> bool:
    if unreachable_rooms(flat, placed, person_mm=600):
        return False
    if not artery_clear(flat, placed, min_width=900):
        return False
    if blocked_approaches(flat, placed):
        return False
    return True


def _by_room(flat):
    return {r.id: [] for r in flat.rooms}


def _items_for(items, assignment, room_id, category):
    out = []
    by_inst = {it.inst: it for it in items}
    for inst, rid in assignment.items():
        item = by_inst.get(inst)
        if item is not None and rid == room_id and item.category == category:
            out.append(item)
    return out


def _seed_item(item: Placement, pose, **kw) -> Placement:
    sized = _copy(item, **kw) if kw else item
    return _commit(sized, pose)


def _explicit_seeds(flat, items, assignment, constraints, variant: int):
    """先试几组成套的位置。搜索只是这些位置都不成立时的后备。"""
    rooms = {r.id: r for r in flat.rooms}
    def one(room_id, category):
        found = _items_for(items, assignment, room_id, category)
        return found[0] if found else None

    plans = []
    bed_w = rooms.get("bedroom_w")
    bed_e = rooms.get("bedroom_e")
    bath = rooms.get("bath")
    kitchen = rooms.get("kitchen")
    living = rooms.get("living")
    if not all((bed_w, bath, kitchen, living)):
        return []
    bw = _bounds(bed_w)
    be = _bounds(bed_e) if bed_e is not None else None
    bb, bk, bl = _bounds(bath), _bounds(kitchen), _bounds(living)
    bedw, war = one("bedroom_w", "bed"), one("bedroom_w", "wardrobe")
    bede = one("bedroom_e", "bed") if bed_e is not None else None
    toilet, basin, shower = one("bath", "toilet"), one("bath", "basin"), one("bath", "shower")
    counter, fridge, washer = one("kitchen", "kitchen_counter"), one("kitchen", "fridge"), one("kitchen", "washing_machine")
    sofa, tv, coffee = one("living", "sofa"), one("living", "tv_bench"), one("living", "coffee_table")
    table = one("living", "dining_table")
    chairs = sorted(_items_for(items, assignment, "living", "chair"), key=lambda c: c.inst)
    if not all((bedw, war, toilet, basin, shower, counter, fridge, washer, sofa, tv, coffee, table)) or len(chairs) < 4:
        return []
    factor = _resize_factor(table, constraints, "dining")
    sized_table = _scale(table, factor) if factor else table
    dining_poses = [(4400, 4100, 90), (4500, 4200, 90), (4300, 4071, 90)]
    shift = variant % len(dining_poses)
    dining_poses = dining_poses[shift:] + dining_poses[:shift]
    for pose in dining_poses:
        placed = _by_room(flat)
        bedroom_w = [
            _seed_item(bedw, (bw[0], bw[3] - bedw.d, 180)),
            _seed_item(war, (bw[0], bw[1], 0), w=1400, d=300),
        ]
        bedroom_e = []
        if bede is not None and be is not None:
            bedroom_e = [_seed_item(bede, (be[2] - bede.w, be[1] + 120, 0))]
        bath_group = [
            _seed_item(toilet, (bb[0] + 490, bb[1], 0)),
            _seed_item(basin, (bb[0] + 1210, bb[1], 0)),
            _seed_item(shower, (bb[0], bb[3] - 650, 270), w=650, d=650),
        ]
        kitchen_group = [
            _seed_item(counter, (bk[0], bk[1], 270)),
            _seed_item(fridge, (bk[0] + counter.d, bk[3] - fridge.d, 180)),
            _seed_item(washer, (bk[0] + counter.d + fridge.w, bk[3] - washer.d, 180)),
        ]
        tv_p = _seed_item(tv, (4650, bl[1], 0))
        sofa_p = _seed_item(sofa, (4550, 1680, 180))
        coffee_p = _seed_item(coffee, (4900, 780, 0))
        if not (_group_ok(bed_w, bedroom_w, {"wardrobe"})
                and (not bedroom_e or _group_ok(bed_e, bedroom_e, set()))
                and _group_ok(bath, bath_group, {"toilet", "basin", "shower"})
                and _group_ok(kitchen, kitchen_group, {"kitchen_counter", "fridge", "washing_machine"})
                and _group_ok(living, [tv_p, sofa_p, coffee_p], set())
                and _pair_ok(sofa_p, tv_p, 1400, 4000)):
            continue
        if not all((
            _rot_ok(bedroom_w, constraints, "bedroom", "bed") and _zone_ok(bedroom_w, constraints, "bedroom"),
            _rot_ok(bath_group, constraints, "bath", "toilet") and _zone_ok(bath_group, constraints, "bath"),
            _rot_ok(kitchen_group, constraints, "kitchen", "kitchen_counter") and _zone_ok(kitchen_group, constraints, "kitchen"),
            _rot_ok([sofa_p, tv_p, coffee_p], constraints, "living", "sofa") and _zone_ok([sofa_p, tv_p, coffee_p], constraints, "living"),
        )):
            continue
        dining = _dining_at(living, sized_table, chairs, pose, [tv_p, sofa_p, coffee_p])
        if dining is None or not (_rot_ok(dining, constraints, "dining", "dining_table") and _zone_ok(dining, constraints, "dining")):
            continue
        placed["bedroom_w"] = bedroom_w
        if bedroom_e:
            placed["bedroom_e"] = bedroom_e
        placed["bath"] = bath_group
        placed["kitchen"] = kitchen_group
        placed["living"] = [tv_p, sofa_p, coffee_p] + dining
        plans.append(placed)
        if len(plans) >= 2:
            break
    return plans


def place_groups(flat, items, assignment, constraints=None, variant: int = 0):
    """返回每个房间的一组家具，以及给审计用的笔记。"""
    constraints = constraints or {}
    notes = []
    for plan in _explicit_seeds(flat, items, assignment, constraints, variant):
        if _routes_ok(flat, plan):
            notes.append("seeded functional groups")
            return plan, notes
        notes.append("seed failed route check")
    placed = _by_room(flat)
    rooms = {r.id: r for r in flat.rooms}

    def take(room_id, category):
        return _items_for(items, assignment, room_id, category)

    for room in flat.rooms:
        beds = take(room.id, "bed")
        wards = take(room.id, "wardrobe")
        if not beds:
            continue
        options = search_bedroom(room, beds, wards, variant)
        if wards:
            factor = _resize_factor(wards[0], constraints, "bedroom")
            if factor and factor < 0.99:
                shrunk = [_copy(wards[0], w=max(300, int(wards[0].w * factor)),
                                d=max(250, int(wards[0].d * factor)))]
                options = search_bedroom(room, beds, shrunk, variant) or options
        seed = _seed_of(constraints, "bedroom", *(p.inst for p in beds + wards))
        if not options and wards:
            options = search_bedroom(room, beds, [], variant)
        options = [g for g in options if _rot_ok(g, constraints, "bedroom", "bed") and _zone_ok(g, constraints, "bedroom")]
        options = _ordered(options, variant, seed, _centroid)
        chosen_bed = next(
            (g for g in options if _routes_ok(flat, {**placed, room.id: list(g)})),
            None,
        )
        if chosen_bed is None and wards:
            bed_only = search_bedroom(room, beds, [], variant)
            chosen_bed = next(
                (g for g in bed_only if _routes_ok(flat, {**placed, room.id: list(g)})),
                bed_only[0] if bed_only else None,
            )
        if chosen_bed is not None:
            placed[room.id] = chosen_bed
            notes.append("bedroom %s: %d" % (room.id, len(chosen_bed)))
        else:
            notes.append("bedroom %s: no legal bed group" % room.id)

    bath = rooms.get("bath") or next((r for r in flat.rooms if take(r.id, "toilet")), None)
    bath_opts = []
    if bath is not None:
        toilets = take(bath.id, "toilet")
        basins = take(bath.id, "basin")
        showers = take(bath.id, "shower")
        if toilets and basins and showers:
            bath_opts = search_bath(bath, toilets[0], basins[0], showers[0], variant)
            seed = _seed_of(constraints, "bath", toilets[0].inst, basins[0].inst, showers[0].inst)
            bath_opts = [g for g in bath_opts if _rot_ok(g, constraints, "bath", "toilet") and _zone_ok(g, constraints, "bath")]
            bath_opts = _ordered(bath_opts, variant, seed, _centroid)
            notes.append("bath candidates %d" % len(bath_opts))

    kitchen = rooms.get("kitchen") or next((r for r in flat.rooms if take(r.id, "fridge")), None)
    kit_opts = []
    if kitchen is not None:
        counters = take(kitchen.id, "kitchen_counter")
        fridges = take(kitchen.id, "fridge")
        washers = take(kitchen.id, "washing_machine")
        if counters and fridges and washers:
            factor = _resize_factor(counters[0], constraints, "kitchen")
            counter = _scale(counters[0], factor) if factor else counters[0]
            kit_opts = search_kitchen(kitchen, counter, fridges[0], washers[0], variant)
            seed = _seed_of(constraints, "kitchen", counters[0].inst)
            kit_opts = [g for g in kit_opts if _rot_ok(g, constraints, "kitchen", "kitchen_counter") and _zone_ok(g, constraints, "kitchen")]
            kit_opts = _ordered(kit_opts, variant, seed, _centroid)
            notes.append("kitchen candidates %d" % len(kit_opts))

    living = rooms.get("living") or next((r for r in flat.rooms if r.id == flat.entry_room), None)
    hall_opts = []
    if living is not None:
        sofas = take(living.id, "sofa")
        tvs = take(living.id, "tv_bench")
        coffees = take(living.id, "coffee_table")
        tables = take(living.id, "dining_table")
        chairs = take(living.id, "chair")
        chairs = sorted(chairs, key=lambda c: c.inst)
        factor = _resize_factor(tables[0], constraints, "dining") if tables else None
        scales = (factor,) if factor else (1.0, 0.85, 0.75)
        living_opts = search_living(living, sofas[:1] and sofas[0], tvs[:1] and tvs[0],
                                    coffees[:1] and coffees[0], [], variant) if sofas and tvs and coffees else []
        dining_opts = search_dining(living, tables[0], chairs, [], variant, scales) if tables else []
        seed_l = _seed_of(constraints, "living", *(p.inst for p in sofas[:1]))
        seed_d = _seed_of(constraints, "dining", *(p.inst for p in tables[:1]))
        living_opts = [g for g in living_opts if _rot_ok(g, constraints, "living", "sofa") and _zone_ok(g, constraints, "living")]
        dining_opts = [g for g in dining_opts if _rot_ok(g, constraints, "dining", "dining_table") and _zone_ok(g, constraints, "dining")]
        living_opts = _ordered(living_opts, variant, seed_l, _centroid)
        dining_opts = _ordered(dining_opts, variant + 1, seed_d, _centroid)
        for live in living_opts[:4] or [None]:
            dinings = dining_opts[:4] or [None]
            if live and tables:
                extra = search_dining(living, tables[0], chairs, live, variant, scales)
                extra = [g for g in extra if _rot_ok(g, constraints, "dining", "dining_table") and _zone_ok(g, constraints, "dining")]
                if extra:
                    dinings = extra[:2] + [g for g in dinings[:2] if g is not None]
            for dine in dinings:
                if live and dine and _overlaps(live, dine):
                    continue
                group = list(live or []) + list(dine or [])
                if group:
                    hall_opts.append(group)
        for live in living_opts[:2]:
            hall_opts.append(list(live))
        for dine in dining_opts[:2]:
            hall_opts.append(list(dine))
        notes.append("hall candidates %d" % len(hall_opts))

    best_bath = next(
        (g for g in bath_opts[:3] if _routes_ok(flat, {**placed, bath.id: list(g)})),
        bath_opts[0] if bath_opts else None,
    ) if bath is not None else None
    best_kit = next(
        (g for g in kit_opts[:2] if _routes_ok(flat, {**placed, kitchen.id: list(g)})),
        kit_opts[0] if kit_opts else None,
    ) if kitchen is not None else None
    baths = [best_bath, None] if best_bath else [None]
    kits = [best_kit, None] if best_kit else [None]
    halls = (hall_opts[:8] + [None]) if hall_opts else [None]
    max_possible = sum(len(plist) for plist in placed.values())
    if best_bath:
        max_possible += len(best_bath)
    if best_kit:
        max_possible += len(best_kit)
    if hall_opts:
        max_possible += max(len(g) for g in hall_opts[:8])
    winner = None
    best_n = -1
    for bath_g in baths:
        for kit_g in kits:
            for hall_g in halls:
                trial = {rid: list(plist) for rid, plist in placed.items()}
                if bath is not None and bath_g:
                    trial[bath.id] = list(bath_g)
                if kitchen is not None and kit_g:
                    trial[kitchen.id] = list(kit_g)
                if living is not None and hall_g:
                    trial[living.id] = list(trial.get(living.id, [])) + list(hall_g)
                n_items = sum(len(plist) for plist in trial.values())
                if n_items <= best_n:
                    continue
                if _routes_ok(flat, trial):
                    winner = trial
                    best_n = n_items
                    if best_n >= max_possible:
                        break
            if best_n >= max_possible:
                break
        if best_n >= max_possible:
            break
    if winner is None:
        notes.append("no fully routed group combination")
        winner = placed
        # 厅堂组放不下就留空，不要半套餐桌或半套沙发。
    else:
        notes.append("routed combination with %d pieces" % best_n)
    return winner, notes


def generate_candidates(flat, items, assignment, constraints=None, n: int = 3):
    from .progressive import solve_progressive
    found = []
    seen = set()
    dupes = 0
    for variant in range(n + 2):
        house = solve_progressive(flat, items, assignment, constraints=constraints, variant=variant)
        sig = tuple(sorted(
            (p.inst, p.x, p.y, p.rot, p.w, p.d)
            for lay in house.by_room.values() for p in lay.placements
        ))
        if sig in seen:
            dupes += 1
            if dupes >= 2:
                break
            continue
        dupes = 0
        seen.add(sig)
        found.append(house)
        if len(found) >= n:
            break
    if not found:
        found.append(solve_progressive(flat, items, assignment, constraints=constraints, variant=0))
    return found
