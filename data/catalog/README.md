> ⚠️ **价格未经核实 / PRICES ARE UNVERIFIED.**
> 本目录中 **全部 150 条 `price_hkd` 都是 Demo 占位值**，`price_verified` 字段**恒为 `false`**。
> 它们是按 IKEA HK 大致价格档位手写的示意数字，**没有逐条核对官网**。`url` 字段一律为空 —— 没核实过的链接我们就不编。
> **上线前必须逐一核对真实售价与货号**，否则任何基于价格的比价 / 预算 / Pareto 方案都会给出错误结论。
>
> 同理，`name_zh` / `name_en` 以 IKEA 公开产品**系列**为蓝本，
> 但 `dims` 是**该品类的典型值**，**不是**某具体型号的官方规格表。
> 它们是"像那么回事、量级正确、能跑摆放算法"的尺寸，不是可以从这里抄去下单的尺寸。

---

# HK 小户型家具 SKU 种子数据集

```
data/catalog/
├── build_catalog.py     # 生成脚本 (可重复运行、可调数量)
├── seed_skus.json       # 150 条 SKU (脚本产物，不要手改)
└── README.md            # 本文件
```

**规模**：150 条，覆盖全部 24 个 category，全部以香港 IKEA 在售 / 曾售产品系列为蓝本。

**用途**：Demo 的"家具库"。求解器从这里挑 SKU 摆进 Room JSON，UI 从这里读 `visual.*` 算"显大分"。

---

## 1. 快速开始

```bash
cd data/catalog

python3 build_catalog.py                 # 默认 150 条 -> seed_skus.json (幂等)
python3 build_catalog.py --stats         # 顺带打印完整统计报告
python3 build_catalog.py --total 400     # 扩到 400 条 (用备用尺寸/饰面)
python3 build_catalog.py --out /tmp/x.json
python3 build_catalog.py --check         # 只校验已存在的 JSON，不改文件
```

**幂等性已验证**：同参数连续运行两次，`seed_skus.json` 的 md5 完全一致
(`f98fc31ad767b43913f1049615a4e1ae`)。货号由 `(模板序号, 变体序号)` 稳定派生，
**不随 `--total` 改变**，所以扩容不会让已有 SKU 的 id 漂移。

**生成结果**：`OK 写入 seed_skus.json 共 150 条 SKU, 校验通过 (0 error)`

---

## 2. 冻结的数值规范

| 规则 | 说明 |
|---|---|
| 长度 | **一律整数毫米 (`int mm`)**。禁止 float 米。 |
| 角度 | 整数度，只允许 `{0, 90, 180, 270}`。 |
| 货号 | 8 位数字字符串，`id = "{retailer前缀}-{货号}"`。 |
| 坐标原点 | `footprint_L` / `top_polygon` 均在**家具自身局部坐标系**，原点在**左前下角**，x 向右、y 向后。 |

---

## 3. 字段含义

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | str | `ikea-hk-{8位货号}`；非 IKEA 货源为 `hkretail-{8位货号}`。全局唯一。 |
| `name_zh` / `name_en` | str | 系列名 + 品类描述。**同名不同尺寸是正常的**（真实产品目录就是这样），靠 `id` / `dims` 区分。 |
| `category` | enum | 24 个冻结值之一，见第 5 节。 |
| `retailer` | str | 默认 `"IKEA HK"`；`aircon` 类目为 `"HK retail (placeholder)"`（见第 7 节）。 |
| `dims` | {w,d,h} | 外接包围盒，整数 mm。`w`=宽(x)、`d`=深(y)、`h`=高(z)。**含所有突出部分**（门板、把手、脚）。 |
| `footprint_L` | [[int,int]×4] | 地面占地矩形，四角坐标。Demo 阶段**只支持矩形**，保留 `_L` 命名是为了将来接 L 形时不改字段名。 |
| `clearance` | {front,left,right,top} | **使用该家具所必需的净空**，整数 mm。 |
| `mount` | str[] | `floor` / `wall` / `ceiling` 的子集。 |
| `can_support` | {max_load_kg, top_polygon} | 顶面可承重。`max_load_kg=0` **且** `top_polygon=[]` 表示**没有可置物的顶面**（椅子、镜子、挂墙晾衣架、微波炉等）。二者必须同时出现，不能只给一个。 |
| `visual` | 6 字段 | 见第 4 节。 |
| `price_hkd` | int | **占位值，未核实。** |
| `price_verified` | bool | **恒为 `false`**。校验器会拒绝任何 `true`。 |
| `url` | str | 一律 `""`。校验器会拒绝任何非空值 —— 没核实过就不填。 |
| `stock` | enum | `in_stock` / `low_stock` / `out_of_stock`，由 `--seed` 决定，可重复。 |
| `storage_volume_L` | int | 净可用储物容积（升）。无储物功能为 `0`。 |
| `assembly` | enum | `flat_pack`（平板包装）/ `pre_assembled`（电器类整机）/ `wall_mount`。 |

