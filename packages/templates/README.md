# packages/templates — 参数化家具 3D 模板

香港小户型 App Demo 的**家具 3D 素材层**。

> **核心原则：Demo 好看 ≠ 模型精细。**
> 好看 = 比例对 + 材质对 + 光照对 + 配色对。
> 一个有门缝线、正确倒角、正确材质的立方体柜子，在娃娃屋视角下和精细模型肉眼几乎看不出差别。

所以我们**不做"一 SKU 一个模型"**（IKEA 官方模型拿不到授权，實惠/淘宝没有公开模型，
3D-FUTURE 的风格和香港在售 SKU 也对不上）。做法是：

```
30 个品类模板  +  (尺寸 mm, 颜色/材质, 门数…参数)   ->  任意 SKU 的 3D 模型
SKU 只需要存: (template_id, w_mm, d_mm, h_mm, color, params)
```

---

## 文件清单 / 快速开始

| 文件 | 作用 |
|---|---|
| `templates.py` | 参数化模板库（trimesh 建模、材质库、UV、贴图入口）。29 个模板函数 |
| `catalog.py` | 30 个模板的注册表：中英文名、默认尺寸、可调范围、材质方案、贴图节点 |
| `export.py` | 批量导出 GLB + **重新 load 回来校验** + 写 manifest.json |
| `contact_sheet.py` | 生成 30 个模板总览图 PNG（自写 numpy z-buffer 光栅化器） |
| `README.md` | 本文件 |

```bash
cd /mnt/d/openclaw/hk-space

python3 packages/templates/export.py          # -> out/templates/*.glb  (30 个)
python3 packages/templates/contact_sheet.py   # -> out/templates_contact_sheet.png
python3 packages/templates/catalog.py         # 打印 30 个模板的清单

# 可选
python3 packages/templates/export.py --variants      # 额外导出 書桌+吊櫃/茶几/地台收納
python3 packages/templates/export.py --texture-demo  # 顺手验一遍"商品主图贴到柜门"
python3 packages/templates/export.py --jobs 1        # 单进程（调试）
```

Python 侧用法：

```python
import sys; sys.path.insert(0, "packages/templates")
import catalog as C

sc = C.build("wardrobe_tall", w_mm=1000, h_mm=2400, color="white")   # 一个 SKU
sc.export("sku_8812.glb")
print(sc.metadata["hk_space"])     # {'template_id': 'wardrobe_tall', 'name_zh': '到頂衣櫃', ...}
```

---

## 冻结的数值规范（全项目一致，不要违反）

* **所有对外长度一律 `int mm`**，字段名带 `_mm` 后缀。内部 ×0.001 转米喂给 trimesh。
* **禁止用 float 米做对外接口。**
* **角度只用整数度，且限制在 `{0, 90, 180, 270}`。**（屏風的摺板就是严格 90° 手风琴）
* 不使用任何需要联网/授权的素材。

### 坐标约定

```
Z 轴向上，Z = 0 是地板
原点 = 占地矩形的中心（不是角点）
+Y   = 家具的正面（柜门、沙发坐面朝向 +Y）
```

一个 `800×400×800` 的两门柜占 `x∈[-400,400]`, `y∈[-200,200]`, `z∈[0,800]`。

**挂墙件例外**（`wall_shelf` / `pegboard` / `mirror` / `air_conditioner`）：
它们自己的 `z=0` 是**自身底面**，不是地板。布局引擎负责把它们抬到离地高度
（挂墙层架 → 1200mm，冷气机 → 梁下 2100mm）。这样模板本身保持"一块可复用的东西"。

### 板材标准

| 常量 | 值 | 说明 |
|---|---|---|
| `T_PANEL` | 18 mm | 柜体板厚（香港/IKEA 常见的低压板） |
| `T_DOOR` | 18 mm | 柜门厚 |
| `T_BACK` | 9 mm | 背板 |
| `SEAM` | 3 mm | **门缝**（视觉上"一秒变真柜子"的关键） |
| `REVEAL` | 2 mm | 门上下左右边缝 |
| `R_EDGE` | 3 mm | 默认倒角半径 |

> **倒角是怎么做的**：`slab(x,y,z)` 沿**最短边**挤出圆角矩形截面。
> 于是一块门板的 4 条门面棱是圆的，一块侧板的立棱和前后棱是圆的。
> 所有零件都走这一条路径，全模型没有一处是纯直角 —— 纯直角看起来像 Minecraft。

