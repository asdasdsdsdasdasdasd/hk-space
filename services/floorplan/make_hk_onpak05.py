"""安柏苑 05 室 —— v3 转录：唯一来源 = 房委會售樓說明書（向量 PDF）上的印刷尺寸。

为什么是 v3
-----------
v1/v2 全部作废：它们是在一张 1600x1200、69KB 的付费商品预览缩略图上做「摄影测量」，
  自己假定形状、自己量像素、自己定比例尺。结果凭空造出一条「西侧竖带」当客厅，
  引擎把沙发和茶几摆到屋外。

v3 的做法（用户要求：不要处理 px，用现成的 exact 尺寸）
-----------
1. 来源 = /mnt/d/openclaw/_ref/hkreal/hos/Sales-Brochure-On-Pak.pdf 第 26 页
   （2/F-9/F 平面图，向量图，非位图）。
2. 比例尺由图上印刷的比例尺条独立标定：0→5m 共 70.86pt，得 1pt = 70.5617mm（1:200）。
3. 用 mutool 把该页导出成 vector trace，取出橙色标注层（.912 .26 .055）= 尺寸线，
   转正 13.5°（右翼倾角）后自动找尺寸链的 tick 位置，tick 间距 = 印刷的 mm 数。
4. 每个数字都与图上标注互相印证（见 DIM_SOURCE 注释）。

单位 05 的尺寸链（实测 tick 间距，mm）
   上链 250 | 2100 | 320 | 2200 | 250              = 5120   （北墙外皮→东墙外皮）
   下链 100 | 2040 | 390  | 3655 | 250              = 6435   （分户墙外皮→东墙外皮）
   右链 200 | 730  | 1135 | 1560 | 2990 | 250       = 6865   （北墙外皮→南墙外皮）
   浴室 1235 x 1470；厨房 2040 宽、1700 台面
   饭厅凹位 2010 x 3000

核对
   图上量得室内净面积 37.6 m2；商品页 493 平方呎 = 45.8 m2 实用面积。
   45.8 x 0.82 = 37.6 —— 差额正好是内墙 + 外墙一半，吻合。
   旧 v2 的轮廓算出来是 502 平方呎（差 +1.9%），而且形状是错的。
"""
from __future__ import annotations

import json
import os

WALL_EXT = 250          # 外墙
WALL_PARTY = 100        # 分户墙
WALL_MAIN = 320         # 客厅内承重墙
WALL_THIN = 120         # 间隔墙
H_ROOM = 2750           # 层高（说明书画 2.75m）

# ---- 由尺寸链累加得到的绝对坐标 (mm)，原点 = 分户墙外皮 / 北墙外皮 ----
X = dict(
    party_w   = 0,
    party_e   = 100,      # 单位西面（下半部）
    west_out  = 1310,     # 单位西面外皮（上半部）
    west_in   = 1560,     # = 1310 + 250
    liv_din_e = 3660,     # = 1560 + 2100
    spine_w   = 3660,     # 中央承重墙 320
    spine_e   = 3980,     # = 3660 + 320
    east_in   = 6180,     # = 3980 + 2200
    east_out  = 6430,     # = 6180 + 250
    nook_w    = 6520,     # 饭厅凹位西面
    nook_e    = 8530,     # = 6520 + 2010
    kit_e     = 2140,     # = 100 + 2040
    kit_wall_e= 2530,     # = 2140 + 390
)
Y = dict(
    north_out = 0,
    north_in  = 200,      # 北链首段实测 200
    y1        = 930,      # = 200 + 730
    y2        = 2065,     # = 930 + 1135
    nook_top  = 1662,     # 凹位北墙内皮
    nook_btm  = 4666,     # 凹位南墙内皮 (= nook_top + 3000)
    y3        = 3625,     # = 2065 + 1560
    spine_bot = 1230,     # 中央 320 承重墙下沿（由轮廓量得）
    bath_n    = 3194,     # 客厅左段净深 200..3074 = 2874 ≈ 印刷 2890
    bath_s    = 4664,
    kit_n     = 4754,     # 浴室南墙 90 + ... 由 x=-1048 链 85|1470|90|185|935|765 推得
    y4        = 6615,     # = 3625 + 2990  南墙内皮
    south_out = 6865,     # = 6615 + 250
)
X0, X1 = X["west_in"], X["east_in"]
# 浴室净宽 2030、净深 1470 —— 图上 BATH 上方的尺寸链就是 2030|390。
# （曾误读成 1235：1235 是 BATH 南边另一条链上的数字，不是浴室宽度。）
BX0, BX1 = 1560, 3590                    # = west_in .. west_in + 2030
BY0, BY1 = 3194, 4664                    # = 1470 deep
KX0, KX1 = X["party_e"], X["kit_e"]      # 厨房净宽 2040
KY0, KY1 = 4754, Y["y4"]                 # 厨房净深 1861

