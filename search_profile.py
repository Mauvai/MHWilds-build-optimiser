"""Search profiles: one file holding everything a run needs besides the data.

A profile stores the weights of the skills that are weighted and every
search setting - pins, exclusions, weapon slots, Gogma credit, reserve,
relaxing and the custom talisman file - so a build goal is one file that
the GUI and the CLI both load.

It stores weights as a short list against skills_default.yaml rather than as
a whole skills file. A weighted skills file is a full copy of the skill data
with weights filled in, so the next data refresh leaves it holding the old
descriptions and missing any new skills; a profile picks up the refreshed
data the next time it is loaded, because the data is never in it.

Named search_profile, not profile, because the standard library already has
a profile module and a local one would shadow it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from gear_filters import GearFilters
from load_data import DATA_DIR, GameData, Skill, load_talismans
from optimiser import (
    MAX_WEAPON_SLOTS,
    BuildTargets,
    PIECE_TYPES,
    RESERVED_SLOTS,
    TALISMAN_SLOT,
    bonus_base_name,
)

PROFILE_VERSION = 1
PROFILES_DIR = DATA_DIR / "profiles"
PROFILE_KEYS = {
    "profile_version",
    "weights",
    "pins",
    "exclude_sets",
    "exclude_pieces",
    "weapon_slots",
    "gogma_set_bonus",
    "gogma_group_skill",
    "reserve",
    "relax",
    "custom_talismans",
    "filters",
    "targets",
}


@dataclass
class SearchProfile:
    weights: dict[str, tuple[float, float]] = field(default_factory=dict)
    # slot -> piece name; TALISMAN_SLOT -> talisman name
    pins: dict[str, str] = field(default_factory=dict)
    exclude_sets: list[str] = field(default_factory=list)
    exclude_pieces: list[str] = field(default_factory=list)
    weapon_slots: list[int] = field(default_factory=list)
    gogma_set_bonus: str | None = None
    gogma_group_skill: str | None = None
    reserve: int = RESERVED_SLOTS
    relax: bool = False
    custom_talismans: str | None = None  # as written: relative to the repo if inside it
    filters: GearFilters = field(default_factory=GearFilters)
    targets: BuildTargets = field(default_factory=BuildTargets)

    def extra_bonus_pieces(self) -> dict[str, int]:
        """The Gogma choices in the form Optimiser takes."""
        extra: dict[str, int] = {}
        for name in (self.gogma_set_bonus, self.gogma_group_skill):
            if name:
                extra[name] = extra.get(name, 0) + 1
        return extra

    def custom_talismans_path(self) -> Path | None:
        if not self.custom_talismans:
            return None
        path = Path(self.custom_talismans)
        return path if path.is_absolute() else DATA_DIR / path


def weights_of(skills: list[Skill]) -> dict[str, tuple[float, float]]:
    """The weighted skills only, in file order: what a profile stores."""
    return {
        s.name: (s.weight, s.level_weight)
        for s in skills
        if s.weight != 0 or s.level_weight != 0
    }


def apply_weights(
    skills: list[Skill], weights: dict[str, tuple[float, float]]
) -> list[Skill]:
    """Skills with every weight zeroed except those the profile names."""
    return [
        replace(s, weight=weights[s.name][0], level_weight=weights[s.name][1])
        if s.name in weights
        else replace(s, weight=0.0, level_weight=0.0)
        for s in skills
    ]


def stored_path(path: Path | None) -> str | None:
    """Relative to the repo when inside it, so a profile survives a move of
    the whole checkout; absolute otherwise, since there is nothing stable to
    be relative to."""
    if path is None:
        return None
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(DATA_DIR).as_posix()
    except ValueError:
        return str(resolved)


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, not {value!r}.")
    return float(value)


def load_profile(path: Path) -> SearchProfile:
    """Read and shape-check a profile file.

    Only the shape is checked here - types, known keys, the version. Whether
    the names in it exist in the game data is profile_problems' job, because
    that needs the data and a profile should load before the data is known
    to be current.
    """
    with Path(path).open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    name = Path(path).name
    if isinstance(raw, list):
        raise ValueError(f"{name} is a skills file, not a search profile.")
    if isinstance(raw, dict) and "sets" in raw:
        raise ValueError(f"{name} is an optimiser results file, not a search profile.")
    if not isinstance(raw, dict) or "profile_version" not in raw:
        raise ValueError(f"{name} is not a search profile: no profile_version.")
    if raw["profile_version"] != PROFILE_VERSION:
        raise ValueError(
            f"{name} is profile version {raw['profile_version']!r}; this version "
            f"of the optimiser reads version {PROFILE_VERSION}."
        )
    unknown = set(raw) - PROFILE_KEYS
    if unknown:
        # Refused rather than ignored: an unknown key is most often a typo of
        # a known one, whose setting would otherwise silently not apply.
        raise ValueError(f"{name} has unknown keys: {', '.join(sorted(unknown))}.")

    weights: dict[str, tuple[float, float]] = {}
    for skill, entry in (raw.get("weights") or {}).items():
        if not isinstance(entry, dict) or set(entry) - {"weight", "level_weight"}:
            raise ValueError(
                f"{name}: weights for {skill!r} need weight and level_weight."
            )
        weights[str(skill)] = (
            _number(entry.get("weight", 0), f"{skill} weight"),
            _number(entry.get("level_weight", 0), f"{skill} level_weight"),
        )

    def string_list(key: str) -> list[str]:
        value = raw.get(key) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{name}: {key} must be a list of names.")
        return value

    pins = raw.get("pins") or {}
    if not isinstance(pins, dict) or not all(isinstance(v, str) for v in pins.values()):
        raise ValueError(f"{name}: pins must map slots to piece names.")

    weapon_slots = raw.get("weapon_slots") or []
    if not isinstance(weapon_slots, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in weapon_slots
    ):
        raise ValueError(f"{name}: weapon_slots must be a list of sizes.")

    reserve = raw.get("reserve", RESERVED_SLOTS)
    if isinstance(reserve, bool) or not isinstance(reserve, int):
        raise ValueError(f"{name}: reserve must be a whole number.")
    relax = raw.get("relax", False)
    if not isinstance(relax, bool):
        raise ValueError(f"{name}: relax must be true or false.")

    return SearchProfile(
        weights=weights,
        pins={str(k): v for k, v in pins.items()},
        exclude_sets=string_list("exclude_sets"),
        exclude_pieces=string_list("exclude_pieces"),
        weapon_slots=list(weapon_slots),
        gogma_set_bonus=raw.get("gogma_set_bonus") or None,
        gogma_group_skill=raw.get("gogma_group_skill") or None,
        reserve=reserve,
        relax=relax,
        custom_talismans=raw.get("custom_talismans") or None,
        filters=GearFilters.from_dict(raw.get("filters"), f"{name}: filters"),
        targets=BuildTargets.from_dict(raw.get("targets"), f"{name}: targets"),
    )


def profile_problems(profile: SearchProfile, game: GameData) -> list[str]:
    """Every name or value in the profile the game data does not recognise.

    All collected at once rather than stopping at the first, so a profile
    written against older data can be fixed in one pass.
    """
    problems: list[str] = []
    skills = {s.name: s for s in game.skills}
    pieces = {p.name: p for p in game.armor}
    sets = {p.set for p in game.armor}
    bonuses = {bonus_base_name(b.name): b.type for p in game.armor for b in p.set_bonuses}

    unknown = [n for n in profile.weights if n not in skills]
    if unknown:
        problems.append("Unknown skills: " + ", ".join(unknown))
    for slot, piece_name in profile.pins.items():
        piece = pieces.get(piece_name)
        if slot == TALISMAN_SLOT:
            if piece_name not in _talisman_names(profile, game):
                problems.append(
                    f"Pinned talisman {piece_name!r} is neither craftable nor in "
                    "the custom talisman file."
                )
        elif slot not in PIECE_TYPES:
            problems.append(f"Pin on unknown slot {slot!r}.")
        elif piece is None:
            problems.append(f"Pinned piece {piece_name!r} does not exist.")
        elif piece.piece_type != slot:
            problems.append(f"{piece_name!r} is a {piece.piece_type} piece, pinned to {slot}.")
    for name in profile.exclude_sets:
        if name not in sets:
            problems.append(f"Excluded set {name!r} does not exist.")
    for name in profile.exclude_pieces:
        if name not in pieces:
            problems.append(f"Excluded piece {name!r} does not exist.")
    if len(profile.weapon_slots) > MAX_WEAPON_SLOTS or any(
        s not in (1, 2, 3) for s in profile.weapon_slots
    ):
        problems.append(
            f"weapon_slots {profile.weapon_slots} is not up to {MAX_WEAPON_SLOTS} "
            "sizes of 1-3."
        )
    for name, kind in (
        (profile.gogma_set_bonus, "set_bonus"),
        (profile.gogma_group_skill, "group_skill"),
    ):
        if name and bonuses.get(name) != kind:
            label = "set bonus" if kind == "set_bonus" else "group skill"
            problems.append(f"Gogma {label} {name!r} is not one any armour carries.")
    if profile.reserve < 0:
        problems.append("reserve cannot be negative.")
    talismans = profile.custom_talismans_path()
    if talismans is not None and not talismans.exists():
        problems.append(f"Custom talisman file {profile.custom_talismans} does not exist.")
    return problems


def _talisman_names(profile: SearchProfile, game: GameData) -> set[str]:
    """Craftable talismans plus the profile's custom file, if it reads.

    A custom file that does not read adds nothing here; the caller reports
    the file itself, so the pin is not blamed for the file's fault twice.
    """
    names = {t.name for t in game.talismans}
    path = profile.custom_talismans_path()
    if path is not None and path.exists():
        try:
            names |= {t.name for t in load_talismans(path)}
        except Exception:  # noqa: BLE001
            pass
    return names


def save_profile(profile: SearchProfile, path: Path) -> None:
    """Write a profile, keys in a fixed order so saved files diff cleanly."""
    payload = {
        "profile_version": PROFILE_VERSION,
        "weights": {
            name: {"weight": _plain(w), "level_weight": _plain(lw)}
            for name, (w, lw) in profile.weights.items()
        },
        "pins": {
            slot: profile.pins[slot]
            for slot in (*PIECE_TYPES, TALISMAN_SLOT)
            if slot in profile.pins
        },
        "exclude_sets": sorted(profile.exclude_sets),
        "exclude_pieces": sorted(profile.exclude_pieces),
        "weapon_slots": list(profile.weapon_slots),
        "gogma_set_bonus": profile.gogma_set_bonus,
        "gogma_group_skill": profile.gogma_group_skill,
        "reserve": profile.reserve,
        "relax": profile.relax,
        "custom_talismans": profile.custom_talismans,
        # Only the filters that differ from include-everything, so a
        # profile from before filters existed reads the same once resaved.
        "filters": profile.filters.to_dict(),
        "targets": profile.targets.to_dict(),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(payload, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _plain(value: float):
    """5.0 -> 5, so the file reads the way the GUI's dropdowns do."""
    return int(value) if float(value).is_integer() else value
