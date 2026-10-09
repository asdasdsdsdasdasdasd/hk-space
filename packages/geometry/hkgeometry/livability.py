"""人性化检查 —— 「这布局能不能住」，跟「显不显大」是两回事。

★ 这层是后加的，因为踩了一个大坑：
  显大分优化出来的布局**分数很高但不像人住的** ——
  沙发和电视柜并排贴同一面墙、茶几离沙发两米远、
  衣柜悬在房间中间。原因是目标函数里只有「每件家具各自朝哪」，
  **没有任何成对的空间关系**。

设计师第一眼看的是关系：
  · 沙发要**面对**电视，距离 1.4-4.0m（近了脖子酸，远了看不清）
  · 茶几要在沙发**正前方** 300-900mm，并且大致居中
  · 床的**床头**要贴实墙，两侧至少一侧留 500mm 上下床
  · 衣柜**正前方**要留 600mm 开门
  · 大件家具**背要靠墙**，不能悬在房间中间
  · 主要通道 ≥600mm，且从门能走到每一件家具

这一层输出可以**直接讲给用户听**：「你这个方案，茶几在沙发背后，坐下去拿不到东西。」
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import LineString, Point

from .types import FRONT_CLEARANCE, Layout, Placement, Room, front_dir

# 每类家具期望的「背靠墙」行为
BACK_TO_WALL = {"wardrobe", "cabinet", "shelf", "tv_bench", "shoe_cabinet",
                "drawer_chest", "fridge", "washing_machine", "bed", "bunk_bed",
                "loft_bed", "storage_bed", "screen", "mirror", "wall_shelf"}
FREE_STANDING = {"coffee_table", "side_table", "dining_table", "chair", "sofa",
                 "chaise_longue", "storage_box", "drying_rack", "pegboard",
                 "microwave", "aircon"}

WALK_MIN_MM = 600
WALL_TOL_MM = 120


@dataclass
class Violation:
    rule: str
    severity: str          # hard | soft
    subjects: list[str]
    detail: str
    fix: str = ""


@dataclass
class LivabilityReport:
    violations: list[Violation] = field(default_factory=list)

    @property
    def hard(self):
        return [v for v in self.violations if v.severity == "hard"]

    @property
    def soft(self):
        return [v for v in self.violations if v.severity != "hard"]

    @property
    def score(self) -> float:
        """0-100。硬伤扣得重。"""
        s = 100.0
        for v in self.violations:
            s -= 22.0 if v.severity == "hard" else 7.0
        return max(0.0, s)

    def summary(self) -> str:
        return "宜居分 %.0f/100（硬傷 %d 條）" % (self.score, len(self.hard))


def _center(p: Placement):
    return p.center()


def _facing_dot(p: Placement, target) -> float:
    cx, cy = _center(p)
    fx, fy = front_dir(p.rot)
    dx, dy = target[0] - cx, target[1] - cy
    L = math.hypot(dx, dy)
    if L < 1:
        return 1.0
    return (dx / L) * fx + (dy / L) * fy


def _wall_gap(room: Room, p: Placement) -> float:
    return p.footprint().distance(room.usable_geo().boundary)


def _nearest_side(room: Room, p: Placement):
    """返回 (从家具**中心**指向最近墙的单位向量, 家具**轮廓**到墙的距离)。

    ⚠️ 距离必须从 footprint 算，不能从中心算 ——
    曾经用中心到墙的距离，一张贴着墙的床被算成「离墙 1304mm」，
    误报「床头没贴墙」。
    """
    poly = room.usable_geo()
    fp = p.footprint()
    near = shapely.ops.nearest_points(fp, poly.boundary)[1]
    nx, ny = near.coords[0]
    cx, cy = _center(p)
    vx, vy = nx - cx, ny - cy
    L = math.hypot(vx, vy) or 1.0
    return (vx / L, vy / L), fp.distance(poly.boundary)


def _unreachable_items(room: Room, layout: Layout, person_mm: int = 600,
                       cell: int = 100) -> list[str]:
    """把人能走的地方栅格化，看从大门口能不能连通到每件家具正前方。"""
    import numpy as np
    try:
        from scipy import ndimage
    except Exception:
        return []
    poly = room.usable_geo()
    r = person_mm / 2.0
    # ★ 矮過 200mm 嘅唔算障礙物 —— 人要**行過去**嘅只有落地家具。
    #   踩過的坑：plan_hk_b 揀咗「伸縮晾衣桿」（1200x60x60，clearance.top=300，
    #   即係裝喺天花的桿）做晾衫位。佢得 60mm 厚，但障礙緩衝半徑照樣當 300mm，
    #   於是喺 26.7m² 嘅客廳中間切出一條 660mm 闊嘅「牆」，
    #   把梳化／茶几／鞋櫃／儲物箱整批隔離成走唔到（實測 4 件）。
    #   一個 60mm 高嘅嘢，現實中你一定行得過去。
    blocking = [p for p in layout.placements if p.h > 200]
    blocked = (shapely.union_all([p.footprint().buffer(r) for p in blocking])
               if blocking else shapely.geometry.GeometryCollection())
    free = poly.difference(blocked)
    minx, miny, maxx, maxy = poly.bounds
    nx = max(1, int((maxx - minx) // cell) + 1)
    ny = max(1, int((maxy - miny) // cell) + 1)
    xs = np.arange(nx) * cell + minx + cell / 2
    ys = np.arange(ny) * cell + miny + cell / 2
    X, Y = np.meshgrid(xs, ys)
    grid = np.asarray(shapely.contains_xy(free, X, Y))
    lab, n = ndimage.label(grid)
    ex, ey = room.entrance_point()
    ix = int(min(max(0, round((ex - minx - cell / 2) / cell)), nx - 1))
    iy = int(min(max(0, round((ey - miny - cell / 2) / cell)), ny - 1))
    def to_cell(px, py):
        jx = int(round((px - minx - cell / 2) / cell))
        jy = int(round((py - miny - cell / 2) / cell))
        return min(max(0, jx), nx - 1), min(max(0, jy), ny - 1)

    def nearest_free(jxc, jyc, radius=12, want=None):
        """在前方邻域里找最近的自由格 —— 探针点常常正好落在缓冲边界上，
        直接取样会误报「走不到」。半径 12 格 = 1200mm。

        want=None 表示「任何非 0 格都可以」（找起點時用）；
        否則只在同一個連通域裡找。"""
        best, bd = None, 10 ** 9
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                jx, jy = jxc + dx, jyc + dy
                if not (0 <= jx < nx and 0 <= jy < ny):
                    continue
                lv = lab[jy, jx]
                if lv == 0:
                    continue
                if want is not None and lv != want:
                    continue
                d = dx * dx + dy * dy
                if d < bd:
                    bd, best = d, (jx, jy)
        return best

    src = lab[iy, ix]
    if src == 0:
        # ★ 門口格被家具緩衝蓋住 ≠ 整間房走唔到。
        #   實測 plan_hk_b 浴室（1.57m²，深 1180mm）：門口探測點正好落在
        #   馬桶的 300mm 緩衝裡，舊版就直接 return「全部家具都走唔到」——
        #   一間正常嘅浴室被報成三件都行唔到。正確做法係由**最近的自由格**
        #   開始淹水，唔係放棄。
        nf = nearest_free(ix, iy)
        if nf is None:
            return [p.inst for p in layout.placements]
        src = lab[nf[1], nf[0]]

    out = []
    for p in layout.placements:
        cx, cy = _center(p)
        fx, fy = front_dir(p.rot)
        hw = min(p.w, p.d) / 2.0
        px, py = cx + fx * (hw + 400), cy + fy * (hw + 400)
        jx, jy = to_cell(px, py)
        if lab[jy, jx] == src:
            continue
        if nearest_free(jx, jy, want=src) is None:
            out.append(p.inst)
    return out


def check_livability(room: Room, layout: Layout) -> LivabilityReport:
    rep = LivabilityReport()
    by = {p.category: p for p in layout.placements}
    poly = room.usable_geo()
    entrance = Point(room.entrance_point())

    sofa, tv = by.get("sofa") or by.get("chaise_longue"), by.get("tv_bench")
    coffee = by.get("coffee_table")
    bed = by.get("bed") or by.get("storage_bed") or by.get("loft_bed") \
        or by.get("bunk_bed")
    night = by.get("side_table") or by.get("drawer_chest")

    # ── ① 沙发 vs 电视：面对面 + 视距
    if sofa and tv:
        d = math.hypot(*[a - b for a, b in zip(_center(sofa), _center(tv))])
        dot_s = _facing_dot(sofa, _center(tv))
        dot_t = _facing_dot(tv, _center(sofa))
        if dot_s < 0.5:
            rep.violations.append(Violation(
                "sofa_faces_tv", "hard", [sofa.inst, tv.inst],
                "沙發沒有面向電視（夾角 %.0f°）" % (math.degrees(math.acos(max(-1, min(1, dot_s))))),
                "把沙發轉向電視"))
        if dot_t < 0.5:
            rep.violations.append(Violation(
                "tv_faces_sofa", "hard", [tv.inst, sofa.inst],
                "電視櫃背對沙發", "把電視櫃轉向沙發"))
        if d < 1400:
            rep.violations.append(Violation(
                "tv_too_close", "hard", [sofa.inst, tv.inst],
                "沙發到電視只有 %.2fm（<1.4m），坐著看不清" % (d / 1000), "拉開距離"))
        elif d > 4000:
            rep.violations.append(Violation(
                "tv_too_far", "soft", [sofa.inst, tv.inst],
                "沙發到電視 %.2fm（>4.0m），看不清楚" % (d / 1000), "拉近"))

    # ── ② 茶几：在沙发正前方 300-900mm 且大致居中
    if sofa and coffee:
        sc, cc = _center(sofa), _center(coffee)
        dx, dy = cc[0] - sc[0], cc[1] - sc[1]
        d = math.hypot(dx, dy)
        dot = _facing_dot(sofa, cc)
        if dot < 0.3:
            rep.violations.append(Violation(
                "coffee_not_in_front", "hard", [sofa.inst, coffee.inst],
                "茶几不在沙發前方（方向點積 %.2f）" % dot,
                "把茶几挪到沙發正前方"))
        if d < 250 or d > 1000:
            rep.violations.append(Violation(
                "coffee_distance", "soft", [sofa.inst, coffee.inst],
                "茶几離沙發 %.0fmm（合理 300-900mm）" % d, "調整距離"))

    # ── ③ 床头靠墙
    if bed:
        (vx, vy), gap = _nearest_side(room, bed)
        # ⚠️ 必须用床的**实际朝向**。曾经遍历四个朝向 —— 那是
        # 「随便哪个朝向能贴墙就算过」，语义完全错了。
        fx, fy = front_dir(bed.rot)
        head_ok = ((-fx) * vx + (-fy) * vy > 0.7) and gap <= WALL_TOL_MM + 200
        if not head_ok:
            rep.violations.append(Violation(
                "bed_head_not_against_wall", "hard", [bed.inst],
                "床頭沒有貼實牆（離最近牆 %.0fmm）" % gap,
                "把床頭推到牆邊"))

    # ── ④ 床头柜要挨着床
    if bed and night:
        d = bed.footprint().distance(night.footprint())
        if d > 500:
            rep.violations.append(Violation(
                "nightstand_far_from_bed", "soft", [bed.inst, night.inst],
                "床頭櫃離床 %.0fmm（應 <500mm）" % d, "挪到床邊"))

    # ── ⑤ 大件家具背要靠墙（不能悬在房间中间）
    for p in layout.placements:
        if p.category not in BACK_TO_WALL:
            continue
        gap = _wall_gap(room, p)
        if gap > WALL_TOL_MM + 180:
            rep.violations.append(Violation(
                "not_backed_to_wall", "hard", [p.inst],
                "%s（%s）離最近的牆 %.0fmm，懸在房間中間" % (p.inst, p.category, gap),
                "推到牆邊"))

    # ── ⑥ 衣柜 / 冰箱 前方要留开门空间
    for p in layout.placements:
        cl = FRONT_CLEARANCE.get(p.category)
        if not cl:
            continue
        cx, cy = _center(p)
        fx, fy = front_dir(p.rot)
        # 正面軸向的半寬永遠是 d/2（模板約定 +Y 是正面）——
        # 用 min(w,d)/2 在 d>w 的款式（例如雪柜 600x700）上會少 50mm。
        hw = p.d / 2.0
        probe = Point(cx + fx * (hw + cl * 0.6), cy + fy * (hw + cl * 0.6))
        if not poly.contains(probe):
            rep.violations.append(Violation(
                "no_clearance_front", "hard", [p.inst],
                "%s 正前方不足 %dmm，門/抽屜打不開" % (p.inst, cl), "換位或換薄款"))
        else:
            for q in layout.placements:
                if q.inst == p.inst:
                    continue
                if probe.distance(q.footprint()) < cl * 0.35:
                    rep.violations.append(Violation(
                        "clearance_blocked", "hard", [p.inst, q.inst],
                        "%s 正前方被 %s 擋住，打不開" % (p.inst, q.inst), "挪開"))
                    break

    # ── ⑦ 主通道：从门能不能**走**到每一件家具面前
    #    用真的连通性（把自由空间栅格化 + BFS），不用直线判定 ——
    #    直线判定在拥挤房间会疯狂误报，也说不清"到底能不能过去"。
    unreachable = _unreachable_items(room, layout, WALK_MIN_MM)
    for inst in unreachable:
        rep.violations.append(Violation(
            "walkway_blocked", "hard", [inst],
            "從大門走不到 %s 前面（人寬 %dmm 過不去）" % (inst, WALK_MIN_MM),
            "騰出通道"))

    # ── ⑨ 門口走道唔可以被家具占
    #    ★ 少了這條，求解器就會把衣櫃擺正浴室門口 —— 實測 plan_hk_b：
    #      睡房嘅衣櫃（750x580）壓住浴室門走道 0.386m²，成扇門開唔到。
    #      走道縮放（放寬階梯）之後更加容易發生，因為放寬本身就係
    #      「容許家具踩入走道」。所以走道必須由**宜居檢查**獨立把關，
    #      唔可以只靠建模階段嘅硬過濾。
    from .greedy import door_approach_zones
    for _oid, z in door_approach_zones(room):
        hit = []
        for p in layout.placements:
            a = p.footprint().intersection(z).area / 1e6
            if a > 0.03:
                hit.append((p.inst, a))
        if hit:
            names = "、".join(i for i, _ in hit)
            # ★ 用 soft 而唔係 hard：1.43m 深的睡房裡，床一定要踩到門洞走道
            #   （實測：避開走道的合法位置 = 0 個）。如果算 hard，求解器就會
            #   用「唔擺床」來消滅這條硬傷 —— 又回到「冇床嘅睡房」。
            #   所以它只做兩件事：① 引擎永遠先試把家具挪出去（L2.5 修復遍）
            #   ② 挪唔到就老實報出來，交俾顧客決定。
            rep.violations.append(Violation(
                "door_blocked", "soft", [i for i, _ in hit],
                "門口走道被 %s 擋住咗（%.2f m²），扇門開唔到" % (names, sum(a for _, a in hit)),
                "把擋住門口的家具挪開"))

    # ── ⑧ 沙发背对门（坐下正对门口会不安）
    if sofa:
        dot = _facing_dot(sofa, room.entrance_point())
        if dot > 0.85:
            rep.violations.append(Violation(
                "sofa_faces_door", "soft", [sofa.inst],
                "沙發正面直衝大門", "轉個角度或加屏風"))

    return rep
