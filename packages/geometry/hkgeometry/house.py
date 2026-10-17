"""整屋三层分解求解 —— 技术方案 v3.2 §3.1。

  Level 1  房间功能分配（哪件家具进哪个房）     小规模 CP-SAT，秒级
  Level 2  房间内布局（每间房独立 CP-SAT）      ★ 多进程并行
  Level 3  跨房间校验（动线/门洞/可达性）        图论 + 几何，毫秒级

为什么不能把整屋塞进一个求解器：变量数爆炸，一定超时。
分层之后每层都可解，层间耦合靠 Level 3 的违规反馈 + 定向重解。
"""
from __future__ import annotations

import json
import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import shapely
from shapely.geometry import LineString, Point

from .corridor import default_corridors
from .exposure import ExposureMap, exposure_map, layout_visual_load
from .greedy import GreedyWeights, door_approach_zones
from .spaciousness import spaciousness_report
from .types import Layout, Opening, Placement, Room, Service, Wall

# ---------------------------------------------------------------- 房间功能
LABEL_TO_FUNC = {
    "客廳": "living", "客廳/飯廳": "living", "客／飯廳": "living", "客/飯廳": "living",
    "飯廳": "living", "客厅": "living",
    "living": "living", "起居室": "living",
    "睡房": "bedroom", "主人房": "bedroom", "客房": "bedroom", "卧室": "bedroom",
    "bedroom": "bedroom", "書房": "bedroom",
    "廚房": "kitchen", "厨房": "kitchen", "kitchen": "kitchen",
    "浴室": "bath", "廁所": "bath", "bath": "bath", "浴室/廁所": "bath",
    "儲物房": "storage", "雜物房": "storage", "storage": "storage", "儲藏室": "storage",
}

# 品类 -> 允许的房间功能
CATEGORY_ROOMS = {
    "bed": {"bedroom"}, "bunk_bed": {"bedroom"}, "loft_bed": {"bedroom"},
    "storage_bed": {"bedroom"},
    "wardrobe": {"bedroom", "storage"},
    "desk": {"bedroom", "living", "storage"},
    "chair": {"living", "bedroom", "kitchen"},
    "sofa": {"living"}, "tv_bench": {"living"}, "coffee_table": {"living"},
    "dining_table": {"living", "kitchen"}, "side_table": {"living", "bedroom"},
    "fridge": {"kitchen"}, "washing_machine": {"kitchen", "bath"},
    "microwave": {"kitchen"},
    "cabinet": {"kitchen", "living", "storage"},
    "shoe_cabinet": {"living"}, "drawer_chest": {"bedroom", "living"},
    "shelf": {"living", "bedroom", "storage", "kitchen"},
    "storage_box": {"living", "bedroom", "storage", "kitchen"},
    "wall_shelf": {"living", "bedroom", "storage"},
    "mirror": {"living", "bedroom", "bath"},
    "screen": {"living", "bedroom"}, "pegboard": {"living", "bedroom", "storage"},
    "drying_rack": {"living", "bath", "kitchen", "storage"},
    "aircon": {"living", "bedroom"},
    # ★ 固定洁具：由來去水位 / 煤氣喉钉死，只能进厨房和浴室
    "toilet": {"bath"}, "basin": {"bath"}, "bathtub": {"bath"}, "shower": {"bath"},
    "stove": {"kitchen"}, "range_hood": {"kitchen"}, "kitchen_counter": {"kitchen"},
}

# ★ 開放式單位（studio）。香港的納米樓／唐樓套間，同 Mies 的 Farnsworth House
#   一樣：全屋就係一個大空間 + 一個拆唔走嘅服務核心，睡房／廚房／客廳只係
#   區域（zone），唔係房間。用「切成幾間房」去建模會把連通性同曝光度一齊
#   切死（見 Room.holes 的說明），所以這裡讓所有品類都准入 "open"。
LABEL_TO_FUNC.update({
    "開放式": "open", "開放式單位": "open", "開放式空間": "open",
    "studio": "open", "open": "open", "開放起居空間": "open",
})
for _v in CATEGORY_ROOMS.values():
    _v.add("open")

