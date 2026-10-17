"""青富苑 B 座 36 室。轮廓按售楼说明书第 23 页的填色和印刷尺寸。

客饭厅是倒 L：上方入口凹位约 2150×2330，左边通高 4525，
右边再深约 1150。浴室 1470×2000 贴在右侧客厅下面，
厨房在浴室右边，约 1690×2065。官方图没有睡房。
"""
from __future__ import annotations

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .onpak05_reference import (
    _as_poly, _attach_openings, _portal, _ring, _walls_bare,
)

# 图纸坐标，y 向下。原点 = 主客厅内墙左上角。
LIVING = [
    (2520, -2330), (4910, -2330), (4910, 0),
    (6215, 0), (6215, 5630),
    (2620, 5630), (2615, 4525),
    (0, 4525), (0, 0),
    (2520, 0),
]
# 85 | 1470 | 85 | 1690 接在客厅底边 2885 之后，总宽 6215。
BATH = [(2970, 5630), (4440, 5630), (4440, 7630), (2970, 7630)]
KITCHEN = [(4525, 5630), (6215, 5630), (6215, 7695), (4525, 7695)]
H_ROOM = 2750


def _furniture() -> list[dict]:
    rows = [
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
    return [dict(inst=i, category=c, w=w, d=d, h=h, room=room)
            for i, c, w, d, h, room in rows]


def build_plan() -> dict:
    living = Polygon(LIVING)
    bath = Polygon(BATH)
    kitchen = Polygon(KITCHEN)
    outline = _as_poly(unary_union([living, bath, kitchen]))
    polys = {"living": living, "bath": bath, "kitchen": kitchen}
    for rid, poly in polys.items():
        if poly.intersection(outline).area < poly.area * 0.98:
            raise ValueError("%s leaves the Ching Fu shell" % rid)
    labels = {"living": "客／飯廳", "bath": "浴室", "kitchen": "廚房"}
    doors = [
        dict(id="d_entry", kind="door", width_mm=900, at=(3715, -2330),
             swing=dict(dir="in", hinge="a")),
        dict(id="d_bath", kind="door", width_mm=750, at=(3705, 5630),
             swing=dict(dir="out", hinge="a"), between=("bath", "living")),
        dict(id="d_kit", kind="door", width_mm=750, at=(5300, 5630),
             swing=dict(dir="out", hinge="b"), between=("kitchen", "living")),
    ]
    order = ["bath", "kitchen", "living"]
    bare = {rid: _walls_bare(rid, polys[rid], outline) for rid in order}
    _attach_openings(bare, [
        dict(id=s["id"], kind="door", width_mm=s["width_mm"], height_mm=2100,
             sill_h_mm=0, swing=s["swing"], at=s["at"])
        for s in doors
    ])
    rooms = []
    for rid in order:
        walls = bare[rid]
        rooms.append(dict(
            id=rid, label=labels[rid], label_source="chingfu_brochure",
            label_score=1.0, polygon=_ring(polys[rid]), holes=[],
            area_mm2=int(round(polys[rid].area)), height_mm=H_ROOM,
            height_field=[], walls=walls,
            openings=[dict(o, wall=w["id"]) for w in walls for o in w["openings"]]))
    connections = []
    portals = []
    for spec in doors:
        if spec["id"] == "d_entry":
            continue
        a, b = spec["between"]
        connections.append(dict(
            a=a, b=b, type="doorway", width_mm=spec["width_mm"],
            door_id=spec["id"], swing=spec["swing"]))
        at, half = spec["at"], spec["width_mm"] / 2.0
        portals.append(_portal((at[0] - half, at[1]), (at[0] + half, at[1])))
    return dict(
        flat_id="plan_hk_chingfu_b36",
        units="mm",
        rooms=rooms,
        connections=connections,
        fixed_services=[],
        entry=dict(room="living", point=[3715, -2100]),
        meta=dict(
            source="hkha_ching_fu_court_block_b_flat_36",
            estate="Ching Fu Court",
            address="18 Tsing Yi Road",
            saleable_m2=42.6,
            saleable_sqft=459,
            outline=_ring(outline),
            program=dict(
                min_counts={"living": 1, "kitchen": 1, "bath": 1},
                min_area_m2={"living": 8.0, "kitchen": 3.0, "bath": 2.5},
                required_links=[["living", "bath"], ["living", "kitchen"]],
                door_sides={},
                furniture=_furniture(),
                artery_mm=900,
                terminal_mm=600,
            ),
            portals=[[[int(round(x)), int(round(y))] for x, y in p.exterior.coords[:-1]]
                     for p in portals],
            provenance="outline traced from the teal living fill on brochure page 23, "
                       "scaled by the printed 2615 mm bay; bath 1470x2000 and kitchen "
                       "1690x2065 sit under the deeper right side",
        ),
    )
