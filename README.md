# Monster Hunter Wilds Build Optimiser

Describe the build you want instead of scrolling through armour piece by piece. You assign a **weight** to each skill, "how much do I care about this?", and a **level weight**, "how much do further levels matter?", and the optimiser searches every armor piece, High and Low Rank, every craftable talisman and decoration for the combinations that score best against your weighting.

One deliberate choice: it does not return ten near-identical builds. Results are banded into *closest variants*, *distinct builds* (built from different pieces) and *distinct bonuses* (set or group bonus effects not already in the list).

## Requirements

- Python 3 with `tkinter`, tested on Python 3.13. Standard CPython installers already include tkinter, so this is normally nothing to do.
- PyYAML, installed with `pip install pyyaml`. Nothing else: the search engine needs no other dependency.

`launch_gui.bat` starts the GUI with `pythonw`, which opens no console window, so a missing dependency fails silently and nothing appears to happen. If double-clicking does nothing, run `python skills_gui.py` from a terminal to see the traceback.

## Quick Start (GUI)

1. Double-click `launch_gui.bat`.

2. On the **Skill Weights** tab, give each skill you care about a weight and a level weight, and see [How scoring works](#how-scoring-works) for what the two mean. The *Type* dropdown narrows the list to armor, weapon, set bonus, group or food skills so you can work through them in order; *Find* matches any part of a name, ignoring case; *Weighted only* shows just the skills you've already set. Weighted skills display their `weight/level` beside the name, so a whole weighting can be reviewed at a glance instead of skill by skill. *Clear All Weights…* zeroes every edit after asking; the file itself only changes when you save.

3. **Run Optimiser** uses whatever values are on screen, saved or not, saving exists only to keep a weighting around afterwards. When you do save, the default name is `skills_weighted.yaml`, placed next to whichever file you opened, usually the repo root, where it shows as an untracked git file unless you *Browse…* to put it in `skills_outputs/`. Keep separate weighted copies side by side for different build goals rather than overwriting one file each time. Saving refuses `skills_default.yaml` outright: the save box writes beside the loaded file, which is usually that one, so typing its name would otherwise replace the master data. Any other existing file is confirmed before replacement, and the GUI asks before discarding unsaved edits if you close the window or open another skills file.

4. Optional settings before running:
   - tick a **Gogma weapon** set bonus or group skill if your weapon contributes a piece toward one of them, see [How it works](#how-it-works) for what that credit buys;
   - set your weapon's **Weapon Slots** so weapon skills get weapon jewels, see [Weapon slots](#weapon-slots);
   - rule armour out with **Exclude Gear…**, see [Excluding armour](#excluding-armour);
   - leave whole categories out on the **Filters** tab, γ sets, Low Rank, a talisman tier, armour weak to an element, or turn transcending off, see [Filters tab](#filters-tab), and set minimum total defence and resistances in its **Targets** box, see [Build targets](#build-targets);
   - load custom talismans built on the other tab, so they join the optimiser's charm pool without touching `craftable_talismans.yaml`;
   - adjust *Reserved slots* (default 2) for the resistance jewels you plan to slot yourself per hunt. The set's smallest slots are held back, so they're size 1 unless the set runs out of those first.

5. While the search runs, a progress bar under the Run button shows which phase it's in, and **Cancel** stops it within a fraction of a second. Results open in a second window with Previous/Next navigation. **Copy This Set** copies the set's text to the clipboard, and **Save All Sets…** writes every set to `optimiser_outputs/`, either as YAML (the same structure the CLI writes) or with a `.txt` name as the CLI's console text, header included. The header records which skills file the weights came from and notes when the run used edits you hadn't saved yet, so the export never points at a file that doesn't hold the weights actually used.

### Filters Tab

Exclusions name sets and pieces one at a time; filters name characteristics, so they keep working when the data gains pieces that match. Each row has an **Include** and an **Exclude** box and starts on Include, so a fresh Filters tab changes nothing:

| Group | Rows |
| --- | --- |
| Armour sets | Alpha (α), Beta (β) and Gamma (γ) sets; High Rank and Low Rank armour; rarity 1 to 8 |
| Armour pieces | Pieces with no decoration slots; pieces whose maximum defence is below a number you set |
| Talismans | Craftable talismans by tier, the numeral their name ends in: tier III is every "… Charm III" |
| Resistances | Per element, armour whose resistance compares a chosen way (`<`, `≤`, `=`, `≥`, `>`) with a value, e.g. fire `<` 0 |

**Enable transcendence**, on by default, is the one that changes pieces rather than removing them. Off, every rarity 5 and 6 piece counts with its slots and maximum defence from before Armor Transcending (see [Data scope](#data-scope)), for armour you haven't upgraded; the minimum-defence row then compares against those lower values too. A line under the heading counts how many pieces and talismans the current filters leave out, so a filter's reach shows before running rather than as an empty result.

Low Rank sets carry no α/β/γ letter, so the variant rows never touch them. The tier rows only filter the craftable talismans: a custom talisman's name is whatever you typed, so reading a tier from it would be a guess. Resistances are the same on every piece of a set in the data, which is what lets a piece-level rule read as "sets weak to fire".

A filtered piece reaches the search as an exclusion, so everything that already handled exclusions handles filters: the pre-search proofs say a requirement is only met by excluded armour, and a pin on a filtered piece is refused, as a pinned-and-excluded piece is, rather than one instruction quietly winning. Results and exports list the active filters under the exclusions. The CLI takes the same filters as `--exclude-variant`, `--exclude-rank`, `--exclude-rarity`, `--exclude-talisman-tier`, `--no-transcendence`, `--min-defense`, `--exclude-slotless` and `--exclude-resistance 'fire<0'`, each adding to a profile's.

### Build Targets

The **Targets** box on the Filters tab sets minimum totals over the five armour pieces: total defence, the same "Total defence" the results show, and each element's resistance. Untick a row for no target; 0 is a real resistance target ("nothing weak to fire overall"), not a blank. Talismans and jewels add to neither here: resistance skills stay skills, applied in game on top of these numbers, which is also what the reserved slots are for. Targets are hard: a set short of one is never shown, ticked relax or not, because relaxing is about required skills.

They're enforced inside the search rather than by filtering its output. Each slot's best defence and best resistance per targeted element over the pieces it may use is known up front, so a partial set that couldn't reach a target even with the best of everything still to come is dropped before it takes a place in the beam. That bound is exact, so it never drops a set that could make it, and summed over all five slots it proves an unreachable target before searching starts, reported the same way as an impossible requirement. Dominated-piece pruning also compares targeted resistances, or a piece could be pruned for one that matches it everywhere except the resistance the target needs. Targets are checked one at a time, so two that are each reachable but never together come back as "the search found nothing" rather than a proof. CLI: `--target-defense 420 --target-resistance fire=0`.

### Search Profiles

**Load Profile…** and **Save Profile…** on the Skills File row keep a whole build goal in one file under `profiles/`: the weights plus every setting on the tab, pins, exclusions, weapon slots, Gogma choices, reserve, relax, the loaded custom talisman file and the Filters tab with its targets. Only the filters that differ from Include are written, so a profile saved before filters existed loads unchanged. The CLI reads the same file with `--profile`, and `--save-profile` writes one from any run, including a plain `--skills-db` run, so an existing weighted file converts in a single command.

A profile stores only the weights of the weighted skills, against `skills_default.yaml`, never a copy of the skill data. That's the difference from a weighted skills file, which duplicates every skill's description and levels: after a data update the weighted file still carries the old text and knows nothing of new skills, while a profile picks up the new data the next time it loads. Paths are stored relative to the repo, so a profile keeps working if you move the checkout.

A profile that names something the data doesn't have, a skill, a pinned or excluded piece, a Gogma bonus no armour carries, a missing talisman file, is refused whole, listing every mismatch at once rather than being half-applied. On the CLI, options given alongside `--profile` override it, except exclusions, which add to the profile's: an extra `--exclude-set` reads as "and also leave this out".

### Custom Talismans Tab

Build charms the game doesn't actually have: up to three skills each, plus up to three armour and three weapon decoration slots per talisman, saved under `custom_talismans_outputs/`. Only armor-type skills are offered, because every talisman in the data carries armour skills only; any weapon slots on a custom talisman receive weapon jewels the same way your weapon does (see [Weapon slots](#weapon-slots)). Load a file on the main tab when you want those charms considered, for example to approximate your best appraised talisman.

**Select/Create File…** opens an existing file for editing or names a new one, and writes nothing by itself, so it never asks to replace anything, opens by itself the first time you visit the tab, and only on click afterwards. A talisman may not list the same skill twice or take a level below 1, because two rows of one skill would stack past the max-level check a single row gets. **Delete Selected** rewrites the file immediately and asks first; there is no undo.

### Update Check

At start-up the GUI asks GitHub whether this checkout is behind [`Mauvai/MHWilds-build-optimiser`](https://github.com/Mauvai/MHWilds-build-optimiser) `main`, and if it is, a banner above the tabs says by how many commits and gives the pull command, naming your local remote for that repo when one exists (`git pull origin main`) and the full URL when none does. It's one request to GitHub's compare API on a background thread, so a slow or absent network never holds the window up; offline, rate-limited, or a copy with no `.git` folder (a zip download) all simply show nothing. The local commit is read from `.git` directly, so git needn't be on PATH, and git is only run for the one case GitHub can't answer: local commits never pushed anywhere, where it checks read-only whether upstream's latest commit is already in your history. **Check for Updates**, beside Dark Mode, turns it off and is remembered in `gui_state.json`.

## Command Line

```
python optimiser.py --skills-db skills_weighted.yaml --count 10
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--skills-db` | `skills_outputs/skills_DB_burst.yaml` | The weighted skills YAML the search optimises against |
| `--profile` | none | Load weights and settings from a [search profile](#search-profiles) instead of `--skills-db`; any option below also passed here wins |
| `--count` | `10` | How many sets to return, dealt round-robin across the diversity bands; minimum 1 |
| `--beam` | `5000` | The beam-search width; see [How it works](#how-it-works) for why that value; minimum 1 |
| `--reserve` | `2` | Slots held back for resistance jewels, smallest first; 0 allowed |
| `--output` | `optimiser_outputs/`, named from the input with any `skills_` prefix stripped | Where the sets are written. Keeping results out of the skills folders means a results file can't be mistaken for a skills DB, and `load_data.py` would refuse it anyway |
| `--pin-head` … `--pin-legs` | none | Force a slot to a named armour piece; see [Pinning armour](#pinning-armour) |
| `--weapon-slots` | none | Your weapon's decoration slot sizes, e.g. `3,2,1`; see [Weapon slots](#weapon-slots) |
| `--exclude-set`, `--exclude-piece` | none | Keep an armour set or a single piece out of the search; repeatable. See [Excluding armour](#excluding-armour) |
| `--gogma-set`, `--gogma-group` | none | Credit one piece toward a set bonus or group skill, the same as the GUI's Gogma selectors |
| `--talismans` | none | A custom talisman file to add to the charm pool |
| `--relax` | off | Return sets that miss a mandatory skill instead of returning fewer |
| `--exclude-variant`, `--exclude-rank`, `--exclude-rarity`, `--exclude-talisman-tier`, `--exclude-resistance`, `--exclude-slotless`, `--min-defense`, `--no-transcendence` | none | The [Filters tab](#filters-tab) on the command line; repeatable where it makes sense, and added to a profile's filters |
| `--target-defense`, `--target-resistance` | none | [Build targets](#build-targets): minimum total defence, and `ELEMENT=N` per element (repeatable); each replaces a profile's value |
| `--save-profile` | none | Also save the weights and settings this run used as a profile |

**Pass `--skills-db` or `--profile` every time.** The default points at a weighted file that isn't committed (everything under the output folders is gitignored), so a bare `python optimiser.py` stops with a usage error naming the missing file. Input `skills_weighted.yaml` writes results to `optimiser_outputs/weighted_gear_sets.yaml`. A `--count` or `--beam` below 1, or a negative `--reserve`, is rejected up front; each of these used to produce an empty result with no explanation.

In a terminal the CLI redraws a one-line progress indicator on stderr while it works; piped or redirected it stays quiet, so carriage returns never leak into a file. The console rendering begins with a header naming your mandatory skills and any pinned pieces. With `--relax` it also names the constraint tier the search ended on and adds a "constraints were relaxed" note ([How scoring works](#how-scoring-works)); without it there's nothing to relax, so a run either meets every requirement or says why it couldn't. Weighted skills that armour, talismans and jewels simply cannot supply, food skills, or weapon skills when no weapon slots were given, are skipped with a warning naming them; the GUI doesn't surface that warning. A *required* skill is different: without `--relax` it empties the result, and the no-sets message names it as the reason, in the GUI as well.

## How Scoring Works

Both values sit on roughly a 0–5 scale, and 5 and above is where they stop being ordinary:

- **weight ≥ 5** makes the skill *mandatory*: no build is returned unless it has the skill.
- **level_weight ≥ 5** (with weight ≥ 5) makes it *mandatory-max*: the build must reach the skill's maximum level. If relaxing is on, this is the first requirement to be dropped.

Below those thresholds the weights are continuous. A skill's first level is worth `weight²`, and `level_weight` sets what each level after it adds, as a share of that first level: nothing at 0, so a lone level 1 is full credit, rising evenly to 1.5× the first level at 5, so a skill you want levelled pulls toward finishing it. A level's worth never depends on how many levels the skill has, so one more level of a weight-4 skill beats the first level of a weight-3 one whether it goes to 3 or to 5. Negative weights actively punish skills you don't want (resistances eating decoration slots, say), and zero means the skill is ignored entirely.

The GUI presents this as a −1 to 5 dropdown: −1 avoids, 0 ignores, 1–4 grade how much you want the skill, and 5 mandates it. The file accepts any number, though, so a hand-edited value outside the dropdown's range is kept and shown rather than snapped. −1 sounds too small to matter, one point of it scores −1 against a weight-4 skill's 16, but the skills you avoid are usually incidental to whatever piece carries them, so any penalty at all tips the balance. Two skills that appear in all ten default sets vanish from the results completely at −1. What it cannot do is outweigh a piece you genuinely want for another reason; nothing in this range beats a weight-4 skill sitting on the same armour.

Mandatory is a **hard filter**. A set missing a mandatory skill is never returned, even if that means fewer sets than you asked for, or none at all. Relaxing is opt-in, `--relax` or the GUI's **Allow sets missing a required skill** checkbox, and only then does the old tiered ranking apply: drop max-levels first, then the requirement itself. The hard filter is deliberate. When relaxing was the default, the GUI, which never displayed the tier, would present sets that had quietly abandoned a requirement as though it had been met.

When nothing qualifies, you get the reason at one of two strengths. Required set bonuses and group skills are checked arithmetically *before* the search starts. No piece carries more than one set bonus, and none carries more than one group skill, so the pieces several requirements need simply add up, and if that total exceeds the slots left free after pinning, the combination is unreachable outright. A set bonus and a group skill *can* share a piece, so mixes of the two are checked exactly instead: every way of filling the free slots is tried against just the required bonuses. That's how Gore Magala's Tyranny at max plus Alluring Pelt (4 + 3 pieces, none carrying both) is ruled out before searching begins. Required skills that nothing in the data can grant, weapon skills for instance, are caught at the same stage. Anything that survives all of that and still finds nothing is reported as how far short each requirement fell, phrased as what the search didn't find rather than what doesn't exist, because the beam is a heuristic and hasn't earned the stronger claim.

### Set Bonuses As Requirements

Set bonuses and group skills are ordinary rows in the skills file, so they take weights exactly like any other skill; there's no separate mechanism. `weight: 5` requires the bonus at level 1, and `weight: 5` with `level_weight: 5` requires it at max. For Gore Magala's Tyranny that means the 2-piece and 4-piece effects. Every set bonus in the data has exactly two levels and every group skill one, so those two settings cover every requirement the game can express.

## Shaping the Search

Alongside the weights, three optional settings shape which gear the search may use: pin a slot to a specific piece, exclude pieces or sets you won't wear, and declare your weapon's slots so weapon skills can be filled at all.

### Pinning Armour

Any of the five slots can be fixed to a specific piece while the search fills the rest, useful when you've already decided on part of a set and want to know what's possible around it. On the CLI that's `--pin-waist "Gore Coil α"`; in the GUI it's the **Fixed gear** panel on the Skill Weights tab, where each slot lists the armour sets that have a piece for it. A set plus a slot identifies a piece uniquely across the entire armour data, so choosing the set is enough, no second dropdown. The Gogma weapon selectors sit in the same panel, being effectively a sixth piece's worth of set bonus.

A pinned slot skips dominated-piece pruning, because pruning only exists to choose between alternatives, and a pinned slot has none. Its single candidate also sorts first in the search's stage order, so every later choice is ranked with the pinned piece already counted. Pinned pieces are marked with `*` in the results.

Pinning narrows the diversity bands: *distinct builds* wants a two-piece difference from everything else chosen, which four pins make impossible, so that band relaxes its rule and tags its sets `(relaxed)`. It's arithmetic, not a warning to act on.

### Weapon Slots

Weapon skills (Attack Boost, Critical Eye, Artillery, the rest) come only from weapon jewels, and weapon jewels fit only weapon slots. So the optimiser needs your weapon's slot layout before it can do anything with those skills: `--weapon-slots 3,2,1` on the CLI, or the three **Weapon Slots** dropdowns in the Fixed Gear panel, where 0 means no slot. Without that information, a weighted weapon skill is ignored with a warning, and a required one is reported as impossible. A custom talisman's weapon slots are filled the same way whenever that talisman is chosen. Results show a `weapon` line, and in the GUI's slot brackets a weapon slot reads `[W3: …]`, so a talisman carrying both kinds of slot can't be socketed wrong.

The weapon is solved separately from the armour, and exactly. Nothing on the armour side grants a weapon skill, so the two can't trade off against each other; the weapon's answer is simply added to every set. Weapon jewels also don't fit the armour model: many grant two skills at once, or two or three levels in one, where every armour jewel is one skill at one level. The solver is a branch-and-bound over every jewel in every slot, verified against brute force in the tests, and it runs once per distinct slot layout, so even with every weapon skill weighted it costs well under a second for a weapon's three slots. The one case that can grow is three weapon slots plus three more on a custom talisman with most weapon skills weighted; that stops after a fixed amount of searching (`WEAPON_SEARCH_NODES`, a few seconds) and keeps the best layout found, which stays good because the best jewels are tried first. Only a fully searched weapon layout is ever used as proof that a required weapon skill can't be reached.

A weapon's own built-in skills aren't modelled yet; only its slots are.

### Excluding Armour

The opposite of pinning: armour the search must never touch, such as sets you haven't unlocked or pieces you won't wear. On the CLI, `--exclude-set "Gore α"` or `--exclude-piece "Lagiacrus Helm β"`, each repeatable. In the GUI, **Exclude Gear…** in the Fixed Gear panel opens a filterable tree of every set with its pieces underneath; double-click a row, or select rows and press Space, to toggle them. Sets and single pieces are tracked separately, so re-including a set you excluded doesn't forget the pieces you'd excluded one by one inside it.

Exclusions affect everything that asks what armour can supply, not just the search: the unreachable-skill warning and the pre-search proofs both say "only excluded armour provides it" rather than blaming the data. Excluding every piece for a slot is reported before the search starts, requirements or not. A piece that is both pinned and excluded is refused, on the CLI and in the GUI alike, because either resolution would silently ignore one of the two instructions.

## How It Works

The core is a beam search over the five equipment slots: one piece per slot plus exactly one talisman (each complete armour combination is tried against the five best talismans), then decorations placed by an *exact* search over the free slots, using the cheapest armour jewel for each skill, and only where a gem raises a weighted skill, never to fill space. The smallest `--reserve` slots stay open for per-hunt resistance jewels and are released automatically only if holding them back would miss a mandatory skill. Required set bonuses count toward that decision too: leave them out of the calculation and a required bonus reads as missing on every attempt, so the reserved slots are never released even when a required skill needs them.

A few choices keep the beam from collapsing or wasting effort:

- Pieces that another piece beats or ties on every relevant skill, slot count and defense are pruned first, but only within the same set-bonus group, since a piece carrying a weighted bonus is never interchangeable with one that lacks it.
- The beam caps how many partial states may share a given set/group-bonus signature. Without the cap it converges on one build neighbourhood and the *distinct bonuses* band has nothing left to draw from.
- Width 5000 (and 2000 final combinations) is where quality plateaus: 3000/1200 left about 1% of score on the table, while 9000/15000 found nothing better at several times the runtime.

The Gogma selectors credit **one extra piece** toward a chosen set bonus and one toward a chosen group skill, standing in for the bonus point your weapon carries, which can complete a bonus that none of the chosen armour carries at all. They only offer bonuses that some armour piece carries; a bonus's piece thresholds come from the armour data, so one that no armour carries (Soul of the Dark Knight) has none, and a point credited to it would do nothing.

### The Scoring Model

Defense also counts toward the score. Each piece's maximum defense is normalised between 62 and 94 and raised to the power 2.5, which penalises low-defense pieces harder than their linear share would, and whole-set defense is worth roughly one weight-2 skill.

The exponents are chosen deliberately. Skill value grows as `weight²`, because with exponent 1 a level-1 weight-2 skill outscored another level of a weight-4 skill and the search filled sets with shallow filler; above roughly 2.5 the gap gets so extreme that mid-weight skills lose their depth again. The constants (`WEIGHT_EXPONENT`, `TOP_LEVEL_VALUE`, `DEFENSE_EXPONENT`) live at the top of [optimiser.py](optimiser.py) with a note on why each value is what it is, if you want to push quality or speed in a different direction.

### Result Bands

| Band | Count (at `--count 10`) | Rule |
| --- | --- | --- |
| closest variants | 3 | Differs from every chosen set by at least 1 piece |
| distinct builds | 3 | Differs from every chosen set by at least 2 pieces |
| distinct bonuses | 4 | Set/group bonus combination not already in the results |

A band that can't fill itself relaxes its own rule and tags those sets `(relaxed)`, so you still get the requested count. A `--count` below 10 spreads the sets round-robin across the three bands instead of shorting one of them; above 10 the 3/3/4 split is kept and the extras are dealt round-robin on top.

## Data Scope

The optimiser sees a deliberately narrow slice of the game:

| Included | Excluded | Why |
| --- | --- | --- |
| High and Low Rank armor, transcended or not | Layered armour | Cosmetic only |
| Craftable talismans | Appraised talismans | Random skills and slots can't be enumerated, so build equivalents in the Custom Talismans tab instead |
| Armor and weapon jewels | Weapons themselves | You supply your weapon's slot sizes and the jewels go in them; the weapon's own built-in skills aren't modelled |

The High Rank armour, talismans, jewels and skills were compiled by hand from the community wiki at [game8.co](https://game8.co/games/Monster-Hunter-Wilds). Every record carries a `source_url` back to its page, and the file headers list the source archives. Set bonus skills use their 2-piece/4-piece tiers as levels; group and food skills are on/off with no levels. The one exception is each skill's `levels` list, what each level actually does in the game's own words (Fire Resistance 3 is "Fire resistance +20 Defense +10"), shown under the description when you click a skill. Game8 has no machine-readable export and keeps that text on one page per skill, so it was pulled in a single request from the [Wilds API](https://wilds.mhdb.io/en/skills), which carries the in-game strings; spot checks against game8's skill pages agree on every number. Food skills have no per-level text in either source. The skill file also records each skill's *scaling* class (linear, geometric, and so on), inferred from how the numeric effect grows across levels, useful context when choosing weights, though it doesn't feed the optimiser directly.

Two armour files came from the [Wilds API](https://wilds.mhdb.io) instead, and say so in their headers. `low_rank_armor.yaml` is every Low Rank piece, copied as the API has it, with `rank: low` added; Low Rank was outside the tool's scope until the Filters tab could switch it on and off, and the API is the only machine-readable source for it. `armor_untranscended.yaml` holds, for each of the 381 pieces whose High Rank slots are listed transcended, the slots and maximum defence it has *before* Armor Transcending, which is what the API reports. Transcending adds 14 maximum defence at rarity 5 and 10 at rarity 6 and upgrades the slots; base defence, skills and resistances don't change. `load_data.py` attaches those values to their pieces and refuses a name that matches no transcended piece, because a skipped record would leave that piece transcended when transcending is switched off.

Need current record counts? Run `python load_data.py` instead of trusting any number written down; the data files are updated more often than this document.

### Checking The Data After A Title Update

```
python check_data.py
```

compares the local files against the [Wilds API](https://wilds.mhdb.io) and lists what differs: High Rank pieces, jewels and armour/weapon skills that exist on one side only, and records that disagree on slot type, slots, skills, jewel size or max level. **It reports and never writes.** The local files hold things the API doesn't provide, transcended slot values and game8 source URLs among them, and where the two disagree it isn't known in advance which side is wrong, so the report says where to look rather than choosing. `--save-api DIR` keeps the fetched JSON, and `--api-dir DIR` compares against it again offline. Exit status is 0 when nothing differs, 1 when something does, 2 when the API couldn't be read.

Transcended pieces are compared on their untranscended slots, since those are what the API gives. Defence isn't compared: the High Rank file's maximum is the transcended one, so it runs higher than the API's for every transcendable piece by design. Low Rank pieces aren't compared either, being the API's own records. Talismans, and set bonus, group and food skills, aren't checked yet.

The first run against real data already found one: the local file lists *Sealed Dragon Cloth α* as a chest piece where the API has it as a head, and *Pinion Necklace α* as a head the API's head list doesn't contain. Worth checking in game before trusting either.

## Tests

```
python -m unittest
```

from the repo root. It needs nothing beyond PyYAML and takes about twenty seconds, most of it a handful of real searches. `tests/test_data.py` checks that the data keeps the shape the code assumes without re-checking, no piece carries two set bonuses, for instance, which is exactly what lets the pre-search check add pieces up as a proof, so a hand edit to the YAML that breaks an assumption fails there rather than surfacing later as a wrong answer.

The GUI tests never open a window: they call `SkillsGui` methods on stand-in objects with the message boxes patched out, covering what gets saved, refused, or passed to the optimiser, but not layout or widget wiring. Where Tk is missing they fall back to a mocked tkinter, so the suite also runs on a bare Linux Python.

## Repository Layout

| File | Purpose |
| --- | --- |
| `skills_gui.py` | The Tkinter GUI: skill weighting, custom talismans, and the optimiser runner with its results window |
| `optimiser.py` | Beam-search engine and scoring model (`Scoring`, `Optimiser`, `GearSet`) plus the CLI entry point. Deliberately print-free so the GUI can reuse it directly |
| `optimiser_report.py` | Rendering only: the console text, the inline result view for the GUI window, and YAML export of results |
| `check_data.py` | Report-only comparison of the local data with the Wilds API; see [Checking the data](#checking-the-data-after-a-title-update) |
| `update_check.py` | The start-up comparison of the local commit with GitHub; see [Update check](#update-check) |
| `gear_filters.py` | The Filters tab's logic: `GearFilters`, turning transcending off, and the piece names a set of filters excludes |
| `search_profile.py` | The search profile format: loading with shape checks, `profile_problems` against the game data, saving |
| `load_data.py` | Typed dataclasses (`Skill`, `ArmorPiece`, `Talisman`, `Decoration`) and loaders with validation (it refuses to treat a results file as a skills DB, for example). Run it directly for record counts |
| `skills_default.yaml` | Every skill: armor, weapon, set bonus, group and food, with descriptions, max level, per-level effects, scaling class and per-source URLs. `weight`/`level_weight` start at 0 placeholders |
| `low_rank_armor.yaml`, `armor_untranscended.yaml` | Low Rank pieces, and the pre-transcending slots and defence of transcendable High Rank ones, both from the Wilds API; see [Data scope](#data-scope) |
| `high_rank_armor.yaml` | All High Rank pieces: defense, resistances, skills, transcended slot values where applicable (`slots_source` says which are listed) and the set/group bonuses each piece participates in |
| `craftable_talismans.yaml` | Smithy-crafted charms only. Craftables carry no decoration slots, but the schema keeps a placeholder for custom ones built in the GUI |
| `decorations.yaml` | Armor and weapon jewels with their slot sizes and skills |
| `tests/` | The `unittest` suite; see [Tests](#tests) |
| `launch_gui.bat` | Windows launcher: runs `pythonw skills_gui.py` from the repo folder |
| `gui_state.json` | Written by the GUI when Dark Mode or Check for Updates is toggled and on close; holds only those two settings. Gitignored |
| `skills_outputs/`, `optimiser_outputs/`, `custom_talismans_outputs/`, `profiles/` | Your generated files. Contents are gitignored, but each folder keeps a tracked `.keepempty` marker so they exist on clone |

## License

[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0), see [license.txt](license.txt). Free to use and share for anything non-commercial; commercial use requires a separate agreement with the licensor.
