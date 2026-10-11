"""CP-SAT 求解器 —— 技术方案 v3.2 §4 方法 B（生产主力）。

★ 架构：**先按关系链锚定，再批量求解**

    第 1 步  锚定序列：电视柜 -> 沙发 -> 茶几 -> 床 -> 衣柜
             每件都在「已经放好的家具」已知的前提下单独求解。
             这样「沙发面对电视」「茶几在沙发正前方 300-900mm」
             就变成**对已知锚点的逐格代价** —— 全部线性，不需要任何 reify。
    第 2 步  剩余家具用 CP-SAT 一次解完（锚点作为障碍），保留全局优化。

为什么不用「一个模型 + 关系约束」：
  · 关系是成对的，两个位置都未知时要引入 reified 布尔量，
    实测 OR-Tools 上很容易直接 INFEASIBLE，而且报错完全看不出原因
    （踩过两次：变量域给小了、Add(x != k).OnlyEnforceIf 不可靠）
  · 顺序锚定把非线性彻底消掉，代码短一半，行为可预测

硬约束（在建模阶段过滤候选格，不进求解器）：
    越界 / 压门洞走廊 / 压通行脊 / 压已锚定的家具
"""
from __future__ import annotations

import math
import os

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Point, Polygon

from .corridor import default_corridors
from .exposure import ExposureMap
from .greedy import FACE_RULES, GreedyWeights, door_approach_zones
from .types import FRONT_CLEARANCE, Layout, Placement, Room, front_dir

# ★ 必须靠墙的品类 —— 这是最基本的常识，不能交给软项去"权衡"。
# 踩过的坑：电视柜被单独放在房间中间，结果"沙发面对它 + 距离 1.6-3.6m"
# 直接无解（房间另一侧根本没有那么远的位置）。
# ★ 「漏放一件」的罰分。必須**大於**任何合理的硬傷條數，
#   否則求解器會用「唔擺佢」來消滅硬傷：實測把書桌移入客廳後，
#   客廳 8 件會產生 ≥5 條硬傷，而漏放梳化只要 5 分 ——
#   求解器於是「解決問題」的方法係唔擺張梳化。客戶的梳化唔係可以咁消失嘅。
# ★★ 2026-10 修：關係項要**壓得住環境項**，否則等於冇。
#   舊值 5,000，而 solve_house 預設 exposure=20,000 x mass（椅子 -> ~4,000，
#   大件家具 -> 20,000），再加 facing 4,000 / occlusion 4,000 / front_block 6,000。
#   實測：4 張餐椅裡有 1 張擺到離餐桌 **5.5 米**（客廳另一端）。
#   關係唔係可以攞嚟換分嘅嘢 —— 呢條係審計裡嘅缺陷 E。
RELATION_SCALE = 40000.0

MISSING_WEIGHT = 12

# ★ 但唔係每一件都一樣重要。
#   實測 plan_hk_b 睡房（3380x1430，放 990x2060 單人床 + 衣櫃 + 床頭櫃）：
#   三件擺唔晒，求解器就在「唔擺床」同「唔擺床頭櫃」之間揀 ——
#   兩者罰分一樣，於是佢揀咗**唔擺床**（因為淨返嘅位擺床頭櫃分數更高）。
#   結果係一間冇床嘅睡房。定義房間功能嘅家具要罰重好多。
ESSENTIAL_CATEGORIES = {"bed", "bunk_bed", "loft_bed", "storage_bed",
                        "toilet", "basin", "bathtub", "shower",
                        "stove", "kitchen_counter", "sofa"}
ESSENTIAL_FACTOR = 4

# 啟發式錨定順序全部有硬傷時，再試幾條隨機順序（見 solve_cpsat 末尾）
RESTARTS = 10


# ★ 有淨空要求嘅品類，落位次序（越前越先落）。
#   「定義房間功能」的件先落，純電器後落 —— 否則電器會霸晒牆，
#   最重要那件反而放唔落（實測：廚房冇廚櫃檯面）。
_SEQ_PRIORITY = ["kitchen_counter", "toilet", "basin", "shower", "bathtub",
                 "stove", "range_hood", "fridge", "washing_machine",
                 "wardrobe", "cabinet", "shelf", "drawer_chest", "shoe_cabinet"]


# ★★ 2026-10 修（真實案例量出嚟嘅瓶頸）：
#   「唔夠位時應該掉邊件？」舊寫法把**所有**必需家具都罰 48 分，
#   於是「掉廚櫃檯面」同「掉雪櫃」**同分** —— 打和之後由顯大分決定，
#   而顯大分永遠偏好掉最大那件。實測安柏苑 05 室 3.8m² 廚房：
#   廚櫃檯面（1,700x600，可用格只有 22/1470）被靜靜咁掉，
#   剩下一個冇檯面嘅廚房，而報告一句都冇提。
#
#   正確語義：掉邊件要**視乎佢有幾定義間房**。
#   馬桶、廚櫃檯面冇咗，間房就唔再係廁所／廚房 —— 罰最重；
#   洗衣機、儲物箱呢類可以搬去第二度嘅，罰最輕。
#   同一品類之間再用面積做微調（掉大件比掉細件貴）。
MISSING_IMPORTANCE = {
    "toilet": 6, "kitchen_counter": 6,          # 冇咗就唔係廁所／廚房
    "bed": 5, "bunk_bed": 5, "loft_bed": 5, "storage_bed": 5,
    "basin": 4, "stove": 4, "sofa": 4, "sink": 4,
    "shower": 3, "bathtub": 3, "fridge": 3,
    "wardrobe": 2, "dining_table": 3, "desk": 2, "tv_bench": 2,
    "coffee_table": 1, "chair": 1, "side_table": 1, "cabinet": 2,
    "shelf": 1, "storage_box": 1, "washing_machine": 1, "drying_rack": 1,
    "mirror": 1, "screen": 1, "shoe_cabinet": 2, "drawer_chest": 2,
    "microwave": 1, "aircon": 1, "wall_shelf": 1, "pegboard": 1,
}
MISSING_IMPORTANCE_DEFAULT = 1


def _door_block_count(room, lay, tol_mm2: float = 30000.0) -> int:
    """有幾件家具侵占了門口走道（門扇掃掠區）。

    ★ 為什麼要進入「揀邊個方案」的判準：門開唔到係硬傷，但 livability 把
      door_blocked 記成 **soft**（細房裡真係避唔開），於是 len(lv.hard)
      完全睇唔到佢 —— 兩個方案打和之後由顯大分決定，而顯大分永遠偏好
      「侵占門口」那個（擠開門位可以擺得更鬆）。
    """
    zones = [z for _i, z in door_approach_zones(room)]
    if not zones:
        return 0
    n = 0
    for p in lay.placements:
        fp = p.footprint()
        if sum(fp.intersection(z).area for z in zones) > tol_mm2:
            n += 1
    return n