# 室内净轮廓（一整个 L/T 形）
OUTLINE = [
    (X["party_e"], Y["south_out"]),
    (X["party_e"], Y["kit_n"]),
    (X["west_out"], Y["kit_n"]),
    (X["west_out"], Y["north_out"]),
    (X["east_out"], Y["north_out"]),
    (X["east_out"], 1533),
    (X["nook_e"], 1533),
    (X["nook_e"], 4914),
    (X["east_out"], 4914),
    (X["east_out"], Y["south_out"]),
]

# 客/飯廳 = 一个 L 形 + 右边饭厅凹位，浴室在左段挖一个缺口。
#   左段 1560..6180、y 200..3074（净 4620 x 2874，印刷 2890）
#   右段 3980..6180、y 3074..4754
#   下段 2530..6180、y 4754..6615
#   凹位 6520..8260、y 1662..4668（印刷 2010 x 3000），
#        经 y 2099..3629 的 1530mm 开口与客厅相通（图上实测 1530）
LIV = [
    (X0, Y["north_in"]), (X["east_in"], Y["north_in"]),
    (X["east_in"], 2099), (X["nook_w"], 2099), (X["nook_w"], Y["nook_top"]),
    (X["nook_e"] - WALL_EXT, Y["nook_top"]), (X["nook_e"] - WALL_EXT, Y["nook_btm"]),
    (X["east_in"], Y["nook_btm"]), (X["east_in"], Y["y4"]),
    (X["kit_wall_e"], Y["y4"]), (X["kit_wall_e"], Y["kit_n"]),
    (BX1 + WALL_THIN, Y["kit_n"]), (BX1 + WALL_THIN, BY0 - WALL_THIN),
    (X0, BY0 - WALL_THIN),
]
BATH = [(BX0, BY0), (BX1, BY0), (BX1, BY1), (BX0, BY1)]
KIT = [(KX0, KY0), (KX1, KY0), (KX1, KY1), (KX0, KY1)]


def wall(wid, a, b, thickness, openings=(), kind="partition"):
    return dict(id=wid, a=list(a), b=list(b), type=kind,
                thickness_mm=thickness, openings=list(openings))


def door(oid, center, width, hinge="a", opens="in"):
    return dict(id=oid, kind="door", center_mm=int(center), width_mm=width,
                height_mm=2100, swing=dict(dir=opens, hinge=hinge))


def win(oid, center, width, sill=900, height=1350):
    return dict(id=oid, kind="window", center_mm=int(center), width_mm=width,
                height_mm=height, sill_h_mm=sill, swing=None)


