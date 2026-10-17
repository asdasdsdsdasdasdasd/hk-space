"""户型理解：几何门槛、PDF 变换、以及青富 B36 的真实图纸。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "floorplan"))
sys.path.insert(0, str(ROOT / "packages" / "geometry"))

from hkgeometry.house import flat_from_dict
from hkgeometry.vision_roles import FixtureProvider, FloorplanAuditor
from pdf_extract import extract_vector_plan, open_pdf
from plan_graph import Evidence, PlanGraph, RoomFace, graph_errors
from understand import understand_floorplan

BROCHURE = ROOT / "out" / "ref" / "Brochure-Ching-Fu.pdf"
TRUTH = Path(__file__).resolve().parent / "fixtures" / "chingfu_b36_truth.json"


class GraphTests(unittest.TestCase):
    def test_overlap_is_rejected(self):
        rooms = [
            RoomFace("a", "客廳", [(0, 0), (2000, 0), (2000, 2000), (0, 2000)],
                     [Evidence("polygon", "test", "a", 1, "observed")]),
            RoomFace("b", "浴室", [(500, 500), (1800, 500), (1800, 1800), (500, 1800)],
                     [Evidence("polygon", "test", "b", 1, "observed")]),
        ]
        graph = PlanGraph("overlap", rooms, [], [], {"room": "a", "point": [100, 100]},
                          dimensions=[{"status": "observed", "mm_per_unit": 1},
                                      {"status": "observed", "mm_per_unit": 1}],
                          scale_mm_per_unit=1)
        errors = graph_errors(graph)
        self.assertTrue(any("overlap" in item for item in errors))

    def test_fixed_services_stay_on_the_room(self):
        doc = dict(
            flat_id="svc", units="mm",
            rooms=[dict(id="bath", label="浴室", polygon=[[0, 0], [2000, 0], [2000, 2000], [0, 2000]],
                        walls=[], openings=[])],
            connections=[],
            fixed_services=[dict(id="basin", kind="basin", point=[400, 400], room="bath")],
            entry=dict(room="bath", point=[1000, 100]),
            meta={},
        )
        flat = flat_from_dict(doc)
        self.assertEqual(len(flat.room("bath").services), 1)
        self.assertEqual(flat.room("bath").services[0].kind, "basin")


class AuditorTests(unittest.TestCase):
    def test_coordinates_are_dropped(self):
        provider = FixtureProvider({
            "select_hypothesis": "wall-cells",
            "polygon": [[0, 0], [9, 9]],
            "scale": 12,
            "rooms": [{"label": "浴室", "x": 10, "y": 20}],
        })
        report = FloorplanAuditor(provider).audit([], "notes")
        self.assertEqual(report["select_hypothesis"], "wall-cells")
        self.assertNotIn("polygon", report)
        self.assertNotIn("scale", report)
        self.assertNotIn("x", report["rooms"][0])


class PdfTests(unittest.TestCase):
    def test_show_pdf_page_transform_is_honoured(self):
        doc = fitz.open()
        page = doc.new_page(width=400, height=400)
        page.draw_rect(fitz.Rect(1, 1, 2, 2), color=(1, 1, 1), fill=(1, 1, 1))
        doc.update_stream(page.get_contents()[0], b"q\n2 0 0 2 30 40 cm\n0.56 0.83 0.81 rg\n10 20 70 50 re\nf\nQ\n")
        page.insert_text(fitz.Point(80, 260), "36", fontsize=14)
        page.insert_text(fitz.Point(78, 274), "LIV/DIN", fontsize=8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scaled.pdf"
            doc.save(path)
            doc.close()
            opened = open_pdf(path)
            try:
                extracted = extract_vector_plan(opened[0], "36")
            finally:
                opened.close()
        bounds = extracted["living"].bounds
        self.assertAlmostEqual(bounds[2] - bounds[0], 140, delta=4)
        self.assertAlmostEqual(bounds[3] - bounds[1], 100, delta=4)
        self.assertAlmostEqual(bounds[0], 50, delta=4)

    def test_missing_dimensions_stay_unverified(self):
        src = fitz.open()
        page = src.new_page(width=300, height=300)
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(40, 40, 180, 160))
        shape.finish(fill=(0.56, 0.83, 0.81))
        shape.commit()
        page.insert_text(fitz.Point(80, 100), "36", fontsize=16)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.pdf"
            out = Path(tmp) / "plain.json"
            src.save(path)
            src.close()
            result = understand_floorplan(path, out, unit_hint="36", max_rounds=1)
            audit = json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "unverified")
        self.assertNotIn("api_key", json.dumps(audit))
        self.assertTrue(result["plan"]["rooms"])


@unittest.skipUnless(BROCHURE.exists(), "brochure pdf is not in out/ref")
class ChingFuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp()) / "b36.json"
        cls.result = understand_floorplan(BROCHURE, cls.out, page=23, unit_hint="B36", max_rounds=1)
        cls.again = understand_floorplan(BROCHURE, cls.out, page=23, unit_hint="B36", max_rounds=1)

    def test_shape_matches_the_brochure(self):
        truth = json.loads(TRUTH.read_text(encoding="utf-8"))
        plan = self.result["plan"]
        self.assertEqual(self.result["status"], "verified")
        self.assertTrue(plan["meta"]["layout_allowed"])
        rooms = {room["id"]: room for room in plan["rooms"]}
        self.assertEqual(set(rooms), set(truth["rooms"]))
        living = rooms["living"]["polygon"]
        self.assertGreater(_concave(living), 1.15)
        bath = rooms["bath"]["polygon"]
        kitchen = rooms["kitchen"]["polygon"]
        span = max(p[0] for p in living) - min(p[0] for p in living)
        left = min(p[0] for p in living)
        bath_x = sum(p[0] for p in bath) / len(bath)
        kitchen_x = sum(p[0] for p in kitchen) / len(kitchen)
        bath_y = sum(p[1] for p in bath) / len(bath)
        self.assertLess(bath_x, kitchen_x)
        self.assertGreater((bath_x - left) / span, 0.35)
        self.assertGreater((kitchen_x - left) / span, 0.55)
        from shapely.geometry import Point as ShpPoint
        from shapely.geometry import Polygon as ShpPolygon
        self.assertFalse(ShpPolygon(living).covers(ShpPoint(bath_x, bath_y)))
        entry = next(op for op in rooms["living"]["openings"] if op["id"] == "d_entry")
        wall = next(w for w in rooms["living"]["walls"] if w["id"] == entry["wall"])
        right = max(p[0] for p in living)
        self.assertGreaterEqual(min(wall["a"][0], wall["b"][0]), right - 120)
        along = entry["center_mm"]
        ax, ay = wall["a"]
        bx, by = wall["b"]
        length = max(1.0, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
        cy = ay + (by - ay) * along / length
        bath_top = min(p[1] for p in bath)
        self.assertLess(abs(cy - bath_top), 900)
        links = {tuple(sorted(pair)) for pair in truth["adjacency"]}
        got = {tuple(sorted((item["a"], item["b"]))) for item in plan["connections"]}
        self.assertTrue(links <= got)

    def test_signature_is_stable_and_audit_is_key_free(self):
        self.assertEqual(self.result["audit"]["signature"], self.again["audit"]["signature"])
        blob = json.dumps(self.result["audit"])
        self.assertNotIn("api_key", blob)
        self.assertNotIn("sk-", blob)

    def test_auditor_cannot_move_the_polygon_and_rejection_stops(self):
        before = self.result["plan"]["rooms"][0]["polygon"]
        provider = FixtureProvider({
            "select_hypothesis": "wall-cells",
            "reject_hypotheses": ["wall-cells", "labels-swapped"],
            "polygon": [[1, 2], [3, 4]],
            "x": 999,
        })
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rejected.json"
            result = understand_floorplan(
                BROCHURE, out, page=23, unit_hint="B36", provider=provider, max_rounds=2)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["plan"]["rooms"][0]["polygon"], before)
        self.assertTrue(any("rejected" in item for item in result["audit"]["failures"]))
        self.assertNotIn("polygon", result["audit"]["llm"][0])


@unittest.skipUnless(BROCHURE.exists(), "brochure pdf is not in out/ref")
class ChingFuNeighborTests(unittest.TestCase):
    """B31/B32/B35 sit on the same sheet as B36; the extractor cannot assume B36's orientation."""

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        tmp = Path(tempfile.mkdtemp())
        for unit in ("B31", "B32", "B35"):
            cls.results[unit] = understand_floorplan(
                BROCHURE, tmp / ("%s.json" % unit), page=23, unit_hint=unit, max_rounds=1)

    def test_each_unit_has_living_bath_and_kitchen(self):
        from shapely.geometry import Polygon as ShpPolygon

        for unit, result in self.results.items():
            rooms = {room["id"]: room for room in result["plan"]["rooms"]}
            self.assertEqual(set(rooms), {"living", "bath", "kitchen"}, unit)
            self.assertGreater(_concave(rooms["living"]["polygon"]), 1.05, unit)
            self.assertTrue(any(op["id"] == "d_entry" for op in rooms["living"]["openings"]), unit)
            living = ShpPolygon(rooms["living"]["polygon"])
            bath = ShpPolygon(rooms["bath"]["polygon"])
            kitchen = ShpPolygon(rooms["kitchen"]["polygon"])
            self.assertGreater(living.area, bath.area, unit)
            self.assertGreater(living.area, kitchen.area, unit)
            self.assertFalse(living.covers(bath.centroid), unit)
            self.assertFalse(living.covers(kitchen.centroid), unit)


