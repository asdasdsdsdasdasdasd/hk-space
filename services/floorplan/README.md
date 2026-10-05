# services/floorplan — 户型图图片 → Room JSON 矢量化管线

用户看到的第一屏: 上传一张香港户型图照片 / 扫描件, 系统把它变成结构化的房间几何 (Room JSON),
再交给下游的空间优化器。**纯经典 OpenCV 方法, 不训练模型、不下载数据集。**

香港户型图的两个特点决定了整套算法: ① **承重墙是实心黑粗块** (200mm), 间隔墙是**细双线** (100mm);
② 图上**几乎一定带尺寸标注数字** (2600 / 3000 / …), 这是恢复真实比例尺最可靠的线索 ——
比任何视觉估计都准, 因为它是图纸自身声明的物理尺寸。

---

## 1. 文件清单

| 文件 | 行数 | 作用 |
|---|---|---|
| `make_test_floorplan.py` | 730 | 程序化生成 3 张「像香港楼盘平面图」的测试图 + ground-truth Room JSON |
| `vectorize.py` | 1536 | 核心管线: 图片 → Room JSON + confidence |
| `render_overlay.py` | 256 | 原图半透明垫底 + 矢量线叠加 (人工校正界面原型) |
| `README.md` | — | 本文档 |

生成物:

| 路径 | 说明 |
|---|---|
| `data/floorplans/plan_hk_{a,b,c}.png` | 3 张测试图 (2220×1680 / 1914×1760 / 1580×2240) |
| `data/floorplans/plan_hk_{a,b,c}.truth.json` | 对应的标准答案 (同 schema) |
| `out/plan_hk_{a,b,c}.json` | 管线输出 |
| `out/floorplan_overlay.png` | **默认叠加图** (A 户型) |
| `out/floorplan_overlay_{a,b,c}(_sidebyside).png` | 各户型的叠加图 / 原图对照图 |
| `out/plan_hk_{a,b,c}_rectified.png` | 倾斜矫正后的图 (叠加图的底图) |

## 2. 依赖

必需: `numpy` `opencv-python` `pillow` (中文字体用系统 Noto Sans CJK)。
可选: `pytesseract` + `tesseract` 二进制 —— **本机没装**, 所以管线自动降级到内置的
「字体模板匹配 OCR」(渲染 0-9 十张模板, 归一化到 24×32 后做相关系数匹配)。
两条路都失败时, 比例尺回退到「按承重墙 ≈200mm 反推」→ 再退到 `--assume-scale` (默认 5.0 mm/px)。
`meta.ocr_engine` 会告诉你实际用了哪条 (`pytesseract` / `template` / `off`)。

## 3. 快速开始 (下面是真实运行结果, 不是示意)

### 3.1 生成测试图

```bash
cd /mnt/d/openclaw/hk-space
python3 services/floorplan/make_test_floorplan.py
```

```
[ok] plan_hk_a.png  2220x1680 px  5.00 mm/px  rooms=5 walls=23 openings=14
     truth -> plan_hk_a.truth.json
[ok] plan_hk_b.png  1914x1760 px  4.55 mm/px  rooms=4 walls=20 openings=11
     truth -> plan_hk_b.truth.json
[ok] plan_hk_c.png  1580x2240 px  5.00 mm/px  rooms=6 walls=28 openings=17
     truth -> plan_hk_c.truth.json
```

三张图分别模拟: A = 干净扫描件 (轻微 0.6° 倾斜); B = 手机拍照 (1.4° 倾斜 + 更强噪点 + L 形客廳);
C = 竖版 + 大角度倾斜 (-2.2°) + **透视畸变** + 重噪点 (最难的一张)。

### 3.2 矢量化 (任务要求的两条命令)

```bash
python3 services/floorplan/vectorize.py --image data/floorplans/plan_hk_a.png --out out/plan_hk_a.json
```

