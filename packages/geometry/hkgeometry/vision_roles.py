"""视觉模型只负责挑错和提动作。坐标仍然由几何决定。"""
from __future__ import annotations

import json
import re

ALLOWED_ACTIONS = {
    "move_group", "rotate_group", "resize_group", "resize_item",
    "change_relation", "change_door_wall", "change_hinge",
    "change_partition", "reject_candidate", "reject_architecture",
}


def parse_json(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"errors": ["model returned non-json"], "actions": [], "severe": []}
    return data if isinstance(data, dict) else {"errors": ["model JSON was not an object"]}


class VisionProvider:
    def complete(self, system: str, user: str, images: list[bytes]) -> dict:
        raise NotImplementedError


class FixtureProvider(VisionProvider):
    """测试和离线回放。不访问网络。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0
        self.last_user = ""
        self.last_images = 0

    def complete(self, system: str, user: str, images: list[bytes]) -> dict:
        self.calls += 1
        self.last_user = user
        self.last_images = len(images or [])
        return dict(self.payload)


class DeepSeekVisionProvider(VisionProvider):
    def __init__(self, client=None):
        self.client = client

    def complete(self, system: str, user: str, images: list[bytes]) -> dict:
        if self.client is None:
            from .llm_advisor import LLMClient
            self.client = LLMClient()
        if not self.client.api_key or self.client.api_key == "sk-local":
            return {"errors": ["no API key"], "actions": [], "severe": ["no_api_key"]}
        return parse_json(self.client.chat_images(system, user, images))


def _clean_actions(data: dict) -> list[dict]:
    out = []
    for action in data.get("actions") or []:
        if not isinstance(action, dict):
            continue
        kind = action.get("type") or action.get("action")
        if kind not in ALLOWED_ACTIONS:
            continue
        action = dict(action)
        action["type"] = kind
        # x/y 是下一轮求解的种子，不是可以直接写进图里的坐标。
        if "polygon" in action:
            action.pop("polygon", None)
            action["dropped_polygon"] = True
        out.append(action)
    return out


STRUCTURE_SYSTEM = (
    "You compare floor-plan images. Return JSON only. "
    "Report room labels, door wall sides, and adjacency disagreements. "
    "Do not invent millimetre coordinates."
)
CRITIC_SYSTEM = (
    "You review an annotated floor plan, including failed candidates. "
    "Return JSON only with keys problems, severe, actions. "
    "problems is a ranked list of objects with keys rank, ids, relation, "
    "transform, confidence, evidence. "
    "actions use move_group, rotate_group, resize_group, change_relation, "
    "change_door_wall, change_hinge, change_partition, or reject_candidate. "
    "change_partition must name one of the scheme ids you were given. "
    "Optional x, y, rot, and zone are candidate seeds. Geometry will snap "
    "and recheck them. Do not treat coordinates as a finished layout."
)


class FloorplanAuditor:
    """看原图、局部和叠图，只回答语义。不能写多边形或毫米坐标。"""

    def __init__(self, provider: VisionProvider):
        self.provider = provider

    def audit(self, images: list[bytes], notes: str) -> dict:
        data = self.provider.complete(FLOORPLAN_SYSTEM, notes, images)
        return _strip_geometry(data)


FLOORPLAN_SYSTEM = (
    "You audit a floor plan. Return JSON only. "
    "Keys: rooms, adjacency, entry_room, openings, fixtures, dimensions, "
    "disagreements, select_hypothesis, reject_hypotheses, evidence. "
    "rooms are labels and counts. openings classify door, window, and swing. "
    "select_hypothesis must be one of the ids you were given. "
    "Do not return polygons, millimetre coordinates, or a scale."
)


def _strip_geometry(data: dict) -> dict:
    if not isinstance(data, dict):
        return {"disagreements": ["model returned non-json"], "select_hypothesis": None}
    blocked = {"polygon", "polygons", "x", "y", "scale", "mm_per_px", "coordinates"}

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in blocked}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    cleaned = clean(data)
    cleaned.setdefault("disagreements", [])
    cleaned.setdefault("select_hypothesis", None)
    cleaned.setdefault("rooms", [])
    return cleaned


class StructureAuditor:
    def __init__(self, provider: VisionProvider):
        self.provider = provider

    def audit(self, images: list[bytes], notes: str = "") -> dict:
        data = self.provider.complete(STRUCTURE_SYSTEM, notes or "Compare these plans.", images)
        data["actions"] = _clean_actions(data)
        data.setdefault("rooms", [])
        data.setdefault("severe", [])
        return data


class LayoutCritic:
    def __init__(self, provider: VisionProvider):
        self.provider = provider

    def critique(self, images: list[bytes], verifier_errors: list[str],
                 diagnostics: list | None = None) -> dict:
        user = json.dumps({
            "verifier_errors": list(verifier_errors or []),
            "diagnostics": diagnostics or [],
            "note": "Red areas are door swings. Arrows are furniture fronts. "
                    "Dashed boxes are approach zones. Failed candidates are included.",
        }, ensure_ascii=False)
        data = self.provider.complete(CRITIC_SYSTEM, user, images)
        data["actions"] = _clean_actions(data)
        data.setdefault("severe", [])
        data.setdefault("problems", [])
        return data
