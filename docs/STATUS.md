# STATUS — 2026-10-13 (v3 户型 + 求解器三个 bug)

> 这份文件是**接力棒**：写清楚现在停在哪、下一步做什么、有哪些坑已经踩过。
> 深挖求解器留到之后，本轮到此为止。

---

## 1. 已完成并验证

**安柏苑 05 室 v3 转录**（唯一来源 = 房委会售楼说明书向量 PDF 上的印刷尺寸链）

| | |
|---|---|
| 生成器 | services/floorplan/make_hk_onpak05.py |
| 产物 | out/plan_hk_onpak05.json |
| 说明 | docs/HK-ONPAK05-V3-TRANSCRIPTION.md |
| 核对图 | out/ref/onpak05_v3_overlay_on_official.png（叠在官方图上，全屋偏差 <= 100 mm）|
| | out/ref/onpak05_v3_vs_official.png（左=重建，右=官方）|

室内净面积 **37.16 m²**，在官方图（转正 13.5° 后）上按颜色量得 37.6 m²，差 **1.2%**。
493 平方呎 = 45.8 m² 实用面积，45.8 x 0.81 = 37.1 —— 差额正好是内墙 + 外墙一半。

---

## 2. 本轮找出的三个真 bug

### 2.1 整层楼「一扇门都没有」（我引入的，藏了整轮）

v3 生成器没有把 `openings` 汇总到**房间层**。`load_flat` 只读房间层那一个键
（wall 里面嵌套的 openings 会被直接丢掉），所以引擎看到的是一间无门户型。

后果：`door_approach_zones()` 返回空 → `repair_door_blocks()` 和 `relayout_search()`
都撞在它们第一行的 `if not zones: return` 上，**静默什么都不做**。
也就是说之前那批基准里，第 6 / 7 / 12 条是在空跑。

修法（生成器）：
- 每个房间输出 `openings` 列表
- 浴室 / 厨房各自登记自己的四面墙；**共用墙两边都要登记**，
  因为 `door_approach_zones` 是靠「墙的哪一侧在房间多边形里」决定门往哪边开的
- 厨房南墙单独一段（否则会继承大门，算出一个门心落在房外的假门扇区）

### 2.2 主通路窄度只可以「否决」，不可以「引导搜索」

`relayout_search` 的旧写法：先用 `_cheap_bad` 挑出**唯一一个**候选，然后才问
`_narrow_artery_penalty`。所以「太窄」只能否决一个已经选好的位，永远不能让人去找一个更宽的位。

修法：先用 `_cheap_bad` 筛出 `ARTERY_TOPK = 12` 个候选，再连主通路窄度一起排 `(bad, artery)`。

### 2.3 我修 2.2 时写出的死循环（害我查了 40 分钟）

在候选扫描里我写了：

```python
if bad > cur_bad:
    continue          # 错！
...
y += step             # 被 continue 跳过了
```

`continue` 作用的是**最内层的 `while y ...` 循环**，于是 `y += step` 永远不执行。
实测现象：`x=1490 y=3630` 一动不动，候选计数器一路升到 **635,800**，
而这个网格最多只有 **2,024** 个位置。

修法：把条件写成嵌套 `if`，绝不在这个位置用 `continue`。

---

## 3. 性能优化（顺手做的，值 30 倍）

| | 之前 | 之后 |
|---|---|---|
| `_cheap_bad` | ~5 ms | **0.138 ms** |
| 做法 | 每个 zone 无条件算 `intersection().area` | `shapely.prepare` 一次 + 先用便宜的 `intersects` 快筛 |
| `Placement.footprint()` | 每次新建 shapely box | 按 `(x,y,rot,w,d)` 缓存，原地改坐标也不会读到过期值 |
| `Placement.center()` | 建 box 再取 centroid | 直接算术 |

坐标下降一个 pass 要评估约两万个候选位，所以这三项是乘数级的。

---

## 4. 修完之后的基准（out/bench_after_fix.log）

| 案例 | 通过 | 最窄通路 |
|---|---|---|
| onpak05 安柏苑 05 室 | **8/12** | 81 mm |
| stress_tiny 2.5x3m | 12/12 | 1000 mm |
| stress_narrow 2x5m | 11/12 | **830 mm**（之前 430）|

onpak05 挂的四条：6（洗衣机侵占厨房门洞 1080 mm²、淋浴间侵占浴室门）、
9（台面和灶台被洗衣机挡住）、11（沙发面壁）、12（81 mm）。

**第 10 条（餐椅聚拢）现在过了**：1105 / 712 / 1006 / 729 mm —— 主通路那个修法顺带把它修好了。

---

## 5. ★ 下次开工第一件事：先质疑第 12 条的量法

第 12 条的 `min_corridor` 是**所有房间取最小**，所以 3.8 m² 的厨房
（净 2040 x 1861）在主导这个数。那个厨房要放 1700 台面 + 灶台 + 雪柜 + 洗衣机，
本来就放不下 900 mm 的中央通路。

**证据**：单独跑客厅（不过 entry_point）`min_corridor` = **700 mm**，不是 81。

所以要决定第 12 条怎么改：
- (a) 把小厨房 / 浴室排除在这个判据之外；或者
- (b) 换成真正的**瓶颈净宽**（由大门走到每件家具前面，路上最窄一段有多阔）。
  `scripts/diag_clearance.py` 已经写好这个量法（栅格化自由空间 → 距离场 → 最大化最小净距），
  直接跑就有数。

---

## 6. 现在还留在树里的调试代码（要么保留成正式开关，要么删）

`packages/geometry/hkgeometry/cpsat_solver.py` 里的 `_tlog()`，由环境变量控制：

- `HK_STAGE_TIMING=1` —— 每段耗时（anchors / grids / batch / repair_rot / repair_door / relayout），
  以及每个 order 的 livability / spaciousness 耗时
- `HK_STAGE_PROGRESS=1` —— 每 200 个候选打印一次 rot / x / y

**就是它把死循环抓出来的**（x / y 一直不变）。成本极低，建议保留。

诊断脚本：`scripts/diag_artery.py`（最窄那一刀在哪）、`diag_clearance.py`（瓶颈净宽）、
`diag_cost.py` / `diag_cost2.py`（量 `_cheap_bad` 成本）、`diag_relayout.py`、
`diag_solve1.py`（单房间求解 + 分阶段计时）。

---

## 7. 下一步，按顺序

1. **定第 12 条的口径**（见 §5）。用 `diag_clearance.py` 拿数再决定。
2. **厨房 3.8 m²**：1700 台面 + 灶台 + 雪柜 + 洗衣机，已经用穷举证明「最多放 3 件」。
   这是产品决定，不是求解器问题 —— 要么减一件电器，要么接受缩短的台面。
3. **第 11 条（沙发面壁）**：先查它是不是和 2.2 同类的 bug ——
   朝向项到底有没有参与搜索，还是同样只做否决。
4. 以上都清了再重跑，然后才生成给你看的设计。

---

## 8. 工作纪律（保持）

- 改文件：**前台**、小步、改完立刻用 grep / `ast.parse` 验证
- 长跑：**同时最多一个**后台任务，`python3 -u`，日志写绝对路径，另开一条短命令去读
- 开新的后台任务之前**先 kill 旧的**
- 候选扫描最内层 `while` 里**永远不要写 `continue`**