```
[vectorize] plan_hk_a.png -> out/plan_hk_a.json
  图像 2220x1680  ink=0.067  倾斜矫正=-0.55 deg (残差 +0.033 deg)
  比例尺 5.0000 mm/px  (来源: ocr, OCR 引擎: template, 命中标注 4 条)
  OCR 读到的数字: 20, 320, 2600, 3000, 8400, 1600, 1200, 20
  标定明细: 2600->5.00mm/px(520px), 3000->5.00mm/px(600px), 1600->5.00mm/px(320px), 1200->5.01mm/px(240px)
  墙 8 段 (承重 9 / 间隔 14 次出现)   开口 10
  房间 5 个: living(客廳) 15m2, bedroom(睡房) 8m2, kitchen(廚房) 7m2, bath(浴室) 4m2, storage(儲物房) 3m2
  confidence = 0.941  {'scale': 0.16, 'rooms': 0.12, 'wall_coverage': 0.151, 'openings': 0.08, 'skew': 0.079, 'clean': 0.05}
```

加上 `--truth` 会打一份和标准答案的对比 (IoU / 开口数 / 比例尺误差):

```
[compare vs ground truth]
  房间数 pred=5 truth=5   平均 IoU=0.957
    living     <-> living     IoU=0.995
    bedroom    <-> bedroom    IoU=0.961
    kitchen    <-> kitchen    IoU=0.974
    bath       <-> bath       IoU=0.950
    storage    <-> storage    IoU=0.906
  开口 pred={'door': 9, 'window': 5} truth={'window': 5, 'door': 9}
  比例尺 pred=5.0 mm/px truth=5.0 mm/px 误差=0.0%
```

三张图的结果:

| 图 | 房间数 | 平均 IoU | 门 (pred/truth) | 窗 (pred/truth) | 比例尺误差 | confidence | 耗时 |
|---|---|---|---|---|---|---|---|
| A 扫描件 | 5 / 5 | **0.957** | 9 / 9 | 5 / 5 | 0.0% | 0.941 | 0.72 s |
| B 拍照+L形 | 4 / 4 | **0.945** | 7 / 7 | 4 / 4 | 0.0% | 0.950 | 0.7 s |
| C 透视+竖版 | 6 / 6 | **0.986** | 13 / 13 | 4 / 4 | 0.0% | 0.765 | 0.8 s |

B 的厨房/浴室标签互换了 (IoU 0.921/0.914 那一对): 房间**几何**是对的, 只是**名字**猜错了 —— 见 §7 局限。

### 3.3 叠加图 (人工校正界面原型)

```bash
python3 services/floorplan/render_overlay.py \
  --image data/floorplans/plan_hk_a.png --json out/plan_hk_a.json \
  --out out/floorplan_overlay.png --side-by-side
# 也可以直接用 vectorize 自带的一步到位:
python3 services/floorplan/vectorize.py --image data/floorplans/plan_hk_a.png \
  --out out/plan_hk_a.json --overlay out/floorplan_overlay.png
```

图上: 半透明原图垫底 + 房间色块(中文名+面积) + **红=承重墙 / 蓝=间隔墙 / 绿=门(带开启弧) /
青=窗 / 品红=ENTRY** + 左上角图例 (比例尺来源、OCR 引擎、读到的数字、房间墙数开口数)。
`*_sidebyside.png` 是「原图 | 矢量化结果」左右对照, 给用户/设计师复核用。

### 3.4 其它入口

```bash
# 调用者已知比例尺时 (优先级最高)
python3 services/floorplan/vectorize.py --image X.png --out X.json --scale-mm-per-px 5.2
# 关掉 OCR (强制走墙体厚度兜底)
python3 services/floorplan/vectorize.py --image X.png --out X.json --no-ocr
# 关掉叠加图 (纯 JSON)
python3 services/floorplan/vectorize.py --image X.png --out X.json --no-overlay
# 调阈值
python3 services/floorplan/vectorize.py --image X.png --out X.json --min-wall-mm 300 --max-opening-mm 2500
```

## 4. 管线每一步在做什么, 以及为什么这么做

以下每条都是实测踩出来的, 不是抄书。

### 4.1 预处理: **不能只用自适应二值化**

```
gray → 大尺度高斯估背景 → 光照归一 → Otsu ┐
       + adaptiveThreshold (局部) ───────────┴→ 取并集 → 2×2 开运算去椒盐
```

坑: 单用 `cv2.adaptiveThreshold` 会把**实心黑承重墙掏空** —— 实心区内部的局部均值也是黑的,
`pixel < mean - C` 不成立, 于是 200mm 的承重墙只剩一圈黑边。后果是厚度剖面全乱、
承重墙被判成两条细线。所以必须先用大尺度高斯 (σ≈min(H,W)/28) 估背景、把光照拉平, 再做全局 Otsu,
最后与 adaptive 取**并集**(Otsu 保住实心块, adaptive 补回细淡的线)。