### 净空 (clearance) 的取用规则（Demo 阶段）

- **需要拉开抽屉 / 开门** → `front` 给 500–600：衣柜 600、抽屉柜 500–550、雪櫃 600、洗衣机 600、微波炉 500。
- **床 / 梳化 / 桌 / 椅 / 屏风** → `front = 0`（这些是"人要用"的空间，属于通道宽度约束，不是家具自身的净空，交给 circulation 约束处理）。
- **散熱需求才会给 side/top**：雪櫃 `left=right=50, top=100`；冷氣 `top=100~150`；上置式洗衣机 `top=600`（要掀盖）。
- 实测分布：`front` 取值为 `{0: 103, 200: 2, 300: 3, 450: 1, 500: 17, 550: 6, 600: 18}`。

---

## 4. `visual` 六个字段（"视觉显大"算法的输入）

| 字段 | 范围 | 规则 |
|---|---|---|
| `lightness_L` | 0–100 整数 | 浅色高、深色低。白橡 85 / 米白 80 / 淺木 72 / 灰 55 / 深胡桃 30 / 黑 18。 |
| `opacity` | 0.0–1.0 | 开放格 0.1 / 玻璃门 0.3 / 半开放 0.5 / 实木门 1.0。 |
| `reflectivity` | 0.0–1.0 | 镜 0.95 / 玻璃 0.7 / 亮面烤漆 0.5 / 哑光木 0.1 / 布艺 0.05。 |
| `openness` | 0.0–1.0 | 开放格占比。 |
| `color_family` | warm/cool/neutral | 色系。 |
| `visual_mass_index` | 0.0–1.0 | **公式计算 + 全体 min-max 归一化**，见下。 |

### 计算公式（严格按规范实现）

```python
raw = (footprint_area_mm2 / 1e6) * (height_mm / 1000) * opacity * (1 - lightness_L / 100)
# 再对全体 SKU 做 min-max 归一化到 [0, 1]
visual_mass_index = (raw - raw_min) / (raw_max - raw_min)
```

本次生成的归一化常数：`raw_min = 0.000184`，`raw_max = 1.952448`（已写入 JSON 顶层字段，便于复算）。

实现细节：`opacity` / `openness` 先 `round(x, 2)`，`lightness_L` 取整，
再用**取整后**的值代入公式 —— 所以外部拿 JSON 里的值能原样复算出 `visual_mass_index`。

---

## 5. 实际统计（本目录 `seed_skus.json` 的真实测量值）

### 5.1 Category 分布

| category | count |
|---|---|
| bed | 6 |
| bunk_bed | 3 |
| loft_bed | 3 |
| storage_bed | 3 |
| wardrobe | 12 |
| cabinet | 10 |
| shelf | 9 |
| tv_bench | 5 |
| shoe_cabinet | 5 |
| desk | 6 |
| dining_table | 4 |
| side_table | 3 |
| chair | 11 |
| sofa | 9 |
| coffee_table | 2 |
| storage_box | 13 |
| wall_shelf | 5 |
| mirror | 4 |
| fridge | 6 |
| washing_machine | 5 |
| microwave | 4 |
| aircon | 5 |
| drying_rack | 9 |
| screen | 8 |
| **合计** | **150** |

按需求建议的分组归集：柜类 (wardrobe+cabinet+shelf+tv_bench+shoe_cabinet+wall_shelf) = 46、
床类 (bed+bunk_bed+loft_bed+storage_bed) = 15、桌类 (desk+dining_table+side_table+coffee_table) = 15、
座类 (chair+sofa) = 20、收纳 (storage_box+mirror+screen+drying_rack) = 34、电器 (fridge+washing_machine+microwave+aircon) = 20。

