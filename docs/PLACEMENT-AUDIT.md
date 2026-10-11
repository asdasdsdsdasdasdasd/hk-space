# 家具摆放审计 —— 为什么位置看起来是随机的

审计日期：2026-10　基准：Farnsworth House（室内 16840 x 8656mm，中央服务核心 7506 x 3816mm）

本文回答一个问题：**「家具放置的位置几乎是随机，没有逻辑联系」** —— 是哪里出的问题。

结论先写：**是真的随机，而且原因逐条可以定位到代码。** 最直接的一条：
**引擎里根本没有「餐椅要跟着餐桌」这条规则**，所以 6 张餐椅被扔到最低曝光度的角落，
其中 2 张落在大厅东墙，离自己的餐桌 **15.3 米**。

---

## 0. 证据：功能关系在真实方案 vs 本引擎

同一空间、同一份 22 件清单。距离 = 家具中心点距离，单位 mm。

| 功能关系 | 原设计（c.1955 真人方案） | 本引擎 | 倍数 |
|---|---|---|---|
| 餐椅1 - 餐檯 | 847 | **15297** | 18x |
| 餐椅2 - 餐檯 | 822 | **10209** | 12x |
| 餐椅3 - 餐檯 | 1276 | 1458 | ok |
| 餐椅4 - 餐檯 | 1265 | 1774 | 1.4x |
| 餐椅5 - 餐檯 | 835 | 1269 | ok |
| 餐椅6 - 餐檯 | 808 | **15285** | 19x |
| 茶几 - 沙发 | 2202 | 1189 | 反而更近 |
| **床 - 床头柜** | 1864 | **14674** | 8x |
| 书桌 - 书桌椅 | 800 | **5296** | 6.6x |
| 衣柜 - 床 | 2251 | 9000 | 4x |
| 躺椅1 - 躺椅2 | 800 | **4472** | 5.6x |
| 扶手椅1 - 扶手椅2 | 650 | **7863** | 12x |
| 躺椅 - 茶几 | 805 | **9653** | 12x |

本引擎 22 件的实际落点（模型坐标，房间 17040 x 8856；x 向东，y 向北）：

~~~
#bed           (14230, 4930) rot=270     <- 床在东北
#bedside       (  255,  455) rot=  0     <- 床头柜在西北角，离床 14.7m
#chair_d1      (16705, 6270) rot= 90     <- 餐椅贴东墙
#chair_d2      (11505, 8470) rot= 90
#chair_d3      (  305, 5945) rot= 90     <- 餐椅贴西墙
#chair_d4      ( 2745, 5705) rot=  0
#chair_d5      ( 2670, 7105) rot=  0
#chair_d6      (16705, 6870) rot= 90     <- 餐椅贴东墙
#chair_desk    ( 8040, 7830) rot= 90
#chaise1       (12740, 2855) rot= 90
#chaise2       (13540, 7255) rot= 90     <- 两张躺椅分居南北
#cocktail      (14405, 6405) rot=  0
#daybed        (15210, 7280) rot=180
#desk          (10840, 3335) rot=  0
#dining_table  ( 1420, 6885) rot=  0     <- 餐桌在西南
#hifi          (16261, 2566) rot=  0
#risom1        ( 1330, 8380) rot=180
#risom2        ( 9180, 7930) rot= 90
#slipper1      ( 4780, 7140) rot= 90
#slipper2      (16580, 7940) rot= 90
#stool         (  820,  420) rot=  0
#wardrobe      ( 6480,  355) rot=  0     <- 衣柜横在北玻璃前
~~~

**观察**：这就是「随机」的真实模样 —— 它不是随机数，而是
**一个只奖励「贴墙 + 低曝光」的目标函数的必然解**。

量化的两条：

（1）**贴墙**。每件家具到最近可走边界的距离（mm）：

