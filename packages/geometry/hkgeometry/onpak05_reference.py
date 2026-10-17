"""安柏苑 05 室的两房参考方案。

官方壳（make_hk_onpak05.py）仍是结构坐标。居屋寶参考图在这个壳里
加了两道隔墙和三扇门，不是另一套被缩放的坐标系。

总尺寸 8745×5970 和壳的 8530×6865 对不上。这里拒绝缩放，
隔墙改钉在两边都有的特征上：西内墙、中央 320 承重墙、饭厅凹位、浴室东墙。
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union


class ExtentMismatch(ValueError):
    """参考图总尺寸和官方壳不一致，而且调用方试图用缩放把它们叠在一起。"""


SHELL_EXTENT_MM = (8530, 6865)          # 凹位东外皮 × 南外皮
REFERENCE_EXTENT_MM = (8745, 5970)      # 居屋寶图上的总宽 × 总深
WALL = 120


def reconcile_extents(shell=SHELL_EXTENT_MM, reference=REFERENCE_EXTENT_MM,
                      scale: float = 1.0) -> dict:
    """记录两套总尺寸。scale 不是 1 就直接拒绝，不许悄悄变换。"""
    if abs(float(scale) - 1.0) > 1e-9:
        raise ExtentMismatch(
            "refusing to scale the HKHA shell (%.0f×%.0f) onto the reference "
            "(%.0f×%.0f); scale=%s" % (shell[0], shell[1], reference[0], reference[1], scale))
    dx = int(shell[0] - reference[0])
    dy = int(shell[1] - reference[1])
    errors = []
    if abs(dx) > 150 or abs(dy) > 150:
        errors.append(
            "overall extents differ by %d×%d mm (shell %d×%d, reference %d×%d); "
            "partitions stay anchored to the shell and are not scaled"
            % (dx, dy, shell[0], shell[1], reference[0], reference[1]))
    return {
        "scale": 1.0,
        "shell_mm": [int(shell[0]), int(shell[1])],
        "reference_mm": [int(reference[0]), int(reference[1])],
        "delta_mm": [dx, dy],
        "errors": errors,
        "status": "extent_mismatch_not_scaled" if errors else "extents_match",
    }


def _shell():
    root = Path(__file__).resolve().parents[3]
    path = root / "services" / "floorplan" / "make_hk_onpak05.py"
    spec = importlib.util.spec_from_file_location("hk_onpak_shell", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _as_poly(g) -> Polygon:
    if g.geom_type == "Polygon":
        return g
    if g.geom_type == "MultiPolygon":
        return max(g.geoms, key=lambda p: p.area)
    raise ValueError("expected a room polygon, got %s" % g.geom_type)


def _ring(poly: Polygon) -> list[tuple[int, int]]:
    pts = [(int(round(x)), int(round(y))) for x, y in poly.exterior.coords]
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _point_on_segment(pt, a, b, tol=90.0) -> bool:
    ax, ay = a
    bx, by = b
    px, py = pt
    abx, aby = bx - ax, by - ay
    L2 = abx * abx + aby * aby
    if L2 < 1:
        return math.hypot(px - ax, py - ay) <= tol
    t = ((px - ax) * abx + (py - ay) * aby) / L2
    if t < -0.05 or t > 1.05:
        return False
    qx, qy = ax + abx * t, ay + aby * t
    return math.hypot(px - qx, py - qy) <= tol


def _dist_along(a, b, pt) -> int:
    return int(round(math.hypot(pt[0] - a[0], pt[1] - a[1])))


def _opening_point(wall, center_mm) -> tuple[float, float]:
    ax, ay = wall["a"]
    bx, by = wall["b"]
    L = max(1.0, math.hypot(bx - ax, by - ay))
    t = float(center_mm) / L
    return (ax + (bx - ax) * t, ay + (by - ay) * t)


def _legacy_openings(shell_doc):
    """官方壳上要留下来的门窗（浴室南门除外，参考图把它改到东墙）。"""
    keep = []
    for room in shell_doc["rooms"]:
        walls = {w["id"]: w for w in room["walls"]}
        for op in room["openings"]:
            if op["id"] in ("d_bath_liv", "d_kit"):
                continue
            if op["kind"] == "door" and op["id"] not in ("d_entry", "d_kit"):
                continue
            wall = walls.get(op.get("wall", ""))
            if wall is None:
                continue
            keep.append((op, _opening_point(wall, op["center_mm"])))
    return keep


def _walls_bare(room_id: str, poly: Polygon, outline: Polygon) -> list[dict]:
    coords = list(poly.exterior.coords)
    walls = []
    for i in range(len(coords) - 1):
        a = (int(round(coords[i][0])), int(round(coords[i][1])))
        b = (int(round(coords[i + 1][0])), int(round(coords[i + 1][1])))
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 40:
            continue
        mid = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        on_shell = outline.boundary.distance(mid) < 160
        walls.append(dict(
            id="w_%s_%d" % (room_id, i), a=list(a), b=list(b),
            type="load_bearing" if on_shell else "partition",
            thickness_mm=250 if on_shell else WALL,
            movable=(not on_shell), openings=[]))
    return walls


def _seg_distance(pt, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = pt
    abx, aby = bx - ax, by - ay
    L2 = abx * abx + aby * aby
    if L2 < 1:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / L2))
    qx, qy = ax + abx * t, ay + aby * t
    return math.hypot(px - qx, py - qy)


def _attach_openings(rooms_walls: dict[str, list[dict]], openings: list[dict]) -> None:
    """一个开口只挂到真正贴着它的墙。两边都贴着（共用边）才挂两次。"""
    for op in openings:
        hits = []
        for rid, walls in rooms_walls.items():
            for w in walls:
                dist = _seg_distance(op["at"], w["a"], w["b"])
                if dist <= 45:
                    hits.append((dist, rid, w))
        if not hits:
            if op.get("kind") == "window":
                continue
            raise ValueError("opening %s did not land on a wall at %s" % (op["id"], op["at"]))
        hits.sort(key=lambda h: h[0])
        best = hits[0][0]
        chosen = [h for h in hits if h[0] <= best + 15]
        # 同一面墙只留最近的一段
        seen = set()
        for dist, rid, w in chosen:
            if w["id"] in seen:
                continue
            seen.add(w["id"])
            w["openings"].append(dict(
                id=op["id"], kind=op["kind"], wall=w["id"],
                center_mm=_dist_along(w["a"], w["b"], op["at"]),
                width_mm=op["width_mm"],
                height_mm=op.get("height_mm", 2100 if op["kind"] == "door" else 1350),
                sill_h_mm=op.get("sill_h_mm", 0 if op["kind"] == "door" else 900),
                swing=op.get("swing")))


def _portal(a, b, thickness=WALL, reach=None) -> Polygon:
    """门洞矩形，向两侧各探进房间，让连通性检查只从门穿过。"""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    span = thickness / 2.0 + 220.0 if reach is None else float(reach)
    return Polygon([
        (ax + nx * span, ay + ny * span),
        (bx + nx * span, by + ny * span),
        (bx - nx * span, by - ny * span),
        (ax - nx * span, ay - ny * span),
    ])


def _cuts(S, kind: str) -> dict[str, Polygon]:
    """kind = reference | alternate | blocked。坐标都是官方壳的毫米。"""
    north_y0 = S.Y["north_in"]
    north_y1 = S.BY0 - S.WALL_THIN          # 3074，浴室北侧
    west = S.X["west_in"]                   # 1560
    spine = S.X["spine_w"]                  # 3660
    east = S.X["east_in"]                   # 6180
    if kind == "unit03":
        # 03 室是 408 呎的 C 组户型：西侧厨厕和 05 室同一条尺寸链，
        # 东侧没有饭厅凹位，所以只切一间睡房。
        return {
            "bedroom_w": box(west, north_y0, spine, north_y1),
        }
    if kind == "reference":
        return {
            "bedroom_w": box(west, north_y0, spine, north_y1),
            "bedroom_e": box(S.X["nook_w"], S.Y["nook_top"],
                             S.X["nook_e"] - S.WALL_EXT, S.Y["nook_btm"]),
        }
    if kind == "alternate":
        # 两间睡房都留在北翼，各自不少于 5m²，中间留走廊。饭厅凹位仍是客厅。
        return {
            "bedroom_w": box(west, north_y0, 3400, north_y1),
            "bedroom_e": box(4200, north_y0, east, north_y1),
        }
    if kind == "blocked":
        # 在入户厅横一道没有门的墙，入口走不到北翼。
        cuts = _cuts(S, "reference")
        cuts["block"] = box(3000, 5000, 6180, 5000 + WALL)
        return cuts
    raise ValueError(kind)


def _door_specs(kind: str, S) -> list[dict]:
    north_y1 = S.BY0 - S.WALL_THIN
    if kind in ("reference", "unit03"):
        specs = [
            # 西睡房：东隔墙靠南的门，门扇向房内（西）开。
            dict(id="d_bed_w", kind="door", width_mm=800, height_mm=2100,
                 at=(S.X["spine_w"], 2600), axis="v",
                 swing=dict(dir="out", hinge="b"),
                 between=("bedroom_w", "living")),
            dict(id="d_bed_e", kind="door", width_mm=800, height_mm=2100,
                 at=(S.X["nook_w"], 2900), axis="v",
                 swing=dict(dir="out", hinge="a"),
                 between=("bedroom_e", "living")),
            dict(id="d_bath", kind="door", width_mm=750, height_mm=2100,
                 at=(S.BX1, 3600), axis="v", reach=320,
                 swing=dict(dir="out", hinge="a"),
                 between=("bath", "living")),
            dict(id="d_kit", kind="door", width_mm=800, height_mm=2100,
                 at=(S.X["kit_e"], S.Y["kit_n"] + 1000), axis="v", reach=560,
                 swing=dict(dir="out", hinge="a"),
                 between=("kitchen", "living")),
        ]
        if kind == "unit03":
            specs = [s for s in specs if s["id"] != "d_bed_e"]
        return specs
    if kind == "alternate":
        return [
            dict(id="d_bed_w", kind="door", width_mm=800, height_mm=2100,
                 at=(3400, 2200), axis="v",
                 swing=dict(dir="in", hinge="a"),
                 between=("bedroom_w", "living")),
            dict(id="d_bed_e", kind="door", width_mm=800, height_mm=2100,
                 at=(4200, 1800), axis="v",
                 swing=dict(dir="in", hinge="b"),
                 between=("bedroom_e", "living")),
            dict(id="d_bath", kind="door", width_mm=750, height_mm=2100,
                 at=(S.BX1, 3600), axis="v", reach=320,
                 swing=dict(dir="out", hinge="a"),
                 between=("bath", "living")),
            dict(id="d_kit", kind="door", width_mm=800, height_mm=2100,
                 at=(S.X["kit_e"], S.Y["kit_n"] + 1000), axis="v", reach=560,
                 swing=dict(dir="out", hinge="a"),
                 between=("kitchen", "living")),
        ]
    if kind == "blocked":
        # 有睡房门，但入户厅被无门的横墙切断，连通性必须失败。
        return _door_specs("reference", S)
    raise ValueError(kind)


def build_plan(kind: str = "reference") -> dict:
    S = _shell()
    shell_doc = S.build()
    recon = reconcile_extents()
    liv0 = Polygon(S.LIV)
    outline = Polygon(S.OUTLINE)
    if kind == "unit03":
        # 官方图上 03 室东墙止于外墙，没有 05 室伸出的饭厅凹位。
        keep = box(-200, -200, S.X["east_out"] + 30, 9000)
        liv0 = _as_poly(liv0.intersection(keep))
        outline = _as_poly(outline.intersection(keep))
    cuts = _cuts(S, kind)
    room_cuts = {k: v for k, v in cuts.items() if k != "block"}
    room_cuts = {k: v for k, v in cuts.items() if k != "block"}
    cut_union = unary_union(list(room_cuts.values()))
    living_g = _as_poly(liv0.difference(cut_union))
    if kind == "blocked":
        # 入户一侧（图纸 +y 向下，南边 y 更大）被无门横墙切断。
        south = living_g.intersection(box(0, 5120, 20000, 9000))
        living_g = _as_poly(south)
    bedrooms = {rid: _as_poly(poly.intersection(liv0)) for rid, poly in room_cuts.items()}

    shell_cover = outline.buffer(80)
    for rid, poly in list(bedrooms.items()) + [("living", living_g)]:
        if poly.area < 1e6:
            raise ValueError("%s collapsed to %.2f m2" % (rid, poly.area / 1e6))
        cover = shell_cover.intersection(poly).area / poly.area
        if cover < 0.98:
            raise ValueError("%s is not inside the HKHA shell (cover %.3f)" % (rid, cover))

    extra = _door_specs(kind, S)
    legacy = _legacy_openings(shell_doc)
    # 每个开口只挂到它真正落在的那条边上。
    by_id = {}
    for op, pt in legacy:
        if op["id"] in by_id:
            continue
        if kind == "unit03" and pt[0] > S.X["east_in"] + 80:
            continue
        by_id[op["id"]] = dict(
            id=op["id"], kind=op["kind"], width_mm=op["width_mm"],
            height_mm=op.get("height_mm", 2100),
            sill_h_mm=op.get("sill_h_mm", 0),
            swing=op.get("swing"), at=pt)
    for spec in extra:
        by_id[spec["id"]] = dict(spec)
    pending = list(by_id.values())

    polys = {
        "living": living_g,
        "bedroom_w": bedrooms["bedroom_w"],
        "bath": Polygon(S.BATH),
        "kitchen": Polygon(S.KIT),
    }
    if kind != "unit03":
        polys["bedroom_e"] = bedrooms["bedroom_e"]
    labels = {
        "bedroom_w": "主人房", "bedroom_e": "客房", "bath": "浴室",
        "kitchen": "廚房", "living": "客／飯廳",
    }
    order = ["bedroom_w", "bath", "kitchen", "living"]
    if kind != "unit03":
        order = ["bedroom_w", "bedroom_e", "bath", "kitchen", "living"]
    rooms = []
    bare = {}
    for rid in order:
        poly = polys[rid]
        bare[rid] = _walls_bare(rid, poly, outline)
    _attach_openings(bare, pending)
    for rid in order:
        poly = polys[rid]
        walls = bare[rid]
        rooms.append(dict(
            id=rid, label=labels[rid], label_source="reference_design" if kind != "blocked" else "blocked_probe",
            label_score=1.0, polygon=_ring(poly), holes=[],
            area_mm2=int(round(poly.area)), height_mm=S.H_ROOM,
            height_field=[], walls=walls, openings=[]))

    for room in rooms:
        room["openings"] = [dict(o, wall=w["id"]) for w in room["walls"] for o in w["openings"]]

    connections = []
    seen = set()
    for spec in extra:
        a, b = spec["between"]
        key = tuple(sorted((a, b, spec["id"])))
        if key in seen:
            continue
        seen.add(key)
        connections.append(dict(
            a=a, b=b, type="doorway", width_mm=spec["width_mm"],
            door_id=spec["id"], swing=spec["swing"]))
    portals = []
    for spec in extra:
        at = spec["at"]
        half = spec["width_mm"] / 2.0
        reach = spec.get("reach")
        if spec.get("axis") == "v":
            portals.append(_portal((at[0], at[1] - half), (at[0], at[1] + half), reach=reach))
        else:
            portals.append(_portal((at[0] - half, at[1]), (at[0] + half, at[1]), reach=reach))

    dining = [[4300, 5300], [5900, 5300], [5900, 6400], [4300, 6400]]
    bedrooms_required = 1 if kind == "unit03" else 2
    links = [["living", "bedroom_w"], ["living", "bath"], ["living", "kitchen"]]
    door_sides = {
        "d_bath": {"room": "bath", "side": "east"},
        "d_kit": {"room": "kitchen", "side": "east"},
        "d_bed_w": {"room": "bedroom_w", "side": "east"},
    }
    if kind != "unit03":
        links.append(["living", "bedroom_e"])
        door_sides["d_bed_e"] = {"room": "bedroom_e", "side": "west"}
    program = {
        "min_counts": {"bedroom": bedrooms_required, "living": 1, "kitchen": 1, "bath": 1},
        "min_area_m2": {"bedroom": 5.0, "living": 10.0, "kitchen": 3.0, "bath": 2.5},
        "required_links": links,
        "door_sides": door_sides,
        "doors": [{k: spec[k] for k in ("id", "between", "swing")} | {"at": list(spec["at"])}
                  for spec in extra],
        "zones": {"dining": dining},
        "furniture": _furniture(guest=kind != "unit03"),
        "artery_mm": 900,
        "terminal_mm": 600,
    }
    return dict(
        flat_id="plan_hk_onpak03" if kind == "unit03" else "plan_hk_onpak05_%s" % kind,
        units="mm",
        rooms=rooms,
        connections=connections,
        fixed_services=[],
        entry=dict(room="living", point=[4700, S.Y["y4"] - 300]),
        meta=dict(
            source="housingpro_two_bedroom_anchored_to_hkha_shell",
            kind=kind,
            reserve_artery=True,
            reconciliation=recon,
            outline=_ring(outline),
            program=program,
            portals=[[ [int(round(x)), int(round(y))] for x, y in p.exterior.coords[:-1]]
                     for p in portals],
            provenance="partitions and relocated bathroom door come from the Housing Pro "
                       "05 layout; coordinates are shell anchors, not a scaled overlay",
        ))


def _furniture(guest: bool = True) -> list[dict]:
    """参考图上读到的件数。尺寸是图上标注，不是求解器事后凑的。"""
    rows = [
        ("#bed_w", "bed", 1400, 1900, 500, "bedroom_w"),
        ("#wardrobe_w", "wardrobe", 1800, 550, 2200, "bedroom_w"),
    ]
    if guest:
        rows.append(("#bed_e", "bed", 900, 1900, 500, "bedroom_e"))
    rows += [
        ("#sofa", "sofa", 1400, 700, 750, "living"),
        ("#tv", "tv_bench", 1200, 400, 450, "living"),
        ("#coffee", "coffee_table", 700, 700, 400, "living"),
        ("#dining", "dining_table", 1600, 800, 750, "living"),
        ("#chair1", "chair", 450, 450, 780, "living"),
        ("#chair2", "chair", 450, 450, 780, "living"),
        ("#chair3", "chair", 450, 450, 780, "living"),
        ("#chair4", "chair", 450, 450, 780, "living"),
        ("#toilet", "toilet", 700, 400, 750, "bath"),
        ("#basin", "basin", 600, 450, 850, "bath"),
        ("#shower", "shower", 900, 900, 2000, "bath"),
        ("#counter", "kitchen_counter", 1700, 600, 850, "kitchen"),
        ("#fridge", "fridge", 600, 650, 1700, "kitchen"),
        ("#washer", "washing_machine", 600, 600, 850, "kitchen"),
    ]
    return [dict(inst=i, category=c, w=w, d=d, h=h, room=room)
            for i, c, w, d, h, room in rows]


def build_unit03() -> dict:
    return build_plan("unit03")


def build_reference() -> dict:
    return build_plan("reference")


def build_alternate() -> dict:
    return build_plan("alternate")


def build_blocked() -> dict:
    return build_plan("blocked")
