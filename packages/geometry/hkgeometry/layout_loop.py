"""提议、验收、视觉批评、再解。模型坐标只当种子，几何不通过就不能发表。"""
from __future__ import annotations

from .annotate import render_annotated
from .circulation import artery_clear, blocked_approaches, unreachable_rooms
from .groups import generate_candidates
from .program import validate_program
from .progressive import humanistic_errors, solve_progressive
from .structure import review_status, validate_structure
from .vision_roles import LayoutCritic


def hard_errors(flat, house=None) -> list[str]:
    errors = list(validate_structure(flat))
    errors.extend(validate_program(flat))
    if house is None:
        return errors
    placed = {rid: lay.placements for rid, lay in house.by_room.items()}
    errors.extend("unreachable %s" % rid for rid in unreachable_rooms(flat, placed))
    if not artery_clear(flat, placed, 900):
        errors.append("main artery is disconnected or narrower than 900mm")
    errors.extend(blocked_approaches(flat, placed))
    errors.extend(humanistic_errors(flat, placed))
    if house.unplaced:
        errors.append("unplaced " + ", ".join(house.unplaced))
    return errors


def publishable(errors: list[str], critic: dict | None) -> bool:
    if errors:
        return False
    if critic is None:
        return False
    if critic.get("severe"):
        return False
    return True


def _snapshot(flat):
    return [(op, op.wall, dict(op.swing) if op.swing else None)
            for room in flat.rooms for op in room.openings]


def _restore(saved) -> None:
    for op, wall, swing in saved:
        op.wall = wall
        op.swing = None if swing is None else dict(swing)


