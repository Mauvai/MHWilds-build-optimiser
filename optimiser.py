"""Gear set optimiser for Monster Hunter Wilds.

Reads a weighted skills DB (each skill carrying a 'weight' and 'level_weight')
and searches for gear sets: one armour piece per equipment slot, one talisman,
and decorations placed only where they raise a weighted skill.

This module is deliberately free of printing so a GUI can import and call
``optimise()`` directly. Rendering lives in optimiser_report.py.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from load_data import (
    ArmorPiece,
    Decoration,
    GameData,
    Skill,
    Talisman,
    load_game_data,
    load_skills,
)

# --- tuning constants -------------------------------------------------------

# How much more a high-weight skill matters than a low-weight one. At 1.0 a
# weight-2 skill sitting at level 1 outscores a further level of a weight-4
# skill, which fills sets with shallow filler; raising it buys depth in the
# skills that matter over breadth of cheap level-1 skills. Above ~2.5 the gap
# gets extreme enough that mid-weight skills start losing their depth again.
WEIGHT_EXPONENT = 2.0

# What each level after the first is worth, as a multiple of the first level,
# at level_weight 5; it falls linearly to nothing at level_weight 0. Above 1, so
# a skill you want levelled pulls toward finishing it rather than toward merely
# having it. A level's value never depends on the skill's max level: splitting
# a fixed total across the levels made each level of a 5-level skill worth less
# than one of a 3-level skill, so a weight-3 skill's first level outbid a
# weight-4 one's next level for the same jewel slot.
TOP_LEVEL_VALUE = 1.5

# Whole-set defence is worth about as much as one skill of this weight. It is
# raised to WEIGHT_EXPONENT alongside skills so the balance holds if that moves.
DEFENSE_EQUIV_WEIGHT = 2.0
DEFENSE_EXPONENT = 2.5  # >1 makes low-defence pieces substantially worse
DEFENSE_FLOOR = 62
DEFENSE_CEILING = 94

MANDATORY_WEIGHT = 5.0  # weight at which a skill is treated as mandatory
MAX_LEVEL_WEIGHT = 5.0  # level_weight at which a skill must reach max level

RESERVED_SLOTS = 2  # slots held back for per-hunt resistance jewels
# 3000/1200 left ~1% of score on the table; the result plateaus here and wider
# beams (9000/15000) find nothing better, at several times the runtime.
BEAM_WIDTH = 5000
FINAL_POOL = 2000  # complete armour combos given the full evaluation
TALISMAN_SHORTLIST = 5

# The talisman schema stores a decoration slot *count* with no size, so a count
# is read as this many slots of this size. A list of sizes is also accepted.
TALISMAN_ARMOUR_SLOT_SIZE = 1

# Credit given to partial progress toward a set/group bonus threshold, so the
# beam search will hold onto states that are part-way to completing one.
BONUS_PROGRESS_CREDIT = 0.8

PIECE_TYPES = ("head", "chest", "arms", "waist", "legs")
# The pins key for a fixed talisman. It rides in the same dict as the armour
# pins so profiles, the CLI and the GUI carry it without a parallel setting.
TALISMAN_SLOT = "talisman"

# Rough share of a run spent in the armour beam search, the rest going to
# evaluating the final pool (talismans and decorations for each set). Only
# steers the progress bar, measured on a 30-skill weighting: 4.7 s against
# 21.1 s.
SEARCH_SHARE = 0.2
# How many states or sets pass between progress reports and cancel checks.
# Small enough that Cancel answers within a fraction of a second, large
# enough that the checks cost nothing measurable.
CHECK_EVERY = 64


class SearchCancelled(Exception):
    """Raised inside Optimiser.run when its should_stop callback says so."""
SET_BONUS_SUFFIX = " Set Bonus"
GROUP_SKILL_SUFFIX = " Group Skill"


@dataclass(frozen=True)
class DiversityTier:
    """One band of the final result list."""

    label: str
    count: int
    min_piece_diff: int = 1
    require_new_bonuses: bool = False


DEFAULT_TIERS = (
    DiversityTier("closest variants", 3, min_piece_diff=1),
    DiversityTier("distinct builds", 3, min_piece_diff=2),
    DiversityTier("distinct bonuses", 4, min_piece_diff=1, require_new_bonuses=True),
)


# --- scoring ----------------------------------------------------------------


def defense_value(defense_max: int) -> float:
    """Normalised, non-linear worth of a piece's max defence."""
    span = DEFENSE_CEILING - DEFENSE_FLOOR
    t = (defense_max - DEFENSE_FLOOR) / span
    return max(0.0, min(1.0, t)) ** DEFENSE_EXPONENT


def bonus_base_name(name: str) -> str:
    """'Gore Magala's Tyranny Set Bonus' -> 'Gore Magala's Tyranny'."""
    for suffix in (SET_BONUS_SUFFIX, GROUP_SKILL_SUFFIX):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


class Scoring:
    """Turns achieved skill levels into a score using the weighted skills DB."""

    def __init__(
        self,
        skills: list[Skill],
        weight_exponent: float = WEIGHT_EXPONENT,
        top_level_value: float = TOP_LEVEL_VALUE,
        defense_equiv_weight: float = DEFENSE_EQUIV_WEIGHT,
    ) -> None:
        self.weight_exponent = weight_exponent
        self.top_level_value = top_level_value
        self.defense_points = defense_equiv_weight**weight_exponent
        self.by_name = {s.name: s for s in skills}
        self.relevant = sorted(s.name for s in skills if s.weight != 0)
        self.mandatory = {s.name for s in skills if s.weight >= MANDATORY_WEIGHT}
        self.mandatory_max = {
            s.name
            for s in skills
            if s.weight >= MANDATORY_WEIGHT and s.level_weight >= MAX_LEVEL_WEIGHT
        }
        self._score_cache: dict[tuple[str, int], float] = {}

    def max_level(self, name: str) -> int:
        return self.by_name[name].max_level

    def weight(self, name: str) -> float:
        skill = self.by_name.get(name)
        return skill.weight if skill else 0.0

    def score(self, name: str, level: int) -> float:
        key = (name, level)
        cached = self._score_cache.get(key)
        if cached is not None:
            return cached

        skill = self.by_name.get(name)
        if skill is None or skill.weight == 0 or level <= 0:
            value = 0.0
        else:
            capped = min(level, skill.max_level)
            # The first level is worth the skill's importance; each one after
            # it a share of that set by level_weight (see TOP_LEVEL_VALUE).
            per_level = self.top_level_value * max(0.0, min(1.0, skill.level_weight / 5.0))
            # Sign-preserving, so 'actively avoid' weights stay negative.
            importance = math.copysign(
                abs(skill.weight) ** self.weight_exponent, skill.weight
            )
            value = importance * (1.0 + per_level * (capped - 1))

        self._score_cache[key] = value
        return value

    def marginal(self, name: str, level: int) -> float:
        """Value of raising a skill from ``level`` to ``level + 1``."""
        return self.score(name, level + 1) - self.score(name, level)


# --- gear profiles ----------------------------------------------------------


def slot_counts(sizes) -> tuple[int, int, int]:
    """Slot sizes -> counts of (size-1, size-2, size-3) slots."""
    counts = [0, 0, 0]
    for size in sizes:
        if 1 <= size <= 3:
            counts[size - 1] += 1
    return counts[0], counts[1], counts[2]


def talisman_slot_sizes(talisman: Talisman) -> tuple[list[int], list[int]]:
    """(armour slot sizes, weapon slot sizes) for a talisman.

    Craftable talismans currently have none of either, but appraised ones are
    expected to, so both the count form and a list-of-sizes form are accepted.
    A bare count carries no sizes, so it is read as that many size-1 slots.
    """
    deco = talisman.decoration_slots

    def sizes_of(value) -> list[int]:
        if isinstance(value, (list, tuple)):
            return [int(s) for s in value if s]
        return [TALISMAN_ARMOUR_SLOT_SIZE] * int(value or 0)

    return sizes_of(deco.armour), sizes_of(deco.weapon)


def consume_slot(slots: tuple[int, int, int], size: int, count: int = 1):
    """Spend ``count`` slots able to hold a size-``size`` decoration.

    Always takes the smallest adequate slot, which is optimal here: slot
    capability is nested (a size-3 slot accepts anything a size-1 one does), so
    never burning a large slot on a small gem can only help later placements.
    """
    n1, n2, n3 = slots
    for _ in range(count):
        if size <= 1 and n1:
            n1 -= 1
        elif size <= 2 and n2:
            n2 -= 1
        elif n3:
            n3 -= 1
        else:
            return None
    return n1, n2, n3


@dataclass
class PieceProfile:
    """An armour piece reduced to only what the search cares about."""

    piece: ArmorPiece
    skill_levels: tuple[int, ...]  # indexed by Context.relevant_skills
    slots: tuple[int, int, int]
    defense: int
    defense_value: float
    bonus_indices: tuple[int, ...]  # indexed by Context.relevant_bonuses


@dataclass
class BonusInfo:
    name: str
    bonus_type: str
    thresholds: tuple[int, ...]
    effects: tuple[str, ...]

    def level_for(self, pieces: int) -> int:
        return sum(1 for t in self.thresholds if pieces >= t)


