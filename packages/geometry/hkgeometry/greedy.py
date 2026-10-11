"""规则 + 贪心放置器 —— 技术方案 v3.2 §4 方法 A（兜底基线）。

在 Demo 里它同时扮演两个角色：
  · 产生一个「没优化过」的摆法（GreedyWeights(exposure=0)）
  · 产生一个「优化过」的摆法（GreedyWeights(exposure=1200)）
关键：**两者的区别就是目标函数里有没有曝光度项。**

它管四件事，缺一件摆出来就不像人住的：
  ① 不重叠、不越界
  ② **门洞走廊**：每扇门往房内伸出 900x1500 的走道，不许被占
  ③ **朝向**：沙发面朝电视、书桌面朝窗、柜子面朝房内（模板约定 +Y 是正面）
  ④ 体量往低曝光区推（可关）
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import shapely
from shapely.geometry import Point
from shapely.ops import nearest_points

from .corridor import default_corridors
from .exposure import ExposureMap
from .types import Layout, Placement, Room, front_dir

GRID = 150            # 候选位置步长（mm）
DOOR_W = 900          # 门洞走道宽
DOOR_D = 1200         # 门洞走道往房内伸的深度
# ★ 門口走道**唔可以食晒間房**。
#   實測 plan_hk_b 浴室：可用面積 1.57m²，而 900x1200 的走道 = 1.08m² = 69%，
#   於是馬桶／洗手盆／淋浴間全部「放不下」，求解器最後只擺得落一塊
#   400x30 的鏡（唯一塞得入剩餘空隙嘅嘢）—— 出來係一間**冇馬桶嘅浴室**。
#   修法：走道面積上限 = 房間可用面積的 MAX_DOOR_ZONE_FRAC，
#   先減深度，深度下限 MIN_DOOR_DEPTH（夠開門同企入去）。
#   大房完全唔受影響（16m² 客廳上限 4.8m² > 1.08m²）。
MAX_DOOR_ZONE_FRAC = 0.30
MIN_DOOR_DEPTH = 600
# 门槛带：门框正下方这条带任何情况都不准摆东西（外开门/推拉门在房内只需要它）
THRESHOLD_MM = 150.0
# 深度只取 1200mm：够站进去、够关门就够了。
# 曾经取 1500 并且再叠一条 2200mm 的「主通道」，结果把 3040 深的睡房吃掉
# 75%，床一件都放不下（NO-SOLUTION）。两者本来就是重复的。


def door_approach_zones(room: Room, width: int = DOOR_W, depth: int = DOOR_D):
    """每扇门 -> 一块**必须留空**的区域。形状取决于开门方式。

    · 内开（香港默认，也是绝大多数的做法）：门扇扫掠区 = 以铰链为圆心、
      半径 = 门扇宽的四分之一圆，锚在**铰链那一侧**。门开尽时门扇贴墙，
      所以真正要空的只有门框旁边那一块。
    · 外开 / 推拉：门不向房内扫，房内只需要一条 150mm 的门槛带。

    ★ 为什么值得分开算：以前一律用「门洞宽 x 900-1200 的矩形走道」，
      等于假设每扇门都在房中间扫出一大块方形 —— 两侧都要求净空，
      还假设门宽一律 900。实测 plan_hk_b 睡房（3380x1430、两扇内开门），
      这种保守模型算出来「床无论怎么放都踩到门」；
      换成铰链侧的四分之一圆之后，扫掠区只占门框一侧，
      另一侧（和门洞正前方）都空出来了。

    为什么不用「半径 900 的圆」：圆会同时向两侧要求净空，
    而内开门只在铰链侧扫掠 —— 那等于凭空多要一倍的面积。
    """
    poly = room.usable_geo()
    doors = [o for o in room.openings if o.kind == "door"]
    n_doors = max(1, len(doors))
    cap = MAX_DOOR_ZONE_FRAC * poly.area
    # 先用一个粗估的总面积去决定要不要收缩（精确面积在下面逐个算）
    approx = sum(min(DOOR_W, o.width_mm + 100) for o in doors) * max(150.0, depth) * 0.5
    if approx > cap:
        depth = int(max(MIN_DOOR_DEPTH, depth * cap / approx))
    zones = []
    for op in doors:
        wall = next((w for w in room.walls if w.id == op.wall), None)
        if wall is None:
            continue
        ax, ay = wall.a
        bx, by = wall.b
        L = max(1.0, math.hypot(bx - ax, by - ay))
        ux, uy = (bx - ax) / L, (by - ay) / L
        t = max(0.0, min(1.0, op.center_mm / L))
        px, py = ax + ux * L * t, ay + uy * L * t
        nx, ny = -uy, ux
        if not poly.contains(Point(px + nx * 200, py + ny * 200)):
            nx, ny = -nx, -ny
        open_w = float(op.width_mm) or float(width)
        w_here = min(float(DOOR_W), open_w + 100.0)
        half = w_here / 2.0
        # 門檻帶：門框正下方一條窄帶，任何情況都唔准擺嘢
        thr = shapely.Polygon([
            (px - ux * half, py - uy * half),
            (px + ux * half, py + uy * half),
            (px + ux * half + nx * THRESHOLD_MM, py + uy * half + ny * THRESHOLD_MM),
            (px - ux * half + nx * THRESHOLD_MM, py - uy * half + ny * THRESHOLD_MM)])
        sw = op.swing or {}
        direction = (sw.get("dir") or "in").lower()
        if direction in ("out", "sliding", "slide"):
            zones.append((op.id, thr))
            continue
        # 内开：铰链在 center -/+ u*half；"left" 约定为靠 wall.a 那一端
        hinge_sign = -1.0 if (sw.get("hinge") or "left") == "left" else 1.0
        hx, hy = px + ux * hinge_sign * half, py + uy * hinge_sign * half
        # 门扇由「贴墙」扫到「垂直入房」-> 扫掠区由 (-hinge_sign*u) 和 n 张成
        ex, ey = -hinge_sign * ux, -hinge_sign * uy
        sq = shapely.Polygon([
            (hx, hy),
            (hx + ex * open_w, hy + ey * open_w),
            (hx + ex * open_w + nx * open_w, hy + ey * open_w + ny * open_w),
            (hx + nx * open_w, hy + ny * open_w)])
        disc = Point(hx, hy).buffer(open_w, quad_segs=12)
        swept = disc.intersection(sq)
        zones.append((op.id, swept.union(thr)))
    return zones
    x = minx
    while x + w <= maxx + 1:
        y = miny
        while y + d <= maxy + 1:
            b = shapely.box(x, y, x + w, y + d)
            if poly.covers(b):
                out.append((int(round(x)), int(round(y))))
            y += GRID
        x += GRID
    return out


# 各类家具的朝向规则
FACE_RULES = {
    "sofa": "tv", "chair": "tv", "chaise_longue": "tv",
    "desk": "window",
    "bed": "center", "bunk_bed": "center", "loft_bed": "center",
    "storage_bed": "center",
    "coffee_table": "center", "side_table": "center", "dining_table": "center",
    "screen": "center",
    "wardrobe": "away_from_wall", "cabinet": "away_from_wall",
    "shelf": "away_from_wall", "tv_bench": "away_from_wall",
    "shoe_cabinet": "away_from_wall", "drawer_chest": "away_from_wall",
    "wall_shelf": "away_from_wall", "fridge": "away_from_wall",
    "washing_machine": "away_from_wall", "open_shelf": "away_from_wall",
    "storage_box": "away_from_wall", "drying_rack": "away_from_wall",
    "microwave": "away_from_wall", "mirror": "away_from_wall",
    "pegboard": "away_from_wall",
}


@dataclass
class GreedyWeights:
    """权重必须配平，否则量级大的项会吃掉其它项。

    层级：硬约束（重叠/出界） >> 门洞 >> 朝向 >> 审美（贴墙/曝光度）。
    踩过的坑：wall_contact 原来给 60，最大奖励 60*300=18000，
    而曝光度项最大才 1500 —— 结果优化器只顾贴墙，**完全不看曝光度**，
    两种摆法分数几乎一样。
    """
    # 软项全部归一化到 0..1 之后再乘权重 —— 否则量级不可比
    overlap: float = 1e6           # 硬：重叠（per cm2）
    door_block: float = 1.2e5      # 硬：门洞走廊被占（per cm2）
    facing: float = 4000.0         # 常识：朝向全错 = 4000
    wall_contact: float = 500.0    # 审美：完全贴墙 = -500
    exposure: float = 6000.0       # ★ 审美：体量全在高曝光区 = 6000。0 = 没优化过
    corridor: float = 2000.0       # 审美：视廊被高家具挡住
    occlusion: float = 4000.0      # ★ 审美：高家具挡在进门视野里
    relation: float = 1.0          # ★ 功能关系（对已锚定家具的空间关系）
    # ★ 2026-10 新增：压住**别人**前方使用区的代价（按被占比例计）。
    #   为什么需要：以前这条是**硬过滤** —— 候选格只要碰到已放家具的前方矩形就淘汰。
    #   实测 3.8m² 厨房：厨柜台面的 900mm 前方矩形 + 雪櫃本身 = 几何上无解，
    #   求解器于是交出一个**没有台面的厨房**。
    #   现实里一条 900mm 通道系服务整排设备（台面/灶台/雪柜）嘅，
    #   唔系每件家具各自私有。所以改成「压得太多先算硬伤，压一角只罚分」。
    front_block: float = 6000.0
    # ★ 2026-10 新增：Hub（餐桌/書桌/梳化/床/廚櫃檯面）周圍要**留得落衛星家具**。
    #   為什麼：錨定階段只按「自己嗰格最靚」揀位，完全唔理後面跟住佢嘅嘢。
    #   實測安柏苑 05 室：餐桌被擺入西側窄臂（可用淨寬 1330），
    #   4 張餐椅裡有 2 張只能走到 1444 / 1971 mm 外（要求 <= 1155）——
    #   唔係求解器擺唔到椅，係**餐檯一開始就揀咗一個放唔落椅嘅位**。
    group_room: float = 12000.0
    # ★ 2026-10 新增（照 Design-MLLM 的構圖均衡項 S_comp）：
    #   他們用「按面積 x 語義顯著度加權嘅視覺質心」同房間幾何中心嘅距離
    #   去懲罰「一邊重一邊輕」。我們之前**完全冇呢一項** ——
    #   曝光項 + 貼牆項兩條都係把家具往外推，所以 Farnsworth 那次 22 件全部貼邊、
    #   睇落似撒出去。呢一項就係反方向嘅力。
    #   逐格近似：重嘅家具放遠離中心要罰多啲。
    balance: float = 4000.0
    circulation: float = 30000.0   # ★ 通行路径被占（高于显大分 —— 走不过去比不显大严重）

# 显大分里 60% 的权重是 isovist 类指标（可见地板/视线/遮挡），
# 但上面这些项只覆盖了「体量曝光度」。所以再加一个廉价的遮挡代理：
# 高家具离入口越近、越高，越挡视线。不精确，但方向对，而且 O(1)。


def _facing_cost(item: Placement, room: Room, center_pt, windows, tvs) -> float:
    """朝向打分，**归一化到 0..1**：0 = 正对目标，1 = 完全背对。

    归一化很重要 —— 原来返回 0..400，乘权重 120 后最大 48000，
    把曝光度项（万级）整个压住，优化器只看朝向不看曝光度。
    """
    rule = FACE_RULES.get(item.category, "away_from_wall")
    cx, cy = item.center()
    fx, fy = front_dir(item.rot)
    if rule == "away_from_wall":
        poly = room.usable_geo()
        if item.footprint().distance(poly.boundary) > 250:
            return 0.0                       # 没贴墙，不谈朝向
        near = nearest_points(item.footprint(), poly.boundary)[1]
        wx, wy = near.coords[0][0] - cx, near.coords[0][1] - cy
        L = math.hypot(wx, wy) or 1.0
        dot = (-wx / L) * fx + (-wy / L) * fy     # 正面背对墙
        return (1.0 - dot) / 2.0
    if rule == "tv" and tvs:
        tx, ty = tvs[0]
    elif rule == "window" and windows:
        tx, ty = windows[0]
    else:
        tx, ty = center_pt
    dx, dy = tx - cx, ty - cy
    L = math.hypot(dx, dy)
    if L < 1:
        return 0.0
    dot = (dx / L) * fx + (dy / L) * fy
    return (1.0 - dot) / 2.0


def greedy_place(room: Room, placements: list[Placement], emap: ExposureMap | None = None,
                 weights: GreedyWeights | None = None, corridors=None,
                 verbose: bool = False) -> Layout:
    W = weights or GreedyWeights()
    if corridors is None:
        corridors = default_corridors(room)
    doors = door_approach_zones(room)
    poly = room.usable_geo()
    cpt = poly.representative_point()
    center_pt = (cpt.x, cpt.y)
    windows = room.window_centers()
    placed: list[Placement] = []
    # 先放电视柜（沙发要朝它），其余按面积从大到小
    order = sorted(placements, key=lambda p: (0 if p.category == "tv_bench" else 1,
                                              -(p.w * p.d)))

    for item in order:
        tvs = [p.center() for p in placed if p.category == "tv_bench"]
        best, best_cost = None, float("inf")
        for rot in (0, 90, 180, 270):                 # ★ 四个朝向全试
            for (x, y) in _candidates(room, item.w, item.d, rot):
                cand = Placement(inst=item.inst, sku=item.sku, category=item.category,
                                 x=x, y=y, rot=rot, w=item.w, d=item.d, h=item.h,
                                 visual=dict(item.visual), price_hkd=item.price_hkd)
                fp = cand.footprint()
                cost = 0.0
                for q in placed:
                    inter = fp.intersection(q.footprint()).area
                    if inter > 0:
                        cost += W.overlap * (inter / 1e4)
                for _did, z in doors:                 # ★ 门洞走廊
                    a = fp.intersection(z).area
                    if a > 0:
                        cost += W.door_block * (a / 1e4)
                cost -= W.wall_contact * max(0.0, 300 - fp.distance(poly.boundary)) / 300.0
                if W.facing > 0:                      # ★ 朝向
                    cost += W.facing * _facing_cost(cand, room, center_pt, windows, tvs)
                if emap is not None and W.exposure > 0:
                    cost += W.exposure * (cand.visual_mass_raw() ** 0.45) \
                            * _expo(emap, fp)
                for cor in corridors:
                    seg = cor.geom().intersection(fp)
                    if not seg.is_empty and seg.length > 0:
                        over = max(0, cand.h - cor.max_height_mm)
                        if over > 0:
                            cost += W.corridor * over * seg.length / 1e3
                if cost < best_cost:
                    best_cost, best = cost, cand
        if best is None:
            if verbose:
                print("   [skip] %s 放不下" % item.inst)
            continue
        placed.append(best)
        if verbose:
            fx, fy = best.front_dir()
            arrow = {(0, 1): "上(+Y)", (-1, 0): "左(-X)", (0, -1): "下(-Y)", (1, 0): "右(+X)"}[(fx, fy)]
            print("   [put ] %-14s (%5d,%5d) rot=%3d  正面朝 %s"
                  % (best.inst, best.x, best.y, best.rot, arrow))
    return Layout(room_id=room.id, placements=placed)


# ---------------------------------------------------------------- 局部精修
_CAND_CACHE: dict = {}
_EXPO_CACHE: dict = {}


def _expo(emap: ExposureMap, fp) -> float:
    """按 footprint 包围盒缓存曝光度 —— 同一次求解里大量重复查询。

    没有这个缓存，一次多起点求解要 90 秒；加上之后降到 20 秒级。
    """
    key = (id(emap),) + tuple(int(round(v / 10)) for v in fp.bounds)
    v = _EXPO_CACHE.get(key)
    if v is None:
        v = emap.mean_over_polygon(fp)
        _EXPO_CACHE[key] = v
    return v


def _cand_cached(room: Room, w: int, d: int, rot: int):
    key = (id(room), w, d, rot)
    if key not in _CAND_CACHE:
        _CAND_CACHE[key] = _candidates(room, w, d, rot)
    return _CAND_CACHE[key]


def layout_cost(room: Room, items: list[Placement], W: GreedyWeights,
                emap: ExposureMap | None, corridors, doors) -> float:
    """整套布局的总代价（越小越好）。局部精修用它。"""
    poly = room.usable_geo()
    cpt = poly.representative_point()
    center_pt = (cpt.x, cpt.y)
    windows = room.window_centers()
    tvs = [p.center() for p in items if p.category == "tv_bench"]
    ent = Point(room.entrance_point())
    cost = 0.0
    fps = [p.footprint() for p in items]
    for i in range(len(items)):
        fp = fps[i]
        for j in range(i + 1, len(items)):
            inter = fp.intersection(fps[j]).area
            if inter > 0:
                cost += W.overlap * (inter / 1e4)
        for _did, z in doors:
            a = fp.intersection(z).area
            if a > 0:
                cost += W.door_block * (a / 1e4)
        cost -= W.wall_contact * max(0.0, 300 - fp.distance(poly.boundary)) / 300.0
        if W.facing > 0:
            cost += W.facing * _facing_cost(items[i], room, center_pt, windows, tvs)
        if emap is not None and W.exposure > 0:
            cost += W.exposure * (items[i].visual_mass_raw() ** 0.45) \
                    * _expo(emap, fp)
        if W.occlusion > 0 and items[i].h >= 1200:
            d = fp.distance(ent)
            tall = min(1.0, max(0.0, (items[i].h - 1100) / 1200.0))
            near = max(0.0, 1.0 - d / 3000.0)
            cost += W.occlusion * tall * near
        for cor in corridors:
            seg = cor.geom().intersection(fp)
            if not seg.is_empty and seg.length > 0:
                over = max(0, items[i].h - cor.max_height_mm)
                if over > 0:
                    cost += W.corridor * over * seg.length / 1e3
    return cost


def refine(room: Room, layout: Layout, weights: GreedyWeights | None = None,
           emap: ExposureMap | None = None, corridors=None, max_passes: int = 4,
           verbose: bool = False) -> Layout:
    """坐标下降：反复让每一件家具在**整屋总代价**下重新选位，直到不再改善。

    为什么贪心之后还需要这一步：贪心是近视的 —— 它一件一件放，
    放第一件时看不到后面几件。实测曝光度权重要加到 20000 才动得起来，
    而且动完指标反而更差（把别的家具挤到坏位置）。
    精修看的是全局代价，所以几轮下来就能真正下降。
    """
    W = weights or GreedyWeights()
    if corridors is None:
        corridors = default_corridors(room)
    doors = door_approach_zones(room)
    cur = list(layout.placements)
    best_cost = layout_cost(room, cur, W, emap, corridors, doors)
    for p in range(max_passes):
        improved = False
        for i in range(len(cur)):
            item = cur[i]
            others = cur[:i] + cur[i + 1:]
            bi, bc = item, best_cost
            for rot in (0, 90, 180, 270):
                for (x, y) in _cand_cached(room, item.w, item.d, rot):
                    cand = Placement(inst=item.inst, sku=item.sku,
                                     category=item.category, x=x, y=y, rot=rot,
                                     w=item.w, d=item.d, h=item.h,
                                     visual=dict(item.visual),
                                     price_hkd=item.price_hkd)
                    c = layout_cost(room, others + [cand], W, emap, corridors, doors)
                    if c < bc - 1e-6:
                        bc, bi = c, cand
            if bi is not item:
                cur[i] = bi
                best_cost = bc
                improved = True
                if verbose:
                    fx, fy = bi.front_dir()
                    print("    [refine] %-14s -> (%5d,%5d) rot=%3d  cost=%.0f"
                          % (bi.inst, bi.x, bi.y, bi.rot, bc))
        if not improved:
            break
    return Layout(room_id=room.id, placements=cur)


def solve(room: Room, placements: list[Placement], weights: GreedyWeights | None = None,
          emap: ExposureMap | None = None, corridors=None, verbose: bool = False) -> Layout:
    """贪心 + 局部精修 —— Demo 默认走这条。"""
    lay = greedy_place(room, placements, emap=emap, weights=weights,
                       corridors=corridors, verbose=verbose)
    return refine(room, lay, weights=weights, emap=emap, corridors=corridors,
                  verbose=verbose)



def solve(room: Room, placements: list[Placement], weights: GreedyWeights | None = None,
          emap: ExposureMap | None = None, corridors=None, restarts: int = 8,
          seed: int = 0, verbose: bool = False) -> Layout:
    """多起点：贪心 + 局部精修，跑若干次取总代价最低的。

    为什么要多起点：贪心是近视的，不同权重会掉进不同的局部最优。
    实测单跑一次时，曝光度权重加到 15000 反而比不加更差（0.429 -> 0.445），
    因为它落进了坏盆地，精修只能在盆地内改善。
    多起点就是最便宜的跳出办法（方案 §4 方法 C）。
    """
    import random
    W = weights or GreedyWeights()
    if corridors is None:
        corridors = default_corridors(room)
    doors = door_approach_zones(room)
    rng = random.Random(seed)
    best, best_cost = None, float("inf")
    for k in range(restarts):
        order = list(placements)
        if k > 0:
            rng.shuffle(order)
        lay = greedy_place(room, order, emap=emap, weights=W, corridors=corridors)
        lay = refine(room, lay, weights=W, emap=emap, corridors=corridors,
                     max_passes=3)
        c = layout_cost(room, lay.placements, W, emap, corridors, doors)
        if c < best_cost:
            best_cost, best = c, lay
            if verbose:
                print("    [restart %2d] cost=%.0f" % (k, c))
    return best if best is not None else Layout(room_id=room.id, placements=[])

