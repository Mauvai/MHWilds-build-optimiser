"""Build targets: minimum total defence and resistances over the armour."""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from tests.support import Value, game, import_gui, weighted
from tests.test_cli import run_cli

from optimiser import ELEMENTS, PIECE_TYPES, BuildTargets, Optimiser, Scoring, optimise
from optimiser_report import set_resistances
from search_profile import SearchProfile, load_profile, save_profile

WEIGHTS = {"Weakness Exploit": (4, 3), "Burst": (3, 2), "Agitator": (3, 2)}
FAST = {"beam_width": 800, "final_pool": 300}


def run(targets: BuildTargets, **options):
    return optimise(game(), Scoring(weighted(WEIGHTS)), targets=targets, **FAST, **options)


class Form(unittest.TestCase):
    def test_round_trip(self):
        targets = BuildTargets(defense=420, resistances={"fire": 0, "dragon": 5})
        self.assertEqual(BuildTargets.from_dict(targets.to_dict()), targets)
        self.assertEqual(BuildTargets().to_dict(), {})
        self.assertTrue(BuildTargets.from_dict(None).is_empty())
        # 0 is a real target for a resistance, not "none".
        self.assertFalse(BuildTargets(resistances={"ice": 0}).is_empty())

    def test_bad_values_are_refused(self):
        for raw in (
            {"defence": 400},
            {"defense": "high"},
            {"resistances": {"lightning": 1}},
            {"resistances": {"fire": 1.5}},
        ):
            with self.assertRaises(ValueError, msg=raw):
                BuildTargets.from_dict(raw)
        with self.assertRaises(ValueError):
            BuildTargets(defense=-1)

    def test_profile_round_trip(self):
        targets = BuildTargets(defense=400, resistances={"water": 2})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.yaml"
            save_profile(SearchProfile(targets=targets), path)
            self.assertEqual(load_profile(path).targets, targets)


class Search(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain, _level, _opt = run(BuildTargets())

    def test_defence_target_is_met_by_every_set(self):
        # Just above the best untargeted set, so the target has to bite.
        target = max(s.defense_total for s in self.plain) + 4
        sets, _level, _opt = run(BuildTargets(defense=target))
        self.assertTrue(sets)
        for gear_set in sets:
            self.assertGreaterEqual(gear_set.defense_total, target)

    def test_resistance_targets_are_met_by_every_set(self):
        targets = BuildTargets(resistances={"fire": 6, "dragon": 0})
        sets, _level, _opt = run(targets)
        self.assertTrue(sets)
        for gear_set in sets:
            totals = set_resistances(gear_set)
            self.assertGreaterEqual(totals["fire"], 6)
            self.assertGreaterEqual(totals["dragon"], 0)

    def test_out_of_reach_target_is_proved_before_searching(self):
        sets, _level, optimiser = run(BuildTargets(defense=5000, resistances={"ice": 99}))
        self.assertEqual(sets, [])
        reasons = optimiser.impossible_requirements()
        self.assertEqual(len(reasons), 2)
        self.assertIn("total defence target 5000 is out of reach", reasons[0])
        self.assertIn("ice resistance target 99", reasons[1])

    def test_slot_bounds_survive_pruning(self):
        # Dominated-piece pruning must not drop the piece a target needs:
        # each slot's best total over the pruned candidates equals the best
        # over every piece that slot could use.
        targets = BuildTargets(defense=1, resistances={e: -50 for e in ELEMENTS})
        optimiser = Optimiser(game(), Scoring(weighted(WEIGHTS)), targets=targets)
        context = optimiser.context
        for piece_type in PIECE_TYPES:
            pool = [p for p in context.available_armor if p.piece_type == piece_type]
            self.assertEqual(
                context.slot_best_defense[piece_type], max(p.defense.max for p in pool)
            )
            for i, element in enumerate(context.target_elements):
                self.assertEqual(
                    context.slot_best_resistance[piece_type][i],
                    max(getattr(p.resistances, element) for p in pool),
                    (piece_type, element),
                )

    def test_no_targets_changes_nothing(self):
        sets, _level, _opt = run(BuildTargets())
        self.assertEqual(
            [s.piece_names for s in sets], [s.piece_names for s in self.plain]
        )


class CommandLine(unittest.TestCase):
    def test_targets_reach_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--skills-db", "skills_weighted.yaml", "--count", "1", "--beam", "300",
                "--target-defense", "350", "--target-resistance", "fire=0",
                "--output", str(Path(tmp) / "o.yaml"),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Targets: total defence >= 350; fire resistance >= 0", result.stdout)

    def test_malformed_targets(self):
        for args in (
            ("--target-resistance", "fire"),
            ("--target-resistance", "lightning=2"),
            ("--target-defense", "-1"),
        ):
            result = run_cli("--skills-db", "skills_weighted.yaml", *args)
            self.assertEqual(result.returncode, 2, args)


class TargetsSection(unittest.TestCase):
    def stub(self):
        keys = ["defense", *ELEMENTS]
        return types.SimpleNamespace(
            target_on_vars={k: Value(False) for k in keys},
            target_value_vars={k: Value("0") for k in keys},
        )

    def test_round_trip_through_the_tab(self):
        G = import_gui()
        stub = self.stub()
        self.assertTrue(G.SkillsGui._current_targets(stub).is_empty())
        targets = BuildTargets(defense=410, resistances={"thunder": -2})
        G.SkillsGui._set_targets(stub, targets)
        self.assertEqual(G.SkillsGui._current_targets(stub), targets)

    def test_bad_number_is_named(self):
        G = import_gui()
        stub = self.stub()
        stub.target_on_vars["water"].set(True)
        stub.target_value_vars["water"].set("x")
        self.assertEqual(
            G.SkillsGui._target_input_problems(stub),
            ["The water resistance target must be a whole number."],
        )


if __name__ == "__main__":
    unittest.main()