def translate_actions(actions: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """动作变成下一轮的区域、朝向、种子。不直接改家具坐标。"""
    accepted, rejected = [], []
    constraints = {"seeds": {}, "zones": {}, "rotations": {}, "resize": {}, "relations": []}
    for action in actions or []:
        if not isinstance(action, dict):
            rejected.append(action)
            continue
        kind = action.get("type") or action.get("action")
        target = action.get("inst") or action.get("group") or action.get("id")
        if kind in ("reject_candidate", "reject_architecture"):
            accepted.append(action)
            constraints["reject"] = True
            continue
        if kind == "move_group":
            if target and ("x" in action or "y" in action):
                constraints["seeds"][target] = (
                    float(action.get("x", 0)), float(action.get("y", 0)), action.get("rot"))
            if target and action.get("zone"):
                constraints["zones"][target] = tuple(action["zone"])
            if target and action.get("rot") is not None:
                constraints["rotations"][target] = int(action["rot"]) % 360
            accepted.append(action)
            continue
        if kind == "rotate_group":
            if target is None or action.get("rot") is None and action.get("rotation") is None:
                rejected.append(action)
                continue
            constraints["rotations"][target] = int(action.get("rot", action.get("rotation"))) % 360
            accepted.append(action)
            continue
        if kind in ("resize_group", "resize_item"):
            if target is None:
                rejected.append(action)
                continue
            spec = {}
            if action.get("scale") is not None:
                spec["scale"] = float(action["scale"])
            if action.get("max_w") is not None:
                spec["max_w"] = int(action["max_w"])
            if not spec:
                spec["scale"] = 0.85
            constraints["resize"][target] = spec
            accepted.append(action)
            continue
        if kind == "change_relation":
            relation = action.get("relation") or action.get("name")
            if not relation:
                rejected.append(action)
                continue
            constraints["relations"].append(str(relation))
            accepted.append(action)
            continue
        if kind == "change_partition":
            scheme = action.get("scheme") or action.get("id")
            if not scheme:
                rejected.append(action)
                continue
            constraints["scheme"] = str(scheme)
            accepted.append(action)
            continue
        if kind in ("change_hinge", "change_door_wall", "reject_architecture"):
            accepted.append(action)
            continue
        rejected.append(action)
    return accepted, rejected, constraints


def apply_actions(flat, house, actions: list[dict]):
    """执行能验证的建筑动作，其余变成约束。坐标种子不会直接落图。"""
    accepted, rejected, constraints = translate_actions(actions)
    kept, dropped = [], []
    saved = _snapshot(flat)
    for action in accepted:
        kind = action.get("type")
        if kind == "change_hinge":
            door_id = action.get("door") or action.get("id")
            hinge = (action.get("swing") or {}).get("hinge")
            direction = (action.get("swing") or {}).get("dir")
            hit = False
            for room in flat.rooms:
                for op in room.openings:
                    if op.id == door_id and hinge and direction:
                        op.swing = {"hinge": hinge, "dir": direction}
                        hit = True
            if not hit:
                dropped.append(action)
                continue
            if validate_structure(flat):
                _restore(saved)
                dropped.append(action)
                continue
            kept.append(action)
            saved = _snapshot(flat)
            continue
        if kind == "change_door_wall":
            door_id = action.get("door") or action.get("id")
            wall_id = action.get("wall")
            hit = False
            for room in flat.rooms:
                if wall_id and wall_id not in {w.id for w in room.walls}:
                    continue
                for op in room.openings:
                    if op.id == door_id and wall_id:
                        op.wall = wall_id
                        hit = True
            if not hit or validate_structure(flat):
                _restore(saved)
                dropped.append(action)
                continue
            kept.append(action)
            saved = _snapshot(flat)
            continue
        kept.append(action)
    rejected.extend(dropped)
    return kept, rejected, constraints


def merge_constraints(base: dict | None, extra: dict | None) -> dict:
    base = base or {}
    extra = extra or {}
    out = {
        "seeds": dict(base.get("seeds") or {}),
        "zones": dict(base.get("zones") or {}),
        "rotations": dict(base.get("rotations") or {}),
        "resize": dict(base.get("resize") or {}),
        "relations": list(base.get("relations") or []),
        "scheme": extra.get("scheme") or base.get("scheme"),
    }
    for key in ("seeds", "zones", "rotations", "resize"):
        out[key].update(extra.get(key) or {})
    for relation in extra.get("relations") or []:
        if relation not in out["relations"]:
            out["relations"].append(relation)
    return out


def scrub_audit(value):
    """审计文件可以留错误和动作，不能留密钥或图片字节。"""
    banned = ("key", "secret", "token", "authorization", "password")
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            low = key.lower()
            if any(word in low for word in banned) or low in ("image", "images"):
                continue
            out[key] = scrub_audit(item)
        return out
    if isinstance(value, list):
        return [scrub_audit(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


def _public_history(row: dict) -> dict:
    """审计记录里不留图片字节，也不留密钥。"""
    skip = {"image", "images", "api_key", "authorization", "key"}
    clean = {}
    for key, value in row.items():
        if key in skip or "key" in key.lower() or "secret" in key.lower():
            continue
        if isinstance(value, (bytes, bytearray)):
            clean[key + "_bytes"] = len(value)
            continue
        clean[key] = value
    return clean


def run_layout_loop(flat, items, assignment, critic: LayoutCritic | None = None,
                    images: list[bytes] | None = None, max_rounds: int = 3,
                    per_round: int = 2):
    structural = validate_structure(flat)
    if structural:
        return None, {
            "hard": structural,
            "severe": [],
            "status": review_status(structural),
            "publishable": False,
            "rounds": 0,
            "history": [],
            "applied_constraints": {},
        }
    applied = {}
    best = None
    best_rank = None
    stale = 0
    history = []
    last_house = None
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        improved = False
        houses = generate_candidates(flat, items, assignment, applied, per_round)
        for house in houses:
            last_house = house
            errors = hard_errors(flat, house)
            picture = images
            if picture is None and critic is not None:
                picture = [render_annotated(flat, house, errors)]
            review = {"severe": [], "actions": [], "problems": []}
            if critic is not None:
                review = critic.critique(picture or [], errors, diagnostics=errors)
            accepted, rejected, constraints = apply_actions(flat, house, review.get("actions") or [])
            history.append(_public_history({
                "round": rounds,
                "hard": errors,
                "severe": list(review.get("severe") or []),
                "problems": review.get("problems") or [],
                "accepted": accepted,
                "rejected": rejected,
                "constraints": constraints,
                "image": (picture or [b""])[0] if picture else b"",
            }))
            if constraints.get("reject"):
                continue
            if not errors and not (review.get("severe") or []):
                rank = (len(house.unplaced), len(review.get("problems") or []))
                if best_rank is None or rank < best_rank:
                    best = house
                    best_rank = rank
                    improved = True
            applied = merge_constraints(applied, constraints)
        if improved:
            stale = 0
        else:
            stale += 1
        if best is not None and stale >= 2:
            break
    house = best or last_house
    if house is None:
        house = solve_progressive(flat, items, assignment)
    errors = hard_errors(flat, house)
    final_review = {"severe": [], "problems": []}
    if critic is not None:
        picture = images if images is not None else [render_annotated(flat, house, errors)]
        final_review = critic.critique(picture, errors, diagnostics=errors)
        if final_review.get("severe"):
            errors = list(errors)
    ok = publishable(errors, final_review if critic is not None else None)
    return house, {
        "hard": errors,
        "severe": list(final_review.get("severe") or []),
        "problems": final_review.get("problems") or [],
        "status": "verified" if ok else review_status(errors),
        "publishable": ok,
        "rounds": rounds,
        "history": [_public_history(row) for row in history],
        "applied_constraints": applied,
        "groups": house.reports.get("groups") if house is not None else [],
    }


def _items_from_program(doc: dict):
    from .types import Placement
    items, assign = [], {}
    for row in ((doc.get("meta") or {}).get("program") or {}).get("furniture") or []:
        items.append(Placement(
            inst=row["inst"], sku=row.get("sku", "ref"), category=row["category"],
            w=int(row["w"]), d=int(row["d"]), h=int(row["h"])))
        assign[row["inst"]] = row["room"]
    return items, assign


def design_from_floorplan(doc: dict, critic: LayoutCritic | None = None, max_rounds: int = 2):
    """原图没有睡房时先砌隔墙和门，再摆家具。模型只能改选方案号。"""
    from .house import flat_from_dict
    from .partitions import ensure_bedrooms, propose_bedroom_partitions, rank_partition_plans

    catalog = {(item.get("meta") or {}).get("scheme"): item for item in propose_bedroom_partitions(doc)}
    ranked = [catalog[name] for name in [
        row["kind"] for row in rank_partition_plans(list(catalog.values()))
    ] if name in catalog]
    if not ranked:
        ranked = [ensure_bedrooms(doc, critic)]
    two = [item for item in ranked if (item.get("meta") or {}).get("scheme", "").startswith("bed-2")]
    one = [item for item in ranked if (item.get("meta") or {}).get("scheme", "").startswith("bed-1")]
    candidates = (one[-2:] + one[:2] + two[:1]) if (two or one) else ranked[:4]
    dedup = []
    for item in candidates:
        if item not in dedup:
            dedup.append(item)
    best = None
    for chosen in dedup[:5]:
        flat = flat_from_dict(chosen)
        items, assign = _items_from_program(chosen)
        house, report = run_layout_loop(
            flat, items, assign, critic=critic, max_rounds=max_rounds, per_round=1)
        placed = {p.inst for lay in (house.by_room.values() if house is not None else []) for p in lay.placements}
        unplaced = len(house.unplaced) if house is not None else 99
        bed_missing = 0 if any(inst.startswith("#bed") for inst in placed) else 1
        beds = sum(1 for room in chosen["rooms"] if str(room.get("id", "")).startswith("bedroom"))
        rank = (bed_missing, unplaced, -beds)
        if best is None or rank < best[0]:
            best = (rank, chosen, house, report)
        if bed_missing == 0 and unplaced == 0:
            break
        wanted = (report.get("applied_constraints") or {}).get("scheme")
        if wanted in catalog and catalog[wanted] not in dedup[:5]:
            dedup.append(catalog[wanted])
    return best[1], best[2], best[3]
