#!/usr/bin/env python3
"""hk-space web console — the solver and the acceptance harness, driven from a browser.

Design rule (docs/STATUS.md §0): a criterion the solver cannot see only vetoes, never guides.
The same discipline applies to this console: **it must not invent its own rulers.**
Everything the UI shows comes from the repo's own code:

  geometry   <- hkgeometry.house.load_flat / flat_from_dict
  layout     <- hkgeometry.house.solve_house  |  hkgeometry.progressive.solve_progressive
  checklist  <- scripts/bench_hk.check()     (the same 16 rows the CLI bench prints)
  scores     <- hkgeometry.spaciousness.spaciousness_report
  overlays   <- hkgeometry.exposure / corridor / circulation (approach_zone, artery erosion)

Stdlib http.server only — no new dependencies (AGENTS.md: do not add deps).
Run:  python3 apps/web/server.py  [--port 8791]   then open http://127.0.0.1:8791
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                                  # hk-space/
for _p in (ROOT / "packages" / "geometry", ROOT / "scripts", ROOT):
    sys.path.insert(0, str(_p))

from shapely.geometry import Point                          # noqa: E402

import bench_hk                                             # noqa: E402
from hkgeometry.corridor import corridor_reports, default_corridors   # noqa: E402
from hkgeometry.circulation import (                        # noqa: E402
    artery_clear, approach_zone, blocked_approaches, portal_shapes,
)
from hkgeometry.exposure import exposure_map                # noqa: E402
from hkgeometry.house import (                              # noqa: E402
    CATEGORY_ROOMS, HouseLayout, flat_from_dict, infer_room_functions, load_flat,
    solve_house,
)
from hkgeometry.livability import _unreachable_items, check_livability  # noqa: E402
from hkgeometry.progressive import solve_progressive        # noqa: E402
from hkgeometry.spaciousness import spaciousness_report     # noqa: E402
from hkgeometry.types import Layout, Placement              # noqa: E402
from hkgeometry.llm_advisor import (                        # noqa: E402
    ACTIONS, LLMClient, _extract_json, _room_facts, apply_decisions, parse_decisions,
    try_nudge,
)


# ─────────────────────────────────────────────────── serialisation


def case_doc(name: str) -> dict:
    case = bench_hk.CASES[name]
    if case.get("document") is not None:
        return case["document"]
    return json.loads((ROOT / case["plan"]).read_text(encoding="utf-8"))


def case_flat(name: str):
    case = bench_hk.CASES[name]
    return flat_from_dict(case["document"]) if case.get("document") is not None \
        else load_flat(str(ROOT / case["plan"]))


def placement_json(p: Placement) -> dict:
    zone = approach_zone(p)
    return dict(inst=p.inst, sku=p.sku, category=p.category, x=p.x, y=p.y, rot=p.rot,
                w=p.w, d=p.d, h=p.h, z=p.z,
                front=list(p.front_dir()),
                clearance_mm=int(
                    (zone.area ** 0.5) if zone is not None else 0),
                zone=[[int(x), int(y)] for x, y in (list(zone.exterior.coords)[:4]
                                                    if zone is not None else [])])


def layout_json(house: HouseLayout) -> dict:
    # A list, not a dict: the browser walks it in solve order and posts it straight back.
    return dict(rooms=[dict(room=rid, placements=[placement_json(p) for p in lay.placements])
                       for rid, lay in house.by_room.items()],
                unplaced=list(house.unplaced),
                assignment=dict(house.assignment))


def placement_from_json(raw: dict) -> Placement:
    return Placement(inst=str(raw["inst"]), sku=str(raw.get("sku", "")),
                     category=str(raw.get("category", "")), x=int(raw["x"]), y=int(raw["y"]),
                     rot=int(raw["rot"]) % 360, w=int(raw["w"]), d=int(raw["d"]),
                     h=int(raw.get("h", 0)), z=int(raw.get("z", 0)))


def house_from_payload(flat, case, payload: dict) -> HouseLayout:
    """Rebuild a HouseLayout out of what the browser posted, so check() can vet it."""
    wanted = bench_hk.wanted_rooms(case)
    by_room, assignment = {}, {}
    raw = payload.get("rooms") or []          # list (our own) or dict (hand-edited file)
    blocks = list(raw.items()) if isinstance(raw, dict) else [(blk.get("room"), blk) for blk in raw]
    for rid, block in blocks:
        rid = str(rid)
        ps = [placement_from_json(p) for p in ((block or {}).get("placements") or [])]
        by_room[rid] = Layout(rid, ps)
        for p in ps:
            assignment[p.inst] = rid
    unplaced = list(payload.get("unplaced") or [])
    for inst in unplaced:                        # keep the case's intended room on record
        if inst in wanted:
            assignment[inst] = wanted[inst]
    return HouseLayout(flat_id=flat.id, by_room=by_room, assignment=assignment,
                       unplaced=unplaced)


def plan_json(flat) -> dict:
    funcs = infer_room_functions(flat)
    from hkgeometry.greedy import door_approach_zones
    rooms = []
    for r in flat.rooms:
        rooms.append(dict(
            id=r.id, label=r.label, func=funcs.get(r.id, r.id), height_mm=r.height_mm,
            area_m2=round(r.geo().area / 1e6, 2),
            polygon=[[int(x), int(y)] for x, y in r.polygon],
            holes=[[[int(x), int(y)] for x, y in h] for h in r.holes],
            walls=[dict(id=w.id, a=[int(w.a[0]), int(w.a[1])], b=[int(w.b[0]), int(w.b[1])],
                        type=w.type, thickness_mm=w.thickness_mm, movable=bool(w.movable))
                   for w in r.walls],
            openings=[dict(id=o.id, wall=o.wall, kind=o.kind, center_mm=o.center_mm,
                           width_mm=o.width_mm, height_mm=o.height_mm, swing=o.swing)
                      for o in r.openings],
            services=[dict(id=s.id, kind=s.kind, point=[int(s.point[0]), int(s.point[1])])
                      for s in r.services],
            entrance=list(r.entrance_point()),
            windows=[list(pt) for pt in r.window_centers()],
            door_zones=[[[int(x), int(y)] for x, y in list(z.exterior.coords)]
                        for _op, z in door_approach_zones(r)],
        ))
    return dict(flat_id=flat.id, rooms=rooms,
                # flat lists too: the canvas draws walls/doors without walking every room
                walls=[w for r in rooms for w in r["walls"]],
                openings=[o for r in rooms for o in r["openings"]],
                entry_room=flat.entry_room,
                entry_point=list(flat.entry_point),
                portals=[[[int(x), int(y)] for x, y in ring] for ring in
                         (flat.meta.get("portals") or [])],
                outline=[[int(x), int(y)] for x, y in (flat.meta.get("outline") or [])],
                area_m2=round(flat.total_area_m2(), 2),
                meta={k: v for k, v in (flat.meta or {}).items()
                      if k in ("source", "label", "image", "audit_status", "program")}
                )


def bounds_of(plan: dict) -> list[float]:
    pts = [pt for r in plan["rooms"] for pt in r["polygon"]]
    pts += [list(pt) for pt in (plan.get("outline") or [])]   # the shell can stick out past the rooms
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def overlays_json(flat, house: HouseLayout) -> dict:
    """Heat / sightlines / 900mm artery — each one computed by the repo's own metric."""
    out = {"exposure": {}, "corridors": {}, "artery": None}
    for r in flat.rooms:
        lay = house.by_room.get(r.id)
        if not lay or not lay.placements:
            continue
        cell = max(250, int(min(min(r.geo().bounds[2] - r.geo().bounds[0],
                                    r.geo().bounds[3] - r.geo().bounds[1]) / 18, 400)) // 50 * 50)
        emap = exposure_map(r, cell_mm=cell)
        out["exposure"][r.id] = dict(
            cell=cell, x0=float(emap.xs[0]), y0=float(emap.ys[0]),
            nx=int(emap.exposure.shape[1]), ny=int(emap.exposure.shape[0]),
            values=[int(round(v * 100)) for v in emap.exposure.ravel().tolist()],
        )
        cors = default_corridors(r)
        out["corridors"][r.id] = [dict(label=c.label, a=list(c.a), b=list(c.b),
                                       penalty=round(float(rep.get("penalty_norm", 0.0)), 3),
                                       blocked=bool(rep.get("blocked_by")))
                                  for c, rep in zip(cors, corridor_reports(cors, lay.placements))]
    # The 900 mm artery: erode the entry room's free space and show what actually survives,
    # so "最窄通路 900mm" in the CLI bench is no longer a boolean dressed as a measurement.
    living = next((r for r in flat.rooms if r.id == flat.entry_room), None)
    if living is not None:
        free = living.geo()
        for p in (house.by_room.get(living.id).placements if house.by_room.get(living.id) else []):
            free = free.difference(p.footprint())
        core = free.buffer(-450.0)
        rings = []
        for part in ([core] if core.geom_type == "Polygon" else list(core.geoms)):
            if part.is_empty:
                continue
            rings.append([[int(x), int(y)] for x, y in part.exterior.coords])
        out["artery"] = dict(room=living.id, min_width_mm=900, polygons=rings,
                             ok=artery_clear(flat, {rid: lay.placements
                                                    for rid, lay in house.by_room.items()}, 900))
    return out


def flags_json(flat, house: HouseLayout) -> dict:
    """Per-item problem chips, straight from livability + circulation (not parsed strings)."""
    flags: dict[str, list] = {}
    by_room = {rid: lay.placements for rid, lay in house.by_room.items()}
    for rid, lay in house.by_room.items():
        if not lay.placements:
            continue
        for v in check_livability(flat.room(rid), lay).violations:
            for inst in v.subjects:
                flags.setdefault(inst, []).append(dict(src=rid, rule=v.rule,
                                                       severity=v.severity, detail=v.detail))
    for msg in blocked_approaches(flat, by_room):
        inst = msg.split(" ", 1)[0]
        flags.setdefault(inst, []).append(dict(src="circulation", rule="approach",
                                               severity="hard", detail=msg))
    for rid, lay in house.by_room.items():          # bench criterion 8, same helper it uses
        for inst in _unreachable_items(flat.room(rid), lay):
            flags.setdefault(inst, []).append(dict(src=rid, rule="unreachable",
                                                   severity="hard",
                                                   detail="%s 走不到正前方" % inst))
    for inst in house.unplaced:
        flags.setdefault(inst, []).append(dict(src="solver", rule="unplaced",
                                               severity="hard", detail="未放成"))
    return flags


def scores_json(flat, house: HouseLayout) -> dict:
    per = {}
    for r in flat.rooms:
        lay = house.by_room.get(r.id)
        if not lay or not lay.placements:
            continue
        rep = spaciousness_report(r, lay)
        per[r.id] = dict(score=round(rep.score, 1), metrics=rep.metrics,
                         detractors=rep.detractors, passes=rep.passes)
    avg = round(sum(v["score"] for v in per.values()) / max(1, len(per)), 1)
    return dict(per_room=per, average=avg)


def assemble(flat, case, house: HouseLayout, elapsed: float) -> dict:
    rows, metrics = bench_hk.check(flat, case, house)
    npass = sum(1 for _t, ok, _d in rows if ok)
    return dict(case=case["label"], elapsed_s=round(elapsed, 2),
                plan=plan_json(flat), bounds=bounds_of(plan_json(flat)),
                layout=layout_json(house),
                checks=[dict(tag=t, ok=bool(ok), detail=d) for t, ok, d in rows],
                pass_count=npass, total=len(rows), metrics=metrics,
                scores=scores_json(flat, house), overlays=overlays_json(flat, house),
                flags=flags_json(flat, house))


# ─────────────────────────────────────────────────── jobs

# Rules a local nudge can actually fix. Orientation / furniture-relationship rules
# are the solver's job (they need a different pose, not a different spot), so the
# console does not pretend to repair them.
REPAIRABLE = ("clearance_blocked", "walkway_blocked", "door_blocked")

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def start_job(name: str, opts: dict) -> str:
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = dict(state="queued", case=name, result=None, error=None, started=time.time())
    threading.Thread(target=_run, args=(jid, name, opts), daemon=True).start()
    return jid


def _run(jid: str, name: str, opts: dict) -> None:
    job = JOBS[jid]
    job["state"] = "running"
    try:
        case = bench_hk.CASES[name]
        items = bench_hk.build_items(case)
        assign = bench_hk.wanted_rooms(case)
        flat = case_flat(name)
        solver = opts.get("solver") or case.get("solver", "cpsat")
        t0 = time.time()
        if solver == "progressive":
            house = solve_progressive(flat, items, assign, variant=int(opts.get("variant", 0)))
        else:
            house = solve_house(flat, items, time_limit_s=float(opts.get("time_limit", 20.0)),
                                max_workers=int(opts.get("workers", 6)),
                                parallel=bool(opts.get("parallel", False)),
                                assignment=assign)
        job["result"] = assemble(flat, case, house, time.time() - t0)
        job["result"]["solver"] = solver
        job["state"] = "done"
    except Exception:                                  # a solver crash must not kill the server
        job["error"] = traceback.format_exc(limit=6)
        job["state"] = "error"


# ─────────────────────────────── chat: LLM 只揀動作，確定性代碼執行，引擎重解同重核

CATALOG_CACHE = None


def load_catalog() -> list:
    """同一批貨架：seed_skus.json + fixtures.json（同 demo_llm_advisor.load_catalog）。"""
    global CATALOG_CACHE
    if CATALOG_CACHE is None:
        out = []
        for fname in ("seed_skus.json", "fixtures.json"):
            p = ROOT / "data" / "catalog" / fname
            if p.is_file():
                out += json.loads(p.read_text(encoding="utf-8")).get("skus") or []
        CATALOG_CACHE = out
    return CATALOG_CACHE


def chat_system() -> str:
    acts = "\n".join(
        "- %s：%s  params: %s" % (k, v["desc"], json.dumps(v["params"], ensure_ascii=False))
        for k, v in ACTIONS.items())
    return (
        "你是香港納米樓（200-500 呎）的室內設計師。客戶用一句話叫你改設計。\n"
        "你**不要**自己擺位，也不要輸出坐標；你只揀動作，"
        "再由確定性幾何引擎重新解算。\n\n"
        "可用動作：\n" + acts + "\n\n"
        "硬規則（違反即無效）：\n"
        "1. 只輸出一個 JSON 物件：{\"say\": \"一句粵語書面語，講你改咗乜／改唔到乜\", \"actions\": [...]}\n"
        "   不要 markdown、不要解釋、不要坐標。\n"
        "2. 每個 action 長這樣：{\"action\": \"add_item\", \"target\": \"#衣櫃_主人房\","
        " \"params\": {...}, \"reason\": \"一句，帶數字\"}。"
        "add_item / define_custom 的 target 係你自己起嘅新名。\n"
        "3. 品類 ↔ 房間限制見 category_rooms（引擎硬規則）。客戶要求違反限制時，"
        "不要照做：在 say 講清楚原因，並提出一個可行替代（例如客廳用 cabinet / shelf）。\n"
        "4. 固定潔具／廚具（toilet basin shower stove range_hood kitchen_counter）"
        "不准 move / drop；sofa 同 bed 不准 drop。\n"
        "5. 只做客戶要求嘅事，不要順手重排其他家具。\n"
        "6. 房間 id、可用尺寸、門位在 rooms；家具現況在 placed；庫存在 catalog。\n"
        "7. 可行性由引擎裁決，你不需要猜。做不到就在 say 講明，不要假裝做到。\n"
        "8. 如果客戶要求已經滿足，actions 給 []，say 講明。"
    )


def chat_facts(flat, house, catalog, message, history) -> dict:
    facts = {
        "rooms": _room_facts(flat, house),        # 同顧問 prompt 用同一份事實
        "placed": [dict(inst=p.inst, cat=p.category, w=p.w, d=p.d, h=p.h, room=rid)
                   for rid, lay in house.by_room.items() for p in lay.placements],
        "unplaced": list(house.unplaced),
        "category_rooms": {k: sorted(v) for k, v in CATEGORY_ROOMS.items()},
        "catalog": [dict(sku=s["id"], cat=s["category"], w=s["dims"]["w"],
                         d=s["dims"]["d"], h=s["dims"]["h"], name=s.get("name_zh") or "")
                    for s in catalog],
        "message": message,
    }
    if history:
        facts["history"] = list(history)[-8:]
    return facts


def chat_gate(flat, decisions):
    """品類 ↔ 房間由 CATEGORY_ROOMS 講，不由 LLM 講。"""
    funcs = infer_room_functions(flat)
    kept, notes = [], []
    for d in decisions:
        if d.action in ("add_item", "define_custom"):
            cat = str(d.params.get("category") or "")
            room = str(d.params.get("to_room") or "")
            allowed = CATEGORY_ROOMS.get(cat)
            if allowed is not None and funcs.get(room) not in allowed:
                notes.append("[refuse] %s 唔准入 %s —— 品類限制，只可以去 %s"
                             % (cat or d.target, room or "?", "、".join(sorted(allowed))))
                continue
        kept.append(d)
    return kept, notes


def chat_apply(flat, case, house, body, decisions, catalog):
    """把決定變成真嘅改動，再交返引擎重解 —— LLM 碰唔到坐標。"""
    items = bench_hk.build_items(case)
    assign = bench_hk.wanted_rooms(case)
    gone = set((body.get("layout") or {}).get("unplaced") or [])
    items = [p for p in items if p.inst not in gone]      # UI 刪走嘅唔會復活
    assign = {k: v for k, v in assign.items() if k not in gone}
    items, assign, resolve_acts, patches, notes = apply_decisions(
        items, assign, decisions, catalog, flat=flat, house=house, verbose=False)
    solved = bool(resolve_acts)
    t_solve = 0.0
    if solved:
        t1 = time.time()
        # 同 _run() 一樣：UI 嘅 auto = 唔指定，跟 case 自己嘅 solver
        solver = body.get("solver") or case.get("solver", "cpsat")
        if solver == "progressive":
            house = solve_progressive(flat, items, assign,
                                      variant=int(body.get("variant") or 0))
        else:
            house = solve_house(flat, items, time_limit_s=float(body.get("time_limit") or 20.0),
                                max_workers=int(body.get("workers") or 6),
                                parallel=bool(body.get("parallel") or False),
                                assignment=assign)
        t_solve = time.time() - t1        # 解算鐘：只有 solver 嘅時間
    out = assemble(flat, case, house, t_solve)
    out["notes"] = list(notes) + list(patches)
    # ★ 加咗件但引擎摆唔入，要当面講 —— 唔講就等於靜靜雞食咗客戶件嘢。
    #   bench 嘅 17 件清單唔識新加嘅件，所以唔會自己報。
    lost = [i for i in (house.unplaced or []) if any(p.inst == i for p in items)]
    if lost:
        out["notes"].append("[engine] 摆唔落：%s —— 清單多咗，但解算之後冇位"
                            % "、".join(lost))
    out["solved"] = solved
    return out


def run_chat(body: dict):
    """一個 chat 請求 = LLM 揀動作 + 代碼執行 + 引擎重解重核。"""
    t0 = time.time()
    name = body.get("case") or "onpak05"
    if name not in bench_hk.CASES:
        return dict(error="unknown case"), 400
    message = (body.get("message") or "").strip()
    if not message:
        return dict(error="message 係空嘅"), 400
    flat, case = case_flat(name), bench_hk.CASES[name]
    house = house_from_payload(flat, case, body.get("layout") or {})
    catalog = load_catalog()
    facts = chat_facts(flat, house, catalog, message, body.get("history"))
    user = "以下是整屋方案嘅結構化事實（單位全部 mm）：\n" + json.dumps(facts, ensure_ascii=False)
    t_llm = time.time()
    try:
        client = LLMClient(timeout=float(body.get("llm_timeout") or 120.0), effort="low")
        raw = client.chat(chat_system(), user, temperature=0.2, max_tokens=1600)
    except Exception as e:                      # 冇 key / 冇 server / HTTP 錯 → 照講
        return dict(error="連唔到 LLM：%s" % e), 503
    llm_s = round(time.time() - t_llm, 2)
    obj = _extract_json(raw)
    say, actions = "", []
    if isinstance(obj, dict):
        say = str(obj.get("say") or obj.get("reply") or "").strip()
        got = obj.get("actions")
        if got is None:
            got = obj.get("decisions")
        actions = got if isinstance(got, list) else ([got] if isinstance(got, dict) else [])
    elif isinstance(obj, list):
        actions = obj
    decisions, dropped = parse_decisions(json.dumps(actions, ensure_ascii=False))
    kept, gate_notes = chat_gate(flat, decisions)
    chat = dict(message=message, say=say, llm_s=llm_s, model=getattr(client, "model", ""),
                actions=[dict(action=d.action, target=d.target, params=d.params,
                              reason=d.reason) for d in kept],
                dropped=list(dropped) + list(gate_notes))
    if not kept:
        chat["solved"] = False
        chat["total_s"] = round(time.time() - t0, 2)
        if not say and not dropped:
            return dict(error="模型冇輸出可解析嘅 JSON", raw=raw[:1200]), 502
        return dict(chat=chat, notes=[]), 200
    out = chat_apply(flat, case, house, body, kept, catalog)
    chat["solved"] = out.get("solved")
    chat["total_s"] = round(time.time() - t0, 2)
    out["chat"] = chat
    return out, 200


# ─────────────────────────────────────────────────── HTTP


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):            # keep the console readable
        if not self.path.startswith("/static/"):
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    # helpers
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # routes
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/health":
                return self._json(dict(ok=True, root=str(ROOT), python=sys.version.split()[0],
                                       cases=list(bench_hk.CASES)))
            if path == "/api/cases":
                out = []
                for name, case in bench_hk.CASES.items():
                    flat = case_flat(name)
                    out.append(dict(name=name, label=case["label"],
                                    solver=case.get("solver", "cpsat"),
                                    area_m2=round(flat.total_area_m2(), 2),
                                    rooms=[dict(id=r.id, label=r.label) for r in flat.rooms],
                                    items=len(case["items"])))
                return self._json(dict(cases=out))
            if path == "/api/plan":
                name = (q.get("case") or ["onpak05"])[0]
                plan = plan_json(case_flat(name))
                return self._json(dict(plan=plan, bounds=bounds_of(plan)))
            if path.startswith("/api/jobs/"):
                job = JOBS.get(path.split("/")[-1])
                return self._json(job or dict(state="unknown"), 200 if job else 404)
            if path == "/api/file":
                return self._serve_allowed((q.get("p") or [""])[0])
            return self._json(dict(error="no route", path=path), 404)
        except Exception:
            return self._json(dict(error=traceback.format_exc(limit=6)), 500)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        path = url.path
        try:
            body = self._body()
            name = body.get("case") or "onpak05"
            if name not in bench_hk.CASES:
                return self._json(dict(error="unknown case", cases=list(bench_hk.CASES)), 400)
            if path == "/api/solve":
                return self._json(dict(job=start_job(name, body)))
            if path in ("/api/verify", "/api/repair"):
                t0 = time.time()          # the live-verify clock the UI shows
                flat, case = case_flat(name), bench_hk.CASES[name]
                house = house_from_payload(flat, case, body.get("layout") or {})
                notes = []
                if path == "/api/repair":
                    for lay in list(house.by_room.values()):
                        room = flat.room(lay.room_id)
                        rep = check_livability(room, lay)
                        stuck = [i for v in rep.violations if v.rule in REPAIRABLE
                                 for i in v.subjects if lay.by_inst(i) is not None]
                        stuck += [i for i in _unreachable_items(room, lay)
                                  if lay.by_inst(i) is not None]
                        for inst in dict.fromkeys(stuck):
                            ok, _dx, _dy, why = try_nudge(room, lay, inst)
                            notes.append("%s: %s%s" % (inst, why, "" if ok else "  [engine found no better spot]"))
                out = assemble(flat, case, house, time.time() - t0)
                # assemble() 自己就是最重的一段（核査 + 顯大分 + 疊圖），
                # 所以這裡報的是整個即時核查的真實耗時，不是只有還原 payload 那一段。
                out["elapsed_s"] = round(time.time() - t0, 2)
                out["notes"] = notes
                return self._json(out)
            if path == "/api/chat":
                out, code = run_chat(body)
                return self._json(out, code)
            return self._json(dict(error="no route", path=path), 404)
        except Exception:
            return self._json(dict(error=traceback.format_exc(limit=6)), 500)

    # static / file allowlists
    def _static(self, rel: str) -> None:
        p = (HERE / "static" / rel).resolve()
        if not str(p).startswith(str((HERE / "static").resolve())) or not p.is_file():
            return self._json(dict(error="not found", path=rel), 404)
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png",
                 ".svg": "image/svg+xml", ".json": "application/json"}.get(p.suffix, "text/plain")
        self._send(200, p.read_bytes(), ctype)

    def _serve_allowed(self, rel: str) -> None:
        """Only floorplan images / plans under the repo, never arbitrary paths."""
        p = (ROOT / rel).resolve()
        if not p.is_file() or not any(str(p).startswith(str((ROOT / d).resolve()))
                                      for d in ("data/floorplans", "out", "data/targets")):
            return self._json(dict(error="not allowed", path=rel), 403)
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json"}.get(
            p.suffix, "application/octet-stream")
        self._send(200, p.read_bytes(), ctype)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("hk-space console → http://%s:%d   (repo: %s)" % (args.host, args.port, ROOT), flush=True)
    print("cases: %s" % ", ".join(bench_hk.CASES), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()