"""Search, scoring and requirement handling in optimiser.py.

Full searches cost a second or two each, so they are kept to the behaviours
only a full search can show; the rest call the pieces directly.
"""

from __future__ import annotations

import itertools
import unittest

from tests.support import game, weighted

from optimiser import (
    DEFAULT_TIERS,
    Optimiser,
    PIECE_TYPES,
    Scoring,
    SearchState,
    bonus_base_name,
    build_tiers,
    consume_slot,
    defense_value,
    fill_slots,
    decoration_options,
    optimise,
)

# The user's own weighting in skills_weighted.yaml: three mandatory skills that
# the default search satisfies easily.
EASY_MANDATORY = {
    "Antivirus": (5, 0),
    "Constitution": (5, 0),
    "Weakness Exploit": (5, 0),
}


class Scoring_(unittest.TestCase):
    def test_level_is_capped_at_max(self):
        scoring = Scoring(weighted({"Weakness Exploit": (4, 4)}))
        self.assertEqual(
            scoring.score("Weakness Exploit", 5), scoring.score("Weakness Exploit", 9)
        )

    def test_level_weight_zero_gives_full_value_at_level_one(self):
        scoring = Scoring(weighted({"Weakness Exploit": (3, 0)}))
        self.assertEqual(
            scoring.score("Weakness Exploit", 1), scoring.score("Weakness Exploit", 5)
        )

    def test_level_weight_five_still_values_the_first_level(self):
        scoring = Scoring(weighted({"Evade Window": (4, 5)}))
        self.assertEqual(scoring.score("Evade Window", 1), 16)

    def test_later_levels_outweigh_the_first_at_high_level_weight(self):
        scoring = Scoring(weighted({"Evade Window": (4, 5)}))
        self.assertGreater(scoring.marginal("Evade Window", 1), scoring.marginal("Evade Window", 0))

    def test_level_value_ignores_max_level(self):
        # Evade Window goes to 5, Evade Extender to 3: same weights, same steps.
        scoring = Scoring(weighted({"Evade Window": (3, 4), "Evade Extender": (3, 4)}))
        for level in range(3):
            self.assertAlmostEqual(
                scoring.marginal("Evade Window", level),
                scoring.marginal("Evade Extender", level),
            )

    def test_a_levelled_skill_beats_a_merely_present_one(self):
        scoring = Scoring(weighted({"Evade Window": (3, 5), "Evade Extender": (5, 1)}))
        self.assertGreater(
            scoring.marginal("Evade Window", 1), scoring.marginal("Evade Extender", 1)
        )

    def test_higher_weighted_level_wins_the_jewel_slot(self):
        # The reported case: one size-2 slot, Evade Window (4/5) already at 2,
        # Evade Extender (3/4) absent. Both jewels are size 2.
        from optimiser import Context

        scoring = Scoring(weighted({"Evade Window": (4, 5), "Evade Extender": (3, 4)}))
        options = decoration_options(
            {"Evade Window": 2}, Context(game(), scoring)
        )
        _value, counts = fill_slots(options, (0, 1, 0))
        chosen = [o.skill for o, n in zip(options, counts) if n]
        self.assertEqual(chosen, ["Evade Window"])

    def test_negative_weight_stays_negative(self):
        scoring = Scoring(weighted({"Weakness Exploit": (-1, 0)}))
        self.assertLess(scoring.score("Weakness Exploit", 1), 0)

    def test_mandatory_thresholds(self):
        scoring = Scoring(
            weighted({"Weakness Exploit": (5, 5), "Antivirus": (5, 4), "Agitator": (4, 5)})
        )
        self.assertEqual(scoring.mandatory, {"Weakness Exploit", "Antivirus"})
        self.assertEqual(scoring.mandatory_max, {"Weakness Exploit"})

    def test_defense_value_is_clamped(self):
        self.assertEqual(defense_value(0), 0.0)
        self.assertEqual(defense_value(1000), 1.0)


