"""
hk-space :: packages/templates/catalog.py
=================================================================
30 个参数化家具模板的注册表。

一个 SKU 只需要存这些东西就能变成 3D 模型：
    (template_id, w_mm, d_mm, h_mm, 颜色/材质, 模板参数 dict)

    from catalog import CATALOG, build
    sc = build("wardrobe_tall", w_mm=1000, h_mm=2400, color="white")
    sc.export("sku_123.glb")

约定（与 templates.py 一致）
-----------------------------------------------------------------
* 所有长度 int mm。
* Z 向上，原点在占地矩形中心，+Y 是正面。
* 挂墙件（wall_shelf / pegboard / mirror / air_conditioner）自身 z=0 是它的
  底面，布局引擎负责抬到离地高度。

清单来源说明
-----------------------------------------------------------------
需求清单原文共 32 个品类名。其中
    「書桌+吊櫃」= 書桌 + 一个参数 (hutch=True)
    「茶几」    = 邊桌 换个直径和高度 (d_mm=800, h_mm=400)
所以它们实现为 **variant**（见文件末尾 VARIANTS），注册模板严格 30 个 ——
对应硬要求"产出 30 个 .glb"。用 "python3 export.py --variants" 可以把这 3 个
variant 也导出来。
（地台收納同样放在 VARIANTS：它是床/地台一类，需要时可以升格为正式模板。）
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import trimesh

try:                     # 既支持 "python3 packages/templates/export.py" 直接跑，
    from . import templates as T     # 也支持 "import packages.templates.catalog"
except ImportError:                  # （布局引擎/服务层会这么用）
    import templates as T


@dataclass(frozen=True)
class TemplateSpec:
    id: str
    name_zh: str
    name_en: str
    group: str                      # 櫃 / 床 / 桌 / 座 / 收納 / 電器 / 其他
    fn: str                         # templates.py 里的函数名
    defaults: dict[str, Any]        # 默认尺寸(mm) + 模板参数
    ranges: dict[str, tuple[int, int]]   # 尺寸可调范围(mm)
    materials: tuple[str, ...]      # 材质/配色方案（templates.MATERIALS 的键）
    tex_node: str                   # 下一步贴商品主图的节点名
    note: str = ""

    def spec_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ranges"] = {k: list(v) for k, v in self.ranges.items()}
        d["materials"] = list(self.materials)
        return d


def _spec(**kw) -> TemplateSpec:
    kw.setdefault("materials", ("oak", "walnut", "white", "birch", "grey"))
    kw.setdefault("tex_node", "door_0")
    return TemplateSpec(**kw)


CATALOG: dict[str, TemplateSpec] = {s.id: s for s in [
    # ---------------------------------------------------------- 櫃 (7)
    _spec(id="cabinet_2door", name_zh="兩門櫃", name_en="2-door cabinet", group="櫃",
          fn="cabinet", defaults=dict(w_mm=800, d_mm=400, h_mm=800, doors=2),
          ranges=dict(w_mm=(600, 1000), d_mm=(300, 450), h_mm=(600, 1000)),
          materials=("white", "oak", "grey", "cream", "matte_black"), tex_node="door_0",
          note="最通用的矮櫃（BESTÅ 型）。門縫 3mm + 踢腳 80mm。"),
    _spec(id="cabinet_3door", name_zh="三門櫃", name_en="3-door cabinet", group="櫃",
          fn="cabinet", defaults=dict(w_mm=1200, d_mm=400, h_mm=800, doors=3),
          ranges=dict(w_mm=(1000, 1800), d_mm=(300, 450), h_mm=(600, 1000)),
          materials=("white", "oak", "grey", "sage"), tex_node="door_0",
          note="同一模板 doors=3；中间门做暗抠手。"),
    _spec(id="wardrobe_tall", name_zh="到頂衣櫃", name_en="full-height wardrobe", group="櫃",
          fn="wardrobe", defaults=dict(w_mm=1200, d_mm=600, h_mm=2400, doors=3),
          ranges=dict(w_mm=(800, 2400), d_mm=(450, 650), h_mm=(2000, 2700)),
          materials=("white_oak", "white", "oak", "walnut"), tex_node="door_0",
          note="HK 核心品类：顶天立地 + 顶部做顶柜。层高 2.4-2.6m 时默认 2400，"
               "h_mm 直接吃层高，可以把梁下空间吃掉。"),
    _spec(id="open_shelf", name_zh="開放格櫃", name_en="open grid shelf", group="櫃",
          fn="open_shelf_unit", defaults=dict(w_mm=770, d_mm=390, h_mm=770, cols=2, rows=2),
          ranges=dict(w_mm=(400, 1500), d_mm=(280, 450), h_mm=(400, 1500)),
          materials=("oak", "white", "birch", "walnut"), tex_node="side",
          note="KALLAX 型格子柜。cols/rows 可以随便改，单元尺寸自动分配。"),
    _spec(id="drawer_chest", name_zh="抽屜櫃", name_en="drawer chest", group="櫃",
          fn="drawer_chest", defaults=dict(w_mm=800, d_mm=480, h_mm=1000, drawers=4),
          ranges=dict(w_mm=(500, 1200), d_mm=(350, 550), h_mm=(600, 1300)),
          materials=("white", "oak", "grey", "walnut"), tex_node="drawer_0",
          note="MALM 型。水平 3mm 缝 + 横条把手。"),
    _spec(id="tv_console", name_zh="電視櫃", name_en="TV console", group="櫃",
          fn="tv_console", defaults=dict(w_mm=1500, d_mm=400, h_mm=450, doors=2),
          ranges=dict(w_mm=(900, 2000), d_mm=(300, 450), h_mm=(350, 600)),
          materials=("walnut", "oak", "white", "matte_black"), tex_node="door_l",
          note="细腿抬到 130mm —— 底下看得见地板，小户型显大的教科书动作。"),
    _spec(id="shoe_cabinet", name_zh="鞋櫃", name_en="shoe cabinet", group="櫃",
          fn="shoe_cabinet", defaults=dict(w_mm=900, d_mm=350, h_mm=1000, doors=2, shelves=4),
          ranges=dict(w_mm=(500, 1200), d_mm=(250, 400), h_mm=(600, 1300)),
          materials=("white", "oak", "cream"), tex_node="door_0",
          note="玄关浅进深 + 抬高 90mm 放常穿的鞋。"),

    # ---------------------------------------------------------- 床 (5)
    _spec(id="bed_single", name_zh="單人床", name_en="single bed", group="床",
          fn="bed_single", defaults=dict(w_mm=900, d_mm=1900, h_mm=400),
          ranges=dict(w_mm=(800, 1100), d_mm=(1800, 2000), h_mm=(300, 500)),
          materials=("birch", "oak", "white", "white_oak"), tex_node="mattress",
          note="3 呎床。床架抬高 310mm，床下看得见地板。"),
    _spec(id="bed_double", name_zh="雙人床", name_en="double bed", group="床",
          fn="bed_double", defaults=dict(w_mm=1500, d_mm=2000, h_mm=400),
          ranges=dict(w_mm=(1200, 1800), d_mm=(1900, 2100), h_mm=(300, 500)),
          materials=("oak", "birch", "walnut"), tex_node="mattress",
          note="1372 / 1524 / 1500 都常见，直接改 w_mm。"),
    _spec(id="bed_loft", name_zh="高架床", name_en="loft bed", group="床",
          fn="bed_loft", defaults=dict(w_mm=1000, d_mm=1900, h_mm=1800),
          ranges=dict(w_mm=(900, 1400), d_mm=(1800, 2000), h_mm=(1500, 2100)),
          materials=("birch", "white", "oak"), tex_node="mattress",
          note="HK 小户型核心解法：竖向堆叠，床下塞书桌。默认床面 1750mm。"),
    _spec(id="bed_storage", name_zh="收納床(油壓)", name_en="hydraulic storage bed", group="床",
          fn="bed_storage", defaults=dict(w_mm=1500, d_mm=2000, h_mm=450, base_mm=300),
          ranges=dict(w_mm=(900, 1800), d_mm=(1800, 2100), h_mm=(350, 550)),
          materials=("white_oak", "white", "oak"), tex_node="base",
          note="气压掀床：整个床底是储物箱。base_mm = 箱体高度。"),
    _spec(id="bunk_bed", name_zh="上下舖", name_en="bunk bed", group="床",
          fn="bunk_bed", defaults=dict(w_mm=1000, d_mm=1900, h_mm=1700, lower_h_mm=350),
          ranges=dict(w_mm=(900, 1400), d_mm=(1800, 2000), h_mm=(1500, 2000)),
          materials=("birch", "white", "oak"), tex_node="mattress_upper",
          note="竖向堆叠第二解。上层护栏 + 侧梯。"),

    # ---------------------------------------------------------- 桌 (3)
    _spec(id="desk", name_zh="書桌", name_en="desk", group="桌",
          fn="desk", defaults=dict(w_mm=1200, d_mm=600, h_mm=750),
          ranges=dict(w_mm=(800, 1800), d_mm=(450, 700), h_mm=(700, 780)),
          materials=("oak", "white", "birch", "walnut"), tex_node="top",
          note="hutch=True 并 hutch_h_mm≈1500 就是「書桌+吊櫃」（见 VARIANTS）。"),
    _spec(id="dining_table", name_zh="餐桌", name_en="dining table", group="桌",
          fn="dining_table", defaults=dict(w_mm=1200, d_mm=750, h_mm=750),
          ranges=dict(w_mm=(700, 1800), d_mm=(600, 900), h_mm=(720, 780)),
          materials=("oak", "walnut", "white", "teak"), tex_node="top",
          note="HK 小户型 1200x750 四人刚好不顶墙。"),
    _spec(id="side_table", name_zh="邊桌", name_en="side table", group="桌",
          fn="side_table", defaults=dict(d_mm=450, h_mm=550, top_shape="round"),
          ranges=dict(d_mm=(300, 600), h_mm=(400, 700)),
          materials=("walnut", "oak", "metal_black", "white"), tex_node="top",
          note="圆几三腿。d_mm=800/h_mm=400 就是「茶几」（见 VARIANTS）。"),

    # ---------------------------------------------------------- 座 (4)
    _spec(id="chair", name_zh="單椅", name_en="chair", group="座",
          fn="chair", defaults=dict(w_mm=460, d_mm=520, h_mm=850, seat_h_mm=450),
          ranges=dict(w_mm=(400, 600), d_mm=(450, 620), h_mm=(780, 950)),
          materials=("oak", "walnut", "birch", "metal_black"), tex_node="seat",
          note="四条锥形木腿 + 竖条靠背，底下完全透空。"),
    _spec(id="sofa_2", name_zh="雙人沙發", name_en="2-seat sofa", group="座",
          fn="sofa", defaults=dict(w_mm=1500, d_mm=850, h_mm=780, seats=2),
          ranges=dict(w_mm=(1200, 1800), d_mm=(780, 950), h_mm=(700, 880)),
          materials=("fabric_grey", "fabric_linen", "fabric_blue", "fabric_sand"),
          tex_node="seat_cushion_0",
          note="坐垫/靠垫用超椭球软包（硬盒子看着像石头）。110mm 短腿留出底缝。"),
    _spec(id="sofa_3", name_zh="三人沙發", name_en="3-seat sofa", group="座",
          fn="sofa", defaults=dict(w_mm=2000, d_mm=900, h_mm=780, seats=3),
          ranges=dict(w_mm=(1700, 2400), d_mm=(800, 1000), h_mm=(700, 880)),
          materials=("fabric_grey", "fabric_linen", "fabric_green", "fabric_sand"),
          tex_node="seat_cushion_0",
          note="同一模板 seats=3。"),
    _spec(id="chaise_longue", name_zh="貴妃椅", name_en="chaise longue", group="座",
          fn="chaise_longue", defaults=dict(w_mm=1600, d_mm=750, h_mm=760),
          ranges=dict(w_mm=(1300, 1900), d_mm=(650, 850), h_mm=(650, 850)),
          materials=("fabric_sand", "fabric_linen", "fabric_grey"), tex_node="seat_cushion",
          note="一头靠背一头扶手，配一个撞色抱枕。"),

    # ---------------------------------------------------------- 收納 (4)
    _spec(id="shelf_unit", name_zh="層架", name_en="shelf unit", group="收納",
          fn="shelf_unit", defaults=dict(w_mm=800, d_mm=350, h_mm=1800, shelves=4),
          ranges=dict(w_mm=(400, 1500), d_mm=(250, 450), h_mm=(600, 2200)),
          materials=("oak", "white", "birch", "metal_black"), tex_node="side",
          note="开放式层架。四腿抬高 90mm，底下透光。"),
    _spec(id="storage_box", name_zh="收納箱", name_en="storage box", group="收納",
          fn="storage_box", defaults=dict(w_mm=400, d_mm=350, h_mm=300),
          ranges=dict(w_mm=(250, 700), d_mm=(250, 500), h_mm=(180, 450)),
          materials=("fabric_linen", "fabric_grey", "plastic_white"), tex_node="body",
          note="布艺/塑料整理箱，带盖缝。床上柜下都能塞。"),
    _spec(id="wall_shelf", name_zh="掛牆層架", name_en="wall shelf", group="收納",
          fn="wall_shelf", defaults=dict(w_mm=900, d_mm=250, h_mm=500, tiers=2),
          ranges=dict(w_mm=(400, 1600), d_mm=(150, 320), h_mm=(250, 900)),
          materials=("white_oak", "oak", "white"), tex_node="board_0",
          note="挂墙件：自身 z=0 是底层板底面，布局引擎负责抬到 1200mm 之类。"),
    _spec(id="pegboard", name_zh="洞洞板", name_en="pegboard", group="收納",
          fn="pegboard", defaults=dict(w_mm=800, d_mm=18, h_mm=600, cols=6, rows=5),
          ranges=dict(w_mm=(400, 1400), d_mm=(12, 25), h_mm=(400, 1200)),
          materials=("birch", "oak", "white", "matte_black"), tex_node="board",
          note="真打孔（manifold 布尔 30 个 8mm 孔）+ 挂钩。挂墙件。"),

    # ---------------------------------------------------------- 電器 (4)
    _spec(id="fridge", name_zh="雪櫃", name_en="fridge", group="電器",
          fn="fridge", defaults=dict(w_mm=600, d_mm=650, h_mm=1700),
          ranges=dict(w_mm=(450, 900), d_mm=(550, 750), h_mm=(850, 1900)),
          materials=("plastic_white", "metal_steel", "matte_black"), tex_node="door_upper",
          note="散热要留 50mm 侧缝 + 100mm 后背 —— clearance 由 SKU 侧给。"),
    _spec(id="washer", name_zh="洗衣機", name_en="washing machine", group="電器",
          fn="washer", defaults=dict(w_mm=600, d_mm=600, h_mm=850),
          ranges=dict(w_mm=(500, 700), d_mm=(450, 700), h_mm=(800, 900)),
          materials=("plastic_white", "metal_steel"), tex_node="door_glass",
          note="前面要留 600mm 开门位（clearance），否则摆得下打不开。"),
    _spec(id="microwave", name_zh="微波爐", name_en="microwave", group="電器",
          fn="microwave", defaults=dict(w_mm=500, d_mm=400, h_mm=300),
          ranges=dict(w_mm=(350, 700), d_mm=(300, 500), h_mm=(220, 400)),
          materials=("metal_black", "plastic_white", "metal_steel"), tex_node="door_glass",
          note="默认放台面/吊柜下，h_mm 决定台面高度。"),
    _spec(id="air_conditioner", name_zh="冷氣機", name_en="split AC (indoor)", group="電器",
          fn="air_conditioner", defaults=dict(w_mm=850, d_mm=200, h_mm=300),
          ranges=dict(w_mm=(600, 1200), d_mm=(180, 260), h_mm=(250, 350)),
          materials=("plastic_white",), tex_node="body",
          note="分体室内机。挂墙件：自身 z=0 是底面，抬到梁下 2100mm。"),

    # ---------------------------------------------------------- 其他 (4)
    _spec(id="screen_divider", name_zh="屏風", name_en="folding screen", group="其他",
          fn="screen_divider", defaults=dict(w_mm=1600, d_mm=400, h_mm=1800, panels=3),
          ranges=dict(w_mm=(900, 2400), d_mm=(300, 600), h_mm=(1200, 2000)),
          materials=("fabric_linen", "fabric_sand", "rattan"), tex_node="panel_0",
          note="摺屏：相邻板严格 90 度（角度只用 {0,90,180,270}），手风琴锯齿。"),
    _spec(id="mirror", name_zh="鏡", name_en="full-length mirror", group="其他",
          fn="mirror", defaults=dict(w_mm=600, d_mm=60, h_mm=1600),
          ranges=dict(w_mm=(400, 900), d_mm=(40, 120), h_mm=(1000, 1900)),
          materials=("oak", "white", "metal_black", "walnut"), tex_node="glass",
          note="小户型显大的作弊器（metallic=1, roughness=0.03 的镜面材质）。"),
    _spec(id="drying_rack", name_zh="晾衫架", name_en="drying rack", group="其他",
          fn="drying_rack", defaults=dict(w_mm=1200, d_mm=600, h_mm=1500),
          ranges=dict(w_mm=(800, 1600), d_mm=(450, 800), h_mm=(1200, 1800)),
          materials=("metal_alu", "metal_steel", "plastic_white"), tex_node="bar_0",
          note="HK 必备。上层 7 根铝杆 + 双侧翻翼 + 下方挂杆。方案必须留晾衫位。"),
]}


# =====================================================================
# 变体（同一模板 + 不同参数；用 export.py --variants 导出）
# =====================================================================
VARIANTS: dict[str, dict[str, Any]] = {
    "desk_hutch": dict(fn="desk", name_zh="書桌+吊櫃", name_en="desk with hutch", group="桌",
                       kwargs=dict(w_mm=1200, d_mm=600, h_mm=750, hutch=True, hutch_h_mm=1500),
                       base="desk", note="= 書桌 加 hutch=True，吊柜离台面 750mm。"),
    "coffee_table": dict(fn="side_table", name_zh="茶几", name_en="coffee table", group="其他",
                         kwargs=dict(d_mm=800, h_mm=400, top_shape="round"),
                         base="side_table", note="= 邊桌 d_mm=800 / h_mm=400。"),
    "platform_storage": dict(fn="platform_storage", name_zh="地台收納", name_en="platform storage",
                             group="收納",
                             kwargs=dict(w_mm=2000, d_mm=1500, h_mm=400, seams=3),
                             base=None, note="把整块地板变成柜。劏房/细单位终极杀器。"),
}


# =====================================================================
# 构建入口
# =====================================================================
def get(template_id: str) -> TemplateSpec:
    if template_id not in CATALOG:
        raise KeyError(f"unknown template id {template_id!r}; known: {sorted(CATALOG)}")
    return CATALOG[template_id]


def build(template_id: str, **overrides: Any) -> trimesh.Scene:
    """按注册表造模型。overrides 覆盖默认尺寸/参数（mm）。

        build("bed_double", w_mm=1372)          # 4 呎半
        build("wardrobe_tall", color="white")   # 换配色
    """
    spec = get(template_id)
    kwargs = dict(spec.defaults)
    kwargs.update(overrides)
    fn = T.TEMPLATE_FUNCS[spec.fn]
    warn = []
    for k, v in kwargs.items():
        if k in spec.ranges and isinstance(v, int):
            lo, hi = spec.ranges[k]
            if not (lo <= v <= hi):
                warn.append(f"{k}={v} 超出建议范围 [{lo},{hi}]")
    sc = fn(**kwargs)
    md = sc.metadata.setdefault("hk_space", {})
    md.update(template_id=template_id, name_zh=spec.name_zh, name_en=spec.name_en)
    if warn:
        md["warnings"] = warn
    return sc


def build_variant(variant_id: str) -> trimesh.Scene:
    v = VARIANTS[variant_id]
    return T.TEMPLATE_FUNCS[v["fn"]](**v["kwargs"])


def catalog_table() -> str:
    """人看的清单（export.py 也会打印）。"""
    lines = [f"{'id':18s} {'中文':12s} {'english':24s} {'group':6s} default (mm)"]
    for s in CATALOG.values():
        d = " ".join(f"{k}={v}" for k, v in s.defaults.items()
                     if k in ("w_mm", "d_mm", "h_mm"))
        lines.append(f"{s.id:18s} {s.name_zh:12s} {s.name_en:24s} {s.group:6s} {d}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(catalog_table())
    print(f"\n共 {len(CATALOG)} 个模板, {len(VARIANTS)} 个 variant")
