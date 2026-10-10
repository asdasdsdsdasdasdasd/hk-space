"""LLM 大腦 —— 由 LLM 決定「幾時、用咩 brief 去調度求解器」。

架構（技術方案 §5 的下一層）：

    客戶需求（自然語言）
        ↓
    LLM 大腦 ──工具──▶ 確定性引擎
        │  inspect_flat      睇戶型（淨尺寸、每扇門嘅寬/開向/鉸鏈、窗、層高）
        │  list_catalog      查家具庫
        │  check_fit         試塞唔塞得入，塞唔入報差幾多 mm
        │  set_brief         ★ 定清單（要咩家具、上限尺寸、放邊間房）
        │  generate_design   ★ 調度求解器出方案
        │  inspect_room      睇某間房實際擺成點 + 有咩問題
        │  change_item       換款／搬房／唔要／加件／訂造
        │  finish            交貨（順便寫畀客戶睇嘅總結）
        ↓
    LLM 自己決定：夠唔夠好？要唔要換清單？要唔要重解？

同舊 pipeline 嘅分別：舊嘅係**引擎驅動**（求解 → 生成意見 → 問 LLM 打補丁），
LLM 只能喺既定方案上修修補補。新嘅係 **LLM 驅動**：清單係佢定嘅、
幾時調求解器係佢決定嘅、唔夠好可以整個 brief 重嚟。

鐵律仍然唔變：
  · LLM 唔輸出坐標（佢只講要咩、放邊間房；位置由求解器決定）
  · 可行性由確定性幾何裁決（check_fit / generate_design / change_item 都會駁回）
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .advisory import advise
from .house import assign_rooms, infer_room_functions, solve_house, verify_house
from .llm_advisor import Decision, apply_decisions, feasibility, _pick_smaller
from .livability import check_livability
from .types import Placement

DEFAULT_BUDGET = 44          # 最多幾次工具調用（實測 24 唔夠：淨係探路就燒晒）
DEFAULT_ROUNDS = 16          # 最多幾輪對話


def _tool(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


TOOLS = [
    _tool("inspect_flat", "睇戶型：每間房嘅淨尺寸、門（寬/開向/鉸鏈）、窗、層高、現有清單", {}, []),
    _tool("list_catalog", "查家具庫有咩款（品類名只可以用 inspect_flat 嘅 catalog_index 列出嘅）",
          {"category": {"type": "string"},
           "max_w": {"type": "integer"}, "max_d": {"type": "integer"},
           "limit": {"type": "integer"}}, []),
    _tool("check_fit", "試下某件家具（自己定尺寸）塞唔塞得入某間房，塞唔入會報差幾多 mm",
          {"category": {"type": "string"}, "w": {"type": "integer"},
           "d": {"type": "integer"}, "to_room": {"type": "string"}},
          ["category", "w", "d", "to_room"]),
    _tool("set_brief", "★ 定／改客戶嘅家具清單。之後要叫 generate_design 才會重新擺位。",
          {"items": {"type": "array", "items": {"type": "object", "properties": {
              "inst": {"type": "string"}, "category": {"type": "string"},
              "w_max": {"type": "integer"}, "d_max": {"type": "integer"},
              "h_min": {"type": "integer"}, "note": {"type": "string"}}}}},
          ["items"]),
    _tool("generate_design", "★ 調度求解器，按現時嘅清單重新擺一次（整屋）", {}, []),
    _tool("inspect_room", "睇某間房擺成點：每件家具嘅尺寸同位置、有咩宜居問題、有咩顧問意見",
          {"room": {"type": "string"}}, ["room"]),
    _tool("change_item", "改一件：op=shrink|move|drop|add|custom",
          {"op": {"type": "string"}, "inst": {"type": "string"},
           "category": {"type": "string"}, "to_room": {"type": "string"},
           "w": {"type": "integer"}, "d": {"type": "integer"}, "h": {"type": "integer"},
           "label": {"type": "string"}, "reason": {"type": "string"}}, ["op"]),
    _tool("finish", "交貨。summary 會直接畀客戶睇：講清楚做了咩、仲有咩取捨。",
          {"summary": {"type": "string"},
           "tradeoffs": {"type": "array", "items": {"type": "string"}}}, ["summary"]),
]

SYSTEM = """你是香港小型住宅（納米樓 / 上車盤）的室內設計師，同時係整個流程的大腦。

