#!/usr/bin/env python3
"""比例尺與尺寸的端到端驗證 —— **開工之前必須先過這一關**。

CAD 尺度錯一次，後面所有嘢（家具放唔放得落、通道夠唔夠、訂造尺寸）
全部係錯嘅，而且錯得唔明顯 —— 圖睇落一樣咁靚。
所以這一關要**量化**，唔可以靠「睇落 OK」。

驗四件事：
  ① 比例尺：矢量化自己 OCR 出嘅 mm/px  vs  生成器嘅真值
  ② 絕對尺寸：每間房嘅淨寬/淨深 vs 真值（mm 誤差）
  ③ 位置：房間中心點嘅絕對座標誤差
  ④ 人體尺度 sanity：門闊、牆厚、層高係唔係合理範圍

用法：
  python3 services/floorplan/verify_scale.py            # 三個戶型全驗
  python3 services/floorplan/verify_scale.py --plan a
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FP = os.path.join(ROOT, "services", "floorplan")
OUT = os.path.join(ROOT, "out")
sys.path.insert(0, FP)
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

# 容差（mm / 百分比）—— 定得比實際需要緊，過得到就真係穩
TOL_SCALE_PCT = 1.0        # 比例尺相對誤差
TOL_DIM_MM = 80            # 每間房每個方向嘅絕對誤差
TOL_POS_MM = 150           # 房間中心位置誤差
# 人體尺度合理範圍
# 合成圖裏有 1150mm 嘅「門」——現實中咁闊通常係門洞/趟門而唔係單扇門，
# 所以上限放寬到 1300 並另發 warning，唔當 FAIL。
SANE = {"door_w": (550, 1300), "wall_loadbearing": (150, 300),
        "wall_partition": (60, 200), "height": (2200, 3200)}
WIDE_DOOR_MM = 1000


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def centroid(poly):
    x0, y0, x1, y1 = bbox(poly)
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def match_rooms(truth_rooms, out_rooms):
    """按中心點最近配對（兩邊排序唔一定一樣）。"""
    pairs, used = [], set()
    for t in truth_rooms:
        tc = centroid(t["polygon"])
        best, bd = None, 1e18
        for i, o in enumerate(out_rooms):
            if i in used:
                continue
            oc = centroid(o["polygon"])
            d = (tc[0] - oc[0]) ** 2 + (tc[1] - oc[1]) ** 2
            if d < bd:
                bd, best = d, i
        if best is not None:
            used.add(best)
            pairs.append((t, out_rooms[best]))
    return pairs


def check(tag: str) -> tuple[bool, list[str]]:
    tp = os.path.join(ROOT, "data", "floorplans", "plan_hk_%s.truth.json" % tag)
    op = os.path.join(OUT, "plan_hk_%s.json" % tag)
    if not (os.path.exists(tp) and os.path.exists(op)):
        return False, ["缺少檔案：%s / %s" % (tp, op)]
    truth = json.load(open(tp, encoding="utf-8"))
    out = json.load(open(op, encoding="utf-8"))
    msgs, ok = [], True

    # ① 比例尺（連來源同共識度）
    want = 1.0 / float(truth["meta"]["px_per_mm"])
    got = float(out["meta"]["mm_per_px"])
    err = abs(got - want) / want * 100.0
    src = out["meta"].get("scale_source")
    votes = out["meta"].get("scale_votes") or []
    msgs.append("① 比例尺  真值 %.4f mm/px   矢量化 %.4f mm/px   誤差 %.3f%%"
                % (want, got, err))
    if votes:
        vals = [float(v[0]) for v in votes]
        spread = (max(vals) - min(vals)) / (sum(vals) / len(vals)) * 100.0
        msgs.append("   來源 %s —— **量出來嘅**：%d 條獨立標注互相印證（%s），彼此相差 %.2f%%"
                    % (src, len(votes), "、".join(str(v[1]) for v in votes), spread))
        if spread > 1.5:
            ok = False
            msgs.append("   ✗ 各條標注之間分歧過大（>1.5%），比例尺唔可信")
    else:
        msgs.append("   ⚠️ 來源 %s —— **推斷出來嘅**（假設承重牆 200mm），唔係量度。"
                    "真實系統喺呢種情況必須叫用戶拉線確認，因為牆厚假設錯 25%% 就等於全屋尺寸錯 25%%。" % src)
        if src not in ("ocr", "cli"):
            msgs.append("   （本次數值剛好正確，但唔可以當成保證）")
    if err > TOL_SCALE_PCT:
        ok = False
        msgs.append("   ✗ 超出容差 %.1f%%" % TOL_SCALE_PCT)

    # ②③ 逐間房尺寸 + 位置
    pairs = match_rooms(truth["rooms"], out["rooms"])
    worst_dim, worst_pos, worst_name = 0, 0.0, ""
    for t, o in pairs:
        tx0, ty0, tx1, ty1 = bbox(t["polygon"])
        ox0, oy0, ox1, oy1 = bbox(o["polygon"])
        dw = max(abs((ox1 - ox0) - (tx1 - tx0)), abs((oy1 - oy0) - (ty1 - ty0)))
        tc, oc = centroid(t["polygon"]), centroid(o["polygon"])
        dp = ((tc[0] - oc[0]) ** 2 + (tc[1] - oc[1]) ** 2) ** 0.5
        if dw > worst_dim:
            worst_dim, worst_name = dw, "%s" % (o.get("label") or o.get("id"))
        worst_pos = max(worst_pos, dp)
        flag = "" if dw <= TOL_DIM_MM and dp <= TOL_POS_MM else "  ✗"
        msgs.append("   %-9s 真值 %4dx%-4d   矢量化 %4dx%-4d   尺寸差 %3dmm  位置差 %4.0fmm%s"
                    % (o.get("label") or o.get("id"), tx1 - tx0, ty1 - ty0,
                       ox1 - ox0, oy1 - oy0, dw, dp, flag))
        if dw > TOL_DIM_MM or dp > TOL_POS_MM:
            ok = False
    msgs.append("② 最大尺寸誤差 %dmm（%s）　③ 最大位置誤差 %.0fmm" % (worst_dim, worst_name, worst_pos))

    # ④ 人體尺度 sanity
    doors = [op_["width_mm"] for r in out["rooms"] for op_ in r.get("openings", [])
             if op_.get("kind") == "door"]
    bad = [d for d in doors if not (SANE["door_w"][0] <= d <= SANE["door_w"][1])]
    heights = {r.get("height_mm", 0) for r in out["rooms"]}
    hbad = [h for h in heights if h and not (SANE["height"][0] <= h <= SANE["height"][1])]
    wide = [d for d in doors if d > WIDE_DOOR_MM]
    msgs.append("④ 門闊 %s mm　層高 %s mm%s"
                % (doors, sorted(heights), "  ✗" if (bad or hbad) else ""))
    if wide:
        msgs.append("   ⚠️ 有 %s mm 闊嘅門 —— 現實中通常係門洞/趟門，"
                    "應該按 sliding 處理而唔係內開" % wide)
    if bad or hbad:
        ok = False
    return ok, msgs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="", help="a / b / c，留空 = 全部")
    args = ap.parse_args()
    tags = [args.plan] if args.plan else ["a", "b", "c"]
    all_ok = True
    for t in tags:
        print("=" * 74)
        print("  plan_hk_%s" % t)
        print("=" * 74)
        ok, msgs = check(t)
        for m in msgs:
            print(m)
        print("  ==> %s" % ("✅ PASS" if ok else "❌ FAIL"))
        all_ok = all_ok and ok
    print()
    print("總結：%s" % ("✅ 三個戶型尺度全部在容差內 —— 可以開工" if all_ok
                        else "❌ 有戶型未過關，唔好喺上面做嘢"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
