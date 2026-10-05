#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_catalog.py -- 生成 HK 小户型 Demo 的家具 SKU 种子数据集 (seed_skus.json)

设计原则
--------
1. 单位冻结: 所有长度 = 整数毫米 (int mm)，字段名带 _mm 语义 (dims.w / dims.d / dims.h)。
   角度只用 {0, 90, 180, 270} 整数度。
2. 可重复运行: 同样参数 -> 逐字节相同的输出 (随机/排序均由 --seed 决定)。
3. 可调数量: --total N。默认 150。
   - N == 150 : 每个模板恰好出 1 个 SKU (主尺寸 + 主饰面)。
   - N > 150  : 用同一模板的备用饰面 / 备用尺寸做变体，按类别配额轮转补齐。
   - N < 150  : 按最大余数法等比缩减排掉部分模板 (会丢失部分高/低对比对)。
4. 高/低对比对: 同一功能同时收录"显大款"与"显小款" (浅色 vs 深色、开放格 vs 实木门、
   1.8m 柜 vs 2.4m 到顶柜、带腿通透梳化 vs 落地厚重梳化)。这是 Demo 演"显大分跳动"的燃料。

数据诚实性
----------
- 所有 price_hkd 均为 Demo 占位值，price_verified 恒为 false。
- name_zh / name_en 以公开产品系列为蓝本；dims 为"该品类典型值"，不是某一具体型号的官方规格。
- url 全部留空 (没有核实过的链接就不编)。
- 香港 IKEA 实际不销售分体/窗口冷气机，aircon 类目的 retailer 标为 "HK retail (placeholder)"。

用法
----
    python3 build_catalog.py                     # 生成 150 条 -> seed_skus.json
    python3 build_catalog.py --total 400         # 生成 400 条
    python3 build_catalog.py --out other.json --stats
    python3 build_catalog.py --check             # 只校验已存在的 seed_skus.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TICK3 = chr(96) * 3   # 三个反引号，避免源码里出现 markdown 围栏

# ---------------------------------------------------------------------------
# 允许的类目 (冻结枚举)
# ---------------------------------------------------------------------------
CATEGORIES = [
    "bed", "bunk_bed", "loft_bed", "storage_bed", "wardrobe", "cabinet", "shelf",
    "tv_bench", "shoe_cabinet", "desk", "dining_table", "side_table", "chair",
    "sofa", "coffee_table", "storage_box", "wall_shelf", "mirror", "fridge",
    "washing_machine", "microwave", "aircon", "drying_rack", "screen",
]

# ---------------------------------------------------------------------------
# 饰面 / 配色表  key -> (lightness_L, reflectivity, color_family)
#   lightness_L: 0-100，浅色高、深色低
#   reflectivity: 镜 0.95 / 玻璃 0.7 / 亮面烤漆 0.5 / 哑光木 0.1 / 布艺 0.05
#   color_family: warm / cool / neutral
# ---------------------------------------------------------------------------
FINISHES = {
    "mirror":      (92, 0.95, "cool"),
    "chrome":      (90, 0.75, "cool"),
    "glass":       (88, 0.70, "cool"),
    "clear":       (88, 0.70, "neutral"),
    "white_oak":   (85, 0.10, "warm"),
    "off_white":   (80, 0.12, "neutral"),
    "steel":       (78, 0.55, "cool"),
    "birch":       (76, 0.12, "warm"),
    "light_wood":  (72, 0.10, "warm"),
    "bamboo":      (70, 0.12, "warm"),
    "beige":       (70, 0.05, "warm"),
    "rattan":      (66, 0.08, "warm"),
    "sage":        (62, 0.10, "cool"),
    "blue":        (58, 0.15, "cool"),
    "grey":        (55, 0.10, "cool"),
    "dark_grey":   (42, 0.08, "cool"),
    "walnut":      (30, 0.12, "warm"),
    "dark_green":  (28, 0.10, "cool"),
    "black_brown": (22, 0.15, "neutral"),
    "black":       (18, 0.45, "neutral"),
}

# 各饰面的备用饰面 (扩量时用)，尽量跨 lightness 制造对比
ALT_FINISH = {
    "white_oak":   ["black_brown"],
    "off_white":   ["walnut", "grey"],
    "light_wood":  ["off_white", "black_brown"],
    "birch":       ["off_white"],
    "bamboo":      ["light_wood"],
    "beige":       ["dark_grey"],
    "rattan":      ["light_wood"],
    "sage":        ["off_white"],
    "grey":        ["off_white"],
    "dark_grey":   ["beige"],
    "walnut":      ["off_white", "light_wood"],
    "black_brown": ["off_white"],
    "black":       ["off_white"],
    "mirror":      ["chrome"],
    "glass":       ["light_wood"],
    "clear":       ["dark_grey"],
    "chrome":      ["off_white"],
    "steel":       ["off_white"],
    "blue":        ["off_white"],
    "dark_green":  ["off_white"],
}