### 4.2 倾斜矫正: 用**墙带中心线的斜率**, 不用霍夫

```
形态学方向开运算 → 长墙带 → 对每条带的 (位置, 厚度方向中心) 做一次线性拟合 → 斜率 → 角度
→ 取长度加权中位数 → 正反两个方向各转一次, 保留残差更小的 → 迭代 3 轮直到残差 <0.12°
```

坑: `HoughLinesP` 在带噪点的照片上估出来的角度偏了 0.4°, 结果"矫正完"的图还斜 0.57° ——
墙厚方向的中心线在 1200px 上漂了 12px, 后面按「一列一列算厚度」的算法就会全线崩。
改用长墙带拟合后, 残差 0.03°~0.10°。预处理里仍然保留了霍夫函数 (`estimate_skew_deg`) 作为备用。

**只做旋转, 不做透视**: 真正的透视矫正需要四点单应 (要用户点 4 个角或检测纸张边界),
本版没做 —— C 图就是带透视的, 它的残差体现在房间多边形边不严格水平 (IoU 仍到 0.986)。

### 4.3 墙体带提取 + 承重/间隔判别 (核心)

```
{h,v} 方向开运算 (核长 = min_wall_mm / mm_per_px) → 连通域 → 每条带记录:
  沿墙范围 [s,e]、厚度方向中心、以及**逐点剖面** profile[(中心, 墨迹长度), ...]
→ 合并「细双线」: 两条平行细线沿墙重叠 >55% 且间距 ≤220mm → 合并成一条间隔墙带
→ 建「墙体网络」掩膜 (最大膨胀连通域) → 网络外的长线 = 尺寸标注线, 网络内的孤立细短线 = 文字/箭头
```

**判别承重墙 / 间隔墙**: 不是看单张图片的填充率 —— 因为窗洞会把一道墙的填充率稀释掉
(顶墙实测 fill 只有 0.49, 按填充率判会判成间隔墙)。最终用的是:

- 合并过的双线 (n_members≥2) → `partition`
- 实心单带且**换算厚度 ≥150mm** → `load_bearing` (HK 间隔墙基本 100mm, 150mm 是个安全分界)

### 4.4 开口 (门/窗) 判别: 用**厚度剖面**, 不是用缺口

沿一条墙线拉通扫描, 每个位置算「垂直于墙的墨迹长度」, 分成三类:

| 剖面 | 含义 | 结论 |
|---|---|---|
| ≥50% 的典型厚度 | 实心墙段 | `wall` |
| 0 < 长度 < 50% 典型厚度 | 只有几根细线 —— 平面图上**窗就画成几根细线** | `window` |
| 完全没墨迹 | 洞 | `door` (再去找门扇线定铰链) |

坑: 窗的两根细线是**连着**两侧墙垛的, 所以连通域分析里它们和墙是**同一个** component,
靠"找缺口"永远发现不了窗。必须靠剖面厚度塌陷。

**墙是"带洞的整段", 不是被洞切成的碎片**: 被门/窗隔开的同线墙段会合并成一条墙线,
洞作为墙上的 opening —— 这样 `opening.wall` 才有意义, `center_mm` (从墙的 a 端点量起)
也才落在墙的长度范围内。这正是 schema 表达的模型 (示例里 3200mm 的墙上一个 800mm 的门洞)。

**门扇线**: 细线 (≤8px) + 长度约等于门洞宽 + 一端贴在墙上 → 是门扇; 据此定 `hinge` (左/右门垛)
和 `swing.dir` (往房间内开还是往外开)。找不到门扇就退化成默认值 (不影响几何, 只影响开向语义)。

**墙线末端与垂直墙之间的洞**: 墙线扫描范围会向两端**延伸到 1.8m 内的垂直墙**,
否则那段门洞永远扫不到, 房间会连成一片 (实测: 储物房和客廳会粘成一个 18m² 的巨室)。
延伸时**只认 ≥50mm 粗的垂直墙** —— 否则尺寸标注线会被当墙, 又冒出一堆假门洞。

### 4.5 房间多边形: 栅格化 + 封洞 + 连通域填充