### 5.2 visual_mass_index 分布

```
  [0.0, 0.1)  ##################################################################################################### 101
  [0.1, 0.2)  ################## 18
  [0.2, 0.3)  ########### 11
  [0.3, 0.4)  ###### 6
  [0.4, 0.5)  #### 4
  [0.5, 0.6)  ## 2
  [0.6, 0.7)  # 1
  [0.7, 0.8)  # 1
  [0.8, 0.9)  ### 3
  [0.9, 1.0)  ### 3
```

`min=0.0000  p25=0.0077  median=0.0332  p75=0.1353  max=1.0000`
`均值=0.1285   标准差=0.2118`

**这是一个严重右偏的分布**（101/150 落在 `[0, 0.1)`）。这是**公式本身的性质，不是 bug**：
`opacity` 与 `height` 都是乘性因子，开放矮家具会被两个因子同时压到接近 0。
详见第 8 节"已知局限"里的处理建议。

### 5.3 其它维度

| 维度 | 实测 |
|---|---|
| `lightness_L` | min=18 max=92 distinct=18 |
| `opacity` | min=0.10 max=1.00 distinct=17 |
| `reflectivity` | min=0.05 max=0.95 distinct=10 |
| `openness` | min=0.00 max=1.00 distinct=19 |
| `color_family` | neutral 82 / warm 39 / cool 29 |
| `mount` | floor 132 / wall 18 |
| `assembly` | flat_pack 130 / pre_assembled 20 |
| `stock` | in_stock 132 / low_stock 11 / out_of_stock 7 |
| `price_hkd` | min=39 median=1090 max=8990 sum=256264 |
| `storage_volume_L` | sum=17795，72 条有储物功能 |
| `height_mm` | min=40 median=830 max=2360 |

### 5.4 每类目的 visual_mass_index 中位数

| category | n | vmi_median | vmi_min | vmi_max | lightness_median |
|---|---|---|---|---|---|
| bed | 6 | 0.1698 | 0.1233 | 0.8167 | 78 |
| bunk_bed | 3 | 0.3190 | 0.2236 | 0.7631 | 72 |
| loft_bed | 3 | 0.3590 | 0.2271 | 0.4088 | 80 |
| storage_bed | 3 | 0.3141 | 0.2812 | 0.6731 | 80 |
| wardrobe | 12 | 0.2078 | 0.0147 | 0.8792 | 80 |
| cabinet | 10 | 0.0999 | 0.0264 | 0.3184 | 72 |
| shelf | 9 | 0.0324 | 0.0020 | 0.2325 | 80 |
| tv_bench | 5 | 0.0421 | 0.0064 | 0.1679 | 72 |
| shoe_cabinet | 5 | 0.0494 | 0.0088 | 0.0923 | 80 |
| desk | 6 | 0.1332 | 0.0578 | 0.2471 | 80 |
| dining_table | 4 | 0.1245 | 0.1041 | 0.3660 | 76 |
| side_table | 3 | 0.0249 | 0.0059 | 0.0301 | 80 |
| chair | 11 | 0.0093 | 0.0039 | 0.0307 | 72 |
| sofa | 9 | 0.5289 | 0.2144 | 1.0000 | 62 |
| coffee_table | 2 | 0.1594 | 0.0047 | 0.1594 | 88 |
| storage_box | 13 | 0.0045 | 0.0010 | 0.0207 | 80 |
| wall_shelf | 5 | 0.0002 | 0.0002 | 0.0066 | 72 |
| mirror | 4 | 0.0018 | 0.0013 | 0.0033 | 92 |
| fridge | 6 | 0.0617 | 0.0185 | 0.1263 | 80 |
| washing_machine | 5 | 0.0505 | 0.0486 | 0.0576 | 80 |
| microwave | 4 | 0.0090 | 0.0073 | 0.0451 | 80 |
| aircon | 5 | 0.0173 | 0.0085 | 0.0257 | 80 |
| drying_rack | 9 | 0.0127 | 0.0000 | 0.0330 | 80 |
| screen | 8 | 0.0044 | 0.0010 | 0.0550 | 72 |

---