# ★ 可以「借住」其他房間的品類。
#   為什麼：plan_hk_b 的廚房只有 2.4m²、浴室 1.7m²，而客廳 26.7m² ——
#   洗衣機／微波爐／層架／儲物箱這類東西，硬要留在「功能上正確」的房間，
#   結果就是 L2 全部放唔落、憑空消失。香港現實也一樣：洗衣機放廚房檯面下、
#   放浴室、放露台、甚至放廳角都有。它們只是**偏好**那間房，不是必須。
#   （床、馬桶、灶台、雪櫃這些則相反 —— 它們定義了那間房的功能，不許搬家。）
FLEXIBLE_CATEGORIES = {
    "washing_machine", "drying_rack", "storage_box", "shelf", "cabinet",
    "microwave", "side_table", "mirror", "desk", "chair", "screen",
    "shoe_cabinet", "storage_bed", "drawer_chest", "pegboard", "wall_shelf",
}
# 借住時不考慮的房間功能（書桌搬入浴室之類的荒謬結果）
BORROW_BAN = {"bath"}

# 每个功能房间的默认偏好权重（数值越大越优先）
DEFAULT_PREF = {
    "bed": {"bedroom": 10}, "wardrobe": {"bedroom": 10, "storage": 5},
    "sofa": {"living": 10}, "tv_bench": {"living": 10},
    "coffee_table": {"living": 10}, "fridge": {"kitchen": 10},
    # 香港的洗衣机多数在厨房（台面下）或浴室；4.5m² 的浴室塞不下全部洁具
    "washing_machine": {"kitchen": 9, "bath": 5},
    "desk": {"bedroom": 9, "living": 4, "storage": 3},
    "side_table": {"bedroom": 8, "living": 5},
    "dining_table": {"living": 8, "kitchen": 5},
    "shelf": {"living": 5, "storage": 6, "bedroom": 4, "kitchen": 3},
    "storage_box": {"living": 4, "storage": 7, "bedroom": 4, "kitchen": 3},
    "cabinet": {"kitchen": 7, "living": 5, "storage": 5},
    "shoe_cabinet": {"living": 9},
    "drying_rack": {"living": 8, "storage": 6, "bath": 3, "kitchen": 3},
    "mirror": {"bath": 7, "living": 5, "bedroom": 4},
    "screen": {"living": 6, "bedroom": 4},
    "chair": {"living": 6, "bedroom": 4, "kitchen": 3},
    "wall_shelf": {"living": 5, "bedroom": 4, "storage": 4},
    "pegboard": {"living": 5, "bedroom": 4, "storage": 4},
    "aircon": {"living": 6, "bedroom": 6},
    "drawer_chest": {"bedroom": 7, "living": 4},
    "microwave": {"kitchen": 9},
    "toilet": {"bath": 10}, "basin": {"bath": 10}, "bathtub": {"bath": 8},
    "shower": {"bath": 9},
    "stove": {"kitchen": 10}, "range_hood": {"kitchen": 9},
    "kitchen_counter": {"kitchen": 10},
    "bunk_bed": {"bedroom": 8}, "loft_bed": {"bedroom": 8},
    "storage_bed": {"bedroom": 8},
}
# 開放式單位裡沒有房間可以「偏好」—— 全部品類對 open 一視同仁。
for _v in DEFAULT_PREF.values():
    _v.setdefault("open", 4)


@dataclass
class Connection:
    a: str
    b: str
    width_mm: int = 800
    kind: str = "doorway"
    door_id: str = ""
    swing: dict | None = None


@dataclass
class Flat:
    id: str
    rooms: list[Room]
    connections: list[Connection]
    entry_room: str
    entry_point: tuple[int, int]
    meta: dict = field(default_factory=dict)

    def room(self, rid: str) -> Room:
        return next(r for r in self.rooms if r.id == rid)

    def functions(self) -> dict[str, str]:
        return infer_room_functions(self)

    def total_area_m2(self) -> float:
        return sum(r.geo().area for r in self.rooms) / 1e6


