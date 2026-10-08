"""LLM 顧問層 —— 讓 LLM 決定「這份平面圖該怎麼改」。

技術方案 v3.2 §5（雙通道修復）+ §8（審閱層）的落地。

架構鐵律（不可違反）：
  · **LLM 不輸出坐標**。它只輸出「動作」(action)，動作由確定性幾何執行。
  · 可行性永遠由幾何校驗器裁決，**不由 LLM 裁決**。
  · 永不返回無解：每條建議都帶量化差距 + 一個可執行動作。

兩條通道（LLM 的工作就是選哪條）：
  ① 加約束重解 (resolve)：shrink_sku / move_item / drop_item
     —— 改的是「問題定義」，然後讓 CP-SAT 從頭解一次。
  ② 局部補丁 (patch)：try_nudge
     —— 不改問題定義，只把某一件在家裡挪一點，幾何校驗通過才接受。
     好處是不動全局，壞處是只能修「差一點」的問題。

（§3.2 的 2.5D 垂直分層與支撐樹尚未實現，所以「吊櫃 / 掛牆」這類動作
  目前無法表達，不允許 LLM 提議 —— 寧可少一個動作，不要一個假動作。）
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .advisory import Advice, front_clearance
from .types import FRONT_CLEARANCE, Layout, Placement, Room

# 釘死在來去水位 / 煤氣喉上的固定件 —— 不許移、不許刪
FIXED_CATEGORIES = {
    "toilet", "basin", "bathtub", "shower",
    "stove", "range_hood", "kitchen_counter", "sink",
}

# 可掛牆、不佔地面的品類（僅用於提示，模型層尚未支持）
WALL_MOUNTABLE = {"tv_bench", "mirror", "shelf"}

# 一個「家」嘅主體家具 —— 唔可以 drop。
# 冇梳化嘅客廳唔係一個可以交付嘅方案；庫裏冇更細嘅款式係**市場缺口**，
# 正確答案係「告訴客戶要定制 / 去別家買」，唔係「唔要張梳化」。
PROTECTED_CATEGORIES = {"sofa", "bed"}

ACTIONS = {
    "shrink_sku": {
        "desc": "把這件家具換成同品類裏更小的款式（改選品，不改位置）",
        "params": {"inst": "str", "max_w": "int mm（可選，默認原尺寸 x0.85）",
                   "max_d": "int mm（可選）"},
        "channel": "resolve",
    },
    "move_item": {
        "desc": "把這件家具移到別間房（房間 id 見 rooms）",
        "params": {"inst": "str", "to_room": "str"},
        "channel": "resolve",
    },
    "drop_item": {
        "desc": "這件不要了，從清單刪掉",
        "params": {"inst": "str"},
        "channel": "resolve",
    },
    "try_nudge": {
        "desc": "不改清單，只叫求解器把這件在原位附近挪一點（引擎自己搜方向）",
        "params": {"inst": "str"},
        "channel": "patch",
    },
    "keep": {
        "desc": "接受現狀（例如固定潔具、或這個取捨可以接受）",
        "params": {"inst": "str"},
        "channel": "accept",
    },
}

SYSTEM_PROMPT = """你是香港小型住宅（納米樓 / 上車盤）的室內設計師，專長是「讓小單位看起來大」。

你會收到一份**已經解算完的整屋平面方案**，以及一份「顧問意見」清單：
哪件家具放不下、差多少 mm、哪件打不開門、哪條通道走不過去。
你的任務是**決定怎麼改**，不是重新設計。

硬規則（違反即為無效回答）：
1. **絕對不要輸出坐標、角度、尺寸數字以外的幾何描述**。你只能輸出下面的動作。
2. 你可用的動作只有這五個：
   - shrink_sku : 換同品類更小的款式。params: {"inst": "#sofa"}
                  max_w / max_d 係**可選**的。你睇唔到庫存，所以除非你對尺寸有
                  硬性要求，否則**唔好填** —— 留空，引擎會揀同品類下一個尺寸檔。
                  如果你填咗上限而庫裏冇符合嘅款，引擎會話你知實際有咩尺寸。
   - move_item  : 移到另一間房。      params: {"inst": "#storage_box", "to_room": "r_storage"}
   - drop_item  : 不要這件。           params: {"inst": "#nightstand"}
   - try_nudge  : 只挪一點點，不改清單。params: {"inst": "#desk"}
   - keep       : 接受現狀。            params: {"inst": "#toilet"}
3. 下列品類是**固定潔具/廚具**，釘死在來去水位上，**不准 move_item、不准 drop_item**，
   只能 keep 或 shrink_sku：toilet basin bathtub shower stove range_hood kitchen_counter sink。
4. 每條顧問意見都要有決定；一條意見如果涉及**多件家具**（例如「廚房有 3 件放不下：
   #fridge、#washer、#counter」），就**逐件各給一條**。不要遺漏，同一件不要重複，
   也不要自己發明新的問題。