# ---------------------------------------------------------------------------
# 模板表 (150 条 == 默认运行下的 150 个 SKU)
#   c      category
#   zh/en  名称
#   w,d,h  主尺寸 (int mm)
#   f      饰面 key
#   op     opacity        omn  openness      refl  reflectivity 覆写 (None=用饰面)
#   L      lightness_L 覆写 (None=用饰面)      col   color_family 覆写
#   p      price_hkd      v    storage_volume_L   a   assembly
#   cf/cs/ct  clearance front / left=right / top
#   load   顶部可承重 kg (0 = 无可置物台面)
#   m      mount 元组     r    retailer (默认 IKEA HK)   code  固定货号
#   sz     备用尺寸 [(w,d,h), ...]                 alt  备用饰面 [key, ...]
# ---------------------------------------------------------------------------
TEMPLATES = [
    # ======================= 衣櫃 wardrobe (12) =======================
    dict(c="wardrobe", zh="PAX 到頂衣櫃", en="PAX Wardrobe, to ceiling",
         w=1000, d=580, h=2360, f="walnut", op=1.00, omn=0.00, p=4490, v=1000,
         cf=600, load=25, sz=[(1500, 580, 2360), (1000, 580, 2010)]),
    dict(c="wardrobe", zh="PAX 衣櫃", en="PAX Wardrobe",
         w=1000, d=580, h=2010, f="light_wood", op=1.00, omn=0.00, p=3490, v=800,
         cf=600, load=25, sz=[(1500, 580, 2010), (750, 580, 2010)]),
    dict(c="wardrobe", zh="PAX 開放式衣櫃框", en="PAX Open wardrobe frame",
         w=750, d=580, h=2010, f="off_white", op=0.10, omn=0.90, p=1490, v=620,
         cf=0, load=20, sz=[(1000, 580, 2010), (750, 580, 2360)]),
    dict(c="wardrobe", zh="KLEPPSTAD 衣櫃", en="KLEPPSTAD Wardrobe",
         w=1170, d=550, h=1760, f="off_white", op=1.00, omn=0.00, p=1490, v=700,
         cf=600, load=25, sz=[(1170, 550, 2030)], alt=["grey"]),
    dict(c="wardrobe", zh="BRIMNES 衣櫃", en="BRIMNES Wardrobe",
         w=780, d=500, h=1900, f="black_brown", op=1.00, omn=0.00, p=1290, v=480,
         cf=600, load=25, sz=[(780, 500, 1240)]),
    dict(c="wardrobe", zh="PLATSA 薄身衣櫃", en="PLATSA Wardrobe, shallow",
         w=1200, d=420, h=1810, f="light_wood", op=1.00, omn=0.00, p=2290, v=560,
         cf=600, load=20, sz=[(1200, 420, 2010)], alt=["off_white"]),
    dict(c="wardrobe", zh="BOAXEL 掛牆開放衣櫃", en="BOAXEL Wall-mounted open wardrobe",
         w=1000, d=400, h=2010, f="off_white", op=0.10, omn=0.95, p=1690, v=480,
         cf=0, load=15, m=("wall",), sz=[(1500, 400, 2010)]),
    dict(c="wardrobe", zh="JONAXEL 網籃衣櫃組合", en="JONAXEL Wardrobe combination, wire",
         w=750, d=510, h=1750, f="off_white", op=0.15, omn=0.90, p=890, v=360,
         cf=0, load=15, sz=[(750, 510, 1040)]),
    dict(c="wardrobe", zh="AURDAL 衣櫃組合", en="AURDAL Wardrobe combination",
         w=1200, d=420, h=2000, f="off_white", op=1.00, omn=0.00, p=2390, v=580,
         cf=600, load=20, sz=[(1200, 420, 2400)], alt=["light_wood"]),
    dict(c="wardrobe", zh="PAX 玻璃門衣櫃", en="PAX Wardrobe with glass doors",
         w=1000, d=580, h=2010, f="off_white", op=0.30, omn=0.10, refl=0.70,
         p=5290, v=760, cf=600, load=25, sz=[(1500, 580, 2010)]),
    dict(c="wardrobe", zh="VUKU 單門衣櫃", en="VUKU Wardrobe",
         w=750, d=500, h=1830, f="off_white", op=1.00, omn=0.00, p=790, v=340,
         cf=600, load=20, sz=[(750, 500, 915)]),
    dict(c="wardrobe", zh="HAUGA 衣櫃", en="HAUGA Wardrobe",
         w=1050, d=550, h=1990, f="grey", op=1.00, omn=0.00, p=1990, v=620,
         cf=600, load=25, sz=[(1050, 550, 1160)], alt=["off_white"]),

    # ======================= 櫃 cabinet (10) =======================
    dict(c="cabinet", zh="MALM 六屜櫃", en="MALM Chest of 6 drawers",
         w=1600, d=480, h=780, f="off_white", op=1.00, omn=0.00, p=2490, v=480,
         cf=550, load=30, sz=[(1600, 480, 1000)], alt=["black_brown"]),
    dict(c="cabinet", zh="MALM 三屜櫃", en="MALM Chest of 3 drawers",
         w=800, d=480, h=780, f="black_brown", op=1.00, omn=0.00, p=1290, v=220,
         cf=550, load=30, sz=[(800, 480, 1000)]),
    dict(c="cabinet", zh="KOPPANG 三屜櫃", en="KOPPANG 3-drawer chest",
         w=1080, d=460, h=840, f="off_white", op=1.00, omn=0.00, p=1190, v=300,
         cf=550, load=30, alt=["grey"]),
    dict(c="cabinet", zh="HEMNES 八屜櫃", en="HEMNES 8-drawer chest",
         w=1600, d=500, h=960, f="off_white", op=1.00, omn=0.00, p=2990, v=560,
         cf=550, load=30, sz=[(1080, 500, 960)]),
    dict(c="cabinet", zh="BRIMNES 四屜櫃", en="BRIMNES 4-drawer chest",
         w=780, d=460, h=1240, f="black_brown", op=1.00, omn=0.00, p=1490, v=400,
         cf=550, load=25),
    dict(c="cabinet", zh="KALLAX 方格櫃", en="KALLAX Shelving unit",
         w=1470, d=390, h=770, f="off_white", op=0.50, omn=0.80, p=1090, v=420,
         cf=0, load=30, sz=[(1470, 390, 1470), (770, 390, 770)]),
    dict(c="cabinet", zh="KALLAX 方格櫃", en="KALLAX Shelving unit",
         w=770, d=390, h=770, f="black_brown", op=0.50, omn=0.80, p=690, v=200,
         cf=0, load=30, sz=[(770, 390, 1470)]),
    dict(c="cabinet", zh="EKET 組合櫃", en="EKET Cabinet combination",
         w=1050, d=350, h=700, f="light_wood", op=0.40, omn=0.70, p=1290, v=300,
         cf=0, load=20, sz=[(1050, 350, 1050)], alt=["off_white"]),
    dict(c="cabinet", zh="FABRIKÖR 玻璃門櫃", en="FABRIKÖR Glass-door cabinet",
         w=800, d=420, h=1500, f="dark_green", op=0.30, omn=0.20, refl=0.70,
         p=2490, v=400, cf=500, load=25),
    dict(c="cabinet", zh="NORDKISA 竹屜櫃", en="NORDKISA Chest of drawers, bamboo",
         w=760, d=400, h=760, f="bamboo", op=1.00, omn=0.00, p=1990, v=180,
         cf=500, load=20),

    # ======================= 電視櫃 tv_bench (5) =======================
    dict(c="tv_bench", zh="BESTÅ 電視櫃", en="BESTÅ TV bench",
         w=1800, d=420, h=380, f="off_white", op=0.80, omn=0.40, p=1890, v=260,
         cf=500, load=50, sz=[(1200, 420, 380)], alt=["black_brown"]),
    dict(c="tv_bench", zh="LACK 電視櫃", en="LACK TV bench",
         w=1490, d=350, h=450, f="black_brown", op=1.00, omn=0.00, p=590, v=120,
         cf=500, load=40, sz=[(1490, 350, 450)]),
    dict(c="tv_bench", zh="FJÄLLBO 電視櫃", en="FJÄLLBO TV bench",
         w=1500, d=360, h=450, f="light_wood", op=0.50, omn=0.60, p=1790, v=180,
         cf=0, load=45),
    dict(c="tv_bench", zh="HAUGA 電視櫃", en="HAUGA TV bench",
         w=1380, d=410, h=570, f="grey", op=0.80, omn=0.30, p=1490, v=220,
         cf=500, load=50, alt=["off_white"]),
    dict(c="tv_bench", zh="VITTSJÖ 電視櫃", en="VITTSJÖ TV bench, glass",
         w=1000, d=360, h=540, f="glass", op=0.30, omn=0.45, refl=0.70,
         p=1090, v=140, cf=0, load=35),

    # ======================= 鞋櫃 shoe_cabinet (5) =======================
    dict(c="shoe_cabinet", zh="TRONES 鞋櫃", en="TRONES Shoe cabinet",
         w=520, d=390, h=390, f="off_white", op=1.00, omn=0.00, p=350, v=60,
         cf=500, load=15, m=("wall",), sz=[(520, 390, 390)]),
    dict(c="shoe_cabinet", zh="MACKAPÄR 鞋及外套櫃", en="MACKAPÄR Shoe and coat cabinet",
         w=800, d=350, h=1250, f="off_white", op=0.85, omn=0.15, p=990, v=180,
         cf=500, load=15, sz=[(400, 350, 1250)]),
    dict(c="shoe_cabinet", zh="HEMNES 鞋櫃", en="HEMNES Shoe cabinet",
         w=890, d=340, h=890, f="off_white", op=1.00, omn=0.00, p=1490, v=160,
         cf=500, load=20),
    dict(c="shoe_cabinet", zh="BISSA 鞋櫃", en="BISSA Shoe cabinet",
         w=490, d=280, h=940, f="black_brown", op=1.00, omn=0.00, p=490, v=80,
         cf=500, load=15, sz=[(490, 280, 490)], alt=["off_white"]),
    dict(c="shoe_cabinet", zh="EKET 開放鞋架", en="EKET Open shoe shelving",
         w=700, d=350, h=700, f="light_wood", op=0.20, omn=0.90, p=690, v=120,
         cf=0, load=15),

    # ======================= 牆架 wall_shelf (5) =======================
    dict(c="wall_shelf", zh="LACK 牆架", en="LACK Wall shelf",
         w=1100, d=260, h=50, f="off_white", op=0.10, omn=1.00, p=190, v=30,
         cf=0, load=15, m=("wall",), sz=[(1900, 260, 50)], alt=["black_brown"]),
    dict(c="wall_shelf", zh="BERGSHULT 牆架", en="BERGSHULT Wall shelf",
         w=1200, d=200, h=40, f="light_wood", op=0.10, omn=1.00, p=240, v=20,
         cf=0, load=12, m=("wall",), sz=[(1800, 200, 40)]),
    dict(c="wall_shelf", zh="FJÄLLBO 牆架", en="FJÄLLBO Wall shelf",
         w=900, d=250, h=40, f="light_wood", op=0.10, omn=1.00, p=290, v=25,
         cf=0, load=15, m=("wall",)),
    dict(c="wall_shelf", zh="EKET 掛牆層架", en="EKET Wall-mounted shelving",
         w=700, d=350, h=350, f="light_wood", op=0.30, omn=0.80, p=490, v=45,
         cf=0, load=15, m=("wall",), sz=[(700, 350, 700)]),
    dict(c="wall_shelf", zh="MOSLANDA 牆架", en="MOSLANDA Wall shelf",
         w=1000, d=250, h=50, f="walnut", op=0.10, omn=1.00, p=390, v=28,
         cf=0, load=20, m=("wall",), alt=["off_white"]),

    # ======================= 床 bed (6) =======================
    dict(c="bed", zh="MALM 高架床框", en="MALM High bed frame",
         w=1560, d=2090, h=1000, f="white_oak", L=78, col="neutral",
         op=0.35, omn=0.60, refl=0.15,
         p=2790, v=420, cf=0, load=120, code="40300605",
         sz=[(1560, 2090, 1000)], alt=["black_brown"]),
    dict(c="bed", zh="MALM 高架床框", en="MALM High bed frame",
         w=1560, d=2090, h=1000, f="black_brown", op=0.35, omn=0.60, p=2990, v=420,
         cf=0, load=120, sz=[(1560, 2090, 1000)]),
    dict(c="bed", zh="NEIDEN 松木床框", en="NEIDEN Bed frame, pine",
         w=1500, d=2000, h=400, f="light_wood", op=0.40, omn=0.70, p=1290, v=0,
         cf=0, load=120, sz=[(1200, 2000, 400), (900, 2000, 400)]),
    dict(c="bed", zh="SONGESAND 床框", en="SONGESAND Bed frame",
         w=1570, d=2090, h=470, f="off_white", op=0.60, omn=0.20, p=2690, v=0,
         cf=0, load=120, sz=[(1370, 2090, 470)]),
    dict(c="bed", zh="SLÄKT 床框", en="SLÄKT Bed frame",
         w=1490, d=2000, h=550, f="off_white", op=0.45, omn=0.50, p=1490, v=0,
         cf=0, load=120, sz=[(970, 2000, 550)]),
    dict(c="bed", zh="TARVA 松木床框", en="TARVA Bed frame, pine",
         w=1500, d=2000, h=420, f="light_wood", op=0.40, omn=0.70, p=1790, v=0,
         cf=0, load=120, sz=[(1200, 2000, 420)]),

    # ======================= 雙層床 bunk_bed (3) =======================
    dict(c="bunk_bed", zh="MYDAL 雙層床框", en="MYDAL Bunk bed frame",
         w=970, d=2000, h=1600, f="light_wood", op=0.40, omn=0.65, p=1990, v=0,
         cf=0, load=120, sz=[(970, 2000, 1600)]),
    dict(c="bunk_bed", zh="VITVAL 雙層床框", en="VITVAL Bunk bed frame",
         w=970, d=2000, h=1570, f="off_white", op=0.40, omn=0.60, p=2290, v=0,
         cf=0, load=120, alt=["grey"]),
    dict(c="bunk_bed", zh="TUFFING 雙層床框", en="TUFFING Bunk bed frame",
         w=1050, d=2000, h=1600, f="grey", op=0.55, omn=0.40, p=2490, v=0,
         cf=0, load=120),

    # ======================= 高架床 loft_bed (3) =======================
    dict(c="loft_bed", zh="SMÅSTAD 高架床", en="SMÅSTAD Loft bed",
         w=2090, d=1040, h=1800, f="off_white", op=0.50, omn=0.55, p=3990, v=0,
         cf=0, load=120, sz=[(2090, 1040, 1800)]),
    dict(c="loft_bed", zh="STORÅ 高架床", en="STORÅ Loft bed",
         w=2090, d=1040, h=1830, f="light_wood", op=0.40, omn=0.65, p=3290, v=0,
         cf=0, load=120),
    dict(c="loft_bed", zh="VITVAL 高架床", en="VITVAL Loft bed",
         w=2000, d=1040, h=1700, f="off_white", op=0.35, omn=0.70, p=2690, v=0,
         cf=0, load=120, alt=["grey"]),

    # ======================= 儲物床 storage_bed (3) =======================
    dict(c="storage_bed", zh="BRIMNES 床連儲物", en="BRIMNES Bed frame with storage",
         w=1560, d=2090, h=470, f="off_white", op=1.00, omn=0.05, p=3490, v=400,
         cf=0, load=120, sz=[(1560, 2090, 470)]),
    dict(c="storage_bed", zh="NORDLI 床連儲物", en="NORDLI Bed frame with storage",
         w=1560, d=2090, h=500, f="grey", op=1.00, omn=0.05, p=4290, v=460,
         cf=0, load=120, alt=["off_white"]),
    dict(c="storage_bed", zh="MALM 床連儲物框", en="MALM Bed frame with storage",
         w=1560, d=2090, h=1000, f="white_oak", op=0.70, omn=0.20, p=3790, v=420,
         cf=0, load=120, sz=[(1560, 2090, 1000)]),

    # ======================= 書桌 desk (6) =======================
    dict(c="desk", zh="LAGKAPTEN 書桌", en="LAGKAPTEN Desk",
         w=1200, d=600, h=730, f="off_white", op=0.60, omn=0.50, p=490, v=0,
         cf=0, load=40, sz=[(1400, 600, 730)], alt=["light_wood"]),
    dict(c="desk", zh="MICKE 書桌", en="MICKE Desk",
         w=1050, d=500, h=750, f="off_white", op=0.90, omn=0.20, p=890, v=60,
         cf=500, load=40, sz=[(1050, 500, 750)]),
    dict(c="desk", zh="ALEX 書桌", en="ALEX Desk",
         w=1320, d=580, h=760, f="off_white", op=0.90, omn=0.30, p=1590, v=120,
         cf=550, load=40, alt=["grey"]),
    dict(c="desk", zh="BEKANT 書桌", en="BEKANT Desk",
         w=1200, d=600, h=720, f="light_wood", op=1.00, omn=0.00, p=1990, v=0,
         cf=0, load=50, sz=[(1600, 800, 720)]),
    dict(c="desk", zh="TROTTEN 書桌", en="TROTTEN Desk",
         w=1200, d=700, h=750, f="grey", op=0.95, omn=0.15, p=1290, v=80,
         cf=500, load=50, alt=["off_white"]),
    dict(c="desk", zh="HAUGA 書桌", en="HAUGA Desk",
         w=1380, d=500, h=750, f="grey", op=0.90, omn=0.30, p=1190, v=100,
         cf=500, load=40),

    # ======================= 餐桌 dining_table (4) =======================
    dict(c="dining_table", zh="LISABO 餐桌", en="LISABO Table",
         w=1400, d=780, h=740, f="light_wood", op=0.60, omn=0.50, p=1990, v=0,
         cf=0, load=50, sz=[(1800, 900, 740)]),
    dict(c="dining_table", zh="EKEDALEN 伸縮餐桌", en="EKEDALEN Extendable table",
         w=1200, d=700, h=750, f="off_white", op=1.00, omn=0.00, p=2290, v=0,
         cf=0, load=50, sz=[(1800, 900, 750)], alt=["walnut"]),
    dict(c="dining_table", zh="NORDEN 摺疊餐桌", en="NORDEN Gateleg table",
         w=900, d=900, h=730, f="birch", op=0.80, omn=0.20, p=1790, v=60,
         cf=0, load=45, sz=[(260, 900, 730)]),
    dict(c="dining_table", zh="TARNO 餐桌", en="TARNO Table",
         w=1100, d=700, h=740, f="walnut", op=1.00, omn=0.00, p=1490, v=0,
         cf=0, load=50, alt=["light_wood"]),

    # ======================= 邊桌 side_table (3) =======================
    dict(c="side_table", zh="LACK 邊桌", en="LACK Side table",
         w=550, d=550, h=450, f="off_white", op=1.00, omn=0.00, p=190, v=0,
         cf=0, load=25, sz=[(350, 350, 450)], alt=["black_brown"]),
    dict(c="side_table", zh="GLADOM 托盤邊桌", en="GLADOM Tray table",
         w=450, d=450, h=530, f="off_white", op=0.30, omn=0.70, p=249, v=0,
         cf=0, load=20, alt=["black"]),
    dict(c="side_table", zh="NORDKISA 竹邊桌", en="NORDKISA Bedside table, bamboo",
         w=600, d=450, h=450, f="bamboo", op=0.90, omn=0.20, p=790, v=40,
         cf=450, load=20),

    # ======================= 茶几 coffee_table (2) =======================
    dict(c="coffee_table", zh="LACK 茶几", en="LACK Coffee table",
         w=900, d=550, h=450, f="black_brown", op=1.00, omn=0.00, p=449, v=0,
         cf=0, load=30, sz=[(1180, 600, 450)], alt=["off_white"]),
    dict(c="coffee_table", zh="VITTSJÖ 玻璃茶几", en="VITTSJÖ Coffee table, glass",
         w=950, d=500, h=450, f="glass", op=0.20, omn=0.80, refl=0.70, p=690, v=0,
         cf=0, load=30),

    # ======================= 椅 chair (11) =======================
    dict(c="chair", zh="ADDE 椅", en="ADDE Chair",
         w=450, d=500, h=850, f="off_white", op=0.20, omn=0.80, p=99, v=0,
         cf=0, load=0, sz=[(450, 500, 850)]),
    dict(c="chair", zh="JANINGE 椅", en="JANINGE Chair",
         w=460, d=520, h=820, f="off_white", op=0.80, omn=0.20, p=390, v=0,
         cf=0, load=0, alt=["grey"]),
    dict(c="chair", zh="TEODORES 椅", en="TEODORES Chair",
         w=440, d=500, h=800, f="bamboo", op=0.40, omn=0.60, p=290, v=0,
         cf=0, load=0),
    dict(c="chair", zh="ODGER 椅", en="ODGER Chair",
         w=460, d=520, h=850, f="light_wood", op=0.50, omn=0.50, p=490, v=0,
         cf=0, load=0, alt=["blue"]),
    dict(c="chair", zh="MARIUS 圓凳", en="MARIUS Stool",
         w=360, d=360, h=450, f="black", op=0.70, omn=0.30, p=99, v=0,
         cf=0, load=0, sz=[(360, 360, 450)], alt=["off_white"]),
    dict(c="chair", zh="NISSE 摺凳", en="NISSE Folding stool",
         w=400, d=400, h=450, f="birch", op=0.25, omn=0.75, p=199, v=0,
         cf=0, load=0),
    dict(c="chair", zh="TERJE 摺椅", en="TERJE Folding chair",
         w=440, d=480, h=800, f="off_white", op=0.30, omn=0.70, p=249, v=0,
         cf=0, load=0, alt=["black"]),
    dict(c="chair", zh="BEKVÄM 腳凳", en="BEKVÄM Stepladder",
         w=430, d=380, h=500, f="light_wood", op=0.20, omn=0.80, p=349, v=0,
         cf=0, load=0),
    dict(c="chair", zh="FROSTA 凳", en="FROSTA Stool",
         w=350, d=350, h=450, f="light_wood", op=0.60, omn=0.40, p=199, v=0,
         cf=0, load=0),
    dict(c="chair", zh="TOBIAS 透明椅", en="TOBIAS Chair, clear",
         w=480, d=520, h=830, f="clear", op=0.30, omn=0.70, refl=0.70, p=690, v=0,
         cf=0, load=0),
    dict(c="chair", zh="AGEN 藤編椅", en="AGEN Chair, rattan",
         w=470, d=530, h=810, f="rattan", op=0.25, omn=0.75, p=990, v=0,
         cf=0, load=0),

    # ======================= 梳化 sofa (9) =======================
    dict(c="sofa", zh="SÖDERHAMN 三人梳化", en="SÖDERHAMN 3-seat sofa",
         w=2360, d=990, h=830, f="grey", op=1.00, omn=0.00, p=5990, v=0,
         cf=0, load=0, sz=[(1860, 990, 830)], alt=["beige"]),
    dict(c="sofa", zh="KIVIK 三人梳化", en="KIVIK 3-seat sofa",
         w=2280, d=950, h=830, f="beige", op=1.00, omn=0.00, p=6990, v=0,
         cf=0, load=0, sz=[(1780, 950, 830)], alt=["dark_grey"]),
    dict(c="sofa", zh="EKTORP 三人梳化", en="EKTORP 3-seat sofa",
         w=2180, d=880, h=880, f="off_white", op=1.00, omn=0.00, p=5490, v=0,
         cf=0, load=0, sz=[(1640, 880, 880)]),
    dict(c="sofa", zh="LANDSKRONA 三人梳化", en="LANDSKRONA 3-seat sofa",
         w=2210, d=880, h=780, f="sage", op=1.00, omn=0.00, p=7990, v=0,
         cf=0, load=0),
    dict(c="sofa", zh="NORSBORG 三人梳化", en="NORSBORG 3-seat sofa",
         w=2260, d=950, h=850, f="dark_grey", op=1.00, omn=0.00, p=6990, v=0,
         cf=0, load=0),
    dict(c="sofa", zh="FRIHETEN 梳化床", en="FRIHETEN Sofa-bed",
         w=2200, d=1000, h=830, f="dark_grey", op=1.00, omn=0.00, p=7990, v=120,
         cf=0, load=0),
    dict(c="sofa", zh="GRÖNLID 雙人梳化", en="GRÖNLID 2-seat sofa",
         w=1740, d=980, h=1040, f="beige", op=1.00, omn=0.00, p=6490, v=0,
         cf=0, load=0, sz=[(1740, 980, 1040)]),
    dict(c="sofa", zh="ÄPPLARYD 雙人梳化", en="ÄPPLARYD 2-seat sofa",
         w=1680, d=920, h=840, f="off_white", op=0.90, omn=0.10, p=5990, v=0,
         cf=0, load=0, alt=["sage"]),
    dict(c="sofa", zh="VIMLE 三人梳化", en="VIMLE 3-seat sofa",
         w=2310, d=980, h=830, f="dark_grey", op=1.00, omn=0.00, p=8990, v=0,
         cf=0, load=0, sz=[(1660, 980, 830)]),

    # ======================= 收納盒 storage_box (13) =======================
    dict(c="storage_box", zh="KUGGIS 附蓋收納盒", en="KUGGIS Box with lid",
         w=370, d=540, h=300, f="off_white", op=1.00, omn=0.00, p=149, v=45,
         cf=0, load=15, sz=[(370, 540, 300)], alt=["black"]),
    dict(c="storage_box", zh="DRÖNA 收納盒", en="DRÖNA Box",
         w=330, d=380, h=310, f="dark_grey", op=1.00, omn=0.00, p=59, v=30,
         cf=0, load=12, alt=["beige"]),
    dict(c="storage_box", zh="TJENA 附蓋收納盒", en="TJENA Box with lid",
         w=250, d=350, h=200, f="off_white", op=1.00, omn=0.00, p=49, v=14,
         cf=0, load=8),
    dict(c="storage_box", zh="PAPPIS 附蓋收納盒", en="PAPPIS Box with lid",
         w=250, d=350, h=200, f="light_wood", op=1.00, omn=0.00, p=79, v=14,
         cf=0, load=8),
    dict(c="storage_box", zh="KNUFF 雜誌收納", en="KNUFF Magazine file",
         w=180, d=250, h=300, f="off_white", op=0.60, omn=0.40, p=39, v=10,
         cf=0, load=5),
    dict(c="storage_box", zh="VARIERA 收納箱", en="VARIERA Box",
         w=320, d=240, h=150, f="off_white", op=0.50, omn=0.50, p=49, v=8,
         cf=0, load=5, alt=["black"]),
    dict(c="storage_box", zh="SAMLA 透明收納箱", en="SAMLA Box, clear",
         w=560, d=390, h=420, f="clear", op=0.15, omn=0.85, refl=0.70, p=149,
         v=75, cf=0, load=10, sz=[(790, 570, 430)]),
    dict(c="storage_box", zh="RÅSKOG 推車", en="RÅSKOG Trolley",
         w=350, d=450, h=780, f="off_white", op=0.20, omn=0.80, p=349, v=60,
         cf=0, load=10, alt=["black"]),
    dict(c="storage_box", zh="DRAGGAN 竹推車", en="DRAGGAN Trolley, bamboo",
         w=380, d=460, h=800, f="bamboo", op=0.20, omn=0.80, p=690, v=70,
         cf=0, load=10),
    dict(c="storage_box", zh="VESKEN 層架推車", en="VESKEN Shelving unit",
         w=540, d=180, h=700, f="off_white", op=0.20, omn=0.80, p=149, v=35,
         cf=0, load=8, sz=[(540, 180, 700)], alt=["black"]),
    dict(c="storage_box", zh="KVISSLE 文件收納", en="KVISSLE Desk organizer",
         w=320, d=180, h=180, f="off_white", op=0.70, omn=0.30, p=129, v=6,
         cf=0, load=4),
    dict(c="storage_box", zh="SKUBB 布藝收納盒", en="SKUBB Box, fabric",
         w=310, d=550, h=180, f="dark_grey", op=1.00, omn=0.00, p=99, v=20,
         cf=0, load=6, alt=["beige"]),
    dict(c="storage_box", zh="TORKIS 洗衣籃", en="TORKIS Laundry basket",
         w=390, d=390, h=600, f="blue", op=0.35, omn=0.65, p=79, v=50,
         cf=0, load=6),

    # ======================= 層架 shelf (9) =======================
    dict(c="shelf", zh="BILLY 書櫃", en="BILLY Bookcase",
         w=800, d=280, h=2020, f="off_white", op=0.80, omn=0.20, p=790, v=240,
         cf=0, load=25, sz=[(800, 280, 1060), (400, 280, 2020)]),
    dict(c="shelf", zh="BILLY 書櫃", en="BILLY Bookcase",
         w=800, d=280, h=2020, f="walnut", op=0.80, omn=0.20, p=990, v=240,
         cf=0, load=25, sz=[(800, 280, 1060)]),
    dict(c="shelf", zh="BILLY 窄書櫃", en="BILLY Bookcase, narrow",
         w=400, d=280, h=2020, f="off_white", op=0.80, omn=0.20, p=490, v=120,
         cf=0, load=20),
    dict(c="shelf", zh="KALLAX 層架", en="KALLAX Shelving unit",
         w=770, d=390, h=1470, f="off_white", op=0.40, omn=0.75, p=890, v=300,
         cf=0, load=25, sz=[(1470, 390, 1470)]),
    dict(c="shelf", zh="KALLAX 層架", en="KALLAX Shelving unit",
         w=770, d=390, h=1470, f="black_brown", op=0.40, omn=0.75, p=890, v=300,
         cf=0, load=25),
    dict(c="shelf", zh="EKET 層架", en="EKET Shelving unit",
         w=700, d=350, h=700, f="light_wood", op=0.40, omn=0.70, p=690, v=180,
         cf=0, load=20, sz=[(700, 350, 1400)], alt=["off_white"]),
    dict(c="shelf", zh="HYLLIS 層架", en="HYLLIS Shelving unit",
         w=600, d=270, h=1400, f="chrome", op=0.10, omn=0.95, p=349, v=90,
         cf=0, load=15, sz=[(1800, 270, 1400)]),
    dict(c="shelf", zh="IVAR 層架", en="IVAR Shelving unit",
         w=890, d=300, h=1790, f="light_wood", op=0.20, omn=0.90, p=1090, v=260,
         cf=0, load=20, sz=[(890, 300, 1240)]),
    dict(c="shelf", zh="OMAR 層架", en="OMAR Shelving unit",
         w=900, d=450, h=1810, f="chrome", op=0.10, omn=0.95, p=990, v=320,
         cf=0, load=20, sz=[(450, 450, 1810)]),

    # ======================= 鏡 mirror (4) =======================
    dict(c="mirror", zh="NISSEDAL 鏡", en="NISSEDAL Mirror",
         w=400, d=30, h=1500, f="mirror", op=1.00, omn=0.00, refl=0.95, p=490,
         v=0, cf=0, load=0, m=("wall",), sz=[(400, 30, 1500)]),
    dict(c="mirror", zh="HOVET 鏡", en="HOVET Mirror",
         w=780, d=30, h=1960, f="mirror", op=1.00, omn=0.00, refl=0.95, p=990,
         v=0, cf=0, load=0, m=("wall",)),
    dict(c="mirror", zh="IKORNNES 鏡", en="IKORNNES Mirror",
         w=520, d=30, h=1640, f="mirror", op=1.00, omn=0.00, refl=0.95, p=790,
         v=0, cf=0, load=0, m=("wall",), sz=[(520, 30, 1640)]),
    dict(c="mirror", zh="LINDBYN 鏡", en="LINDBYN Mirror",
         w=500, d=30, h=1600, f="mirror", op=1.00, omn=0.00, refl=0.95, p=690,
         v=0, cf=0, load=0, m=("wall",)),

    # ======================= 雪櫃 fridge (6) =======================
    dict(c="fridge", zh="LAGAN 雪櫃", en="LAGAN Fridge",
         w=550, d=580, h=850, f="off_white", op=1.00, omn=0.00, p=1990, v=0,
         cf=600, cs=50, ct=100, load=20, a="pre_assembled", sz=[(550, 580, 850)]),
    dict(c="fridge", zh="LAGAN 雙門雪櫃", en="LAGAN Fridge/freezer",
         w=550, d=580, h=1350, f="off_white", op=1.00, omn=0.00, p=2990, v=0,
         cf=600, cs=50, ct=100, load=20, a="pre_assembled"),
    dict(c="fridge", zh="MEDELSTOR 雪櫃", en="MEDELSTOR Fridge",
         w=600, d=600, h=850, f="steel", op=1.00, omn=0.00, refl=0.55, p=2990,
         v=0, cf=600, cs=50, ct=100, load=20, a="pre_assembled"),
    dict(c="fridge", zh="UDDEN 雪櫃", en="UDDEN Fridge/freezer",
         w=600, d=620, h=1850, f="off_white", op=1.00, omn=0.00, p=4990, v=0,
         cf=600, cs=50, ct=100, load=20, a="pre_assembled"),
    dict(c="fridge", zh="VÄRMD 雪櫃", en="VÄRMD Fridge",
         w=600, d=600, h=850, f="off_white", op=1.00, omn=0.00, p=2490, v=0,
         cf=600, cs=50, ct=100, load=20, a="pre_assembled"),
    dict(c="fridge", zh="LAGAN 迷你雪櫃", en="LAGAN Mini fridge",
         w=450, d=450, h=500, f="off_white", op=1.00, omn=0.00, p=990, v=0,
         cf=500, cs=50, ct=100, load=15, a="pre_assembled"),

    # ======================= 洗衣機 washing_machine (5) =======================
    dict(c="washing_machine", zh="LAGAN 前置式洗衣機", en="LAGAN Front-load washer",
         w=600, d=520, h=850, f="off_white", op=1.00, omn=0.00, p=2490, v=0,
         cf=600, cs=20, load=20, a="pre_assembled", sz=[(600, 520, 850)]),
    dict(c="washing_machine", zh="TVÄTTAD 洗衣機", en="TVÄTTAD Washing machine",
         w=600, d=600, h=850, f="off_white", op=1.00, omn=0.00, p=3990, v=0,
         cf=600, cs=20, load=20, a="pre_assembled"),
    dict(c="washing_machine", zh="MEDELSTOR 洗衣機", en="MEDELSTOR Washing machine",
         w=600, d=560, h=850, f="steel", op=1.00, omn=0.00, refl=0.55, p=3490,
         v=0, cf=600, cs=20, load=20, a="pre_assembled"),
    dict(c="washing_machine", zh="RENLIG 洗衣機", en="RENLIG Washing machine",
         w=600, d=540, h=850, f="off_white", op=1.00, omn=0.00, p=2990, v=0,
         cf=600, cs=20, load=20, a="pre_assembled"),
    dict(c="washing_machine", zh="LAGAN 上置式洗衣機", en="LAGAN Top-load washer",
         w=560, d=560, h=860, f="off_white", op=1.00, omn=0.00, p=2190, v=0,
         cf=0, cs=20, ct=600, load=25, a="pre_assembled"),

    # ======================= 微波爐 microwave (4) =======================
    dict(c="microwave", zh="MEDELSTOR 微波爐", en="MEDELSTOR Microwave",
         w=460, d=370, h=290, f="off_white", op=1.00, omn=0.00, p=690, v=0,
         cf=500, ct=50, load=0, a="pre_assembled"),
    dict(c="microwave", zh="LAGAN 微波爐", en="LAGAN Microwave",
         w=440, d=350, h=260, f="off_white", op=1.00, omn=0.00, p=490, v=0,
         cf=500, ct=50, load=0, a="pre_assembled"),
    dict(c="microwave", zh="MATÄLSKARE 微波爐", en="MATÄLSKARE Microwave",
         w=500, d=400, h=300, f="black", op=1.00, omn=0.00, refl=0.45, p=1290,
         v=0, cf=500, ct=50, load=0, a="pre_assembled"),
    dict(c="microwave", zh="SVÄRTA 微波爐", en="SVÄRTA Microwave",
         w=460, d=360, h=280, f="off_white", op=1.00, omn=0.00, p=890, v=0,
         cf=500, ct=50, load=0, a="pre_assembled"),

    # ========= 冷氣機 aircon (5) -- IKEA HK 不售，retailer 用 placeholder =========
    dict(c="aircon", zh="窗口式冷氣機 3/4 匹", en="Window air conditioner, 3/4 HP",
         w=450, d=600, h=350, f="off_white", op=1.00, omn=0.00, p=2990, v=0,
         cf=300, cs=50, ct=100, load=0, a="pre_assembled", m=("wall",),
         r="HK retail (placeholder)"),
    dict(c="aircon", zh="窗口式冷氣機 1 匹", en="Window air conditioner, 1 HP",
         w=560, d=660, h=380, f="off_white", op=1.00, omn=0.00, p=3990, v=0,
         cf=300, cs=50, ct=100, load=0, a="pre_assembled", m=("wall",),
         r="HK retail (placeholder)"),
    dict(c="aircon", zh="分體式冷氣室內機 1 匹", en="Split-type indoor unit, 1 HP",
         w=800, d=200, h=290, f="off_white", op=1.00, omn=0.00, p=3990, v=0,
         cf=200, ct=150, load=0, a="pre_assembled", m=("wall",),
         r="HK retail (placeholder)"),
    dict(c="aircon", zh="分體式冷氣室內機 1.5 匹", en="Split-type indoor unit, 1.5 HP",
         w=900, d=220, h=300, f="off_white", op=1.00, omn=0.00, p=4990, v=0,
         cf=200, ct=150, load=0, a="pre_assembled", m=("wall",),
         r="HK retail (placeholder)"),
    dict(c="aircon", zh="移動式冷氣機", en="Portable air conditioner",
         w=350, d=400, h=700, f="off_white", op=1.00, omn=0.00, p=2990, v=0,
         cf=300, cs=100, ct=150, load=0, a="pre_assembled",
         r="HK retail (placeholder)"),

    # ======================= 晾衣架 drying_rack (9) =======================
    dict(c="drying_rack", zh="JÄLL 晾衣架", en="JÄLL Drying rack",
         w=600, d=600, h=1400, f="off_white", op=0.15, omn=0.90, p=149, v=0,
         cf=0, load=15, sz=[(600, 600, 1400)]),
    dict(c="drying_rack", zh="MULIG 晾衣架", en="MULIG Drying rack",
         w=700, d=500, h=1400, f="off_white", op=0.15, omn=0.90, p=199, v=0,
         cf=0, load=15),
    dict(c="drying_rack", zh="FROST 晾衣架", en="FROST Drying rack",
         w=600, d=500, h=1400, f="chrome", op=0.15, omn=0.90, p=249, v=0,
         cf=0, load=15),
    dict(c="drying_rack", zh="TORKIS 晾衣架", en="TORKIS Drying rack",
         w=800, d=550, h=1300, f="blue", op=0.15, omn=0.90, p=129, v=0,
         cf=0, load=12),
    dict(c="drying_rack", zh="PRESSAD 晾衣架", en="PRESSAD Drying rack",
         w=700, d=400, h=1500, f="steel", op=0.15, omn=0.90, p=349, v=0,
         cf=0, load=15, sz=[(700, 400, 1500)]),
    dict(c="drying_rack", zh="MULIG 掛牆晾衣架", en="MULIG Wall-mounted drying rack",
         w=600, d=300, h=300, f="off_white", op=0.15, omn=0.90, p=99, v=0,
         cf=0, load=8, m=("wall",)),
    dict(c="drying_rack", zh="JÄLL 掛牆晾衣架", en="JÄLL Wall-mounted drying rack",
         w=500, d=250, h=250, f="off_white", op=0.15, omn=0.90, p=79, v=0,
         cf=0, load=8, m=("wall",)),
    dict(c="drying_rack", zh="伸縮晾衣桿", en="Extendable drying rod",
         w=1200, d=60, h=60, f="chrome", op=0.10, omn=1.00, p=129, v=0,
         cf=0, ct=300, load=10, m=("wall",), sz=[(2000, 60, 60)]),
    dict(c="drying_rack", zh="落地折疊晾衣架", en="Folding floor drying rack",
         w=900, d=600, h=1500, f="off_white", op=0.15, omn=0.90, p=299, v=0,
         cf=0, load=15),

    # ======================= 屏風 screen (8) =======================
    dict(c="screen", zh="RISÖR 屏風", en="RISÖR Room divider",
         w=1200, d=30, h=1700, f="bamboo", op=0.20, omn=0.80, p=1290, v=0,
         cf=0, load=0, sz=[(1200, 30, 1700)]),
    dict(c="screen", zh="三扇折疊屏風", en="3-panel folding screen",
         w=1350, d=30, h=1700, f="off_white", op=0.35, omn=0.65, p=499, v=0,
         cf=0, load=0, alt=["black"]),
    dict(c="screen", zh="四扇折疊屏風", en="4-panel folding screen",
         w=1800, d=30, h=1700, f="light_wood", op=0.35, omn=0.65, p=699, v=0,
         cf=0, load=0),
    dict(c="screen", zh="兩扇布藝屏風", en="2-panel fabric screen",
         w=900, d=30, h=1600, f="beige", op=0.30, omn=0.70, p=399, v=0,
         cf=0, load=0, alt=["off_white"]),
    dict(c="screen", zh="紗簾屏風", en="Sheer curtain screen",
         w=1500, d=20, h=1900, f="off_white", op=0.10, omn=0.90, p=299, v=0,
         cf=0, load=0),
    dict(c="screen", zh="儲物屏風架", en="Storage screen shelving",
         w=1200, d=350, h=1700, f="light_wood", op=0.30, omn=0.70, p=1290, v=180,
         cf=0, load=15),
    dict(c="screen", zh="藤編屏風", en="Rattan screen",
         w=1350, d=30, h=1700, f="rattan", op=0.25, omn=0.75, p=899, v=0,
         cf=0, load=0),
    dict(c="screen", zh="PVC 摺疊屏風", en="PVC folding screen",
         w=1200, d=25, h=1700, f="off_white", op=0.40, omn=0.60, p=399, v=0,
         cf=0, load=0),
]