客戶會用自然語言講佢要咩。你有工具可以：
  1. inspect_flat 睇戶型（**每扇門嘅寬度、開向、鉸鏈都在裡面**）
  2. list_catalog 查家具庫；check_fit 試塞唔塞得入
  3. set_brief 定清單 —— 呢個係你嘅設計決定
  4. generate_design 叫求解器擺位
  5. inspect_room 睇結果，判斷好唔好
  6. change_item 換款／搬房／唔要／加件／訂造
  7. finish 交貨

工作順序（自己判斷，唔一定要跟死）：
  · **第一步一定係 inspect_flat** —— 佢會回傳成個家具庫嘅索引
    （品類名、有幾多款、尺寸範圍）同每間房嘅限制。
    ★ 唔好逐個品類名去 list_catalog 試 —— 索引已經喺手，
      品類名只可以用索引裡面出現過嘅，試唔存在嘅名係純浪費。
  · 再定 brief（唔好照抄客戶講嘅嘢 —— 客戶想要嘅嘢可能根本塞唔落，
    你嘅價值就係及早發現，並提出可行嘅替代：換細一號、換品類、訂造、或者唔要）
  · generate_design，然後 inspect_room 睇實際結果
  · 有問題就 change_item 或改 brief 再 generate_design
  · 覺得可以交貨就 finish