def resolve_pins(
    game: GameData, pinned_pieces: dict[str, str] | None
) -> dict[str, ArmorPiece]:
    """Equipment slot -> the armour piece pinned to it.

    Piece names are unique across the armour data, so a name alone identifies a
    piece. Every input is validated here rather than at the call site because an
    unrecognised name would otherwise reach the beam as an empty candidate list,
    turning a typo into a search that silently returns nothing.
    """
    if not pinned_pieces:
        return {}

    by_name = {piece.name: piece for piece in game.armor}
    resolved: dict[str, ArmorPiece] = {}
    for piece_type, name in pinned_pieces.items():
        if not name or piece_type == TALISMAN_SLOT:
            continue
        if piece_type not in PIECE_TYPES:
            raise ValueError(
                f"{piece_type!r} is not an equipment slot; expected one of "
                + ", ".join(PIECE_TYPES)
            )
        piece = by_name.get(name)
        if piece is None:
            raise ValueError(f"No armour piece is named {name!r}.")
        if piece.piece_type != piece_type:
            raise ValueError(
                f"{name!r} is a {piece.piece_type} piece and cannot be pinned to "
                f"the {piece_type} slot."
            )
        resolved[piece_type] = piece
    return resolved


def resolve_talisman_pin(
    game: GameData, pinned_pieces: dict[str, str] | None
) -> Talisman | None:
    """The talisman pinned under TALISMAN_SLOT, or None.

    Looked up in game.talismans, so a custom talisman can be pinned once its
    file has been folded into the pool.
    """
    name = (pinned_pieces or {}).get(TALISMAN_SLOT)
    if not name:
        return None
    for talisman in game.talismans:
        if talisman.name == name:
            return talisman
    raise ValueError(f"No talisman is named {name!r}.")


def resolve_exclusions(
    game: GameData,
    excluded_sets: list[str] | set[str] | None = None,
    excluded_pieces: list[str] | set[str] | None = None,
) -> set[str]:
    """Names of every armour piece ruled out, by set or individually.

    Validated for the same reason pins are: an unknown name would otherwise
    exclude nothing, and a typo would quietly search with the piece still in.
    """
    all_sets = {piece.set for piece in game.armor}
    all_pieces = {piece.name for piece in game.armor}
    for name in excluded_sets or ():
        if name not in all_sets:
            raise ValueError(f"No armour set is named {name!r}.")
    for name in excluded_pieces or ():
        if name not in all_pieces:
            raise ValueError(f"No armour piece is named {name!r}.")

    sets = set(excluded_sets or ())
    return {
        piece.name
        for piece in game.armor
        if piece.set in sets or piece.name in set(excluded_pieces or ())
    }


class Context:
    """Precomputed, weight-profile-specific view of the game data."""

    def __init__(
        self,
        game: GameData,
        scoring: Scoring,
        pinned_pieces: dict[str, str] | None = None,
        excluded_sets: list[str] | set[str] | None = None,
        excluded_pieces: list[str] | set[str] | None = None,
        weapon_slots: tuple[int, ...] = (),
    ) -> None:
        # A pinned talisman narrows the pool to itself, and everything that
        # reads game.talismans - reachability, the proofs, the per-set
        # shortlist - then sees only that one without knowing about pins.
        self.pinned_talisman = resolve_talisman_pin(game, pinned_pieces)
        if self.pinned_talisman is not None:
            game = replace(game, talismans=[self.pinned_talisman])
        self.game = game
        self.scoring = scoring
        self.weapon_slots = tuple(weapon_slots)
        self.pinned = resolve_pins(game, pinned_pieces)
        self.excluded = resolve_exclusions(game, excluded_sets, excluded_pieces)
        # Both are the user saying opposite things about one piece. Refused
        # rather than resolved either way, because each resolution silently
        # ignores one of the two instructions.
        for piece_type, piece in self.pinned.items():
            if piece.name in self.excluded:
                raise ValueError(
                    f"{piece.name!r} is pinned to the {piece_type} slot but also "
                    "excluded."
                )
        # Everything below that asks what armour can supply asks this list,
        # not game.armor, so exclusions reach the search, the reachability
        # warning and the pre-search proofs alike.
        self.available_armor = [a for a in game.armor if a.name not in self.excluded]

        # The registry keeps every bonus, excluded carriers or not: it only
        # records each bonus's thresholds, which exclusions do not change.
        self.bonus_registry = self._build_bonus_registry(game)

        reachable = self._gear_reachable_skills()
        self.relevant_skills = [name for name in scoring.relevant if name in reachable]
        self.skill_index = {name: i for i, name in enumerate(self.relevant_skills)}

        self.relevant_bonuses = sorted(
            name for name in self.bonus_registry if scoring.weight(name) != 0
        )
        self.bonus_index = {name: i for i, name in enumerate(self.relevant_bonuses)}

        self.decoration_for_skill = self._best_decorations(game)
        # A pinned slot skips prune_dominated entirely: pruning only decides
        # between alternatives, and a pinned slot has none. The single candidate
        # also makes the slot sort first in _search_armour's stage order, so every
        # later decision is ranked with the pinned piece already counted.
        self.candidates = {
            piece_type: (
                [self.profile(self.pinned[piece_type])]
                if piece_type in self.pinned
                else prune_dominated(
                    [
                        self.profile(a)
                        for a in self.available_armor
                        if a.piece_type == piece_type
                    ],
                    scoring,
                    self.relevant_skills,
                )
            )
            for piece_type in PIECE_TYPES
        }

    @staticmethod
    def _build_bonus_registry(game: GameData) -> dict[str, BonusInfo]:
        registry: dict[str, BonusInfo] = {}
        for piece in game.armor:
            for bonus in piece.set_bonuses:
                base = bonus_base_name(bonus.name)
                if base not in registry:
                    registry[base] = BonusInfo(
                        name=base,
                        bonus_type=bonus.type,
                        thresholds=tuple(e.pieces_required for e in bonus.effects),
                        effects=tuple(e.skill for e in bonus.effects),
                    )
        return registry

    def _gear_reachable_skills(self) -> set[str]:
        """Skills obtainable from non-excluded armour, talismans or armour gems."""
        game = self.game
        reachable = {s.name for a in self.available_armor for s in a.skills}
        reachable |= {s.name for t in game.talismans for s in t.skills}
        reachable |= {
            s.name for d in game.decorations if d.type == "armor" for s in d.skills
        }
        return reachable

    def _best_decorations(self, game: GameData) -> dict[str, Decoration]:
        """Cheapest armour decoration granting each skill."""
        best: dict[str, Decoration] = {}
        for deco in game.decorations:
            if deco.type != "armor":
                continue
            for granted in deco.skills:
                current = best.get(granted.name)
                if current is None or deco.slot_level < current.slot_level:
                    best[granted.name] = deco
        return best

    def weapon_jewel_skills(self) -> set[str]:
        return {s.name for d in self.game.decorations if d.type == "weapon" for s in d.skills}

    def weapon_slots_exist(self) -> bool:
        """Is there anywhere at all a weapon jewel could go?

        The weapon's own slots, or a talisman's - custom ones can carry them,
        and a run with such a talisman loaded can use them even with no
        weapon slots given.
        """
        return bool(self.weapon_slots) or any(
            talisman_slot_sizes(t)[1] for t in self.game.talismans
        )

    def unreachable_weighted_skills(self) -> list[str]:
        reachable = self._gear_reachable_skills()
        if self.weapon_slots_exist():
            reachable |= self.weapon_jewel_skills()
        return [
            name
            for name in self.scoring.relevant
            if name not in reachable and name not in self.bonus_registry
        ]

    def profile(self, piece: ArmorPiece) -> PieceProfile:
        levels = [0] * len(self.relevant_skills)
        for granted in piece.skills:
            idx = self.skill_index.get(granted.name)
            if idx is not None:
                levels[idx] += granted.level

        bonuses = []
        for bonus in piece.set_bonuses:
            idx = self.bonus_index.get(bonus_base_name(bonus.name))
            if idx is not None:
                bonuses.append(idx)

        return PieceProfile(
            piece=piece,
            skill_levels=tuple(levels),
            slots=slot_counts(piece.slots),
            defense=piece.defense.max,
            defense_value=defense_value(piece.defense.max),
            bonus_indices=tuple(sorted(bonuses)),
        )


def prune_dominated(
    profiles: list[PieceProfile], scoring: Scoring, relevant_skills: list[str]
) -> list[PieceProfile]:
    """Drop pieces that some other piece is at least as good as in every way.

    Pieces are only compared within the same relevant-bonus group, since a
    piece carrying a weighted set bonus is never interchangeable with one that
    doesn't.
    """
    wants_more = [scoring.weight(name) > 0 for name in relevant_skills]

    def cumulative(slots: tuple[int, int, int]) -> tuple[int, int, int]:
        n1, n2, n3 = slots
        return n3, n3 + n2, n3 + n2 + n1

    def dominates(a: PieceProfile, b: PieceProfile) -> bool:
        for i, more_is_better in enumerate(wants_more):
            if more_is_better:
                if a.skill_levels[i] < b.skill_levels[i]:
                    return False
            elif a.skill_levels[i] > b.skill_levels[i]:
                return False
        if any(x < y for x, y in zip(cumulative(a.slots), cumulative(b.slots))):
            return False
        return a.defense >= b.defense

    groups: dict[tuple[int, ...], list[PieceProfile]] = {}
    for profile in profiles:
        groups.setdefault(profile.bonus_indices, []).append(profile)

    kept: list[PieceProfile] = []
    for group in groups.values():
        group.sort(
            key=lambda p: (sum(p.skill_levels), sum(p.slots), p.defense), reverse=True
        )
        survivors: list[PieceProfile] = []
        for profile in group:
            if not any(dominates(other, profile) for other in survivors):
                survivors.append(profile)
        kept.extend(survivors)
    return kept


