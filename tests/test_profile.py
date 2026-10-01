"""Search profiles: the file format, validation, the CLI and the GUI."""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.support import REPO, Value, game, import_gui, weighted
from tests.test_cli import run_cli

from search_profile import (
    SearchProfile,
    apply_weights,
    load_profile,
    profile_problems,
    save_profile,
    stored_path,
    weights_of,
)

FULL = SearchProfile(
    weights={"Weakness Exploit": (5.0, 0.0), "Attack Boost": (4.0, 3.0)},
    pins={"waist": "Gore Coil α"},
    exclude_sets=["Lagiacrus β"],
    exclude_pieces=["Udra Mirehelm γ"],
    weapon_slots=[3, 2],
    gogma_set_bonus="Gore Magala's Tyranny",
    gogma_group_skill=None,
    reserve=1,
    relax=False,
)


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write(self, name, payload) -> Path:
        path = self.tmp / name
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        return path


class Format(Tmp):
    def test_round_trip(self):
        path = self.tmp / "p.yaml"
        save_profile(FULL, path)
        self.assertEqual(load_profile(path), FULL)

    def test_saved_file_reads_like_the_gui(self):
        path = self.tmp / "p.yaml"
        save_profile(FULL, path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["weights"]["Weakness Exploit"], {"weight": 5, "level_weight": 0})

    def test_other_files_are_refused(self):
        cases = {
            "skills file": [{"name": "x"}],
            "results file": {"sets": []},
            "no profile_version": {"weights": {}},
        }
        for label, payload in cases.items():
            with self.assertRaises(ValueError, msg=label):
                load_profile(self.write("x.yaml", payload))

    def test_unknown_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown keys: exclude_set"):
            load_profile(self.write("x.yaml", {"profile_version": 1, "exclude_set": ["a"]}))

    def test_other_version_is_refused(self):
        with self.assertRaisesRegex(ValueError, "version 2"):
            load_profile(self.write("x.yaml", {"profile_version": 2}))

    def test_bad_types_are_refused(self):
        for payload in (
            {"weights": {"Weakness Exploit": {"weight": "high"}}},
            {"weights": {"Weakness Exploit": 5}},
            {"reserve": "2"},
            {"relax": "yes"},
            {"weapon_slots": "3,2"},
            {"exclude_sets": "Gore α"},
        ):
            with self.assertRaises(ValueError, msg=payload):
                load_profile(self.write("x.yaml", {"profile_version": 1, **payload}))

    def test_minimal_profile_takes_defaults(self):
        profile = load_profile(self.write("x.yaml", {"profile_version": 1}))
        self.assertEqual(profile, SearchProfile())


class Weights(unittest.TestCase):
    def test_weights_of_keeps_only_weighted(self):
        skills = weighted({"Weakness Exploit": (5, 0), "Attack Boost": (0, 3)})
        self.assertEqual(
            weights_of(skills),
            {"Weakness Exploit": (5, 0), "Attack Boost": (0, 3)},
        )

    def test_apply_weights_zeroes_everything_else(self):
        skills = apply_weights(
            weighted({"Agitator": (4, 4)}), {"Weakness Exploit": (5.0, 0.0)}
        )
        by_name = {s.name: s for s in skills}
        self.assertEqual(by_name["Weakness Exploit"].weight, 5.0)
        self.assertEqual(by_name["Agitator"].weight, 0.0)

    def test_stored_path_is_repo_relative_inside_the_repo(self):
        inside = REPO / "custom_talismans_outputs" / "mine.yaml"
        self.assertEqual(stored_path(inside), "custom_talismans_outputs/mine.yaml")
        self.assertIsNone(stored_path(None))


class Problems(unittest.TestCase):
    def test_full_profile_is_clean(self):
        self.assertEqual(profile_problems(FULL, game()), [])

    def test_every_problem_is_reported_at_once(self):
        bad = SearchProfile(
            weights={"Nope": (1, 0)},
            pins={"head": "Lagiacrus Mail β", "hat": "x"},
            exclude_sets=["No Set"],
            exclude_pieces=["No Piece"],
            weapon_slots=[4],
            gogma_set_bonus="Alluring Pelt",  # a group skill, not a set bonus
            reserve=-1,
            custom_talismans="custom_talismans_outputs/does_not_exist.yaml",
        )
        problems = profile_problems(bad, game())
        self.assertEqual(len(problems), 9, problems)