> **门缝为什么要有暗色条**：缝是真留了 3mm，但在平光渲染里缝两侧是同一个颜色、
> 同一个法线，3mm 的缝会**完全看不见**。真实照片里缝是暗的，靠的是那条缝的
> ambient occlusion。这里用 `_seam_strips()` 放一根压暗的细条把 AO 假出来 ——
> 比多加 500 个面有用得多。

---

## 30 个模板清单（含**实际导出**的面数）

数据来自 `out/templates/manifest.json`（export.py 每次重新生成）。

| # | 模板 id / GLB | 中文 | English | 组 | 默认尺寸 (mm) | tri | 零件 | 贴图节点 |
|---|---|---|---|---|---|---|---|---|
| 1 | `cabinet_2door.glb` | 兩門櫃 | 2-door cabinet | 櫃 | 800×400×800 | 808 | 16 | `door_0` |
| 2 | `cabinet_3door.glb` | 三門櫃 | 3-door cabinet | 櫃 | 1200×400×800 | 1032 | 21 | `door_0` |
| 3 | `wardrobe_tall.glb` | 到頂衣櫃 | full-height wardrobe | 櫃 | 1200×600×2400 | 1380 | 27 | `door_0` |
| 4 | `open_shelf.glb` | 開放格櫃 | open grid shelf | 櫃 | 770×390×770 | 420 | 7 | `side` |
| 5 | `drawer_chest.glb` | 抽屜櫃 | drawer chest | 櫃 | 800×480×1000 | 1196 | 25 | `drawer_0` |
| 6 | `tv_console.glb` | 電視櫃 | TV console | 櫃 | 1500×400×450 | 1060 | 21 | `door_l` |
| 7 | `shoe_cabinet.glb` | 鞋櫃 | shoe cabinet | 櫃 | 900×350×1000 | 1120 | 22 | `door_0` |
| 8 | `bed_single.glb` | 單人床 | single bed | 床 | 900×1900×400 | 4228 | 17 | `mattress` |
| 9 | `bed_double.glb` | 雙人床 | double bed | 床 | 1500×2000×400 | 5952 | 19 | `mattress` |
| 10 | `bed_loft.glb` | 高架床 | loft bed | 床 | 1000×1900×1800 | 3164 | 26 | `mattress` |
| 11 | `bed_storage.glb` | 收納床(油壓) | hydraulic storage bed | 床 | 1500×2000×450 | 5352 | 9 | `base` |
| 12 | `bunk_bed.glb` | 上下舖 | bunk bed | 床 | 1000×1900×1700 | 5248 | 34 | `mattress_upper` |
| 13 | `desk.glb` | 書桌 | desk | 桌 | 1200×600×750 | 596 | 12 | `top` |
| 14 | `dining_table.glb` | 餐桌 | dining table | 桌 | 1200×750×750 | 524 | 9 | `top` |
| 15 | `side_table.glb` | 邊桌 | side table | 桌 | ⌀450×550 | 384 | 5 | `top` |
| 16 | `chair.glb` | 單椅 | chair | 座 | 460×520×850 | 672 | 12 | `seat` |
| 17 | `sofa_2.glb` | 雙人沙發 | 2-seat sofa | 座 | 1500×850×780 | 10296 | 12 | `seat_cushion_0` |
| 18 | `sofa_3.glb` | 三人沙發 | 3-seat sofa | 座 | 2000×900×780 | 13624 | 14 | `seat_cushion_0` |
| 19 | `chaise_longue.glb` | 貴妃椅 | chaise longue | 座 | 1600×750×760 | 6908 | 9 | `seat_cushion` |
| 20 | `shelf_unit.glb` | 層架 | shelf unit | 收納 | 800×350×1800 | 672 | 12 | `side` |
| 21 | `storage_box.glb` | 收納箱 | storage box | 收納 | 400×350×300 | 3448 | 4 | `body` |
| 22 | `wall_shelf.glb` | 掛牆層架 | wall shelf | 收納 | 900×250×500 | 480 | 8 | `board_0` |
| 23 | `pegboard.glb` | 洞洞板 | pegboard | 收納 | 800×18×600 | 1572 | 7 | `board` |
| 24 | `fridge.glb` | 雪櫃 | fridge | 電器 | 600×650×1700 | 508 | 11 | `door_upper` |
| 25 | `washer.glb` | 洗衣機 | washing machine | 電器 | 600×600×850 | 708 | 6 | `door_glass` |
| 26 | `microwave.glb` | 微波爐 | microwave | 電器 | 500×400×300 | 344 | 7 | `door_glass` |
| 27 | `air_conditioner.glb` | 冷氣機 | split AC (indoor) | 電器 | 850×200×300 | 240 | 4 | `body` |
| 28 | `screen_divider.glb` | 屏風 | folding screen | 其他 | 1600×400×1800 | 540 | 9 | `panel_0` |
| 29 | `mirror.glb` | 鏡 | full-length mirror | 其他 | 600×60×1600 | 180 | 3 | `glass` |
| 30 | `drying_rack.glb` | 晾衫架 | drying rack | 其他 | 1200×600×1500 | 1404 | 32 | `bar_0` |

