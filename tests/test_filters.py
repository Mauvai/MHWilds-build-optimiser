"""Gear filters: what each one removes, how they reach a run, and the file form."""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from tests.support import Value, game, import_gui, weighted
from tests.test_cli import run_cli

from gear_filters import (
    ELEMENTS,
    RARITIES,
    TALISMAN_TIERS,
    VARIANTS,
    GearFilters,
    ResistanceRule,
    apply_filters,
    pin_conflicts,
    talisman_tier,
    variant_of,
)
from optimiser import Scoring, optimise
from search_profile import SearchProfile, load_profile, save_profile


def piece(name: str):
    return next(p for p in game().armor if p.name == name)


class Classifying(unittest.TestCase):
    def test_variant_letters(self):
        self.assertEqual(variant_of(piece("Afi Crown α")), "α")
        self.assertEqual(variant_of(piece("Arkvulcan Helm γ")), "γ")
        # Low Rank sets carry no letter, so no variant filter touches them.
        self.assertIsNone(variant_of(piece("Quematrice Helm")))

    def test_talisman_tiers(self):
        tiers = {t.name: talisman_tier(t) for t in game().talismans}
        self.assertEqual(tiers["Blast Charm III"], 3)
        self.assertEqual(tiers["Blast Charm I"], 1)
        # The two charms whose names end in "Charm" have no tier at all.
        self.assertEqual(sum(1 for t in tiers.values() if t is None), 2)


class Applying(unittest.TestCase):
    def test_default_changes_nothing(self):
        result = apply_filters(game(), GearFilters())
        self.assertIs(result.game, game())
        self.assertEqual(result.removed, {})

    def test_gamma_sets(self):
        removed = apply_filters(game(), GearFilters(exclude_variants={"γ"})).removed
        gamma = {p.name for p in game().armor if p.set.endswith("γ")}
        self.assertEqual(set(removed), gamma)
        self.assertEqual(len(gamma), 25)

    def test_ranks(self):
        removed = apply_filters(game(), GearFilters(exclude_ranks={"low"})).removed
        self.assertEqual(set(removed), {p.name for p in game().armor if p.rank == "low"})
        removed = apply_filters(game(), GearFilters(exclude_ranks={"high"})).removed
        self.assertTrue(all(piece(n).rank == "high" for n in removed))

    def test_armour_stays_listed_so_pins_and_exclusions_still_resolve(self):
        result = apply_filters(game(), GearFilters(exclude_ranks={"low"}))
        self.assertEqual(len(result.game.armor), len(game().armor))

    def test_transcendence_off_swaps_slots_and_defence(self):
        result = apply_filters(game(), GearFilters(transcendence=False))
        after = {p.name: p for p in result.game.armor}
        crown = after["Afi Crown α"]
        self.assertEqual(crown.slots, [2])
        self.assertEqual(crown.defense.max, 76)
        self.assertEqual(crown.defense.base, piece("Afi Crown α").defense.base)
        # Rarity 7 and 8 cannot be transcended and are left alone.
        untouched = next(p for p in game().armor if p.rarity == 8)
        self.assertEqual(after[untouched.name], untouched)
        # Nothing removed: transcending changes pieces, it filters none.
        self.assertEqual(result.removed, {})

    def test_minimum_defence_sees_the_transcendence_choice(self):
        filters = GearFilters(min_defense=80)
        on = apply_filters(game(), filters).removed
        off = apply_filters(game(), GearFilters(min_defense=80, transcendence=False)).removed
        self.assertNotIn("Afi Crown α", on)  # 90 transcended
        self.assertIn("Afi Crown α", off)  # 76 before
        self.assertIn("76", off["Afi Crown α"])

    def test_resistance_rules(self):
        rule = ResistanceRule("fire", "<", 0)
        removed = apply_filters(game(), GearFilters(resistance_rules=[rule])).removed
        expected = {p.name for p in game().armor if p.resistances.fire < 0}
        self.assertEqual(set(removed), expected)
        for op, value, name, out in (
            ("=", 2, "Afi Crown α", True),
            (">=", 3, "Afi Crown α", False),
            (">", 1, "Afi Crown α", True),
            ("<=", -3, "Afi Crown α", False),
        ):
            rule = ResistanceRule("fire", op, value)
            self.assertEqual(rule.matches(piece(name)), out, (op, value))

    def test_bad_rules_are_refused(self):
        with self.assertRaises(ValueError):
            ResistanceRule("lightning", "<", 0)
        with self.assertRaises(ValueError):
            ResistanceRule("fire", "!=", 0)

    def test_slotless_and_rarity(self):
        removed = apply_filters(game(), GearFilters(exclude_slotless=True)).removed
        self.assertEqual(set(removed), {p.name for p in game().armor if not any(p.slots)})
        removed = apply_filters(game(), GearFilters(exclude_rarities={8})).removed
        self.assertEqual(set(removed), {p.name for p in game().armor if p.rarity == 8})

    def test_talisman_tier_filter(self):
        result = apply_filters(game(), GearFilters(exclude_talisman_tiers={3}))
        names = {t.name for t in result.game.talismans}
        self.assertNotIn("Blast Charm III", names)
        self.assertIn("Blast Charm II", names)
        self.assertEqual(
            len(game().talismans) - len(names),
            sum(1 for t in game().talismans if talisman_tier(t) == 3),
        )