~~~
#wardrobe    0    #chair_d3    0    #bedside     0    #chaise1    30
#chair_d1   30    #chair_d6   30    #risom2     44    #chair_desk 44
#desk       70    #slipper2   80    #risom1     96    #chair_d2  116
#hifi      118    #chaise2   154    #bed       154    #stool     200
#slipper1  380    #dining_table 400  #daybed    820    #cocktail  954
#chair_d5 1446    #chair_d4  2400
中位数 106mm，平均 344mm；22 件里 16 件在 200mm 以内。
~~~

（2）**低曝光**。曝光度图归一化到峰值 = 1.0，这 22 件落在 **0.05 ~ 0.43**，
即全部挤在曝光度最低的一半区域里（房间面积 122 m2，视野最好的位置没有被用到）。

两条加起来的后果就是：**家具全部被推向边角，而且彼此远离** ——
因为「离别人远」既降低互相遮挡、又让每件都能贴到墙。

---

## 1. 全部规则清单

### 1.1 宜居检查（packages/geometry/hkgeometry/livability.py）

共 14 条，形式为 Violation(rule, severity, subjects, detail, fix)。

| # | rule | 级别 | 触发条件 | 阈值来源 |
|---|---|---|---|---|
| 1 | sofa_faces_tv | hard | 沙发正面与电视方向的点积 < 0.5 | 常量 |
| 2 | tv_faces_sofa | hard | 电视柜正面与沙发方向点积 < 0.5 | 常量 |
| 3 | tv_too_close | hard | 沙发到电视 < 1400mm | 常量 |
| 4 | tv_too_far | soft | 沙发到电视 > 4000mm | 常量 |
| 5 | coffee_not_in_front | hard | 茶几相对沙发正面的点积 < 0.3 | 常量 |
| 6 | coffee_distance | soft | 茶几离沙发 < 250 或 > 1000mm | 代码 250/1000，提示文字却写「合理 300-900」（不一致） |
| 7 | bed_head_not_against_wall | hard | 床头方向点积 <= 0.7 或离墙 > 320mm | WALL_TOL_MM+200 |
| 8 | nightstand_far_from_bed | soft | 任何 side_table / drawer_chest 离床 > 500mm | 常量 |
| 9 | not_backed_to_wall | hard | 大件家具离最近墙 > 300mm | WALL_TOL_MM+180 |
| 10 | no_clearance_front | hard | FRONT_CLEARANCE 类家具正前方探测点出界 | FRONT_CLEARANCE |
| 11 | clearance_blocked | hard | 正前方 cl 距离内有别的家具 | FRONT_CLEARANCE |
| 12 | walkway_blocked | hard | 从大门洪水填充走不到某件家具前面（人宽 600mm） | WALK_MIN_MM=600 |
| 13 | door_blocked | **soft** | 任一门扇扫掠区被占 > 0.03 m2 | 0.03 m2 |
| 14 | sofa_faces_door | soft | 沙发正面直冲大门，点积 > 0.85 | 常量 |

**这张表里没有任何一条是「功能分组」规则。** 没有「餐椅要靠近餐桌」、
没有「躺椅要成对」、没有「衣柜要在睡区」、没有「书桌要对着窗或墙」。
14 条里 11 条是**可行性**（打不开门 / 走不过去 / 悬空），3 条是**老三条**（沙发-电视-茶几）。

### 1.2 朝向规则 FACE_RULES（greedy.py:127）

~~~
sofa / chair / chaise_longue            -> tv
desk                                    -> window
bed / bunk_bed / loft_bed / storage_bed -> center
coffee_table / side_table / dining_table / screen -> center
wardrobe / cabinet / shelf / tv_bench / shoe_cabinet / drawer_chest /
wall_shelf / fridge / washing_machine / open_shelf / storage_box /
drying_rack / microwave / mirror / pegboard -> away_from_wall
~~~

**三条致命点**：

1. **chair 归到 tv 规则**。椅子被要求面向电视柜。餐桌椅也是 chair，
   于是 6 张餐桌椅全部去找电视柜。