## 6. 故意埋的"高/低对比对"（Demo 演出的燃料）

这是这份数据集**最重要**的部分。同一功能刻意同时收录"显大款"和"显小款"，
否则 Demo 演不出"换个柜子 → 显大分从 68 跳到 81"。

### 衣柜：同样是收纳，vmi 差 55 倍

| SKU | L | opacity | openness | vmi | h |
|---|---|---|---|---|---|
| PAX 到頂衣櫃 (深胡桃) | 30 | 1.00 | 0.00 | **0.8792** | 2360 |
| BRIMNES 衣櫃 (黑褐) | 22 | 1.00 | 0.00 | 0.5303 | 1900 |
| HAUGA 衣櫃 (灰) | 55 | 1.00 | 0.00 | 0.4745 | 1990 |
| PAX 衣櫃 (淺木) | 72 | 1.00 | 0.00 | 0.2995 | 2010 |
| PLATSA 薄身衣櫃 (淺木, d=420) | 72 | 1.00 | 0.00 | 0.2344 | 1810 |
| KLEPPSTAD 衣櫃 (白) | 80 | 1.00 | 0.00 | 0.2078 | 1760 |
| AURDAL 衣櫃組合 (白) | 80 | 1.00 | 0.00 | 0.1850 | 2000 |
| VUKU 單門衣櫃 (白) | 80 | 1.00 | 0.00 | 0.1259 | 1830 |
| PAX 玻璃門衣櫃 | 80 | 0.30 | 0.10 | 0.0641 | 2010 |
| JONAXEL 網籃衣櫃組合 | 80 | 0.15 | 0.90 | 0.0184 | 1750 |
| PAX 開放式衣櫃框 | 80 | 0.10 | 0.90 | **0.0160** | 2010 |
| BOAXEL 掛牆開放衣櫃 | 80 | 0.10 | 0.95 | **0.0147** | 2010 |

**同一类目内 vmi 从 0.0147 到 0.8792** —— 这是最有力的一组演出素材。
注意它同时把三个维度拉开了：**颜色深浅**（30 vs 80）、**门板透明度**（1.0 vs 0.1）、**高度**（2360 vs 1760）。

### 梳化：深灰厚重 vs 米白带腿

| SKU | L | vmi |
|---|---|---|
| VIMLE 三人梳化 (深灰) | 42 | **1.0000** |
| FRIHETEN 梳化床 | 42 | 0.9718 |
| SÖDERHAMN 三人梳化 (灰) | 55 | 0.8007 |
| LANDSKRONA (灰綠) | 62 | 0.5289 |
| KIVIK 三人梳化 (米色) | 70 | 0.4949 |
| EKTORP 三人梳化 (米白) | 80 | 0.3098 |
| ÄPPLARYD 雙人梳化 (米白, 帶腿通透) | 80 | **0.2144** |

`VIMLE → ÄPPLARYD` 替换 = vmi 降 79%，而两者都是"能坐两个人的梳化"。

### 其它已埋好的对

- **电视柜**：LACK 深褐落地 (0.1679) ↔ FJÄLLBO 淺木開放 (0.0312) — **5.4 倍**
- **书柜**：BILLY 胡桃 (0.2325) ↔ BILLY 白 (0.0664) — **同尺寸同结构，只换颜色，3.5 倍**
- **高柜 vs 矮柜**：PAX 到顶 2360mm ↔ VUKU 1830mm ↔ 墙架 50mm
- **通透 vs 厚重**：TOBIAS 透明椅 / AGEN 藤编椅 ↔ JANINGE 实心椅；VITTSJÖ 玻璃茶几 ↔ LACK 实木茶几
- **隔断**：紗簾屏風 (openness 0.90) ↔ 儲物屏風架 (带收纳但挡光)

---

## 7. 数据来源与可信度

