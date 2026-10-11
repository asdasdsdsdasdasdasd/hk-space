"""Farnsworth House (Mies van der Rohe, 1951) —— 由 HABS 測繪圖還原成 plan JSON。

為什麼用這棟樓做測試（用戶 2026-10 指示：唔准再用自己生成嘅戶型圖）
  1. 它是**單一開放空間 + 一個浮在中央的服務核心**，四周全部相通 —— 正是
     「曝光度／等視域」指標唯一有意義的幾何，而且係我自己絕對畫唔出嘅形狀。
  2. 所有尺寸都有公開一手來源，可以逐條核對，唔使靠估。
  3. National Trust 展覽「Edith Farnsworth Reconsidered」把 c.1955 嘅實際陳設
     畫成一張有比例、有 A-Z 字母嘅平面圖 —— 可以做「同真人比」嘅對照組。

一手來源
  [A] HABS No. IL-1105 written data (Susan S. Benjamin, NPS, 1974) —— 英寸尺寸內文
  [B] HABS IL-1105 sheet 3 of 8 "PLAN"（Elizabeth Milnarik, 2009）—— 總平面 + 尺寸鏈
  [C] HABS IL-1105 sheet 6 of 8 "PLAN OF CORE" —— 服務核心大樣
  [D] National Trust "Edith Farnsworth Reconsidered" Exhibition Plan（2019-20）
      —— c.1955 陳設對照組，見 data/targets/farnsworth_1955.json

比例尺（先確認再開工 —— 用戶要求）
  [B] 上量得：室內進深 1434 px，標註 28'-8"  → 1434/28.667 = 50.02 px/ft
            地面水磨石格 137.5 px，大樣 [C] 標 2'-9" → 137.5/2.75 = 50.0 px/ft
  兩條獨立證據都指向 50.0 px/ft → 1 ft = 304.8 mm → 6.096 mm/px。
  由此量得室內淨長 2759 px = 16817 mm，對 [A] 嘅 55'-3" = 16840 mm 差 23mm(0.14%)。

服務核心（本案最關鍵的一個數）
  [C] 直接印住 24'-7 1/2" x 12'-6 1/4" = 7506 x 3816 mm。
  ⚠️ 我自己喺 [B] 上量到嘅進深只有 3371mm（量到嘅南面係核心南側櫃身，唔係核心
     最外緣）。兩個讀數差 445mm，最後採用 [C] 嘅印刷尺寸 —— 印刷尺寸係一手，
     而且同 [A] 嘅文字、同 55'-3" 總長互相對得上。
  位置：[B]/[D] 兩邊都量到核心西面距離西玻璃約 5.47m、東面距離東玻璃約 3.87m。
"""
from __future__ import annotations

import json
import os

MM_PER_FT = 304.8
PX_PER_FT = 50.0                      # 兩條獨立證據交叉驗證（見檔頭）
MM_PER_PX = MM_PER_FT / PX_PER_FT     # 6.096

# --- 由 [B] 量到的像素（原圖 7200x4800） ---
INT_X0, INT_X1 = 3085.0, 5844.0       # 室內西牆 / 東牆 內面
INT_Y0, INT_Y1 = 899.0, 2319.0        # 室內北牆 / 南牆 內面
DOOR_Y0, DOOR_Y1 = 1468.0, 1813.0     # 西牆上嘅入口雙扇門

WALL_MM = 100.0                       # 玻璃幕牆（框 + 玻璃 + 泛水）
CORE_W, CORE_D = 7506, 3816           # [C] 印刷尺寸 24'-7 1/2" x 12'-6 1/4"
CORE_X0 = 5440                        # 西面距西玻璃內面（[B]/[D] 量度）
CORE_Y0 = 1200                        # 北面距北玻璃內面（[C] 1243 - 半個幕牆）


def px_to_mm(v: float, origin: float) -> int:
    return int(round((v - origin) * MM_PER_PX))