DEFAULT_TOTAL = 150  # == len(TEMPLATES)


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------
def footprint_rect(w: int, d: int):
    """矩形 footprint，四角坐标 (整数 mm)。"""
    return [[0, 0], [w, 0], [w, d], [0, d]]


def visual_mass_raw(w: int, d: int, h: int, opacity: float, lightness_L: int) -> float:
    """规范公式: (footprint_area_mm2/1e6) * (height_mm/1000) * opacity * (1 - L/100)"""
    footprint_area_mm2 = w * d
    return (footprint_area_mm2 / 1e6) * (h / 1000.0) * opacity * (1.0 - lightness_L / 100.0)


def make_code(ti: int, vi: int) -> str:
    """由 (模板序号, 变体序号) 稳定派生的 8 位货号 -- 与 --total 无关，保证可重复。"""
    return str(10000000 + ((ti * 7919 + vi * 104729) % 89999999))


def expand(template: dict, total: int):
    """展开模板的候选变体 [(w,d,h,finish), ...]，第 0 个永远是主尺寸 + 主饰面。"""
    w, d, h, f = template["w"], template["d"], template["h"], template["f"]
    alts = template.get("alt") or ALT_FINISH.get(f, [])
    sizes = template.get("sz") or []
    variants = [(w, d, h, f)]
    for s in sizes:
        variants.append((s[0], s[1], s[2], f))
    for af in alts[:2]:
        variants.append((w, d, h, af))
        if sizes:
            s = sizes[0]
            variants.append((s[0], s[1], s[2], af))
    seen, uniq = set(), []
    for v in variants:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def base_quota(base_counts: dict) -> dict:
    return {c: base_counts.get(c, 0) for c in CATEGORIES}


