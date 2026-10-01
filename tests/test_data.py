"""Invariants of the game data that the code relies on.

Most of these are not about the data being right - it was compiled by hand and
nothing here can check it against the game - but about it keeping the shape
that parts of the optimiser assume without re-checking. Each test names the
code that breaks if its invariant stops holding.
"""

from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tests.support import game

import yaml

from load_data import load_skills
from optimiser import bonus_base_name


class DataInvariants(unittest.TestCase):
    def test_every_referenced_skill_exists(self):
        # Scoring looks skills up by name; a misspelt reference would score 0
        # silently rather than fail.
        data = game()
        known = {s.name for s in data.skills}
        referenced = {
            s.name
            for source in (data.armor, data.talismans, data.decorations)
            for record in source
            for s in record.skills
        }
        self.assertEqual(referenced - known, set())

    def test_every_armour_bonus_has_a_skills_row(self):
        # Bonuses are weighted through their skills-file row, matched by the
        # base name with " Set Bonus" / " Group Skill" stripped.
        data = game()
        known = {s.name for s in data.skills}
        bonuses = {
            bonus_base_name(b.name) for piece in data.armor for b in piece.set_bonuses
        }
        self.assertEqual(bonuses - known, set())

    def test_no_piece_carries_two_bonuses_of_one_kind(self):
        # Optimiser.impossible_requirements adds up the pieces required set
        # bonuses need, and separately group skills, as a proof. That sum is
        # only a proof while no piece carries two of the same kind.
        for piece in game().armor:
            kinds = Counter(b.type for b in piece.set_bonuses)
            self.assertLessEqual(max(kinds.values(), default=0), 1, piece.name)

    def test_bonus_thresholds(self):
        # The README and the GUI's level captions assume set bonuses are 2/4
        # pieces and group skills 3.
        shapes = {
            (b.type, tuple(e.pieces_required for e in b.effects))
            for piece in game().armor
            for b in piece.set_bonuses
        }
        self.assertEqual(shapes, {("set_bonus", (2, 4)), ("group_skill", (3,))})

    def test_piece_names_and_slot_set_pairs_are_unique(self):
        # Pins are by name on the CLI and by (slot, set) in the GUI; either
        # being ambiguous would pin the wrong piece.
        armor = game().armor
        self.assertEqual(len({p.name for p in armor}), len(armor))
        self.assertEqual(len({(p.piece_type, p.set) for p in armor}), len(armor))

    def test_no_piece_exceeds_a_skills_max_level(self):
        max_level = {s.name: s.max_level for s in game().skills}
        for piece in game().armor:
            for s in piece.skills:
                self.assertLessEqual(s.level, max_level[s.name], piece.name)


class RanksAndTranscending(unittest.TestCase):
    def test_ranks_split_by_rarity(self):
        # The Filters tab offers High and Low Rank as separate switches; a
        # piece filed under the wrong rank would follow the wrong one.
        for piece in game().armor:
            self.assertIn(piece.rank, ("high", "low"), piece.name)
            if piece.rank == "low":
                self.assertLessEqual(piece.rarity, 4, piece.name)
                self.assertEqual(piece.slots_source, "base", piece.name)
            else:
                self.assertGreaterEqual(piece.rarity, 5, piece.name)

    def test_every_transcended_piece_has_untranscended_values(self):
        # Switching transcending off swaps these in; a piece without them
        # would stay transcended with nothing on screen saying so.
        for piece in game().armor:
            if piece.slots_source == "transcended":
                self.assertIsNotNone(piece.untranscended, piece.name)
            else:
                self.assertIsNone(piece.untranscended, piece.name)

    def test_transcending_only_ever_improves_a_piece(self):
        # A per-slot comparison, largest first, so "[3,1,1] from [2]" passes
        # and a transcended list that lost a slot would not.
        for piece in game().armor:
            before = piece.untranscended
            if before is None:
                continue
            self.assertLess(before.defense_max, piece.defense.max, piece.name)
            after = sorted((s for s in piece.slots if s), reverse=True)
            base = sorted((s for s in before.slots if s), reverse=True)
            self.assertGreaterEqual(len(after), len(base), piece.name)
            for a, b in zip(after, base):
                self.assertGreaterEqual(a, b, piece.name)


class LoadSkills(unittest.TestCase):
    def _write(self, payload) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        with handle:
            yaml.safe_dump(payload, handle)
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_results_file_is_refused(self):
        path = self._write({"skills_db": "x", "sets": []})
        with self.assertRaisesRegex(ValueError, "results file"):
            load_skills(path)

    def test_non_skills_list_is_refused(self):
        path = self._write([{"name": "x"}])
        with self.assertRaisesRegex(ValueError, "not a skills file"):
            load_skills(path)

    def test_file_without_levels_still_loads(self):
        # A weighted file saved before the levels field existed.
        record = {
            "name": "Test Skill",
            "type": "Armor",
            "description": "",
            "max_level": 3,
            "scaling": "N/A",
            "weight": 2,
            "level_weight": 1,
            "source_url": "",
        }
        (skill,) = load_skills(self._write([record]))
        self.assertEqual(skill.levels, [])


if __name__ == "__main__":
    unittest.main()