class Slots(unittest.TestCase):
    def test_consume_takes_smallest_adequate_slot(self):
        self.assertEqual(consume_slot((1, 1, 1), 1), (0, 1, 1))
        self.assertEqual(consume_slot((0, 1, 1), 1), (0, 0, 1))
        self.assertEqual(consume_slot((1, 0, 1), 2), (1, 0, 0))
        self.assertIsNone(consume_slot((3, 0, 0), 2))

    def test_fill_slots_matches_brute_force(self):
        # fill_slots claims to be exact; check it against every assignment on
        # a small case with mixed gem sizes competing for mixed slots.
        scoring = Scoring(
            weighted(
                {"Defense Boost": (3, 4), "Weakness Exploit": (4, 3), "Agitator": (2, 2)}
            )
        )
        optimiser = Optimiser(game(), scoring)
        options = decoration_options({}, optimiser.context)
        slots = (2, 1, 1)

        def brute():
            best = 0.0
            ranges = [range(len(o.values) + 1) for o in options]
            for counts in itertools.product(*ranges):
                free = slots
                ok = True
                for option, count in zip(options, counts):
                    for _ in range(count):
                        free = consume_slot(free, option.size)
                        if free is None:
                            ok = False
                            break
                    if not ok:
                        break
                if ok:
                    best = max(
                        best,
                        sum(sum(o.values[:c]) for o, c in zip(options, counts)),
                    )
            return best

        value, _counts = fill_slots(options, slots)
        self.assertAlmostEqual(value, brute())


class BuildTiers(unittest.TestCase):
    def test_counts_add_up(self):
        for count in (1, 2, 5, 9, 10, 11, 20):
            self.assertEqual(sum(t.count for t in build_tiers(count)), count, count)

    def test_ten_is_the_default_split(self):
        self.assertEqual(build_tiers(10), DEFAULT_TIERS)


class StrictFilter(unittest.TestCase):
    def test_strict_returns_only_sets_meeting_every_requirement(self):
        sets, level, _ = optimise(game(), Scoring(weighted(EASY_MANDATORY)))
        self.assertEqual(len(sets), 10)
        self.assertEqual(level, 0)
        for gear_set in sets:
            self.assertEqual(gear_set.constraint_level, 0)
            for name in EASY_MANDATORY:
                self.assertGreaterEqual(gear_set.skill_levels.get(name, 0), 1)

    def test_results_are_distinct(self):
        sets, _, _ = optimise(game(), Scoring(weighted(EASY_MANDATORY)))
        self.assertEqual(len({s.piece_names for s in sets}), len(sets))

    def test_unsuppliable_requirement_empties_strict_but_not_relaxed(self):
        weights = {"Airborne": (5, 0), "Weakness Exploit": (4, 3)}
        sets, _, optimiser = optimise(game(), Scoring(weighted(weights)))
        self.assertEqual(sets, [])
        self.assertEqual(len(optimiser.impossible_requirements()), 1)
        self.assertIn("Airborne", optimiser.impossible_requirements()[0])

        relaxed, level, _ = optimise(game(), Scoring(weighted(weights)), strict=False)
        self.assertEqual(len(relaxed), 10)
        self.assertEqual(level, 2)