def scale_down_quota(total: int, base_counts: dict) -> dict:
    """最大余数法: 把 total 条按模板比例分配到各类目。"""
    scaled = {c: base_counts.get(c, 0) * total / DEFAULT_TOTAL for c in CATEGORIES}
    q = {c: int(v) for c, v in scaled.items()}
    rem = total - sum(q.values())
    order = sorted(scaled.items(), key=lambda kv: (-(kv[1] - int(kv[1])), kv[0]))
    for c, _ in order[:max(0, rem)]:
        q[c] += 1
    return q


def build(total: int, seed: int):
    base_counts = Counter(t["c"] for t in TEMPLATES)
    assert sum(base_counts.values()) == DEFAULT_TOTAL, \
        f"模板表必须正好 {DEFAULT_TOTAL} 条，当前 {sum(base_counts.values())}"

    pool_by_cat = {c: [] for c in CATEGORIES}
    for ti, t in enumerate(TEMPLATES):
        pool_by_cat[t["c"]].append((ti, t, expand(t, total)))

    caps = {c: sum(len(vs) for _, _, vs in pool_by_cat[c]) for c in CATEGORIES}

    if total >= DEFAULT_TOTAL:
        quota = base_quota(base_counts)
        extra = total - DEFAULT_TOTAL
        i, guard = 0, 0
        while extra > 0 and guard < 500000:
            guard += 1
            c = CATEGORIES[i % len(CATEGORIES)]
            i += 1
            if quota[c] < caps[c]:
                quota[c] += 1
                extra -= 1
        if extra > 0:
            print(f"[warn] 变体池耗尽，实际生成 {total - extra} 条 (池上限 {sum(caps.values())})",
                  file=sys.stderr)
    else:
        quota = scale_down_quota(total, base_counts)

    # 轮转取数: 第一轮保证每个模板都出场 (高/低对比对不会丢)
    picked = []
    for c in CATEGORIES:
        want = quota.get(c, 0)
        if want <= 0:
            continue
        order, depth = [], 0
        while True:
            added = False
            for ti, t, vs in pool_by_cat[c]:
                if depth < len(vs):
                    order.append((ti, t, vs[depth]))
                    added = True
            if not added:
                break
            depth += 1
        picked.extend(order[:want])

    codes, records, raws = set(), [], []
    for ti, t, (w, d, h, fk) in picked:
        L_fin, refl_fin, col_fin = FINISHES[fk]
        L = t["L"] if t.get("L") is not None else L_fin
        refl = t["refl"] if t.get("refl") is not None else refl_fin
        col = t.get("col") or col_fin
        op, omn = float(t["op"]), float(t["omn"])

        code, vi = (t.get("code") or make_code(ti, 0)), 1
        while code in codes:
            code = make_code(ti, vi)
            vi += 1
        codes.add(code)

        retailer = t.get("r", "IKEA HK")
        prefix = "ikea-hk" if retailer == "IKEA HK" else "hkretail"
        load = int(t.get("load", 0))

        rec = OrderedDict()
        rec["id"] = f"{prefix}-{code}"
        rec["name_zh"] = t["zh"]
        rec["name_en"] = t["en"]
        rec["category"] = t["c"]
        rec["retailer"] = retailer
        rec["dims"] = OrderedDict(w=int(w), d=int(d), h=int(h))
        rec["footprint_L"] = footprint_rect(w, d)
        rec["clearance"] = OrderedDict(
            front=int(t.get("cf", 0)),
            left=int(t.get("cs", 0)),
            right=int(t.get("cs", 0)),
            top=int(t.get("ct", 0)),
        )
        rec["mount"] = list(t.get("m", ("floor",)))
        rec["can_support"] = OrderedDict(
            max_load_kg=load,
            top_polygon=footprint_rect(w, d) if load > 0 else [],
        )
        rec["visual"] = OrderedDict(
            lightness_L=int(L),
            opacity=round(op, 2),
            reflectivity=round(float(refl), 2),
            openness=round(omn, 2),
            color_family=col,
            visual_mass_index=0.0,   # 归一化后回填
        )
        rec["price_hkd"] = int(t["p"])
        rec["price_verified"] = False
        rec["url"] = ""
        rec["stock"] = "in_stock"
        rec["storage_volume_L"] = int(t.get("v", 0))
        rec["assembly"] = t.get("a", "flat_pack")

        raws.append(visual_mass_raw(w, d, h, op, int(L)))
        records.append(rec)

    lo, hi = min(raws), max(raws)
    span = (hi - lo) or 1.0
    for rec, raw in zip(records, raws):
        rec["visual"]["visual_mass_index"] = round((raw - lo) / span, 4)

    rng = random.Random(seed)
    for rec in records:
        rec["stock"] = ("in_stock" if rng.random() < 0.88 else
                        ("low_stock" if rng.random() < 0.67 else "out_of_stock"))
    return records, lo, hi


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
REQUIRED_TOP = ["id", "name_zh", "name_en", "category", "retailer", "dims",
                "footprint_L", "clearance", "mount", "can_support", "visual",
                "price_hkd", "price_verified", "url", "stock",
                "storage_volume_L", "assembly"]


