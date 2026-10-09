"""装不下 / 摆不好时的「顾问意见」—— 技术方案 v3.2 §8 审阅层的位置。

★ 这是 **LLM 应该下场的地方**。

求解器只会算，不会说话。它给出的结果只有两种状态：
「摆下了」和「没摆下」。但用户需要知道的是：
  · 哪一件放不下？
  · 差多少？（不能只说"放不下"，要说"差 570mm"）
  · 我有几个选择？各自代价是什么？

所以这一层做两件事：
  ① 把几何结果翻译成**结构化的、带量化差距的**问题清单（本模块上半部分）
  ② 把清单渲染成给客户看的话（下半部分 —— 生产环境应该由 LLM 写，
     这里先用模板，模板里的每条 fact 就是喂给 LLM 的输入）

关键原则（方案 §8）：
  **永不返回「无解」。** 永远告诉用户差多少、以及能怎么办。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import Point

from .greedy import door_approach_zones
from .livability import check_livability
from .types import Layout, Placement, Room, front_dir

from .types import FRONT_CLEARANCE

CLEARANCE = FRONT_CLEARANCE

# 每條硬傷規則 → 給客戶的具體選項。上面已經專門處理過的規則不在這裏。
RULE_OPTIONS = {
    # ★ 門被擋住的三個選項，由便宜到貴：
    #   換門 < 改擺位 < 換家具。香港小戶型裡「把內開門改成推拉門」
    #   往往是唯一可行的一招（拆牆唔准，房又唔夠深）。
    "door_blocked": ["把內開門改成推拉門（最有效，唔使改牆）",
                     "把擋住門的家具挪開／換薄款",
                     "門改成外開"],
    "tv_faces_sofa": ["把電視轉向沙發", "電視改用掛牆"],
    "tv_too_close": ["換細一號電視櫃", "把沙發拉開"],
    "bed_head_not_against_wall": ["床頭推到牆邊", "換細一號床"],
    "not_backed_to_wall": ["推到牆邊", "換個位置"],
    "no_clearance_front": ["換個位置", "換薄一點的款式"],
    "clearance_blocked": ["把擋住的那件挪開", "換薄一點的款式"],
    "coffee_not_in_front": ["茶几移到沙發正前方", "唔要茶几"],
}


@dataclass
class Advice:
    room: str
    room_label: str
    kind: str              # wont_fit | blocked | unreachable | tight
    severity: str          # high | medium
    subjects: list[str] = field(default_factory=list)
    headline: str = ""
    detail: str = ""
    shortfall_mm: int = 0
    options: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)     # ★ 喂给 LLM 的结构化事实


def front_clearance(room: Room, layout: Layout, item: Placement,
                    others: list[Placement] | None = None) -> tuple[int, str]:
    """家具正前方**实际**留了多少 mm，以及被谁挡住（'' = 没被挡）。"""
    poly = room.usable_geo()
    others = [q for q in (others or layout.placements) if q.inst != item.inst]
    cx, cy = item.center()
    fx, fy = front_dir(item.rot)
    # ★ 正面軸向的半寬**永遠是 d/2**：模板約定 +Y 是正面，
    #   所以深度 d 就是沿正面方向的那條邊（rot 0/180 不換軸、
    #   rot 90/270 換軸後正面軸上仍是 d）。之前寫成 fy==0 時用 w，
    #   對 rot 270 的書桌會把起點多推 300mm 推到房外，
    #   於是「直望牆」被誤判成「前方 1600mm 全空」。
    half_front = item.d / 2.0
    start = Point(cx + fx * half_front, cy + fy * half_front)
    reach = 1600.0
    if not poly.buffer(1.0).contains(start):
        return 0, ""          # 正面已經頂住牆/在房外，沒有淨空可言
    ray = shapely.LineString([(start.x, start.y),
                              (start.x + fx * reach, start.y + fy * reach)])
    d_wall = reach
    hit = ray.intersection(poly.boundary)
    if not hit.is_empty:
        d_wall = hit.distance(start)
    blocker, d_obs, best = "", d_wall, None
    for q in others:
        if ray.intersects(q.footprint()):
            seg = ray.intersection(q.footprint())
            dd = seg.distance(start)
            if dd < d_obs:
                d_obs, blocker = dd, q.inst
    return int(round(d_obs)), blocker


def advise(flat, house, requested: dict, unassigned: list | None = None) -> list[Advice]:
    """requested = {room_id: [Placement, ...]} 表示**本来希望**放进这间房的家具。

    返回给客户看的顾问意见。
    """
    out: list[Advice] = []
    # ★ 連房都分配唔到嘅家具（每間房都塞唔落嘅尺寸）單獨講一次。
    #   唔講嘅話佢們會完全消失：客戶清單 23 件，報告寫「就位 18/18」，
    #   而床同淋浴間根本冇出現過（plan_hk_b 實測）。
    if unassigned:
        names = "、".join(p.inst for p in unassigned)
        out.append(Advice(
            room="", room_label="全屋", kind="wont_fit", severity="high",
            subjects=[p.inst for p in unassigned],
            headline="呢個戶型有 %d 件冇任何一間房放得落：%s" % (len(unassigned), names),
            detail="每間房的可用尺寸都細過這些家具（或者放完之後人過唔到）。"
                   "換更細的款式、跨零售商比價、或者改用定制。",
            options=["換更小尺寸的款式", "跨零售商比價 / 改為定制", "唔要呢件"],
            facts={"unassigned": [{"inst": p.inst, "cat": p.category,
                                   "w": p.w, "d": p.d} for p in unassigned]}))
    # 有房但房裏放唔落 → 用「missing」逐間講（下面已經有）
    for r in flat.rooms:
        want = requested.get(r.id, [])
        lay = house.by_room.get(r.id)
        if lay is None:
            lay = Layout(room_id=r.id, placements=[])
        got = {p.inst for p in lay.placements}

        # ① 根本没放下的
        missing = [p for p in want if p.inst not in got]
        if missing:
            names = "、".join(p.inst for p in missing)
            out.append(Advice(
                room=r.id, room_label=r.label or r.id, kind="wont_fit",
                severity="high", subjects=[p.inst for p in missing],
                headline="%s 有 %d 件放不下：%s" % (r.label or r.id, len(missing), names),
                detail="房間可用面積 %.1f m²，放完其餘家具後沒有空間容納這些。"
                       % (r.usable_geo().area / 1e6),
                options=["換更小尺寸的款式", "移到其他房間", "改為定制（貼合尺寸）"],
                facts={"room_area_m2": round(r.usable_geo().area / 1e6, 2),
                       "missing": [{"inst": p.inst, "cat": p.category,
                                    "w": p.w, "d": p.d} for p in missing],
                       "placed": len(got), "requested": len(want)}))

        # ② 放下了但前方空间不够
        for p in lay.placements:
            need = CLEARANCE.get(p.category)
            if not need:
                continue
            have, blocker = front_clearance(r, lay, p)
            if have + 20 < need:
                who = ("被 %s 擋住" % blocker) if blocker else "前面就是牆"
                out.append(Advice(
                    room=r.id, room_label=r.label or r.id, kind="blocked",
                    severity="high" if have < need * 0.6 else "medium",
                    subjects=[p.inst] + ([blocker] if blocker else []),
                    headline="%s 的%s打不開（差 %dmm）" % (r.label or r.id, p.inst, need - have),
                    detail="%s 正前方需要 %dmm，實際只有 %dmm，%s。"
                           % (p.inst, need, have, who),
                    shortfall_mm=need - have,
                    options=["把 %s 挪開" % blocker if blocker else "換位",
                             "換薄一點的款式", "把 %s 移到別的位置" % p.inst],
                    facts={"inst": p.inst, "cat": p.category, "need_mm": need,
                           "have_mm": have, "blocked_by": blocker,
                           "shortfall_mm": need - have}))

        # ③ 走不到
        lv = check_livability(r, lay)
        for v in lv.hard:
            if v.rule == "walkway_blocked":
                out.append(Advice(
                    room=r.id, room_label=r.label or r.id, kind="unreachable",
                    severity="high", subjects=v.subjects,
                    headline="%s 走不到 %s 前面" % (r.label or r.id, v.subjects[0]),
                    detail=v.detail,
                    options=["騰出 600mm 通道", "換窄一點的家具", "重新安排進門動線"],
                    facts={"inst": v.subjects[0], "person_width_mm": 600}))
            elif v.rule == "sofa_faces_tv":
                out.append(Advice(
                    room=r.id, room_label=r.label or r.id, kind="tight",
                    severity="medium", subjects=v.subjects,
                    headline="%s 的沙發沒能面對電視" % (r.label or r.id),
                    detail=v.detail + "（房間太窄，轉不過來）",
                    options=["換小一號沙發", "改為 L 形擺法", "電視改用掛牆"],
                    facts={"rule": v.rule, "subjects": v.subjects}))

        # ④ ★ 其餘硬傷一律要有人認領。
        #    為什麼：上面只翻譯了 walkway_blocked 同 sofa_faces_tv 兩條規則，
        #    其他硬傷（背唔到牆、開唔到門、床頭唔貼牆…）就變成「有硬傷但冇意見」。
        #    外層閉環見到「0 條意見」就以為搞掂收工 —— 實測客廳剩 1 條硬傷
        #    卻報 0 條顧問意見，閉環提早收斂。硬傷必須全部浮上水面。
        handled = {"walkway_blocked", "sofa_faces_tv"}
        # ★ door_blocked 係 soft（細房裡真係可能避唔開），但佢一定要有人認領 ——
        #   一扇開唔到嘅門係顧客必須知嘅事，唔可以靜靜雞過去。
        pending = [v for v in lv.hard if v.rule not in handled] +                   [v for v in lv.soft if v.rule == "door_blocked"]
        for v in pending:
            out.append(Advice(
                room=r.id, room_label=r.label or r.id, kind="blocked",
                severity="high" if v.severity == "hard" else "medium",
                subjects=list(v.subjects),
                headline="%s %s" % (r.label or r.id, v.detail),
                detail=v.detail,
                options=list(RULE_OPTIONS.get(v.rule,
                                              ["把擋住門口的家具挪開",
                                               "換個位置", "改為定制（貼合尺寸）"])),
                facts={"rule": v.rule, "subjects": list(v.subjects), "detail": v.detail}))
    return out


def render_for_customer(advices: list[Advice], flat=None, max_items: int = 4) -> str:
    """把顾问意见渲染成给客户看的话。

    ★ 生产环境这一步交给 LLM：输入就是每条 Advice 的 "facts"（结构化、带量化差距），
      输出是自然语言。这里先用模板，保证 Demo 里也能跑通、而且是**诚实**的。

    模板的写法刻意遵循三条：
      · 不说「无解」，说「差多少」
      · 每条都给 2-3 个具体选项，而不是让客户自己想
      · 不隐藏问题 —— 摆不好就说摆不好
    """
    if not advices:
        return "所有家具都放得下，動線也通。"
    high = [a for a in advices if a.severity == "high"]
    lines = ["⚠️ 這份方案有 %d 處需要你決定：" % len(advices)]
    for i, a in enumerate(advices[:max_items], 1):
        lines.append("")
        lines.append("%d. 【%s】%s" % (i, a.room_label, a.headline))
        lines.append("   %s" % a.detail)
        if a.options:
            lines.append("   可以這樣改：")
            for o in a.options:
                lines.append("     · %s" % o)
    if len(advices) > max_items:
        lines.append("")
        lines.append("（還有 %d 處，見完整報告）" % (len(advices) - max_items))
    lines.append("")
    lines.append("這不是「無解」—— 是現在的家具清單超出了這間屋的容量。"
                 "改小一號、換個款式、或者移到別的房間都可以。")
    return "\n".join(lines)