2. **没有 tv_bench 时，tv 规则会静默退化**（cpsat_solver.py:348 _facing_target）：

   ~~~
   if rule == 'tv':
       tv = next((a for a in anchors if a.category == 'tv_bench'), None)
       if tv: return ('point', tv.center())        # 没有 tv 就什么都不返回
   if rule == 'window' and room.window_centers(): ...
   if rule == 'away_from_wall': return ('away', None)
   c = room.usable_geo().representative_point()    # <-- 掉到这里
   return ('point', (c.x, c.y))
   ~~~

   Farnsworth 方案里没有电视柜，于是 **11 张椅子的正面全部朝向房间的
   representative_point（一个任意的几何代表点）**。它们互相之间、和任何家具之间，
   都没有关系。**这一条单独就足以造成「看起来是随机摆的」。**
   dining_table 也是 center 规则，同样掉到这个代表点。
3. **window 规则取 window_centers()[0]（列表第一个），不是最近的那扇窗**。
   整栋楼四面玻璃时，书桌朝的是「第一扇窗」，与它实际挨着哪面墙无关。

### 1.3 功能关系 RELATIONS（cpsat_solver.py:128）

全表只有 **5 条**：

~~~
(sofa, tv_bench)       d 1600-3600  dot >= 0.85  in_front
(tv_bench, sofa)       d 1600-3600  dot >= 0.85  in_front
(coffee_table, sofa)   d 300-900    in_front
(chair, desk)          d 300-900
(side_table, bed)      d 0-500
~~~

对照 CATEGORY_ROOMS 里的品类表，**关系覆盖率 = 5 / 25**。
缺失且本案直接相关的：**餐椅<->餐桌**、餐桌<->餐椅、躺椅<->躺椅、躺椅<->茶几、
扶手椅<->茶几、衣柜<->床、柜子<->*、圆凳<->餐桌。

### 1.4 品类准入 CATEGORY_ROOMS / 偏好权重 DEFAULT_PREF（house.py:39 / 78）

- CATEGORY_ROOMS：品类 -> 允许的**房间功能**。房间功能只有 living / bedroom /
  kitchen / bath / storage / open 六种。**粒度是「房间」，不是「区域」。**
- DEFAULT_PREF：品类 -> 房间功能的偏好分（0-10）。
- 结论：**引擎的世界模型里没有 dining zone / sleeping zone / TV zone 这种概念。**
  一个 120m2 的开放空间和一间 8m2 的睡房，表达力完全一样 —— 都是「一个 open 房间」。
  所以求解器没有任何「应该放在哪一块」的先验，只有「哪一格代价低」。

### 1.5 硬约束（建模阶段过滤，不进求解器）

| 约束 | 位置 | 说明 |
|---|---|---|
| 不出界 / 不落进 core 洞 | _cost_grids 里的 poly.contains | 房间多边形（含 holes） |
| 不落进门扇扫掠区 | door_approach_zones | 内开门 = 铰链侧四分之一圆 + 150mm 门槛带；面积上限 30% 可用面积，深度下限 600mm |
| 不落进主通道 | circulation_zones | 实测**返回空列表**（cpsat_solver.py:177 注释「默认不另加通道」） |
| 不重叠 | AddNoOverlap2D | 变长区间，尺寸由 r 经 AddElement 决定 |
| 前方净空（仅锚定件） | anchor_fronts | 只对已放好的锚点查，批量件之间不查 |
| 关系（仅严格模式） | strict_relations | **只用于锚定阶段**，批量阶段写死 False |

---

## 2. 目标函数 —— 全部项与权重

GreedyWeights（greedy.py:146）。所有软项先归一化到 0..1，再乘权重。

