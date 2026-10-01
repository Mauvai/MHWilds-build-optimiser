"""Compare the local game data with the Wilds API and report what differs.

    python check_data.py                  fetch from wilds.mhdb.io and compare
    python check_data.py --save-api DIR   ...and keep the fetched JSON in DIR
    python check_data.py --api-dir DIR    compare against JSON saved earlier

Report-only by design. The local files were compiled by hand from game8 and
carry what the API does not give, transcended slot values and source URLs
among them; and where the two disagree, it is not known in advance which
side is wrong - the first run found a piece type the local file may have
wrong. So instead of overwriting, this says where to look after a title
update: new pieces and jewels, records the API no longer has (renamed, or
never in it), and fields that disagree.

Exit status: 0 when nothing differs, 1 when something does, 2 when the API
could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from load_data import ArmorPiece, Decoration, Skill, load_game_data

API = "https://wilds.mhdb.io"
# Projections keep each response to the fields compared here; the full
# armour list with crafting materials runs to several megabytes otherwise.
ENDPOINTS = {
    "version": ("/version", None, None),
    "armor": (
        "/en/armor",
        {"rank": "high"},
        ["name", "kind", "slots", "skills.skill.name", "skills.skill.kind", "skills.level"],
    ),
    "decorations": (
        "/en/decorations",
        None,
        ["name", "kind", "slot", "skills.skill.name", "skills.level"],
    ),
    "skills": ("/en/skills", None, ["name", "kind", "ranks.level"]),
}
TIMEOUT = 60  # seconds; the first request for a list can be slow before it is cached


def fetch(endpoint: str) -> object:
    path, query, projection = ENDPOINTS[endpoint]
    params = {}
    if query:
        params["q"] = json.dumps(query)
    if projection:
        params["p"] = json.dumps({name: True for name in projection})
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers={"User-Agent": "MHWilds-build-optimiser"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


# --- normalising names ------------------------------------------------------

_SIZE_SUFFIX = re.compile(r"\s*[\[【]\s*\d\s*[\]】]\s*$")


def jewel_key(name: str) -> str:
    """'Charge/KO Jwl【3】' and 'Charge/KO Jewel [3]' -> 'charge/ko jewel'.

    game8 writes the size in full-width brackets with no space and the API
    in square ones after a space, and both abbreviate "Jewel" to "Jwl" on
    some long names but not on the same ones. The size is compared on its
    own, so dropping it from the key loses nothing.
    """
    base = _SIZE_SUFFIX.sub("", name)
    base = re.sub(r"\bJwl\b", "Jewel", base)
    return " ".join(base.split()).casefold()


def skill_map(entries) -> dict[str, int]:
    return {e["skill"]["name"]: e["level"] for e in entries}


# --- comparing ----------------------------------------------------------------


@dataclass
class Section:
    title: str
    only_api: list[str] = field(default_factory=list)
    only_local: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.only_api or self.only_local or self.changed)


def compare_armor(local: list[ArmorPiece], api: list[dict]) -> Section:
    """High Rank pieces by name.

    The API gives slots before transcending. A base-slot piece is compared
    on its own slots; a transcended one on its untranscended slots from
    armor_untranscended.yaml, since its listed slots are transcended and
    every one would read as a difference. Low Rank pieces are the API's own
    records, copied rather than compiled, so they are not compared here.
    """
    section = Section("Armour (High Rank)")
    local_by_name = {p.name: p for p in local}
    api_by_name = {a["name"]: a for a in api}
    section.only_api = sorted(set(api_by_name) - set(local_by_name))
    section.only_local = sorted(set(local_by_name) - set(api_by_name))

    for name in sorted(set(api_by_name) & set(local_by_name)):
        piece, record = local_by_name[name], api_by_name[name]
        if "kind" in record and record["kind"] != piece.piece_type:
            section.changed.append(f"{name}: slot {piece.piece_type} here, {record['kind']} in the API")
        base_slots = None
        if piece.slots_source == "base":
            base_slots = piece.slots
        elif piece.untranscended is not None:
            base_slots = piece.untranscended.slots
        if base_slots is not None:
            here = sorted((s for s in base_slots if s), reverse=True)
            there = sorted(record.get("slots", []), reverse=True)
            if here != there:
                section.changed.append(f"{name}: slots {here} here, {there} in the API")
        if "skills" in record:
            here = {s.name: s.level for s in piece.skills}
            there = {
                e["skill"]["name"]: e["level"]
                for e in record["skills"]
                if e["skill"].get("kind", "armor") == "armor"
            }
            if here != there:
                section.changed.append(f"{name}: skills {here} here, {there} in the API")
    return section


def compare_decorations(local: list[Decoration], api: list[dict]) -> Section:
    section = Section("Decorations")
    local_by_key = {jewel_key(d.name): d for d in local}
    api_by_key = {jewel_key(a["name"]): a for a in api}
    section.only_api = sorted(api_by_key[k]["name"] for k in set(api_by_key) - set(local_by_key))
    section.only_local = sorted(local_by_key[k].name for k in set(local_by_key) - set(api_by_key))

    for key in sorted(set(api_by_key) & set(local_by_key)):
        deco, record = local_by_key[key], api_by_key[key]
        if record.get("slot") != deco.slot_level:
            section.changed.append(
                f"{deco.name}: size {deco.slot_level} here, {record.get('slot')} in the API"
            )
        if record.get("kind") != deco.type:
            section.changed.append(
                f"{deco.name}: {deco.type} jewel here, {record.get('kind')} in the API"
            )
        if "skills" in record:
            here = {s.name: s.level for s in deco.skills}
            there = skill_map(record["skills"])
            if here != there:
                section.changed.append(f"{deco.name}: skills {here} here, {there} in the API")
    return section


def compare_skills(local: list[Skill], api: list[dict]) -> Section:
    """Armour and weapon skills by name, and their number of levels.

    Set bonuses, group skills and food skills are left out: the local file
    stores a bonus as one row with its piece tiers as levels, and matching
    that shape against the API's has not been worked out yet.
    """
    section = Section("Skills (armour and weapon)")
    kinds = {"armor": "Armor", "weapon": "Weapon"}
    local_by_name = {s.name: s for s in local if s.type in kinds.values()}
    api_by_name = {a["name"]: a for a in api if a.get("kind") in kinds}
    section.only_api = sorted(set(api_by_name) - set(local_by_name))
    section.only_local = sorted(set(local_by_name) - set(api_by_name))

    for name in sorted(set(api_by_name) & set(local_by_name)):
        skill, record = local_by_name[name], api_by_name[name]
        if kinds[record["kind"]] != skill.type:
            section.changed.append(f"{name}: {skill.type} here, {record['kind']} in the API")
        ranks = record.get("ranks")
        if ranks is not None and len(ranks) != skill.max_level:
            section.changed.append(
                f"{name}: max level {skill.max_level} here, {len(ranks)} in the API"
            )
    return section


def render(sections: list[Section], version: str) -> str:
    lines = [f"Wilds API data version {version}", ""]
    for section in sections:
        lines.append(f"{section.title}: " + ("no differences" if section.clean else ""))
        for label, items in (
            ("In the API only (new, or named differently here)", section.only_api),
            ("Here only (renamed or removed in the API, or never in it)", section.only_local),
            ("Different", section.changed),
        ):
            if items:
                lines.append(f"  {label}: {len(items)}")
                lines.extend(f"    {item}" for item in items)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--api-dir", metavar="DIR", help="read saved API JSON instead of fetching")
    source.add_argument("--save-api", metavar="DIR", help="also save the fetched API JSON here")
    for stream in (sys.stdout, sys.stderr):  # names carry α/β/γ; see optimiser.main
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser.parse_args()

    payloads: dict[str, object] = {}
    try:
        for endpoint in ENDPOINTS:
            if args.api_dir:
                path = Path(args.api_dir) / f"{endpoint}.json"
                payloads[endpoint] = json.loads(path.read_text(encoding="utf-8"))
            else:
                payloads[endpoint] = fetch(endpoint)
    except (OSError, ValueError) as exc:
        print(f"Could not read the API data ({endpoint}): {exc}", file=sys.stderr)
        return 2

    if args.save_api:
        target = Path(args.save_api)
        target.mkdir(parents=True, exist_ok=True)
        for endpoint, payload in payloads.items():
            (target / f"{endpoint}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )

    game = load_game_data()
    sections = [
        compare_armor([p for p in game.armor if p.rank == "high"], payloads["armor"]),
        compare_decorations(game.decorations, payloads["decorations"]),
        compare_skills(game.skills, payloads["skills"]),
    ]
    print(render(sections, payloads["version"].get("version", "unknown")))
    return 0 if all(s.clean for s in sections) else 1


if __name__ == "__main__":
    sys.exit(main())
