# DEPRECATED —— 已废弃，不要运行。
# 本文件的几何是在一张 1600x1200 的付费预览缩略图上做「摄影测量」得到的：
# 自定形状、自量像素、自定比例尺，两轴还各自定标（11.60 / 12.88 mm/px）。
# 结果凭空造出一条图上不存在的「西侧竖带」，引擎把沙发茶几摆到屋外。
# 唯一有效的替代品：services/floorplan/make_hk_onpak05.py
#   （v3，几何全部由售楼说明书向量 PDF 上的印刷尺寸链累加而来）。
# 保留此文件只为记录教训；其中 493 平方呎商品页的「擺位設計圖」仍可作为
# 「真人怎么摆」的参考，但要重新锚定到 v3 的几何上。
"""安柏苑 05 室（居屋寶 擺位設計圖）—— 真实香港居屋户型 + 真实设计师摆位。

来源（同一份文件同时提供「真实施工图则」与「真实设计摆位」）
  居屋寶 Housing Pro「安柏苑 05室 擺位設計圖」，實用面積約 493 平方呎
    https://housingpro.com.hk/shop/layout-plan-on-pak-court-05-493sqft
  预览图 layout-plan-on-pak-court-05-493sqft-...-std1.jpg（1600x1200）
  交叉校验：房委會《安柏苑售樓說明書》2/F-9/F 平面圖（一手、印刷毫米尺寸）
    https://hos.housingauthority.gov.hk/mini-site/hos2024/common/pdf/Sales-Brochure-On-Pak.pdf

比例尺（两张图各自锚定在印刷尺寸上，不靠单一比例尺）
  东西向：程序化检测到的外墙中心距 754 px，图上印的单元总长 8745 mm -> 11.60 mm/px
  南北向：外墙中心距 557 px，图上印的右侧尺寸链合计 7175 mm        -> 12.88 mm/px
  （预览图被非等比缩放，所以两轴分开定标；单件家具反算校验：
    沙发 1,400x700 量到 110x63 px -> 12.7 / 11.1 mm/px，同量级）
  转录不确定度：房间边界 ±150 mm。

面积核对
  按下面房间划分相加约 45.6 m2 = 491 平方呎，对上商品页写的 493 平方呎（差 0.4%）。
"""
from __future__ import annotations

raise SystemExit("DEPRECATED: use make_hk_onpak05.py (printed dims from the vector PDF)")

import json
import os

SX, SY = 11.60, 12.88
PX0, PY0 = 428.0, 240.0


def mmx(v):
    return int(round((v - PX0) * SX))


def mmy(v):
    return int(round((v - PY0) * SY))


W, D = 8745, 7175
WALL = 250          # 主力墙（图上多处标 250）

# 房间划分（取自图上可见墙体；客厅是绕著睡房/浴室/厨房的 U 形）
ROOMS = [
    dict(id="bedroom", label="睡房（主）", poly=[(1390, 0), (3730, 0), (3730, 2770), (1390, 2770)],
         h=2750),
    dict(id="bath", label="浴室", poly=[(1390, 3660), (3730, 3660), (3730, 5350), (1390, 5350)],
         h=2750),
    dict(id="kitchen", label="廚房", poly=[(0, 5410), (2150, 5410), (2150, 7175), (0, 7175)],
         h=2750),
    dict(id="bedroom2", label="睡房（細）", poly=[(6403, 1900), (8745, 1900), (8745, 5410),
                                                 (6403, 5410)], h=2750),
    # ★ 客/飯廳是「繞住三間房」的 U 形 —— 用 shell + holes 表示。
    #   外殼特意避開廚房（廚房貼住西外牆同南外牆，做成洞會貼邊 -> 幾何無效），
    #   另外三間房做洞，各內縮 1mm 以免洞同外殼相切。
    dict(id="living", label="客/飯廳",
         poly=[(0, 0), (8745, 0), (8745, 7175), (2150, 7175), (2150, 5410), (0, 5410)],
         holes=[[(1391, 1), (3729, 1), (3729, 2769), (1391, 2769)],
                [(1391, 3661), (3729, 3661), (3729, 5349), (1391, 5349)],
                [(6404, 1901), (8744, 1901), (8744, 5409), (6404, 5409)]],
         h=2750),
]


# 门的位置取自图上画出来的开启弧线；窗取自外墙上的窗洞符号。
DOOR = lambda i, c, w, hinge="left": dict(id=i, kind="door", center_mm=c, width_mm=w,
                                          height_mm=2100, swing=dict(dir="in", hinge=hinge))
WIN = lambda i, c, w: dict(id=i, kind="window", center_mm=c, width_mm=w, height_mm=1350,
                           sill_h_mm=900, swing=None)