def build() -> dict:
    d_mm = px_to_mm(INT_Y1, INT_Y0)                   # 量得 8656 = 28'-4 3/4"
    W = 16840                                          # 55'-3"（[A]/[B]）
    D = 8656
    cx0, cy0 = CORE_X0, CORE_Y0
    cx1, cy1 = cx0 + CORE_W, cy0 + CORE_D
    door_a = px_to_mm(DOOR_Y0, INT_Y0)
    door_b = px_to_mm(DOOR_Y1, INT_Y0)

    X0, Y0 = -WALL_MM, -WALL_MM
    X1, Y1 = W + WALL_MM, D + WALL_MM

    walls = [
        {"id": "wW", "a": [X0, Y0], "b": [X0, Y1], "type": "glazing", "thickness_mm": 100},
        {"id": "wE", "a": [X1, Y0], "b": [X1, Y1], "type": "glazing", "thickness_mm": 100},
        {"id": "wN", "a": [X0, Y0], "b": [X1, Y0], "type": "glazing", "thickness_mm": 100},
        {"id": "wS", "a": [X0, Y1], "b": [X1, Y1], "type": "glazing", "thickness_mm": 100},
    ]
    openings = [
        # [A]: 「There is one door on the west side, between the porch and the house」
        # [B]/[D] 都畫成向露台（西）外開嘅雙扇玻璃門，量到 2080-2100 寬
        {"id": "d0", "wall": "wW", "kind": "door",
         "center_mm": int(round((door_a + door_b) / 2.0)),
         "width_mm": 2080,
         "swing": {"dir": "out", "hinge": "left"}},
        # 四面都係玻璃幕牆 —— 全部當窗（渲染 + 「看得到窗」用）
        {"id": "nN", "wall": "wN", "kind": "window", "center_mm": W // 2,
         "width_mm": W - 600, "swing": None},
        {"id": "nS", "wall": "wS", "kind": "window", "center_mm": W // 2,
         "width_mm": W - 600, "swing": None},
        # [A]: 東側兩扇可開小窗，每扇 4'-9" x 2'-6"
        {"id": "nE1", "wall": "wE", "kind": "window", "center_mm": int(D * 0.30),
         "width_mm": 1448, "swing": None},
        {"id": "nE2", "wall": "wE", "kind": "window", "center_mm": int(D * 0.70),
         "width_mm": 1448, "swing": None},
    ]

    room = {
        "id": "living",
        "label": "開放起居空間",
        "label_source": "habs",
        "label_score": 1.0,
        "polygon": [[X0, Y0], [X1, Y0], [X1, Y1], [X0, Y1]],
        # ★ 拆唔走嘅服務核心：廚房檯面 + 客廁 + 機電核心 + 主廁 + 壁爐 + 南側櫃
        "holes": [[[cx0, cy0], [cx1, cy0], [cx1, cy1], [cx0, cy1]]],
        "area_mm2": (X1 - X0) * (Y1 - Y0) - CORE_W * CORE_D,
        "height_mm": 2870,                 # floor EL 2'-4" -> ceiling EL 11'-9" [C]
        "height_field": [],
        "walls": walls,
        "openings": openings,
    }

    plan = {
        "flat_id": "plan_farnsworth",
        "units": "mm",
        "rooms": [room],
        "connections": [],
        "fixed_services": [],
        "entry": {"room": "living",
                  "point": [400, int(round((door_a + door_b) / 2.0))]},
        "meta": {
            "source": "habs_measured_drawing",
            "confidence": 0.95,
            "mm_per_px": round(MM_PER_PX, 4),
            "scale_source": "dimension-chain",
            "scale_method": ("two independent chains: 28ft-8in over 1434 px, and the "
                             "2ft-9in travertine floor grid over 137.5 px"),
            "scale_votes": [[50.02, 8738, 1434, "28'-8\""], [50.0, 838, 137.5, "2'-9\""]],
            "reference": ["HABS No. IL-1105 written data [A]",
                          "HABS IL-1105 sheet 3 of 8 PLAN [B]",
                          "HABS IL-1105 sheet 6 of 8 PLAN OF CORE [C]",
                          "National Trust 'Edith Farnsworth Reconsidered' Exhibition Plan [D]"],
            "interior_mm": [W, D],
            "core_rect_mm": [cx0, cy0, cx1, cy1],
            "porch_mm": [6790, D],
            "known_conflict": ("core depth: printed on [C] as 7506x3816, but measured off [B] "
                               "as 7406x3371 (my [B] reading took the south cabinet face). "
                               "Using the printed [C] value."),
        },
    }
    return plan


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    out = os.path.join(root, "out", "plan_farnsworth.json")
    plan = build()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    m = plan["meta"]
    iw, idp = m["interior_mm"]
    cx0, cy0, cx1, cy1 = m["core_rect_mm"]
    o = plan["rooms"][0]["openings"][0]
    print("=== Farnsworth House —— 尺度核對 ===")
    print("比例尺      %.4f mm/px  (50.0 px/ft，兩條獨立尺寸鏈互證)" % m["mm_per_px"])
    print("室內        %.0f x %.0f mm = %.2f m2  (55'-3\" x 28'-4 3/4\")"
          % (iw, idp, iw * idp / 1e6))
    print("服務核心    %d x %d mm = %.2f m2   西距 %d, 北距 %d"
          % (cx1 - cx0, cy1 - cy0, (cx1 - cx0) * (cy1 - cy0) / 1e6, cx0, cy0))
    print("   北側通道 %d mm   南側通道 %d mm   東側 %.0f mm"
          % (cy0, idp - cy1, iw - cx1))
    print("淨可用面積  %.2f m2" % ((iw * idp - (cx1 - cx0) * (cy1 - cy0)) / 1e6))
    print("入口門      %d mm 寬，中心距北牆 %d mm，向外開" % (o["width_mm"], o["center_mm"]))
    print("層高        %d mm  (floor EL 2'-4\" -> ceiling EL 11'-9\")" % plan["rooms"][0]["height_mm"])
    print("->", out)


if __name__ == "__main__":
    main()
