"""安柏苑两房方案：门、通道、隔墙排名。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packages", "geometry"))

from hkgeometry.circulation import approach_zone, artery_clear, blocked_approaches, unreachable_rooms  # noqa: E402
from hkgeometry.groups import snap_seed  # noqa: E402
from hkgeometry.house import flat_from_dict  # noqa: E402
from hkgeometry.layout_loop import apply_actions, hard_errors, publishable, run_layout_loop, scrub_audit  # noqa: E402
from hkgeometry.onpak05_reference import (  # noqa: E402
    ExtentMismatch, build_blocked, build_reference, reconcile_extents,
)
from hkgeometry.partitions import rank_partition_plans  # noqa: E402
from hkgeometry.program import opening_side, validate_program  # noqa: E402
from hkgeometry.progressive import _faces_aisle, _hits_door, _pair_ok, approaches_conflict, dining_slots, humanistic_errors, solve_progressive  # noqa: E402
from hkgeometry.structure import validate_structure  # noqa: E402
from hkgeometry.types import Placement  # noqa: E402
from hkgeometry.vision_eval import score_structure  # noqa: E402
from hkgeometry.vision_roles import FixtureProvider, LayoutCritic  # noqa: E402


class Onpak05Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = build_reference()
        cls.flat = flat_from_dict(cls.doc)

    def test_extent_mismatch_is_not_scaled_away(self):
        report = reconcile_extents()
        self.assertEqual(report["scale"], 1.0)
        self.assertTrue(report["errors"])
        with self.assertRaises(ExtentMismatch):
            reconcile_extents(scale=8745 / 8530)

    def test_swing_survives_loading(self):
        raw = {
            "flat_id": "swing",
            "rooms": [{
                "id": "only", "label": "客廳", "polygon": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]],
                "walls": [{
                    "id": "w", "a": [0, 0], "b": [3000, 0], "type": "partition",
                    "thickness_mm": 100, "movable": True,
                    "openings": [{
                        "id": "d", "kind": "door", "wall": "w", "center_mm": 1500,
                        "width_mm": 800, "swing": {"dir": "out", "hinge": "b"},
                    }],
                }],
                "openings": [{
                    "id": "d", "kind": "door", "wall": "w", "center_mm": 1500,
                    "width_mm": 800, "swing": {"dir": "out", "hinge": "b"},
                }],
            }],
            "entry": {"room": "only", "point": [1500, 200]},
        }
        flat = flat_from_dict(raw)
        door = flat.room("only").openings[0]
        self.assertEqual(door.swing, {"dir": "out", "hinge": "b"})
        self.assertTrue(flat.room("only").walls[0].movable)

    def test_reference_program_and_door_sides(self):
        self.assertEqual(validate_program(self.flat), [])
        self.assertEqual(opening_side(self.flat.room("bath"), "d_bath"), "east")
        self.assertEqual(opening_side(self.flat.room("bedroom_w"), "d_bed_w"), "east")
        self.assertEqual(opening_side(self.flat.room("bedroom_e"), "d_bed_e"), "west")
        self.assertEqual(opening_side(self.flat.room("kitchen"), "d_kit"), "east")
        self.assertEqual(validate_structure(self.flat), [])
        funcs = {}
        from hkgeometry.house import infer_room_functions
        funcs = infer_room_functions(self.flat)
        self.assertEqual(sum(1 for f in funcs.values() if f == "bedroom"), 2)
        for room in self.flat.rooms:
            if funcs[room.id] == "bedroom":
                self.assertGreaterEqual(room.geo().area / 1e6, 5.0)

    def test_empty_reference_is_connected(self):
        self.assertEqual(unreachable_rooms(self.flat, {}), [])
        self.assertTrue(artery_clear(self.flat, {}, 900))

    def test_sofa_across_the_hall_is_rejected(self):
        living = self.flat.room("living")
        minx, miny, maxx, maxy = living.geo().bounds
        # 一条横过客厅包围盒的家具。它不需要填满异形轮廓，只要切断入口。
        bar = Placement(inst="#sofa", sku="block", category="sofa",
                        x=int(minx), y=int((miny + maxy) / 2),
                        w=int(maxx - minx), d=500, h=750)
        lost = unreachable_rooms(self.flat, {"living": [bar]}, person_mm=600)
        self.assertTrue(lost or not artery_clear(self.flat, {"living": [bar]}, 900))

    def test_solver_keeps_the_route_and_both_beds(self):
        items, assign = [], {}
        for row in self.doc["meta"]["program"]["furniture"]:
            items.append(Placement(inst=row["inst"], sku="ref", category=row["category"],
                                   w=row["w"], d=row["d"], h=row["h"]))
            assign[row["inst"]] = row["room"]
        house = solve_progressive(self.flat, items, assign)
        self.assertEqual(house.unplaced, [])
        placed = {rid: lay.placements for rid, lay in house.by_room.items()}
        self.assertEqual(hard_errors(self.flat, house), [])
        beds = [p for ps in placed.values() for p in ps if p.category == "bed"]
        self.assertEqual(len(beds), 2)
        self.assertEqual(unreachable_rooms(self.flat, placed), [])
        self.assertTrue(artery_clear(self.flat, placed, 900))
        living = placed["living"]
        table = next(p for p in living if p.category == "dining_table")
        chairs = [p for p in living if p.category == "chair"]
        self.assertEqual(len(chairs), 4)
        slots = dining_slots(table)
        used = set()
        for chair in chairs:
            match = [s for s in slots if abs(chair.x - s[0]) < 5 and abs(chair.y - s[1]) < 5 and chair.rot == s[2]]
            self.assertEqual(len(match), 1)
            used.add(match[0])
        self.assertEqual(len(used), 4)
        for item in living:
            self.assertFalse(_hits_door(self.flat.room("living"), item.footprint()))
        sofa = next(p for p in living if p.category == "sofa")
        tv = next(p for p in living if p.category == "tv_bench")
        self.assertTrue(_pair_ok(sofa, tv, 1400, 4000))
        kitchen = self.flat.room("kitchen")
        for item in placed["kitchen"]:
            if item.category in ("fridge", "washing_machine", "kitchen_counter"):
                self.assertTrue(_faces_aisle(kitchen, item))
        self.assertEqual(blocked_approaches(self.flat, placed), [])
        self.assertEqual(humanistic_errors(self.flat, placed), [])

    def test_enclosed_basin_is_rejected(self):
        bath = self.flat.room("bath")
        minx, miny, maxx, maxy = bath.usable_geo().bounds
        basin = Placement(inst="#basin", sku="t", category="basin",
                          x=int((minx + maxx) / 2 - 200), y=int((miny + maxy) / 2 - 150),
                          w=400, d=300, h=800, rot=0)
        toilet = Placement(inst="#toilet", sku="t", category="toilet",
                           x=basin.x - 350, y=basin.y, w=350, d=500, h=700, rot=90)
        shower = Placement(inst="#shower", sku="t", category="shower",
                           x=basin.x + 350, y=basin.y - 200, w=700, d=700, h=2000, rot=0)
        zone = approach_zone(basin)
        self.assertIsNotNone(zone)
        blocked = zone.intersection(toilet.footprint()).area + zone.intersection(shower.footprint()).area
        self.assertGreater(blocked, 0)

    def test_model_coordinates_are_seeds_not_commits(self):
        critic = LayoutCritic(FixtureProvider({
            "severe": ["basin is enclosed"],
            "actions": [{"type": "move_group", "group": "bath", "x": -99999, "y": -99999}],
        }))
        house, report = run_layout_loop(
            self.flat, [], {}, critic=critic, max_rounds=1, per_round=1)
        self.assertFalse(report["publishable"])
        self.assertGreaterEqual(critic.provider.calls, 1)
        accepted, rejected, constraints = apply_actions(
            self.flat, house, critic.provider.payload["actions"])
        self.assertEqual(rejected, [])
        self.assertTrue(accepted)
        self.assertEqual(constraints["seeds"]["bath"][0], -99999)
        for lay in (house.by_room or {}).values():
            for item in lay.placements:
                self.assertNotEqual((item.x, item.y), (-99999, -99999))
        snapped = snap_seed(self.flat.room("living"), Placement(
            inst="#sofa", sku="t", category="sofa", w=1400, d=700, h=750), -99999, -99999, 0)
        self.assertIsNotNone(snapped)
        self.assertNotEqual((snapped[0], snapped[1]), (-99999, -99999))
        box = Placement(inst="#sofa", sku="t", category="sofa", x=snapped[0], y=snapped[1],
                        rot=snapped[2], w=1400, d=700, h=750)
        self.assertGreater(
            self.flat.room("living").usable_geo().intersection(box.footprint()).area,
            0.9 * box.footprint().area)
        self.assertFalse(publishable(["still broken"], {"severe": []}))

    def test_vision_metrics(self):
        truth = {"rooms": [{"id": "kitchen", "label": "廚房",
                            "doors": [{"id": "d_kit", "wall_side": "east"}],
                            "adjacent_to": ["living"]}],
                 "severe": ["wrong_door"]}
        predicted = {"rooms": [{"id": "kitchen", "label": "廚房",
                                "doors": [{"id": "d_kit", "wall_side": "north"}],
                                "adjacent_to": ["living"]}],
                     "severe": ["wrong_door", "invented"]}
        score = score_structure(predicted, truth)
        self.assertEqual(score["room_label_accuracy"], 1.0)
        self.assertEqual(score["door_wall_accuracy"], 0.0)
        self.assertEqual(score["adjacency_accuracy"], 1.0)
        self.assertEqual(score["severe_error_recall"], 1.0)
        self.assertEqual(score["false_corrections"], 1)

    def test_blocked_plan_is_not_ranked_and_reference_wins(self):
        ranked = rank_partition_plans()
        kinds = [row["kind"] for row in ranked]
        self.assertNotIn("blocked", kinds)
        self.assertGreaterEqual(len(ranked), 2)
        self.assertEqual(kinds[0], "reference")
        blocked = flat_from_dict(build_blocked())
        self.assertTrue(validate_program(blocked) or unreachable_rooms(blocked, {}))

    def _program_items(self):
        items, assign = [], {}
        for row in self.doc["meta"]["program"]["furniture"]:
            items.append(Placement(inst=row["inst"], sku="ref", category=row["category"],
                                   w=row["w"], d=row["d"], h=row["h"]))
            assign[row["inst"]] = row["room"]
        return items, assign

    def test_partial_dining_and_trapped_bath_are_illegal(self):
        table = Placement(inst="#dining", sku="t", category="dining_table",
                          x=4400, y=4100, rot=90, w=1600, d=800, h=750)
        chair = Placement(inst="#chair1", sku="t", category="chair",
                          x=4575, y=3600, rot=0, w=450, d=450, h=780)
        errors = humanistic_errors(self.flat, {"living": [table, chair]})
        self.assertTrue(any("incomplete" in e for e in errors))
        bath = self.flat.room("bath")
        basin = Placement(inst="#basin", sku="t", category="basin",
                          x=2200, y=2500, w=600, d=450, h=800, rot=0)
        toilet = Placement(inst="#toilet", sku="t", category="toilet",
                           x=basin.x - 200, y=basin.y - 100, w=700, d=400, h=700, rot=90)
        shower = Placement(inst="#shower", sku="t", category="shower",
                           x=basin.x + 200, y=basin.y - 100, w=800, d=800, h=2000, rot=0)
        self.assertTrue(approaches_conflict(bath, [basin, toilet, shower]))

    def test_sofa_must_face_the_tv_both_ways(self):
        sofa = Placement(inst="#sofa", sku="t", category="sofa", x=1000, y=1000,
                         w=1400, d=700, h=750, rot=0)
        tv = Placement(inst="#tv", sku="t", category="tv_bench", x=1100, y=2800,
                       w=1200, d=400, h=450, rot=180)
        self.assertTrue(_pair_ok(sofa, tv, 1400, 4000))
        tv.rot = 0
        self.assertFalse(_pair_ok(sofa, tv, 1400, 4000))

    def test_critic_sees_failures_and_actions_retarget_the_solve(self):
        provider = FixtureProvider({
            "severe": ["sofa blocks the hall"],
            "problems": [{"rank": 1, "ids": ["#huge"], "relation": "keep the artery",
                          "confidence": 0.9, "evidence": "the desk fills the room"}],
            "actions": [{"type": "rotate_group", "group": "living", "inst": "#sofa", "rot": 90}],
        })
        huge = Placement(inst="#huge", sku="t", category="desk", w=50000, d=50000, h=700)
        _house, report = run_layout_loop(
            self.flat, [huge], {"#huge": "living"}, critic=LayoutCritic(provider),
            max_rounds=1, per_round=1)
        self.assertGreaterEqual(provider.calls, 1)
        self.assertIn("unplaced", provider.last_user)
        self.assertFalse(report["publishable"])
        self.assertEqual(report["applied_constraints"]["rotations"]["#sofa"], 90)
        items, assign = self._program_items()
        free = solve_progressive(self.flat, items, assign)
        forced = solve_progressive(
            self.flat, items, assign, constraints={"rotations": {"living": 90}})
        free_sig = sorted((p.inst, p.x, p.y, p.rot) for lay in free.by_room.values() for p in lay.placements)
        forced_sig = sorted((p.inst, p.x, p.y, p.rot) for lay in forced.by_room.values() for p in lay.placements)
        self.assertNotEqual(free_sig, forced_sig)

    def test_clean_layout_is_publishable_and_audit_has_no_secrets(self):
        provider = FixtureProvider({"severe": [], "problems": [], "actions": []})
        items, assign = self._program_items()
        house, report = run_layout_loop(
            self.flat, items, assign, critic=LayoutCritic(provider),
            max_rounds=1, per_round=1)
        self.assertEqual(report["hard"], [])
        self.assertTrue(report["publishable"])
        self.assertGreaterEqual(provider.calls, 1)
        self.assertGreaterEqual(provider.last_images, 1)
        dirty = {"hard": report["hard"], "api_key": "sk-test-secret", "image": b"\x89PNG"}
        clean = scrub_audit(dirty)
        self.assertNotIn("api_key", clean)
        self.assertNotIn("sk-test-secret", str(clean))
        self.assertIsNone(house.unplaced or None)
        self.assertEqual(len([p for lay in house.by_room.values() for p in lay.placements
                              if p.category == "chair"]), 4)


if __name__ == "__main__":
    unittest.main()