@dataclass
class HouseLayout:
    flat_id: str
    by_room: dict[str, Layout] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    reports: dict[str, object] = field(default_factory=dict)
    assignment: dict[str, str] = field(default_factory=dict)
    unplaced: list[str] = field(default_factory=list)


@dataclass
class HouseReport:
    ok: bool = True
    per_room: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    reachable: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    door_blocked: dict = field(default_factory=dict)
    house_visual_load: float = 0.0
    n_placed: int = 0
    n_items: int = 0
    score: float = 0.0


# ---------------------------------------------------------------- 加载
def flat_from_dict(d: dict) -> Flat:
    rooms = []
    allpts = []
    for r in d["rooms"]:
        allpts += [(int(x), int(y)) for x, y in r["polygon"]]
    minx = min(p[0] for p in allpts)
    miny = min(p[1] for p in allpts)

    def T(p, flip=True):
        return (int(p[0]) - minx, (-int(p[1]) if flip else int(p[1])))

    # 统一坐标：图纸 +y 向下 -> 翻成 +y 向上，平移到原点
    tmp = []
    for r in d["rooms"]:
        poly = [T(p) for p in r["polygon"]]
        tmp.append((r, poly))
    ally = [p[1] for _r, poly in tmp for p in poly]
    ymin = min(ally)

    for r, poly in tmp:
        poly = [(x, y - ymin) for x, y in poly]
        walls = []
        for w in r.get("walls", []):
            a = T(w["a"]); b = T(w["b"])
            walls.append(Wall(w["id"], (a[0], a[1] - ymin), (b[0], b[1] - ymin),
                              w.get("type", "partition"),
                              int(w.get("thickness_mm", 100) or 100),
                              bool(w.get("movable", False))))
        ops = [Opening(o["id"], o.get("wall", ""), o.get("kind", "door"),
                       int(o.get("center_mm", 0) or 0),
                       int(o.get("width_mm", 800) or 800),
                       int(o.get("height_mm", 2000) or 2000),
                       int(o.get("sill_h_mm", 0) or 0),
                       int(o.get("sill_depth_mm", 0) or 0),
                       swing=o.get("swing"))
               for o in r.get("openings", [])]
        holes = [[T(p) for p in h] for h in r.get("holes", [])]
        holes = [[(x, y - ymin) for x, y in h] for h in holes]
        rooms.append(Room(id=r["id"], label=r.get("label", ""), polygon=poly,
                          height_mm=int(r.get("height_mm", 2450) or 2450),
                          walls=walls, openings=ops, holes=holes))
    conns = [Connection(c["a"], c["b"], int(c.get("width_mm", 800) or 800),
                        c.get("type", c.get("kind", "doorway")),
                        c.get("door_id", "") or "",
                        c.get("swing"))
             for c in d.get("connections", [])]
    ent = d.get("entry", {}) or {}
    ep = T(ent.get("point", [0, 0]))
    meta = dict(d.get("meta", {}) or {})

    def M(p):
        q = T(p)
        return [int(q[0]), int(q[1] - ymin)]

    if meta.get("portals"):
        meta["portals"] = [[M(p) for p in ring] for ring in meta["portals"]]
    if meta.get("outline"):
        meta["outline"] = [M(p) for p in meta["outline"]]
    program = meta.get("program") or {}
    zones = program.get("zones") or {}
    if zones:
        program = dict(program)
        program["zones"] = {k: [M(p) for p in ring] for k, ring in zones.items()}
        meta["program"] = program
    _attach_services(d, rooms, T, ymin)
    return Flat(id=d.get("flat_id", "flat"), rooms=rooms, connections=conns,
                entry_room=ent.get("room", rooms[0].id),
                entry_point=(int(ep[0]), int(ep[1] - ymin)), meta=meta)


def _attach_services(d: dict, rooms: list[Room], transform, ymin: int) -> None:
    """上传图里的固定洁具要进房间，不能在读入时丢掉。"""
    for raw in d.get("fixed_services") or []:
        if not isinstance(raw, dict):
            continue
        pt = raw.get("point") or raw.get("at")
        if not pt or len(pt) < 2:
            continue
        mapped = transform(pt)
        point = (int(mapped[0]), int(mapped[1] - ymin))
        host = next((room for room in rooms if room.id == raw.get("room")), None)
        if host is None:
            host = next((room for room in rooms if room.geo().buffer(80).covers(Point(point))), None)
        if host is None:
            continue
        host.services.append(Service(
            id=str(raw.get("id") or "service"),
            kind=str(raw.get("kind") or "service"),
            point=point,
            room=host.id,
        ))