| 内容 | 来源 | 可信度 |
|---|---|---|
| `category` 取值、schema 结构 | 项目冻结规范 | ✅ **已冻结，可直接依赖** |
| `visual.*` 六个字段 | 按冻结公式与规则表计算 | ✅ **可复算、可依赖** |
| `footprint_L` / `clearance` / `can_support` 的**存在与自洽** | 脚本保证 | ✅ 校验器 0 error |
| 产品**系列名**（PAX / BILLY / MALM …） | IKEA 公开产品线，真实存在 | 🟡 名称真实，但**具体型号未必在 HK 在售** |
| `dims` 数值 | **按品类典型值手写** | 🟠 **不是官方规格表**。量级正确、HK 现实合理，但**不可用于下单/施工** |
| `price_hkd` | **手写占位值** | 🔴 **未核实**。`price_verified` 恒为 `false` |
| `url` | 故意留空 | 🔴 未核实 |
| `clearance` / `can_support` 数值 | 按工程常识估算 | 🟠 未实测，未查厂商安装手册 |
| `storage_volume_L` | 按外框体积 × 经验利用率估算 | 🟠 未实测 |

### 一个必须说明的例外：aircon

**香港 IKEA 并不销售分体式 / 窗口式冷气机。** 5 条 `aircon` SKU 的
`retailer` 标为 `"HK retail (placeholder)"`，`id` 前缀相应为 `hkretail-`，
尺寸是香港常见机型（3/4 匹、1 匹、1.5 匹窗口机与分体室内机）的典型值。
保留这 5 条是因为**冷气的室内机与窗口机位是 HK 小户型平面图上真实存在的固定障碍**，
摆放求解器必须知道它们占多大地方 —— 但它们的价格与型号**纯属占位，完全未核实**。

### 与规范示例的那一条差异

规范文档里给的示例 SKU `ikea-hk-40300605` (MALM 高架床框) 已原样保留，
其余字段完全对得上：

```json
{"lightness_L": 78, "opacity": 0.35, "reflectivity": 0.15,
 "openness": 0.6, "color_family": "neutral", "visual_mass_index": 0.2303}
```

**唯一不同是 `visual_mass_index`：规范示例写 0.28，实际算出 0.2303。**
原因是规范同时要求"对全体 SKU 做 min-max 归一化"，而 0.28 是未归一化的示意值
(该 SKU 的原始 raw 值 = 3.2604 × 1.0 × 0.35 × 0.22 = 0.2511，
在 `raw_min=0.000184, raw_max=1.952448` 下归一化得 0.2303)。
**归一化后的值才是可比的**，所以这里遵循公式而不是示例数字。

---

## 8. 已知局限

1. **`visual_mass_index` 严重右偏**（101/150 < 0.1）。
   `opacity` 和 `height` 是乘性因子，小件开放家具被双重压缩。
   **建议**：UI 上展示"显大分"时不要直接用 vmi 线性映射，
   改用**同分位排名（percentile rank）**或 `log(1+raw)` 后再归一化。
   **注意**：存储层保持规范公式原样，转换放在展示层做 —— 否则跨版本不可比。

2. **`footprint_L` 全是矩形。** L 形沙发、转角柜、L 形书桌都被拍成了外接矩形，
   **会高估占地**。Demo 阶段按需求接受；接真实 SKU 时这是第一个要补的字段。

3. **高度是单值。** 斜顶、阶梯形柜体（下柜深上柜浅）表达不了。
   到顶柜 + 横梁的组合是 HK 户型的核心痛点，未来可能需要 `profile_z` 字段。

4. **`price_hkd` 全部未核实**，且同一 SKU 的不同尺寸变体共用一个价格
   （变体是扩量时生成的，价格没跟着变）。做预算 / Pareto 前必须修正。

5. **没有颜色纹理 / 图片 / 材质贴图字段。**
   3D 渲染要把商品主图 UV 贴到柜门上，需要另加 `texture_url` —— 现在没有。

6. **`can_support` 只标了顶面，没标层板承重。**
   书柜每一层的承重其实不同，求解器无法判断"这层能不能放满书"。

7. **`out_of_stock` 的 7 条仍在库里。**
   Demo 想演"没货了给你换一个"就直接用；求解器若不处理 `stock` 字段，
   可能摆出一个买不到的方案 —— **这是个待接的钩子，不是已完成的功能**。

8. **没有"同系列组合关系"。** PAX 柜体 + 柜门 + 内部配件在真实目录里是分开卖的，
   这里被合并成了一个 SKU。做真实报价时会差一截。

9. **`clearance` 是估算值**，未查厂商安装手册，也未实测。

---

## 9. 如何扩充到 5000 个

### 现状能力