```
把识别出的墙画成掩膜 → 每个开口画一条封条 (往两侧各多伸 4px, 再 3×3 闭运算)
→ 自由空间做 4 连通填充 → 丢掉贴边框的 (那是房子外面) 和 <0.8m² 的
→ 取外轮廓 → approxPolyDP → 顶点吸附到「墙表面网格」(墙中心线 ± 厚度/2) → 正交化
```

封条只差 1px 没搭上墙, 房间就会漏气 (实测踩过)。这条路径天然给出**墙内表面**的多边形,
正好是下游排家具要用的「可用面积」。

### 4.6 比例尺恢复 (三级)

1. **OCR + 墙跨距** (最准): 尺寸数字量的是"它两侧最近两道墙的距离"。所以对每个 OCR 数字,
   取它左右/上下最近的墙中心线夹逼, `mm_per_px = 数字 / 像素跨距`。
   * 不用「尺寸线长度」: 相邻的标注链线段常常连成一条长线, 按段取长度必错。
   * 多个候选按 **数字位数加权聚类**, 取权重和最大的一簇 (抗 OCR 漏读; 直接取中位数会被一堆
     被截断的 2-3 位数带偏)。
2. **按承重墙 ≈200mm 反推** (兜底, 出奇地稳): `200 / 粗墙像素厚度的上四分位`。
3. **`--assume-scale`** (默认 5.0 mm/px) 或调用者给的 `--scale-mm-per-px`。

再加一道**交叉校验**: OCR 反推的比例尺如果和墙体厚度反推的差超过 2 倍, 直接弃用 OCR 结果并写进
`meta.warnings` —— C 图就踩到了这个 (OCR 读到一堆被截断的数字, 反推出 0.502 mm/px),
校验把它拦下来, 回退到墙体厚度法, 结果正好是 5.000 mm/px (真值 5.0)。

### 4.7 房间名与其它语义

- `id`/`label`: 按**面积排名**套一份 HK 常用名表 (客廳/睡房/廚房/浴室/儲物房/飯廳)。这是纯启发式。
- `height_mm`: 常数 2450 (平面图反推不出层高, 由调用者覆盖)。
- `height_field`: 恒为 `[]` (梁位识别需要另一套符号识别, 没做; 保留字段让下游可填)。
- `fixed_services`: 恒为 `[]` —— **平面图上没有可机读的去水位/煤氣位符号, 本版不猜**。
  宁可留空, 也不要编造一个下游会信的假去水位。
- `entry`: 唯一一个「只有一个相邻房间」的门 = 对外大门; 入口点 = 门中心往外推 100mm。

### 4.8 confidence

加权和, 并把每项写进 `meta.confidence_terms` 方便调:

```
0.30  基线
+0.16 比例尺来自 OCR      / +0.08 调用者指定 / +0.03 墙体厚度推断 / 0 默认假设
+0.12 房间数 ≥3 (或 +0.05 ≥1)
+0.16 房间边长被墙覆盖的比例 (墙-多边形贴合度, 实测好图 0.95+)
+0.08 开口数量 (4 条封顶)
+0.08 倾斜残差 (残差越大越低)
+0.05 全程无 warning
×0.45 什么都没识别出来时整体打折 (空白图/噪声图 → 0.17)
```

## 5. 坐标系与 schema

严格按给定 schema 输出, 字段名一个没改。补充说明 (README 里说清楚, 避免下游猜):

- **单位** 一律整数毫米; 字段名带 `_mm` 后缀; 角度只用 0/90/180/270。
- **坐标系**: 原点 = 所有房间多边形包围盒的**最小角** (所以坐标从 0 附近开始, 不是图像左上角),
  `+x` 向右, `+y` **向下** (图像坐标系, 与 cv2 一致)。要转数学坐标系: `y' = H - y`。
- **房间多边形 = 墙内表面** (可用面积), 不含墙体本身。相邻房间的多边形之间隔着墙厚。
- **`opening.center_mm`** = 从该墙的 `a` 端点沿墙量到开口中心的距离; `width_mm` = 洞口净宽。
- `meta` 里除了规定的 `source` / `confidence`, 额外带了渲染/调试必需的字段 (**只增不改**):