def load_flat(path: str) -> Flat:
    with open(path, encoding="utf-8") as f:
        return flat_from_dict(json.load(f))


def infer_room_functions(flat: Flat) -> dict[str, str]:
    """先从房名推断；没有房名就按面积排名猜（可解释的兜底）。"""
    funcs: dict[str, str] = {}
    used = set()
    for r in flat.rooms:
        lab = (r.label or "").strip()
        f = LABEL_TO_FUNC.get(lab)
        if f is None:
            f = LABEL_TO_FUNC.get(r.id.lower())
        if f:
            # 房名写明了就照用。睡房可以有两间，不能因为第一间占了
            # "bedroom" 就把第二间丢掉。
            funcs[r.id] = f
            used.add(f)
    # 剩下的按面积从大到小补：living -> bedroom -> kitchen -> bath -> storage
    rest = [r for r in flat.rooms if r.id not in funcs]
    rest.sort(key=lambda r: -r.geo().area)
    order = ["living", "bedroom", "kitchen", "bath", "storage"]
    for r in rest:
        for f in order:
            if f not in used:
                funcs[r.id] = f
                used.add(f)
                break
        else:
            funcs[r.id] = "storage"
    return funcs


# ---------------------------------------------------------------- Level 1
def assign_rooms(flat: Flat, items: list[Placement],
                 area_ratio: float = 0.55, verbose: bool = False):
    """Level 1：房间功能分配。返回 {inst: room_id}。"""
    from ortools.sat.python import cp_model

    funcs = infer_room_functions(flat)
    by_func: dict[str, list[Room]] = {}
    for r in flat.rooms:
        by_func.setdefault(funcs[r.id], []).append(r)

    model = cp_model.CpModel()
    a = {}
    allowed_map = {}
    unassigned = {}
    over_vars = []
    for it in items:
        allowed_funcs = CATEGORY_ROOMS.get(it.category)
        cands = [r for r in flat.rooms
                 if (allowed_funcs is None or funcs[r.id] in allowed_funcs)]
        # 尺寸硬约束：不仅要塞得进，还要**留得下 600mm 通道**。
        # 储物房只有 1030mm 宽，放 900mm 宽的书架之后人就过不去了 ——
        # 这种房间宁可让家具去别处，也不要摆出一个走不进去的方案。
        def _bounds(r):
            minx, miny, maxx, maxy = r.usable_geo().bounds
            return maxx - minx, maxy - miny

        def dims_ok(r):
            rw, rd = _bounds(r)
            return (it.w <= rw and it.d <= rd) or (it.w <= rd and it.d <= rw)

        def walk_ok(r):
            rw, rd = _bounds(r)
            short, long_ = min(it.w, it.d), max(it.w, it.d)
            room_short, room_long = min(rw, rd), max(rw, rd)
            return ((short + 600 <= room_short and long_ <= room_long) or
                    (long_ + 600 <= room_long and short <= room_short))

        strict = [r for r in cands if dims_ok(r) and walk_ok(r)]
        relaxed = [r for r in cands if dims_ok(r)]
        ok = strict or relaxed                        # 放寬「要留 600mm 通道」
        # ★ 只有在**連放寬版都冇**（偏好房連尺寸都塞唔落）時，才去其他房借位。
        #   一開始寫成「凡係 FLEXIBLE 就一律加借位候選」，靠權重壓住 ——
        #   結果 plan_hk_a 的晾衫架被容量罰分推去睡房（權重 100 vs 800 只差 700 分，
        #   而客廳超容量一格就係 1 分/1000mm²，好易反超），
        #   睡房因此多咗一件、「走唔到」硬傷由 0 變 1。借位係例外，唔係常態。
        if not strict and it.category in FLEXIBLE_CATEGORIES:
            ok = ok + [r for r in flat.rooms
                       if r not in ok and funcs[r.id] not in BORROW_BAN
                       and dims_ok(r)]
        if not ok:
            ok = cands          # 实在没有就放宽，让 Level 2 去失败
        allowed_map[it.inst] = ok
        for r in ok:
            a[(it.inst, r.id)] = model.NewBoolVar("a_%s_%s" % (it.inst, r.id))
        # ★ 每件家具都有一個「邊度都去唔到」的出口，重罰但**存在**。
        #   為什麼：以前只有「一件房都冇得揀」嘅家具才有出口，其餘一律
        #   AddExactlyOne。結果係細單位（plan_hk_b：浴室 1.7m²、廚房 2.4m²）
        #   房間容量一爆，整個模型 INFEASIBLE，solver.Value() 對未解出嘅模型
        #   亂答一餐 —— 實測 23 件只分配到 8 件，**15 件憑空消失**，
        #   而且連一句顧問意見都冇。L1 永遠唔應該 INFEASIBLE：
        #   分配唔到就明講「呢件冇房放得落」，交返俾顧問層去講。
        un = model.NewBoolVar("un_%s" % it.inst)
        model.AddExactlyOne([a[(it.inst, r.id)] for r in ok] + [un])
        unassigned[it.inst] = un

    # 容量：每间房的家具体量不超过可用面积的一定比例。
    # ★ 超容量改成**软罚**而不是硬约束 —— 硬约束会直接 INFEASIBLE。
    #   面積以 1000mm² 為單位（= 10cm²），罰分 1 分 = 10cm² 超標。
    #   為什么唔可以繼續用硬約束：plan_hk_b 睡房可用 4.8m²，
    #   0.55 比例 = 2.64m²，而一張雙人床本身就 3.26m² —— 硬約束下
    #   求解器唯一嘅出路係「唔擺張床」，於是輸出一個冇床嘅睡房。
    #   寧可超標，都唔可以靜靜雞少一件家具。
    for r in flat.rooms:
        tot = int(r.usable_geo().area * area_ratio) // 1000
        terms = []
        for it in items:
            if (it.inst, r.id) in a:
                terms.append(a[(it.inst, r.id)] * ((it.w * it.d) // 1000))
        if not terms:
            continue
        over = model.NewIntVar(0, 200000, "over_" + r.id)
        model.Add(sum(terms) <= tot + over)
        over_vars.append(over)
    # 目标：偏好 + 让「同功能的房间」装到最合适的一间
    #       再减去「边度都去唔到」的重罚（10000 分 vs 偏好 100-1000 分）
    # ★ 「冇房放得落」嘅罰分必須**遠大於**任何超容量罰分，
    #   否則求解器會覺得「唔擺張床」比「睡房超標少少」更划算。
    UNASSIGNED_PENALTY = 10 ** 7
    obj = [-v for v in over_vars]
    for it in items:
        pref = DEFAULT_PREF.get(it.category, {})
        for r in allowed_map[it.inst]:
            w = pref.get(funcs[r.id], 1) * 100
            obj.append(a[(it.inst, r.id)] * w)
        if it.inst in unassigned:
            obj.append(unassigned[it.inst] * (-UNASSIGNED_PENALTY))
    model.Maximize(sum(obj))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    st = solver.Solve(model)
    out = {}
    for it in items:
        for r in allowed_map[it.inst]:
            if (it.inst, r.id) in a and solver.Value(a[(it.inst, r.id)]):
                out[it.inst] = r.id
                break
    if verbose:
        stuck = [it.inst for it in items if it.inst not in out]
        print("   [L1] %s  分配 %d/%d 件%s"
              % (solver.StatusName(st), len(out), len(items),
                 ("   ⚠️ 冇房放得落：%s" % "、".join(stuck)) if stuck else ""))
        for it in items:
            if it.inst in out:
                print("        %-14s %-16s -> %s(%s)" % (
                    it.inst, it.category, out[it.inst], funcs.get(out[it.inst], "?")))
    return out, funcs


# ---------------------------------------------------------------- Level 2
def _l2_worker(job):
    """每个房间一个独立任务 —— 参数全部随 job 传进去。

    ⚠️ 不能用模块级全局变量传上下文：ProcessPoolExecutor 的子进程里
    父进程设的全局量是空的（第一次写就踩了这个坑）。
    """
    rid, items, time_limit, room, emap, W, cor, entry = job
    from .cpsat_solver import solve_cpsat
    lay, info = solve_cpsat(room, items, emap=emap, weights=W, corridors=cor,
                            time_limit_s=time_limit, verbose=False,
                            entry_point=entry)
    return rid, lay, str(info)


def solve_house(flat: Flat, items: list[Placement], weights: GreedyWeights | None = None,
                time_limit_s: float = 30.0, max_workers: int = 6,
                parallel: bool = True, verbose: bool = False,
                assignment: dict[str, str] | None = None) -> HouseLayout:
    """三层：分配 -> 每房并行求解 -> 跨房校验。

    assignment 给了就**照用**，不再重新分配 —— LLM 顧問層決定的
    「把 #storage_box 搬到儲物房」必須能覆蓋 L1 的默認分配，否則
    LLM 的決定會被 assign_rooms() 默默改回去。
    """
    W = weights or GreedyWeights(exposure=20000.0)
    funcs = infer_room_functions(flat)
    if assignment is None:
        assign, funcs = assign_rooms(flat, items, verbose=verbose)
    else:
        assign = dict(assignment)
        if verbose:
            print("   [L1] 沿用外部指定分配（%d 件）" % len(assign))
    house = HouseLayout(flat_id=flat.id, assignment=assign)

    per_room_items: dict[str, list[Placement]] = {r.id: [] for r in flat.rooms}
    for it in items:
        rid = assign.get(it.inst)
        if rid:
            per_room_items[rid].append(it)
        else:
            house.unplaced.append(it.inst)

    jobs, ctx = [], {}
    for r in flat.rooms:
        its = per_room_items[r.id]
        if not its:
            house.by_room[r.id] = Layout(room_id=r.id, placements=[])
            house.scores[r.id] = float("nan")
            continue
        poly = r.usable_geo()
        minx, miny, maxx, maxy = poly.bounds
        cell = max(50, int(min(maxx - minx, maxy - miny) / 40) // 50 * 50)
        emap = exposure_map(r, cell_mm=cell)
        cor = default_corridors(r)
        ctx[r.id] = (r, emap, W, cor)
        entry = flat.entry_point if r.id == flat.entry_room else None
        jobs.append((r.id, its, time_limit_s, r, emap, W, cor, entry))

    results = {}
    if parallel and len(jobs) > 1:
        try:
            with ProcessPoolExecutor(max_workers=min(max_workers, len(jobs))) as ex:
                for rid, lay, info in ex.map(_l2_worker, jobs):
                    results[rid] = (lay, info)
        except Exception as e:                       # 并行失败就退回串行
            if verbose:
                print("   [L2] 并行失败(%s)，退回串行" % e)
            for job in jobs:
                rid, lay, info = _l2_worker(job)
                results[rid] = (lay, info)
    else:
        for job in jobs:
            rid, lay, info = _l2_worker(job)
            results[rid] = (lay, info)

    # ---- Level 2.5：局部修复遍
    # ★ 为什么需要：求解器的代价表里**没有「保持房间连通」这一项**。
    #   L 形房间的内凹角天然是低曝光区，于是家具全被推到那里，
    #   把左右两翼之间唯一的通道掐到 <600mm —— 实测 plan_hk_b：
    #   16.2m² 的 L 形客廳，梳化／茶几／鞋櫃／儲物箱四件全部「走唔到」，
    #   因为鞋櫃+儲物櫃+層架+書桌在转角处围成一堵墙。
    #   修法：解完之后，对每一件「走唔到」的家具做局部螺旋搜索
    #   （方向与步长由引擎定），搬得动就搬。这是**补丁通道**，
    #   不改问题定义，也不重解。
    from .livability import check_livability
    from .llm_advisor import try_nudge
    for rid, (lay, info) in results.items():
        if not lay.placements:
            continue
        r = flat.room(rid)
        for _pass in range(3):
            _lv = check_livability(r, lay)
            stuck = sorted({s for v in (_lv.hard + _lv.soft)
                            if v.rule in ("walkway_blocked", "door_blocked")
                            for s in v.subjects})
            if not stuck:
                break
            moved = False
            for inst in stuck:
                ok, _dx, _dy, why = try_nudge(r, lay, inst)
                if ok:
                    moved = True
                    if verbose:
                        print("   [L2.5] %-9s 修复 %s：%s" % (rid, inst, why))
            if not moved:
                break

    for rid, (lay, info) in results.items():
        r = flat.room(rid)
        emap = ctx[rid][1]
        rep = spaciousness_report(r, lay, emap, ctx[rid][3])
        house.by_room[rid] = lay
        house.scores[rid] = rep.score
        house.reports[rid] = rep
        # ★ L2 漏放嘅都要入 unplaced —— 以前只記「冇分配到房間」嘅，
        #   於是「分配到浴室但浴室放唔落」嘅 2 件憑空消失，
        #   報告寫「就位 15/18」，而客戶嘅清單其實有 23 件。
        got = {p.inst for p in lay.placements}
        for it in per_room_items.get(rid, []):
            if it.inst not in got and it.inst not in house.unplaced:
                house.unplaced.append(it.inst)
        if verbose:
            print("   [L2] %-9s(%s) %-22s 放 %d 件" % (
                rid, funcs.get(rid, "?"), str(info), len(lay.placements)))
    house.meta = {"functions": funcs}
    return house


# ---------------------------------------------------------------- Level 3
def verify_house(flat: Flat, house: HouseLayout, verbose: bool = False) -> HouseReport:
    """跨房间校验：可达性、门洞走廊、整屋视觉负荷。"""
    rep = HouseReport()
    funcs = house.meta.get("functions", {})
    # ① 可达性：从入口房间出发，沿 connections 能到所有房间吗
    adj: dict[str, set] = {r.id: set() for r in flat.rooms}
    for c in flat.connections:
        adj.setdefault(c.a, set()).add(c.b)
        adj.setdefault(c.b, set()).add(c.a)
    seen, stack = set(), [flat.entry_room]
    while stack:
        x = stack.pop()
        if x in seen or x not in adj:
            continue
        seen.add(x)
        stack.extend(adj[x] - seen)
    rep.reachable = sorted(seen)
    rep.unreachable = sorted(r.id for r in flat.rooms if r.id not in seen)
    if rep.unreachable:
        rep.issues.append("不可達房間: " + ", ".join(rep.unreachable))
        rep.ok = False

    # ② 每间房的门洞走廊有没有被家具占
    for r in flat.rooms:
        lay = house.by_room.get(r.id)
        if lay is None:
            continue
        zones = door_approach_zones(r)
        blk = sum(p.footprint().intersection(z).area
                  for p in lay.placements for _i, z in zones) / 1e4
        rep.door_blocked[r.id] = blk
        if blk > 1:
            rep.issues.append("%s 的門洞走廊被占 %.0f cm²" % (r.id, blk))
            rep.ok = False

    # ③ 整屋视觉负荷（面积加权）
    tot = 0.0
    for r in flat.rooms:
        lay = house.by_room.get(r.id)
        if lay is None or not lay.placements:
            continue
        emap = exposure_map(r, cell_mm=100)
        tot += layout_visual_load(lay, emap, r.geo().area / 1e6) * (r.geo().area / 1e6)
    rep.house_visual_load = tot / max(1e-6, flat.total_area_m2())
    rep.n_placed = sum(len(l.placements) for l in house.by_room.values())
    rep.n_items = rep.n_placed + len(house.unplaced)
    rep.per_room = {rid: {"score": (None if math.isnan(s) else round(s, 1)),
                          "n": len(house.by_room[rid].placements),
                          "func": funcs.get(rid, "?")}
                    for rid, s in house.scores.items()}
    vals = [s for s in house.scores.values() if not math.isnan(s)]
    rep.score = sum(vals) / len(vals) if vals else 0.0
    if house.unplaced:
        rep.issues.append("沒放下的家具: " + ", ".join(house.unplaced))
    return rep