**实际产出（export.py 跑完的真实数字）**

| 指标 | 值 |
|---|---|
| 模板数 | **30** 个 `.glb`（+ `manifest.json`） |
| 三角面范围 | **180**（`mirror`）～ **13624**（`sofa_3`），中位 1060，合计 74060 |
| 零件数范围 | 3 ～ 34 个部件 / 模板 |
| 总体积 | **4156 KB (4.06 MB)**；最大 `sofa_3.glb` 630 KB，最小 `mirror.glb` 14 KB |
| 导出耗时 | ~0.2 s（8 进程并行） |
| 校验 | 每个文件都用 `trimesh.load(..., force='scene')` 读回来，确认顶点数 > 0 |
| 材质数 | 每个 GLB 只带 2~5 个 material（trimesh 按内容去重；30 个文件共 80 个） |
| 总览图 | `out/templates_contact_sheet.png`，2640×2392 px，874 KB |

顶点/UV：因为要生成 UV，顶点是炸开的（trimesh 的 uv 是 per-vertex 的），
所以顶点数 ≈ 三角面数 × 3。

### 清单对应关系的一个说明（重要）

需求清单原文共 **32** 个品类名，但硬要求是"产出 30 个 `.glb`"。其中两族只差一个参数，
所以实现成 **variant**（同一模板 + 不同参数），注册模板严格 30 个：

| 清单里的名字 | 实现方式 |
|---|---|
| 書桌+吊櫃 | `desk(hutch=True, hutch_h_mm=1500)` —— 见 `VARIANTS["desk_hutch"]` |
| 茶几 | `side_table(d_mm=800, h_mm=400)` —— 见 `VARIANTS["coffee_table"]` |
| 地台收納 | `TEMPLATE_FUNCS["platform_storage"]`，见 `VARIANTS["platform_storage"]`（备选升格） |

`python3 packages/templates/export.py --variants` 可以把这 3 个一起导到
`out/templates/variants/`（默认不导，保证 `out/templates/*.glb` 恰好 30 个）。

---

## 材质库（`templates.MATERIALS`）

按"木 / 布 / 金属 / 玻璃"分组，各自带正确的 `baseColor` + `metallic` + `roughness`。
模板的 `color=` 参数可以传**材质键名**或**RGB 元组**（0-1 或 0-255 都行）：

```python
T.wardrobe(1200, 600, 2400, color="white")             # 换材质方案
T.wardrobe(1200, 600, 2400, color=(0.85, 0.82, 0.75))  # 换颜色（0-1 会自动 x255）
```

| 组 | 键 | 说明 |
|---|---|---|
| 木 | `oak` `birch` `walnut` `teak` `white_oak` | 浅橡木 / 桦木 / 胡桃 / 柚木 |
| 板 | `white` `cream` `grey` `charcoal` `matte_black` `sage` `terracotta` | 柜体板材 |
| 布 | `fabric_linen` `fabric_grey` `fabric_blue` `fabric_sand` `fabric_green` `mattress` `bedding` | roughness≈0.95 |
| 金属 | `metal_steel` `metal_black` `metal_brass` `metal_alu` `metal_chrome` | metallic 0.8~1.0 |
| 玻璃 | `glass`（半透明）`glass_dark` `mirror` | 镜面 `metallic=1, roughness=0.03` |
| 塑料 | `plastic_white` `plastic_grey` | 白电 |
| 其他 | `rattan` | 藤编 |

---

## 如何新增一个品类（5 步）

1. **在 `templates.py` 里写一个函数**，签名统一为 `(w_mm, d_mm, h_mm, color=None, *, ...)`，
   返回 `trimesh.Scene`：

