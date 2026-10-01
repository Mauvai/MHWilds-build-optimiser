"""check_data.py's comparisons, against real API responses saved as fixtures.

mhdb_high_rank_heads.json is the API's High Rank head list (name and slots)
as fetched on 2026-09-24, API version 2026-04-15. The single-record samples
below are copied from the same API.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import REPO, game

from check_data import compare_armor, compare_decorations, compare_skills, jewel_key

FIXTURES = Path(__file__).parent / "fixtures"

CLERK_VISOR = {
    "kind": "head",
    "name": "Clerk Visor α",
    "slots": [2, 1],
    "skills": [
        {"skill": {"name": "Recovery Up", "kind": "armor"}, "level": 1},
        {"skill": {"name": "Glory's Favor", "kind": "group"}, "level": 1},
        {"skill": {"name": "Divine Blessing", "kind": "armor"}, "level": 2},
        {"skill": {"name": "Palico Rally", "kind": "armor"}, "level": 2},
    ],
}
CHARGE_KO = {
    "name": "Charge/KO Jewel [3]",
    "slot": 3,
    "kind": "weapon",
    "skills": [
        {"skill": {"name": "Charge Master"}, "level": 3},
        {"skill": {"name": "Slugger"}, "level": 1},
    ],
}


def heads():
    return json.loads((FIXTURES / "mhdb_high_rank_heads.json").read_text(encoding="utf-8"))


def local_heads():
    # High Rank only, as check_data.main passes them: Low Rank is the API's
    # own data and is not compared.
    return [p for p in game().armor if p.piece_type == "head" and p.rank == "high"]


class JewelNames(unittest.TestCase):
    def test_both_spellings_meet(self):
        self.assertEqual(jewel_key("Charge/KO Jwl【3】"), jewel_key("Charge/KO Jewel [3]"))
        self.assertEqual(jewel_key("Adapt Jewel【1】"), jewel_key("Adapt Jewel [1]"))
        self.assertNotEqual(jewel_key("Attack Jewel II [2]"), jewel_key("Attack Jewel III [3]"))


class Armour(unittest.TestCase):
    def test_real_head_list(self):
        # 137 heads on each side and one disagreement, which is a real one:
        # the local file has Sealed Dragon Cloth α as a chest piece where the
        # API lists it as a head, and Pinion Necklace α as a head the API's
        # head list does not have. Every piece agrees on its slots: base-slot
        # pieces on their own, transcended ones on their untranscended slots.
        section = compare_armor(local_heads(), heads())
        self.assertEqual(section.only_api, ["Sealed Dragon Cloth α"])
        self.assertEqual(section.only_local, ["Pinion Necklace α"])
        self.assertEqual(section.changed, [])

    def test_matching_record_is_clean_and_group_skills_are_ignored(self):
        section = compare_armor(local_heads(), [CLERK_VISOR])
        self.assertEqual(section.changed, [])

    def test_changes_are_reported(self):
        record = dict(CLERK_VISOR, kind="chest")
        record["skills"] = CLERK_VISOR["skills"][:1]
        changed = compare_armor(local_heads(), [record]).changed
        self.assertEqual(len(changed), 2)  # slot type and skills; untranscended slots agree
        self.assertIn("slot head here, chest in the API", changed[0])

    def test_untranscended_slots_are_compared(self):
        record = dict(CLERK_VISOR, slots=[2, 2])
        (change,) = compare_armor(local_heads(), [record]).changed
        self.assertIn("slots [2, 1] here, [2, 2] in the API", change)

    def test_base_slots_are_compared(self):
        record = {"name": "Lagiacrus Helm β", "kind": "head", "slots": [3, 2]}
        (change,) = compare_armor(local_heads(), [record]).changed
        self.assertIn("slots [3, 2, 1] here, [3, 2] in the API", change)


class Decorations(unittest.TestCase):
    def test_matching_record_is_clean(self):
        section = compare_decorations(game().decorations, [CHARGE_KO])
        self.assertEqual(section.changed, [])
        self.assertEqual(section.only_api, [])

    def test_changes_are_reported(self):
        record = dict(CHARGE_KO, slot=2, skills=CHARGE_KO["skills"][:1])
        changed = compare_decorations(game().decorations, [record]).changed
        self.assertEqual(len(changed), 2)

    def test_new_jewel_is_listed(self):
        section = compare_decorations([], [CHARGE_KO])
        self.assertEqual(section.only_api, ["Charge/KO Jewel [3]"])


class Skills(unittest.TestCase):
    def test_max_level_and_new_skills(self):
        api = [
            {"name": "Charge Master", "kind": "weapon", "ranks": [{}, {}]},
            {"name": "Brand New Skill", "kind": "armor", "ranks": [{}]},
            {"name": "Gore Magala's Tyranny", "kind": "set", "ranks": [{}, {}]},
        ]
        section = compare_skills(game().skills, api)
        self.assertEqual(section.only_api, ["Brand New Skill"])
        self.assertIn("Charge Master: max level 3 here, 2 in the API", section.changed)
        # Only armour and weapon skills are compared, so everything else local
        # counts as absent from this three-record API.
        self.assertIn("Weakness Exploit", section.only_local)


class Cli(unittest.TestCase):
    def test_saved_api_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = Path(tmp)
            payloads = {
                "version": {"version": "2026-04-15T01:28:18+00:00"},
                "armor": [dict(h, kind="head") for h in heads()],
                "decorations": [CHARGE_KO],
                "skills": [],
            }
            for name, payload in payloads.items():
                (api / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(REPO / "check_data.py"), "--api-dir", str(api)],
                cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Wilds API data version 2026-04-15", result.stdout)
        # Against all local armour, the head list above shows up as a slot
        # disagreement rather than a missing piece.
        self.assertIn(
            "Sealed Dragon Cloth α: slot chest here, head in the API", result.stdout
        )

    def test_unreadable_api_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(REPO / "check_data.py"), "--api-dir", tmp],
                cwd=REPO, capture_output=True, text=True, encoding="utf-8",
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Could not read the API data", result.stderr)


if __name__ == "__main__":
    unittest.main()
