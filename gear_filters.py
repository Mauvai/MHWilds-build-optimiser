"""Gear filters: whole categories of armour and talismans kept out of a run.

Exclusions name sets and pieces one at a time; filters name characteristics
- every γ set, all Low Rank armour, tier III talismans, pieces whose fire
resistance is below zero - and so keep working when the data gains pieces
that match. Every filter defaults to include, so an empty GearFilters is
the whole data, unchanged.

Applied to the game data before a run rather than inside the search: the
armour side becomes a list of piece names to exclude, handed to the
optimiser alongside the user's own exclusions, so pins, the pre-search
proofs and the "only excluded armour provides it" messages all treat a
filtered piece exactly like an excluded one without the engine knowing
filters exist. Turning transcending off is the one filter that changes
pieces rather than removing them, so it is applied to the data itself.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field, replace

from load_data import ArmorPiece, Defense, GameData, Talisman
from optimiser import TALISMAN_SLOT

VARIANTS = ("α", "β", "γ")
VARIANT_NAMES = {"α": "Alpha", "β": "Beta", "γ": "Gamma"}
RANKS = ("high", "low")
RARITIES = tuple(range(1, 9))
TALISMAN_TIERS = (1, 2, 3, 4, 5)
ELEMENTS = ("fire", "water", "thunder", "ice", "dragon")

# Stored as plain ASCII so profiles stay easy to hand-edit; the GUI shows
# the typographic forms.
OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    "=": operator.eq,
    ">=": operator.ge,
    ">": operator.gt,
}

_TIER_NUMERALS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_TIER = re.compile(r" (I|II|III|IV|V)$")


def variant_of(piece: ArmorPiece) -> str | None:
    """'Gore α' -> 'α'. Low Rank sets carry no letter and return None."""
    suffix = piece.set.rsplit(" ", 1)[-1]
    return suffix if suffix in VARIANTS else None


def talisman_tier(talisman: Talisman) -> int | None:
    """'Attack Charm III' -> 3. A name with no numeral has no tier."""
    match = _TIER.search(talisman.name)
    return _TIER_NUMERALS[match.group(1)] if match else None


@dataclass(frozen=True)
class ResistanceRule:
    """Exclude armour whose resistance to one element compares this way."""

    element: str
    op: str
    value: int

    def __post_init__(self) -> None:
        if self.element not in ELEMENTS:
            raise ValueError(f"{self.element!r} is not an element; expected one of {', '.join(ELEMENTS)}.")
        if self.op not in OPERATORS:
            raise ValueError(f"{self.op!r} is not a comparison; expected one of {' '.join(OPERATORS)}.")

    def matches(self, piece: ArmorPiece) -> bool:
        return OPERATORS[self.op](getattr(piece.resistances, self.element), self.value)

    def describe(self) -> str:
        return f"{self.element} resistance {self.op} {self.value}"


@dataclass
class GearFilters:
    """What to leave out. Every field's default keeps everything in."""

    exclude_variants: set[str] = field(default_factory=set)
    exclude_ranks: set[str] = field(default_factory=set)
    exclude_rarities: set[int] = field(default_factory=set)
    exclude_talisman_tiers: set[int] = field(default_factory=set)
    # On by default because the High Rank data lists transcended values: off
    # swaps in each transcendable piece's slots and defence from before.
    transcendence: bool = True
    resistance_rules: list[ResistanceRule] = field(default_factory=list)
    min_defense: int = 0  # maximum defence, after the transcendence choice
    exclude_slotless: bool = False

    def is_default(self) -> bool:
        return self == GearFilters()

    def armour_reason(self, piece: ArmorPiece) -> str | None:
        """Why this piece is filtered out, or None if it stays.

        Expects a piece the transcendence choice has already been applied
        to, so the defence threshold and the slot test see what the search
        will see.
        """
        variant = variant_of(piece)
        if variant in self.exclude_variants:
            return f"{VARIANT_NAMES[variant]} ({variant}) armour is filtered out"
        if piece.rank in self.exclude_ranks:
            return f"{piece.rank.capitalize()} Rank armour is filtered out"
        if piece.rarity in self.exclude_rarities:
            return f"rarity {piece.rarity} armour is filtered out"
        if self.exclude_slotless and not any(piece.slots):
            return "armour with no decoration slots is filtered out"
        if piece.defense.max < self.min_defense:
            return f"its defence {piece.defense.max} is below the minimum {self.min_defense}"
        for rule in self.resistance_rules:
            if rule.matches(piece):
                return f"armour with {rule.describe()} is filtered out"
        return None

    def keeps_talisman(self, talisman: Talisman) -> bool:
        return talisman_tier(talisman) not in self.exclude_talisman_tiers

    def describe(self) -> list[str]:
        """One line per active filter, for result headers and the GUI."""
        lines = []
        if self.exclude_variants:
            lines.append(
                "Without "
                + ", ".join(
                    f"{VARIANT_NAMES[v]} ({v})" for v in VARIANTS if v in self.exclude_variants
                )
                + " sets"
            )
        if self.exclude_ranks:
            lines.append(
                "Without "
                + " and ".join(f"{r.capitalize()} Rank" for r in RANKS if r in self.exclude_ranks)
                + " armour"
            )
        if self.exclude_rarities:
            lines.append(
                "Without rarity " + ", ".join(str(r) for r in sorted(self.exclude_rarities))
            )
        if self.exclude_talisman_tiers:
            numerals = {v: k for k, v in _TIER_NUMERALS.items()}
            lines.append(
                "Without talisman tiers "
                + ", ".join(numerals[t] for t in sorted(self.exclude_talisman_tiers))
            )
        if not self.transcendence:
            lines.append("Transcending off: untranscended slots and defence")
        if self.min_defense:
            lines.append(f"Minimum piece defence {self.min_defense}")
        if self.exclude_slotless:
            lines.append("Without slotless armour")
        for rule in self.resistance_rules:
            lines.append(f"Without {rule.describe()}")
        return lines

    # --- profile form ---------------------------------------------------------

    def to_dict(self) -> dict:
        """Only what differs from the default, so a profile stays short."""
        out: dict = {}
        if self.exclude_variants:
            out["exclude_variants"] = [v for v in VARIANTS if v in self.exclude_variants]
        if self.exclude_ranks:
            out["exclude_ranks"] = [r for r in RANKS if r in self.exclude_ranks]
        if self.exclude_rarities:
            out["exclude_rarities"] = sorted(self.exclude_rarities)
        if self.exclude_talisman_tiers:
            out["exclude_talisman_tiers"] = sorted(self.exclude_talisman_tiers)
        if not self.transcendence:
            out["transcendence"] = False
        if self.min_defense:
            out["min_defense"] = self.min_defense
        if self.exclude_slotless:
            out["exclude_slotless"] = True
        if self.resistance_rules:
            out["resistance_rules"] = [
                {"element": r.element, "op": r.op, "value": r.value}
                for r in self.resistance_rules
            ]
        return out

    @classmethod
    def from_dict(cls, raw, where: str = "filters") -> "GearFilters":
        """Shape-checked, refusing unknown keys and values like a profile does."""
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be a mapping.")
        allowed = {
            "exclude_variants", "exclude_ranks", "exclude_rarities",
            "exclude_talisman_tiers", "transcendence", "min_defense",
            "exclude_slotless", "resistance_rules",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"{where} has unknown keys: {', '.join(sorted(unknown))}.")

        def members(key: str, valid) -> set:
            value = raw.get(key) or []
            if not isinstance(value, list) or any(v not in valid for v in value):
                raise ValueError(
                    f"{where}.{key} must be a list drawn from "
                    + ", ".join(str(v) for v in valid) + "."
                )
            return set(value)

        def whole(key: str, default: int) -> int:
            value = raw.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{where}.{key} must be a whole number of 0 or more.")
            return value

        transcendence = raw.get("transcendence", True)
        slotless = raw.get("exclude_slotless", False)
        if not isinstance(transcendence, bool) or not isinstance(slotless, bool):
            raise ValueError(f"{where}: transcendence and exclude_slotless are true or false.")

        rules = []
        for entry in raw.get("resistance_rules") or []:
            if not isinstance(entry, dict) or set(entry) != {"element", "op", "value"}:
                raise ValueError(f"{where}.resistance_rules entries need element, op and value.")
            value = entry["value"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{where}.resistance_rules values are whole numbers.")
            rules.append(ResistanceRule(entry["element"], str(entry["op"]), value))

        return cls(
            exclude_variants=members("exclude_variants", VARIANTS),
            exclude_ranks=members("exclude_ranks", RANKS),
            exclude_rarities=members("exclude_rarities", RARITIES),
            exclude_talisman_tiers=members("exclude_talisman_tiers", TALISMAN_TIERS),
            transcendence=transcendence,
            resistance_rules=rules,
            min_defense=whole("min_defense", 0),
            exclude_slotless=slotless,
        )


def untranscend(piece: ArmorPiece) -> ArmorPiece:
    """The piece as it is before Armor Transcending; others unchanged."""
    before = piece.untranscended
    if before is None:
        return piece
    return replace(
        piece,
        slots=list(before.slots),
        defense=Defense(base=piece.defense.base, max=before.defense_max),
        slots_source="base",
        untranscended=None,
    )


@dataclass
class FilteredData:
    """The data a run sees, and why each filtered piece is out."""

    game: GameData
    # piece name -> reason; handed to the optimiser as excluded pieces
    removed: dict[str, str]


def apply_filters(game: GameData, filters: GearFilters | None) -> FilteredData:
    """Apply the filters to the base game data.

    Call this before any custom talisman file is folded in: the tier filter
    is for the craftable talismans, whose names carry the tier, and a custom
    talisman's name is whatever its author typed.

    Armour stays in game.armor even when filtered out, so a pin or an
    exclusion naming it still resolves; it is the removed map, passed on as
    exclusions, that keeps it out of the search.
    """
    if filters is None or filters.is_default():
        return FilteredData(game=game, removed={})
    armour = game.armor if filters.transcendence else [untranscend(p) for p in game.armor]
    removed = {}
    for piece in armour:
        reason = filters.armour_reason(piece)
        if reason is not None:
            removed[piece.name] = reason
    talismans = [t for t in game.talismans if filters.keeps_talisman(t)]
    return FilteredData(
        game=replace(game, armor=armour, talismans=talismans), removed=removed
    )


def pin_conflicts(
    original: GameData, filtered: FilteredData, pins: dict[str, str]
) -> list[str]:
    """Pins naming something the filters removed, one line each.

    Refused like a pinned-and-excluded piece, for the same reason: keeping
    the pin ignores the filter, dropping it ignores the pin, and neither
    should happen without the user choosing. A pinned talisman only
    conflicts if it is craftable, in original, and gone from filtered;
    a custom one is never filtered.
    """
    problems = []
    for slot, name in pins.items():
        if slot == TALISMAN_SLOT:
            craftable = {t.name for t in original.talismans}
            kept = {t.name for t in filtered.game.talismans}
            if name in craftable and name not in kept:
                problems.append(
                    f"{name} is the pinned talisman, but its tier is filtered out."
                )
            continue
        reason = filtered.removed.get(name)
        if reason:
            problems.append(f"{name} is pinned to {slot}, but {reason}.")
    return problems
