"""Loads the project's MHWilds data YAML files into typed data objects."""

from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent

SKILLS_PATH = DATA_DIR / "skills_default.yaml"
ARMOR_PATH = DATA_DIR / "high_rank_armor.yaml"
LOW_RANK_ARMOR_PATH = DATA_DIR / "low_rank_armor.yaml"
UNTRANSCENDED_PATH = DATA_DIR / "armor_untranscended.yaml"
TALISMANS_PATH = DATA_DIR / "craftable_talismans.yaml"
DECORATIONS_PATH = DATA_DIR / "decorations.yaml"


@dataclass
class SkillRank:
    """What one level of a skill does, in the game's own wording."""

    level: int
    effect: str
    # Set bonuses and group skills name each tier ("Black Eclipse I"); ordinary
    # skills do not, and an empty string keeps them out of saved files.
    name: str = ""


@dataclass
class Skill:
    name: str
    type: str
    description: str
    max_level: int
    scaling: str
    weight: float
    level_weight: float
    source_url: str
    # Defaulted, and so last, because food skills have no per-level text and a
    # weighted file saved before this field existed must still load.
    levels: list[SkillRank] = field(default_factory=list)


@dataclass
class SkillLevel:
    name: str
    level: int


@dataclass
class Defense:
    base: int
    max: int


@dataclass
class Resistances:
    fire: int = 0
    water: int = 0
    thunder: int = 0
    ice: int = 0
    dragon: int = 0


@dataclass
class SetBonusEffect:
    skill: str
    pieces_required: int


@dataclass
class SetBonus:
    name: str
    type: str
    effects: list[SetBonusEffect]


@dataclass
class Untranscended:
    """A transcendable piece as it is before Armor Transcending.

    Only the values transcending changes: the slots and the maximum
    defence. Base defence, skills and resistances are the same either way.
    """

    slots: list[int]
    defense_max: int


@dataclass
class ArmorPiece:
    name: str
    piece_type: str
    set: str
    rarity: int
    defense: Defense
    resistances: Resistances
    skills: list[SkillLevel]
    slots: list[int]
    slots_source: str
    set_bonuses: list[SetBonus]
    source_url: str
    # Defaulted, and so last, because the High Rank file predates both and
    # does not repeat them: every piece in it is High Rank, and its
    # untranscended values live in armor_untranscended.yaml.
    rank: str = "high"
    untranscended: Untranscended | None = None


@dataclass
class DecorationSlots:
    armour: int = 0
    weapon: int = 0


@dataclass
class Talisman:
    name: str
    rarity: int
    skills: list[SkillLevel]
    slots: list[int]
    decoration_slots: DecorationSlots
    source_url: str


@dataclass
class Decoration:
    name: str
    type: str
    rarity: int
    slot_level: int
    skills: list[SkillLevel]
    source_url: str


@dataclass
class GameData:
    skills: list[Skill] = field(default_factory=list)
    armor: list[ArmorPiece] = field(default_factory=list)
    talismans: list[Talisman] = field(default_factory=list)
    decorations: list[Decoration] = field(default_factory=list)


def _load_yaml(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _skill_levels(raw: list[dict]) -> list[SkillLevel]:
    return [SkillLevel(name=s["name"], level=s["level"]) for s in raw]


def load_skills(path: Path = SKILLS_PATH) -> list[Skill]:
    raw = _load_yaml(path)
    allowed = {f.name for f in fields(Skill)}
    required = {
        f.name
        for f in fields(Skill)
        if f.default is MISSING and f.default_factory is MISSING
    }

    if isinstance(raw, dict) and "sets" in raw:
        raise ValueError(f"{path.name} is an optimiser results file, not a skills file.")
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], dict):
        raise ValueError(f"{path.name} is not a skills file: expected a list of skills.")
    if not required <= set(raw[0]) <= allowed:
        raise ValueError(
            f"{path.name} is not a skills file: entries should have the fields "
            f"{', '.join(sorted(required))}, and optionally levels."
        )

    skills = []
    for entry in raw:
        levels = [SkillRank(**rank) for rank in entry.pop("levels", None) or []]
        skills.append(Skill(**entry, levels=levels))
    return skills


