"""理解一张户型图。几何负责坐标，模型只负责挑假设。

过不了门槛就标 unverified，不会因为模型自己说通过就放行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "floorplan"))
sys.path.insert(0, str(ROOT / "packages" / "geometry"))

import fitz

from pdf_extract import extract_vector_plan, find_page, open_pdf, render_page
from plan_graph import PlanGraph
from reconstruct import acceptance_errors, build_graphs
from render_overlay import measure_alignment, render_overlay


def understand_floorplan(source, out_json, *, page=None, unit_hint=None, provider=None,
                         max_rounds=3, overlay_path=None, live=False) -> dict:
    source = Path(source)
    out_json = Path(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    if provider is None and live:
        provider = _live_provider()
    if source.suffix.lower() == ".pdf":
        result = _understand_pdf(source, out_json, page=page, unit_hint=unit_hint,
                                 provider=provider, max_rounds=max_rounds, overlay_path=overlay_path)
    else:
        result = _understand_raster(source, out_json, provider=provider,
                                    max_rounds=max_rounds, overlay_path=overlay_path)
    audit_path = out_json.with_name(out_json.stem + "_audit.json")
    audit_path.write_text(json.dumps(_scrub(result["audit"]), ensure_ascii=False, indent=2), encoding="utf-8")
    out_json.write_text(json.dumps(_scrub(result["plan"]), ensure_ascii=False, indent=2), encoding="utf-8")
    result["audit_path"] = str(audit_path)
    return result


def _understand_pdf(source, out_json, *, page, unit_hint, provider, max_rounds, overlay_path):
    doc = open_pdf(source)
    try:
        index = find_page(doc, page, unit_hint)
        pdf_page = doc[index]
        extracted = extract_vector_plan(pdf_page, unit_hint)
        flat_id = "%s-%s" % (source.stem, unit_hint or index + 1)
        graphs = build_graphs(extracted, flat_id)
        render_path = out_json.with_name(out_json.stem + "_render.png")
        clip = _clip(extracted)
        render_page(pdf_page, render_path, zoom=2.0, clip=clip)
        return _finish(graphs, render_path, clip, extracted, out_json, provider, max_rounds, overlay_path,
                       vector_matches_render=extracted.get("vector_matches_render", False))
    finally:
        doc.close()


def _understand_raster(source, out_json, *, provider, max_rounds, overlay_path):
    from vectorize import Params, vectorize
    doc = vectorize(source, out_json, Params(), overlay_path=None, verbose=False, persist=False)
    graph = _graph_from_raster(doc, source.stem)
    return _finish([graph], source, None, None, out_json, provider, max_rounds, overlay_path,
                   vector_matches_render=True, raster_doc=doc)


def _finish(graphs, image_path, clip, extracted, out_json, provider, max_rounds, overlay_path,
            vector_matches_render, raster_doc=None):
    overlay_path = Path(overlay_path) if overlay_path else out_json.with_name(out_json.stem + "_overlay.png")
    llm_rounds = []
    chosen = _fewest_errors(graphs)
    status = "unverified"
    failures = []
    overlay = {}
    rounds = 0
    limit = max(1, int(max_rounds))
    for rounds in range(1, limit + 1):
        plan = chosen.to_room_json()
        _attach_raster_meta(plan, image_path, clip, chosen, raster_doc)
        plan_path = out_json
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        try:
            render_overlay(Path(image_path), plan_path, overlay_path)
        except SystemExit:
            overlay = dict(living_agreement=0.0, blocking=True, per_room={})
        else:
            overlay = measure_alignment(Path(image_path), plan)
        failures = acceptance_errors(chosen, overlay)
        failures.extend(_structure_errors(plan))
        if not vector_matches_render:
            failures.append("vector fill does not match the render")
        report = None
        if provider is not None:
            report = _ask_auditor(provider, graphs, chosen, failures, image_path, overlay_path)
            llm_rounds.append(_scrub(report))
            rejected = set(report.get("reject_hypotheses") or [])
            if chosen.hypothesis in rejected:
                failures.append("auditor rejected this hypothesis")
            selected = _select(graphs, report)
            if (selected is not None and selected.hypothesis != chosen.hypothesis
                    and selected.hypothesis not in rejected and chosen.hypothesis not in rejected):
                chosen = selected
                continue
        if not failures:
            status = "verified"
            break
        if provider is None:
            break
        alternate = _next_untried(graphs, chosen, llm_rounds)
        if alternate is None or alternate.hypothesis in (rejected if provider is not None else set()):
            break
        if len(acceptance_errors(alternate)) > len(failures):
            break
        chosen = alternate
    if any("overlap" in item or "invalid" in item for item in failures):
        status = "rejected"
    elif failures:
        status = "unverified"
    plan = chosen.to_room_json()
    _attach_raster_meta(plan, image_path, clip, chosen, raster_doc)
    plan["meta"]["audit_status"] = status
    plan["meta"]["layout_allowed"] = status == "verified"
    plan["meta"]["hypothesis"] = chosen.hypothesis
    plan["meta"]["overlay"] = str(overlay_path)
    plan["meta"]["rectified_image"] = str(image_path)
    plan["meta"]["walls_detected"] = len(chosen.walls)
    plan["meta"]["openings_detected"] = len(chosen.openings)
    plan["meta"]["confidence"] = round(float(overlay.get("living_agreement") or 0), 3)
    plan["meta"]["scale_source"] = "dimension-consensus" if chosen.scale_mm_per_unit else "none"
    plan["meta"]["ocr_engine"] = "template"
    out_json.write_text(json.dumps(_scrub(plan), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        render_overlay(Path(image_path), out_json, overlay_path, title="understood floor plan")
    except SystemExit:
        pass
    audit = dict(
        status=status,
        hypothesis=chosen.hypothesis,
        confidence={room.id: round(room.evidence[0].confidence, 3) if room.evidence else 0 for room in chosen.rooms},
        evidence=plan["meta"].get("evidence") or [],
        failures=failures,
        llm=llm_rounds,
        overlays=[str(overlay_path), str(image_path)],
        rounds=rounds,
        signature=chosen.signature(),
        overlay_metrics=overlay,
        vector_matches_render=vector_matches_render,
    )
    return dict(plan=plan, audit=audit, status=status)


def _fewest_errors(graphs: list[PlanGraph]) -> PlanGraph:
    return min(graphs, key=lambda graph: (len(acceptance_errors(graph)), 0 if graph.hypothesis == "wall-cells" else 1))


def _select(graphs, report):
    name = report.get("select_hypothesis")
    if not name:
        return None
    return next((graph for graph in graphs if graph.hypothesis == name), None)


def _next_untried(graphs, chosen, rounds):
    used = {chosen.hypothesis}
    for report in rounds:
        if report.get("select_hypothesis"):
            used.add(report["select_hypothesis"])
    for graph in graphs:
        if graph.hypothesis not in used:
            return graph
    return None


def _ask_auditor(provider, graphs, chosen, failures, image_path, overlay_path):
    from hkgeometry.vision_roles import FloorplanAuditor
    notes = json.dumps({
        "hypotheses": [graph.hypothesis for graph in graphs],
        "current": chosen.hypothesis,
        "rooms": [{"id": room.id, "label": room.label} for room in chosen.rooms],
        "failures": failures,
        "instruction": "Select an existing hypothesis or reject one. Do not invent coordinates.",
    }, ensure_ascii=False)
    images = []
    for path in (image_path, overlay_path):
        file = Path(path)
        if file.exists():
            images.append(file.read_bytes())
    return FloorplanAuditor(provider).audit(images, notes)


def _structure_errors(plan: dict) -> list[str]:
    from hkgeometry.house import flat_from_dict
    from hkgeometry.structure import validate_structure
    try:
        flat = flat_from_dict(plan)
    except Exception as exc:
        return ["plan could not be loaded: %s" % exc]
    return validate_structure(flat)


def _attach_raster_meta(plan, image_path, clip, graph: PlanGraph, raster_doc):
    plan.setdefault("meta", {})
    if raster_doc is not None:
        meta = raster_doc.get("meta") or {}
        plan["meta"]["mm_per_px"] = meta.get("mm_per_px")
        plan["meta"]["origin_px"] = meta.get("origin_px") or [0, 0]
        return
    if not graph.scale_mm_per_unit or not graph.origin_pt or clip is None:
        plan["meta"]["mm_per_px"] = graph.scale_mm_per_unit
        plan["meta"]["origin_px"] = [0, 0]
        return
    zoom = 2.0
    ox = (graph.origin_pt[0] - clip.x0) * zoom
    oy = (graph.origin_pt[1] - clip.y0) * zoom
    plan["meta"]["mm_per_px"] = graph.scale_mm_per_unit / zoom
    plan["meta"]["origin_px"] = [round(ox, 2), round(oy, 2)]


def _clip(extracted):
    living = extracted["living"]
    minx, miny, maxx, maxy = living.bounds
    for service in extracted.get("services") or []:
        sx0, sy0, sx1, sy1 = service["geom"].bounds
        minx, miny = min(minx, sx0), min(miny, sy0)
        maxx, maxy = max(maxx, sx1), max(maxy, sy1)
    return fitz.Rect(minx - 20, miny - 20, maxx + 20, maxy + 20)


def _graph_from_raster(doc: dict, flat_id: str) -> PlanGraph:
    from plan_graph import Evidence, OpeningHyp, RoomFace, WallHyp
    rooms = []
    walls = []
    openings = []
    for room in doc.get("rooms") or []:
        polygon = [(int(p[0]), int(p[1])) for p in room.get("polygon") or []]
        if len(polygon) < 3:
            continue
        rooms.append(RoomFace(
            id=room.get("id") or "room", label=room.get("label") or "", polygon=polygon,
            evidence=[Evidence("polygon", "raster", room.get("id") or "", 0.5, "inferred")]))
        for wall in room.get("walls") or []:
            walls.append(WallHyp(
                id=wall["id"], a=tuple(wall["a"]), b=tuple(wall["b"]),
                thickness_mm=int(wall.get("thickness_mm") or 100),
                wall_type=wall.get("type") or "partition"))
        for opening in room.get("openings") or []:
            openings.append(OpeningHyp(
                id=opening["id"], wall_id=opening.get("wall") or "", kind=opening.get("kind") or "door",
                center_mm=int(opening.get("center_mm") or 0), width_mm=int(opening.get("width_mm") or 800)))
    votes = (doc.get("meta") or {}).get("scale_votes") or []
    dimensions = []
    for vote in votes:
        if not isinstance(vote, (list, tuple)) or len(vote) < 1:
            continue
        dimensions.append(dict(mm_per_unit=float(vote[0]), value=vote[3] if len(vote) > 3 else "",
                               status="observed", method="raster-ocr"))
    scale = None
    if len(dimensions) >= 2:
        scales = [item["mm_per_unit"] for item in dimensions]
        if max(scales) <= min(scales) * 1.08:
            scale = sorted(scales)[len(scales) // 2]
    entry = doc.get("entry") or {}
    return PlanGraph(
        flat_id=flat_id, rooms=rooms, walls=walls, openings=openings,
        entry=dict(room=entry.get("room") or (rooms[0].id if rooms else ""), point=entry.get("point") or [0, 0]),
        dimensions=dimensions if scale is not None else [],
        scale_mm_per_unit=scale, hypothesis="raster",
    )


def _live_provider():
    from hkgeometry.vision_roles import DeepSeekVisionProvider
    return DeepSeekVisionProvider()


def _scrub(value):
    secret = {"api_key", "authorization", "apikey", "image", "images", "bytes"}
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if str(key).lower() not in secret}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return None
    return value
