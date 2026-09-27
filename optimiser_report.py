"""Rendering for optimiser results.

Kept separate from optimiser.py so a GUI can consume the same GearSet objects
and replace only this layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from load_data import ArmorPiece, Talisman
from optimiser import (
    PIECE_TYPES,
    TALISMAN_SLOT,
    WEAPON_SOURCE,
    GearSet,
    Scoring,
    SlotAssignment,
)

CONSTRAINT_DESCRIPTIONS = {
    0: "all mandatory skills present, and mandatory max-level skills at max",
    1: "all mandatory skills present (max-level requirement could not be met)",
    2: "no mandatory skill requirement could be met - ranked on score alone",
}

WIDTH = 78
PIN_MARKER = "* "  # leading column on a pinned piece; legend lives in the header


def _pin_marker(gear_set: GearSet, piece) -> str:
    return PIN_MARKER if piece.piece_type in gear_set.pinned_types else "  "


def _charm_marker(gear_set: GearSet) -> str:
    return PIN_MARKER if TALISMAN_SLOT in gear_set.pinned_types else "  "


def _skill_note(name: str, level: int, scoring: Scoring) -> str:
    skill = scoring.by_name.get(name)
    if skill is None:
        return ""
    notes = []
    if level >= skill.max_level:
        notes.append("MAX")
    if name in scoring.mandatory_max:
        notes.append("mandatory-max")
    elif name in scoring.mandatory:
        notes.append("mandatory")
    if skill.weight:
        notes.append(f"w{skill.weight:g}/lw{skill.level_weight:g}")
    return "  " + " ".join(f"[{n}]" for n in notes) if notes else ""


def _sorted_skills(gear_set: GearSet, scoring: Scoring):
    def sort_key(item):
        name, level = item
        skill = scoring.by_name.get(name)
        weight = skill.weight if skill else 0.0
        return (-weight, -level, name)

    return sorted(gear_set.skill_levels.items(), key=sort_key)


def _bonus_pieces_text(bonus) -> str:
    text = f"{bonus.pieces} pieces"
    if bonus.extra:
        text += f" (+{bonus.extra} weapon)"
    return text


def _decorations_by_source(gear_set: GearSet) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for placement in gear_set.placements:
        if placement.decoration is None:
            continue
        grouped.setdefault(placement.source, []).append(placement.decoration.name)
    for placement in gear_set.weapon_placements:
        if placement.decoration is None:
            continue
        # A talisman can hold both kinds; its weapon jewels get their own row
        # so nobody tries to socket one in an armour slot.
        source = (
            placement.source
            if placement.source == WEAPON_SOURCE
            else f"{placement.source} (weapon slots)"
        )
        grouped.setdefault(source, []).append(placement.decoration.name)
    return grouped


def _own_weapon_slots(gear_set: GearSet) -> list[SlotAssignment]:
    return [p for p in gear_set.weapon_placements if p.source == WEAPON_SOURCE]


def _free_weapon_line(gear_set: GearSet) -> list[str]:
    """The weapon-side counterpart of "Free slots", or nothing without any."""
    if not gear_set.weapon_placements:
        return []
    free = gear_set.weapon_free_slots
    if not free:
        return ["  Free weapon slots: none"]
    sizes = ", ".join(str(s) for s in free)
    return [f"  Free weapon slots: [{sizes}] (nothing worth slotting)"]


def render_set(gear_set: GearSet, rank: int, scoring: Scoring) -> str:
    lines = ["=" * WIDTH]
    tier = f"  [{gear_set.tier}]" if gear_set.tier else ""
    lines.append(
        f"Set {rank}{tier}   score {gear_set.total_score:.2f}"
        f"   (skills {gear_set.skill_score:.2f} + defence {gear_set.defense_score:.2f})"
    )
    lines.append("-" * WIDTH)

    for piece in gear_set.pieces:
        slots = ", ".join(str(s) for s in piece.slots if s) or "-"
        lines.append(
            f"{_pin_marker(gear_set, piece)}{piece.piece_type:<6} {piece.name:<26}"
            f" {piece.set:<18} def {piece.defense.max:>3}  slots [{slots}]"
        )
    own = _own_weapon_slots(gear_set)
    if own:
        slots = ", ".join(str(p.size) for p in own)
        lines.append(f"  {'weapon':<6} {'':<26} {'':<18}          slots [{slots}]")
    talisman_skills = ", ".join(
        f"{s.name} {s.level}" for s in gear_set.talisman.skills
    )
    lines.append(
        f"{_charm_marker(gear_set)}{'charm':<6} {gear_set.talisman.name:<26} {talisman_skills}"
    )
    lines.append(f"  Total defence {gear_set.defense_total}")

    lines.append("")
    lines.append("  Skills:")
    for name, level in _sorted_skills(gear_set, scoring):
        skill = scoring.by_name.get(name)
        if skill is None:
            continue
        if skill.type in ("Set Bonus", "Group"):
            continue
        capped = min(level, skill.max_level)
        lines.append(
            f"    {name:<28} {capped}/{skill.max_level}{_skill_note(name, capped, scoring)}"
        )

    if gear_set.active_bonuses:
        lines.append("")
        lines.append("  Set / group bonuses:")
        for bonus in gear_set.active_bonuses:
            kind = "group" if bonus.bonus_type == "group_skill" else "set"
            effects = ", ".join(bonus.effects)
            weight = scoring.weight(bonus.name)
            marker = f"  [w{weight:g}]" if weight else ""
            lines.append(
                f"    {bonus.name:<28} {kind:<5} {_bonus_pieces_text(bonus)}"
                f" -> level {bonus.level}  ({effects}){marker}"
            )

    grouped = _decorations_by_source(gear_set)
    lines.append("")
    if grouped:
        lines.append("  Decorations:")
        for source, decos in grouped.items():
            lines.append(f"    {source:<28} {', '.join(decos)}")
    else:
        lines.append("  Decorations: none worth slotting")

    if gear_set.free_slots:
        sizes = ", ".join(str(s) for s in gear_set.free_slots)
        reserved = min(gear_set.reserved_slots, len(gear_set.free_slots))
        note = (
            f" ({reserved} reserved for resistance jewels)"
            if reserved
            else " (nothing worth slotting)"
        )
        lines.append(f"  Free slots: [{sizes}]{note}")
    else:
        lines.append("  Free slots: none")
    lines.extend(_free_weapon_line(gear_set))

    return "\n".join(lines)


def _placements_by_source(gear_set: GearSet) -> dict[str, list[SlotAssignment]]:
    """Every slot (filled or not) grouped by the piece/talisman it belongs to."""
    grouped: dict[str, list[SlotAssignment]] = {}
    for placement in gear_set.placements:
        grouped.setdefault(placement.source, []).append(placement)
    return grouped


def _slot_brackets(placements: list[SlotAssignment]) -> str:
    """'[3: Attack Jewel III] [1: empty]'; weapon slots read 'W3:'.

    The W marks the one line where both kinds meet - a talisman with armour
    and weapon slots - and costs nothing on the lines where only one can.
    """
    if not placements:
        return ""
    parts = [
        f"[{'W' if p.weapon else ''}{p.size}: "
        f"{p.decoration.name if p.decoration else 'empty'}]"
        for p in placements
    ]
    return "  " + " ".join(parts)


def render_set_inline(gear_set: GearSet, rank: int, total: int, scoring: Scoring) -> str:
    """Render a set with each piece's decoration slots inline on its own line.

    Used by the GUI results viewer (one set at a time); the console renderer
    (render_set) instead groups decorations into a separate section.
    """
    lines = []
    tier = f"  [{gear_set.tier}]" if gear_set.tier else ""
    lines.append(
        f"Set {rank} of {total}{tier}   score {gear_set.total_score:.2f}"
        f"   (skills {gear_set.skill_score:.2f} + defence {gear_set.defense_score:.2f})"
    )
    lines.append("-" * WIDTH)

    by_source = _placements_by_source(gear_set)
    for piece in gear_set.pieces:
        brackets = _slot_brackets(by_source.get(piece.name, []))
        lines.append(
            f"{_pin_marker(gear_set, piece)}{piece.piece_type:<6} {piece.name:<26}"
            f" {piece.set:<18} def {piece.defense.max:>3}{brackets}"
        )
    own = _own_weapon_slots(gear_set)
    if own:
        lines.append(f"  {'weapon':<6} {'':<26} {'':<18}        {_slot_brackets(own)}")

    talisman_skills = ", ".join(
        f"{s.name} {s.level}" for s in gear_set.talisman.skills
    )
    talisman_slots = by_source.get(gear_set.talisman.name, []) + [
        p for p in gear_set.weapon_placements if p.source == gear_set.talisman.name
    ]
    talisman_brackets = _slot_brackets(talisman_slots)
    lines.append(
        f"{_charm_marker(gear_set)}{'charm':<6} {gear_set.talisman.name:<26}"
        f" {talisman_skills}{talisman_brackets}"
    )
    lines.append(f"  Total defence {gear_set.defense_total}")

    lines.append("")
    lines.append("  Skills:")
    for name, level in _sorted_skills(gear_set, scoring):
        skill = scoring.by_name.get(name)
        if skill is None or skill.type in ("Set Bonus", "Group"):
            continue
        capped = min(level, skill.max_level)
        lines.append(
            f"    {name:<28} {capped}/{skill.max_level}{_skill_note(name, capped, scoring)}"
        )

    if gear_set.active_bonuses:
        lines.append("")
        lines.append("  Set / group bonuses:")
        for bonus in gear_set.active_bonuses:
            kind = "group" if bonus.bonus_type == "group_skill" else "set"
            effects = ", ".join(bonus.effects)
            weight = scoring.weight(bonus.name)
            marker = f"  [w{weight:g}]" if weight else ""
            lines.append(
                f"    {bonus.name:<28} {kind:<5} {_bonus_pieces_text(bonus)}"
                f" -> level {bonus.level}  ({effects}){marker}"
            )

    lines.append("")
    if gear_set.free_slots:
        sizes = ", ".join(str(s) for s in gear_set.free_slots)
        reserved = min(gear_set.reserved_slots, len(gear_set.free_slots))
        note = (
            f" ({reserved} reserved for resistance jewels)"
            if reserved
            else " (nothing worth slotting)"
        )
        lines.append(f"  Free slots: [{sizes}]{note}")
    else:
        lines.append("  Free slots: none")
    lines.extend(_free_weapon_line(gear_set))

    return "\n".join(lines)


# --- structured view (the GUI results window) ------------------------------

# Game order, which is also the order the results window draws them in.
ELEMENTS = ("fire", "water", "thunder", "ice", "dragon")


@dataclass
class SlotCell:
    size: int
    level: int  # the jewel's own slot level; 0 for an empty slot
    decoration: str  # "" for an empty slot
    weapon: bool = False


@dataclass
class EquipmentRow:
    kind: str  # piece type, "weapon" or "charm"
    name: str
    detail: str  # the armour set, or the talisman's skills
    defence: int | None
    pinned: bool
    slots: list[SlotCell]


@dataclass
class SkillMeter:
    name: str
    level: int
    max_level: int
    tags: list[str]

    @property
    def maxed(self) -> bool:
        return self.level >= self.max_level

    @property
    def minimal(self) -> bool:
        """One level of a skill that goes to 3 or more: barely there."""
        return self.level == 1 and self.max_level >= 3


@dataclass
class BonusLine:
    name: str
    kind: str  # "set" or "group"
    pieces: str
    level: int
    effects: str
    weight: float


@dataclass
class SetView:
    rank: int
    total: int
    tier: str
    total_score: float
    skill_score: float
    defence_score: float
    equipment: list[EquipmentRow]
    defence_total: int
    skills: list[SkillMeter]
    bonuses: list[BonusLine]
    free_slots: list[str]
    resistances: dict[str, int]


# "Attack Jewel【3】" -> "Attack Jewel": the view draws the level as pips.
_LEVEL_SUFFIX = re.compile(r"\s*【\d+】$")


def _cells(placements: list[SlotAssignment]) -> list[SlotCell]:
    return [
        SlotCell(
            size=p.size,
            level=p.decoration.slot_level if p.decoration else 0,
            decoration=_LEVEL_SUFFIX.sub("", p.decoration.name) if p.decoration else "",
            weapon=p.weapon,
        )
        for p in placements
    ]


def set_resistances(gear_set: GearSet) -> dict[str, int]:
    """The armour's elemental resistances summed, in ELEMENTS order.

    Talismans and decorations carry none of their own, and resistance skills
    are left as skills: this is the number the equipment screen shows.
    """
    return {
        element: sum(getattr(p.resistances, element) for p in gear_set.pieces)
        for element in ELEMENTS
    }


def _free_slot_notes(gear_set: GearSet) -> list[str]:
    if gear_set.free_slots:
        sizes = ", ".join(str(s) for s in gear_set.free_slots)
        reserved = min(gear_set.reserved_slots, len(gear_set.free_slots))
        note = (
            f" ({reserved} reserved for resistance jewels)"
            if reserved
            else " (nothing worth slotting)"
        )
        notes = [f"Free slots: [{sizes}]{note}"]
    else:
        notes = ["Free slots: none"]
    return notes + [line.strip() for line in _free_weapon_line(gear_set)]


def set_view(gear_set: GearSet, rank: int, total: int, scoring: Scoring) -> SetView:
    """Everything render_set_inline shows, as data a GUI can lay out itself."""
    by_source = _placements_by_source(gear_set)
    equipment = [
        EquipmentRow(
            kind=piece.piece_type,
            name=piece.name,
            detail=piece.set,
            defence=piece.defense.max,
            pinned=piece.piece_type in gear_set.pinned_types,
            slots=_cells(by_source.get(piece.name, [])),
        )
        for piece in gear_set.pieces
    ]
    own = _own_weapon_slots(gear_set)
    if own:
        equipment.append(EquipmentRow("weapon", "", "", None, False, _cells(own)))
    talisman = gear_set.talisman
    talisman_slots = by_source.get(talisman.name, []) + [
        p for p in gear_set.weapon_placements if p.source == talisman.name
    ]
    equipment.append(
        EquipmentRow(
            kind="charm",
            name=talisman.name,
            detail=", ".join(f"{s.name} {s.level}" for s in talisman.skills),
            defence=None,
            pinned=TALISMAN_SLOT in gear_set.pinned_types,
            slots=_cells(talisman_slots),
        )
    )

    skills = []
    for name, level in _sorted_skills(gear_set, scoring):
        skill = scoring.by_name.get(name)
        if skill is None or skill.type in ("Set Bonus", "Group"):
            continue
        capped = min(level, skill.max_level)
        note = _skill_note(name, capped, scoring)
        # [MAX] is what the meter's colour already says.
        tags = [t.strip("[]") for t in note.split() if t != "[MAX]"]
        skills.append(SkillMeter(name, capped, skill.max_level, tags))

    bonuses = [
        BonusLine(
            name=b.name,
            kind="group" if b.bonus_type == "group_skill" else "set",
            pieces=_bonus_pieces_text(b),
            level=b.level,
            effects=", ".join(b.effects),
            weight=scoring.weight(b.name),
        )
        for b in gear_set.active_bonuses
    ]

    return SetView(
        rank=rank,
        total=total,
        tier=gear_set.tier,
        total_score=gear_set.total_score,
        skill_score=gear_set.skill_score,
        defence_score=gear_set.defense_score,
        equipment=equipment,
        defence_total=gear_set.defense_total,
        skills=skills,
        bonuses=bonuses,
        free_slots=_free_slot_notes(gear_set),
        resistances=set_resistances(gear_set),
    )


def render_console(
    sets: list[GearSet],
    scoring: Scoring,
    constraint_level: int,
    db_path: Path,
    pinned: dict[str, ArmorPiece] | None = None,
    pinned_talisman: Talisman | None = None,
    strict: bool = True,
    reasons: list[str] | None = None,
    excluded: list[str] | None = None,
) -> str:
    header = ["=" * WIDTH, f"MH Wilds gear sets for {db_path}"]
    # In strict mode the tier is always 0 by construction, so naming it would
    # only repeat the requirement line below.
    if not strict:
        header.append(
            f"Constraint tier {constraint_level}: "
            f"{CONSTRAINT_DESCRIPTIONS[constraint_level]}"
        )
    mandatory = sorted(scoring.mandatory)
    if mandatory:
        header.append("Mandatory skills: " + ", ".join(mandatory))
    pins = [
        f"{piece_type} {pinned[piece_type].name}"
        for piece_type in PIECE_TYPES
        if piece_type in (pinned or {})
    ]
    if pinned_talisman is not None:
        pins.append(f"talisman {pinned_talisman.name}")
    if pins:
        header.append(f"Pinned ({PIN_MARKER.strip()}): " + ", ".join(pins))
    if excluded:
        header.append("Excluded: " + ", ".join(excluded))
    if strict and mandatory:
        header.append("Mandatory skills are required: sets missing one are not shown.")
    elif constraint_level > 0 and mandatory:
        header.append(
            "NOTE: constraints were relaxed to fill the requested number of sets."
        )

    if not sets:
        header.append("")
        header.append(
            "No gear set meets these requirements."
            if strict and mandatory
            else "No gear sets could be built."
        )
        header.extend(f"  - {reason}" for reason in reasons or [])
        if strict and mandatory:
            header.append("Pass --relax to search without the requirement.")
        return "\n".join(header)

    body = [render_set(s, i, scoring) for i, s in enumerate(sets, start=1)]
    return "\n".join(header) + "\n" + "\n".join(body)


def gear_set_to_dict(gear_set: GearSet, rank: int, scoring: Scoring) -> dict:
    return {
        "rank": rank,
        "tier": gear_set.tier,
        "score": {
            "total": round(gear_set.total_score, 3),
            "skills": round(gear_set.skill_score, 3),
            "defence": round(gear_set.defense_score, 3),
        },
        "defence_total": gear_set.defense_total,
        "pieces": [
            {
                "piece_type": p.piece_type,
                "name": p.name,
                "set": p.set,
                "defence": p.defense.max,
                "slots": [s for s in p.slots if s],
                "pinned": p.piece_type in gear_set.pinned_types,
            }
            for p in gear_set.pieces
        ],
        "talisman": {
            "name": gear_set.talisman.name,
            "skills": [
                {"name": s.name, "level": s.level} for s in gear_set.talisman.skills
            ],
        },
        "skills": {
            name: min(level, scoring.by_name[name].max_level)
            for name, level in _sorted_skills(gear_set, scoring)
            if name in scoring.by_name
        },
        "set_bonuses": [
            {
                "name": b.name,
                "type": b.bonus_type,
                "pieces": b.pieces,
                "extra_from_weapon": b.extra,
                "level": b.level,
                "effects": list(b.effects),
            }
            for b in gear_set.active_bonuses
        ],
        "decorations": [
            {
                "source": p.source,
                "slot_size": p.size,
                "decoration": p.decoration.name,
                "weapon_slot": p.weapon,
            }
            for p in gear_set.placements + gear_set.weapon_placements
            if p.decoration is not None
        ],
        "free_slots": gear_set.free_slots,
        "free_weapon_slots": gear_set.weapon_free_slots,
        "reserved_slots": gear_set.reserved_slots,
        "weapon_slots": gear_set.weapon_slots,
    }


def write_yaml(
    sets: list[GearSet],
    scoring: Scoring,
    constraint_level: int,
    db_path: Path,
    output: Path,
) -> None:
    payload = {
        "skills_db": str(db_path),
        "constraint_level": constraint_level,
        "constraint": CONSTRAINT_DESCRIPTIONS[constraint_level],
        "sets": [
            gear_set_to_dict(s, i, scoring) for i, s in enumerate(sets, start=1)
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        yaml.dump(payload, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