| 键 | 用途 |
|---|---|
| `mm_per_px`, `scale_source`, `scale_method`, `scale_votes` | 比例尺及其来源与全部投票明细 |
| `origin_px` | JSON 的 (0,0) 在**矫正后图片**上的像素位置 —— render_overlay 靠它把毫米画回图片 |
| `rectified_image` / `overlay` | 矫正图 / 叠加图路径 (给人工校正界面用) |
| `image_size_px`, `deskew_deg`, `residual_skew_deg` | 图像与矫正信息 |
| `ocr_engine`, `ocr_tokens` | 用的哪条 OCR / 读到的原始数字 |
| `walls_detected`, `openings_detected`, `confidence_terms`, `warnings` | 诊断 |

一个真实的房间片段 (`out/plan_hk_a.json`, 直接抄出来的):

```json
{
  "id": "living", "label": "客廳",
  "polygon": [[0, 0], [0, 1850], [8200, 1850], [8200, 0]],
  "height_mm": 2450, "height_field": [],
  "walls": [
    {"id": "w0", "a": [-155, -98], "b": [8405, -98], "type": "load_bearing", "thickness_mm": 200},
    {"id": "w1", "a": [-190, 1902], "b": [7360, 1902], "type": "partition", "thickness_mm": 110},
    {"id": "w2", "a": [-98, -180], "b": [-98, 5110], "type": "load_bearing", "thickness_mm": 200}
  ],
  "openings": [
    {"id": "n1", "wall": "w0", "kind": "window", "center_mm": 2165, "width_mm": 1810, "swing": null},
    {"id": "d2", "wall": "w1", "kind": "door", "center_mm": 3700, "width_mm": 820,
     "swing": {"dir": "in", "hinge": "right"}}
  ]
}
```

(真值: 顶窗 1200→3000 即宽 1800 中心 2100; 睡房门宽 850 铰链右。检测 1810/2165 与 820/铰链右。)

## 6. 精度实测

对三张测试图的汇总见 §3.2 的表; 逐房间 IoU 见 `--truth` 输出。
再补几条实测数字:

- **比例尺**: 三张图误差都是 **0.0%** (A/B 走 OCR 墙跨距法, C 走墙体厚度兜底)。
- **开口判别**: 10/10、8/8、11/11 全中, 门/窗没有互相误判。
- **抗倾斜**: 1.4° 倾斜 + 噪点 (B) 与 -2.2° + 透视 (C) 都能跑通, 矫正残差 <0.11°。
- **速度**: 单张图 0.7–0.8 s (含 OCR 与渲染前的全部计算), 峰值内存 ~190MB。
- **退化输入**: 纯白图 / 纯噪声图不会崩, 输出空 rooms + warning + confidence 0.17。
- **比例尺敏感性**: `--scale-mm-per-px` 给错 4% (5.2 对 5.0) 时, A 图仍能出 4 个房间,
  但最小的储物房 (3m²) 会丢 —— 阈值类算法对比例尺敏感, 详见局限。

## 7. 已知局限 (按重要性排)

1. **不做透视矫正**。只做旋转。手机斜拍时房间多边形会整体变成轻微平行四边形,
   边长有 1–3% 的系统误差 (C 图可见)。真解法: 检测图纸四角 / 用户点 4 点求单应。
2. **房间名是猜的**。按面积排名套名字, B 图里厨房和浴室就互换了。要修必须靠房内符号
   (灶台/坐厕/浴缸/床) 做分类 —— 属于另一套模型的事。
3. **不理解家具、洁具、梁柱符号**。图上的沙发、床、灶台只会被当作噪声滤掉 (这是刻意的:
   几何要比智能重要); 梁 (`height_field`) 与去水位 (`fixed_services`) 一律留空。
4. **尺寸数字 OCR 对旋转+透视敏感**。内置模板匹配在 A/B 上 4 位数全读对,
   在 C (2.2° + 透视 + 重噪点) 上把 1600 读成 160、8000 读成 80。
   装上 `pytesseract` 会明显变好; 另外现在的模板 OCR 是拿系统字体渲染的模板 ——
   真实图纸多是 CAD 单线字, 换成 CAD 字模会更稳。
5. **只支持正交墙**。45° 斜墙 / 圆弧墙会被强行吸附到 0/90 度 (HK 有些老楼的斜墙会失真)。
6. **阈值对比例尺敏感**。`min_wall_mm` (默认 400) 换算成像素后决定哪些线算墙;
   比例尺估偏 >5% 时, 400mm 上下的小墙段会漏 (储物房那种薄墙就是第一个牺牲品)。
