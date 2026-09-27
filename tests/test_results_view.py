"""The structured set view the GUI results window draws from."""

from __future__ import annotations

import unittest

from optimiser import Scoring, optimise
from optimiser_report import ELEMENTS, SkillMeter, set_view
from tests.support import game, weighted

WEIGHTS = {"Antivirus": (5, 0), "Weakness Exploit": (5, 0), "Attack Boost": (4, 3)}


class SetViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scoring = Scoring(weighted(WEIGHTS))
        cls.sets, _, _ = optimise(game(), cls.scoring, weapon_slots=(3, 2))
        cls.view = set_view(cls.sets[0], 1, len(cls.sets), cls.scoring)

    def test_resistances_are_the_armour_sum(self):
        gear_set = self.sets[0]
        self.assertEqual(list(self.view.resistances), list(ELEMENTS))
        for element in ELEMENTS:
            expected = sum(getattr(p.resistances, element) for p in gear_set.pieces)
            self.assertEqual(self.view.resistances[element], expected)

    def test_every_piece_then_weapon_then_charm(self):
        kinds = [row.kind for row in self.view.equipment]
        self.assertEqual(kinds[-2:], ["weapon", "charm"])
        self.assertEqual(len(kinds), len(self.sets[0].pieces) + 2)

    def test_jewel_names_drop_the_level_suffix(self):
        cells = [c for row in self.view.equipment for c in row.slots if c.decoration]
        self.assertTrue(cells)
        for cell in cells:
            self.assertNotIn("【", cell.decoration)
            self.assertTrue(1 <= cell.level <= cell.size)

    def test_skill_meter_states(self):
        self.assertTrue(SkillMeter("x", 5, 5, []).maxed)
        self.assertTrue(SkillMeter("x", 1, 5, []).minimal)
        self.assertTrue(SkillMeter("x", 1, 3, []).minimal)
        self.assertFalse(SkillMeter("x", 1, 1, []).minimal)  # 1/1 is maxed
        self.assertFalse(SkillMeter("x", 2, 5, []).minimal)
        for meter in self.view.skills:
            self.assertNotIn("MAX", meter.tags)


if __name__ == "__main__":
    unittest.main()