| 项 | 权重 | 方向 | 公式 |
|---|---|---|---|
| exposure | **6000** | 越小越好 | W.exposure * mass * emap.value_at(格子中心) |
| facing | 4000 | 越小越好 | W.facing * (1 - dot) / 2 |
| occlusion | 4000 | 越小越好 | 仅 h >= 1200：W.occlusion * tall * max(0, 1 - 离入口距离/3000) |
| circulation | 30000 | 越大越差 | W.circulation * path_hit（路径被占） |
| corridor | 2000 | 越大越差 | W.corridor * 超出量 * 段长/1e3 |
| wall_contact | 500 | 越大越好 | -W.wall_contact * max(0, 300 - 离边界距离) / 300 |
| relation | **1.0** | 越大越差 | W.relation * viol * 5000 |

量级对比（按本案实际 SKU 尺寸算 mass = visual_mass_raw() ** 0.45）：

| 家具 | mass | exposure 项最大贡献 | relation 项最大贡献 |
|---|---|---|---|
| 衣柜 3700x650 h1800 | 1.01 | 6036 | 该品类无关系 |
| 椅子 480x550 h780 | 0.20 | 1224 | 该品类无关系 |
| daybed 1960x900 h600 | 0.24 | 1462 | — |
| 茶几 750x750 h400 | 0.13 | 780 | 5000 |

关系项本身量级不小，但有两个致命前提：
1. **只有 5 对品类有关系**，本案 22 件里只有 2 件（茶几、床头柜）能吃到；
2. **批量路径根本不计算关系**（见下）。

---

## 3. 求解流程 —— 7 步，以及每一步实际做了什么

~~~
L1  assign_rooms()     品类 -> 房间。本案只有一个房间，全部进同一个。
L2  solve_house()      每间房一个进程：exposure_map + 3-4 条锚定顺序并行试
    +- _solve_with_order()
    |   +- 第1步 锚定阶段  对 anchors_order 逐件 _cost_grids(strict=True) -> argmin
    |   |                 顺序 = ANCHOR_ORDER 命中的品类 + 所有 FRONT_CLEARANCE>0 的品类
    |   +- 第2步 批量阶段  剩下的所有件一次解完 _cost_grids(strict=False) + AddNoOverlap2D
    +- repair_rotations() 逐件原地试 4 个朝向，取「宜居硬伤最少」
    +- 放宽阶梯           门洞走道缩到 60% / 40% / 25% / 0 再试
L2.5 house.py 修复遍     走道堵 / 门被挡 -> 尝试搬开
L3  verify_house()      可达性 + 门洞 + 整屋视觉负荷
~~~

### 缺陷 A（最致命）：批量阶段不计算关系

cpsat_solver.py:814

~~~
grids, nx, ny, ncells = _cost_grids(room, rest, emap, W, corridors, doors,
                                    spines, placed, [], soft_paths=soft_paths)
                                    #      ^^^^^^ anchors  ^^ tvs
~~~

_cost_grids 的第 8 个参数是 anchors，第 9 个是 strict_relations（默认 False）。

而 _rel_violation(item, cx, cy, anchors, ...) 是**只对 anchors 里已有的家具**求关系。
批量阶段的 anchors = placed = **只有锚定阶段放好的那几件**。

本案的锚定集合 = ANCHOR_ORDER 命中的品类（sofa / chaise_longue / coffee_table / bed / wardrobe）
+ 所有 FRONT_CLEARANCE > 0 的品类（wardrobe / cabinet / ...）
= **{daybed(sofa), chaise1, chaise2, coffee_table, bed, wardrobe, hifi(cabinet)}**

批量集合 = {6 张餐椅, 餐桌, 书桌, 书桌椅, 2 张 risom, 2 张 slipper, 床头柜, 圆凳}

于是：

- (coffee_table, sofa)：两件都是锚定件 -> **会算** ✓
- (side_table, bed)：床头柜在批量、床在锚定 -> **会算** ✓
  （所以床-床头柜 14.7m 会被罚，但只罚 5000，抵不过别的项）
- (chair, desk)：**两件都在批量里**，anchors 里一个都没有 -> **永远不生效** ✗
- 其余 20 件：**根本没有对应的 RELATIONS 条目** ✗