def _missing_penalty(items, lay) -> int:
    """漏放罰分 —— 按「呢件有幾定義間房」分級，唔再係一刀切。"""
    got = {p.inst for p in lay.placements}
    tot = 0.0
    for it in items:
        if it.inst in got:
            continue
        imp = MISSING_IMPORTANCE.get(it.category, MISSING_IMPORTANCE_DEFAULT)
        # 同級之間：大件少少貴過細件（面積 m² 當小數用）
        tot += MISSING_WEIGHT * imp + (it.w * it.d) / 1e6
    return int(round(tot))


def _scale_zone(z, f: float):
    """把門洞走道按比例縮細（以自身中心為準）。

    doors 裡兩種形式都有：door_approach_zones 回傳 (id, poly)，
    entry_corridor_zones 回傳純 poly —— 兩種都要吃得下。
    """
    if isinstance(z, (tuple, list)):
        return (z[0], _scale_zone(z[1], f))
    b = z.bounds
    cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    hw, hh = (b[2] - b[0]) * f / 2.0, (b[3] - b[1]) * f / 2.0
    return shapely.box(cx - hw, cy - hh, cx + hw, cy + hh)

BACK_TO_WALL_HARD = {"wardrobe", "cabinet", "shelf", "tv_bench", "shoe_cabinet",
                     "drawer_chest", "fridge", "washing_machine", "bed",
                     "bunk_bed", "loft_bed", "storage_bed", "screen", "mirror",
                     "wall_shelf", "pegboard", "storage_box",
                     # 固定洁具：全部贴墙（水管在墙上）
                     "toilet", "basin", "bathtub", "shower", "stove",
                     "range_hood", "kitchen_counter"}
WALL_HARD_MM = 160

# ★ 正前方必须留够的纵深（mm）。也是常识，不是审美。
# 电视柜前面要有 2.4m 才放得下沙发 —— 否则「沙发面对电视 + 距离 1.6-3.6m」
# 这条关系直接无解，求解器只能退回软约束，摆出一个背对电视的方案。
# 正前方纵深 = 统一净空表（只有 A 类 > 0）
FRONT_DEPTH_MIN = dict(FRONT_CLEARANCE)
# ★ 只要这张表里有值（>0），这件家具就走**顺序锚定**路径。
# 批量路径（AddNoOverlap2D）只能保证不重叠，**查不了「A 挡住 B 开门」**；
# 顺序锚定才有「前方使用区不能压到已放家具」这条硬过滤。
# 之前浴室 4 件全走批量，马桶前面顶着淋浴间也没人管。
SEQUENTIAL_REQUIRED = {k for k, v in FRONT_DEPTH_MIN.items() if v and v > 0}

GRID = 50

# ★ 網格要隨房間大小自適應。
#   50mm 一格對香港的房（≤30m²）剛好，但 Farnsworth House 那種
#   17m x 8.7m 的單一開放空間 = 148m²，50mm 網格會產生 346x182 = 63k 格，
#   乘 4 個朝向再乘 22 件家具 —— 候選表直接爆掉（實測跑 10 分鐘都出唔到）。
#   這裡用「總格數上限」反推格距：小房間原封不動（仍然 50mm），
#   大空間自動放粗。代價係位置精度，換嚟嘅係跑得完。
MAX_GRID_CELLS = 6000