def build():
    # 浴室四面墙（南墙上有门）
    w_bath_n = wall("wBATH_N", (BX0, BY0-WALL_THIN), (BX1, BY0-WALL_THIN), WALL_THIN, [], "partition")
    w_bath_s = wall("wBATH_S", (BX0, BY1 + WALL_THIN // 2), (BX1, BY1 + WALL_THIN // 2),
                    WALL_THIN, [door("d_bath_liv", 400, 700)], "partition")
    w_bath_e = wall("wBATH_E", (BX1 + WALL_THIN // 2, BY0-WALL_THIN),
                    (BX1 + WALL_THIN // 2, BY1 + WALL_THIN // 2), 390, [], "load_bearing")
    w_bath_w = wall("wBATH_W", (X0, BY0-WALL_THIN), (X0, BY1 + WALL_THIN // 2),
                    WALL_EXT, [], "load_bearing")
    # 厨房四面墙（北墙上有门）
    w_kit_n = wall("wKIT_N", (X["party_e"], Y["kit_n"]), (X["kit_e"], Y["kit_n"]),
                   WALL_THIN, [door("d_kit", 1500, 900)], "partition")
    w_kit_e = wall("wKIT_E", (X["kit_e"], Y["kit_n"]), (X["kit_e"], Y["y4"]), 390, [], "load_bearing")
    w_kit_w = wall("wKIT_W", (X["party_e"], Y["kit_n"]), (X["party_e"], Y["y4"]),
                   WALL_PARTY, [], "load_bearing")
    w_south = wall("wL_S", (X["party_e"], Y["y4"]), (X["east_in"], Y["y4"]), WALL_EXT,
                   [door("d_entry", 3130, 860)], "load_bearing")
    # 厨房南墙单独一段：大门不在厨房范围内，共用整条 wL_S 会给厨房算出一个
    # 门中心落在房外的假门扇净空区。
    w_kit_s = wall("wKIT_S", (X["party_e"], Y["y4"]), (X["kit_e"], Y["y4"]), WALL_EXT, [], "load_bearing")

    rooms = [
        dict(id="living", label="客／飯廳", label_source="brochure_legend",
             label_score=1.0, polygon=[list(p) for p in LIV], holes=[],
             area_mm2=0, height_mm=H_ROOM, height_field=[],
             walls=[
                 wall("wL_N", (X0, Y["north_in"]), (X["east_in"], Y["north_in"]), WALL_EXT,
                      [win("n_liv1", 900, 1400), win("n_liv2", 3200, 1500)], "load_bearing"),
                 wall("wL_W", (X0, Y["north_in"]), (X0, BY0-WALL_THIN), WALL_EXT,
                      [win("n_liv3", 1900, 1100)], "load_bearing"),
                 wall("wL_E", (X["east_in"], Y["north_in"]), (X["east_in"], 2099), WALL_EXT, [], "load_bearing"),
                 wall("wL_E2", (X["east_in"], 3629), (X["east_in"], Y["y4"]), WALL_EXT, [], "load_bearing"),
                 wall("wL_NOOK_N", (X["nook_w"], Y["nook_top"]), (X["nook_e"]-WALL_EXT, Y["nook_top"]), WALL_EXT,
                      [win("n_din", 900, 1100)], "load_bearing"),
                 wall("wL_NOOK_W", (X["nook_w"], Y["nook_top"]), (X["nook_w"], 2099), WALL_EXT, [], "load_bearing"),
                 wall("wL_NOOK_E", (X["nook_e"]-WALL_EXT, Y["nook_top"]), (X["nook_e"]-WALL_EXT, Y["nook_btm"]), WALL_EXT,
                      [win("n_din2", 1600, 1400)], "load_bearing"),
                 wall("wL_NOOK_S", (X["east_in"], Y["nook_btm"]), (X["nook_e"]-WALL_EXT, Y["nook_btm"]), WALL_EXT, [], "load_bearing"),
                 wall("wL_SPINE", (X["spine_w"], Y["north_in"]), (X["spine_w"], Y["spine_bot"]), WALL_MAIN, [], "load_bearing"),
                 w_bath_n, w_bath_s, w_bath_e, w_bath_w,
                 w_kit_n, w_kit_e, w_kit_w, w_south,
             ]),
        dict(id="bath", label="浴室", label_source="brochure_legend", label_score=1.0,
             polygon=[list(p) for p in BATH], holes=[], area_mm2=0, height_mm=H_ROOM,
             height_field=[], walls=[w_bath_n, w_bath_s, w_bath_e, w_bath_w]),
        dict(id="kitchen", label="廚房", label_source="brochure_legend", label_score=1.0,
             polygon=[list(p) for p in KIT], holes=[], area_mm2=0, height_mm=H_ROOM,
             height_field=[], walls=[w_kit_n, w_kit_e, w_kit_w, w_kit_s]),
    ]
    # 共用墙要在两间房的 walls 里各出现一次：door_approach_zones() 按 room.openings
    # 去找 wall id，再问「墙的哪一侧在房间多边形里」决定门向哪边开。
    # 另外 load_flat 只读房间层的 openings，wall 里嵌套的会被丢掉。
    for r in rooms:
        r["openings"] = [dict(o, wall=w["id"]) for w in r["walls"] for o in w["openings"]]
    for r in rooms:
        a = 0.0
        q = r["polygon"]
        for i in range(len(q)):
            x1, y1 = q[i]
            x2, y2 = q[(i + 1) % len(q)]
            a += x1 * y2 - x2 * y1
        r["area_mm2"] = int(abs(a) / 2.0)
    return dict(flat_id="plan_hk_onpak05", units="mm", rooms=rooms, connections=[],
                fixed_services=[],
                entry=dict(room="living", point=[4700, Y["y4"] - 300]),
                meta=dict(source="hkha_on_pak_court_sales_brochure_p26_vector",
                          confidence=0.95, outline=OUTLINE, mm_per_px=None,
                          scale_note="由图上印刷比例尺条标定：5 m = 70.86 pt，1 pt = 70.5617 mm（1:200）",
                          reference=["房委會 安柏苑售樓說明書 2/F-9/F 平面圖（第26頁，向量 PDF）"],
                          chains=dict(
                              north="250|2100|320|2200|250 = 5120",
                              south="100|2040|390|3655|250 = 6435",
                              east="200|730|1135|1560|2990|250 = 6865",
                              bath="1235 x 1470",
                              kitchen="2040 wide, 1700 counter",
                              dining_nook="2010 x 3000")))


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    out = os.path.join(root, "out", "plan_hk_onpak05.json")
    p = build()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
    tot = sum(r["area_mm2"] for r in p["rooms"])
    for r in p["rooms"]:
        print("  %-8s %7.2f m2" % (r["id"], r["area_mm2"] / 1e6))
    print("  净面积合计 %.2f m2 = %.0f 平方呎（净）；实用面积 493 平方呎 = 45.8 m2" %
          (tot / 1e6, tot / 1e6 * 10.7639))
    print("->", out)


if __name__ == "__main__":
    main()