def room_walls():
    """每间房自己那一圈的墙 + 门 + 窗。共用墙在两边各列一次（渲染层会去重）。"""
    return {
        "living": [
            dict(id="wL_N", a=[0, 0], b=[8745, 0], type="load_bearing", thickness_mm=WALL,
                 openings=[WIN("n_liv1", 5200, 1500), WIN("n_liv2", 7500, 1200)]),
            dict(id="wL_E", a=[8745, 0], b=[8745, 7175], type="load_bearing",
                 thickness_mm=WALL, openings=[]),
            dict(id="wL_S", a=[8745, 7175], b=[2150, 7175], type="load_bearing",
                 thickness_mm=WALL, openings=[DOOR("d_entry", 3400, 900)]),
            dict(id="wL_Ke", a=[2150, 7175], b=[2150, 5410], type="partition",
                 thickness_mm=150, openings=[DOOR("d_kit", 800, 800)]),
            dict(id="wL_S2", a=[2150, 5410], b=[0, 5410], type="partition",
                 thickness_mm=150, openings=[]),
            dict(id="wL_W", a=[0, 5410], b=[0, 0], type="load_bearing",
                 thickness_mm=WALL, openings=[]),
        ],
        "bedroom": [
            dict(id="wB_W", a=[1390, 0], b=[1390, 2770], type="partition",
                 thickness_mm=150, openings=[]),
            dict(id="wB_N", a=[1390, 0], b=[3730, 0], type="load_bearing",
                 thickness_mm=WALL, openings=[WIN("n_bed", 1170, 1600)]),
            dict(id="wB_E", a=[3730, 0], b=[3730, 2770], type="partition",
                 thickness_mm=150, openings=[]),
            dict(id="wB_S", a=[3730, 2770], b=[1390, 2770], type="partition",
                 thickness_mm=150, openings=[DOOR("d_bed", 1700, 800)]),
        ],
        "bath": [
            dict(id="wBa_W", a=[1390, 3660], b=[1390, 5350], type="partition",
                 thickness_mm=150, openings=[]),
            dict(id="wBa_N", a=[1390, 3660], b=[3730, 3660], type="partition",
                 thickness_mm=150, openings=[]),
            dict(id="wBa_E", a=[3730, 3660], b=[3730, 5350], type="partition",
                 thickness_mm=150, openings=[DOOR("d_bath", 700, 700)]),
            dict(id="wBa_S", a=[3730, 5350], b=[1390, 5350], type="partition",
                 thickness_mm=150, openings=[]),
        ],
        "kitchen": [
            dict(id="wK_W", a=[0, 5410], b=[0, 7175], type="load_bearing",
                 thickness_mm=WALL, openings=[WIN("n_kit", 880, 1000)]),
            dict(id="wK_N", a=[0, 5410], b=[2150, 5410], type="partition",
                 thickness_mm=150, openings=[]),
            dict(id="wK_E", a=[2150, 5410], b=[2150, 7175], type="partition",
                 thickness_mm=150, openings=[DOOR("d_kit", 800, 800)]),
            dict(id="wK_S", a=[2150, 7175], b=[0, 7175], type="load_bearing",
                 thickness_mm=WALL, openings=[]),
        ],
        "bedroom2": [
            dict(id="wB2_W", a=[6403, 1900], b=[6403, 5410], type="partition",
                 thickness_mm=150, openings=[DOOR("d_bed2", 1750, 800)]),
            dict(id="wB2_N", a=[6403, 1900], b=[8745, 1900], type="partition",
                 thickness_mm=150, openings=[]),
            dict(id="wB2_E", a=[8745, 1900], b=[8745, 5410], type="load_bearing",
                 thickness_mm=WALL, openings=[WIN("n_bed2", 1750, 1500)]),
            dict(id="wB2_S", a=[8745, 5410], b=[6403, 5410], type="partition",
                 thickness_mm=150, openings=[]),
        ],
    }


def build() -> dict:
    rw = room_walls()
    rooms = []
    for r in ROOMS:
        rooms.append(dict(id=r["id"], label=r["label"], label_source="plan", label_score=1.0,
                          polygon=r["poly"], holes=r.get("holes", []),
                          area_mm2=0, height_mm=r["h"],
                          height_field=[],
                          walls=rw.get(r["id"], []),
                          openings=[o for w in rw.get(r["id"], []) for o in
                                    [dict(ww, wall=w["id"]) for ww in w["openings"]]]))
    return dict(flat_id="plan_hk_onpak05", units="mm", rooms=rooms,
                connections=[], fixed_services=[],
                entry=dict(room="living", point=[3700, 6800]),
                meta=dict(source="housingscheme_layout_plan",
                          confidence=0.75,
                          mm_per_px=[SX, SY],
                          scale_source="printed dimension chains",
                          scale_method="x: 8745mm over 754px; y: 7175mm over 557px",
                          reference=["居屋寶 安柏苑05室 擺位設計圖",
                                     "房委會 安柏苑售樓說明書 2/F-9/F 平面圖"],
                          area_check="room sum ~491 sq ft vs stated 493 sq ft"))


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    out = os.path.join(root, "out", "plan_hk_onpak05.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    p = build()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
    tot = 0
    for r in p["rooms"]:
        xs = [q[0] for q in r["polygon"]]
        ys = [q[1] for q in r["polygon"]]
        a = 0.0
        for i in range(len(r["polygon"])):
            x1, y1 = r["polygon"][i]
            x2, y2 = r["polygon"][(i + 1) % len(r["polygon"])]
            a += x1 * y2 - x2 * y1
        a = abs(a) / 2.0
        tot += a
        print("  %-10s %-10s %6.2f m2  bbox %dx%d" % (r["id"], r["label"], a / 1e6,
                                                      max(xs) - min(xs), max(ys) - min(ys)))
    print("合计 %.2f m2 = %.0f 平方呎（商品页写 493）" % (tot / 1e6, tot / 1e6 * 10.7639))
    print("->", out)


if __name__ == "__main__":
    main()