def validate(records):
    errs, ids = [], set()
    for i, r in enumerate(records):
        for k in REQUIRED_TOP:
            if k not in r:
                errs.append(f"#{i} 缺字段 {k}")
        if r.get("id") in ids:
            errs.append(f"#{i} id 重复 {r.get('id')}")
        ids.add(r.get("id"))
        if r.get("category") not in CATEGORIES:
            errs.append(f"#{i} 非法 category {r.get('category')}")
        if r.get("price_verified") is not False:
            errs.append(f"#{i} price_verified 必须为 false")
        d = r.get("dims", {})
        if not all(isinstance(d.get(k), int) and d[k] > 0 for k in ("w", "d", "h")):
            errs.append(f"#{i} dims 必须是正整数 mm")
        else:
            exp = [[0, 0], [d["w"], 0], [d["w"], d["d"]], [0, d["d"]]]
            if r["footprint_L"] != exp:
                errs.append(f"#{i} footprint_L 与 dims 不一致")
        cs = r.get("clearance", {})
        for k in ("front", "left", "right", "top"):
            if not isinstance(cs.get(k), int) or cs[k] < 0:
                errs.append(f"#{i} clearance.{k} 必须非负整数 mm")
        if not r.get("mount") or any(m not in ("floor", "wall", "ceiling") for m in r["mount"]):
            errs.append(f"#{i} mount 非法 {r.get('mount')}")
        v = r.get("visual", {})
        if not (0 <= v.get("lightness_L", -1) <= 100):
            errs.append(f"#{i} lightness_L 越界")
        for k in ("opacity", "reflectivity", "openness"):
            if not (0.0 <= v.get(k, -1) <= 1.0):
                errs.append(f"#{i} visual.{k} 越界 {v.get(k)}")
        if v.get("color_family") not in ("warm", "cool", "neutral"):
            errs.append(f"#{i} color_family 非法")
        if not (0.0 <= v.get("visual_mass_index", -1) <= 1.0):
            errs.append(f"#{i} visual_mass_index 越界")
        su = r.get("can_support", {})
        if su.get("max_load_kg", -1) < 0:
            errs.append(f"#{i} max_load_kg 为负")
        if su.get("max_load_kg", 0) > 0 and len(su.get("top_polygon", [])) != 4:
            errs.append(f"#{i} 有承重但 top_polygon 非矩形")
        if su.get("max_load_kg", 0) == 0 and su.get("top_polygon") != []:
            errs.append(f"#{i} 无承重但 top_polygon 非空")
        if not isinstance(r.get("price_hkd"), int) or r["price_hkd"] <= 0:
            errs.append(f"#{i} price_hkd 非法")
        if r.get("url") != "":
            errs.append(f"#{i} url 必须为空 (未核实)")
    return errs