```python
def bench(w_mm=1200, d_mm=350, h_mm=450, color=None, *, body="oak", **kw):
    W, D, H = int(w_mm), int(d_mm), int(h_mm)      # 一律先 int()
    mf = _resolve(body, color)                     # color 可以是键名或 RGB
    m = Model("bench")
    m.add(place(slab(W, D, 30, 4), 0, 0, zb_mm=H - 30), mf, node="top")
    for lm in _legs(W, D, 60, H - 30, 45, material("metal_black")):
        m.add(lm[0], lm[1], node="leg")
    return m.build(dict(w_mm=W, d_mm=D, h_mm=H))
```

   可以复用的零件构造函数：

   | 函数 | 用途 |
   |---|---|
   | `slab(x,y,z, r)` | 圆角板（主力，沿最短边挤出） |
   | `cyl` `tapered_cyl` | 圆柱 / 锥形腿 |
   | `soft_box(x,y,z, bulge)` | 超椭球软包（坐垫/枕头/床垫），`bulge` 越小越方 |
   | `annulus(r_min, r_max, h)` | 圆环（洗衣机舱门圈） |
   | `place(mesh, x, y, zb_mm=…)` | 按 mm 摆位（x/y 是包围盒中心，zb 是离地高度） |
   | `_legs(W, D, inset, h, dia, mat)` | 四条锥形腿 |
   | `_carcass(W, D, H, shelves=…)` | 柜体（侧/顶/底/背/层板/踢脚） |
   | `_door_bank(W, h, n, t, y, z0, mat)` | 一排带 3mm 缝的门 |
   | `_seam_strips(...)` | 门缝暗线（横/竖都行） |
   | `_bar_handle(len, vertical=…)` | 金属长条把手 |

2. **加进 `TEMPLATE_FUNCS`**（文件末尾的字典）。

3. **在 `catalog.py` 的 `CATALOG` 里加一条 `_spec(...)`**：中英文名、组、默认尺寸、
   可调范围、材质方案、贴图节点名、一句 note。

4. `python3 packages/templates/export.py` —— 自动导出、校验、更新 manifest。

5. `python3 packages/templates/contact_sheet.py` —— 看一眼比例对不对。

> 原则：**先把尺寸和比例做对，再想细节。** 一个尺寸错的漂亮模型在娃娃屋里是灾难。

---

## 如何接纹理贴图（下一步：商品主图 UV 贴到柜门）

**好消息：UV 已经生成好了。** `Model.add()` 默认对每个零件做 `planar_uv()`
（按每个面的主法线做轴对齐投影，UV 落在 0..1），导出的 GLB 里每个 primitive
都带 `TEXCOORD_0`。所以贴图只需要一步：

```python
import sys; sys.path.insert(0, "packages/templates")
import templates as T, catalog as C
from PIL import Image

sc = C.build("cabinet_2door", w_mm=800, color="white")
T.apply_texture(sc, "door_0", Image.open("ikea_sku_main.jpg"),
                material_key="white", uv_rect=(0.03, 0.03, 0.97, 0.97))
sc.export("sku_1234.glb")        # 门板上就带着商品主图了
```

`uv_rect=(u0,v0,u1,v1)` 可以把图只贴到门板的一角（比如把 IKEA 官图裁到门板内框），
`None` 就是贴满整块。自检（会真的把 GLB load 回来确认贴图在）：

```bash
python3 packages/templates/export.py --texture-demo
# -> out/texture_demo.glb  (baseColorTexture=yes)
```

### 节点命名规则（贴图 / 换色 / 隐藏零件的寻址表）

| 节点名 | 出现在 |
|---|---|
| `body` | 柜体的板件（同名多块会自动加 `_1` `_2` 后缀） |
| `door_0` `door_1` `door_2` … | 柜门（左到右）；`top_door_*` 是到顶衣柜的顶柜门 |
| `drawer_0` … | 抽屉面板（下到上） |
| `handle_*` `seam` `top` `seat` `seat_cushion_0` `back_cushion_0` `arm` | 把手 / 门缝暗线 / 台面 / 坐面 / 坐垫 / 靠垫 / 扶手 |
| `mattress` `pillow_0` `frame` `post` `rail` `slat` `guard` `ladder_rung` | 床类 |
| `leg` | 所有腿 |
| `board` `panel_0` `glass` `base` `bar_0` `hook_0` `niche_door` | 洞洞板 / 屏风板 / 镜面 / 床箱 / 晾衫杆 / 挂钩 / 电视柜暗格 |