class BedroomPartitionTests(unittest.TestCase):
    def test_b36_shell_gains_a_bedroom_and_a_bed(self):
        from hkgeometry.layout_loop import design_from_floorplan, hard_errors
        from hkgeometry.vision_roles import FixtureProvider, LayoutCritic

        source = json.loads((ROOT / "out" / "plan_hk_chingfu_b36_understood.json").read_text(encoding="utf-8"))
        self.assertFalse(any(room["id"].startswith("bedroom") for room in source["rooms"]))
        critic = LayoutCritic(FixtureProvider({
            "severe": [], "problems": [], "actions": [],
        }))
        doc, house, report = design_from_floorplan(source, critic=critic, max_rounds=1)
        rooms = {room["id"] for room in doc["rooms"]}
        self.assertIn("bedroom_w", rooms)
        self.assertTrue(any(conn.get("door_id") == "d_bedroom_w" for conn in doc["connections"]))
        self.assertIsNotNone(house)
        placed = {p.inst for lay in house.by_room.values() for p in lay.placements}
        self.assertIn("#bed_w", placed)
        errors = hard_errors(flat_from_dict(doc), house)
        self.assertFalse(any("overlap" in item or "entry" in item or "shell" in item for item in errors))
        placed_cats = {p.category for lay in house.by_room.values() for p in lay.placements}
        self.assertIn("bed", placed_cats)
        self.assertIn("dining_table", placed_cats)
        self.assertIn("toilet", placed_cats)
        self.assertIn("fridge", placed_cats)


def _concave(polygon) -> float:
    from shapely.geometry import Polygon
    poly = Polygon(polygon)
    return poly.envelope.area / poly.area


if __name__ == "__main__":
    unittest.main()