class Cli(Tmp):
    def test_profile_run(self):
        path = self.tmp / "p.yaml"
        save_profile(FULL, path)
        result = run_cli(
            "--profile", str(path), "--count", "2", "--output", str(self.tmp / "o.yaml")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waist Gore Coil α", result.stdout)
        self.assertIn("Excluded: Lagiacrus β, Udra Mirehelm γ", result.stdout)
        self.assertIn("weapon", result.stdout)

    def test_command_line_overrides_the_profile(self):
        path = self.tmp / "p.yaml"
        save_profile(FULL, path)
        saved = self.tmp / "effective.yaml"
        result = run_cli(
            "--profile", str(path), "--count", "1", "--output", str(self.tmp / "o.yaml"),
            "--pin-waist", "Arkvulcan Coil γ", "--reserve", "0",
            "--exclude-set", "Gore α", "--weapon-slots", "3",
            "--save-profile", str(saved),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        effective = load_profile(saved)
        self.assertEqual(effective.pins, {"waist": "Arkvulcan Coil γ"})
        self.assertEqual(effective.reserve, 0)
        self.assertEqual(effective.weapon_slots, [3])
        self.assertEqual(set(effective.exclude_sets), {"Lagiacrus β", "Gore α"})
        self.assertEqual(effective.weights, FULL.weights)

    def test_profile_and_skills_db_are_exclusive(self):
        result = run_cli("--profile", "a.yaml", "--skills-db", "b.yaml")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with", result.stderr)

    def test_bad_profile_is_a_usage_error(self):
        path = self.tmp / "p.yaml"
        save_profile(SearchProfile(pins={"head": "Nope"}), path)
        result = run_cli("--profile", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Pinned piece 'Nope' does not exist", result.stderr)

    def test_missing_skills_db_is_a_usage_error(self):
        result = run_cli("--skills-db", str(self.tmp / "missing.yaml"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)

    def test_skills_db_can_be_saved_as_a_profile(self):
        saved = self.tmp / "from_db.yaml"
        result = run_cli(
            "--skills-db", "skills_weighted.yaml", "--count", "1",
            "--output", str(self.tmp / "o.yaml"), "--save-profile", str(saved),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            load_profile(saved).weights,
            {"Antivirus": (5.0, 0.0), "Constitution": (5.0, 0.0), "Weakness Exploit": (5.0, 0.0)},
        )


class Gui(Tmp):
    def stub(self):
        G = import_gui()
        skills = weighted(dict(FULL.weights))
        stub = types.SimpleNamespace(
            skills=skills,
            cache={
                s.name: {
                    "weight": G._weight_text(s.weight),
                    "level_weight": G._weight_text(s.level_weight),
                }
                for s in skills
            },
            reserved_slots_var=Value("1"),
            gogma_set_var=Value("Gore Magala's Tyranny"),
            gogma_group_var=Value(G.NONE_OPTION),
            excluded_sets=set(FULL.exclude_sets),
            excluded_pieces=set(FULL.exclude_pieces),
            relax_var=Value(False),
            custom_talismans_source=None,
            _pinned_pieces=lambda: dict(FULL.pins),
            _weapon_slots=lambda: (3, 2),
            _current_filters=lambda: FULL.filters,
            _current_targets=lambda: FULL.targets,
            game_data=game(),
            file_label_var=Value(),
            status_var=Value(),
        )
        stub._effective_skills = lambda: G.SkillsGui._effective_skills(stub)
        return G, stub

    def test_current_profile_matches_the_tab(self):
        G, stub = self.stub()
        self.assertEqual(G.SkillsGui._current_profile(stub), FULL)

    def test_saving_clears_unsaved_weights(self):
        G, stub = self.stub()
        stub.cache["Agitator"]["weight"] = "3"
        stub.skills_by_name = {}
        G.SkillsGui._write_profile(stub, G.SkillsGui._current_profile(stub), self.tmp / "p.yaml")
        self.assertIn("Agitator", load_profile(self.tmp / "p.yaml").weights)
        self.assertFalse(G.SkillsGui._has_unsaved_changes(stub))

    def test_mismatched_profile_is_refused_whole(self):
        G, stub = self.stub()
        path = self.tmp / "bad.yaml"
        save_profile(SearchProfile(weights={"Nope": (1, 0)}), path)
        stub._use_skills = mock.MagicMock()
        with mock.patch.object(G, "messagebox") as box:
            self.assertFalse(G.SkillsGui._read_profile(stub, path))
            self.assertIn("Unknown skills: Nope", box.showerror.call_args.args[1])
        stub._use_skills.assert_not_called()


if __name__ == "__main__":
    unittest.main()