def skill_record(skill: Skill) -> dict:
    """A skill as it is written to YAML: unnamed tiers drop the empty name."""
    record = asdict(skill)
    for rank in record["levels"]:
        if not rank["name"]:
            del rank["name"]
    if not record["levels"]:
        del record["levels"]
    return record


def load_armor(path: Path = ARMOR_PATH) -> list[ArmorPiece]:
    pieces = []
    for raw in _load_yaml(path):
        pieces.append(
            ArmorPiece(
                name=raw["name"],
                piece_type=raw["piece_type"],
                set=raw["set"],
                rarity=raw["rarity"],
                defense=Defense(**raw["defense"]),
                resistances=Resistances(**raw["resistances"]),
                skills=_skill_levels(raw["skills"]),
                slots=raw["slots"],
                slots_source=raw["slots_source"],
                set_bonuses=[
                    SetBonus(
                        name=sb["name"],
                        type=sb["type"],
                        effects=[SetBonusEffect(**e) for e in sb["effects"]],
                    )
                    for sb in raw.get("set_bonuses", [])
                ],
                source_url=raw["source_url"],
                rank=raw.get("rank", "high"),
            )
        )
    return pieces


def load_untranscended(path: Path = UNTRANSCENDED_PATH) -> dict[str, Untranscended]:
    """Piece name -> its values before transcending."""
    return {
        raw["name"]: Untranscended(slots=raw["slots"], defense_max=raw["defense_max"])
        for raw in _load_yaml(path)
    }


def load_all_armor() -> list[ArmorPiece]:
    """High Rank then Low Rank, with untranscended values attached.

    A name in the untranscended file that matches no transcended piece is
    refused rather than skipped: it means one of the two files was renamed
    or edited without the other, and a skipped record would quietly leave
    that piece transcended when transcending is switched off.
    """
    pieces = load_armor(ARMOR_PATH) + load_armor(LOW_RANK_ARMOR_PATH)
    transcended = {p.name: p for p in pieces if p.slots_source == "transcended"}
    for name, values in load_untranscended().items():
        piece = transcended.get(name)
        if piece is None:
            raise ValueError(
                f"{UNTRANSCENDED_PATH.name} lists {name!r}, which is not a "
                "transcended piece in the armour data."
            )
        piece.untranscended = values
    return pieces


def load_talismans(path: Path = TALISMANS_PATH) -> list[Talisman]:
    talismans = []
    raw_list = _load_yaml(path) or []
    for raw in raw_list:
        talismans.append(
            Talisman(
                name=raw["name"],
                rarity=raw["rarity"],
                skills=_skill_levels(raw["skills"]),
                slots=raw.get("slots", []),
                decoration_slots=DecorationSlots(**raw.get("decoration_slots", {})),
                source_url=raw.get("source_url", ""),
            )
        )
    return talismans


def load_decorations() -> list[Decoration]:
    decorations = []
    for raw in _load_yaml(DECORATIONS_PATH):
        decorations.append(
            Decoration(
                name=raw["name"],
                type=raw["type"],
                rarity=raw["rarity"],
                slot_level=raw["slot_level"],
                skills=_skill_levels(raw["skills"]),
                source_url=raw["source_url"],
            )
        )
    return decorations


def load_game_data() -> GameData:
    return GameData(
        skills=load_skills(),
        armor=load_all_armor(),
        talismans=load_talismans(),
        decorations=load_decorations(),
    )


if __name__ == "__main__":
    data = load_game_data()
    print(f"Skills:      {len(data.skills)}")
    print(f"Armor:       {len(data.armor)}")
    print(f"Talismans:   {len(data.talismans)}")
    print(f"Decorations: {len(data.decorations)}")