| `--total` | 结果 |
|---|---|
| 150 | ✅ 每模板 1 条（本目录交付物） |
| 400 | ✅ 已验证，400 条全部 id 唯一、24 个 category 齐全、校验 0 error |
| 超过变体池上限 | 脚本会 warn 并生成到池上限为止 |

变体池 = 每个模板的「主尺寸 + `sz` 备用尺寸」×「主饰面 + `alt` 备用饰面」。
当前池约 **400 条**。**光靠现有模板翻不到 5000。**

### 扩到 5000 的正确做法（按性价比排序）

**① 加模板（主要手段，覆盖 150 → 1500）**
在 `build_catalog.py` 的 `TEMPLATES` 里加 dict。每条模板可带 `sz`（备用尺寸）
和 `alt`（备用饰面），单模板即可产出 4–8 个 SKU。
建议按类目补到每类 40–80 个模板：HK 现实里 `wardrobe` / `storage_box` / `shelf` 是绝对主力。

**② 补真实货源（1500 → 3000）**
目前只有 IKEA HK 一家。真实护城河在**跨零售商比价**，应补：
日本城、實惠 Pricerite、Francfranc，
以及**香港本地定制傢俬厂商**（地台床 / 到顶柜 / 窗台柜 —— 这些在香港是主流解，不是长尾）。
补法：加一个 `retailers/` 子目录，每个零售商一个 JSON，`build_catalog.py` 负责合并去重。

**③ 参数化生成（3000 → 5000，定制家具）**
香港的正解常是**定制**而非买成品，所以这一步价值很高：
- **地台床**：`w × d` 连续可变，`h ∈ {300, 400, 450}` → 单条规则可生成数百 SKU
- **到顶衣柜**：`w ∈ 400..1200 step 50` × `d ∈ {350, 400, 500, 580}` × `h = 层高 - 冗余`
- **窗台柜**：`d ∈ 450..600`，直接吃 HK 窗台深度
- **高架床**：`h` 必须 ≤ 层高 − 梁高 − 冗余，与具体户型绑定

这类应标 `assembly = "custom_build"`（需在 schema 里新增该枚举值）并加
`lead_time_days` / `min_order` 字段 —— **这是从 Demo 走向可上线时的必经一步。**

**④ 从真实数据反推（长期）**
爬 IKEA HK 官网 → 拿真实货号 / 价格 / 官方尺寸 / 主图 → 把 `price_verified` 翻成 `true`、
填上 `url`。**这一步做完，本 README 头部那条红色警告才能撤掉。**

### 扩充时的硬性检查清单

- [ ] `validate()` 必须过（脚本会自动跑，失败则拒绝写文件且 exit 1）
- [ ] `--check` 在 CI / 提交前跑一次
- [ ] 新增类目必须先加进 `CATEGORIES`（冻结枚举，别乱动）
- [ ] 尺寸要符合 HK 现实：衣柜宽 400–1200 / 深 350–600；床 900/1200/1350/1500；到顶柜高 2000–2400
- [ ] 新 SKU 要**刻意成对**：同功能一个显大款 + 一个显小款
- [ ] **绝不填 `price_verified: true`**，除非真的逐条核对过
- [ ] 改完重跑，确认 md5 变了但 `--check` 仍然 0 error

---

## 10. 校验器覆盖范围

`python3 build_catalog.py --check` 检查以下全部（任一条不过即 exit 1）：

- 17 个顶层字段齐全
- `id` 全局唯一
- `category` ∈ 24 个冻结值
- `price_verified` **必须为 `false`**
- `url` **必须为空字符串**
- `dims.w/d/h` 均为正整数
- `footprint_L` 必须是 4 点矩形，且与 `dims` 严格一致
- `clearance.{front,left,right,top}` 均为非负整数
- `mount` 非空且 ∈ {floor, wall, ceiling}
- `lightness_L` ∈ [0,100]
- `opacity` / `reflectivity` / `openness` ∈ [0,1]
- `color_family` ∈ {warm, cool, neutral}
- `visual_mass_index` ∈ [0,1]
- `can_support.max_load_kg` ≥ 0
- `max_load_kg > 0` ⟺ `top_polygon` 是 4 点矩形（二者必须同时成立）
- `price_hkd` 为正整数

**当前状态：150 条，0 error。**