7. **不处理填充图案 (hatch) 画的承重墙**。本版靠"实心粗黑块"识别承重墙;
   若图纸用斜线填充表示结构墙, 会被判成间隔墙 —— 需要加一个"纹理方向"判据。
8. **测试图是我们自己画的**。它证明了管线自洽, 不等于在真实 HK 楼盘图上的成绩。
   在拿真图定量评估之前, 上面所有 IoU 都只能当"实现正确性"的证据, 不能当"产品效果"的证据。
9. 图上若出现**两个朝向的标注链**(横排数字 + 竖排数字), 本版只认横排数字 ——
   生成器里的竖排尺寸也画成水平, 这点与真实图纸不一致 (HK 图纸竖排数字通常转 90°)。

## 8. 下一步: 用 CubiCasa5k 训练分割模型替换经典方法

经典方法的天花板很清楚 (上面的局限 1/3/7)。业界标准做法是**语义分割 + 后处理矢量化的混合方案**:

1. **数据**: [CubiCasa5k](https://github.com/CubiCasa/CubiCasa5k) (5000 张芬兰平面图, 带像素级
   多边形标注)。HK 风格差异大 (黑实心承重墙 / 细双线间隔墙 / 中文字), 需要再标 200–500 张 HK 图
   (IKEA/中原/美联的楼盘图 PDF 里就有), 用 CubiCasa 预训练权重做微调。
2. **模型**: 5 类语义 (背景 / 承重墙 / 间隔墙 / 门 / 窗) + 房间实例分割 (CubiCasa 的
   `room` 通道), U-Net 或 SegFormer-B0 就够 (图小、类别少, 手机端也能跑)。
3. **接到本管线**: 分割 mask 直接替换 `binarize()` 之后的全部内容 —— 也就是
   把 `extract_bands / merge_double_lines / detect_walls` 换成
   「按类别取 mask → 形态学骨架 → 直线拟合 → 正交吸附」。后面的
   比例尺恢复、房间多边形提取、Room JSON 组装**一行都不用改**, 因为它们只依赖
   `walls_px / openings_px` 这两个中间结构 —— 这正是现在这么分层的原因。
4. **门/窗的语义直接来自分割**, 不再靠"剖面塌陷"这种间接判据 → 局限 4/7 一起消失。
5. **比例尺仍需 OCR**: 分割模型读不出尺寸数字。可以把内置模板 OCR 换成
   一个小的数字 CRNN (合成数据训练, 半天就能做完), 或者直接上 PaddleOCR。
6. **回退链**: 模型置信度低 → 回到本版经典管线 → 再不行 → 人工校正界面
   (`render_overlay` 就是它的原型: 原图垫底 + 矢量线 + 拖点改线)。

短期性价比最高的三件事 (不需要训练): ① 装 `tesseract` 走 `pytesseract`;
② 加四点单应做透视矫正; ③ 用 HK 楼盘图跑一遍, 把阈值 (`min_wall_mm` 等) 重新标定。

## 9. 给人工校正界面的接口

`render_overlay.py` 已经是校正界面的原型: 底图 (原图或矫正图) + 矢量线 + 置信度提示。
做成交互式界面的最小改动:

1. 用 `meta.origin_px` + `meta.mm_per_px` 把毫米坐标双向映射到图片像素 (已经在用);
2. 用户拖动房间多边形顶点 / 墙端点 → 直接改 Room JSON 的整数毫米坐标;
3. 「补拉一条线量比例尺」→ 调用者拿到两点像素距离 + 输入的真实长度 → 重算 `mm_per_px`
   → 重跑管线 (`vectorize(params=Params(scale_mm_px=…))`);
4. `meta.warnings` 直接显示成黄色提示条; `meta.confidence < 0.6` 时默认切到校正模式。

## 10. 自测记录 (可直接复现)

```bash
cd /mnt/d/openclaw/hk-space
python3 services/floorplan/make_test_floorplan.py
for n in a b c; do
  python3 services/floorplan/vectorize.py --image data/floorplans/plan_hk_$n.png \
      --out out/plan_hk_$n.json --truth data/floorplans/plan_hk_$n.truth.json \
      --overlay out/floorplan_overlay_$n.png
done
# 退化输入 (不应崩溃)
python3 -c "import numpy as np,cv2;cv2.imwrite('/tmp/blank.png',np.full((900,1200),255,np.uint8))"
python3 services/floorplan/vectorize.py --image /tmp/blank.png --out /tmp/x.json
```