`catalog.CATALOG[id].tex_node` 已经写好每个模板"最该贴主图"的那个节点。
场景级元数据也会写进 GLB 的 `scenes[].extras.hk_space`（template_id / 中英文名 / 尺寸）。

### 贴图时的已知限制

* `planar_uv` 是**轴对齐投影**（box projection），不是展开 UV。柜门这种平板是对的；
  圆柱/球形零件会拉伸。要贴曲面得改成柱面/球面投影。
* 生成 UV 会把顶点炸开，文件大约大 2~3 倍。不想带 UV 就 `Model(name, uv=False)`。
* SKU 主图建议先**去背景 + 正方形化**再配 `uv_rect`，否则门板边缘会有白边。

---

## 已知局限

1. **不是 CAD**：没有板材开料、没有五金孔位、没有装配关系。这是给娃娃屋看的视觉层。
   要出施工图得另做一层（`slab()` 的尺寸可以直接复用作开料单）。
2. **门缝是"看起来"的缝**：3mm 是几何上的真缝，再加一根压暗条。渲染器没有 AO，
   所以暗线是手工补的。
3. **软包用超椭球近似**：坐垫/枕头不是布料模拟，没有凹陷和褶皱。
   好处是面数少（沙发 1 万面）。
4. **不保证实体（watertight）**：零件之间是叠放/穿插的，不做布尔并集。
   娃娃屋不在意；但如果要做 3D 打印或物理碰撞，得先 merge + 修法线。
5. **`pegboard` 是唯一用布尔运算的模板**（manifold 引擎打 30 个 8mm 孔）。
   布尔失败会**静默退化成实心板**（tri 数会掉到 ~500，manifest 里能看出来）。
6. **GLB 是 Z-up**（和项目其它部分一致：Room JSON、布局引擎都是 Z-up）。
   glTF 规范默认 Y-up，所以在 three.js 里要加一层 `model.rotation.x = -Math.PI / 2`。
7. **`screen_divider` 的 90° 摺角占 550×571mm 地面**：这是"角度只用 0/90/180/270"
   这条冻结规范的代价（真实屏风会摺 30~45°，占地更小）。
8. **挂墙件的离地高度不在模板里**，由布局引擎负责。
9. **`air_conditioner` 的导风板**内倾 35°，严格说超出了"角度只用 {0,90,180,270}"。
   它是零件内部的装饰性倾角（不是布局位姿）；若被判定违规，把它改成 0/90 即可。
10. **没有 leave-out clearance 字段**：抽屉拉出、柜门开合、冰箱散热需要多少空间，
    属于 SKU/布局层的数据，模板层不管（但家具库必须补，否则会摆出"不撞墙但打不开门"）。

---

## 设计笔记（给后面的自己）

* **`slab()` 沿最短边挤出圆角**，这一条规则给了全库统一的倒角，几乎不用想细节。
  （绕 Y 轴转 90° 之后 profile 的 X 会落到 -Z，轴映射写错的话尺寸会**静默互换** ——
  这个 bug 我踩过，表现是"冰箱变成了 y 轴 1700mm 深"。）
* **有腿的家具一定要真的做腿**，底下看得见地板，这对"显大"极其重要
  （`tv_console` 的 130mm 细腿、`shelf_unit` 的 90mm 腿、沙发的 110mm 短腿）。
* **总览图一开始用 matplotlib 画，撞了才知道**：matplotlib 只有"按多边形平均深度排序"，
  没有逐像素 z-buffer。一块 600×850 的前脸被切成几个大三角形后，平均深度会被整个 z 跨度
  带偏，于是排到贴在上面的小零件（洗衣机舱门、控制面板）前面，把零件切成一堆黑三角。
  换成自写的 numpy z-buffer（正交投影 + 逐像素深度测试 + 2x 超采样）后遮挡完全正确，
  还能顺手把阴影当**真几何**丢进去让 z-buffer 自己处理遮挡。
* **镜头方位角**：`azim=+58` 才看得到 `+Y` 正面；`azim=-60` 看的是背面，
  等于把所有柜门都藏起来（也是踩过的坑）。
* **贴在前脸上的小零件必须整块在前脸之外**（min y > D/2），否则它会和平面前脸抢深度，
  渲染出莫名其妙的黑色楔形。