硬規則：
  · **唔准輸出坐標**。你講「要咩、放邊間房」，位置由求解器決定。
  · 可行性由引擎裁決。佢駁回你嘅動作時會講清楚差幾多 mm，照住改。
  · 固定潔具（馬桶/洗手盆/浴缸/淋浴/灶台/抽油煙機/廚櫃檯面）钉死在來去水位，
    唔可以搬房、唔可以唔要。
  · 庫存冇合尺寸嘅款，就用 change_item 的 custom 訂造（要自己算準 w/d/h）。
    香港嘅正解經常就係訂造傢俬 —— 唔好因為「IKEA 冇」就放棄。
  · **唔好整重複**：要換一件，就改佢（custom 之後原本嗰件要換走）或者
    一開始 set_brief 就用你想要嘅尺寸。唔好又加一件新嘅、又留低舊嗰件。
  · set_brief 係**整個清單換掉**（唔係追加）；我會自動幫你補返必需嘅
    床／馬桶／洗手盆／灶台，補咗會話你知。
  · 每次調用完工具，睇清楚結果再決定下一步。**唔好未睇結果就連環估。**
  · 預算有限（預設 24 次工具調用），唔好重複試同一個注定失敗嘅動作。"""


@dataclass
class AgentState:
    items: list = field(default_factory=list)
    assign: dict = field(default_factory=dict)
    house: object = None
    advisories: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    finished: bool = False
    summary: str = ""
    tradeoffs: list = field(default_factory=list)


class DesignAgent:
    def __init__(self, flat, catalog, client, brief_text: str = "",
                 budget: int = DEFAULT_BUDGET, rounds: int = DEFAULT_ROUNDS,
                 verbose: bool = True):
        self.flat = flat
        self.catalog = catalog
        self.client = client
        self.brief_text = brief_text
        self.budget = budget
        self.rounds = rounds
        self.verbose = verbose
        self.st = AgentState()

    # ---------------- 工具實作 ----------------
    def _room_facts(self) -> list:
        funcs = infer_room_functions(self.flat)
        out = []
        for r in self.flat.rooms:
            b = r.usable_geo().bounds
            doors = []
            for op in r.openings:
                if op.kind == "door":
                    sw = op.swing or {}
                    doors.append({"width_mm": op.width_mm,
                                  "swing": sw.get("dir") or "in",
                                  "hinge": sw.get("hinge") or "left"})
            out.append({
                "room": r.id, "label": r.label, "func": funcs.get(r.id, "?"),
                "area_m2": round(r.usable_geo().area / 1e6, 2),
                "net_w_mm": int(b[2] - b[0]), "net_d_mm": int(b[3] - b[1]),
                "height_mm": getattr(r, "height_mm", 0),
                "doors": doors,
                "n_windows": sum(1 for o in r.openings if o.kind == "window"),
            })
        return out

    def _catalog_index(self) -> list:
        """整個家具庫嘅**索引** —— 一個品類一行。

        ★ 唔可以叫 LLM 逐個品類名去試：佢會試 "tv"、"mattress"、
          "nightstand" 呢啲根本唔存在嘅名，白白燒掉一半預算
          （實測第一次跑 24 次調用全部花喺 list_catalog，一次都沒去到設計）。
          所以開場就俾佢成個索引：品類、有幾多款、尺寸範圍。
        """
        agg: dict = {}
        fixed = set()
        for s in self.catalog:
            c = s["category"]
            a = agg.setdefault(c, {"n": 0, "min_w": 10 ** 9, "min_d": 10 ** 9,
                                   "max_w": 0, "max_d": 0})
            w, d = s["dims"]["w"], s["dims"]["d"]
            a["n"] += 1
            a["min_w"] = min(a["min_w"], w); a["max_w"] = max(a["max_w"], w)
            a["min_d"] = min(a["min_d"], d); a["max_d"] = max(a["max_d"], d)
            if str(s.get("id", "")).startswith("fix-"):
                fixed.add(c)
        return [{"category": c, "n": a["n"], "min": "%dx%d" % (a["min_w"], a["min_d"]),
                 "max": "%dx%d" % (a["max_w"], a["max_d"]),
                 "fixed": c in fixed}
                for c, a in sorted(agg.items())]

    def t_inspect_flat(self, **_):
        return {"flat": self.flat.id, "rooms": self._room_facts(),
                "catalog_index": self._catalog_index(),
                "brief": [{"inst": p.inst, "cat": p.category,
                           "w": p.w, "d": p.d, "h": p.h} for p in self.st.items],
                "has_design": self.st.house is not None}

    def t_list_catalog(self, category=None, max_w=None, max_d=None, limit=12, **_):
        rows = []
        for s in self.catalog:
            if category and s["category"] != category:
                continue
            w, d = s["dims"]["w"], s["dims"]["d"]
            if max_w and not (w <= max_w or d <= max_w):
                continue
            if max_d and not (d <= max_d or w <= max_d):
                continue
            rows.append({"sku": s["id"], "cat": s["category"], "name": s.get("name_zh", ""),
                         "w": w, "d": d, "h": s["dims"]["h"],
                         "price": s.get("price_hkd", 0)})
        rows.sort(key=lambda r: r["w"] * r["d"])
        return {"n": len(rows), "items": rows[:int(limit or 12)]}

    def t_check_fit(self, category, w, d, to_room, **_):
        try:
            r = self.flat.room(to_room)
        except Exception:
            return {"error": "冇房間 %s（可用：%s）"
                             % (to_room, "、".join(x.id for x in self.flat.rooms))}
        import shapely
        b = r.usable_geo().bounds
        rw, rd = int(b[2] - b[0]), int(b[3] - b[1])
        fits = (w <= rw and d <= rd) or (w <= rd and d <= rw)
        res = {"fits": fits, "room_net_w": rw, "room_net_d": rd, "item": "%dx%d" % (w, d)}
        if not fits:
            res["shortfall_mm"] = int(min(max(0, w - rw), max(0, d - rd))
                                      or min(max(0, w - rd), max(0, d - rw)))
            res["hint"] = "試下調轉擺（w<->d）或者縮細"
        if self.st.house is not None:
            lay = self.st.house.by_room.get(to_room)
            if lay is not None:
                res["room_currently_has"] = [p.inst for p in lay.placements]
        return res

    def _ensure_mandatory(self, items: list) -> list:
        """守住「住得成人」嘅底線：浴室必有馬桶+洗手盆、廚房必有灶台、睡房必有床。

        ★ 呢條唔係 LLM 嘅決定。實測 LLM 試過 set_brief 漏咗成個浴室潔具
          （出咗一間冇廁所嘅屋），亦試過為咗塞灶台而 drop 咗雪櫃。
          清單係佢嘅設計空間，但「有廁所沖涼煮飯」係產品底線。
        """
        from demo_house import pick_sku
        have = {p.category for p in items}
        added = []
        funcs = infer_room_functions(self.flat)
        need = []
        for r in self.flat.rooms:
            f = funcs.get(r.id, "?")
            if f == "bath":
                need += [("toilet", 500, 800), ("basin", 700, 600)]
            elif f == "kitchen":
                need += [("stove", 700, 600)]
            elif f == "bedroom":
                need += [("bed", 2200, 2200)]
        for cat, mw, md in need:
            if cat in have:
                continue
            s = pick_sku(self.catalog, cat, mw, md, "light")
            if s is None:
                continue
            items.append(Placement(inst="#%s" % cat, sku=s["id"], category=cat,
                                   w=s["dims"]["w"], d=s["dims"]["d"], h=s["dims"]["h"],
                                   visual=dict(s["visual"]),
                                   price_hkd=s.get("price_hkd", 0)))
            have.add(cat)
            added.append(cat)
        self.st.auto_added = added
        return items

    def t_set_brief(self, items=None, **_):
        from demo_house import pick_sku
        if not items:
            return {"error": "set_brief 要一個 items 陣列，"
                             "每項 {inst, category, w_max, d_max}"}
        new, skipped = [], []
        for it in items or []:
            cat = str(it.get("category") or "").strip()
            inst = str(it.get("inst") or ("#%s" % cat)).strip()
            if not inst.startswith("#"):
                inst = "#" + inst
            mw = int(it.get("w_max") or 2000)
            md = int(it.get("d_max") or 2000)
            s = pick_sku(self.catalog, cat, mw, md, "light")
            if s is None:
                skipped.append({"inst": inst, "cat": cat,
                                "why": "庫存冇 %dx%d 以內嘅 %s" % (mw, md, cat)})
                continue
            new.append(Placement(inst=inst, sku=s["id"], category=s["category"],
                                 w=s["dims"]["w"], d=s["dims"]["d"], h=s["dims"]["h"],
                                 visual=dict(s["visual"]),
                                 price_hkd=s.get("price_hkd", 0)))
        # ★ 硬性功能需求：一間屋唔可以冇廁所、冇灶台、冇床。
        #   引擎負責守住這條底線（唔係 LLM 話點就點）——
        #   實測 LLM 有一次 set_brief 把馬桶/洗手盆/淋浴全部漏掉，
        #   出咗一個「冇廁所嘅屋」。清單係 LLM 嘅設計決定，
        #   但「住得成人」係產品底線，唔可以談。
        self.st.items = self._ensure_mandatory(new)
        self.st.assign, self.st.house = {}, None
        return {"ok": True, "brief_size": len(new),
                "items": [{"inst": p.inst, "cat": p.category,
                           "w": p.w, "d": p.d} for p in new],
                "skipped": skipped,
                "added_mandatory": getattr(self.st, "auto_added", []),
                "note": ("清單已更新。叫 generate_design 才會擺位。"
                         + ("★ 我幫你補返咗必需嘅 %s —— 一間屋唔可以冇廁所/灶台/床。"
                            % "、".join(getattr(self.st, "auto_added", []))
                            if getattr(self.st, "auto_added", []) else ""))}

    def t_generate_design(self, **_):
        if not self.st.items:
            return {"error": "清單係空嘅 —— 先 set_brief"}
        assign, _ = assign_rooms(self.flat, self.st.items, verbose=False)
        house = solve_house(self.flat, self.st.items, time_limit_s=40.0,
                            max_workers=5, parallel=True, verbose=False)
        house.assignment = assign
        self.st.assign, self.st.house = assign, house
        rep = verify_house(self.flat, house)
        self.st.advisories = advise(self.flat, house,
                                    self._want_by_room(), unassigned=None)
        rooms = []
        for r in self.flat.rooms:
            lay = house.by_room.get(r.id)
            lv = check_livability(r, lay) if (lay and lay.placements) else None
            rooms.append({
                "room": r.id, "label": r.label,
                "placed": [p.inst for p in (lay.placements if lay else [])],
                "hard": len(lv.hard) if lv else 0,
                "soft": len(lv.soft) if lv else 0,
                "score": (None if house.scores.get(r.id) != house.scores.get(r.id)
                          else round(house.scores.get(r.id) or 0, 1)),
            })
        got = {p.inst for lay in house.by_room.values() for p in lay.placements}
        unplaced = [p.inst for p in self.st.items if p.inst not in got]
        return {"ok": True, "rooms": rooms,
                "placed": rep.n_placed, "requested": len(self.st.items),
                "unplaced": unplaced,
                "house_visual_load": round(rep.house_visual_load, 4),
                "advisories": [{"room": a.room_label, "headline": a.headline,
                                "subjects": a.subjects,
                                "options": a.options[:3]}
                               for a in self.st.advisories[:10]],
                "n_advisories": len(self.st.advisories)}

    def _want_by_room(self) -> dict:
        out: dict = {}
        for p in self.st.items:
            rid = self.st.assign.get(p.inst)
            if rid:
                out.setdefault(rid, []).append(p)
        return out

    def t_inspect_room(self, room, **_):
        if self.st.house is None:
            return {"error": "仲未有設計 —— 先 generate_design"}
        try:
            r = self.flat.room(room)
        except Exception:
            return {"error": "冇房間 %s" % room}
        lay = self.st.house.by_room.get(room)
        b = r.usable_geo().bounds
        res = {"room": room, "net_w_mm": int(b[2] - b[0]), "net_d_mm": int(b[3] - b[1]),
               "doors": [{"width_mm": o.width_mm, "swing": (o.swing or {}).get("dir", "in")}
                         for o in r.openings if o.kind == "door"]}
        if lay is None or not lay.placements:
            res["placements"] = []
            res["note"] = "呢間房冇嘢"
            return res
        lv = check_livability(r, lay)
        res["placements"] = [{"inst": p.inst, "cat": p.category, "w": p.w, "d": p.d,
                              "h": p.h, "x": p.x, "y": p.y, "rot": p.rot}
                             for p in lay.placements]
        res["violations"] = [{"rule": v.rule, "sev": v.severity,
                              "subjects": v.subjects, "detail": v.detail}
                             for v in (lv.hard + lv.soft)]
        res["advisories"] = [{"headline": a.headline, "subjects": a.subjects,
                              "options": a.options[:3]}
                             for a in self.st.advisories if a.room == room]
        res["feasible"] = {}
        for a in self.st.advisories:
            if a.room == room:
                res["feasible"][a.headline] = feasibility(
                    a, self.catalog, {p.inst: p for p in self.st.items},
                    self.flat, self.st.house)
        return res

    def t_change_item(self, op=None, inst=None, category=None, to_room=None,
                      w=None, d=None, h=None, label=None, reason="", **_):
        # ★ op 一定要有默認值。實測 LLM 會漏咗 op 直接俾尺寸
        #   （佢以為「有 w/d/h 就係訂造」），而簽名寫成必填就會拋
        #   TypeError 直接炸穿成個 agent —— 工具層永遠唔應該因為
        #   參數唔齊而崩，要回一句佢睇得明嘅錯誤。
        if not op:
            op = "custom" if (w and d) else ""
        if op not in ("shrink", "move", "drop", "add", "custom"):
            return {"error": "op 只可以係 shrink|move|drop|add|custom"
                             "（訂造要同時俾 w 同 d）"}
        if self.st.house is None and op in ("shrink", "move", "drop"):
            return {"error": "仲未有設計 —— 先 generate_design"}
        # ★ custom 對**已經喺清單裡面**嘅嘢＝就地把佢換成訂造尺寸，
        #   唔係加一件新嘅。實測 LLM 會「訂造一件新床 + 留低舊床」，
        #   然後想 drop 舊嘅（drop bed 被主體家具守衛擋、drop 淋浴被
        #   固定件守衛擋）—— 白白燒咗 6 次調用，而個設計係重複嘅。
        #   工具語義寫清楚，佢就唔會走錯路。
        if op == "custom" and inst and any(p.inst == inst for p in self.st.items):
            cur = next(p for p in self.st.items if p.inst == inst)
            if not (w and d):
                return {"error": "custom 要俾齊 w 同 d"}
            old = "%dx%d" % (cur.w, cur.d)
            cur.w, cur.d = int(w), int(d)
            if h:
                cur.h = int(h)
            cur.sku = "custom-%s" % cur.category
            if to_room:
                self.st.assign[cur.inst] = to_room
            self.t_generate_design()
            return {"notes": ["[resize] %s %s -> %dx%dx%d（訂造，就地換）"
                              % (inst, old, cur.w, cur.d, cur.h)],
                    "applied": True, "brief_size": len(self.st.items)}
        if op in ("add", "custom"):
            cat = str(category or "").strip()
            p = Placement(inst="#%s" % (label or ("%s_new" % cat)), sku="custom-%s" % cat,
                          category=cat, w=int(w or 0), d=int(d or 0), h=int(h or 0),
                          visual=dict(lightness_L=75, opacity=0.55,
                                      color_family="neutral", visual_mass_index=0.12))
            dec = Decision(action="define_custom", target=p.inst,
                           params={"category": cat, "to_room": to_room,
                                   "w": p.w, "d": p.d, "h": p.h, "label": label or ""},
                           reason=reason)
        else:
            amap = {"shrink": "shrink_sku", "move": "move_item",
                    "drop": "drop_item", "custom": "define_custom"}
            act = amap.get(op)
            if act is None:
                return {"error": "op 只可以係 shrink|move|drop|add|custom"}
            params = {"inst": inst}
            if to_room:
                params["to_room"] = to_room
            if not inst:
                return {"error": "%s 要指定 inst" % op}
            dec = Decision(action=act, target=inst, params=params, reason=reason)
        items, assign, acts, patches, notes = apply_decisions(
            self.st.items, self.st.assign, [dec], self.catalog,
            flat=self.flat, house=None)
        # drop 之後要補返必需件（LLM 為咗塞灶台 drop 咗雪櫃、又或者
        # 想 drop 走唯一一張床 —— 底線要每次都重新守住）
        before = {p.category for p in items}
        items = self._ensure_mandatory(items)
        back = [c for c in getattr(self.st, "auto_added", []) if c not in before]
        self.st.items, self.st.assign = items, assign
        if acts or back:
            self.t_generate_design()
        res = {"notes": notes, "applied": bool(acts or back),
               "brief_size": len(items)}
        if back:
            res["auto_added_mandatory"] = back
            res["note"] = ("★ %s 係底線件，唔可以冇 —— 我幫你補返。"
                           % "、".join(back))
        return res

    def t_finish(self, summary, tradeoffs=None, **_):
        self.st.finished = True
        self.st.summary = str(summary or "")
        self.st.tradeoffs = list(tradeoffs or [])
        return {"ok": True, "note": "收到，收工。"}

    # ---------------- agent loop ----------------
    def run(self) -> AgentState:
        msgs = [{"role": "system", "content": SYSTEM}]
        user = "客戶需求：\n%s\n\n（戶型：%s，%d 間房）\n請開工。"
        user = user % (self.brief_text or "（冇特別講，你自己按香港小戶型嘅常見需要決定）",
                       self.flat.id, len(self.flat.rooms))
        msgs.append({"role": "user", "content": user})
        nudged = False
        for rnd in range(self.rounds):
            if self.st.finished or len(self.st.calls) >= self.budget:
                break
            # ★ 預算將盡就催佢交貨。冇呢一步，agent 會一路做到彈盡糧絕 ——
            #   實測跑咗 34 次調用、20 分鐘，最後一次都冇 finish，
            #   客戶攞到嘅係一份冇總結、冇取捨說明嘅設計。
            if not nudged and len(self.st.calls) >= int(self.budget * 0.75):
                nudged = True
                msgs.append({"role": "user", "content":
                             "（系統提示）預算剩 %d 次工具調用。請收尾："
                             "如果而家嘅方案可以接受就立即 finish；"
                             "如果仲有致命問題，優先處理最嚴重嗰件，然後 finish。"
                             % (self.budget - len(self.st.calls))})
            try:
                msg = self.client.chat_tools(msgs, TOOLS)
            except Exception as e:
                if self.verbose:
                    print("   [agent] LLM 出錯：%s" % e)
                break
            calls = msg.get("tool_calls") or []
            msgs.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
            if not calls:
                if self.verbose:
                    print("   [agent] 模型冇叫工具，停：%s"
                          % (msg.get("content") or "")[:160])
                break
            for c in calls:
                fn = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except Exception:
                    args = {}
                handler = getattr(self, "t_" + fn, None)
                # ★ 工具層永遠唔可以拋異常。LLM 漏參數／傳錯型別係常態
                #   （實測：漏咗 set_brief 嘅 items、漏咗 change_item 嘅 op），
                #   任何一個 TypeError 都會炸穿成個 agent loop，
                #   而客戶只會見到「跑到一半冇咗」。要回一句佢睇得明嘅錯誤。
                try:
                    out = handler(**args) if handler else {"error": "冇呢個工具 %s" % fn}
                except TypeError as e:
                    out = {"error": "參數唔啱：%s。請照住工具 schema 再傳一次。" % e}
                except Exception as e:
                    out = {"error": "%s 執行失敗：%r" % (fn, e)}
                self.st.calls.append({"n": len(self.st.calls) + 1, "tool": fn, "args": args})
                if self.verbose:
                    print("   [agent %2d] %-16s %s -> %s"
                          % (len(self.st.calls), fn,
                             json.dumps(args, ensure_ascii=False)[:70],
                             json.dumps(out, ensure_ascii=False)[:110]))
                msgs.append({"role": "tool", "tool_call_id": c.get("id", ""),
                             "content": json.dumps(out, ensure_ascii=False)[:14000]})
        # ★ 強制收尾：無論如何都要有一次「交貨」。
        #   實測 agent 跑 39 次調用、38 分鐘，最後都冇 finish ——
        #   客戶攞到嘅係一份冇總結、冇取捨說明嘅圖，等於白做。
        #   所以時間到就用 tool_choice 逼佢只可以叫 finish。
        if not self.st.finished and self.st.house is not None:
            msgs.append({"role": "user", "content":
                         "（系統）時間到了。請立即呼叫 finish：用粵語書面語寫一段"
                         "畀客戶睇嘅總結，講清楚（1）做咗咩決定（2）仲有咩取捨／"
                         "要客戶決定嘅事。唔需要再叫其他工具。"})
            try:
                # ★ 唔可以用 tool_choice 逼佢叫 finish：DeepSeek 開住思考模式時
                #   直接回 400「Thinking mode does not support this tool_choice」。
                #   改成唔帶工具、用純文字要一段總結 —— 效果一樣，而且穩。
                txt = self.client.chat(
                    "你用粵語書面語寫室內設計方案嘅交貨總結。",
                    "以下係你嘅工作記錄（工具調用）：\n%s\n\n"
                    "請寫一段畀客戶睇嘅總結，講清楚："
                    "（1）你做咗咩決定、為咩（2）仲有咩取捨或者要客戶自己決定嘅事。"
                    "唔要客套話，直接講重點。"
                    % json.dumps(self.st.calls[-14:], ensure_ascii=False)[:4000],
                    temperature=0.3, max_tokens=4000)
                if txt:
                    self.st.summary = txt.strip()
                    self.st.finished = True
            except Exception as e:
                if self.verbose:
                    print("   [agent] 強制收尾失敗：%s" % e)
                self.st.summary = self.st.summary or "（未能產生總結）"
        return self.st