5. 選擇優先級（由便宜到貴）：
   try_nudge < shrink_sku < move_item < drop_item。
   能挪 200mm 解決的，就不要換款式；能換款式解決的，就不要丟掉。
6. 香港現實：這間屋的層高只有 2.4-2.6m，主力牆不可拆，窗台是白送的儲物位。
   不要建議任何結構改動。
7. 如果一間房是「根本塞不下」（例如 1.85m 深的客廳要放 920mm 沙發 + 450mm 電視櫃
   + 900mm 通道 = 2270mm），**換小一號是唯一誠實的答案**，不要假裝挪一下就好了。
8. ★ 一間房的容量是有限的：**move_item 把一件家具塞進另一間房，很可能把那間房
   原有的家具逼走**（實測：把書桌移進客廳，結果客廳放不下梳化，梳化被擠掉）。
   所以順序是 try_nudge -> shrink_sku -> move_item，move_item 是最後手段。
9. ★ 梳化同床是「一個家」的主體家具，**永遠不要 drop_item**。庫裏沒有更小的款式
   不是叫你放棄它，而是要你 keep，並在 reason 裏寫清楚「呢件要定制 / 去第二間舖買」——
   這正是本產品存在的理由（IKEA HK 最細梳化 1680mm，而香港常見梳化位只有 1400mm）。

10. ★ **升級規則**：如果引擎回報你上一輪的動作做唔到（換唔到 / 挪唔到 / 被駁回），
    你必須換一個**更強**的動作（try_nudge -> shrink_sku -> move_item -> drop_item），
    唔可以重複同一個，亦唔可以即刻 keep。
    唯一例外：feasible 顯示其餘動作全部「行唔通 / 唔准」—— 咁就 keep，
    並喺 reason 講明「呢件要定制或者去第二間舖買」。

★ 每條意見都附帶一個 feasible 欄位，那是**引擎查過庫存之後的結論**：
   標住「行唔通」/「唔准」的動作唔好再揀，揀咗都只會被駁回，浪費客戶時間。
   你只需要在剩下的可行動作裏揀一個。

輸出格式：**只輸出一個 JSON 陣列**，不要解釋、不要 markdown 代碼塊。
每個元素：
{"action": "shrink_sku", "target": "#sofa", "params": {...}, "reason": "一句話，用粵語書面語"}