class ImpossibleRequirements(unittest.TestCase):
    def reasons(self, weights, **kwargs):
        optimiser = Optimiser(game(), Scoring(weighted(weights)), **kwargs)
        return optimiser.impossible_requirements()

    def test_two_set_bonuses_at_max_need_eight_pieces(self):
        (reason,) = self.reasons(
            {"Gore Magala's Tyranny": (5, 5), "Arkveld's Hunger": (5, 5)}
        )
        self.assertIn("8 pieces", reason)

    def test_pins_leave_too_few_carriers(self):
        (reason,) = self.reasons(
            {"Gore Magala's Tyranny": (5, 5)},
            pinned_pieces={"head": "Lagiacrus Helm β", "chest": "Lagiacrus Mail β"},
        )
        self.assertIn("only 3 free slot(s)", reason)

    def test_set_and_group_mix_is_checked_jointly(self):
        # 4 + 3 pieces with no piece carrying both: only the exact joint check
        # can prove this, since the two kinds can share pieces in general.
        (reason,) = self.reasons(
            {"Gore Magala's Tyranny": (5, 5), "Alluring Pelt": (5, 0)}
        )
        self.assertIn("No choice of armour", reason)

    def test_gogma_points_can_make_a_mix_possible(self):
        self.assertEqual(
            self.reasons(
                {"Gore Magala's Tyranny": (5, 5), "Alluring Pelt": (5, 0)},
                extra_bonus_pieces={"Gore Magala's Tyranny": 1, "Alluring Pelt": 1},
            ),
            [],
        )

    def test_joint_check_agrees_with_brute_force(self):
        # _bonus_combination_exists keeps only maximal carrier sets per slot;
        # compare it with a product over every carrier set.
        optimiser = Optimiser(game(), Scoring(weighted({})))
        cases = [
            {"Gore Magala's Tyranny": 4, "Alluring Pelt": 3},
            {"Gore Magala's Tyranny": 2, "Alluring Pelt": 3},
            {"Arkveld's Hunger": 4, "Alluring Pelt": 1},
            {"Gore Magala's Tyranny": 2, "Arkveld's Hunger": 2, "Alluring Pelt": 1},
        ]
        armor = game().armor
        for needs in cases:
            names = tuple(needs)
            per_slot = [
                {
                    frozenset(
                        n
                        for n in names
                        if any(bonus_base_name(b.name) == n for b in p.set_bonuses)
                    )
                    for p in armor
                    if p.piece_type == t
                }
                for t in PIECE_TYPES
            ]
            expected = any(
                all(sum(n in s for s in combo) >= needs[n] for n in names)
                for combo in itertools.product(*per_slot)
            )
            self.assertEqual(
                optimiser._bonus_combination_exists(needs, list(PIECE_TYPES)),
                expected,
                needs,
            )


class ReservedSlots(unittest.TestCase):
    """A required set bonus must not stop reserved slots being released."""

    PIECES = (
        "Skull Mask α",
        "Kunafa Cloak α",
        "Dahaad Shardbraces γ",
        "Gravios Coil β",
        "Dahaad Shardgreaves β",
    )

    def build(self, weights):
        optimiser = Optimiser(game(), Scoring(weighted(weights)), reserved_slots=2)
        by_name = {p.name: p for p in game().armor}
        pieces = [by_name[n] for n in self.PIECES]
        talisman = next(
            t for t in game().talismans if all(s.name != "Defense Boost" for s in t.skills)
        )
        profiles = tuple(optimiser.context.profile(p) for p in pieces)
        state = SearchState(
            profiles=profiles,
            levels=(0,) * len(optimiser.context.relevant_skills),
            slots=(0, 0, 0),
            defense_value_sum=sum(p.defense_value for p in profiles),
            bonus_counts=(0,) * len(optimiser.context.relevant_bonuses),
            rank=0.0,
        )
        base = {}
        for piece in pieces:
            for s in piece.skills:
                base[s.name] = base.get(s.name, 0) + s.level
        return optimiser._build_set(pieces, talisman, base, state)

    def test_reserve_released_for_a_required_max_skill(self):
        # These pieces have exactly seven slots and no Defense Boost, so
        # reaching 7/7 needs every slot, the two reserved ones included.
        result = self.build({"Defense Boost": (5, 5)})
        self.assertEqual((result.reserved_slots, result.constraint_level), (0, 0))
        self.assertEqual(result.skill_levels["Defense Boost"], 7)

    def test_active_required_bonus_does_not_block_release(self):
        result = self.build({"Defense Boost": (5, 5), "Jin Dahaad's Revolt": (5, 0)})
        self.assertEqual((result.reserved_slots, result.constraint_level), (0, 0))
        self.assertEqual(result.skill_levels["Defense Boost"], 7)


class Pins(unittest.TestCase):
    def test_bad_pins_raise(self):
        for pins in (
            {"head": "No Such Piece"},
            {"head": "Lagiacrus Mail β"},  # a chest piece
            {"hat": "Lagiacrus Helm β"},
        ):
            with self.assertRaises(ValueError, msg=pins):
                Optimiser(game(), Scoring(weighted({})), pinned_pieces=pins)

    def test_pinned_piece_is_in_every_set(self):
        sets, _, _ = optimise(
            game(),
            Scoring(weighted(EASY_MANDATORY)),
            pinned_pieces={"waist": "Gore Coil α"},
        )
        self.assertTrue(sets)
        for gear_set in sets:
            self.assertIn("Gore Coil α", gear_set.piece_names)
            self.assertIn("waist", gear_set.pinned_types)


if __name__ == "__main__":
    unittest.main()