class Pins(unittest.TestCase):
    def test_pinned_piece_that_a_filter_removes_is_refused(self):
        filters = GearFilters(exclude_variants={"γ"})
        result = apply_filters(game(), filters)
        problems = pin_conflicts(game(), result, {"head": "Arkvulcan Helm γ"})
        self.assertEqual(len(problems), 1)
        self.assertIn("Gamma", problems[0])
        self.assertEqual(pin_conflicts(game(), result, {"head": "Arkvulcan Helm α"}), [])

    def test_pinned_talisman_of_a_filtered_tier_is_refused(self):
        result = apply_filters(game(), GearFilters(exclude_talisman_tiers={3}))
        self.assertTrue(pin_conflicts(game(), result, {"talisman": "Blast Charm III"}))
        # A custom talisman's name is never filtered.
        self.assertEqual(pin_conflicts(game(), result, {"talisman": "My Charm III"}), [])


class InASearch(unittest.TestCase):
    def run_filtered(self, filters: GearFilters):
        result = apply_filters(game(), filters)
        scoring = Scoring(weighted({"Weakness Exploit": (4, 3), "Burst": (3, 2)}))
        sets, _level, _opt = optimise(
            result.game, scoring, excluded_pieces=sorted(result.removed), beam_width=600,
            final_pool=200,
        )
        self.assertTrue(sets)
        return sets

    def test_filtered_pieces_never_appear(self):
        sets = self.run_filtered(GearFilters(exclude_variants={"β"}, exclude_ranks={"low"}))
        for gear_set in sets:
            for p in gear_set.pieces:
                self.assertNotEqual(variant_of(p), "β")
                self.assertEqual(p.rank, "high")

    def test_untranscended_values_reach_the_results(self):
        sets = self.run_filtered(GearFilters(transcendence=False))
        for gear_set in sets:
            for p in gear_set.pieces:
                original = piece(p.name)
                if original.untranscended is not None:
                    self.assertEqual(p.slots, original.untranscended.slots)
                    self.assertEqual(p.defense.max, original.untranscended.defense_max)
            self.assertEqual(gear_set.defense_total, sum(p.defense.max for p in gear_set.pieces))


class FileForm(unittest.TestCase):
    FULL = GearFilters(
        exclude_variants={"γ"},
        exclude_ranks={"low"},
        exclude_rarities={5},
        exclude_talisman_tiers={3},
        transcendence=False,
        resistance_rules=[ResistanceRule("fire", "<", 0)],
        min_defense=70,
        exclude_slotless=True,
    )

    def test_round_trip(self):
        self.assertEqual(GearFilters.from_dict(self.FULL.to_dict()), self.FULL)
        self.assertEqual(GearFilters().to_dict(), {})
        self.assertEqual(GearFilters.from_dict(None), GearFilters())

    def test_bad_values_are_refused(self):
        for raw in (
            {"exclude_variant": ["γ"]},
            {"exclude_variants": ["delta"]},
            {"exclude_rarities": [9]},
            {"min_defense": -1},
            {"transcendence": "no"},
            {"resistance_rules": [{"element": "fire", "op": "<"}]},
            {"resistance_rules": [{"element": "fire", "op": "~", "value": 0}]},
        ):
            with self.assertRaises(ValueError, msg=raw):
                GearFilters.from_dict(raw)

    def test_profile_round_trip_and_older_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.yaml"
            profile = SearchProfile(weights={"Burst": (3.0, 2.0)}, filters=self.FULL)
            save_profile(profile, path)
            self.assertEqual(load_profile(path).filters, self.FULL)
            # A profile written before filters existed has no key at all.
            path.write_text("profile_version: 1\nweights: {}\n", encoding="utf-8")
            self.assertEqual(load_profile(path).filters, GearFilters())

    def test_description_names_every_active_filter(self):
        lines = self.FULL.describe()
        self.assertEqual(len(lines), 8)
        self.assertEqual(GearFilters().describe(), [])


