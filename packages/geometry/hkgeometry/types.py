"""Room / Layout 数据结构（对齐技术方案 v3.2 §4.1）。

Demo 阶段用 dataclass，不做持久化。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import shapely
from shapely.geometry import Polygon

from .units import ROOM_MARGIN_MM, as_int_mm

# 视觉体量的压缩指数 —— 见 Placement.visual_mass() 的说明
VISUAL_MASS_EXPONENT = 0.45

# ★ 唯一的「正前方需要多少 mm」表。
# 曾经在 livability / advisory / cpsat_solver 里各写了一份、互不一致 ——
# 结果浴室那条检查里根本没有 toilet，马桶前面顶着淋浴间也报「宜居 100」。
# 现在全项目只有这一份。
# ⚠️ 这些值是**真实使用空间**，不是"越大越保险"。
# 定大了会直接让房间无解 —— 实测两个例子：
#   · 淋浴間要 600：淋浴間是**走进去的**，它自己的门就在前方，
#     900 深 + 600 = 1500 > 浴室宽度 1480，几何上永远放不下。
#   · 雪柜要 950：标准雪柜门 800 就够开。
# ★★ 两类完全不同的东西，不能混在一张表里：
#
#   A. 「要站人 / 要开门」的 —— 正前方必须是**空的**（硬区，不许放任何家具）
#      柜门、抽屉、雪柜门、洗衣機門、以及要站着用的马桶/洗手盆/灶台。
#
#   B. 「要面对别的东西」的 —— 正前方本来就该有家具（沙发对着电视、
#      椅子对着书桌、茶几在沙发前）。这些的"前方留白"是个伪需求：
#      真当硬区的话，1850mm 深的客厅里电视柜要 450+1800=2250mm，
#      **沙发和电视柜会直接放不下**（实测踩过）。
#      这类关系交给 RELATIONS（距离 + 夹角）去管。
FRONT_CLEARANCE = {
    # --- A 类：必须空出使用空间 ---
    "wardrobe": 600, "cabinet": 500, "drawer_chest": 500, "shelf": 700,
    "shoe_cabinet": 600, "fridge": 800, "washing_machine": 700,
    # ★ 2026-10 修正：灶台 / 廚櫃檯面的前方淨空由 900 改成 600。
    #   為什麼：900mm 是「兩個人並排操作」的商用廚房數字。香港住宅廚房
    #   的過道實際是 700-900mm，而安柏苑 05 室廚房淨 2040x1861，
    #   把 1700 檯面擺西牆、雪櫃+洗衣機擺對面之後，過道就是 750mm ——
    #   這是**正常**的香港廚房，不是壞設計。
    #   但 900 的硬區在 750 的過道裡永遠放唔落，於是求解器冇一個合法位置，
    #   而判據第 9 條（用同一張表）就一定 FAIL：實測 onpak05 唯一剩下的一條
    #   就是「#counter 正前方被 #fridge 擋」。
    #   600 = 一個人的身體深度（站得落、開得門）。房間本身的通行質量
    #   另有第 12 條（主通路）在管，不需要靠這一項去兼職。
    "toilet": 600, "basin": 700, "stove": 600, "kitchen_counter": 600,
    "bathtub": 500, "shower": 500,
    # --- B 类：正前方本就该有家具，不设硬区 ---
    #   ★ 書桌試過改成 A 類（600mm 坐位）—— 實測睡房 8.4m² 放
    #     2090x1560 的床 + 衣櫃之後，書桌**完全放唔落**（漏放 1 件）。
    #     正確的修法唔係加硬淨空，而係**錨定順序**（見 cpsat_solver 的
    #     小房間窮舉）：書桌先落，床塞得入；床先落，右邊只剩 730mm。
    "tv_bench": 0, "sofa": 0, "chaise_longue": 0, "coffee_table": 0,
    "side_table": 0, "desk": 0, "dining_table": 0, "chair": 0,
    "bed": 0, "bunk_bed": 0, "loft_bed": 0, "storage_bed": 0,
    "storage_box": 0, "mirror": 0, "screen": 0, "drying_rack": 0,
    "pegboard": 0, "wall_shelf": 0, "microwave": 0, "aircon": 0,
}

# 家具模板的约定（见 packages/templates/README.md）：
#   **+Y 是家具的正面**（柜门、沙发坐面朝向 +Y），原点在占地矩形中心。
# 所以绕 Z 旋转 rot 之后，正面方向是：
#   rot=0 -> +Y   rot=90 -> -X   rot=180 -> -Y   rot=270 -> +X
ROT_TO_FRONT = {0: (0, 1), 90: (-1, 0), 180: (0, -1), 270: (1, 0)}
FRONT_TO_ROT = {v: k for k, v in ROT_TO_FRONT.items()}


def front_dir(rot: int) -> tuple[int, int]:
    """家具的正面朝向（单位向量）。"""
    return ROT_TO_FRONT[int(rot) % 360]


def hinge_at_start(swing: dict | None) -> bool:
    """铰链在墙段起点（wall.a / 开口的 p0）那一端。

    接受 left/a/start。其余值（right/b/end）算在终点。
    """
    hinge = ((swing or {}).get("hinge") or "left").lower()
    return hinge in ("a", "left", "start", "0")


@dataclass
class HeightPatch:
    """高度场的一块 —— 层高不是常数（横梁 / 假天花 / 冷气机）。"""
    region: list[tuple[int, int]]
    z_max: int
    kind: str = "beam"          # beam | bulkhead | ac_unit

    def polygon(self) -> Polygon:
        return Polygon([(int(x), int(y)) for x, y in self.region])


@dataclass
class Wall:
    id: str
    a: tuple[int, int]
    b: tuple[int, int]
    type: str = "partition"     # load_bearing | partition
    thickness_mm: int = 100
    movable: bool = False       # 装修隔墙可以改；结构墙不行


@dataclass
class Opening:
    id: str
    wall: str
    kind: str                   # door | window
    center_mm: int
    width_mm: int
    height_mm: int = 2000
    sill_h_mm: int = 0
    sill_depth_mm: int = 0
    swing: dict | None = None


@dataclass
class Service:
    """來去水位 / 煤氣喉 / 電箱 —— 香港特有的固定锚点。"""
    id: str
    kind: str
    point: tuple[int, int]
    room: str = ""


@dataclass
class Room:
    id: str
    polygon: list[tuple[int, int]]
    height_mm: int = 2450
    label: str = ""
    walls: list[Wall] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    height_field: list[HeightPatch] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    # 房間裡「本來就存在、拆不走」的實體 —— 服務核心 / 結構柱 / 不能動的內膽。
    # Mies 的 Farnsworth House 是第一個逼出這個欄位的案例：服務核心浮在玻璃盒
    # 中央，四周全部相通，用「切成幾個房間」建模會把連通性同曝光度一齊切死。
    # 所以這裡用 shell + holes 表示，Shapely 原生支援，其餘幾何運算零改動。
    holes: list[list[tuple[int, int]]] = field(default_factory=list)

    def geo(self) -> Polygon:
        shell = [(int(x), int(y)) for x, y in self.polygon]
        rinds = [[(int(x), int(y)) for x, y in h] for h in self.holes]
        return Polygon(shell, rinds)

    def usable_geo(self) -> Polygon:
        """扣掉现场误差余量的可用区域（§8.5）。"""
        return self.geo().buffer(-ROOM_MARGIN_MM)

    def z_max_at(self, x: int, y: int) -> int:
        """该点允许的最高家具顶面高度。"""
        z = self.height_mm
        for patch in self.height_field:
            if patch.polygon().covers(shapely.Point(int(x), int(y))):
                z = min(z, patch.z_max)
        return z

    def diagonal_mm(self) -> float:
        minx, miny, maxx, maxy = self.geo().bounds
        return float(((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5)

    def entrance_point(self) -> tuple[int, int]:
        """入口内侧的一点 —— 最重要的视点。"""
        door = next((o for o in self.openings if o.kind == "door"), None)
        poly = self.geo()
        if door is None:
            c = poly.representative_point()
            return int(c.x), int(c.y)
        wall = next((w for w in self.walls if w.id == door.wall), None)
        if wall is None:
            c = poly.representative_point()
            return int(c.x), int(c.y)
        ax, ay = wall.a
        bx, by = wall.b
        L = max(1.0, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
        t = door.center_mm / L
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        # 沿墙法线往房间内推进 500mm，再夹进多边形
        nx, ny = -(by - ay) / L, (bx - ax) / L
        cand = shapely.Point(px + nx * 500, py + ny * 500)
        if not poly.contains(cand):
            cand = shapely.Point(px - nx * 500, py - ny * 500)
        if not poly.contains(cand):
            cand = shapely.Point(px, py)
        if not poly.contains(cand):
            cand = poly.representative_point()
        return int(round(cand.x)), int(round(cand.y))

    def window_centers(self) -> list[tuple[int, int]]:
        """窗内侧的点 —— 「进门要能看到窗」这条靠它。"""
        out = []
        poly = self.geo()
        for op in self.openings:
            if op.kind != "window":
                continue
            wall = next((w for w in self.walls if w.id == op.wall), None)
            if wall is None:
                continue
            ax, ay = wall.a
            bx, by = wall.b
            L = max(1.0, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
            t = op.center_mm / L
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            nx, ny = -(by - ay) / L, (bx - ax) / L
            cand = shapely.Point(px + nx * 600, py + ny * 600)
            if not poly.contains(cand):
                cand = shapely.Point(px - nx * 600, py - ny * 600)
            if poly.contains(cand):
                out.append((int(round(cand.x)), int(round(cand.y))))
        return out


@dataclass
class Placement:
    """一件家具在房间里的摆放。"""
    inst: str                       # "#wardrobe"
    sku: str = ""
    category: str = ""
    x: int = 0
    y: int = 0
    rot: int = 0                    # 0/90/180/270
    w: int = 0
    d: int = 0
    h: int = 0
    z: int = 0
    visual: dict = field(default_factory=dict)
    price_hkd: int = 0

    # ★ 2026-10：footprint 缓存。
    #   坐标下降每跑一次要评估约两万个候选位，每个候选都要问「其他 10 件家具在哪」——
    #   也就是每次重建 ~100 个 shapely box。实测这是整个 relayout_search 的瓶颈。
    #   key 用 (x,y,rot,w,d)，所以即使有人**原地改** p.x 也不会读到过期值。
    _fp_key: tuple | None = field(default=None, repr=False, compare=False)
    _fp_val: object | None = field(default=None, repr=False, compare=False)

    def footprint(self) -> Polygon:
        """轴对齐 footprint（rot ∈ {0,90,180,270} 所以永远是矩形）。"""
        k = (self.x, self.y, self.rot, self.w, self.d)
        if self._fp_key != k:
            w, d = (self.w, self.d)
            if self.rot in (90, 270):
                w, d = d, w
            self._fp_val = shapely.box(self.x, self.y, self.x + w, self.y + d)
            self._fp_key = k
        return self._fp_val

    def visual_mass_raw(self) -> float:
        """原始视觉体量：面积(m²) x 高(m) x 实心度 x (1 - 明度)。"""
        v = self.visual or {}
        opacity = float(v.get("opacity", 1.0))
        light = float(v.get("lightness_L", 60)) / 100.0
        return (self.w * self.d) / 1e6 * (self.h / 1000.0) * opacity * (1.0 - light)

    def visual_mass(self) -> float:
        """压缩后的视觉体量 —— **这是要用的那个**。

        为什么必须压缩：原始公式是四个因子连乘，实测同一批 SKU 里
        最大 / 最小 = 79 倍（深色到顶柜 vs 浅色开放层架）。
        而 `visual_mass_exposure` 是体量加权平均 —— 79 倍的权重差会让
        优化器实际上只在乎那一件柜子，剩下 90% 的家具被忽略。

        感知上也不会差 79 倍。取 0.45 次方后压缩到 7.2 倍，
        五件家具的权重变成 0.36/0.25/0.25/0.09/0.05，都还有影响力。
        （`_calibrate.py` 里有扫描表。）
        """
        raw = self.visual_mass_raw()
        return raw ** VISUAL_MASS_EXPONENT if raw > 0 else 0.0

    def center(self) -> tuple[float, float]:
        # 直接算，不用建 shapely box（这是 relayout 里调用最频繁的函数）。
        w, d = (self.w, self.d)
        if self.rot in (90, 270):
            w, d = d, w
        return (self.x + w / 2.0, self.y + d / 2.0)

    def front_dir(self) -> tuple[int, int]:
        return front_dir(self.rot)

    def front_point(self, dist: float = 300.0) -> tuple[float, float]:
        """正面外侧的一点 —— 用来判断「它朝着什么」。"""
        cx, cy = self.center()
        fx, fy = self.front_dir()
        return cx + fx * dist, cy + fy * dist


@dataclass
class Layout:
    room_id: str
    placements: list[Placement] = field(default_factory=list)

    def footprints(self) -> list[Polygon]:
        return [p.footprint() for p in self.placements]

    def by_inst(self, inst: str) -> Placement | None:
        return next((p for p in self.placements if p.inst == inst), None)


def validate_int_mm(layout: Layout) -> None:
    """§8.7 自检：任何 float 长度都是 bug。"""
    for p in layout.placements:
        for name in ("x", "y", "w", "d", "h", "z"):
            as_int_mm(getattr(p, name))