# ---------------------------------------------------------------------------
# 统计报告
# ---------------------------------------------------------------------------
def report(records):
    n = len(records)
    out = [f"总数: {n}", "", "| category | count |", "|---|---|"]
    cat = Counter(r["category"] for r in records)
    for c in CATEGORIES:
        out.append(f"| {c} | {cat.get(c, 0)} |")
    out.append(f"| **合计** | **{n}** |")

    vmi = sorted(r["visual"]["visual_mass_index"] for r in records)
    q = lambda p: vmi[min(n - 1, int(p * n))]
    buckets = [0] * 10
    for x in vmi:
        buckets[min(9, int(x * 10))] += 1
    out += ["", "visual_mass_index 分布 (0-1, 10 等分):", TICK3]
    for i, b in enumerate(buckets):
        out.append(f"  [{i/10:.1f}, {(i+1)/10:.1f})  {'#' * b} {b}")
    out.append(f"  min={vmi[0]:.4f}  p25={q(0.25):.4f}  median={q(0.50):.4f}"
               f"  p75={q(0.75):.4f}  max={vmi[-1]:.4f}")
    mean = sum(vmi) / n
    out.append(f"  均值={mean:.4f}   标准差={(sum((x-mean)**2 for x in vmi)/n)**0.5:.4f}")
    out.append(TICK3)

    def spread(key, fmt="{:.2f}"):
        vals = [r["visual"][key] for r in records]
        return (f"min={fmt.format(min(vals))} max={fmt.format(max(vals))} "
                f"distinct={len(set(vals))}")

    out += ["",
            f"lightness_L : {spread('lightness_L', '{:.0f}')}",
            f"opacity     : {spread('opacity')}",
            f"reflectivity: {spread('reflectivity')}",
            f"openness    : {spread('openness')}",
            f"color_family: {dict(Counter(r['visual']['color_family'] for r in records))}",
            f"mount       : {dict(Counter(m for r in records for m in r['mount']))}",
            f"assembly    : {dict(Counter(r['assembly'] for r in records))}",
            f"stock       : {dict(Counter(r['stock'] for r in records))}"]

    pr = sorted(r["price_hkd"] for r in records)
    out.append(f"price_hkd   : min={pr[0]} median={pr[n//2]} max={pr[-1]} sum={sum(pr)}")
    out.append(f"storage_L   : sum={sum(r['storage_volume_L'] for r in records)} "
               f"items_with_storage={sum(1 for r in records if r['storage_volume_L'] > 0)}")
    dh = sorted(r["dims"]["h"] for r in records)
    out.append(f"height_mm   : min={dh[0]} median={dh[n//2]} max={dh[-1]}")
    out.append("clearance.front 分布: " + str(dict(sorted(
        Counter(r["clearance"]["front"] for r in records).items()))))
    out += ["", "每类目 visual_mass_index 中位数:",
            "| category | n | vmi_median | vmi_min | vmi_max | lightness_median |",
            "|---|---|---|---|---|---|"]
    for c in CATEGORIES:
        rows = [r for r in records if r["category"] == c]
        if not rows:
            continue
        vs = sorted(r["visual"]["visual_mass_index"] for r in rows)
        ls = sorted(r["visual"]["lightness_L"] for r in rows)
        out.append(f"| {c} | {len(rows)} | {vs[len(vs)//2]:.4f} | {vs[0]:.4f} | "
                   f"{vs[-1]:.4f} | {ls[len(ls)//2]} |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="生成 HK 小户型 Demo 家具 SKU 种子数据集")
    ap.add_argument("--total", type=int, default=DEFAULT_TOTAL, help="SKU 总数 (默认 150)")
    ap.add_argument("--seed", type=int, default=20261004, help="库存状态随机种子")
    ap.add_argument("--out", default=str(HERE / "seed_skus.json"), help="输出 JSON 路径")
    ap.add_argument("--stats", action="store_true", help="打印统计报告")
    ap.add_argument("--check", action="store_true", help="只校验已存在的 JSON")
    args = ap.parse_args()

    if args.check:
        data = json.loads(Path(args.out).read_text(encoding="utf-8"))
        recs = data["skus"] if isinstance(data, dict) else data
        errs = validate(recs)
        print(f"校验 {args.out}: {len(recs)} 条, 错误 {len(errs)}")
        for e in errs[:50]:
            print("  -", e)
        print(report(recs))
        return 0 if not errs else 1

    records, lo, hi = build(args.total, args.seed)
    errs = validate(records)
    if errs:
        print("校验失败:", file=sys.stderr)
        for e in errs[:50]:
            print("  -", e, file=sys.stderr)
        return 1

    payload = OrderedDict()
    payload["schema_version"] = "1.0"
    payload["unit"] = "mm"
    payload["generated_by"] = "build_catalog.py"
    payload["total"] = len(records)
    payload["price_verified_all_false"] = True
    payload["vmi_raw_min"] = round(lo, 6)
    payload["vmi_raw_max"] = round(hi, 6)
    payload["skus"] = records

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"OK 写入 {out}  共 {len(records)} 条 SKU, 校验通过 (0 error)")
    if args.stats:
        print()
        print(report(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
