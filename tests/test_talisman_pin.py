"""Pinning a talisman: the optimiser, profiles, the report and the GUI."""

from __future__ import annotations

import tempfile
import types
import unittest
from dataclasses import asdict, replace
from pathlib import Path

import yaml

from tests.support import Value, game, import_gui, weighted

from load_data import DecorationSlots, SkillLevel, Talisman
from optimiser import TALISMAN_SLOT, Context, Scoring, optimise
from optimiser_report import render_console, set_view
from search_profile import SearchProfile, load_profile, profile_problems, save_profile

G = import_gui()

WEIGHTS = {"Weakness Exploit": (5, 0), "Attack Boost": (4, 3)}
# Nothing to do with the weighted skills, so the search would never pick it
# on its own: if every set wears it, the pin did that.
UNWANTED = "Blast Charm I"
CUSTOM = Talisman(
    name="My Appraised Charm",
    rarity=6,
    skills=[SkillLevel("Attack Boost", 2)],
    slots=[],
    decoration_slots=DecorationSlots(armour=0, weapon=0),
    source_url="custom",
)


def run(pins, data=None):
    scoring = Scoring(weighted(WEIGHTS))
    sets, _, _ = optimise(data or game(), scoring, beam_width=300, pinned_pieces=pins)
    return sets, scoring


class Engine(unittest.TestCase):
    def test_every_set_wears_the_pinned_talisman(self):
        sets, _ = run({TALISMAN_SLOT: UNWANTED})
        self.assertTrue(sets)
        for gear_set in sets:
            self.assertEqual(gear_set.talisman.name, UNWANTED)
            self.assertIn(TALISMAN_SLOT, gear_set.pinned_types)

    def test_combines_with_armour_pins(self):
        sets, _ = run({"waist": "Gore Coil α", TALISMAN_SLOT: UNWANTED})
        for gear_set in sets:
            self.assertEqual(gear_set.talisman.name, UNWANTED)
            self.assertEqual({"waist", TALISMAN_SLOT}, set(gear_set.pinned_types))

    def test_custom_talisman_once_in_the_pool(self):
        data = replace(game(), talismans=list(game().talismans) + [CUSTOM])
        sets, _ = run({TALISMAN_SLOT: CUSTOM.name}, data)
        self.assertTrue(sets)
        self.assertTrue(all(s.talisman.name == CUSTOM.name for s in sets))

    def test_unknown_talisman_is_refused(self):
        with self.assertRaisesRegex(ValueError, "No talisman is named"):
            Context(game(), Scoring(weighted(WEIGHTS)), pinned_pieces={TALISMAN_SLOT: "Nope"})

    def test_unpinned_runs_are_unmarked(self):
        sets, _ = run({})
        self.assertTrue(all(TALISMAN_SLOT not in s.pinned_types for s in sets))


class Report(unittest.TestCase):
    def test_marked_in_text_and_view(self):
        sets, scoring = run({TALISMAN_SLOT: UNWANTED})
        pinned = next(t for t in game().talismans if t.name == UNWANTED)
        text = render_console(sets, scoring, 0, Path("x.yaml"), pinned_talisman=pinned)
        self.assertIn(f"talisman {UNWANTED}", text)
        self.assertIn(f"* charm  {UNWANTED}", text)
        charm = set_view(sets[0], 1, len(sets), scoring).equipment[-1]
        self.assertEqual(charm.kind, "charm")
        self.assertTrue(charm.pinned)


class Profiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_round_trip(self):
        profile = SearchProfile(pins={"head": "Udra Mirehelm γ", TALISMAN_SLOT: UNWANTED})
        path = self.tmp / "p.yaml"
        save_profile(profile, path)
        self.assertEqual(load_profile(path), profile)
        self.assertEqual(profile_problems(profile, game()), [])

    def test_unknown_talisman_is_a_problem(self):
        problems = profile_problems(SearchProfile(pins={TALISMAN_SLOT: "Nope"}), game())
        self.assertEqual(len(problems), 1)
        self.assertIn("Pinned talisman 'Nope'", problems[0])

    def test_custom_talisman_found_in_the_profiles_file(self):
        path = self.tmp / "mine.yaml"
        path.write_text(
            yaml.safe_dump([asdict(CUSTOM)], allow_unicode=True), encoding="utf-8"
        )
        pinned = SearchProfile(pins={TALISMAN_SLOT: CUSTOM.name})
        self.assertEqual(len(profile_problems(pinned, game())), 1)
        with_file = replace(pinned, custom_talismans=str(path))
        self.assertEqual(profile_problems(with_file, game()), [])


class Gui(unittest.TestCase):
    def stub(self, custom=()):
        stub = types.SimpleNamespace(
            game_data=game(),
            custom_talismans_loaded=list(custom),
            pin_vars={TALISMAN_SLOT: Value(G.NONE_OPTION)},
            talisman_pin_combo=types.SimpleNamespace(config=lambda **k: stub.__dict__.update(k)),
        )
        for name in (
            "_pinnable_talismans",
            "_refresh_talisman_pin_options",
            "_pinned_talisman",
        ):
            setattr(stub, name, getattr(G.SkillsGui, name).__get__(stub))
        return stub

    def test_options_list_custom_then_craftable(self):
        stub = self.stub([CUSTOM])
        stub._refresh_talisman_pin_options()
        values = stub.values
        self.assertEqual(values[:2], [G.NONE_OPTION, CUSTOM.name])
        self.assertEqual(len(values), 2 + len(game().talismans))

    def test_unloading_the_custom_file_unpins_its_talisman(self):
        stub = self.stub([CUSTOM])
        stub.pin_vars[TALISMAN_SLOT].set(CUSTOM.name)
        self.assertEqual(stub._pinned_talisman(), CUSTOM)
        stub.custom_talismans_loaded = []
        stub._refresh_talisman_pin_options()
        self.assertEqual(stub.pin_vars[TALISMAN_SLOT].get(), G.NONE_OPTION)
        self.assertIsNone(stub._pinned_talisman())


if __name__ == "__main__":
    unittest.main()