# --- decoration filling -----------------------------------------------------


@dataclass
class DecoOption:
    skill: str
    decoration: Decoration
    size: int
    values: tuple[float, ...]  # marginal value of each further level


def decoration_options(
    levels: dict[str, int], context: Context
) -> list[DecoOption]:
    scoring = context.scoring
    options: list[DecoOption] = []
    for name in context.relevant_skills:
        if scoring.weight(name) <= 0:
            continue
        deco = context.decoration_for_skill.get(name)
        if deco is None:
            continue
        current = levels.get(name, 0)
        headroom = scoring.max_level(name) - current
        if headroom <= 0:
            continue
        values = tuple(scoring.marginal(name, current + i) for i in range(headroom))
        if not any(v > 0 for v in values):
            continue
        options.append(
            DecoOption(
                skill=name, decoration=deco, size=deco.slot_level, values=values
            )
        )
    # Bigger gems first: they are the constrained resource.
    options.sort(key=lambda o: (-o.size, -o.values[0]))
    return options


def fill_slots(
    options: list[DecoOption], slots: tuple[int, int, int]
) -> tuple[float, tuple[int, ...]]:
    """Exact best decoration mix for the available slots.

    Returns (added score, count of each option taken). Ties prefer taking
    nothing, so slots are never filled with gems that add no value.
    """
    memo: dict[tuple[int, tuple[int, int, int]], tuple[float, tuple[int, ...]]] = {}

    def solve(idx: int, free: tuple[int, int, int]):
        if idx == len(options):
            return 0.0, ()
        key = (idx, free)
        cached = memo.get(key)
        if cached is not None:
            return cached

        skip_value, skip_counts = solve(idx + 1, free)
        best = (skip_value, (0,) + skip_counts)

        option = options[idx]
        remaining = free
        gained = 0.0
        for taken in range(1, len(option.values) + 1):
            remaining = consume_slot(remaining, option.size)
            if remaining is None:
                break
            gained += option.values[taken - 1]
            sub_value, sub_counts = solve(idx + 1, remaining)
            if gained + sub_value > best[0]:
                best = (gained + sub_value, (taken,) + sub_counts)

        memo[key] = best
        return best

    return solve(0, slots)


# --- weapon decorations -----------------------------------------------------

WEAPON_SOURCE = "weapon"  # SlotAssignment.source for the weapon's own slots
MAX_WEAPON_SLOTS = 3
# Search nodes solve_weapon_slots may visit before settling for the best
# layout found. A weapon's own three slots finish in well under a second
# even with every weapon skill weighted; it is a weapon plus a custom
# talisman's three more, with most skills weighted, that grows by several
# times per slot. Past this the answer is still good - jewels are tried
# best-first - but no longer proven, and WeaponFill.exact says so.
WEAPON_SEARCH_NODES = 100_000