class FiltersTab(unittest.TestCase):
    """The tab's variables to GearFilters and back, without a window."""

    def stub(self):
        G = import_gui()
        stub = types.SimpleNamespace(
            filter_vars={},
            min_defense_var=Value("70"),
            transcendence_var=Value(True),
            resistance_op_vars={e: Value("<") for e in ELEMENTS},
            resistance_value_vars={e: Value("0") for e in ELEMENTS},
            filter_summary_var=Value(),
            game_data=game(),
        )
        keys = (
            [f"variant:{v}" for v in VARIANTS] + ["rank:high", "rank:low"]
            + [f"rarity:{r}" for r in RARITIES] + [f"tier:{t}" for t in TALISMAN_TIERS]
            + ["slotless", "mindef"] + [f"res:{e}" for e in ELEMENTS]
        )
        stub.filter_vars = {k: Value(G.INCLUDE) for k in keys}
        for name in ("_excluded", "_current_filters", "_refresh_filter_summary"):
            setattr(stub, name, getattr(G.SkillsGui, name).__get__(stub))
        return G, stub

    def test_everything_included_by_default(self):
        G, stub = self.stub()
        self.assertTrue(G.SkillsGui._current_filters(stub).is_default())
        G.SkillsGui._refresh_filter_summary(stub)
        self.assertEqual(stub.filter_summary_var.get(), "Nothing filtered out.")

    def test_round_trip_through_the_tab(self):
        G, stub = self.stub()
        G.SkillsGui._set_filters(stub, FileForm.FULL)
        self.assertEqual(G.SkillsGui._current_filters(stub), FileForm.FULL)
        # Shown with the typographic operator, stored as ASCII.
        rule = ResistanceRule("dragon", ">=", 3)
        G.SkillsGui._set_filters(stub, GearFilters(resistance_rules=[rule]))
        self.assertEqual(stub.resistance_op_vars["dragon"].get(), "\u2265")
        self.assertEqual(G.SkillsGui._current_filters(stub).resistance_rules, [rule])
        self.assertIn("Filtered out:", stub.filter_summary_var.get())

    def test_bad_number_is_named_not_guessed(self):
        G, stub = self.stub()
        stub.filter_vars["res:fire"].set(G.EXCLUDE)
        stub.resistance_value_vars["fire"].set("abc")
        problems = G.SkillsGui._filter_input_problems(stub)
        self.assertEqual(problems, ["The fire resistance value must be a whole number."])


class CommandLine(unittest.TestCase):
    def test_filters_reach_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--skills-db", "skills_weighted.yaml", "--count", "1", "--beam", "300",
                "--exclude-variant", "gamma", "--exclude-rank", "low",
                "--exclude-resistance", "dragon<=-3", "--no-transcendence",
                "--output", str(Path(tmp) / "sets.yaml"),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Filters: Without Gamma (γ) sets; Without Low Rank armour", result.stdout)
        self.assertIn("Without dragon resistance <= -3", result.stdout)

    def test_pin_against_a_filter_is_a_usage_error(self):
        result = run_cli(
            "--skills-db", "skills_weighted.yaml",
            "--pin-head", "Arkvulcan Helm γ", "--exclude-variant", "γ",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Arkvulcan Helm γ is pinned to head", result.stderr)

    def test_malformed_options(self):
        for args in (
            ("--exclude-variant", "delta"),
            ("--exclude-resistance", "fire"),
            ("--exclude-rarity", "9"),
            ("--min-defense", "-5"),
        ):
            result = run_cli("--skills-db", "skills_weighted.yaml", *args)
            self.assertEqual(result.returncode, 2, args)


if __name__ == "__main__":
    unittest.main()