### 缺陷 B：锚定顺序是按「品类」选的，不是按「功能组」

ANCHOR_ORDER = [tv_bench, sofa, chaise_longue, coffee_table, bed, storage_bed,
bunk_bed, loft_bed, wardrobe]

本案里**餐桌不在锚定顺序里**（不在 ANCHOR_ORDER，FRONT_CLEARANCE[dining_table] = 0），
所以餐桌和 6 张餐椅**全部落到批量阶段**，而批量阶段之间不查任何关系。
= 「餐桌和餐椅之间没有任何联系」是**设计上就有的**，不是 bug。

### 缺陷 C：批量阶段没有「前方净空」检查

代码注释自己写了（cpsat_solver.py:95-97）：

> 批量路径（AddNoOverlap2D）只能保证不重叠，**查不了「A 挡住 B 开门」**

所以批量件之间互相挡住前方，只能靠 L3 事后检查报出来，求解时并不知道。

---

## 4. 全部 prompt（原文）

### 4.1 Agent 模式 agent.py:78 SYSTEM

给了 8 个工具（inspect_flat / list_catalog / check_fit / set_brief /
generate_design / inspect_room / change_item / finish），硬规则 7 条，关键部分：

~~~
硬规则：
  · **唔准輸出坐標**。你講「要咩、放邊間房」，位置由求解器決定。
  · 可行性由引擎裁決。佢駁回你嘅動作時會講清楚差幾多 mm，照住改。
  · 固定潔具（馬桶/洗手盆/浴缸/淋浴/灶台/抽油煙機/廚櫃檯面）钉死在來去水位...
  · 庫存冇合尺寸嘅款，就用 change_item 的 custom 訂造...
  · set_brief 係**整個清單換掉**...
  · 預算有限（預設 24 次工具調用）...
~~~

**审计意见**：这个 prompt 里**没有任何一句关于「空间关系」的指导**。
LLM 被明确禁止输出坐标，工具也只有「放哪间房」这个粒度 ——
**所以哪怕 LLM 完全知道「餐椅要围着餐桌」，它也没有任何工具能表达这件事。**
这是 agent 架构层面的能力缺口，不是提示词写得不好。

### 4.2 顾问模式 llm_advisor.py:97 SYSTEM_PROMPT

11 条硬规则，7 个动作（shrink_sku / move_item / drop_item / try_nudge / keep /
add_item / define_custom）。核心约束：

~~~
1. 絕對不要輸出坐標、角度、尺寸數字以外的幾何描述。你只能輸出下面的動作。
4. 每條顧問意見都要有決定...
5. 選擇優先級（由便宜到貴）：try_nudge < shrink_sku < move_item < drop_item
11. ★ 升級規則：如果引擎回報你上一輪的動作做唔到，你必須換一個**更強**的動作
~~~

**审计意见**：动作集合里**没有任何「把 A 挪到 B 旁边」的操作**。
最接近的是 try_nudge（「引擎自己搜方向」），它优化的目标是门洞被占面积，
与功能关系无关。

### 4.3 顾问收到的输入（advisory.py）

顾问收到的「意见」**全部来自 livability 的 14 条 violation 和 L1 的漏放**：