reason 要具體到數字（例如「客廳淨深 1850mm，放唔落 920mm 梳化」），
因為這句話會直接給客戶看。"""


@dataclass
class Decision:
    """LLM 的一個決定。注意它**沒有**任何坐標欄位。"""
    action: str
    target: str = ""
    params: dict = field(default_factory=dict)
    reason: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def channel(self) -> str:
        return ACTIONS.get(self.action, {}).get("channel", "?")

    def __str__(self) -> str:
        p = ", ".join("%s=%s" % (k, v) for k, v in self.params.items())
        return "%s(%s) %s" % (self.action, self.target, p)


# --------------------------------------------------------------------------
# ① 把幾何結果 + 顧問意見 → 提示詞
# --------------------------------------------------------------------------

def _room_facts(flat, house) -> list[dict]:
    out = []
    for r in getattr(flat, "rooms", []):
        lay = None
        if house is not None:
            lay = getattr(house, "by_room", {}).get(r.id)
        poly = r.usable_geo()
        minx, miny, maxx, maxy = poly.bounds
        out.append({
            "room": r.id,
            "label": r.label or r.id,
            "func": getattr(flat, "functions", lambda: {})().get(r.id, "?"),
            "area_m2": round(poly.area / 1e6, 2),
            "usable_w_mm": int(maxx - minx),
            "usable_d_mm": int(maxy - miny),
            "placed": [p.inst for p in (lay.placements if lay else [])],
        })
    return out


def _room_choices(flat, house, p: Placement) -> list[str]:
    """這件家具**按品類**可以搬去邊幾間房（按可用面積排）。

    ★ 必須過 CATEGORY_ROOMS。
      踩過的坑：以前只按面積排就把五間房全列出來，LLM 於是決定
      「電視櫃 / 鞋櫃 / 儲物箱 搬去睡房」—— 5.1m² 的睡房。品類搬到
      功能唔啱嘅房間係無意義嘅，引擎要喺**提議階段**就唔好講出嚟。
    """
    if flat is None:
        return []
    from .house import CATEGORY_ROOMS, infer_room_functions
    funcs = infer_room_functions(flat)
    allowed = CATEGORY_ROOMS.get(p.category)
    # 佢而家喺邊間房 —— 同房唔算「搬」，唔好列出來（實測 LLM 會答
    # move_item #sofa living -> living，白白浪費一輪）
    here = ""
    if house is not None:
        for rid, lay in getattr(house, "by_room", {}).items():
            if any(q.inst == p.inst for q in lay.placements):
                here = rid
                break
    out = []
    for r in flat.rooms:
        if allowed is not None and funcs.get(r.id) not in allowed:
            continue
        if r.id == here:
            continue
        lay = getattr(house, "by_room", {}).get(r.id) if house is not None else None
        a = r.usable_geo().area / 1e6
        n = len(lay.placements) if lay else 0
        out.append((a, r.id, n))
    out.sort(reverse=True)
    if not out:
        return []
    return ["%s(%.1fm²,現有%d件)" % (rid, a, n) for a, rid, n in out]


def feasibility(advice: Advice, catalog: list[dict] | None,
                items_by_inst: dict | None, flat=None, house=None) -> list[str]:
    """★ 引擎先替 LLM 查一次庫存，把每條意見的**可行動作**列出來。

    為什麼要有這一步：LLM 睇唔到家具庫，所以會反覆揀「換細一號」——
    而 IKEA HK 最細嘅梳化就係 1680x920，根本冇得更細。
    引擎知道，就應該講；唔講嘅話，LLM 要撞三輪先收斂，
    而客戶見到嘅係「AI 諗咗三次先發現買唔到」。

    這**不是**替 LLM 做決定：決策仍然係 LLM 揀邊個動作，
    引擎只負責講「邊個動作做得到、邊個做唔到」。
    """
    catalog = catalog or []
    items_by_inst = items_by_inst or {}
    lines: list[str] = []
    _rooms_cache: dict = {}
    r_here = None
    if flat is not None and advice.room:
        try:
            r_here = flat.room(advice.room)
        except Exception:
            r_here = None
    placed_set = set()
    if house is not None:
        placed_set = {q.inst for lay in house.by_room.values() for q in lay.placements}
    for s in advice.subjects:
        p = items_by_inst.get(s)
        if p is None:
            continue
        # 格式固定成 "<action> <inst>：可行/唔可行 — <原因>"，
        # 一行一個動作 —— 這行字既要餵 LLM，也要被 driver 解析返出來。
        sm = _pick_smaller(catalog, p.category, p.w, p.d, room=r_here)
        if sm is None:
            sw, sd, n = smallest_in_category(catalog, p.category)
            lines.append("shrink_sku %s：唔可行 — 庫裏 %d 款 %s 最細係 %dx%d，"
                         "而呢間房 %s，冇一款塞得入"
                         % (s, n, p.category, sw, sd,
                            "只有 %.1f m²" % (r_here.usable_geo().area / 1e6)
                            if r_here is not None else "太細"))
        else:
            lines.append("shrink_sku %s：可行 — 可換成 %dx%d"
                         % (s, sm["dims"]["w"], sm["dims"]["d"]))
        if p.category in FIXED_CATEGORIES:
            lines.append("move_item %s：唔可行 — 固定潔具/廚具，來去水位釘死" % s)
            lines.append("drop_item %s：唔可行 — 同上，固定件" % s)
        else:
            ch = _rooms_cache.get(s)
            if ch is None:
                ch = _room_choices(flat, house, p)
                _rooms_cache[s] = ch
            if ch:
                lines.append("move_item %s：可行 — 目標房間 %s" % (s, "、".join(ch)))
            else:
                lines.append("move_item %s：唔可行 — 同品類冇其他房可以放" % s)
            if p.category in PROTECTED_CATEGORIES:
                lines.append("drop_item %s：唔可行 — 主體家具，冇咗就唔係一個家" % s)
            else:
                lines.append("drop_item %s：可行 — 但會憑空少一件，"
                             "請喺 reason 講清楚代價（例如「改掛牆」）" % s)
        if s in placed_set:
            lines.append("try_nudge %s：可行 — 引擎自己搜方向同步長，唔改清單" % s)
        else:
            lines.append("try_nudge %s：唔可行 — 呢件根本冇放得落，冇位置可以挪"
                         "（佢係「裝唔落」，唔係「擺得唔好」）" % s)
    return lines


def build_prompt(advices: list[Advice], flat=None, house=None,
                 extra: dict | None = None, catalog: list[dict] | None = None,
                 items_by_inst: dict | None = None) -> tuple[str, str]:
    """回傳 (system, user)。user 是**純結構化事實**，沒有一句我先寫好的話。"""
    payload = {
        "flat": {
            "id": getattr(flat, "id", ""),
            "total_area_m2": round(flat.total_area_m2(), 1) if flat is not None else None,
            "rooms": _room_facts(flat, house),
        },
        "advices": [
            {
                "room": a.room,
                "room_label": a.room_label,
                "kind": a.kind,
                "severity": a.severity,
                "subjects": a.subjects,
                "headline": a.headline,
                "detail": a.detail,
                "shortfall_mm": a.shortfall_mm,
                "options": a.options,
                "facts": a.facts,
                "feasible": feasibility(a, catalog, items_by_inst, flat, house),
            }
            for a in advices
        ],
        "n_advices": len(advices),
    }
    if extra:
        payload.update(extra)

    tail = (
        "請為上面 %d 條顧問意見給出決定，輸出 JSON 陣列。"
        "一條意見涉及幾件家具就出幾條（每件一條），同一件唔好重複。"
        % len(advices)
        if advices else
        "上面沒有任何顧問意見 —— 請輸出空陣列 []。"
    )
    user = (
        "以下是整屋方案的結構化事實（單位全部是 mm，房間 id 用 room 欄位）：\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
        + "\n\n" + tail
    )
    return SYSTEM_PROMPT, user


# --------------------------------------------------------------------------
# ② 解析
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def _extract_json(text: str):
    """模型很愛加解釋和 code fence，這裡負責把它挖出來。"""
    if not text:
        return None
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    # 退而求其次：抓第一個 [ 到最後一個 ]
    i, j = t.find("["), t.rfind("]")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            pass
    # 再退：逐一抓 {...}
    objs = []
    for m in re.finditer(r"\{[^{}]*\}", t):
        try:
            objs.append(json.loads(m.group(0)))
        except Exception:
            continue
    return objs or None


def parse_decisions(text: str, allowed_targets: set[str] | None = None
                    ) -> tuple[list[Decision], list[str]]:
    """回傳 (決策, 被丟掉的理由)。不做 silent 修復 —— 丟掉就說出來。"""
    obj = _extract_json(text)
    dropped: list[str] = []
    if obj is None:
        return [], ["模型輸出裏找不到 JSON"]
    if isinstance(obj, dict):
        for k in ("decisions", "result", "results", "actions"):
            if isinstance(obj.get(k), list):
                obj = obj[k]
                break
        else:
            obj = [obj]
    if not isinstance(obj, list):
        return [], ["JSON 頂層不是陣列"]

    out: list[Decision] = []
    for i, d in enumerate(obj):
        if not isinstance(d, dict):
            dropped.append("#%d 不是物件" % i)
            continue
        act = str(d.get("action") or d.get("do") or "").strip()
        if act not in ACTIONS:
            dropped.append("#%d 未知動作 %r" % (i, act))
            continue
        params = dict(d.get("params") or {})
        # 允許模型把 params 攤平在頂層
        for k in ("inst", "to_room", "max_w", "max_d"):
            if k not in params and k in d:
                params[k] = d[k]
        tgt = str(d.get("target") or d.get("inst") or params.get("inst") or "").strip()
        if tgt and not tgt.startswith("#"):
            tgt = "#" + tgt
        if tgt and allowed_targets is not None and tgt not in allowed_targets:
            dropped.append("#%d 未知家具 %s" % (i, tgt))
            continue
        if act != "keep" and not tgt:
            dropped.append("#%d %s 沒有 target" % (i, act))
            continue
        if act == "move_item" and not params.get("to_room"):
            dropped.append("#%d move_item 沒有 to_room" % i)
            continue
        if act == "keep":
            params.pop("inst", None)
        else:
            params["inst"] = tgt
        out.append(Decision(action=act, target=tgt, params=params,
                            reason=str(d.get("reason") or "").strip(), raw=d))
    return out, dropped


# --------------------------------------------------------------------------
# ③ 調模型（OpenAI 兼容 /chat/completions，只用標準庫）
#
# ★ 默認走 **DeepSeek 官方 API（V4.1 Flash）**。
#   本地 llama.cpp 一樣支援 —— 把 base_url 指去 127.0.0.1:8080/v1，
#   dialect 會自動切過去。兩個 API 方言唔同，唔可以混：
#     · llama.cpp + Qwen：chat_template_kwargs={"enable_thinking": false}
#     · DeepSeek 官方：reasoning_effort = low / high / max
# --------------------------------------------------------------------------

DEEPSEEK_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-flash"        # = DeepSeek-V4.1-Flash（1M ctx，支援圖片）
LOCAL_BASE = "http://127.0.0.1:8080/v1"
LOCAL_MODEL = "Qwen3.8-27B-Q4_K_M.gguf"

# ★ 刻意**不**繼承 OPENAI_BASE_URL：這台機器上那個變數指向 localhost:8000，
#   而 8000 上沒有任何東西在聽（實測 Connection refused）。
DEFAULT_BASE = os.environ.get("HKSPACE_LLM_URL", DEEPSEEK_BASE)
DEFAULT_MODEL = os.environ.get("HKSPACE_LLM_MODEL", DEEPSEEK_MODEL)


def find_api_key() -> str:
    """找 API key：先環境變數，再 DSH 的憑證檔。

    ★ key 只會留在記憶體裡：唔會入 repo、唔會入 log、唔會入 prompt。
      所以呢個函數回傳嘅字串**永遠唔應該被 print 出來**。
    """
    for env in ("HKSPACE_LLM_KEY", "DEEPSEEK_API_KEY"):
        v = os.environ.get(env)
        if v:
            return v
    cands = [os.path.expanduser("~/.dsh/.credentials.yaml")]
    if os.environ.get("DSH_HOME"):
        cands.append(os.path.join(os.environ["DSH_HOME"], ".credentials.yaml"))
    for p in cands:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY:"):
                        return line.split(":", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


class LLMClient:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: float = 300.0,
                 thinking: bool = False, effort: str = "low"):
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        host = urlparse(self.base_url).hostname or ""
        # 本機 = llama.cpp 方言；api.deepseek.com = DeepSeek 官方方言
        self.dialect = "llamacpp" if host in ("127.0.0.1", "localhost", "::1")             else "deepseek"
        if model:
            self.model = model
        elif self.dialect == "llamacpp":
            self.model = os.environ.get("HKSPACE_LLM_MODEL", LOCAL_MODEL)
        else:
            self.model = DEFAULT_MODEL
        self.api_key = api_key or find_api_key() or "sk-local"
        self.timeout = timeout
        self.thinking = thinking
        self.effort = effort
        self.calls: list[dict] = []

    def describe(self) -> str:
        return "%s / %s (%s, effort=%s)" % (
            urlparse(self.base_url).hostname, self.model, self.dialect,
            self.effort if self.dialect == "deepseek" else "n/a")

    def list_models(self) -> list[str]:
        req = urllib.request.Request(self.base_url + "/models",
                                     headers={"Authorization": "Bearer " + self.api_key})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("id", "") for m in data.get("data", [])]

    def chat(self, system: str, user: str, temperature: float = 0.2,
             max_tokens: int = 0) -> str:
        """要 JSON 的結構化輸出。

        ★ 兩個方言的「思考」要分開處理：
          · llama.cpp + Qwen：直接關掉思考（chat_template_kwargs）。
          · DeepSeek V4.1：冇「關」呢個選項（只得 low/high/max），
            所以只能**俾足額度**。實測 effort=low 喺 prompt 變大之後
            仍然會用光 2000 tokens 去思考，content 回來係空字串
            （finish_reason=length）—— 表現就係「模型冇輸出」。
            所以 DeepSeek 預設 8000，而且截斷時會加倍重試一次。
        """
        if not max_tokens:
            max_tokens = 8000 if self.dialect == "deepseek" else 2000
        for attempt in (0, 1):
            txt, finish = self._post(system, user, temperature, max_tokens)
            if txt:
                return txt
            if finish == "length" and attempt == 0:
                print("    [llm] 輸出被 max_tokens=%d 截斷（思考食晒額度），"
                      "加倍重試" % max_tokens)
                max_tokens *= 2
                continue
            return ""
        return ""

    def _post(self, system: str, user: str, temperature: float,
              max_tokens: int) -> tuple[str, str]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.dialect == "llamacpp":
            body["chat_template_kwargs"] = {"enable_thinking": bool(self.thinking)}
        elif self.effort:
            # DeepSeek V4.1 支援 low / high / max，默認 high。
            # 呢個任務係「照住結構化事實揀一個動作」，low 就夠，
            # 而且 reasoning token 係收費嘅。
            body["reasoning_effort"] = self.effort
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError("LLM HTTP %s: %s" % (e.code, e.read()[:400])) from e
        ch = data["choices"][0]
        msg = ch.get("message") or {}
        finish = ch.get("finish_reason") or ""
        txt = (msg.get("content") or "").strip()
        # ★ 唔可以隨手拎 reasoning_content 當答案。
        #   思考過程裏模型會寫「草稿 JSON」，拎去解析等於**拿未完成嘅想法當決定**。
        #   只在「被截斷 + content 全空」時才退而求其次，而且記低旗標。
        if not txt and finish == "length":
            txt = (msg.get("reasoning_content") or "").strip()
            if txt:
                self.truncated_thinking = True
        u = data.get("usage") or {}
        self.last_finish = finish
        self.calls.append({"in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
                           "finish": finish, "chars": len(txt)})
        return txt, finish

    def decide(self, advices: list[Advice], flat=None, house=None,
               allowed_targets: set[str] | None = None,
               extra: dict | None = None, temperature: float = 0.2,
               catalog: list[dict] | None = None,
               items_by_inst: dict | None = None
               ) -> tuple[list[Decision], dict]:
        system, user = build_prompt(advices, flat, house, extra,
                                    catalog=catalog, items_by_inst=items_by_inst)
        raw = self.chat(system, user, temperature=temperature)
        dec, dropped = parse_decisions(raw, allowed_targets)
        return dec, {"system": system, "user": user, "raw": raw, "dropped": dropped}


# --------------------------------------------------------------------------
# ④ 執行動作 —— 確定性代碼，LLM 碰不到這裏
# --------------------------------------------------------------------------

def _dims_fit(w: int, d: int, room) -> bool:
    """這件家具塞唔塞得入呢間房（用可用邊界，唔算通道）。"""
    if room is None:
        return True
    minx, miny, maxx, maxy = room.usable_geo().bounds
    rw, rd = maxx - minx, maxy - miny
    return (w <= rw and d <= rd) or (w <= rd and d <= rw)


def _pick_smaller(catalog: list[dict], category: str, cur_w: int, cur_d: int,
                  max_w: int = 0, max_d: int = 0, room=None) -> dict | None:
    """同品類裏比現在更小的款式。

    兩種模式：
      · LLM 給了 max_w / max_d —— 當**硬約束**，在符合上限的裏面挑最大的（少犧牲）。
      · LLM 只說「換細一號」 —— 挑**下一個尺寸檔**：長寬都不超過現在、
        且至少一邊真的變小，取面積最大的那一款。
        （★ 不要再用「原尺寸 x0.85」當默認上限：實測 1200x600 的書桌
          x0.85 = 1020x510，剛好把 1050x500 的 MICKE 排除掉，
          於是回報「庫裏沒有更小的款式」—— 明明有。）
    """
    best, best_a = None, None
    for s in catalog:
        if s.get("category") != category:
            continue
        w, d = s["dims"]["w"], s["dims"]["d"]
        # ★ 「換細一號」只有在**換完之後真係塞得入呢間房**才算可行。
        #   唔檢查的話：睡房淨深 1430mm，而庫裏「下一個尺寸」係 1490x2000 ——
        #   引擎照樣報「可行」，LLM 換完一樣放唔落，白行一輪。
        if not _dims_fit(w, d, room):
            continue
        if max_w or max_d:
            # 上限 = min(LLM 給的, 現在的) —— LLM 睇唔到庫，
            # 佢随口寫 1500x800 唔代表佢想換一件**更大**嘅（踩過：
            # 1200x600 嘅書桌被「換」成 1320x580 嘅 ALEX，反而闊咗）。
            lw = min(max_w or 10 ** 9, cur_w)
            ld = min(max_d or 10 ** 9, cur_d)
            if not ((w <= lw and d <= ld) or (w <= ld and d <= lw)):
                continue
            if w >= cur_w and d >= cur_d:
                continue                  # 沒有真的變小，換了等於沒換
            a = w * d
            if best_a is None or a > best_a:
                best, best_a = s, a
        else:
            if w > cur_w or d > cur_d:
                continue                  # 不許有一邊變大（放唔落就係放唔落）
            if w >= cur_w and d >= cur_d:
                continue                  # 兩邊都一樣 = 沒變小
            a = w * d
            if best_a is None or a > best_a:
                best, best_a = s, a
    return best


def smallest_in_category(catalog: list[dict], category: str) -> tuple[int, int, int]:
    """同品類最小的 (w, d, 款式數) —— 用嚟老實講「庫裏最細就係咁大」。"""
    sizes = sorted({(s["dims"]["w"], s["dims"]["d"])
                    for s in catalog if s.get("category") == category},
                   key=lambda t: t[0] * t[1])
    if not sizes:
        return 0, 0, 0
    return sizes[0][0], sizes[0][1], len(sizes)


def _fits(room: Room, p: Placement, others: list[Placement]) -> bool:
    fp = p.footprint()
    if not room.usable_geo().buffer(1.0).contains(fp):
        return False
    for q in others:
        if q is p:
            continue
        if fp.intersects(q.footprint().buffer(-1.0)):
            return False
    return True


def try_nudge(room: Room, lay: Layout, inst: str, step_mm: int = 50,
              max_steps: int = 14) -> tuple[bool, int, int, str]:
    """局部補丁：在原位附近螺旋搜索，找一個「確實修好了問題」的位移。

    方向與步長由**引擎**決定，不由 LLM 決定 —— LLM 只說「試試挪這件」。

    評分順序（前面的贏）：① 少幾件走不到 ② 這件自己也走得到了
    ③ 前淨空不低於要求 ④ 位移越小越好。
    ★ 原地不動 (0,0) 只有在「本來就沒問題」時才算贏 —— 否則補丁會
      回報一個假的 ✅（踩過：desk 走不到，nudge 卻回「挪 (+0,+0)mm」）。
    """
    from .livability import check_livability, _unreachable_items

    p = lay.by_inst(inst)
    if p is None:
        return False, 0, 0, "不在這間房"
    others = [q for q in lay.placements if q.inst != inst]
    need = FRONT_CLEARANCE.get(p.category, 0)
    base_others = {q.inst: front_clearance(room, lay, q)[0] for q in others}

    # ★ 用**完整**的宜居檢查做裁判，唔好自己拍腦袋列幾條規則。
    #   踩過的坑：舊版只查「重疊 / 前淨空 / 走不到件數」，結果一個補丁
    #   令走不到由 4 件減到 3 件、補丁本身報 ✅，但整間房的硬傷由 5 條升到 6 條
    #   （挪完之後那件背唔到牆 / 開唔到門），外層裁決照樣整輪回滾，白做。
    lv_before = check_livability(room, lay)
    h_before = len(lv_before.hard)
    rules_on = {v.rule for v in lv_before.hard if inst in v.subjects}
    un_before = set(_unreachable_items(room, lay))

    ox, oy = p.x, p.y
    cands = [(0, 0)]
    for r in range(1, max_steps + 1):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            cands.append((dx * r * step_mm, dy * r * step_mm))

    scored = []
    for dx, dy in cands:
        moved = (dx, dy) != (0, 0)
        p.x, p.y = ox + dx, oy + dy
        if moved and not _fits(room, p, others):
            continue
        have, _ = front_clearance(room, lay, p)
        if need and have + 20 < need:
            continue                      # 前淨空不足，直接淘汰
        ok_others = True
        for q in others:
            h2, _ = front_clearance(room, lay, q)
            if h2 + 10 < base_others.get(q.inst, 0):
                ok_others = False
                break
        if not ok_others:
            continue
        if not moved:
            # 原地：只有「本來就冇問題」才算候選，否則會回報假 ✅
            if rules_on:
                continue
            scored.append((0.0, 0.0, dx, dy, have, h_before))
            continue
        lv_after = check_livability(room, lay)
        if len(lv_after.hard) > h_before:
            continue                      # 修一件、整壞兩件，唔換
        still = [v for v in lv_after.hard
                 if inst in v.subjects and v.rule in rules_on]
        if still:
            continue                      # 呢件自己嘅問題冇修好
        gain = (h_before - len(lv_after.hard)) + (1.0 if rules_on else 0.0)
        scored.append((gain, -0.02 * (abs(dx) + abs(dy)), dx, dy, have,
                       len(lv_after.hard)))
    p.x, p.y = ox, oy
    if not scored:
        return False, 0, 0, "附近沒有能修好問題的位置"
    scored.sort(key=lambda t: (-t[0], -t[1]))
    gain, _, dx, dy, have, h_after = scored[0]
    if gain <= 0:
        return False, dx, dy, "挪唔到（原地已是最優，問題唔在位置上）"
    p.x, p.y = ox + dx, oy + dy
    tail = "，前淨空 %dmm" % have if need else ""
    return True, dx, dy, "挪 (%+d, %+d)mm，全房硬傷 %d -> %d%s" % (
        dx, dy, h_before, h_after, tail)


def assign_allowed(flat, p: Placement) -> set[str]:
    """按品類，這件家具可以進哪幾間房（房間 id）。"""
    from .house import CATEGORY_ROOMS, infer_room_functions
    funcs = infer_room_functions(flat)
    allowed = CATEGORY_ROOMS.get(p.category)
    if allowed is None:
        return {r.id for r in flat.rooms}
    return {r.id for r in flat.rooms if funcs.get(r.id) in allowed}


def apply_decisions(items: list[Placement], assign: dict[str, str],
                    decisions: list[Decision], catalog: list[dict],
                    flat=None, house=None, verbose: bool = True,
                    drop_budget: int = 99
                    ) -> tuple[list[Placement], dict[str, str], list[str], list[str]]:
    """回傳 (新清單, 新分配, 重解通道的動作清單, 補丁日誌, 逐步說明)。"""
    notes: list[str] = []
    patches: list[str] = []
    by_inst = {p.inst: p for p in items}
    resolve_acts: list[str] = []
    done: set = set()               # 同一件家具可能被兩條意見各提一次，唔好做兩次
    drops = 0

    for d in decisions:
        if d.target and d.action != "keep":
            key = (d.action, d.target)
            if key in done:
                notes.append("[dup] %s %s —— 同一件已經處理過，跳過"
                             % (d.action, d.target))
                continue
            done.add(key)
        p = by_inst.get(d.target)
        if d.action == "keep":
            notes.append("[keep] %s —— 接受現狀" % d.target)
            continue
        if p is None:
            notes.append("[skip] %s 不在清單裏" % d.target)
            continue
        if d.action in ("move_item", "drop_item") and p.category in FIXED_CATEGORIES:
            notes.append("[skip] %s 是固定潔具/廚具，不能移也不能丟" % d.target)
            continue
        if d.action == "drop_item" and p.category in PROTECTED_CATEGORIES:
            notes.append("[refuse] %s 唔可以 drop —— 冇梳化/冇床嘅方案唔可以交貨。"
                         "庫內冇更細嘅款式係**市場缺口**，正確做法係 keep，"
                         "再向客戶講『呢件要定制或者去第二間買』" % d.target)
            continue

        if d.action == "shrink_sku":
            if p.category in FIXED_CATEGORIES:
                notes.append("[skip] %s 是固定件，型號改了也裝不回原位（來去水位固定）"
                             % d.target)
                continue
            r_target = None
            if flat is not None:
                rid_t = assign.get(d.target)
                if rid_t:
                    try:
                        r_target = flat.room(rid_t)
                    except Exception:
                        r_target = None
            s = _pick_smaller(catalog, p.category,
                              p.w, p.d,
                              int(d.params.get("max_w") or 0),
                              int(d.params.get("max_d") or 0),
                              room=r_target)
            if s is None:
                sw, sd, n = smallest_in_category(catalog, p.category)
                lw = int(d.params.get("max_w") or 0)
                ld = int(d.params.get("max_d") or 0)
                if sw and (sw < p.w or sd < p.d):
                    # ★ 庫裏明明有更細嘅，只係 LLM 自己填嘅上限太緊。
                    #   實測：LLM 寫 max_w=1000 去換 1200x600 嘅書桌，
                    #   而庫裏最細嘅係 1050x500 —— 只差 50mm 就被判「換唔到」，
                    #   於是 LLM 以為行唔通，轉去 keep。反饋必須講清楚係邊種 miss。
                    notes.append("[near-miss] %s 其實換得到：庫裏 %s 最細係 %dx%d"
                                 "（你手上 %dx%d），但超出你俾嘅上限 %dx%d。"
                                 "→ 放寬上限（或者直接唔填 max_w/max_d，"
                                 "由引擎揀下一個尺寸檔）就換得成"
                                 % (d.target, p.category, sw, sd, p.w, p.d, lw, ld))
                elif sw:
                    notes.append("[miss] %s 換唔到：你手上係 %dx%d，而庫裏 %d 款 %s "
                                 "最細就係 %dx%d —— 同品類真係冇更細嘅，換款呢條路行唔通。"
                                 "改用 try_nudge（唔改清單）；如果真係塞唔落，"
                                 "就 keep 然後喺 reason 講明『要定制 / 跨零售商比價』"
                                 % (d.target, p.w, p.d, n, p.category, sw, sd))
                else:
                    notes.append("[miss] %s：庫裏根本冇 %s 呢個品類"
                                 % (d.target, p.category))
                continue
            old = "%dx%d" % (p.w, p.d)
            p.sku = s["id"]
            p.w, p.d, p.h = s["dims"]["w"], s["dims"]["d"], s["dims"]["h"]
            p.visual = dict(s.get("visual") or {})
            p.price_hkd = s.get("price_hkd", 0)
            notes.append("[shrink] %s %s -> %s (%dx%d)，$%d"
                         % (d.target, old, s["id"], p.w, p.d, p.price_hkd))
            resolve_acts.append(d.action)

        elif d.action == "move_item":
            to = str(d.params.get("to_room"))
            if flat is not None and flat.room(to) is None:
                notes.append("[skip] %s 沒有房間 %s" % (d.target, to))
                continue
            if flat is not None and to not in assign_allowed(flat, p):
                notes.append("[refuse] %s（%s）唔可以搬去 %s —— 品類限制，"
                             "只可以放 %s" % (d.target, p.category, to,
                                            "、".join(sorted(assign_allowed(flat, p)))
                                            or "冇任何房"))
                continue
            old = assign.get(d.target, "?")
            assign[d.target] = to
            notes.append("[move] %s %s -> %s" % (d.target, old, to))
            resolve_acts.append(d.action)

        elif d.action == "drop_item":
            # ★ 丟家具要有預算。
            #   為什麼：閉環的目標函數係「硬傷 + 顯大分」，而**丟嘢一定會令分數上升**
            #   （東西少 → 視覺負荷低 → 硬傷少）。冇預算嘅話 LLM 會學到
            #   「唔要就解決咗」：plan_hk_b 實測 6 輪之後丟咗 6 件，
            #   硬傷 5→0、客廳 82→96，但客戶嘅清單由 23 件縮到 9 件。
            #   客戶嘅家具唔係優化器嘅自由變數。
            if drops >= drop_budget:
                notes.append("[refuse] %s 唔可以再 drop —— 呢次閉環嘅丟棄預算已用完。"
                             "靠「唔要」係解決唔到容量問題嘅：請改用 move_item / "
                             "shrink_sku，或者 keep 並叫客戶定制。" % d.target)
                continue
            drops += 1
            items = [q for q in items if q.inst != d.target]
            assign.pop(d.target, None)
            notes.append("[drop] %s（%s）" % (d.target, p.category))
            if p.category in WALL_MOUNTABLE:
                # ★ 誠實標註假設：LLM 說「電視改掛牆」，但 §3.2 的 2.5D 分層
                #   還沒實現，掛牆件表達不出來，只能用「移除電視櫃」近似。
                #   不寫這句，報告就會假裝牆上真的有一部電視。
                notes.append("    （假設：%s 改為掛牆 —— 2.5D 分層未實現，"
                             "暫以「移除 %s」近似）" % (d.target, d.target))
            resolve_acts.append(d.action)

        elif d.action == "try_nudge":
            rid = assign.get(d.target)
            lay = house.by_room.get(rid) if house is not None else None
            r = flat.room(rid) if (flat is not None and rid) else None
            if lay is None or r is None:
                notes.append("[skip] %s 沒有已解的位置可挪" % d.target)
                continue
            ok, dx, dy, why = try_nudge(r, lay, d.target)
            patches.append("%s %s" % ("✅" if ok else "❌", why))
            notes.append("[nudge] %s %s" % (d.target, why))

    return items, assign, resolve_acts, patches, notes