def parse_weapon_slots(text: str) -> tuple[int, ...]:
    """'3,2,1' -> (3, 2, 1); '' -> (). Raises ValueError on anything else."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    try:
        sizes = tuple(int(p) for p in parts)
    except ValueError:
        raise ValueError(f"weapon slots must be sizes 1-3 separated by commas, not {text!r}")
    if len(sizes) > MAX_WEAPON_SLOTS or any(not 1 <= s <= 3 for s in sizes):
        raise ValueError(
            f"a weapon has at most {MAX_WEAPON_SLOTS} slots, each of size 1-3; "
            f"got {text!r}"
        )
    return sizes


@dataclass
class WeaponFill:
    """The best weapon-decoration layout for one list of weapon slot sizes."""

    sizes: tuple[int, ...]  # largest first
    decorations: tuple[Decoration | None, ...]  # one per entry in sizes
    levels: dict[str, int]  # weapon skills granted, capped at max
    tier: int  # constraint_level_met over the required weapon skills alone
    score: float
    exact: bool = True  # False if WEAPON_SEARCH_NODES ran out first


def solve_weapon_slots(
    sizes: tuple[int, ...], decorations: list[Decoration], scoring: Scoring
) -> WeaponFill:
    """Exact best weapon decorations for these slots.

    Weapon jewels are the one place the armour-side model does not fit: many
    carry two skills, or grant two or three levels at once, so fill_slots'
    one-level-per-gem counts cannot express them. Nothing on the armour side
    grants a weapon skill either, so the weapon is solved on its own, once
    per distinct set of slot sizes, and the result is simply added on.

    The search is a depth-first branch and bound over the slots, largest
    first, trying the jewels that gain most at the current levels first. A
    branch is cut when even an optimistic finish cannot beat the best layout
    found so far: for the score, each remaining slot is credited with the
    most any fitting jewel would gain right now, which never undercounts,
    because every skill's value grows by the same or less with each further
    level; for the required skills, with the most levels any fitting jewel
    could add. Jewels another jewel matches or beats on every weighted skill,
    at no larger size, are dropped first, and slots of equal size take jewels
    in a fixed order, so the same pair in two orders is tried once. The
    result is ranked on the required weapon skills first, then score, then
    fewest gems, so no slot is filled with a jewel that adds nothing.

    An earlier version memoised on the levels reached instead; with most
    weapon skills weighted and six slots, that table outgrew memory.
    """
    order = tuple(sorted(sizes, reverse=True))
    names = sorted(
        {
            s.name
            for d in decorations
            if d.type == "weapon"
            for s in d.skills
            if scoring.weight(s.name) != 0
        }
    )
    index = {name: i for i, name in enumerate(names)}
    caps = tuple(scoring.max_level(name) for name in names)

    def vector(deco: Decoration) -> tuple[int, ...]:
        levels = [0] * len(names)
        for granted in deco.skills:
            if granted.name in index:
                levels[index[granted.name]] += granted.level
        return tuple(levels)

    wanted = [scoring.weight(n) > 0 for n in names]
    candidates: list[tuple[Decoration, tuple[int, ...]]] = []
    for deco in decorations:
        if deco.type != "weapon":
            continue
        vec = vector(deco)
        if any(v and more for v, more in zip(vec, wanted)):
            candidates.append((deco, vec))

    def dominates(a, b) -> bool:
        (da, va), (db, vb) = a, b
        if da.slot_level > db.slot_level:
            return False
        return all(
            (x >= y) if more else (x <= y) for x, y, more in zip(va, vb, wanted)
        )

    # Sorted so a dominating jewel is always met before the ones it beats.
    candidates.sort(key=lambda c: (c[0].slot_level, [-v for v in c[1]], c[0].name))
    kept: list[tuple[Decoration, tuple[int, ...]]] = []
    for candidate in candidates:
        if not any(dominates(other, candidate) for other in kept):
            kept.append(candidate)

    mandatory = [
        (index[n], caps[index[n]] if n in scoring.mandatory_max else 1)
        for n in names
        if n in scoring.mandatory
    ]
    # Per jewel, only the skills it touches: (index, levels) pairs, all of
    # them for applying it, the wanted ones for the optimistic gain.
    touched = [[(k, v) for k, v in enumerate(vec) if v] for _d, vec in kept]
    touched_wanted = [[(k, v) for k, v in t if wanted[k]] for t in touched]
    by_size = {
        size: [i for i, (d, _v) in enumerate(kept) if d.slot_level <= size]
        for size in (1, 2, 3)
    }
    # Most levels of each required skill one jewel of each size can carry;
    # fixed, so worked out once rather than at every node.
    most_levels = {
        size: {i: max((kept[j][1][i] for j in by_size[size]), default=0) for i, _t in mandatory}
        for size in (1, 2, 3)
    }

    def tier_of(levels: list[int]) -> int:
        tier = 0
        for i, target in mandatory:
            if levels[i] < 1:
                return 2
            if levels[i] < target:
                tier = 1
        return tier

    def delta(pairs, levels: list[int]) -> float:
        """What adding these (skill, levels) pairs is worth at these levels."""
        total = 0.0
        for k, v in pairs:
            have = levels[k]
            if have < caps[k]:
                total += scoring.score(names[k], min(caps[k], have + v))
                total -= scoring.score(names[k], have)
        return total

    best_key = (3, 0.0, 0)
    best_choice: list[Decoration | None] = [None] * len(order)
    choice: list[Decoration | None] = [None] * len(order)
    levels = [0] * len(names)
    nodes = 0

    def search(slot: int, start: int, gems: int, current: float) -> None:
        nonlocal best_key, best_choice, nodes
        nodes += 1
        # Past the budget only the leaf is still taken: the empty-slot branch
        # at the end of every level runs straight down to one, so the best
        # layout found so far always gets compared before the search unwinds.
        out_of_budget = nodes > WEAPON_SEARCH_NODES
        if slot == len(order):
            key = (tier_of(levels), -current, gems)
            if key < best_key:
                best_key, best_choice = key, list(choice)
            return

        remaining = order[slot:]
        gains = [delta(pairs, levels) for pairs in touched_wanted]
        best_gain = {
            size: max((gains[j] for j in by_size[size]), default=0.0)
            for size in set(remaining)
        }
        bound = current + sum(max(0.0, best_gain[size]) for size in remaining)
        # The best tier still possible: each required skill can gain at most
        # the most levels any jewel fitting each remaining slot carries.
        tier_floor = 0
        for i, target in mandatory:
            reach = levels[i] + sum(most_levels[size][i] for size in remaining)
            if reach < 1:
                tier_floor = 2
                break
            if reach < target:
                tier_floor = 1
        if (tier_floor, -bound, gems) >= best_key:
            return

        same_size_next = slot + 1 < len(order) and order[slot + 1] == order[slot]
        ranked = sorted(
            (j for j in by_size[order[slot]] if j >= start), key=lambda j: -gains[j]
        )
        for j in ranked:
            if gains[j] <= 0 or out_of_budget:
                break  # every wanted skill on it is capped; useless from here on
            step = delta(touched[j], levels)
            before = list(levels)
            for k, v in touched[j]:
                levels[k] = min(caps[k], levels[k] + v)
            choice[slot] = kept[j][0]
            search(slot + 1, j if same_size_next else 0, gems + 1, current + step)
            levels[:] = before
            choice[slot] = None
        # Empty last: a slot left empty ends its equal-size run, so every
        # later slot of that size stays empty too (start past the end).
        search(slot + 1, len(kept) if same_size_next else 0, gems, current)

    search(0, 0, 0, 0.0)
    tier, neg_value, _gems = best_key
    chosen = tuple(best_choice)
    totals = [0] * len(names)
    for deco in chosen:
        if deco is not None:
            for i, v in enumerate(vector(deco)):
                totals[i] = min(caps[i], totals[i] + v)
    return WeaponFill(
        sizes=order,
        decorations=chosen,
        levels={n: lv for n, lv in zip(names, totals) if lv},
        tier=tier,
        score=-neg_value,
        exact=nodes <= WEAPON_SEARCH_NODES,
    )


# --- results ----------------------------------------------------------------


@dataclass
class SlotAssignment:
    source: str  # armour piece, talisman, or WEAPON_SOURCE
    size: int
    decoration: Decoration | None = None
    weapon: bool = False  # a weapon-jewel slot, on the weapon or the talisman


@dataclass
class ActiveBonus:
    name: str
    bonus_type: str
    pieces: int  # from armour only
    extra: int  # from a weapon-granted bonus point, if any (see extra_bonus_pieces)
    level: int
    effects: list[str]


@dataclass
class GearSet:
    pieces: list[ArmorPiece]
    talisman: Talisman
    skill_levels: dict[str, int]
    active_bonuses: list[ActiveBonus]
    placements: list[SlotAssignment]
    free_slots: list[int]
    reserved_slots: int
    weapon_slots: int
    defense_total: int
    skill_score: float
    defense_score: float
    total_score: float
    constraint_level: int
    tier: str = ""
    pinned_types: frozenset[str] = frozenset()
    # Weapon-jewel slots, the weapon's own and the talisman's, kept apart from
    # placements so free_slots and the reserve stay armour-only: resistance
    # jewels are armour jewels and cannot go in a weapon slot.
    weapon_placements: list[SlotAssignment] = field(default_factory=list)

    @property
    def weapon_free_slots(self) -> list[int]:
        return sorted(p.size for p in self.weapon_placements if p.decoration is None)

    @property
    def piece_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.pieces)

    @property
    def bonus_signature(self) -> frozenset[tuple[str, int]]:
        return frozenset((b.name, b.level) for b in self.active_bonuses)


def constraint_level_met(levels: dict[str, int], scoring: Scoring) -> int:
    """0 = mandatory maxed, 1 = mandatory present, 2 = nothing enforced."""
    for name in scoring.mandatory:
        if levels.get(name, 0) < 1:
            return 2
    for name in scoring.mandatory_max:
        if levels.get(name, 0) < scoring.max_level(name):
            return 1
    return 0


# --- search -----------------------------------------------------------------


@dataclass
class SearchState:
    profiles: tuple[PieceProfile, ...]
    levels: tuple[int, ...]
    slots: tuple[int, int, int]
    defense_value_sum: float
    bonus_counts: tuple[int, ...]
    rank: float


class Optimiser:
    def __init__(
        self,
        game: GameData,
        scoring: Scoring,
        beam_width: int = BEAM_WIDTH,
        final_pool: int = FINAL_POOL,
        reserved_slots: int = RESERVED_SLOTS,
        extra_bonus_pieces: dict[str, int] | None = None,
        pinned_pieces: dict[str, str] | None = None,
        excluded_sets: list[str] | set[str] | None = None,
        excluded_pieces: list[str] | set[str] | None = None,
        weapon_slots: tuple[int, ...] | list[int] | None = None,
    ) -> None:
        self.game = game
        self.scoring = scoring
        weapon_slots = tuple(weapon_slots or ())
        if len(weapon_slots) > MAX_WEAPON_SLOTS or any(
            s not in (1, 2, 3) for s in weapon_slots
        ):
            raise ValueError(
                f"A weapon has at most {MAX_WEAPON_SLOTS} slots, each of size 1-3; "
                f"got {list(weapon_slots)}."
            )
        self.weapon_slots = weapon_slots
        self._weapon_fills: dict[tuple[int, ...], WeaponFill] = {}
        self.context = Context(
            game,
            scoring,
            pinned_pieces=pinned_pieces,
            excluded_sets=excluded_sets,
            excluded_pieces=excluded_pieces,
            weapon_slots=weapon_slots,
        )
        self.game = self.context.game  # narrowed to a pinned talisman, if any
        self.beam_width = beam_width
        self.final_pool = final_pool
        self.reserved_slots = reserved_slots
        # Piece-count-equivalents credited to a named set/group bonus from a
        # source outside armour (e.g. a weapon's own bonus point), keyed by
        # the bonus's base name (see bonus_base_name). See the GUI's "Gogma
        # weapon skills" selectors.
        self.extra_bonus_pieces = {k: v for k, v in (extra_bonus_pieces or {}).items() if v}
        self._slot_potential = self._build_slot_potential()
        self._talisman_levels = self._best_talisman_levels()
        # Every complete set the last run() evaluated, kept so
        # unmet_requirements can say how close the search came.
        self._evaluated: list[GearSet] = []
        self._progress = None
        self._should_stop = None

    def weapon_fill(self, talisman_weapon_sizes=()) -> WeaponFill:
        """Best weapon jewels for the weapon's slots plus a talisman's.

        Cached per distinct size list: nothing on the armour side changes the
        answer, so every talisman without weapon slots - all the craftable
        ones - shares a single solve for the whole run.
        """
        key = tuple(sorted((*self.weapon_slots, *talisman_weapon_sizes), reverse=True))
        fill = self._weapon_fills.get(key)
        if fill is None:
            fill = solve_weapon_slots(key, self.game.decorations, self.scoring)
            self._weapon_fills[key] = fill
        return fill

    def _weapon_placements(
        self, talisman: Talisman, talisman_weapon_sizes: list[int], fill: WeaponFill
    ) -> list[SlotAssignment]:
        """Put the solved jewels into the physical weapon-side slots.

        fill.decorations lines up with the slot sizes sorted largest first,
        and each jewel fits the size it was solved for, so sorting the
        physical slots the same way (weapon before talisman on a tie) and
        pairing them off is always a valid placement.
        """
        slots = [
            SlotAssignment(source=WEAPON_SOURCE, size=s, weapon=True)
            for s in self.weapon_slots
        ] + [
            SlotAssignment(source=talisman.name, size=s, weapon=True)
            for s in talisman_weapon_sizes
        ]
        for slot, deco in zip(sorted(slots, key=lambda s: -s.size), fill.decorations):
            slot.decoration = deco
        return slots

    def _required_level(self, name: str) -> int:
        """Level a mandatory skill or bonus must reach: max, or just 1."""
        if name in self.scoring.mandatory_max:
            return self.scoring.max_level(name)
        return 1

    def impossible_requirements(self) -> list[str]:
        """Requirements no gear set can meet, found without searching.

        Each line is a proof, not a search result, so it may be worded as
        'cannot'. Three kinds are caught. A required skill nothing in the data
        grants at all (a weapon skill, or a bonus no armour carries). Required
        set bonuses, or required group skills, needing more pieces than the
        free slots hold: no piece carries more than one set bonus or more than
        one group skill, so within each kind the pieces add up rather than
        overlap. And, when neither of those fires, required bonuses that no
        choice of pieces satisfies together - see _bonus_combination_exists.
        """
        context = self.context
        reasons: list[str] = []
        reachable = context._gear_reachable_skills()
        from_excluded = {
            s.name
            for piece in self.game.armor
            if piece.name in context.excluded
            for s in piece.skills
        }

        free_types = [t for t in PIECE_TYPES if t not in context.pinned]
        # No set exists at all with an empty slot, requirements or not, so
        # this is reported even for a run that requires nothing.
        for piece_type in free_types:
            if not any(a.piece_type == piece_type for a in context.available_armor):
                reasons.append(f"Every {piece_type} piece is excluded.")

        needed_by_type: dict[str, list[int]] = {}
        needs: dict[str, int] = {}  # required bonus -> pieces still to find
        targets: dict[str, int] = {}
        weapon_skills = context.weapon_jewel_skills()
        weapon_required: list[str] = []
        for name in sorted(self.scoring.mandatory):
            info = context.bonus_registry.get(name)
            if info is None:
                if name in reachable:
                    continue
                if name in weapon_skills:
                    if context.weapon_slots_exist():
                        weapon_required.append(name)
                    else:
                        reasons.append(
                            f"{name} is a weapon skill, which only weapon "
                            "decorations grant, and no weapon slots are set."
                        )
                    continue
                if name in from_excluded:
                    reasons.append(
                        f"{name} is required, but only excluded armour provides it."
                    )
                else:
                    reasons.append(
                        f"{name} is required, but no armour piece, talisman or "
                        "decoration provides it."
                    )
                continue

            target = self._required_level(name)
            threshold = info.thresholds[min(target, len(info.thresholds)) - 1]
            pinned_count = sum(
                1
                for piece in context.pinned.values()
                if any(bonus_base_name(b.name) == name for b in piece.set_bonuses)
            )
            extra = self.extra_bonus_pieces.get(name, 0)
            need = max(0, threshold - pinned_count - extra)
            if need == 0:
                continue

            carriers = sum(
                1
                for piece_type in free_types
                if any(
                    piece.piece_type == piece_type
                    and any(bonus_base_name(b.name) == name for b in piece.set_bonuses)
                    for piece in context.available_armor
                )
            )
            if need > carriers:
                reasons.append(
                    f"{name} level {target} needs {need} more piece(s), but only "
                    f"{carriers} free slot(s) have a piece carrying it."
                )
            needed_by_type.setdefault(info.bonus_type, []).append(need)
            needs[name] = need
            targets[name] = target

        # One bonus on its own is already covered by the carriers check above;
        # the sum only says something new when two or more compete for slots.
        for bonus_type, type_needs in sorted(needed_by_type.items()):
            needed = sum(type_needs)
            if len(type_needs) > 1 and needed > len(free_types):
                kind = "group skills" if bonus_type == "group_skill" else "set bonuses"
                reasons.append(
                    f"The required {kind} need {needed} pieces between them, but "
                    f"only {len(free_types)} slot(s) are free and no piece carries "
                    f"more than one of them."
                )

        # The checks above only ever count one kind at a time. A set bonus and
        # a group skill can share a piece, so their sum proves nothing - but
        # whether enough pieces actually carry both is a question the data can
        # answer exactly. Only asked when nothing above has fired, so a
        # simpler reason is never buried under this one.
        if not reasons and len(needs) > 1 and not self._bonus_combination_exists(
            needs, free_types
        ):
            wanted = ", ".join(
                f"{name} level {targets[name]} ({need} more piece(s))"
                for name, need in needs.items()
            )
            reasons.append(
                f"No choice of armour for the {len(free_types)} free slot(s) "
                f"gives all of these at once: {wanted}. Too few pieces carry more "
                "than one of them for the counts to fit."
            )

        # The weapon side is solved exactly and depends on nothing else, so
        # if its best layout misses a required weapon skill, every set does.
        # Each distinct talisman weapon-slot list is tried, since a custom
        # talisman's slots add to the weapon's own.
        if weapon_required:
            layouts = {tuple(talisman_slot_sizes(t)[1]) for t in self.game.talismans}
            fills = [self.weapon_fill(extra) for extra in layouts | {()}]
            # A fill that ran out of budget proves nothing; the search then
            # runs and unmet_requirements reports the miss as a miss.
            if all(f.exact for f in fills) and not any(
                all(f.levels.get(n, 0) >= self._required_level(n) for n in weapon_required)
                for f in fills
            ):
                best = min(fills, key=lambda f: (f.tier, -f.score))
                reached = ", ".join(
                    f"{n} {best.levels.get(n, 0)}/{self._required_level(n)}"
                    for n in weapon_required
                )
                reasons.append(
                    f"No layout of weapon decorations in weapon slots "
                    f"{list(best.sizes)} reaches every required weapon skill; the "
                    f"best reaches {reached}."
                )
        return reasons

    def _bonus_combination_exists(
        self, needs: dict[str, int], free_types: list[str]
    ) -> bool:
        """Can some piece per free slot supply every required bonus together?

        Exact, by exhaustive search over what each slot can contribute, and
        cheap because only the required bonuses count. A slot is reduced to
        the sets of them its pieces carry, keeping only the maximal sets: a
        threshold is a minimum, so a piece carrying more of the required
        bonuses is never worse than one carrying fewer. Skills are ignored
        entirely, so True does not mean a valid set exists - only that the
        bonuses do not rule one out.
        """
        names = tuple(needs)
        options: list[list[frozenset[str]]] = []
        for piece_type in free_types:
            signatures = {
                frozenset(
                    name
                    for name in names
                    if any(bonus_base_name(b.name) == name for b in piece.set_bonuses)
                )
                for piece in self.context.available_armor
                if piece.piece_type == piece_type
            }
            options.append([s for s in signatures if not any(s < o for o in signatures)])

        def search(index: int, remaining: dict[str, int]) -> bool:
            if all(count <= 0 for count in remaining.values()):
                return True
            # Each slot adds at most one piece toward any single bonus.
            if max(remaining.values()) > len(options) - index:
                return False
            return any(
                search(
                    index + 1,
                    {name: count - (name in signature) for name, count in remaining.items()},
                )
                for signature in options[index]
            )

        return search(0, dict(needs))

    def unmet_requirements(self) -> list[str]:
        """How far short of each requirement the last run() fell.

        Worded as what the search did not find rather than what cannot exist:
        the beam is a heuristic, so a miss here is not a proof.
        """
        if not self._evaluated:
            return ["The search did not build any complete gear set."]

        reasons: list[str] = []
        for name in sorted(self.scoring.mandatory):
            target = self._required_level(name)
            best = max(s.skill_levels.get(name, 0) for s in self._evaluated)
            if best < target:
                reasons.append(
                    f"{name}: the best set found reached level {best} of the "
                    f"{target} required."
                )
        if not reasons:
            reasons.append(
                "Each requirement was met by some set found, but no set met all "
                "of them at once."
            )
        return reasons

    def _build_slot_potential(self) -> tuple[float, float, float]:
        """Optimistic value of one free slot of each size, for beam ranking."""
        best = [0.0, 0.0, 0.0]
        for name in self.context.relevant_skills:
            weight = self.scoring.weight(name)
            if weight <= 0:
                continue
            deco = self.context.decoration_for_skill.get(name)
            if deco is None:
                continue
            # Later levels can be worth more than the first, so the most any
            # one level is worth, not the first level's value, is the bound.
            value = max(
                self.scoring.marginal(name, level)
                for level in range(self.scoring.max_level(name))
            )
            for size in range(deco.slot_level, 4):
                best[size - 1] = max(best[size - 1], value)
        return best[0], best[1], best[2]

    def _best_talisman_levels(self) -> dict[str, int]:
        best: dict[str, int] = {}
        for talisman in self.game.talismans:
            for granted in talisman.skills:
                if granted.level > best.get(granted.name, 0):
                    best[granted.name] = granted.level
        return best

    def _mandatory_reachable(self, state: SearchState) -> bool:
        """Could this complete armour set still satisfy the mandatory skills?

        Checks that the gem slots (optionally helped by the best talisman for
        one skill) can cover the remaining levels. Used to prioritise the final
        pool, so a conservative answer only costs ranking, not correctness.
        """
        context = self.context
        scoring = self.scoring

        deficits: dict[str, int] = {}
        for name in scoring.mandatory:
            target = (
                scoring.max_level(name) if name in scoring.mandatory_max else 1
            )
            idx = context.skill_index.get(name)
            if idx is None:
                bonus_idx = context.bonus_index.get(name)
                if bonus_idx is None:
                    continue
                info = context.bonus_registry[name]
                have = state.bonus_counts[bonus_idx] + self.extra_bonus_pieces.get(name, 0)
                if info.level_for(have) < target:
                    return False
                continue
            shortfall = target - state.levels[idx]
            if shortfall > 0:
                deficits[name] = shortfall

        if not deficits:
            return True

        talisman_levels = self._talisman_levels
        # A talisman can cover one skill; try each in turn.
        for helped in [None, *deficits]:
            needs: list[int] = []
            feasible = True
            for name, shortfall in deficits.items():
                if name == helped:
                    shortfall -= talisman_levels.get(name, 0)
                if shortfall <= 0:
                    continue
                deco = context.decoration_for_skill.get(name)
                if deco is None:
                    feasible = False
                    break
                needs.extend([deco.slot_level] * shortfall)
            if not feasible:
                continue
            needs.sort(reverse=True)
            free = state.slots
            for size in needs:
                free = consume_slot(free, size)
                if free is None:
                    break
            else:
                return True
        return False

    def _rank_state(self, state: SearchState, pieces_chosen: int) -> float:
        scoring = self.scoring
        total = 0.0
        for idx, name in enumerate(self.context.relevant_skills):
            total += scoring.score(name, state.levels[idx])

        for idx, name in enumerate(self.context.relevant_bonuses):
            count = state.bonus_counts[idx] + self.extra_bonus_pieces.get(name, 0)
            if count == 0:
                continue
            info = self.context.bonus_registry[name]
            level = info.level_for(count)
            total += scoring.score(name, level)
            # partial credit for being part-way to the next threshold
            next_thresholds = [t for t in info.thresholds if t > count]
            if next_thresholds and pieces_chosen < len(PIECE_TYPES):
                need = min(next_thresholds)
                step = scoring.marginal(name, level)
                total += step * (count / need) * BONUS_PROGRESS_CREDIT

        total += scoring.defense_points * state.defense_value_sum / len(PIECE_TYPES)

        n1, n2, n3 = state.slots
        p1, p2, p3 = self._slot_potential
        total += n1 * p1 + n2 * p2 + n3 * p3
        return total

    def _report(self, fraction: float, message: str) -> None:
        """Pass progress on, and stop the run if asked to.

        Both happen at the same points, so a caller that only wants to cancel
        still passes through here as often as one drawing a progress bar.
        """
        if self._should_stop is not None and self._should_stop():
            raise SearchCancelled
        if self._progress is not None:
            self._progress(min(1.0, max(0.0, fraction)), message)

    def _search_armour(self) -> list[SearchState]:
        order = sorted(PIECE_TYPES, key=lambda t: len(self.context.candidates[t]))
        empty_levels = (0,) * len(self.context.relevant_skills)
        empty_bonuses = (0,) * len(self.context.relevant_bonuses)
        beam = [
            SearchState(
                profiles=(),
                levels=empty_levels,
                slots=(0, 0, 0),
                defense_value_sum=0.0,
                bonus_counts=empty_bonuses,
                rank=0.0,
            )
        ]

        for stage, piece_type in enumerate(order, start=1):
            candidates = self.context.candidates[piece_type]
            expanded: list[SearchState] = []
            for position, state in enumerate(beam):
                if position % CHECK_EVERY == 0:
                    done = (stage - 1 + position / len(beam)) / len(order)
                    self._report(
                        SEARCH_SHARE * done,
                        f"Searching armour: slot {stage} of {len(order)} ({piece_type})",
                    )
                for profile in candidates:
                    levels = tuple(
                        a + b for a, b in zip(state.levels, profile.skill_levels)
                    )
                    slots = (
                        state.slots[0] + profile.slots[0],
                        state.slots[1] + profile.slots[1],
                        state.slots[2] + profile.slots[2],
                    )
                    counts = list(state.bonus_counts)
                    for idx in profile.bonus_indices:
                        counts[idx] += 1
                    new_state = SearchState(
                        profiles=state.profiles + (profile,),
                        levels=levels,
                        slots=slots,
                        defense_value_sum=state.defense_value_sum
                        + profile.defense_value,
                        bonus_counts=tuple(counts),
                        rank=0.0,
                    )
                    new_state.rank = self._rank_state(new_state, stage)
                    expanded.append(new_state)

            expanded.sort(key=lambda s: s.rank, reverse=True)
            if stage < len(order):
                beam = self._truncate(expanded, self.beam_width)
            else:
                # Sets that can still satisfy the mandatory skills come first,
                # otherwise the pool fills with high-scoring but invalid sets.
                expanded.sort(
                    key=lambda s: (not self._mandatory_reachable(s), -s.rank)
                )
                beam = self._truncate(expanded, self.final_pool)

        return beam

    def _truncate(self, states: list[SearchState], width: int) -> list[SearchState]:
        """Keep the best states, but cap how many share a bonus signature.

        Without this the beam converges on a single build neighbourhood and the
        'distinct bonuses' results have nothing to draw from.
        """
        if not self.context.relevant_bonuses or len(states) <= width:
            return states[:width]

        quota = max(1, width // max(2, 2 ** len(self.context.relevant_bonuses)))
        kept: list[SearchState] = []
        overflow: list[SearchState] = []
        seen: dict[tuple[int, ...], int] = {}
        for state in states:
            signature = state.bonus_counts
            count = seen.get(signature, 0)
            if count < quota and len(kept) < width:
                kept.append(state)
                seen[signature] = count + 1
            else:
                overflow.append(state)

        if len(kept) < width:
            kept.extend(overflow[: width - len(kept)])
        return kept

    def _evaluate(self, state: SearchState) -> GearSet | None:
        # The search visits slots in candidate-count order; present them head->legs.
        pieces = sorted(
            (p.piece for p in state.profiles),
            key=lambda piece: PIECE_TYPES.index(piece.piece_type),
        )

        base_levels: dict[str, int] = {}
        for piece in pieces:
            for granted in piece.skills:
                base_levels[granted.name] = base_levels.get(granted.name, 0) + granted.level

        best: GearSet | None = None
        for talisman in self._shortlist_talismans(base_levels):
            candidate = self._build_set(pieces, talisman, base_levels, state)
            if candidate is None:
                continue
            if best is None or (
                candidate.constraint_level,
                -candidate.total_score,
            ) < (best.constraint_level, -best.total_score):
                best = candidate
        return best

    def _shortlist_talismans(self, base_levels: dict[str, int]) -> list[Talisman]:
        scoring = self.scoring
        scored: list[tuple[float, int, Talisman]] = []
        for index, talisman in enumerate(self.game.talismans):
            gain = 0.0
            for granted in talisman.skills:
                current = base_levels.get(granted.name, 0)
                gain += scoring.score(granted.name, current + granted.level) - scoring.score(
                    granted.name, current
                )
            sizes, weapon_sizes = talisman_slot_sizes(talisman)
            for size in sizes:
                gain += self._slot_potential[min(size, 3) - 1]
            # Exact, not an estimate: the weapon side is solved outright and
            # cached, so a talisman's weapon slots are worth precisely what
            # they add to the weapon's own layout.
            if weapon_sizes:
                gain += self.weapon_fill(weapon_sizes).score - self.weapon_fill().score
            scored.append((gain, index, talisman))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [t for _, _, t in scored[:TALISMAN_SHORTLIST]]

    def _build_set(
        self,
        pieces: list[ArmorPiece],
        talisman: Talisman,
        base_levels: dict[str, int],
        state: SearchState,
    ) -> GearSet | None:
        context = self.context
        scoring = self.scoring

        levels = dict(base_levels)
        for granted in talisman.skills:
            levels[granted.name] = levels.get(granted.name, 0) + granted.level

        talisman_sizes, talisman_weapon_sizes = talisman_slot_sizes(talisman)

        # Weapon skills come only from weapon jewels and those only go in
        # weapon slots, so the weapon's result is simply added before the
        # reservation loop - which then judges required weapon skills along
        # with everything else.
        weapon = self.weapon_fill(talisman_weapon_sizes)
        for name, level in weapon.levels.items():
            levels[name] = levels.get(name, 0) + level

        # Every physical decoration slot's size. Reservation is decided on
        # this concrete list (smallest sizes first) rather than an abstract
        # slot-count, so the slots actually left empty are guaranteed to be
        # the least valuable ones - see _assign_decorations.
        all_sizes = sorted(
            size for piece in pieces for size in piece.slots if size
        )
        all_sizes.extend(talisman_sizes)
        all_sizes.sort()

        options = decoration_options(levels, context)

        # Bonus levels depend only on the pieces, so they are known before any
        # gem is placed - and must be, because the reservation loop below
        # judges each trial by constraint_level_met. Left out, a required set
        # bonus reads as missing on every trial, no trial ever improves on the
        # first, and the reserved slots are never released for the skills
        # that need them.
        active_bonuses = self._active_bonuses(pieces)
        for bonus in active_bonuses:
            levels[bonus.name] = bonus.level

        chosen_counts: tuple[int, ...] = ()
        reserved = 0
        final_levels: dict[str, int] = {}
        best_level = 3
        max_reserve = min(self.reserved_slots, len(all_sizes))
        for reserve in range(max_reserve, -1, -1):
            available_counts = slot_counts(all_sizes[reserve:])
            _value, counts = fill_slots(options, available_counts)
            trial_levels = dict(levels)
            for option, count in zip(options, counts):
                if count:
                    trial_levels[option.skill] = trial_levels.get(option.skill, 0) + count
            level_met = constraint_level_met(trial_levels, scoring)
            if level_met < best_level:
                best_level = level_met
                chosen_counts, reserved, final_levels = counts, reserve, trial_levels
            if level_met == 0:
                break

        if not final_levels:
            return None

        placements, free_slots = self._assign_decorations(
            pieces, talisman, talisman_sizes, options, chosen_counts, reserved
        )

        # Pieces alone can push a skill past its cap; report the effective level.
        for name, level in final_levels.items():
            skill = scoring.by_name.get(name)
            if skill is not None and level > skill.max_level:
                final_levels[name] = skill.max_level

        skill_score = sum(
            scoring.score(name, level) for name, level in final_levels.items()
        )
        defense_total = sum(p.defense.max for p in pieces)
        defense_component = (
            scoring.defense_points * state.defense_value_sum / len(PIECE_TYPES)
        )

        return GearSet(
            pieces=pieces,
            talisman=talisman,
            skill_levels=final_levels,
            active_bonuses=active_bonuses,
            placements=placements,
            free_slots=free_slots,
            reserved_slots=reserved,
            weapon_slots=len(talisman_weapon_sizes),
            defense_total=defense_total,
            skill_score=skill_score,
            defense_score=defense_component,
            total_score=skill_score + defense_component,
            constraint_level=constraint_level_met(final_levels, scoring),
            pinned_types=frozenset(context.pinned)
            | ({TALISMAN_SLOT} if context.pinned_talisman else set()),
            weapon_placements=self._weapon_placements(
                talisman, talisman_weapon_sizes, weapon
            ),
        )

    def _assign_decorations(
        self,
        pieces: list[ArmorPiece],
        talisman: Talisman,
        talisman_sizes: list[int],
        options: list[DecoOption],
        counts: tuple[int, ...],
        reserve_count: int,
    ) -> tuple[list[SlotAssignment], list[int]]:
        # Built in each piece's own slot order (not size order), so the GUI's
        # per-piece display matches the source data's slot ordering.
        slots: list[SlotAssignment] = []
        for piece in pieces:
            for size in piece.slots:
                if size:
                    slots.append(SlotAssignment(source=piece.name, size=size))
        for size in talisman_sizes:
            slots.append(SlotAssignment(source=talisman.name, size=size))

        # The `reserve_count` smallest slots are held back entirely - never
        # offered to the fitter below - matching the sizes fill_slots was
        # given in _build_set, so a size-1 slot is always sacrificed before a
        # size-2/3 one.
        reserved_ids = {
            id(s) for s in sorted(slots, key=lambda s: s.size)[:reserve_count]
        }
        eligible = [s for s in slots if id(s) not in reserved_ids]

        wanted: list[Decoration] = []
        for option, count in zip(options, counts):
            wanted.extend([option.decoration] * count)
        wanted.sort(key=lambda d: -d.slot_level)

        for deco in wanted:
            fit = min(
                (s for s in eligible if s.decoration is None and s.size >= deco.slot_level),
                key=lambda s: s.size,
                default=None,
            )
            if fit is None:
                continue
            fit.decoration = deco

        free = sorted(s.size for s in slots if s.decoration is None)
        return slots, free

    def _active_bonuses(self, pieces: list[ArmorPiece]) -> list[ActiveBonus]:
        counts: dict[str, int] = {}
        for piece in pieces:
            for bonus in piece.set_bonuses:
                base = bonus_base_name(bonus.name)
                counts[base] = counts.get(base, 0) + 1

        # A weapon-granted bonus point can activate a bonus no chosen armour
        # piece carries at all, so consider every name with an extra point too.
        names = set(counts) | {n for n, e in self.extra_bonus_pieces.items() if e}

        active: list[ActiveBonus] = []
        for name in names:
            info = self.context.bonus_registry.get(name)
            if info is None:
                continue  # extra point on a bonus with no known armour source
            armour_pieces = counts.get(name, 0)
            extra = self.extra_bonus_pieces.get(name, 0)
            total = armour_pieces + extra
            level = info.level_for(total)
            if level <= 0:
                continue
            active.append(
                ActiveBonus(
                    name=name,
                    bonus_type=info.bonus_type,
                    pieces=armour_pieces,
                    extra=extra,
                    level=level,
                    effects=[
                        effect
                        for effect, threshold in zip(info.effects, info.thresholds)
                        if total >= threshold
                    ],
                )
            )
        active.sort(key=lambda b: (b.bonus_type, -b.level, b.name))
        return active

    def run(
        self,
        tiers=DEFAULT_TIERS,
        strict: bool = True,
        progress=None,
        should_stop=None,
    ) -> tuple[list[GearSet], int]:
        """Search and pick the result bands.

        strict makes mandatory a hard filter: only sets meeting every
        requirement (tier 0) are returned, even if that is fewer than asked for
        or none. With strict off, the tier loosens until the bands can be
        filled, which is how the --relax option behaves.
        """
        # progress(fraction, message) is called now and then from this
        # thread; should_stop() is polled at the same points and raises
        # SearchCancelled when it returns True. Both are optional.
        self._progress, self._should_stop = progress, should_stop
        self._evaluated = []
        if strict and self.impossible_requirements():
            return [], 0

        states = self._search_armour()

        evaluated: list[GearSet] = []
        for position, state in enumerate(states):
            if position % CHECK_EVERY == 0:
                self._report(
                    SEARCH_SHARE + (1 - SEARCH_SHARE) * position / len(states),
                    f"Evaluating sets: {position} of {len(states)}",
                )
            gear_set = self._evaluate(state)
            if gear_set is not None:
                evaluated.append(gear_set)

        evaluated.sort(key=lambda s: (s.constraint_level, -s.total_score))
        self._evaluated = evaluated
        if strict:
            pool = [s for s in evaluated if s.constraint_level == 0]
            return select_diverse(pool, tiers), 0
        if not evaluated:
            return [], 2

        wanted = sum(tier.count for tier in tiers)
        constraint_level = 2
        for level in (0, 1, 2):
            if sum(1 for s in evaluated if s.constraint_level <= level) >= wanted:
                constraint_level = level
                break

        pool = [s for s in evaluated if s.constraint_level <= constraint_level]
        return select_diverse(pool, tiers), constraint_level


def select_diverse(pool: list[GearSet], tiers) -> list[GearSet]:
    """Pick sets band by band so the results are not near-identical.

    Each band relaxes its own rules rather than returning short, so the caller
    always gets the requested number of sets when the pool can supply them.
    """
    chosen: list[GearSet] = []
    used_signatures: set[frozenset[tuple[str, int]]] = set()
    taken: set[tuple[str, ...]] = set()

    def piece_diff(gear_set: GearSet) -> int:
        if not chosen:
            return len(PIECE_TYPES)
        return min(
            sum(1 for a, b in zip(gear_set.piece_names, other.piece_names) if a != b)
            for other in chosen
        )

    for tier in tiers:
        added = 0
        rules = [(tier.require_new_bonuses, tier.min_piece_diff)]
        if tier.require_new_bonuses:
            rules.append((False, max(tier.min_piece_diff, 2)))
        if tier.min_piece_diff > 1:
            rules.append((False, 1))

        for rule_index, (require_new_bonuses, min_piece_diff) in enumerate(rules):
            if added >= tier.count:
                break
            label = tier.label if rule_index == 0 else f"{tier.label} (relaxed)"
            for gear_set in pool:
                if added >= tier.count:
                    break
                names = gear_set.piece_names
                if names in taken:
                    continue
                if require_new_bonuses and gear_set.bonus_signature in used_signatures:
                    continue
                if piece_diff(gear_set) < min_piece_diff:
                    continue
                gear_set.tier = label
                chosen.append(gear_set)
                taken.add(names)
                used_signatures.add(gear_set.bonus_signature)
                added += 1

    return chosen


def optimise(
    game: GameData,
    scoring: Scoring,
    beam_width: int = BEAM_WIDTH,
    final_pool: int = FINAL_POOL,
    reserved_slots: int = RESERVED_SLOTS,
    tiers=DEFAULT_TIERS,
    extra_bonus_pieces: dict[str, int] | None = None,
    pinned_pieces: dict[str, str] | None = None,
    strict: bool = True,
    excluded_sets: list[str] | set[str] | None = None,
    excluded_pieces: list[str] | set[str] | None = None,
    weapon_slots: tuple[int, ...] | list[int] | None = None,
    progress=None,
    should_stop=None,
) -> tuple[list[GearSet], int, Optimiser]:
    optimiser = Optimiser(
        game,
        scoring,
        beam_width=beam_width,
        final_pool=final_pool,
        reserved_slots=reserved_slots,
        extra_bonus_pieces=extra_bonus_pieces,
        pinned_pieces=pinned_pieces,
        excluded_sets=excluded_sets,
        excluded_pieces=excluded_pieces,
        weapon_slots=weapon_slots,
    )
    sets, constraint_level = optimiser.run(
        tiers, strict=strict, progress=progress, should_stop=should_stop
    )
    return sets, constraint_level, optimiser


def build_tiers(count: int) -> tuple[DiversityTier, ...]:
    """Scale the default 3/3/4 banding to the requested number of sets.

    Below ten the sets are dealt round-robin across the bands; above it the
    3/3/4 split is kept and the extra sets are dealt round-robin on top, so
    --count 20 returns twenty rather than silently stopping at ten.
    """
    if count < 10:
        shares = [0, 0, 0]
        extra = count
    else:
        shares = [tier.count for tier in DEFAULT_TIERS]
        extra = count - sum(shares)
    for i in range(extra):
        shares[i % 3] += 1
    return tuple(
        DiversityTier(t.label, share, t.min_piece_diff, t.require_new_bonuses)
        for t, share in zip(DEFAULT_TIERS, shares)
        if share
    )


def gear_set_filename(db_path: Path) -> str:
    """Results filename that can't be mistaken for a skills DB."""
    stem = db_path.stem
    for prefix in ("skills_DB_", "skills_db_", "skills_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    return f"{stem}_gear_sets.yaml"


WIDTH_PROGRESS = 78


def _terminal_progress():
    """A progress callback that redraws one stderr line, for interactive use.

    Only installed when stderr is a terminal: piped or redirected, the
    carriage returns would land in the file as clutter.
    """
    last = [-1]

    def show(fraction: float, message: str) -> None:
        percent = int(fraction * 100)
        if percent == last[0]:
            return
        last[0] = percent
        line = f"{percent:3d}%  {message}"[:WIDTH_PROGRESS]
        sys.stderr.write("\r" + line.ljust(WIDTH_PROGRESS))
        sys.stderr.flush()

    return show


def main() -> None:
    # Piped or redirected, Python on Windows writes stdout and stderr in the
    # ANSI code page, which has no α/β/γ, so the first armour name printed
    # would raise UnicodeEncodeError: `optimiser.py ... > sets.txt` died
    # mid-run, and a usage error naming a piece crashed instead of printing.
    # Done first, before argparse can report an error of its own.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    weights_source = parser.add_mutually_exclusive_group()
    weights_source.add_argument(
        "--skills-db",
        help="weighted skills YAML to optimise against",
    )
    weights_source.add_argument(
        "--profile",
        metavar="FILE",
        help="search profile holding the weights and every setting; any "
        "option also given on the command line overrides it",
    )
    parser.add_argument("--count", type=int, default=10, help="number of sets to return")
    parser.add_argument("--beam", type=int, default=BEAM_WIDTH, help="beam width")
    # None rather than the real default, for this and the options below, so
    # a profile's value is only overridden when the option is actually given.
    parser.add_argument(
        "--reserve",
        type=int,
        default=None,
        help=f"slots to hold back for resistance jewels (default {RESERVED_SLOTS})",
    )
    parser.add_argument("--output", help="YAML file to write the sets to")
    for piece_type in PIECE_TYPES:
        parser.add_argument(
            f"--pin-{piece_type}",
            metavar="PIECE",
            help=f"force the {piece_type} slot to this armour piece, by name",
        )
    parser.add_argument(
        "--pin-talisman",
        metavar="TALISMAN",
        help="force this talisman, by name (craftable, or from --talismans)",
    )
    parser.add_argument(
        "--exclude-set",
        action="append",
        default=[],
        metavar="SET",
        help="leave every piece of this armour set out of the search (repeatable)",
    )
    parser.add_argument(
        "--exclude-piece",
        action="append",
        default=[],
        metavar="PIECE",
        help="leave this armour piece out of the search (repeatable)",
    )
    parser.add_argument(
        "--weapon-slots",
        default=None,
        metavar="SIZES",
        help="your weapon's decoration slot sizes, e.g. 3,2,1; weapon jewels "
        "are then placed for weighted weapon skills",
    )
    parser.add_argument(
        "--gogma-set", metavar="BONUS", help="credit one piece toward this set bonus"
    )
    parser.add_argument(
        "--gogma-group", metavar="SKILL", help="credit one piece toward this group skill"
    )
    parser.add_argument(
        "--talismans",
        metavar="FILE",
        help="custom talismans file to add to the talisman pool",
    )
    parser.add_argument(
        "--relax",
        action="store_true",
        help="allow sets that miss a mandatory skill rather than returning fewer",
    )
    parser.add_argument(
        "--save-profile",
        metavar="FILE",
        help="also write the weights and settings this run used as a profile",
    )
    args = parser.parse_args()
    # Each of these otherwise fails quietly: a count or beam of 0, or a
    # negative reserve (every set then fails to build), returns no sets and no
    # reason.
    if args.count < 1:
        parser.error("--count must be at least 1")
    if args.beam < 1:
        parser.error("--beam must be at least 1")
    if args.reserve is not None and args.reserve < 0:
        parser.error("--reserve cannot be negative")

    from dataclasses import replace as updated

    from load_data import load_talismans
    from optimiser_report import render_console, write_yaml
    from search_profile import (
        SearchProfile,
        apply_weights,
        load_profile,
        profile_problems,
        save_profile,
        stored_path,
        weights_of,
    )

    game = load_game_data()

    # Everything the run uses is gathered into one profile - the loaded one,
    # or an empty one - with each option given on the command line laid over
    # it. One validation pass then covers both sources alike.
    if args.profile:
        db_path = Path(args.profile)
        try:
            profile = load_profile(db_path)
        except (OSError, ValueError) as exc:
            parser.error(f"--profile: {exc}")
        skills = apply_weights(game.skills, profile.weights)
    else:
        db_path = Path(args.skills_db or "skills_outputs/skills_DB_burst.yaml")
        if not db_path.exists():
            parser.error(
                f"{db_path} does not exist; pass --skills-db FILE or --profile FILE"
            )
        try:
            skills = load_skills(db_path)
        except ValueError as exc:
            parser.error(f"--skills-db: {exc}")
        profile = SearchProfile(weights=weights_of(skills))

    pins = dict(profile.pins)
    for piece_type in (*PIECE_TYPES, TALISMAN_SLOT):
        if getattr(args, f"pin_{piece_type}"):
            pins[piece_type] = getattr(args, f"pin_{piece_type}")
    weapon_slots = list(profile.weapon_slots)
    if args.weapon_slots is not None:
        try:
            weapon_slots = list(parse_weapon_slots(args.weapon_slots))
        except ValueError as exc:
            parser.error(f"--weapon-slots: {exc}")
    profile = updated(
        profile,
        pins=pins,
        # Exclusions add to the profile's rather than replace them: a
        # command-line exclusion reads as "and also leave this out".
        exclude_sets=sorted(set(profile.exclude_sets) | set(args.exclude_set)),
        exclude_pieces=sorted(set(profile.exclude_pieces) | set(args.exclude_piece)),
        weapon_slots=weapon_slots,
        gogma_set_bonus=args.gogma_set or profile.gogma_set_bonus,
        gogma_group_skill=args.gogma_group or profile.gogma_group_skill,
        reserve=profile.reserve if args.reserve is None else args.reserve,
        relax=profile.relax or args.relax,
        custom_talismans=(
            stored_path(Path(args.talismans)) if args.talismans else profile.custom_talismans
        ),
    )
    # A skills file's weights need no name check: they are that file's own
    # skill rows. A profile's are names typed against some version of the
    # data, and may not match this one.
    problems = profile_problems(
        profile if args.profile else updated(profile, weights={}), game
    )
    if problems:
        parser.error("; ".join(problems))

    talismans_path = profile.custom_talismans_path()
    if talismans_path is not None:
        try:
            extra_talismans = load_talismans(talismans_path)
        except Exception as exc:  # noqa: BLE001 - any malformed file is a usage error
            parser.error(f"custom talismans {talismans_path}: {exc}")
        game = updated(game, talismans=list(game.talismans) + extra_talismans)

    scoring = Scoring(skills)
    strict = not profile.relax
    # Built here only to validate pins and exclusions together before a
    # search starts - pinned-and-excluded is a conflict between two valid
    # names that profile_problems cannot see - so it is a usage error rather
    # than a traceback.
    try:
        context = Context(
            game,
            scoring,
            pinned_pieces=profile.pins,
            excluded_sets=profile.exclude_sets,
            excluded_pieces=profile.exclude_pieces,
            weapon_slots=tuple(profile.weapon_slots),
        )
    except ValueError as exc:
        parser.error(str(exc))
    pinned = context.pinned

    if args.save_profile:
        save_profile(profile, Path(args.save_profile))
        print(f"Saved profile to {args.save_profile}")

    unreachable = context.unreachable_weighted_skills()
    # A required one is not ignored under the hard filter: it empties the
    # result, and the no-sets message below names it as the reason.
    if strict:
        unreachable = [n for n in unreachable if n not in scoring.mandatory]
    if unreachable:
        hint = (
            ""
            if context.weapon_slots_exist()
            else " (weapon skills need --weapon-slots)"
        )
        print(
            "Warning: these weighted skills cannot come from armour, talismans or "
            f"decorations and were ignored{hint}: " + ", ".join(unreachable)
        )

    sets, constraint_level, optimiser = optimise(
        game,
        scoring,
        beam_width=args.beam,
        reserved_slots=profile.reserve,
        tiers=build_tiers(args.count),
        extra_bonus_pieces=profile.extra_bonus_pieces(),
        pinned_pieces=profile.pins,
        strict=strict,
        excluded_sets=profile.exclude_sets,
        excluded_pieces=profile.exclude_pieces,
        weapon_slots=tuple(profile.weapon_slots),
        progress=_terminal_progress() if sys.stderr.isatty() else None,
    )
    if sys.stderr.isatty():
        sys.stderr.write("\r" + " " * WIDTH_PROGRESS + "\r")

    # impossible_requirements is a proof, so it wins over unmet_requirements,
    # which only reports what the search did not find.
    reasons: list[str] = []
    if not sets:
        reasons = optimiser.impossible_requirements()
        if not reasons:
            reasons = optimiser.unmet_requirements() + [
                "The search is a heuristic, so this is what it did not find, "
                "not proof that nothing exists."
            ]

    print(
        render_console(
            sets,
            scoring,
            constraint_level,
            db_path,
            pinned=pinned,
            pinned_talisman=context.pinned_talisman,
            strict=strict,
            reasons=reasons,
            excluded=profile.exclude_sets + profile.exclude_pieces,
        )
    )

    output = Path(args.output) if args.output else Path("optimiser_outputs") / (
        gear_set_filename(db_path)
    )
    write_yaml(sets, scoring, constraint_level, db_path, output)
    print(f"\nWrote {len(sets)} sets to {output}")


if __name__ == "__main__":
    main()