~~~
round1: [客廳 走不到 #shelf 前面, 睡房 有 1 件放不下：#bed,
         浴室 有 1 件放不下：#shower, 廚房 有 3 件放不下：...,
         廚房 門口走道被 #microwave 擋住咗（0.08 m2）]
~~~

**没有一条是关于「摆得不好看 / 不成组 / 动线不合理」的。**
所以 LLM 能看到的问题 = 可行性问题，永远看不到布局问题。

---

## 5. LLM 实际做的决策

### 5.1 Agent 模式（out/agent_trace_b.json，27 次工具调用，824s）

~~~
 1 inspect_flat     {}
 2 set_brief        12 件
 3 list_catalog     wardrobe        <- 提示词已说索引在 inspect_flat 里，仍然去查
 4 list_catalog     sofa
 5 list_catalog     dining_table
 6 set_brief        重写清单
 7 generate_design
 8-11 inspect_room  living / bath / kitchen / bedroom
12 change_item      add shower            (没给尺寸)
13 change_item      add kitchen_counter   (没给尺寸)
14 change_item      drop #coffee_liv      「窄廳放唔落梳化+茶几+餐檯三組」
15 change_item      add shower 900x900x2000      <- 与 12 重复
16 change_item      add kitchen_counter 1200x600 <- 与 13 重复
17 generate_design
18 inspect_room     living
19 list_catalog     screen
20 list_catalog     tv_bench
21 change_item      move #dry_liv -> bedroom
22 change_item      drop #tv_liv    「求解器擺唔到梳化對正電視（夾角 70°）」
23 generate_design
24-27 inspect_room  living / bedroom / bath / kitchen
（没有调用 finish，summary 是预算耗尽后的强制兜底）
~~~

**审计意见**：

- 12/13 与 15/16 是**同一件事做了两次**（第一次没带尺寸）。
- 21 把晾衣架从客厅移进睡房 —— **纯粹的「从有问题的地方挪走」，没有任何空间理由**。
- 22 直接**删掉电视柜**，因为求解器摆不出「沙发对正电视」。这是「删掉问题」而不是「解决问题」。
- **全程 27 次调用里，没有一次表达过任何空间关系**（因为它表达不了）。

### 5.2 顾问模式（out/llm_trace_b.json，4 轮）

| 轮 | 意见数 | LLM 决定 |
|---|---|---|
| 1 | 5 | try_nudge #shelf；shrink_sku #bed；... |
| 2 | 4 | **move_item #shelf -> bedroom**；keep #bed；... |
| 3 | 3 | **drop_item #shelf**；shrink_sku #fridge；... |
| 4 | 3 | **move_item #shelf -> bedroom**（又回到第 2 轮） |

**「客廳 走不到 #shelf 前面」这条意见从第 1 轮挂到第 4 轮都没有消除**，
而 LLM 在第 2 轮和第 4 轮给出了**完全相同的动作**。
这是一个**不收敛的闭环**：动作执行了，但意见不变，LLM 只能反复试同一招。

---

## 6. 缺陷清单（按影响排序）

| # | 缺陷 | 位置 | 证据 | 影响 |
|---|---|---|---|---|
| **A** | 批量阶段 strict_relations=False，且 anchors 只含锚定件 | cpsat_solver.py:814 | 全屋 22 件里只有 2 件能吃到关系项 | 关系形同虚设 |
| **B** | RELATIONS 只有 5 条，缺「餐椅<->餐桌」等 | cpsat_solver.py:128 | 6 张餐椅距餐桌最远 15.3m | **直接造成「随机」观感** |
| **C** | chair 归到 tv 规则，且无 tv 时退化到房间代表点 | greedy.py:128 + cpsat_solver.py:348 | 无电视柜时 11 张椅子朝同一个任意点 | **直接造成「随机」观感** |
| **D** | 世界模型里没有「区域 / zone」概念 | house.py:39 | 120m2 开放空间 = 1 个 open 房间 | 求解器没有「该放哪块」的先验 |
| **E** | 目标函数奖励「贴墙 + 低曝光」，没有「功能靠近」项 | greedy.py:146 | 22 件全部落在房间边界 600mm 内 | 系统性把家具推到边上 |
| **F** | 批量阶段没有前方净空检查 | cpsat_solver.py:95 注释自承 | 只能事后报 | 互相挡门 |
| **G** | 顾问收到的意见只有可行性，永远没有布局质量 | advisory.py | 4 轮意见全是「放不下 / 走不到」 | LLM 看不到问题 |
| **H** | LLM 没有任何表达空间关系的工具 | agent.py:78 / llm_advisor.py:48 | 27 次调用无一涉及关系 | 大脑想到了也做不了 |
| **I** | 顾问闭环不收敛（同一动作反复） | agent / advisor 循环 | #shelf 意见 4 轮不变 | 白烧调用 |
| **J** | Agent 从不调用 finish，靠兜底 | agent.py budget nudge | 27 次调用无 finish | 交付质量不可控 |
| **K** | bed_head_not_against_wall / not_backed_to_wall 是香港实心墙假设 | livability.py:245 / 266 | 在四面玻璃的 Farnsworth 上误报 3 条 | 用错尺 |
| **L** | nightstand_far_from_bed 把任何 side_table 都当床头柜 | livability.py:203 | 圆凳被当成床头柜，报「离床 12.7m」 | 误报 |
| **M** | coffee_distance 代码 250/1000 与提示文字 300-900 不一致 | livability.py:239 | — | 口径混乱 |
| **N** | window 规则取第一扇窗而非最近 | cpsat_solver.py:354 | 四面玻璃时书桌朝向与所处位置无关 | 朝向无意义 |
| **O** | 大空间靠 MAX_GRID_CELLS=6000 把网格放粗到 200mm | cpsat_solver.py:108 | 家具位置精度 200mm | 精度换速度（可接受，但要知道） |

---

## 7. 建议修法（按性价比排序）

1. **补一张空间关系表**（缺陷 B）。做成数据而不是代码：
   (self_cat, other_cat) -> {dmin, dmax, in_front, dot, weight, hard}，
   补上 餐椅<->餐桌、餐桌<->餐椅、躺椅<->躺椅、扶手椅<->茶几、衣柜<->床、圆凳<->餐桌。
   **这一条单独就能消掉「看起来随机」的大部分观感。**

2. **修批量阶段**（缺陷 A）：把 anchors 从「只有锚定件」改成「锚定件 + 同批次里
   已经定下来的件」，并且在批量阶段也先跑一遍 strict_relations 求一次，
   有解就用严格解。至少要把 (chair, desk) 这种**两件都在批量里**的关系接上。

3. **给朝向兜底**（缺陷 C）：tv 规则没有 tv 时，退化成「朝向最近的功能伙伴」
   （餐桌、书桌、茶几），再退化成「朝向房间长轴」；**绝对不允许退化成几何代表点**。

4. **引入 zone**（缺陷 D）：房间内可以标注命名区域（餐区 / 睡区 / 起居区 / 工作区），
   由户型图语义或 LLM 给出；品类在 zone 上有偏好权重。
   这样「餐桌 + 6 椅」就是一个带锚点的组，而不是 7 个独立个体。

5. **把「功能靠近」写进目标函数**（缺陷 E）：现在的关系项只有在 RELATIONS 命中时才非零；
   应该对**每一对**有关系的家具都给分，且权重要与环境项配平（不能 5000 vs 6000）。

6. **顾问层要能看到布局质量**（缺陷 G / H）：advisory 除了 14 条 violation，
   还要输出「成组度 / 关系违背」报告；动作集合要加 group_with(inst, target)。

7. **让香港专属规则可开关**（缺陷 K）：not_backed_to_wall / bed_head_not_against_wall
   应该由建筑类型（实心墙住宅 / 玻璃幕墙亭子 / 开放式单位）决定是否启用，
   而不是写死在宜居检查里。

---

## 8. 怎么复现本文所有数字

~~~
cd /mnt/d/openclaw/hk-space
python3 services/floorplan/make_farnsworth_plan.py         # 生成户型
python3 scripts/verify_target_overlay.py                   # 对照组核对
python3 scripts/compare_farnsworth.py --time-limit 90      # 求解 + 对比（约 5 分钟）
python3 scripts/compare_farnsworth.py --reuse              # 只重画
python3 -c "import json;print(json.load(open('out/agent_trace_b.json'))['calls'])"
python3 -c "import json;print(json.load(open('out/llm_trace_b.json'))[0]['decisions'])"
~~~