def _grid_for(poly) -> int:
    minx, miny, maxx, maxy = poly.bounds
    g = 50
    while g < 1000 and ((int((maxx - minx) // g) + 1) *
                        (int((maxy - miny) // g) + 1)) > MAX_GRID_CELLS:
        g += 50
    return g
SCALE = 1000
# ★★ 2026-10 修：夾頂值 = BIG // SCALE = 10^4。即係話**任何代價 >= 10,000
#   嘅格全部變成同一個數**，求解器分唔出邊個好啲。
#   實測安柏苑 05 室嘅客廳：梳化 31.5% 嘅可用格、餐桌 35.6% 被夾頂，
#   而梳化未夾頂嘅範圍只有 6917..7124（3% 落差）——
#   三分一嘅解空間對求解器嚟講係完全一樣嘅。
#   而 exposure 一項就可以去到 20,000（solve_house 預設）。
#   呢個夾頂令「加強關係權重」之類嘅改動全部失效（一齊撞頂）。
SCALE = 1000
BIG = 10 ** 10
SPINE_W = 700          # 主通道宽度（从门沿法线直进，硬约束）

# 锚定顺序（越靠前越先定）
# ★ 锚定顺序 = 「别人要跟着它走」的家具先放。
#   2026-10 补 dining_table / desk / side_table：**批量阶段查不了关系**
#   （_cost_grids 的 anchors 只含锚定阶段已放好的件），所以餐桌和书桌
#   如果留在批量里，跟它们有关系的椅子就永远看不到它们 —— 实测餐椅被扔到
#   8.3 米外。把它们提到锚定阶段，批量里的椅子才有「可依附的对象」。
ANCHOR_ORDER = ["tv_bench", "sofa", "chaise_longue", "coffee_table", "dining_table",
                "bed", "storage_bed", "bunk_bed", "loft_bed", "desk",
                "wardrobe", "cabinet"]

# 功能关系：对已锚定的家具，新家具要满足的空间关系
# (self_cat, other_cat) -> dict(dmin, dmax, w_dist, in_front, w_front)
RELATIONS = {
    # 面对面时夹角要小：dot>=0.85 约 32 度。放宽到 0.6（53 度）会出现
    # 「沙发和电视斜着摆」，坐着得扭脖子。
    ("sofa", "tv_bench"): dict(dmin=1600, dmax=3600, w_dist=2.0, in_front=True,
                               w_front=4.0, dot=0.85),
    ("tv_bench", "sofa"): dict(dmin=1600, dmax=3600, w_dist=2.0, in_front=True,
                               w_front=4.0, dot=0.85),
    ("coffee_table", "sofa"): dict(dmin=300, dmax=900, w_dist=4.0, in_front=True, w_front=6.0),
    ("chair", "desk"): dict(dmin=300, dmax=900, w_dist=3.0, in_front=False, w_front=0.0),
    ("side_table", "bed"): dict(dmin=0, dmax=500, w_dist=4.0, in_front=False, w_front=0.0),
    # ★ 2026-10 補：**餐椅要跟著餐桌**。缺這一條係「擺出嚟似隨機」最大嘅單一原因 ——
    #   實測 Farnsworth 22 件裡有 2 張餐椅離自己張餐桌 15.3 米。
    #   1,100mm 係「椅前沿貼住檯邊」到「檯中心」嘅合理上限
    #   （檯 1,600x800：長邊座位離中心 ~625，短邊 ~1,025）。
    #   slack 特別收緊到 1.05：椅唔係「附近」就算，要真係坐到檯邊。
    #   實測 slack=1.35 時求解器把椅擺到 1.51m 都只罰 113 分，睇落「合規」但唔係人坐嘅位。
    ("chair", "dining_table"): dict(dmin=0, dmax=1100, w_dist=6.0, in_front=False,
                                    w_front=0.0, slack=1.05),
    # ★ 2026-10 補：**廚房排成一列**。真實香港 3.8m² 廚房嘅正解係
    #   台面 / 灶台 / 雪櫃 / 洗衣機沿同一面牆排隊，共用一條 900mm 通道。
    #   冇呢組關係，佢哋會各自搵「最貼牆」嘅位，互相搶牆、又互相封住對方嘅淨空。
    ("stove", "kitchen_counter"): dict(dmin=0, dmax=900, w_dist=5.0, in_front=False,
                                       w_front=0.0, slack=1.15),
    ("fridge", "kitchen_counter"): dict(dmin=0, dmax=1400, w_dist=4.0, in_front=False,
                                        w_front=0.0, slack=1.15),
    ("washing_machine", "kitchen_counter"): dict(dmin=0, dmax=1400, w_dist=3.0,
                                                 in_front=False, w_front=0.0, slack=1.15),
}


@dataclass
class SolveInfo:
    status: str = ""
    objective: float = 0.0
    wall_time_s: float = 0.0
    n_tuples: int = 0
    n_items: int = 0

    def __str__(self):
        return "%-9s obj=%9.0f %5.2fs 候選=%d" % (
            self.status, self.objective, self.wall_time_s, self.n_tuples)


def circulation_zones(room: Room, width: int = SPINE_W):
    """★ 主通道：从大门口贯穿到房间最深处的走道，宽 800mm，硬约束不许放家具。

    为什么不是「门口一条短带」：短带只保证进门不撞柜子，**不保证房间内部
    还有路走**。实测 5 件家具塞进 7.5m² 之后，家具之间到处小于 600mm，
    人根本走不进去 —— 但求解器完全不知道，因为它的硬约束只有「不重叠」。

    加上这条贯穿通道之后，家具自然被分到通道两侧，布局立刻像人住的。
    """
    poly = room.usable_geo()
    zones = []
    for op in room.openings:
        if op.kind != "door":
            continue
        wall = next((w for w in room.walls if w.id == op.wall), None)
        if wall is None:
            continue
        ax, ay = wall.a
        bx, by = wall.b
        Lw = max(1.0, math.hypot(bx - ax, by - ay))
        ux, uy = (bx - ax) / Lw, (by - ay) / Lw
        t = max(0.0, min(1.0, op.center_mm / Lw))
        px, py = ax + ux * Lw * t, ay + uy * Lw * t
        nx, ny = -uy, ux
        if not poly.contains(Point(px + nx * 200, py + ny * 200)):
            nx, ny = -nx, -ny
        # 沿法线方向能走多远（房间在该方向的尺度），取 75%
        minx, miny, maxx, maxy = poly.bounds
        reach = (maxy - miny) if abs(ny) > abs(nx) else (maxx - minx)
        depth = min(2600.0, reach * 0.75)
        half = width / 2.0
        corners = [(px - ux * half, py - uy * half),
                   (px + ux * half, py + uy * half),
                   (px + ux * half + nx * depth, py + uy * half + ny * depth),
                   (px - ux * half + nx * depth, py - uy * half + ny * depth)]
        zones.append((op.id + "_walkway", Polygon(corners)))
    # ⚠️ 默认**不另加通道** —— 门口的净空区（door_approach_zones）已经保证了
    # 进门有地方站。再叠一条长通道会把小房间吃光（实测 3040 深的睡房
    # 连一张床都放不下）。房间内部走不走得通交给 livability 的连通性检查去判，
    # 而不是靠一条又长又宽的硬通道去"预防"。
    return []


def _farthest_point(poly, origin, cell: int = 150):
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx + cell / 2, maxx, cell)
    ys = np.arange(miny + cell / 2, maxy, cell)
    X, Y = np.meshgrid(xs, ys)
    ok = np.asarray(shapely.contains_xy(poly, X, Y))
    if not ok.any():
        c = poly.representative_point()
        return (c.x, c.y)
    d = (X - origin[0]) ** 2 + (Y - origin[1]) ** 2
    d = np.where(ok, d, -1)
    iy, ix = np.unravel_index(int(np.argmax(d)), d.shape)
    return (float(X[iy, ix]), float(Y[iy, ix]))


def _band(a, b, width):
    """从 a 到 b 的一条带状矩形（走道）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    h = width / 2.0
    return Polygon([(a[0] + nx * h, a[1] + ny * h), (b[0] + nx * h, b[1] + ny * h),
                    (b[0] - nx * h, b[1] - ny * h), (a[0] - nx * h, a[1] - ny * h)])


def log_corridor(room, z, depth):
    if os.environ.get("HKSPACE_DEBUG_ORDER"):
        print("[dbg] corridor %s depth=%d area=%.2f m2" % (room.id, depth, z.area / 1e6))


def entry_corridor_zones(room: Room, entry_point, depth: int = 1100):
    """★ 入口房间的动线：**沿「有门的那面墙」拉一条走廊**，再把大门接过去。

    为什么不是「大门→每扇门」的斜带：实测客厅 8200×1850，4 扇门都在同一面长墙上。
    斜带从大门（左墙）连到最远那扇门（x=7835），**横穿整个房间**，
    把沙发/餐桌的位置全吃掉。而且即使降级成软约束（权重 30000），
    求解器仍然宁愿省曝光度分也不让路 —— 结果右半边完全走不进去。

    真实动线是沿墙走的：进门 → 沿墙走到各个房门口。
    这条走廊又窄又贴墙，占地少得多，而且符合直觉。

    ★ 宽度取 1100mm 而不是 800：走廊本身不放家具，但**紧贴走廊放**的家具
    会各占 300mm 的人体缓冲。800 的话被单侧家具一挤就只剩 500mm，
    人宽 600mm 过不去 —— 实测晾衫架（1200x60 的薄条）就把储物房门掐死了。
    """
    poly = room.usable_geo()
    minx, miny, maxx, maxy = poly.bounds
    # ★ 又长又窄的房间**不留沿墙走廊**。
    # 实测：客厅 8200x1850，1100mm 的沿墙走廊占掉后只剩 750mm ——
    # 连一张 920mm 深的沙发都放不下，直接报「#sofa 放不下」。
    # 真实动线在这种厅里本来就是沿着家具之间的空隙走的，
    # 强行留一条贯通走廊反而制造出"走不过去"的伪问题。
    # 窄房交给「门口净空区(硬) + 宜居连通性检查」兜底。
    # 走廊深度自适应：必须给「家具那一侧」留下至少 950mm
    # （够放一张 920mm 深的沙发）。1850mm 深的客厅 -> 走廊 900mm。
    room_min = min(maxx - minx, maxy - miny)
    if room_min < 1700:
        return []
    depth = int(min(depth, max(750, room_min - 950)))
    by_wall = {}
    for op in room.openings:
        if op.kind == "door":
            by_wall.setdefault(op.wall, []).append(op)
    zones = []
    corridors = []
    for wid, ops in by_wall.items():
        wall = next((w for w in room.walls if w.id == wid), None)
        if wall is None:
            continue
        ax, ay = wall.a
        bx, by = wall.b
        L = max(1.0, math.hypot(bx - ax, by - ay))
        ux, uy = (bx - ax) / L, (by - ay) / L
        s0 = max(0.0, min(o.center_mm - o.width_mm / 2 for o in ops) - 250)
        s1 = min(L, max(o.center_mm + o.width_mm / 2 for o in ops) + 250)
        if s1 - s0 < 100:
            continue
        nx, ny = -uy, ux
        if not poly.contains(Point(ax + ux * s0 + nx * 200, ay + uy * s0 + ny * 200)):
            nx, ny = -nx, -ny
        corners = [(ax + ux * s0, ay + uy * s0), (ax + ux * s1, ay + uy * s1),
                   (ax + ux * s1 + nx * depth, ay + uy * s1 + ny * depth),
                   (ax + ux * s0 + nx * depth, ay + uy * s0 + ny * depth)]
        z = Polygon(corners)
        log_corridor(room, z, depth)
        zones.append((wid + "_corridor", z))
        corridors.append(z)
    # 大门 -> 最近的走廊：一条短连接带
    if corridors and entry_point is not None:
        ep = Point(entry_point[0], entry_point[1])
        best = min(corridors, key=lambda z: z.distance(ep))
        near = shapely.ops.nearest_points(best, ep)[0]
        zones.append(("entry_link", _band(tuple(entry_point), (near.x, near.y),
                                          depth)))
    return zones


def circulation_paths(room: Room, entry_point, width: int = 650):
    """★ 从**入口**走到每扇门内侧的走道 —— 只给入口房间用。

    为什么需要：门口的净空区只保证「门口本身不堆东西」，
    **不保证从大门走得到那个门口**。
    实测客廳（8200x1850，入口和 4 扇门都在它身上）的家具会把
    去睡房 / 浴室 / 厨房的路堵死，而 Level 3 只查净空区，报「0 cm² 侵占」，
    看起来一切正常 —— 用户一眼就看出来了。
    """
    poly = room.usable_geo()
    zones = []
    for op in room.openings:
        if op.kind != "door":
            continue
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
        inner = (px + nx * 500, py + ny * 500)
        zones.append((op.id + "_path", _band(tuple(entry_point), inner, width)))
    return zones


def front_zone(item: Placement, need: int, rot: int | None = None):
    """家具正前方的「使用区」矩形（宽 = 家具宽度，深 = need）。"""
    r = (item.rot // 90) if rot is None else rot
    w, d = _rot_dims(item, r)
    x, y = item.x, item.y
    fx, fy = front_dir(r * 90)
    if fy > 0:
        return shapely.box(x, y + d, x + w, y + d + need)
    if fy < 0:
        return shapely.box(x, y - need, x + w, y)
    if fx > 0:
        return shapely.box(x + w, y, x + w + need, y + d)
    return shapely.box(x - need, y, x, y + d)


def _rot_dims(item: Placement, r: int):
    w, d = item.w, item.d
    if r in (1, 3):
        w, d = d, w
    return w, d


# ★ 朝向「找不到目標」時的退化鏈。**唔准**退化到房間幾何代表點。
#   實測 Farnsworth House：全屋冇電視櫃，而 FACE_RULES 把 chair 歸到 tv 規則，
#   於是 11 張椅（包括 6 張餐椅）嘅正面全部朝向同一個任意點 ——
#   呢一條單獨就足以令人覺得「擺得好隨機」。
#   正確退化順序：最近的功能夥伴 -> 房間長軸。
_FACING_PARTNERS = ("tv_bench", "dining_table", "desk", "coffee_table", "sofa",
                    "bed", "chaise_longue")


def _facing_target(item, room, anchors):
    rule = FACE_RULES.get(item.category, "away_from_wall")
    if rule == "tv":
        tv = next((a for a in anchors if a.category == "tv_bench"), None)
        if tv:
            return ("point", tv.center())
    if rule == "window" and room.window_centers():
        return ("point", room.window_centers()[0])
    if rule == "away_from_wall":
        return ("away", None)
    ref = room.usable_geo().representative_point()
    # 依「夥伴品類」優先次序，取離房間代表點最近嘅一個做朝向目標
    for cat in _FACING_PARTNERS:
        cands = [a for a in anchors if a.category == cat]
        if cands:
            best = min(cands, key=lambda a: (a.center()[0] - ref.x) ** 2 +
                       (a.center()[1] - ref.y) ** 2)
            return ("point", best.center())
    # 連夥伴都冇：沿房間長軸，至少唔係一個「任意點」
    b = room.usable_geo().bounds
    if (b[2] - b[0]) >= (b[3] - b[1]):
        return ("point", (b[0] if ref.x > (b[0] + b[2]) / 2 else b[2], ref.y))
    return ("point", (ref.x, b[1] if ref.y > (b[1] + b[3]) / 2 else b[3]))


def _rel_violation(item, cx, cy, anchors, facing_dot=None, slack=1.35):
    """功能关系违背程度，**归一化到 0..1**。0 = 完全满足。

    ★ 这一层必须能当硬约束用。踩过的坑：把它当软项、权重给得比
      「体量曝光度」小，求解器就会为了显大分把沙发拧过去背对电视 ——
      分高了，但根本不是人住的。**功能关系不许拿来换分数。**
    """
    # ★ 多條關係之間要取 **min**，唔係 max。
    #   一間房可以有兩張餐桌、兩張書桌，而一張椅只屬於其中一張。
    #   取 max 的話，一張坐得好地地嘅餐椅會因為「離書桌好遠」而被判違規 ——
    #   反過來亦一樣。正確語義係「至少滿足一條適用嘅關係」。
    vs = []
    for a in anchors:
        r = RELATIONS.get((item.category, a.category))
        if not r:
            continue
        ax, ay = a.center()
        d = math.hypot(cx - ax, cy - ay)
        vv = 0.0
        sl = r.get("slack", slack)      # 每條關係可以有自己的鬆緊度
        if d < r["dmin"]:
            vv = max(vv, (r["dmin"] - d) / max(1.0, r["dmin"]))
        if d > r["dmax"] * sl:
            vv = max(vv, (d - r["dmax"] * sl) / r["dmax"])
        if r["in_front"] and d > 1:
            fx, fy = front_dir(a.rot)
            dot = ((cx - ax) * fx + (cy - ay) * fy) / d
            thr = r.get("dot", 0.6)
            if dot < thr:
                vv = max(vv, (thr - dot) / (thr + 1.0))
        vs.append(vv)
    v = min(vs) if vs else 0.0
    if facing_dot is not None and facing_dot < 0.55:
        v = max(v, (0.55 - facing_dot) / 1.55)
    return min(1.0, v)


def _cost_grids(room, items, emap, W, corridors, doors, spines, anchors, tvs,
                strict_relations: bool = False, soft_paths=()):
    """给每件家具算「每格每朝向」的代价表 + 可行候选表。"""
    poly = room.usable_geo()
    shapely.prepare(poly)
    boundary = poly.boundary
    shapely.prepare(boundary)
    minx, miny, maxx, maxy = poly.bounds
    global GRID
    GRID = _grid_for(poly)          # 同一個房間由頭到尾用同一個格距
    nx = int((maxx - minx) // GRID) + 1
    ny = int((maxy - miny) // GRID) + 1
    ncells = nx * ny
    ent = Point(room.entrance_point())
    blockers = [z for _d, z in doors] + [z for _d, z in spines]
    for z in blockers:
        shapely.prepare(z)
    obst = [a.footprint() for a in anchors]
    for o in obst:
        shapely.prepare(o)
    # ★ 已放家具的「前方使用区」——候选格不许压上去。
    # 这是**双向检查的另一半**：原来只查「我前面有没有别人」，
    # 不查「我会不会挡住别人的门」。浴室顺序是 马桶→洗手盆→淋浴間，
    # 淋浴間最后放，于是它压在马桶正前方也没人拦。
    anchor_fronts = []
    for a in anchors:
        need = FRONT_DEPTH_MIN.get(a.category, 0)
        if need and need > 0:
            z = front_zone(a, need)
            shapely.prepare(z)
            anchor_fronts.append((a.inst, z))
    out = {}
    for item in items:
        allowed = []
        grid = np.zeros(4 * ncells, dtype=np.int64)
        w_by_r, d_by_r = [], []
        for r in range(4):
            w, d = _rot_dims(item, r)
            w_by_r.append(w)
            d_by_r.append(d)
            fx, fy = front_dir(r * 90)
            kind, tgt = _facing_target(item, room, anchors)
            mass = item.visual_mass_raw() ** 0.45
            for j in range(ny):
                yy = miny + j * GRID
                for i in range(nx):
                    xx = minx + i * GRID
                    base = r * ncells + j * nx + i
                    box = shapely.box(xx, yy, xx + w, yy + d)
                    if not poly.covers(box):
                        grid[base] = -1
                        continue
                    if any(z.intersects(box) for z in blockers):
                        grid[base] = -1
                        continue
                    path_hit = 0.0
                    for _n, pz in soft_paths:
                        inter = box.intersection(pz).area
                        if inter > 0:
                            path_hit += inter / 1e6
                    if any(o.intersects(box) for o in obst):
                        grid[base] = -1
                        continue
                    # ★ 唔可以「一碰到就淘汰」：一條 900mm 通道係服務整排設備嘅。
                    #   只有當新件**佔咗別人前方使用區嘅大部份**（>35%）才算真係封死；
                    #   壓一角（例如雪櫃擺喺長檯面嘅另一頭）只罰分。
                    fb = 0.0
                    hard_front = False
                    for _n, z in anchor_fronts:
                        if z.intersects(box):
                            fr = box.intersection(z).area / max(1.0, z.area)
                            if fr > 0.35:
                                hard_front = True
                                break
                            fb = max(fb, fr)
                    if hard_front:
                        grid[base] = -1
                        continue
                    # ★ 常识硬约束：柜子/床/电视柜必须靠墙
                    if item.category in BACK_TO_WALL_HARD and                             box.distance(boundary) > WALL_HARD_MM:
                        grid[base] = -1
                        continue
                    # ★ 常识硬约束：正前方要留够纵深
                    need = FRONT_DEPTH_MIN.get(item.category)
                    if need:
                        # 正前方的「使用区」矩形：宽 = 家具宽度，深 = need
                        if fy > 0:
                            zone = shapely.box(xx, yy + d, xx + w, yy + d + need)
                        elif fy < 0:
                            zone = shapely.box(xx, yy - need, xx + w, yy)
                        elif fx > 0:
                            zone = shapely.box(xx + w, yy, xx + w + need, yy + d)
                        else:
                            zone = shapely.box(xx - need, yy, xx, yy + d)
                        # ① 不能出房间
                        if not poly.covers(zone):
                            grid[base] = -1
                            continue
                        # ② 不能压别的家具（不然门/抽屜照样打不开）
                        if any(o.intersects(zone) for o in obst):
                            grid[base] = -1
                            continue
                    ccx, ccy = xx + w / 2.0, yy + d / 2.0
                    c = 0.0
                    if emap is not None and W.exposure > 0:
                        c += W.exposure * mass * emap.value_at(ccx, ccy)
                    if W.wall_contact > 0:
                        c -= W.wall_contact * max(0.0, 300 - box.distance(boundary)) / 300.0
                    if W.facing > 0:
                        if kind == "away":
                            if box.distance(boundary) <= 250:
                                near = shapely.ops.nearest_points(box, boundary)[1]
                                wx = near.coords[0][0] - ccx
                                wy = near.coords[0][1] - ccy
                                L = math.hypot(wx, wy) or 1.0
                                c += W.facing * (1 - ((-wx / L) * fx + (-wy / L) * fy)) / 2.0
                        else:
                            dx, dy = tgt[0] - ccx, tgt[1] - ccy
                            L = math.hypot(dx, dy)
                            if L > 1:
                                c += W.facing * (1 - ((dx / L) * fx + (dy / L) * fy)) / 2.0
                    if W.corridor > 0:
                        for cor in corridors:
                            seg = cor.geom().intersection(box)
                            if not seg.is_empty and seg.length > 0:
                                over = max(0, item.h - cor.max_height_mm)
                                if over > 0:
                                    c += W.corridor * over * seg.length / 1e3
                    if W.occlusion > 0 and item.h >= 1200:
                        d_ent = box.distance(ent)
                        tall = min(1.0, max(0.0, (item.h - 1100) / 1200.0))
                        c += W.occlusion * tall * max(0.0, 1.0 - d_ent / 3000.0)
                    if W.circulation > 0 and path_hit > 0:
                        c += W.circulation * path_hit
                    if getattr(W, "front_block", 0) > 0 and fb > 0:
                        c += W.front_block * fb
                    # ★ 功能关系：严格模式下直接当硬约束（违背就淘汰该格）
                    fdot = None
                    if kind != "away":
                        ddx, ddy = tgt[0] - ccx, tgt[1] - ccy
                        LL = math.hypot(ddx, ddy)
                        if LL > 1:
                            fdot = (ddx / LL) * fx + (ddy / LL) * fy
                    viol = _rel_violation(item, ccx, ccy, anchors, fdot)
                    # ⚠️ 严格过滤必须放在 allowed.append 之前！
                    # 否则被淘汰的格子仍留在候选表里，而且 grid=-1 会被
                    # argmin 当成「最小代价」反而优先选中 —— 加了约束结果更差。
                    if strict_relations and viol > 0.0:
                        grid[base] = -1
                        continue
                    if W.relation > 0:
                        c += W.relation * viol * RELATION_SCALE
                    grid[base] = int(round(c * SCALE)) if c < BIG / SCALE else BIG // SCALE
                    allowed.append([int(xx), int(yy), r])
        out[item.inst] = (grid, allowed, w_by_r, d_by_r)
    return out, nx, ny, ncells


def _place_anchor(room, item, grids, nx, ny, ncells):
    """单件锚定：直接取代价最低的可行格（单件问题，argmin 就是最优）。"""
    grid, allowed, w_by_r, d_by_r = grids[item.inst]
    if not allowed:
        return None
    best, bc = None, None
    for (xx, yy, r) in allowed:
        kx = int(round((xx - _gmin(room)[0]) / GRID))
        ky = int(round((yy - _gmin(room)[1]) / GRID))
        v = grid[r * ncells + ky * nx + kx]
        if v < 0:                      # 不可行格，跳过（-1 会被误当最小代价）
            continue
        if bc is None or v < bc:
            bc, best = v, (xx, yy, r)
    if best is None:
        return None
    xx, yy, r = best
    return Placement(inst=item.inst, sku=item.sku, category=item.category,
                     x=int(xx), y=int(yy), rot=int(r) * 90,
                     w=item.w, d=item.d, h=item.h,
                     visual=dict(item.visual), price_hkd=item.price_hkd)


def _gmin(room):
    minx, miny, _, _ = room.usable_geo().bounds
    return int(minx), int(miny)


def _solve_batch(room, items, grids, nx, ny, ncells, time_limit_s, seed, verbose):
    from ortools.sat.python import cp_model
    minx, miny = _gmin(room)
    _, _, maxx, maxy = room.usable_geo().bounds
    model = cp_model.CpModel()
    xivs, yivs, terms, refs = [], [], [], {}
    tuples_total = 0
    for item in items:
        grid, allowed, w_by_r, d_by_r = grids[item.inst]
        if not allowed:
            if verbose:
                print("   [cpsat] %-14s 沒有可行位置" % item.inst)
            continue
        tuples_total += len(allowed)
        x = model.NewIntVar(int(minx), int(minx + (nx - 1) * GRID), "x" + item.inst)
        y = model.NewIntVar(int(miny), int(miny + (ny - 1) * GRID), "y" + item.inst)
        rv = model.NewIntVar(0, 3, "r" + item.inst)
        model.AddAllowedAssignments([x, y, rv], allowed)
        wv = model.NewIntVar(min(w_by_r), max(w_by_r), "w" + item.inst)
        dv = model.NewIntVar(min(d_by_r), max(d_by_r), "d" + item.inst)
        model.AddElement(rv, w_by_r, wv)
        model.AddElement(rv, d_by_r, dv)
        xe = model.NewIntVar(0, int(maxx + max(w_by_r) + 1), "xe" + item.inst)
        ye = model.NewIntVar(0, int(maxy + max(d_by_r) + 1), "ye" + item.inst)
        xivs.append(model.NewIntervalVar(x, wv, xe, "xi" + item.inst))
        yivs.append(model.NewIntervalVar(y, dv, ye, "yi" + item.inst))
        kx = model.NewIntVar(0, nx - 1, "kx" + item.inst)
        ky = model.NewIntVar(0, ny - 1, "ky" + item.inst)
        model.AddDivisionEquality(kx, x - int(minx), GRID)
        model.AddDivisionEquality(ky, y - int(miny), GRID)
        idx = model.NewIntVar(0, 4 * ncells - 1, "ix" + item.inst)
        model.Add(idx == rv * ncells + ky * nx + kx)
        cval = model.NewIntVar(0, BIG, "c" + item.inst)
        flat = np.where(grid < 0, BIG // 10, grid).astype(np.int64).tolist()
        model.AddElement(idx, flat, cval)
        terms.append(cval)
        refs[item.inst] = (x, y, rv, item)
    if xivs:
        model.AddNoOverlap2D(xivs, yivs)
    if terms:
        model.Minimize(sum(terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = seed
    st = solver.Solve(model)
    info = SolveInfo(status=solver.StatusName(st),
                     objective=(solver.ObjectiveValue() / SCALE) if terms else 0.0,
                     wall_time_s=solver.WallTime(), n_tuples=tuples_total,
                     n_items=len(refs))
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [], info
    out = []
    for inst, (x, y, rv, item) in refs.items():
        out.append(Placement(inst=inst, sku=item.sku, category=item.category,
                             x=int(solver.Value(x)), y=int(solver.Value(y)),
                             rot=int(solver.Value(rv)) * 90,
                             w=item.w, d=item.d, h=item.h,
                             visual=dict(item.visual), price_hkd=item.price_hkd))
    return out, info


def solve_cpsat(room: Room, items: list[Placement], emap: ExposureMap | None = None,
                weights: GreedyWeights | None = None, corridors=None,
                time_limit_s: float = 25.0, seed: int = 0, verbose: bool = False,
                report: bool = False, extra_zones=(), entry_point=None):
    """返回 (Layout, SolveInfo)；report=True 时返回 (Layout, SolveInfo, 宜居报告, 显大报告)。"""
    W = weights or GreedyWeights()
    if corridors is None:
        corridors = default_corridors(room)
    # ⚠️ 入口房间的「大门→每扇门」通行带必须当**软约束**。
    # 当硬约束试过：客厅 8200mm 长，入口到最远那扇门的通行带横穿整个房间，
    # 沙发和电视柜直接放不下。软约束 + 高权重（30000，高于显大分 20000）才对：
    # 能避就避，实在避不开也不能让房间摆不了。
    doors = door_approach_zones(room)
    # ★ 入口房间：沿墙走廊 + 门口连接带，**硬约束**
    # （改回硬约束是对的 —— 走廊贴墙又窄，占地远小于斜带，不会挤掉沙发）
    if entry_point is not None:
        doors = list(doors) + entry_corridor_zones(room, entry_point)
    soft_paths = []
    doors = list(doors) + list(extra_zones)
    spines = circulation_zones(room)

    def _order_for(seq):
        """★ 顺序必须同时包含两类：
            ① seq 里点名的品类（沙发/电视柜/床…）
            ② 所有有净空要求的品类（洁具/电器/柜子）
        ⚠️ 曾经只做了 ① —— 于是浴室/厨房的家具**全部走批量路径**，
           而批量路径没有「前方使用区」检查，马桶被淋浴间顶死也照样出方案。
           上一版写在循环外的那段 anchors_order 成了死代码，白写。
        """
        out = []
        for cat in seq:
            for it in items:
                if it.category == cat and it not in out:
                    out.append(it)
        # ★ 順序要**決定性**而且要按重要性排。
        #   舊寫法是在 items 上掃 SEQUENTIAL_REQUIRED，次序取決於 items 的排列；
        #   而 SEQUENTIAL_REQUIRED 本身係 set。
        #   實測後果：廚房 3.8m² 裡雪櫃/洗衣機/灶台先落，佔滿北牆，
        #   **廚櫃檯面（1,700x600）最後冇位，被靜靜咁漏放** ——
        #   一間冇檯面嘅廚房。定義功能嘅件一定要先落。
        for cat in _SEQ_PRIORITY:
            for it in items:
                if it.category == cat and it not in out:
                    out.append(it)
        for it in items:
            if it.category in SEQUENTIAL_REQUIRED and it not in out:
                out.append(it)
        return out

    # ★ 锚定顺序本身是个自由度：先放电视柜还是先放沙发，结果差很多。
    # 先放电视柜容易把它放到「前面没有 2.4m」的位置，导致沙发那条关系无解。
    # 所以试几种顺序，按「硬伤最少 -> 显大分最高」挑。
    orders = [_order_for(ANCHOR_ORDER),
              _order_for(["sofa", "tv_bench", "chaise_longue", "coffee_table",
                          "bed", "wardrobe"]),
              _order_for(["coffee_table", "sofa", "tv_bench", "bed", "wardrobe"])]

    # small rooms: enumerate every anchor order
    if len(items) <= 4:
        import itertools
        orders = [list(p) for p in itertools.permutations(items)]

    if os.environ.get("HKSPACE_DEBUG_ORDER"):
        print("[dbg] room=%s items=%s" % (room.id, [i.inst for i in items]))
        for oi, o in enumerate(orders):
            print("[dbg]   order%d = %s" % (oi, [i.inst for i in o]))
    best = None
    for oi, anchors_order in enumerate(orders):
        lay, info = _solve_with_order(room, items, anchors_order, emap, W, corridors,
                                      doors, spines, time_limit_s, seed,
                                      verbose and oi == 0, soft_paths=soft_paths)
        if not lay.placements:
            continue
        from .livability import check_livability
        from .spaciousness import spaciousness_report
        lv = check_livability(room, lay)
        sp = spaciousness_report(room, lay, emap, corridors)
        # ⚠️ 漏放的家具必须算硬伤！
        # 曾经只比 len(lv.hard)，结果「少放一件家具」反而硬伤更少、分数更高 ——
        # 电视柜放不下就直接不放，求解器还觉得自己很对。
        missing = max(0, len(items) - len(lay.placements))
        key = (len(lv.hard) + _missing_penalty(items, lay),
               _door_block_count(room, lay), -round(sp.score, 1))
        if verbose:
            print("   [order %d] %s  宜居 %d 硬傷  漏放 %d  顯大 %.0f"
                  % (oi, " ".join(p.inst.strip("#") for p in anchors_order[:3]),
                     len(lv.hard), missing, sp.score))
        if best is None or key < best[0]:
            best = (key, lay, info, lv, sp)

    # ★ 放寬階梯：原尺寸的門洞走道放唔落時，再試「走道縮到 60%」。
    #   為什麼要這樣：plan_hk_b 的睡房 3380x1430，兩扇門在兩條長邊上，
    #   各 900x1200 的走道加起來把整間房封死 —— 一張 990x2060 的單人床
    #   左右都塞唔入，結果輸出**一間冇床嘅睡房**。
    #   縮到 60% 之後仍然唔准壓住門框（人仲要行得入），只係唔再要求
    #   整條 900x1200 全空。這是一條階梯：本來就夠位嘅房第一輪就放得落，
    #   根本行唔到這裡，所以唔會影響大房。
    #   階梯一路收到 25%：實測 plan_hk_b 睡房（1430mm 深、兩扇門在兩條長邊上）
    #   收到 40% 都仲係放唔落 —— 因為房間淨深只夠 990，而兩條走道加起來
    #   無論幾淺都會切斷整個深度。收到 25%（≈300mm）就變成「只係唔准壓住門扇」，
    #   床先擺得入。冇這一步，輸出就係一間冇床嘅睡房。
    # ★ 階梯唔可以走到 0.0 —— 「門完全開唔到」永遠唔係一個可接受嘅方案。
    #   由 0.0 放走之後，求解器就會為咗降低其他代價而把床擺正喺門後面
    #   （實測安柏苑 05 室主人房：床頭貼住大門那面牆，侵占門扇區 0.40 m²）。
    for _frac in ((0.6, 0.4, 0.25) if best is not None and doors
                  and (len(items) - len(best[1].placements)) > 0 else ()):
        if len(items) - len(best[1].placements) == 0:
            break
        # frac=0 要走「完全冇走道」，唔可以留一個退化多邊形 ——
        # shapely.box(cx,cy,cx,cy) 係一個點，intersects 仲係會中，等於冇放寬過。
        small = [] if _frac <= 0 else [_scale_zone(z, _frac) for z in doors]
        for oi, anchors_order in enumerate(orders):
            lay, info = _solve_with_order(room, items, anchors_order, emap, W, corridors,
                                          small, spines, time_limit_s, seed, False,
                                          soft_paths=soft_paths)
            if not lay.placements:
                continue
            from .livability import check_livability
            from .spaciousness import spaciousness_report
            lv = check_livability(room, lay)
            sp = spaciousness_report(room, lay, emap, corridors)
            missing = max(0, len(items) - len(lay.placements))
            key = (len(lv.hard) + _missing_penalty(items, lay), -round(sp.score, 1))
            if verbose:
                print("   [relaxed-door %.1f #%d] 宜居 %d 硬傷  漏放 %d  顯大 %.0f"
                      % (_frac, oi, len(lv.hard), missing, sp.score))
            if key < best[0]:
                best = (key, lay, info, lv, sp)
            if best[0][0] == 0 and missing == 0:
                break

    # ★ 隨機重啟：啟發式順序全部有硬傷時，用隨機錨定順序再搜。
    #   為什麼：求解器的代價表裏**冇「保持房間連通」呢一項**，而 L 形房間
    #   嘅內凹角正好係低曝光區 —— 家具全被推去嗰度，把兩翼之間唯一嘅通道
    #   掐到 <600mm。實測 plan_hk_b 嘅 16.2m² L 形客廳：
    #   3 條啟發式順序全部封死房間（10 件裏 10 件走唔到），
    #   而 13 條隨機順序裏面有 1 條完全連通（硬傷 2、walk 0）。
    #   錨定順序本身係自由度，既然啟發式搵唔到，就抽樣搵。
    if best is not None and best[0][0] > 0 and len(items) > 4:
        import random as _random
        rng = _random.Random(20261005)
        for k in range(RESTARTS):
            o = list(items)
            rng.shuffle(o)
            lay, info = _solve_with_order(room, items, o, emap, W, corridors,
                                          doors, spines, time_limit_s, seed + 1000 + k,
                                          False, soft_paths=soft_paths)
            if not lay.placements:
                continue
            from .livability import check_livability
            from .spaciousness import spaciousness_report
            lv = check_livability(room, lay)
            sp = spaciousness_report(room, lay, emap, corridors)
            missing = max(0, len(items) - len(lay.placements))
            key = (len(lv.hard) + _missing_penalty(items, lay),
                   _door_block_count(room, lay), -round(sp.score, 1))
            if verbose:
                print("   [restart %d] 宜居 %d 硬傷  漏放 %d  顯大 %.0f"
                      % (k, len(lv.hard), missing, sp.score))
            if key < best[0]:
                best = (key, lay, info, lv, sp)
            if best[0][0] == 0:
                break                      # 完美就停，唔好燒時間
    if best is None:
        return Layout(room_id=room.id, placements=[]), SolveInfo(status="NO-SOLUTION")
    if report:
        return best[1], best[2], best[3], best[4]
    return best[1], best[2]


def _solve_with_order(room, items, anchors_order, emap, W, corridors, doors, spines,
                      time_limit_s, seed, verbose, soft_paths=()):
    rest = [it for it in items if it not in anchors_order]
    placed: list[Placement] = []
    total_t = 0.0
    # ---- 第 1 步：按关系链逐个锚定 ----
    for it in anchors_order:
        # 先按「关系必须满足」求；实在没有候选就退回软约束（宁可有瑕疵也别放不下）
        p = None
        for strict in (True, False):
            grids, nx, ny, ncells = _cost_grids(room, [it], emap, W, corridors, doors,
                                                spines, placed, [],
                                                strict_relations=strict,
                                                soft_paths=soft_paths)
            p = _place_anchor(room, it, grids, nx, ny, ncells)
            if p is not None:
                if verbose and strict:
                    print("   [anchor] %-14s 關係嚴格滿足" % it.inst)
                break
            if verbose and strict:
                print("   [anchor] %-14s 嚴格關係下無解，放寬" % it.inst)
        if p is None:
            if verbose:
                print("   [anchor] %-14s 放不下" % it.inst)
            continue
        placed.append(p)
        if verbose:
            fx, fy = front_dir(p.rot)
            arrow = {(0, 1): "上", (-1, 0): "左", (0, -1): "下", (1, 0): "右"}[(fx, fy)]
            print("   [anchor] %-14s (%5d,%5d) r=%3d 正面朝 %s"
                  % (p.inst, p.x, p.y, p.rot, arrow))
    # ---- 第 2 步：其余一次解完（锚点作为障碍）----
    info = SolveInfo(status="ANCHOR-ONLY")
    if rest:
        grids, nx, ny, ncells = _cost_grids(room, rest, emap, W, corridors, doors,
                                            spines, placed, [], soft_paths=soft_paths)
        got, info = _solve_batch(room, rest, grids, nx, ny, ncells, time_limit_s,
                                 seed, verbose)
        placed += got
    if verbose:
        print("   [cpsat] %s" % info)
    lay = Layout(room_id=room.id, placements=placed)
    lay = repair_rotations(room, lay)
    return lay, info


def repair_rotations(room: Room, layout: Layout, max_passes: int = 2) -> Layout:
    """收尾修复：逐件原地试 4 个朝向，取「宜居硬伤最少」的那个。

    为什么需要：顺序锚定是近视的 —— 先放沙发时不知道电视柜最后会落在哪，
    结果两者相对角度不对（实测 69°，要求 <53°）。
    原地转一下往往就解决了，而且不影响已经优化好的位置。
    """
    from .livability import check_livability
    poly = room.usable_geo()
    from .greedy import door_approach_zones
    blockers = [z for _d, z in door_approach_zones(room) +
                circulation_zones(room)]
    cur = list(layout.placements)
    best_key = None
    for _ in range(max_passes):
        improved = False
        for i, p in enumerate(cur):
            others = cur[:i] + cur[i + 1:]
            base_key = _liv_key(room, others + [p])
            best = p
            for r in (0, 90, 180, 270):
                if r == p.rot:
                    continue
                cand = Placement(inst=p.inst, sku=p.sku, category=p.category,
                                 x=p.x, y=p.y, rot=r, w=p.w, d=p.d, h=p.h,
                                 visual=dict(p.visual), price_hkd=p.price_hkd)
                fp = cand.footprint()
                if not poly.covers(fp):
                    continue
                if any(z.intersects(fp) for z in blockers):
                    continue
                if any(fp.intersects(q.footprint()) for q in others):
                    continue
                # ⚠️ 旋转修复也必须尊重**前方净空区**。
                # 曾经漏了这条：马桶先放好、淋浴間再被"修复"转进马桶正前方，
                # 前面所有净空检查全白做。要双向查 ——
                #   ① 我转过去之后，我的前方使用区不能压到别人
                #   ② 我不能压到别人的前方使用区
                need_me = FRONT_DEPTH_MIN.get(cand.category, 0)
                if need_me and need_me > 0:
                    zf = front_zone(cand, int(need_me))
                    if any(zf.intersects(q.footprint()) for q in others):
                        continue
                    if any(zf.intersects(front_zone(q, int(FRONT_DEPTH_MIN.get(q.category, 0))))
                           for q in others if FRONT_DEPTH_MIN.get(q.category, 0)):
                        continue
                blocked_front = False
                for q in others:
                    nq = FRONT_DEPTH_MIN.get(q.category, 0)
                    if nq and nq > 0 and front_zone(q, int(nq)).intersects(fp):
                        blocked_front = True
                        break
                if blocked_front:
                    continue
                k = _liv_key(room, others + [cand])
                if k < base_key:
                    base_key, best = k, cand
            if best is not p:
                cur[i] = best
                improved = True
        if not improved:
            break
    return Layout(room_id=layout.room_id, placements=cur)


def _liv_key(room, placements):
    from .livability import check_livability
    lv = check_livability(room, Layout(room_id=room.id, placements=placements))
    return (len(lv.hard), len(lv.violations))



