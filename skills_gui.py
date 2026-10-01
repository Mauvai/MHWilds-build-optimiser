"""GUI for assigning weight / level_weight to skills, building custom
talismans, and running the gear set optimiser.

Filter skills by type, edit their weight and level_weight, then save a
copy of skills.yaml (with the edited values). A second tab lets the user
build custom talismans (not present in craftable_talismans.yaml) and save
them to their own file; the main tab can load such a file and fold those
talismans into the optimiser's talisman pool without touching the yaml.
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from dataclasses import dataclass, field, replace
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import yaml

from gear_filters import (
    ELEMENTS,
    RARITIES,
    TALISMAN_TIERS,
    VARIANT_NAMES,
    VARIANTS,
    GearFilters,
    ResistanceRule,
    apply_filters,
    pin_conflicts,
)
from load_data import (
    DATA_DIR,
    SKILLS_PATH,
    DecorationSlots,
    GameData,
    Skill,
    SkillLevel,
    Talisman,
    load_game_data,
    load_skills,
    load_talismans,
    skill_record,
)
from optimiser import (
    MAX_WEAPON_SLOTS,
    PIECE_TYPES,
    RESERVED_SLOTS,
    TALISMAN_SLOT,
    GearSet,
    Scoring,
    SearchCancelled,
    bonus_base_name,
    gear_set_filename,
    optimise,
    talisman_slot_sizes,
)
from optimiser_report import render_console, render_set_inline, set_view, write_yaml
from results_view import draw_set
import update_check
from search_profile import (
    PROFILES_DIR,
    SearchProfile,
    apply_weights,
    load_profile,
    profile_problems,
    save_profile,
    stored_path,
    weights_of,
)

NONE_OPTION = "(None)"
CUSTOM_TALISMANS_DIR = DATA_DIR / "custom_talismans_outputs"
OPTIMISER_OUTPUTS_DIR = DATA_DIR / "optimiser_outputs"
MAX_TALISMAN_SKILLS = 3
MAX_TALISMAN_SLOTS = 3

HINT_WRAP = 300  # px; the widest column a hint has to sit in is the gear panel

# Light keeps whatever ttk theme Tk starts with - vista on Windows, which draws
# entries, comboboxes and scrollbars through the OS and ignores colour styling.
# That is exactly why dark has to switch to clam: clam is drawn by Tk, so its
# colours can be set. The two modes therefore look different by construction,
# and that is the accepted trade for leaving the light appearance untouched.
THEMES = {
    "light": {
        "ttk_theme": None,  # None means "whatever Tk started with"
        "hint": "#555555",
        "status": "#2a7f2a",
        "update": "#b35900",
        "window": None,  # None means "leave the widget's own default alone"
        "text_bg": "white",
        "text_fg": "black",
        "select_bg": "#0078d7",
        "select_fg": "white",
    },
    "dark": {
        "ttk_theme": "clam",
        "hint": "#a0a0a0",
        "status": "#6fbf6f",
        "update": "#f0a040",
        "window": "#1f1f1f",
        "surface": "#2b2b2b",  # entries, buttons, anything inset
        "border": "#3c3c3c",
        "text": "#e6e6e6",
        "disabled": "#6b6b6b",
        "hover": "#3a3a3a",
        "text_bg": "#252526",
        "text_fg": "#e6e6e6",
        "select_bg": "#094771",
        "select_fg": "#ffffff",
    },
}

# Tuning the layout means tuning these, not hunting through the builders. The
# skill list is the one thing that has no natural height of its own, so it names
# its own here and the rest of the layout follows from it.
LIST_ROWS = 18
GAP = 4  # vertical breathing room between a hint and the control it describes
SCREEN_MARGIN = 80  # px left for the title bar and taskbar when sizing to fit
GUI_STATE_PATH = DATA_DIR / "gui_state.json"  # remembered settings only; gitignored
UPDATE_POLL_MS = 250  # how often the main thread looks for the update check's answer
SECTION_PAD = (8, 4, 8, 8)  # inside every titled box, so they all read alike
PIN_DETAIL_HEIGHT = 18  # px reserved per pinned-piece line, set or not

# Every control that holds a value gets one of these above it. They live in one
# dict rather than inline at each widget so wording stays consistent and a
# behaviour change cannot leave a caption describing what the code used to do -
# "weight" in particular is only true because mandatory skills are now enforced.
HINTS = {
    "type": "Show only skills of this type.",
    "weight": "How much you want this skill.",
    "level_focus": (
        "How much each level after the first adds. 0: only having the skill "
        "counts. 5: each further level is worth more than the first."
    ),
    "pins": (
        "Force a slot to a specific set's piece, or fix the talisman (craftable "
        "or custom). Slots left empty are chosen freely."
    ),
    "exclude": (
        "Armour the search must never use: sets you have not unlocked, or "
        "pieces you refuse to wear."
    ),
    "exclude_dialog": (
        "Double-click, or select and press Space, to exclude or include. A set "
        "row covers every piece in it; pieces can also be excluded one by one."
    ),
    "weapon_slots": (
        "Your weapon's decoration slot sizes, 0 for none. Weapon jewels are "
        "then placed for weighted weapon skills; without slots those skills "
        "are ignored."
    ),
    "gogma": (
        "Credits one extra piece toward this bonus, standing in for the bonus "
        "point a Gogma weapon carries."
    ),
    # Not "level-1 slots": the smallest slots are the ones held back, which
    # are size 2 or 3 on a set that runs out of size-1 slots.
    "reserved": (
        "Decoration slots left empty for resistance jewels. The smallest slots "
        "are held back, so size 1 while the set has them."
    ),
    "relax": (
        "Return the full ten sets even if some miss a required skill. Off by "
        "default: a short list that meets your requirements beats a full one "
        "that quietly does not."
    ),
    "output": (
        "Filename for Save. Written beside the skills file you loaded, not "
        "into skills_outputs/."
    ),
    "profile": (
        "Open reads a weighted skills file. A profile instead holds the "
        "weights and every setting on the Skill Weights tab - pins, "
        "exclusions, weapon slots, Gogma, reserve and relax - plus the custom "
        "talismans loaded for the optimiser, in one file, applied to the "
        "current skill data."
    ),
    "talismans": (
        "Talismans from a file, added to the optimiser's pool. Separate from "
        "the file being edited above. craftable_talismans.yaml is never "
        "modified."
    ),
    "filters": (
        "Every option starts on Include. Tick Exclude to keep that whole "
        "category out of the search; the count below says how much is left "
        "out. Pins on a filtered piece are refused rather than overridden."
    ),
    "filter_armour": "Armour sets by variant, rank and rarity.",
    "filter_pieces": (
        "Pieces by what they offer. Defence is the piece's maximum, after "
        "the transcending choice below."
    ),
    "filter_talismans": (
        "Craftable talismans by the tier their name ends in: III is every "
        "'... Charm III'. Custom talismans are never filtered."
    ),
    "filter_resistances": (
        "Exclude armour whose resistance to an element compares this way, "
        "e.g. fire < 0 drops every set weak to fire."
    ),
    "transcendence": (
        "On: rarity 5 and 6 armour counts with its transcended slots and "
        "defence. Off: as it is before transcending, for armour you have "
        "not upgraded."
    ),
    "ct_name": "Shown in results. Any name that is not already in the file.",
    "ct_rarity": "Cosmetic here - the optimiser does not read it.",
    "ct_skills": "Up to three armour skills and the level each is granted at.",
    "ct_armour_slots": "Decoration slot sizes on the talisman. 0 means no slot.",
    "ct_weapon_slots": (
        "Weapon-jewel slot sizes, filled alongside the weapon's own slots when "
        "this talisman is chosen."
    ),
}

# Weight is signed because a negative weight actively avoids a skill. Level
# weight is not: Scoring clamps it to 0-1 before use, so a negative value is a
# silent no-op, and the free-text box it replaces let you type one.
WEIGHT_CHOICES = [str(v) for v in range(-1, 6)]
LEVEL_WEIGHT_CHOICES = [str(v) for v in range(0, 6)]

# How the operators read on screen; profiles and the CLI store the ASCII.
OPERATOR_LABELS = {"<": "<", "<=": "\u2264", "=": "=", ">=": "\u2265", ">": ">"}
TIER_NUMERALS = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
INCLUDE, EXCLUDE = "include", "exclude"

WEIGHT_SCALE = (
    ("-1", "Avoid"),
    ("0", "Ignore"),
    ("1-4", "Prefer, increasingly"),
    ("5", "Require - no set without it is shown"),
)
# Deliberately not phrased as a minimum level, because it does not set one.
# It splits a skill's value between having it and levelling it; only the 5/5
# corner, where mandatory-max applies, forces a level at all.
LEVEL_FOCUS_SCALE = (
    ("0", "Level 1 is worth as much as max"),
    ("1-4", "Higher levels worth increasingly more"),
    ("5", "Only max level is worth full value"),
)

DESC_WRAP = 520  # px; the description column's wrap width, and its own measure
LEVEL_GAP = 2  # px between one level's effect and the next

# Field labels stay on screen with nothing beside them when no skill is chosen,
# so the section keeps its shape and reads as waiting rather than broken. Each
# value's column is widened to the longest value in the loaded file, which keeps
# the labels after it from sliding sideways as you click down the list.
INFO_FIELDS = (
    ("Type:", "type"),
    ("Max Level:", "max_level"),
    ("Scaling:", "scaling"),
)


def _hint(parent: tk.Widget, key: str, wrap: int = HINT_WRAP) -> ttk.Label:
    label = ttk.Label(
        parent,
        text=HINTS[key],
        style="Hint.TLabel",
        wraplength=wrap,
        justify=tk.LEFT,
    )
    return label


def _scale_table(parent: tk.Widget, rows) -> ttk.Frame:
    """A dial's values as a two-column key: value on the left, meaning right."""
    table = ttk.Frame(parent)
    for index, (value, meaning) in enumerate(rows):
        ttk.Label(
            table, text=value, style="Hint.TLabel", width=4, anchor=tk.E
        ).grid(row=index, column=0, sticky=tk.E, padx=(0, 8))
        ttk.Label(table, text=meaning, style="Hint.TLabel").grid(
            row=index, column=1, sticky=tk.W
        )
    return table


def _weight_text(value: float) -> str:
    """Spell a stored weight the way the dropdown spells it, so 5.0 shows as 5.

    A non-integral value from a hand-edited file is left exactly as it is: the
    dropdown displays it and offers integers alongside, so it survives untouched
    unless a new value is actually chosen.
    """
    return str(int(value)) if float(value).is_integer() else str(value)


def _slot_sizes(value, default_size: int = 1) -> list[int]:
    """Normalise a decoration_slots.armour/weapon value to a list of sizes."""
    if isinstance(value, (list, tuple)):
        return [int(s) for s in value if s]
    return [default_size] * int(value or 0)


@dataclass
class RunRequest:
    """Everything one optimiser run reads, captured on the main thread.

    The worker thread gets this and nothing else, which is what keeps it off
    Tk entirely: Tk is not thread-safe, and a worker reading a StringVar
    mid-run would be reading it from the wrong thread.
    """

    skills: list[Skill]
    reserved_slots: int = RESERVED_SLOTS
    extra_bonus_pieces: dict[str, int] = field(default_factory=dict)
    custom_talismans: list[Talisman] = field(default_factory=list)
    pinned_pieces: dict[str, str] = field(default_factory=dict)
    excluded_sets: list[str] = field(default_factory=list)
    excluded_pieces: list[str] = field(default_factory=list)
    strict: bool = True
    weapon_slots: tuple[int, ...] = ()
    filters: GearFilters = field(default_factory=GearFilters)
    # What the weights came from, for export headers: the loaded file's path,
    # marked when the run used edits not yet saved to it.
    source_label: str = ""


@dataclass
class RunResult:
    """A finished run, as the worker hands it back to the main thread."""

    sets: list[GearSet]
    scoring: Scoring
    reasons: list[str]
    constraint_level: int
    request: RunRequest


class SkillsGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MHWilds Skill Weights")


        self.game_data: GameData = load_game_data()
        self.sets_by_slot = {
            piece_type: sorted(
                {p.set for p in self.game_data.armor if p.piece_type == piece_type}
            )
            for piece_type in PIECE_TYPES
        }
        # (slot, set) identifies a piece uniquely across the whole armour data,
        # so choosing a set for a slot fully determines which piece is pinned
        # and no second dropdown is needed.
        self.piece_by_slot_set = {
            (p.piece_type, p.set): p for p in self.game_data.armor
        }
        self.set_of_piece = {p.name: p.set for p in self.game_data.armor}
        # Set bonus and group tiers are named in both the armour data and the
        # skills file ("Black Eclipse II"), which is how a tier finds out how
        # many pieces it needs without the skills file repeating it.
        self._pieces_by_tier = {
            effect.skill: effect.pieces_required
            for piece in self.game_data.armor
            for bonus in piece.set_bonuses
            for effect in bonus.effects
        }
        # The Gogma selectors offer only bonuses some armour piece carries. The
        # optimiser learns a bonus's piece thresholds from the armour data, so
        # a bonus no armour carries (Soul of the Dark Knight) has none, and a
        # weapon point credited to it would silently do nothing.
        self._armour_bonus_names = {
            bonus_base_name(bonus.name)
            for piece in self.game_data.armor
            for bonus in piece.set_bonuses
        }
        # game_data.skills is always skills_default.yaml, whatever file the
        # weighting tab has open; see _levels_for.
        self._default_levels = {
            s.name: s.levels for s in self.game_data.skills if s.levels
        }

        self.current_path: Path = Path(SKILLS_PATH)
        self.skills: list[Skill] = []
        self.skills_by_name: dict[str, Skill] = {}
        self.cache: dict[str, dict[str, str]] = {}
        self.output_dir: Path = self.current_path.parent

        self.selected_name: str | None = None
        self._suppress_trace = False

        state = self._read_state()
        self.dark_var = tk.BooleanVar(value=bool(state.get("dark", False)))
        # On unless switched off: the check is one request to GitHub's API
        # per launch, and the box beside Dark Mode turns it off for good.
        self.check_updates_var = tk.BooleanVar(
            value=bool(state.get("check_updates", True))
        )
        self.update_status: update_check.UpdateStatus | None = None
        # Captured before any theme switch, so light mode can always get back to
        # whatever Tk chose for this platform rather than to a name hard-coded
        # here - "vista" does not exist on Linux.
        self._native_ttk_theme = ttk.Style(root).theme_use()

        # Optimiser integration state.
        self._optimiser_running = False
        self.gear_sets: list[GearSet] = []
        self.gear_result: RunResult | None = None
        self.gear_scoring: Scoring | None = None
        self.gear_set_index = 0
        self.results_window: tk.Toplevel | None = None

        # Custom talismans loaded (via the main tab) for the optimiser to use,
        # kept separate from craftable_talismans.yaml entirely.
        self.custom_talismans_loaded: list[Talisman] = []
        self.custom_talismans_source: Path | None = None

        # Armour left out of the search. Sets and pieces are kept apart, as the
        # optimiser takes them, so excluding a set and later including it again
        # does not lose pieces excluded one by one inside it.
        self.excluded_sets: set[str] = set()
        self.excluded_pieces: set[str] = set()
        self.exclusions_window: tk.Toplevel | None = None

        # Custom Talismans tab editing state.
        self.custom_talisman_path: Path | None = None
        self.custom_talismans: list[Talisman] = []
        self.ct_selected_index: int | None = None

        self._build_widgets()
        self._load_file(self.current_path)
        # Theme before sizing: clam's padding and font metrics differ from
        # vista's, so the requested size is only meaningful once the widgets are
        # wearing the theme they will open in.
        self._apply_theme()
        self._apply_window_size()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.check_updates_var.get():
            self._start_update_check()

    def _apply_window_size(self) -> None:
        """Open at the smallest size the widgets fit, which is also the minimum.

        No hard-coded size: character widths, theme padding and the desktop's
        scaling only exist at runtime, and two earlier fixed sizes clipped the
        Gogma selectors. Tk's requested size is what the widgets actually need,
        capped to the screen. Nothing about the size is read from or written to
        gui_state.json, so every launch opens the same.

        update_idletasks forces the pending layout pass first; before it, a
        window reports a requested size of 1x1.
        """
        self.root.update_idletasks()
        width = min(self.root.winfo_reqwidth(), self.root.winfo_screenwidth())
        height = min(
            self.root.winfo_reqheight(),
            self.root.winfo_screenheight() - SCREEN_MARGIN,
        )
        self.root.minsize(width, height)
        self.root.geometry(f"{width}x{height}")

    @staticmethod
    def _read_state() -> dict:
        """Remembered settings, or {} if there is nothing usable.

        An older file may still carry width and height; they are ignored.

        A missing file is the normal first run and a corrupt one is someone's
        stray edit; both fall back to the computed defaults, so neither is worth
        a dialog before the window is even up.
        """
        try:
            state = json.loads(GUI_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _write_state(self) -> None:
        try:
            GUI_STATE_PATH.write_text(
                json.dumps(
                    {
                        "dark": bool(self.dark_var.get()),
                        "check_updates": bool(self.check_updates_var.get()),
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # a read-only checkout must still be able to quit

    def _on_close(self) -> None:
        if not self._discard_changes_ok():
            return
        # The worker is a daemon thread and dies with the process anyway;
        # asking it to stop first just saves it finishing work nobody reads.
        self._cancel_optimiser()
        self._write_state()
        self.root.destroy()

    # --- layout ------------------------------------------------------------

    # --- theming -----------------------------------------------------------

    def _apply_theme(self) -> None:
        """Repaint everything for the current mode.

        ttk styles are global and live, so switching is a restyle rather than a
        rebuild - but only for ttk widgets. Listboxes and the results Text are
        classic Tk, drawn from their own options, so they are recoloured by hand
        below; miss one and it stays a white rectangle in a dark window.
        """
        palette = THEMES["dark" if self.dark_var.get() else "light"]
        style = ttk.Style(self.root)
        style.theme_use(palette["ttk_theme"] or self._native_ttk_theme)
        if palette["ttk_theme"]:
            self._style_dark(style, palette)

        style.configure("Hint.TLabel", foreground=palette["hint"])
        style.configure("Status.TLabel", foreground=palette["status"])
        style.configure("Update.TLabel", foreground=palette["update"])
        if palette["window"]:
            self.root.configure(background=palette["window"])
            style.configure("Hint.TLabel", background=palette["window"])
            style.configure("Status.TLabel", background=palette["window"])
            style.configure("Update.TLabel", background=palette["window"])

        for listbox in (self.listbox, self.ct_listbox):
            listbox.configure(
                background=palette["text_bg"],
                foreground=palette["text_fg"],
                selectbackground=palette["select_bg"],
                selectforeground=palette["select_fg"],
            )
        self._theme_results_window()
        self._theme_exclusions_window()

        # A combobox builds its drop-down list the first time it is opened, from
        # the option database rather than from the style, so these have to be
        # set before that happens - which is why they are refreshed on every
        # theme change rather than once at startup.
        for option, value in (
            ("*TCombobox*Listbox.background", palette["text_bg"]),
            ("*TCombobox*Listbox.foreground", palette["text_fg"]),
            ("*TCombobox*Listbox.selectBackground", palette["select_bg"]),
            ("*TCombobox*Listbox.selectForeground", palette["select_fg"]),
        ):
            self.root.option_add(option, value)

        self._set_dark_title_bar(bool(palette["ttk_theme"]))
        # clam and the native theme pad labels differently, so the height held
        # for descriptions is re-measured in the theme now in force.
        if self.skills:
            self._size_description_box()

    def _theme_results_window(self) -> None:
        """Colour the results Toplevel, if it is open.

        Its Canvas is classic Tk, so no ttk restyle reaches it - the set is
        redrawn in the new colours instead - and the Toplevel carries its own
        background rather than inheriting the root's.
        """
        palette = THEMES["dark" if self.dark_var.get() else "light"]
        window = getattr(self, "results_window", None)
        if window is None or not window.winfo_exists():
            return
        if palette["window"]:
            window.configure(background=palette["window"])
        else:
            window.configure(background=self.root.cget("background"))
        if self.gear_sets:
            self._render_current_set()

    @staticmethod
    def _style_dark(style: ttk.Style, palette: dict) -> None:
        """Colour clam's elements. Only reached in dark mode."""
        window, surface = palette["window"], palette["surface"]
        text, border = palette["text"], palette["border"]

        style.configure(
            ".",
            background=window,
            foreground=text,
            fieldbackground=surface,
            bordercolor=border,
            # clam draws its 3D relief from these two; matching them to the
            # background is what flattens the bevels instead of leaving pale
            # highlights around every widget.
            lightcolor=window,
            darkcolor=window,
            troughcolor=surface,
            arrowcolor=text,
            insertcolor=text,
        )
        style.map(".", foreground=[("disabled", palette["disabled"])])

        style.configure("TLabelframe", background=window, bordercolor=border)
        style.configure("TLabelframe.Label", background=window, foreground=text)
        style.configure("TButton", background=surface, foreground=text)
        style.map(
            "TButton",
            background=[("pressed", border), ("active", palette["hover"])],
        )
        style.configure(
            "TCombobox", fieldbackground=surface, background=surface, foreground=text
        )
        # readonly is its own state for a combobox: without this map, every
        # dropdown in this GUI stays light, because they are all readonly.
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", surface)],
            background=[("readonly", surface)],
            foreground=[("readonly", text)],
        )
        style.configure("TEntry", fieldbackground=surface, foreground=text)
        style.configure(
            "TSpinbox", fieldbackground=surface, background=surface, foreground=text
        )
        style.configure("TCheckbutton", background=window, foreground=text)
        style.map(
            "TCheckbutton",
            background=[("active", window)],
            indicatorcolor=[("selected", palette["select_bg"])],
        )
        style.configure("TNotebook", background=window, bordercolor=border)
        style.configure("TNotebook.Tab", background=surface, foreground=text)
        style.map("TNotebook.Tab", background=[("selected", window)])
        style.configure(
            "TScrollbar", background=surface, troughcolor=window, bordercolor=border
        )
        style.configure("TSeparator", background=border)
        # The Exclude Gear tree. Unlike the Listboxes, Treeview is ttk, so it
        # follows the style - but clam's default Treeview is white, so it
        # needs colours of its own like every other ttk widget here.
        style.configure(
            "Treeview",
            background=palette["text_bg"],
            fieldbackground=palette["text_bg"],
            foreground=palette["text_fg"],
            bordercolor=border,
        )
        style.map(
            "Treeview",
            background=[("selected", palette["select_bg"])],
            foreground=[("selected", palette["select_fg"])],
        )
        style.configure("Treeview.Heading", background=surface, foreground=text)

    def _set_dark_title_bar(self, dark: bool) -> None:
        """Ask Windows for a dark title bar. Cosmetic, so failure is ignored.

        The frame stays light otherwise, which looks worse than no dark mode at
        all. Attribute 20 is DWMWA_USE_IMMERSIVE_DARK_MODE on Windows 10 1903
        and later; 19 was its number before that, so both are tried. Anything
        else - a different OS, an older build, a missing dwmapi - simply leaves
        the title bar alone.
        """
        try:
            import ctypes

            self.root.update_idletasks()
            handle = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(int(dark))
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    handle, attribute, ctypes.byref(value), ctypes.sizeof(value)
                ) == 0:
                    break
            # The frame only repaints on a visibility change.
            self.root.withdraw()
            self.root.deiconify()
        except Exception:  # noqa: BLE001
            pass

    def _on_theme_toggle(self) -> None:
        self._apply_theme()
        self._write_state()

    # --- update check ----------------------------------------------------------

    def _on_update_toggle(self) -> None:
        self._write_state()
        if self.check_updates_var.get() and self.update_status is None:
            self._start_update_check()

    def _start_update_check(self) -> None:
        """Ask GitHub on a worker thread; the answer is picked up by polling.

        The same split as the optimiser run: the worker only writes to a
        queue and never touches Tk, which is not thread-safe.
        """
        results: queue.Queue = queue.Queue()

        def worker() -> None:
            try:
                results.put(update_check.check())
            except Exception:  # noqa: BLE001 - a failed check is no answer
                results.put(None)

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(UPDATE_POLL_MS, self._poll_update_check, results)

    def _poll_update_check(self, results: queue.Queue) -> None:
        try:
            status = results.get_nowait()
        except queue.Empty:
            self.root.after(UPDATE_POLL_MS, self._poll_update_check, results)
            return
        self._show_update_status(status)

    def _show_update_status(self, status) -> None:
        """Show the banner if this copy is behind; say nothing otherwise."""
        self.update_status = status
        if status is None or not status.out_of_date:
            return
        self.update_text_var.set(status.message())
        self.update_banner.pack(side=tk.TOP, fill=tk.X, before=self.notebook)
        # The window was sized before the banner existed; without a resize
        # the banner's height comes off the bottom of the tab instead.
        self._apply_window_size()

    def _dismiss_update_banner(self) -> None:
        self.update_banner.pack_forget()

    def _build_widgets(self) -> None:
        chrome = ttk.Frame(self.root, padding=(8, 4, 8, 0))
        chrome.pack(side=tk.TOP, fill=tk.X)
        # Outside the notebook because it applies to the whole application
        # rather than to any one tab's contents.
        ttk.Checkbutton(
            chrome,
            text="Dark Mode",
            variable=self.dark_var,
            command=self._on_theme_toggle,
        ).pack(side=tk.RIGHT)
        ttk.Checkbutton(
            chrome,
            text="Check for Updates",
            variable=self.check_updates_var,
            command=self._on_update_toggle,
        ).pack(side=tk.RIGHT, padx=(0, 12))

        # Built now, shown only once the check finds this copy out of date,
        # so an up-to-date or offline start looks exactly as it did before.
        self.update_banner = ttk.Frame(self.root, padding=(8, 4, 8, 0))
        self.update_text_var = tk.StringVar(value="")
        ttk.Label(
            self.update_banner,
            textvariable=self.update_text_var,
            style="Update.TLabel",
            wraplength=900,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(
            self.update_banner, text="Dismiss", command=self._dismiss_update_banner
        ).pack(side=tk.RIGHT, padx=(8, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        skills_tab = ttk.Frame(self.notebook)
        filters_tab = ttk.Frame(self.notebook)
        files_tab = ttk.Frame(self.notebook)
        talismans_tab = ttk.Frame(self.notebook)
        self.notebook.add(skills_tab, text="Skill Weights")
        self.notebook.add(filters_tab, text="Filters")
        self.notebook.add(files_tab, text="Skills File")
        self.notebook.add(talismans_tab, text="Custom Talismans")

        self._build_skills_tab(skills_tab)
        self._build_filters_tab(filters_tab)
        self._build_skills_file_tab(files_tab)
        self._build_custom_talismans_tab(talismans_tab)

    def _build_skills_tab(self, parent: ttk.Frame) -> None:
        """Run and Save Weights along the bottom, the editing panels above.

        Each section is laid out the same way: hint, then controls. Order
        matters to pack(). The two BOTTOM sections are packed before the body
        so they claim their height first, and 'run' before 'save' so the
        primary action sits at the very bottom edge.
        """
        run = self._section(parent, "Run", side=tk.BOTTOM, fill=tk.X)
        options = ttk.Frame(run)
        options.pack(side=tk.TOP, fill=tk.X)

        reserved_group = ttk.Frame(options)
        reserved_group.pack(side=tk.LEFT, anchor=tk.N)
        _hint(reserved_group, "reserved", wrap=260).pack(side=tk.TOP, anchor=tk.W)
        reserved_row = ttk.Frame(reserved_group)
        reserved_row.pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))
        ttk.Label(reserved_row, text="Reserved Slots:").pack(side=tk.LEFT)
        self.reserved_slots_var = tk.StringVar(value=str(RESERVED_SLOTS))
        ttk.Spinbox(
            reserved_row,
            from_=0,
            to=10,
            textvariable=self.reserved_slots_var,
            width=3,
            justify=tk.CENTER,
        ).pack(side=tk.LEFT, padx=(8, 0))

        relax_group = ttk.Frame(options)
        relax_group.pack(side=tk.LEFT, anchor=tk.N, padx=(24, 0))
        _hint(relax_group, "relax", wrap=420).pack(side=tk.TOP, anchor=tk.W)
        self.relax_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            relax_group,
            text="Allow sets missing a required skill",
            variable=self.relax_var,
        ).pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))

        self.run_button = ttk.Button(
            options, text="Run Optimiser", command=self._run_optimiser
        )
        self.run_button.pack(side=tk.RIGHT, anchor=tk.N)
        self.status_var = tk.StringVar(value="")
        ttk.Label(options, textvariable=self.status_var, style="Status.TLabel").pack(
            side=tk.RIGHT, anchor=tk.N, padx=(12, 12)
        )

        # Its own row under the options, so a long progress message never
        # pushes the Run button around.
        progress_row = ttk.Frame(run)
        progress_row.pack(side=tk.TOP, fill=tk.X, pady=(GAP, 0))
        self.cancel_button = ttk.Button(
            progress_row, text="Cancel", command=self._cancel_optimiser, state=tk.DISABLED
        )
        self.cancel_button.pack(side=tk.RIGHT)
        self.progress_var = tk.DoubleVar(value=0.0)
        ttk.Progressbar(
            progress_row, variable=self.progress_var, maximum=1.0, mode="determinate"
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(0, 8))
        self.progress_text_var = tk.StringVar(value="")
        ttk.Label(
            progress_row, textvariable=self.progress_text_var, style="Hint.TLabel", width=48
        ).pack(side=tk.LEFT)

        save = self._section(parent, "Save Weights", side=tk.BOTTOM, fill=tk.X)
        _hint(save, "output", wrap=900).pack(side=tk.TOP, anchor=tk.W)
        save_row = ttk.Frame(save)
        save_row.pack(side=tk.TOP, fill=tk.X, pady=(GAP, 0))
        ttk.Label(save_row, text="Output File:").pack(side=tk.LEFT)
        self.output_name_var = tk.StringVar(value="skills_weighted.yaml")
        ttk.Entry(save_row, textvariable=self.output_name_var, width=28).pack(
            side=tk.LEFT, padx=(4, 4)
        )
        ttk.Button(save_row, text="Browse...", command=self._browse_save).pack(
            side=tk.LEFT
        )
        ttk.Button(save_row, text="Save", command=self._save).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        body = ttk.Frame(parent)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)

        self._build_gear_section(body)
        self._build_skill_list_section(body)
        self._build_weighting_section(body)

    def _build_skills_file_tab(self, parent: ttk.Frame) -> None:
        """Which skills file is being weighted, and whole-goal profiles."""
        files = self._section(parent, "Skills File", side=tk.TOP, fill=tk.X)
        _hint(files, "profile", wrap=900).pack(side=tk.TOP, anchor=tk.W)
        file_row = ttk.Frame(files)
        file_row.pack(side=tk.TOP, fill=tk.X, pady=(GAP, 0))
        ttk.Button(file_row, text="Open...", command=self._open_file).pack(side=tk.LEFT)
        ttk.Button(file_row, text="Load Profile...", command=self._load_profile).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(file_row, text="Save Profile...", command=self._save_profile).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        self.file_label_var = tk.StringVar(value="")
        ttk.Label(
            file_row, textvariable=self.file_label_var, style="Hint.TLabel"
        ).pack(side=tk.LEFT, padx=(8, 0))

    # --- filters tab -----------------------------------------------------------

    def _build_filters_tab(self, parent: ttk.Frame) -> None:
        """Include/Exclude pairs per category, plus transcending.

        Each row's two boxes share one StringVar, each box writing its own
        value when ticked and the other's when unticked, so exactly one of
        the pair is always ticked without any handler keeping them in step.
        """
        self.filter_vars: dict[str, tk.StringVar] = {}

        top = self._section(parent, "Filters", side=tk.TOP, fill=tk.X)
        _hint(top, "filters", wrap=900).pack(side=tk.TOP, anchor=tk.W)
        summary_row = ttk.Frame(top)
        summary_row.pack(side=tk.TOP, fill=tk.X, pady=(GAP, 0))
        self.filter_summary_var = tk.StringVar(value="")
        ttk.Label(
            summary_row, textvariable=self.filter_summary_var, style="Status.TLabel"
        ).pack(side=tk.LEFT)
        ttk.Button(
            summary_row, text="Include Everything", command=self._reset_filters
        ).pack(side=tk.RIGHT)

        body = ttk.Frame(parent)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)
        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, anchor=tk.N, fill=tk.Y)
        middle = ttk.Frame(body)
        middle.pack(side=tk.LEFT, anchor=tk.N, fill=tk.Y, padx=(8, 0))
        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, anchor=tk.N, fill=tk.BOTH, expand=True, padx=(8, 0))

        armour = ttk.LabelFrame(left, text="Armour Sets", padding=SECTION_PAD)
        armour.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
        _hint(armour, "filter_armour", wrap=280).pack(side=tk.TOP, anchor=tk.W)
        grid = self._filter_grid(armour)
        for letter in VARIANTS:
            self._filter_row(grid, f"variant:{letter}", f"{VARIANT_NAMES[letter]} ({letter}) sets")
        self._filter_row(grid, "rank:high", "High Rank armour")
        self._filter_row(grid, "rank:low", "Low Rank armour")
        for rarity in RARITIES:
            self._filter_row(grid, f"rarity:{rarity}", f"Rarity {rarity}")

        pieces = ttk.LabelFrame(middle, text="Armour Pieces", padding=SECTION_PAD)
        pieces.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
        _hint(pieces, "filter_pieces", wrap=300).pack(side=tk.TOP, anchor=tk.W)
        grid = self._filter_grid(pieces)
        self._filter_row(grid, "slotless", "Pieces with no decoration slots")
        row = self._filter_row(grid, "mindef", "Pieces with defence below")
        self.min_defense_var = tk.StringVar(value="70")
        ttk.Spinbox(
            grid, from_=0, to=200, textvariable=self.min_defense_var, width=5,
            justify=tk.CENTER, command=self._refresh_filter_summary,
        ).grid(row=row, column=3, sticky=tk.W, padx=(6, 0))
        self.min_defense_var.trace_add("write", lambda *_a: self._refresh_filter_summary())

        transcend = ttk.LabelFrame(middle, text="Transcendence", padding=SECTION_PAD)
        transcend.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
        _hint(transcend, "transcendence", wrap=300).pack(side=tk.TOP, anchor=tk.W)
        self.transcendence_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            transcend, text="Enable transcendence", variable=self.transcendence_var,
            command=self._refresh_filter_summary,
        ).pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))

        talismans = ttk.LabelFrame(middle, text="Talismans", padding=SECTION_PAD)
        talismans.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
        _hint(talismans, "filter_talismans", wrap=300).pack(side=tk.TOP, anchor=tk.W)
        grid = self._filter_grid(talismans)
        for tier in TALISMAN_TIERS:
            self._filter_row(grid, f"tier:{tier}", f"Tier {TIER_NUMERALS[tier]} talismans")

        resist = ttk.LabelFrame(right, text="Resistances", padding=SECTION_PAD)
        resist.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
        _hint(resist, "filter_resistances", wrap=320).pack(side=tk.TOP, anchor=tk.W)
        grid = self._filter_grid(resist)
        self.resistance_op_vars: dict[str, tk.StringVar] = {}
        self.resistance_value_vars: dict[str, tk.StringVar] = {}
        for element in ELEMENTS:
            row = self._filter_row(grid, f"res:{element}", f"{element.capitalize()} resistance")
            op_var = tk.StringVar(value=OPERATOR_LABELS["<"])
            ttk.Combobox(
                grid, textvariable=op_var, values=list(OPERATOR_LABELS.values()),
                state="readonly", width=3,
            ).grid(row=row, column=3, sticky=tk.W, padx=(6, 0))
            value_var = tk.StringVar(value="0")
            ttk.Spinbox(
                grid, from_=-10, to=10, textvariable=value_var, width=4,
                justify=tk.CENTER, command=self._refresh_filter_summary,
            ).grid(row=row, column=4, sticky=tk.W, padx=(4, 0))
            for var in (op_var, value_var):
                var.trace_add("write", lambda *_a: self._refresh_filter_summary())
            self.resistance_op_vars[element] = op_var
            self.resistance_value_vars[element] = value_var

        self._refresh_filter_summary()

    def _filter_grid(self, parent: tk.Widget) -> ttk.Frame:
        """A grid with Include / Exclude column headings above the boxes."""
        grid = ttk.Frame(parent)
        grid.pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))
        ttk.Label(grid, text="Include", style="Hint.TLabel").grid(row=0, column=1, padx=4)
        ttk.Label(grid, text="Exclude", style="Hint.TLabel").grid(row=0, column=2, padx=4)
        return grid

    def _filter_row(self, grid: ttk.Frame, key: str, label: str) -> int:
        """One category: its label, then the Include and Exclude boxes."""
        row = grid.grid_size()[1]
        var = tk.StringVar(value=INCLUDE)
        ttk.Label(grid, text=label).grid(row=row, column=0, sticky=tk.W, pady=1)
        for column, (on, off) in enumerate(((INCLUDE, EXCLUDE), (EXCLUDE, INCLUDE)), start=1):
            ttk.Checkbutton(
                grid, variable=var, onvalue=on, offvalue=off,
                command=self._refresh_filter_summary,
            ).grid(row=row, column=column)
        self.filter_vars[key] = var
        return row

    def _excluded(self, key: str) -> bool:
        return self.filter_vars[key].get() == EXCLUDE

    def _current_filters(self) -> GearFilters:
        """The tab as a GearFilters. A value box that does not parse counts
        as that row's default, and _filter_input_problems names it."""
        rules = []
        labels = {v: k for k, v in OPERATOR_LABELS.items()}
        for element in ELEMENTS:
            if not self._excluded(f"res:{element}"):
                continue
            try:
                value = int(self.resistance_value_vars[element].get())
            except ValueError:
                continue
            op = labels.get(self.resistance_op_vars[element].get(), "<")
            rules.append(ResistanceRule(element, op, value))
        min_defense = 0
        if self._excluded("mindef"):
            try:
                min_defense = max(0, int(self.min_defense_var.get()))
            except ValueError:
                pass
        return GearFilters(
            exclude_variants={v for v in VARIANTS if self._excluded(f"variant:{v}")},
            exclude_ranks={r for r in ("high", "low") if self._excluded(f"rank:{r}")},
            exclude_rarities={r for r in RARITIES if self._excluded(f"rarity:{r}")},
            exclude_talisman_tiers={t for t in TALISMAN_TIERS if self._excluded(f"tier:{t}")},
            transcendence=bool(self.transcendence_var.get()),
            resistance_rules=rules,
            min_defense=min_defense,
            exclude_slotless=self._excluded("slotless"),
        )

    def _filter_input_problems(self) -> list[str]:
        """Ticked rows whose number box does not hold a whole number."""
        problems = []
        if self._excluded("mindef"):
            try:
                int(self.min_defense_var.get())
            except ValueError:
                problems.append("The minimum defence must be a whole number.")
        for element in ELEMENTS:
            if self._excluded(f"res:{element}"):
                try:
                    int(self.resistance_value_vars[element].get())
                except ValueError:
                    problems.append(f"The {element} resistance value must be a whole number.")
        return problems

    def _set_filters(self, filters: GearFilters) -> None:
        """Put a profile's filters on the tab."""
        def put(key: str, excluded: bool) -> None:
            self.filter_vars[key].set(EXCLUDE if excluded else INCLUDE)

        for v in VARIANTS:
            put(f"variant:{v}", v in filters.exclude_variants)
        for r in ("high", "low"):
            put(f"rank:{r}", r in filters.exclude_ranks)
        for r in RARITIES:
            put(f"rarity:{r}", r in filters.exclude_rarities)
        for t in TALISMAN_TIERS:
            put(f"tier:{t}", t in filters.exclude_talisman_tiers)
        put("slotless", filters.exclude_slotless)
        put("mindef", filters.min_defense > 0)
        if filters.min_defense:
            self.min_defense_var.set(str(filters.min_defense))
        self.transcendence_var.set(filters.transcendence)
        # One rule per element on this tab; a hand-edited profile with two
        # for one element shows the last, and the summary says so.
        by_element = {rule.element: rule for rule in filters.resistance_rules}
        for element in ELEMENTS:
            rule = by_element.get(element)
            put(f"res:{element}", rule is not None)
            if rule is not None:
                self.resistance_op_vars[element].set(OPERATOR_LABELS[rule.op])
                self.resistance_value_vars[element].set(str(rule.value))
        self._refresh_filter_summary()

    def _reset_filters(self) -> None:
        self._set_filters(GearFilters())

    def _refresh_filter_summary(self) -> None:
        """Count what the filters leave out, so a filter's reach is visible
        before running rather than discovered as an empty result."""
        if not hasattr(self, "filter_summary_var"):
            return  # a trace firing while the tab is still being built
        filters = self._current_filters()
        if filters.is_default():
            self.filter_summary_var.set("Nothing filtered out.")
            return
        result = apply_filters(self.game_data, filters)
        dropped = len(self.game_data.talismans) - len(result.game.talismans)
        parts = [f"{len(result.removed)} of {len(self.game_data.armor)} armour pieces"]
        if dropped:
            parts.append(f"{dropped} of {len(self.game_data.talismans)} talismans")
        text = "Filtered out: " + " and ".join(parts) + "."
        if not filters.transcendence:
            text += " Transcending off."
        self.filter_summary_var.set(text)

    def _section(self, parent: tk.Widget, title: str, **pack_options) -> ttk.LabelFrame:
        """One titled box, padded the same as every other box."""
        frame = ttk.LabelFrame(parent, text=title, padding=SECTION_PAD)
        frame.pack(padx=8, pady=(6, 0), **pack_options)
        return frame

    def _build_gear_section(self, body: ttk.Frame) -> None:
        """Pinned armour and the Gogma bonus point, as one group.

        Both answer the same question - which gear is fixed before the search
        starts - and the Gogma selectors are in effect a sixth piece's worth of
        set bonus, so they belong together rather than in separate places.

        Packed first, so it is the leftmost column and the skill list sits
        against it. Anchored north rather than filling, so the box ends where
        its controls do instead of bordering a column of empty space.
        """
        gear = ttk.LabelFrame(body, text="Fixed Gear", padding=SECTION_PAD)
        gear.pack(side=tk.LEFT, anchor=tk.N, pady=(6, 0))

        _hint(gear, "pins", wrap=280).pack(side=tk.TOP, anchor=tk.W)
        pins = ttk.Frame(gear)
        pins.pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))

        self.pin_vars: dict[str, tk.StringVar] = {}
        self.pin_detail_labels: dict[str, ttk.Label] = {}
        for index, piece_type in enumerate(PIECE_TYPES):
            ttk.Label(pins, text=f"{piece_type.capitalize()}:", width=7).grid(
                row=index * 2, column=0, sticky=tk.W, pady=(2, 0)
            )
            var = tk.StringVar(value=NONE_OPTION)
            combo = ttk.Combobox(
                pins,
                textvariable=var,
                values=[NONE_OPTION] + self.sets_by_slot[piece_type],
                state="readonly",
                width=24,
            )
            combo.grid(row=index * 2, column=1, sticky=tk.W, padx=(4, 0), pady=(2, 0))
            detail = ttk.Label(pins, text="", style="Hint.TLabel", wraplength=280)
            detail.grid(row=index * 2 + 1, column=0, columnspan=2, sticky=tk.W)
            # Hold the row open whether or not a pin is set, so choosing one
            # doesn't grow the panel and push the Gogma selectors off the bottom.
            pins.grid_rowconfigure(index * 2 + 1, minsize=PIN_DETAIL_HEIGHT)
            var.trace_add(
                "write", lambda *_a, slot=piece_type: self._on_pin_change(slot)
            )
            self.pin_vars[piece_type] = var
            self.pin_detail_labels[piece_type] = detail

        # The talisman is the sixth row: same layout, but chosen by name from
        # the craftable list plus whatever custom file is loaded, which is why
        # its options are refreshed whenever that file changes.
        row = len(PIECE_TYPES) * 2
        ttk.Label(pins, text="Talisman:", width=8).grid(
            row=row, column=0, sticky=tk.W, pady=(2, 0)
        )
        var = tk.StringVar(value=NONE_OPTION)
        self.talisman_pin_combo = ttk.Combobox(
            pins, textvariable=var, state="readonly", width=24
        )
        self.talisman_pin_combo.grid(
            row=row, column=1, sticky=tk.W, padx=(4, 0), pady=(2, 0)
        )
        detail = ttk.Label(pins, text="", style="Hint.TLabel", wraplength=280)
        detail.grid(row=row + 1, column=0, columnspan=2, sticky=tk.W)
        pins.grid_rowconfigure(row + 1, minsize=PIN_DETAIL_HEIGHT)
        var.trace_add("write", lambda *_a: self._on_pin_change(TALISMAN_SLOT))
        self.pin_vars[TALISMAN_SLOT] = var
        self.pin_detail_labels[TALISMAN_SLOT] = detail
        self._refresh_talisman_pin_options()

        ttk.Separator(gear, orient=tk.HORIZONTAL).pack(
            side=tk.TOP, fill=tk.X, pady=(10, 6)
        )

        _hint(gear, "exclude", wrap=280).pack(side=tk.TOP, anchor=tk.W)
        exclude_row = ttk.Frame(gear)
        exclude_row.pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))
        ttk.Button(
            exclude_row, text="Exclude Gear...", command=self._open_exclusions
        ).pack(side=tk.LEFT)
        self.exclusion_summary_var = tk.StringVar(value=self._exclusion_summary())
        ttk.Label(
            exclude_row, textvariable=self.exclusion_summary_var, style="Hint.TLabel"
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(gear, orient=tk.HORIZONTAL).pack(
            side=tk.TOP, fill=tk.X, pady=(10, 6)
        )

        ttk.Label(gear, text="Weapon Slots").pack(side=tk.TOP, anchor=tk.W)
        _hint(gear, "weapon_slots", wrap=280).pack(side=tk.TOP, anchor=tk.W, pady=(2, 0))
        weapon_row = ttk.Frame(gear)
        weapon_row.pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))
        self.weapon_slot_vars: list[tk.StringVar] = []
        for i in range(MAX_WEAPON_SLOTS):
            var = tk.StringVar(value="0")
            ttk.Combobox(
                weapon_row,
                textvariable=var,
                values=["0", "1", "2", "3"],
                state="readonly",
                width=4,
            ).pack(side=tk.LEFT, padx=(0 if i == 0 else 4, 0))
            self.weapon_slot_vars.append(var)

        ttk.Separator(gear, orient=tk.HORIZONTAL).pack(
            side=tk.TOP, fill=tk.X, pady=(10, 6)
        )

        ttk.Label(gear, text="Gogma Weapon Skills").pack(side=tk.TOP, anchor=tk.W)
        _hint(gear, "gogma", wrap=280).pack(side=tk.TOP, anchor=tk.W, pady=(2, 0))
        gogma = ttk.Frame(gear)
        gogma.pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))

        ttk.Label(gogma, text="Set Bonus:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.gogma_set_var = tk.StringVar(value=NONE_OPTION)
        self.gogma_set_combo = ttk.Combobox(
            gogma,
            textvariable=self.gogma_set_var,
            values=[NONE_OPTION],
            state="readonly",
            width=22,
        )
        self.gogma_set_combo.grid(row=0, column=1, sticky=tk.W, padx=(4, 0), pady=2)

        ttk.Label(gogma, text="Group Skill:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.gogma_group_var = tk.StringVar(value=NONE_OPTION)
        self.gogma_group_combo = ttk.Combobox(
            gogma,
            textvariable=self.gogma_group_var,
            values=[NONE_OPTION],
            state="readonly",
            width=22,
        )
        self.gogma_group_combo.grid(row=1, column=1, sticky=tk.W, padx=(4, 0), pady=2)

    def _build_skill_list_section(self, body: ttk.Frame) -> None:
        """The skill list and the filter that drives it, in one box.

        The type filter used to sit in its own row above the body, where it read
        as a page-level control rather than what it is: a filter on this list.
        """
        skills = ttk.LabelFrame(body, text="Skills", padding=SECTION_PAD)
        skills.pack(side=tk.LEFT, anchor=tk.N, fill=tk.Y, padx=(8, 0), pady=(6, 0))

        _hint(skills, "type", wrap=250).pack(side=tk.TOP, anchor=tk.W)
        type_row = ttk.Frame(skills)
        type_row.pack(side=tk.TOP, anchor=tk.W, pady=(GAP, GAP))
        ttk.Label(type_row, text="Type:").pack(side=tk.LEFT)
        self.type_var = tk.StringVar(value="All")
        self.type_combo = ttk.Combobox(
            type_row, textvariable=self.type_var, values=["All"], state="readonly", width=15
        )
        self.type_combo.pack(side=tk.LEFT, padx=(4, 0))
        self.type_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_listbox())

        search_row = ttk.Frame(skills)
        search_row.pack(side=tk.TOP, anchor=tk.W, fill=tk.X, pady=(0, GAP))
        ttk.Label(search_row, text="Find:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(search_row, textvariable=self.search_var, width=22).pack(
            side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True
        )
        self.search_var.trace_add("write", lambda *_a: self._refresh_listbox())

        weighted_row = ttk.Frame(skills)
        weighted_row.pack(side=tk.TOP, anchor=tk.W, fill=tk.X, pady=(0, GAP))
        self.weighted_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            weighted_row,
            text="Weighted only",
            variable=self.weighted_only_var,
            command=self._refresh_listbox,
        ).pack(side=tk.LEFT)
        self.weighted_count_var = tk.StringVar(value="")
        ttk.Label(
            weighted_row, textvariable=self.weighted_count_var, style="Hint.TLabel"
        ).pack(side=tk.RIGHT)

        list_frame = ttk.Frame(skills)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Wide enough for the longest name plus its "  5/5" weight suffix.
        self.listbox = tk.Listbox(
            list_frame, width=36, height=LIST_ROWS, exportselection=False
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.listbox.yview
        )
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        # The names behind the rows, in order. Rows carry a weight suffix, so
        # the row text is no longer the name and cannot be looked up by it.
        self._listed_names: list[str] = []

        ttk.Button(
            skills, text="Clear All Weights...", command=self._clear_all_weights
        ).pack(side=tk.TOP, anchor=tk.W, pady=(GAP, 0))

    def _build_weighting_section(self, body: ttk.Frame) -> None:
        detail = ttk.LabelFrame(body, text="Skill Weighting", padding=SECTION_PAD)
        detail.pack(side=tk.LEFT, anchor=tk.N, fill=tk.BOTH, expand=True,
                    padx=(8, 0), pady=(6, 0))

        ttk.Label(detail, text="Skill Information:").pack(anchor=tk.W, pady=(0, 2))

        self.name_label = ttk.Label(detail, text="", font=("Segoe UI", 12, "bold"))
        self.name_label.pack(anchor=tk.W)

        self.info_frame = ttk.Frame(detail)
        self.info_frame.pack(anchor=tk.W, pady=(0, GAP))
        self.info_value_labels: dict[str, ttk.Label] = {}
        for column, (caption, field) in enumerate(INFO_FIELDS):
            ttk.Label(self.info_frame, text=caption, style="Hint.TLabel").grid(
                row=0, column=column * 2, sticky=tk.W, padx=(0 if column == 0 else 12, 4)
            )
            value = ttk.Label(self.info_frame, text="", style="Hint.TLabel")
            value.grid(row=0, column=column * 2 + 1, sticky=tk.W)
            self.info_value_labels[field] = value

        # The description and the per-level effects sit in a frame whose height
        # is fixed and whose children cannot resize it, so Adaptability's two
        # short levels occupy the same space as Defense Boost's seven and the
        # dials below never move. Without this, clicking through the list
        # shuffles the controls up and down under the pointer. The height is
        # measured from the loaded file in _size_description_box.
        ttk.Label(detail, text="Description:").pack(anchor=tk.W, pady=(0, 2))

        self.desc_holder = ttk.Frame(detail, width=DESC_WRAP)
        self.desc_holder.pack(anchor=tk.W, fill=tk.X, pady=(0, 10))
        self.desc_holder.pack_propagate(False)
        self.desc_content = ttk.Frame(self.desc_holder)
        self.desc_content.pack(anchor=tk.NW, fill=tk.X)
        self.desc_label = ttk.Label(
            self.desc_content, text="", wraplength=DESC_WRAP, justify=tk.LEFT
        )
        self.desc_label.pack(anchor=tk.NW)
        # Rebuilt per skill by _fill_levels: one row per level, the level (or
        # set pieces) in a narrow column and the game's effect text beside it.
        self.levels_frame = ttk.Frame(self.desc_content)
        self.levels_frame.pack(anchor=tk.NW, fill=tk.X, pady=(6, 0))

        # Both dials share one grid so their captions share column 0, which
        # sizes itself to the longer of them. Packed separately, each dropdown
        # started wherever its own caption ended and the two sat at different
        # offsets - "Weight:" being shorter than "Level Focus:".
        dials = ttk.Frame(detail)
        dials.pack(side=tk.TOP, anchor=tk.W, fill=tk.X)

        _hint(dials, "weight", wrap=DESC_WRAP).grid(
            row=0, column=0, columnspan=2, sticky=tk.W
        )
        _scale_table(dials, WEIGHT_SCALE).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(2, 0)
        )
        ttk.Label(dials, text="Weight:").grid(
            row=2, column=0, sticky=tk.W, pady=(GAP, GAP + 6)
        )
        self.weight_var = tk.StringVar()
        self.weight_entry = ttk.Combobox(
            dials,
            textvariable=self.weight_var,
            values=WEIGHT_CHOICES,
            state="readonly",
            width=6,
        )
        self.weight_entry.grid(
            row=2, column=1, sticky=tk.W, padx=(8, 0), pady=(GAP, GAP + 6)
        )
        self.weight_var.trace_add("write", self._on_weight_change)

        _hint(dials, "level_focus", wrap=DESC_WRAP).grid(
            row=3, column=0, columnspan=2, sticky=tk.W
        )
        _scale_table(dials, LEVEL_FOCUS_SCALE).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=(2, 0)
        )
        ttk.Label(dials, text="Level Focus:").grid(
            row=5, column=0, sticky=tk.W, pady=(GAP, 0)
        )
        self.level_weight_var = tk.StringVar()
        self.level_weight_entry = ttk.Combobox(
            dials,
            textvariable=self.level_weight_var,
            values=LEVEL_WEIGHT_CHOICES,
            state="readonly",
            width=6,
        )
        self.level_weight_entry.grid(
            row=5, column=1, sticky=tk.W, padx=(8, 0), pady=(GAP, 0)
        )
        self.level_weight_var.trace_add("write", self._on_level_weight_change)

        self.weight_entry.config(state=tk.DISABLED)
        self.level_weight_entry.config(state=tk.DISABLED)

    def _pinnable_talismans(self) -> dict[str, Talisman]:
        """Name -> talisman, custom first. On a clash the craftable one wins,
        since it comes first in the optimiser's pool and is what a pin by that
        name resolves to."""
        by_name: dict[str, Talisman] = {}
        for talisman in self.game_data.talismans:
            by_name.setdefault(talisman.name, talisman)
        custom = {}
        for talisman in self.custom_talismans_loaded:
            if talisman.name not in by_name:
                custom.setdefault(talisman.name, talisman)
        return {**custom, **by_name}

    def _refresh_talisman_pin_options(self) -> None:
        """Reload the talisman dropdown after the custom file changes.

        A pinned custom talisman whose file was just unloaded is unpinned,
        rather than left naming something the run could not find.
        """
        names = list(self._pinnable_talismans())
        self.talisman_pin_combo.config(values=[NONE_OPTION] + names)
        var = self.pin_vars[TALISMAN_SLOT]
        if var.get() not in names:
            var.set(NONE_OPTION)

    def _on_pin_change(self, piece_type: str) -> None:
        label = self.pin_detail_labels[piece_type]
        if piece_type == TALISMAN_SLOT:
            talisman = self._pinned_talisman()
            if talisman is None:
                label.config(text="")
                return
            skills = ", ".join(f"{s.name} {s.level}" for s in talisman.skills)
            armour, weapon = talisman_slot_sizes(talisman)
            slots = f"  [{','.join(map(str, armour))}]" if armour else ""
            slots += f"  W[{','.join(map(str, weapon))}]" if weapon else ""
            label.config(text=f"{skills or 'no skills'}{slots}")
            return
        piece = self._pinned_piece(piece_type)
        if piece is None:
            label.config(text="")
            return
        slots = ",".join(str(s) for s in piece.slots if s) or "-"
        label.config(text=f"{piece.name}  def {piece.defense.max}  [{slots}]")

    def _pinned_piece(self, piece_type: str):
        set_name = self.pin_vars[piece_type].get()
        if not set_name or set_name == NONE_OPTION:
            return None
        return self.piece_by_slot_set.get((piece_type, set_name))

    def _pinned_talisman(self) -> Talisman | None:
        name = self.pin_vars[TALISMAN_SLOT].get()
        if not name or name == NONE_OPTION:
            return None
        return self._pinnable_talismans().get(name)

    def _pinned_pieces(self) -> dict[str, str]:
        """Armour pins by slot, plus the talisman under TALISMAN_SLOT."""
        pinned = {}
        for piece_type in PIECE_TYPES:
            piece = self._pinned_piece(piece_type)
            if piece is not None:
                pinned[piece_type] = piece.name
        talisman = self._pinned_talisman()
        if talisman is not None:
            pinned[TALISMAN_SLOT] = talisman.name
        return pinned

    def _weapon_slots(self) -> tuple[int, ...]:
        """The weapon slot dropdowns as sizes, zeros ("no slot") dropped."""
        return tuple(int(v.get()) for v in self.weapon_slot_vars if int(v.get() or 0))

    # --- exclusions ----------------------------------------------------------

    def _exclusion_summary(self) -> str:
        if not self.excluded_sets and not self.excluded_pieces:
            return "Nothing excluded"
        parts = []
        if self.excluded_sets:
            parts.append(f"{len(self.excluded_sets)} set(s)")
        if self.excluded_pieces:
            parts.append(f"{len(self.excluded_pieces)} piece(s)")
        return "Excluded: " + ", ".join(parts)

    def _exclusion_state(self, iid: str) -> str:
        """What the Status column says for one tree row."""
        kind, name = iid.split(":", 1)
        if kind == "set":
            return "excluded" if name in self.excluded_sets else ""
        if name in self.excluded_pieces:
            return "excluded"
        if self.set_of_piece.get(name) in self.excluded_sets:
            return "excluded (set)"
        return ""

    def _toggle_exclusions(self, iids) -> None:
        """Flip each row between excluded and included.

        A piece row whose set is excluded is left alone: including it on its
        own would need a per-piece override of the set, which is a third state
        the optimiser has no notion of. Include the set instead.
        """
        for iid in iids:
            kind, name = iid.split(":", 1)
            target = self.excluded_sets if kind == "set" else self.excluded_pieces
            if kind == "piece" and self.set_of_piece.get(name) in self.excluded_sets:
                continue
            if name in target:
                target.discard(name)
            else:
                target.add(name)
        self.exclusion_summary_var.set(self._exclusion_summary())

    def _open_exclusions(self) -> None:
        window = self.exclusions_window
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return

        window = tk.Toplevel(self.root)
        window.title("Exclude Gear")
        window.geometry("560x680")
        window.transient(self.root)
        self.exclusions_window = window

        frame = ttk.Frame(window, padding=8)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        _hint(frame, "exclude_dialog", wrap=520).pack(side=tk.TOP, anchor=tk.W)

        filter_row = ttk.Frame(frame)
        filter_row.pack(side=tk.TOP, fill=tk.X, pady=(GAP, GAP))
        ttk.Label(filter_row, text="Filter:").pack(side=tk.LEFT)
        self.exclusion_filter_var = tk.StringVar()
        ttk.Entry(filter_row, textvariable=self.exclusion_filter_var, width=30).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        self.exclusion_filter_var.trace_add(
            "write", lambda *_a: self._fill_exclusion_tree()
        )

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(
            tree_frame, columns=("state",), show=("tree", "headings"),
            selectmode="extended",
        )
        tree.heading("#0", text="Armour")
        tree.heading("state", text="Status")
        tree.column("#0", width=360)
        tree.column("state", width=120, anchor=tk.W)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        scroll.pack(side=tk.LEFT, fill=tk.Y)
        tree.config(yscrollcommand=scroll.set)
        self.exclusion_tree = tree

        def toggle_selected(_event=None):
            self._toggle_exclusions(tree.selection())
            self._refresh_exclusion_states()
            # Returning "break" stops Treeview's own double-click handler,
            # which would also expand or collapse a set row on every toggle.
            return "break"

        tree.bind("<Double-1>", toggle_selected)
        tree.bind("<space>", toggle_selected)

        buttons = ttk.Frame(frame)
        buttons.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Exclude / Include Selected", command=toggle_selected).pack(
            side=tk.LEFT
        )

        def clear_all():
            self.excluded_sets.clear()
            self.excluded_pieces.clear()
            self.exclusion_summary_var.set(self._exclusion_summary())
            self._refresh_exclusion_states()

        ttk.Button(buttons, text="Include Everything", command=clear_all).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side=tk.RIGHT)

        self._theme_exclusions_window()
        self._fill_exclusion_tree()

    def _fill_exclusion_tree(self) -> None:
        """Rebuild the tree for the current filter text.

        A set whose name matches keeps all its pieces; otherwise it is shown
        with only the pieces that match, and opened, so a search for one
        piece does not leave it hidden inside a collapsed set.
        """
        tree = self.exclusion_tree
        tree.delete(*tree.get_children())
        needle = self.exclusion_filter_var.get().strip().lower()
        pieces_by_set: dict[str, list] = {}
        for piece in self.game_data.armor:
            pieces_by_set.setdefault(piece.set, []).append(piece)

        for set_name in sorted(pieces_by_set):
            pieces = sorted(
                pieces_by_set[set_name], key=lambda p: PIECE_TYPES.index(p.piece_type)
            )
            if needle and needle not in set_name.lower():
                pieces = [p for p in pieces if needle in p.name.lower()]
                if not pieces:
                    continue
            set_iid = f"set:{set_name}"
            tree.insert(
                "", tk.END, iid=set_iid, text=set_name, open=bool(needle),
                values=(self._exclusion_state(set_iid),),
            )
            for piece in pieces:
                piece_iid = f"piece:{piece.name}"
                tree.insert(
                    set_iid, tk.END, iid=piece_iid,
                    text=f"{piece.piece_type:<6} {piece.name}",
                    values=(self._exclusion_state(piece_iid),),
                )
        self._refresh_exclusion_states()

    def _refresh_exclusion_states(self) -> None:
        """Rewrite the Status column in place, keeping selection and scroll."""
        tree = self.exclusion_tree
        palette = THEMES["dark" if self.dark_var.get() else "light"]
        tree.tag_configure("excluded", foreground=palette["hint"])
        for set_iid in tree.get_children():
            for iid in (set_iid, *tree.get_children(set_iid)):
                state = self._exclusion_state(iid)
                tree.item(iid, values=(state,), tags=("excluded",) if state else ())

    def _theme_exclusions_window(self) -> None:
        palette = THEMES["dark" if self.dark_var.get() else "light"]
        window = getattr(self, "exclusions_window", None)
        if window is None or not window.winfo_exists():
            return
        if palette["window"]:
            window.configure(background=palette["window"])
        self._refresh_exclusion_states()

    def _build_custom_talismans_tab(self, parent: ttk.Frame) -> None:
        files = self._section(parent, "Talisman File", side=tk.TOP, fill=tk.X)
        file_row = ttk.Frame(files)
        file_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(file_row, text="File:").pack(side=tk.LEFT)
        self.ct_file_var = tk.StringVar(value="(none selected)")
        ttk.Label(file_row, textvariable=self.ct_file_var, style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(4, 8)
        )
        ttk.Button(
            file_row, text="Select/Create File...", command=self._select_custom_talisman_file
        ).pack(side=tk.LEFT)

        talismans = self._section(parent, "Use in Optimiser", side=tk.TOP, fill=tk.X)
        _hint(talismans, "talismans", wrap=900).pack(side=tk.TOP, anchor=tk.W)
        talisman_row = ttk.Frame(talismans)
        talisman_row.pack(side=tk.TOP, fill=tk.X, pady=(GAP, 0))
        ttk.Button(
            talisman_row,
            text="Load Custom Talismans...",
            command=self._load_custom_talismans_for_optimiser,
        ).pack(side=tk.LEFT)
        ttk.Button(
            talisman_row, text="Clear", command=self._clear_loaded_custom_talismans
        ).pack(side=tk.LEFT, padx=(4, 0))
        self.custom_talismans_status_var = tk.StringVar(
            value="Custom talismans: none loaded"
        )
        ttk.Label(
            talisman_row,
            textvariable=self.custom_talismans_status_var,
            style="Hint.TLabel",
        ).pack(side=tk.LEFT, padx=(8, 0))

        body = ttk.Frame(parent)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)

        list_box = ttk.LabelFrame(body, text="Talismans", padding=SECTION_PAD)
        list_box.pack(side=tk.LEFT, anchor=tk.N, fill=tk.Y, pady=(6, 0))
        list_frame = ttk.Frame(list_box)
        list_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.ct_listbox = tk.Listbox(
            list_frame, width=30, height=LIST_ROWS, exportselection=False
        )
        self.ct_listbox.pack(side=tk.TOP, fill=tk.Y, expand=True)
        ct_scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.ct_listbox.yview
        )
        self.ct_listbox.config(yscrollcommand=ct_scroll.set)
        self.ct_listbox.bind("<<ListboxSelect>>", self._on_custom_talisman_select)
        self.ct_delete_button = ttk.Button(
            list_frame, text="Delete Selected", command=self._delete_custom_talisman
        )
        self.ct_delete_button.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))

        form = ttk.LabelFrame(body, text="Talisman Details", padding=SECTION_PAD)
        form.pack(side=tk.LEFT, anchor=tk.N, fill=tk.BOTH, expand=True,
                  padx=(8, 0), pady=(6, 0))

        _hint(form, "ct_name", wrap=420).grid(
            row=0, column=0, columnspan=4, sticky=tk.W
        )
        ttk.Label(form, text="Name:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.ct_name_var = tk.StringVar()
        self.ct_name_entry = ttk.Entry(form, textvariable=self.ct_name_var, width=30)
        self.ct_name_entry.grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=(8, 0))

        _hint(form, "ct_rarity", wrap=420).grid(
            row=2, column=0, columnspan=4, sticky=tk.W, pady=(8, 0)
        )
        ttk.Label(form, text="Rarity:").grid(row=3, column=0, sticky=tk.W, pady=4)
        self.ct_rarity_var = tk.StringVar(value="5")
        self.ct_rarity_spin = ttk.Spinbox(
            form, from_=1, to=8, textvariable=self.ct_rarity_var, width=5, justify=tk.CENTER
        )
        self.ct_rarity_spin.grid(row=3, column=1, sticky=tk.W, padx=(8, 0))

        _hint(form, "ct_skills", wrap=420).grid(
            row=4, column=0, columnspan=4, sticky=tk.W, pady=(8, 0)
        )
        ttk.Label(form, text="Skills:").grid(row=5, column=0, sticky=tk.NW, pady=(4, 4))
        self.ct_skill_widgets: list[tuple[ttk.Combobox, ttk.Spinbox]] = []
        skill_row_vars: list[tuple[tk.StringVar, tk.StringVar]] = []
        for i in range(MAX_TALISMAN_SKILLS):
            name_var = tk.StringVar(value=NONE_OPTION)
            level_var = tk.StringVar(value="1")
            name_combo = ttk.Combobox(
                form, textvariable=name_var, values=[NONE_OPTION], state="readonly", width=26
            )
            name_combo.grid(row=5 + i, column=1, sticky=tk.W, padx=(8, 4), pady=2)
            level_spin = ttk.Spinbox(
                form, from_=1, to=5, textvariable=level_var, width=4, justify=tk.CENTER
            )
            level_spin.grid(row=5 + i, column=2, sticky=tk.W, pady=2)
            self.ct_skill_widgets.append((name_combo, level_spin))
            skill_row_vars.append((name_var, level_var))
        self.ct_skill_rows = skill_row_vars

        slot_row = 6 + MAX_TALISMAN_SKILLS
        _hint(form, "ct_armour_slots", wrap=420).grid(
            row=slot_row - 1, column=0, columnspan=4, sticky=tk.W, pady=(12, 0)
        )
        ttk.Label(form, text="Armour Slots:").grid(
            row=slot_row, column=0, sticky=tk.W, pady=(4, 4)
        )
        self.ct_armour_slot_widgets: list[ttk.Combobox] = []
        self.ct_armour_slot_vars: list[tk.StringVar] = []
        for i in range(MAX_TALISMAN_SLOTS):
            var = tk.StringVar(value="0")
            combo = ttk.Combobox(
                form, textvariable=var, values=["0", "1", "2", "3"], state="readonly", width=4
            )
            combo.grid(row=slot_row, column=1 + i, sticky=tk.W, padx=(8 if i == 0 else 4, 0))
            self.ct_armour_slot_widgets.append(combo)
            self.ct_armour_slot_vars.append(var)

        _hint(form, "ct_weapon_slots", wrap=420).grid(
            row=slot_row + 1, column=0, columnspan=4, sticky=tk.W, pady=(8, 0)
        )
        ttk.Label(form, text="Weapon Slots:").grid(
            row=slot_row + 2, column=0, sticky=tk.W, pady=(4, 4)
        )
        self.ct_weapon_slot_widgets: list[ttk.Combobox] = []
        self.ct_weapon_slot_vars: list[tk.StringVar] = []
        for i in range(MAX_TALISMAN_SLOTS):
            var = tk.StringVar(value="0")
            combo = ttk.Combobox(
                form, textvariable=var, values=["0", "1", "2", "3"], state="readonly", width=4
            )
            combo.grid(row=slot_row + 2, column=1 + i, sticky=tk.W, padx=(8 if i == 0 else 4, 0))
            self.ct_weapon_slot_widgets.append(combo)
            self.ct_weapon_slot_vars.append(var)

        button_row = slot_row + 3
        buttons = ttk.Frame(form)
        buttons.grid(row=button_row, column=0, columnspan=4, sticky=tk.W, pady=(16, 0))
        self.ct_new_button = ttk.Button(
            buttons, text="New", command=self._clear_custom_talisman_form
        )
        self.ct_new_button.pack(side=tk.LEFT)
        self.ct_save_button = ttk.Button(
            buttons, text="Save Talisman", command=self._save_custom_talisman
        )
        self.ct_save_button.pack(side=tk.LEFT, padx=(8, 0))

        status_row = ttk.Frame(parent, padding=(16, 4, 16, 8))
        status_row.pack(side=tk.BOTTOM, fill=tk.X)
        self.ct_status_var = tk.StringVar(value="")
        ttk.Label(status_row, textvariable=self.ct_status_var, style="Status.TLabel").pack(
            side=tk.LEFT
        )

        self._set_custom_talisman_controls_enabled(False)

    # --- skills tab: file handling ------------------------------------------

    def _open_file(self) -> None:
        if not self._discard_changes_ok():
            return
        path_str = filedialog.askopenfilename(
            title="Open skills file",
            initialdir=str(self.current_path.parent),
            initialfile=self.current_path.name,
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        self._load_file(Path(path_str))

    def _load_file(self, path: Path) -> None:
        try:
            skills = load_skills(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Failed to load file", f"Could not load {path}:\n{exc}")
            return
        self._use_skills(skills, path)

    def _use_skills(self, skills: list[Skill], path: Path) -> None:
        """Make these skills the ones being edited, as if read from path."""
        self.current_path = path
        self.output_dir = path.parent
        self.skills = skills
        self.skills_by_name = {s.name: s for s in self.skills}
        self.cache = {
            s.name: {
                "weight": _weight_text(s.weight),
                "level_weight": _weight_text(s.level_weight),
            }
            for s in self.skills
        }
        self.selected_name = None
        self.status_var.set("")

        self.file_label_var.set(str(self.current_path))
        types = ["All"] + sorted({s.type for s in self.skills})
        self.type_combo.config(values=types)
        self.type_var.set("All")

        self._size_description_box()
        self._size_info_columns()
        self._refresh_gogma_options()
        self._refresh_listbox()
        self._refresh_custom_talisman_skill_options()

    def _size_description_box(self) -> None:
        """Reserve the height the tallest skill in this file needs.

        Every skill is laid out in the real widgets once and Tk reports the
        height it asked for. Estimating from font.measure() instead undercounts
        wherever a word wraps early, and with up to seven wrapped level lines
        the error compounds into clipped text. A constant would be wrong at a
        different display scaling, in a different theme, or against a file whose
        text runs longer. Laying out a couple of hundred skills once per load
        is cheap next to that.
        """
        tallest = 1
        for skill in self.skills:
            self._fill_description(skill)
            self.desc_content.update_idletasks()
            tallest = max(tallest, self.desc_content.winfo_reqheight())
        self._fill_description(self.skills_by_name.get(self.selected_name))
        self.desc_holder.config(height=tallest)

    def _levels_for(self, skill: Skill):
        """This skill's per-level effects, borrowed from the default file if
        the loaded one predates them - a weighted file saved before levels
        existed still shows them, without having to be re-saved first."""
        return skill.levels or self._default_levels.get(skill.name, [])

    def _level_caption(self, rank) -> str:
        """'Lv 3', or the piece count a set bonus or group tier needs."""
        pieces = self._pieces_by_tier.get(rank.name) if rank.name else None
        return f"{pieces} pieces" if pieces else f"Lv {rank.level}"

    def _fill_description(self, skill: Skill | None) -> None:
        for child in self.levels_frame.winfo_children():
            child.destroy()
        if skill is None:
            self.desc_label.config(text="")
            return
        self.desc_label.config(text=skill.description)

        levels = self._levels_for(skill)
        captions = [self._level_caption(rank) for rank in levels]
        font = tkfont.nametofont("TkDefaultFont")
        caption_width = max((font.measure(c) for c in captions), default=0) + 10
        for row, (rank, caption) in enumerate(zip(levels, captions)):
            text = f"{rank.name}: {rank.effect}" if rank.name else rank.effect
            ttk.Label(self.levels_frame, text=caption, style="Hint.TLabel").grid(
                row=row, column=0, sticky=tk.NW, pady=(0, LEVEL_GAP)
            )
            ttk.Label(
                self.levels_frame,
                text=text,
                wraplength=DESC_WRAP - caption_width,
                justify=tk.LEFT,
            ).grid(row=row, column=1, sticky=tk.NW, pady=(0, LEVEL_GAP))
        self.levels_frame.grid_columnconfigure(0, minsize=caption_width)

    def _size_info_columns(self) -> None:
        """Hold each info value's column at its widest value in this file.

        Without it the captions after a value slide left and right as you move
        down the list - "Set Bonus" is more than twice the width of "Food" - and
        the row jitters on every selection.
        """
        font = tkfont.nametofont("TkDefaultFont")
        for column, (_caption, field) in enumerate(INFO_FIELDS):
            widest = max(
                (font.measure(str(getattr(skill, field))) for skill in self.skills),
                default=0,
            )
            self.info_frame.grid_columnconfigure(column * 2 + 1, minsize=widest)

    def _refresh_gogma_options(self) -> None:
        offered = [s for s in self.skills if s.name in self._armour_bonus_names]
        set_names = [NONE_OPTION] + sorted(
            s.name for s in offered if s.type == "Set Bonus"
        )
        group_names = [NONE_OPTION] + sorted(
            s.name for s in offered if s.type == "Group"
        )
        self.gogma_set_combo.config(values=set_names)
        self.gogma_group_combo.config(values=group_names)
        if self.gogma_set_var.get() not in set_names:
            self.gogma_set_var.set(NONE_OPTION)
        if self.gogma_group_var.get() not in group_names:
            self.gogma_group_var.set(NONE_OPTION)

    def _is_weighted(self, name: str) -> bool:
        """Non-zero weight in the (possibly unsaved) edits."""
        try:
            return float(self.cache[name]["weight"]) != 0
        except ValueError:
            return False

    def _filtered_skills(self) -> list[Skill]:
        """Skills passing the type, text and weighted-only filters together.

        Text matches anywhere in the name, ignoring case, so "resist" finds
        every resistance skill without knowing how each is spelt.
        """
        selected_type = self.type_var.get()
        needle = self.search_var.get().strip().lower()
        weighted_only = self.weighted_only_var.get()
        return [
            s
            for s in self.skills
            if (selected_type == "All" or s.type == selected_type)
            and needle in s.name.lower()
            and (not weighted_only or self._is_weighted(s.name))
        ]

    def _row_text(self, name: str) -> str:
        """'Weakness Exploit   5/0' for weighted skills, the bare name otherwise.

        Showing the weight in the list is what makes a weighting reviewable
        at a glance; before, checking it meant clicking every skill in turn.
        """
        if not self._is_weighted(name):
            return name
        cached = self.cache[name]
        return f"{name}   {cached['weight']}/{cached['level_weight']}"

    def _refresh_listbox(self) -> None:
        """Rebuild the rows, keeping the selection if it is still listed.

        Typing in Find rebuilds on every key; dropping the selection each
        time would clear the weighting panel under the user mid-search.
        """
        keep = self.selected_name
        self.listbox.delete(0, tk.END)
        self._listed_names = [s.name for s in self._filtered_skills()]
        for name in self._listed_names:
            self.listbox.insert(tk.END, self._row_text(name))
        self._update_weighted_count()
        if keep in self._listed_names:
            index = self._listed_names.index(keep)
            self.listbox.selection_set(index)
            self.listbox.see(index)
            return
        self.selected_name = None
        self._show_details(None)

    def _update_weighted_count(self) -> None:
        count = sum(1 for s in self.skills if self._is_weighted(s.name))
        self.weighted_count_var.set(f"{count} weighted" if count else "")

    def _refresh_selected_row(self) -> None:
        """Redraw one row after its weight changed, without a full rebuild."""
        name = self.selected_name
        if name not in self._listed_names:
            return
        index = self._listed_names.index(name)
        self.listbox.delete(index)
        self.listbox.insert(index, self._row_text(name))
        self.listbox.selection_set(index)
        self._update_weighted_count()

    def _clear_all_weights(self) -> None:
        """Every weight and level weight back to 0, after asking.

        Only the edits are cleared; nothing is written until Save, so the
        loaded file is untouched and reopening it undoes this.
        """
        if not any(self._is_weighted(s.name) for s in self.skills):
            return
        if not messagebox.askyesno(
            "Clear All Weights",
            "Set every skill's weight and level focus to 0? The file is not "
            "changed until you save.",
        ):
            return
        for entry in self.cache.values():
            entry["weight"] = "0"
            entry["level_weight"] = "0"
        self._refresh_listbox()
        if self.selected_name is not None:
            self._show_details(self.selected_name)

    def _on_select(self, _event: object) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        name = self._listed_names[selection[0]]
        self.selected_name = name
        self._show_details(name)

    def _show_details(self, name: str | None) -> None:
        self._suppress_trace = True
        try:
            if name is None:
                # Blank values, not blank labels: the captions above stay put,
                # so nothing in the section moves when a skill is chosen.
                self.name_label.config(text=" ")
                for value in self.info_value_labels.values():
                    value.config(text="")
                self._fill_description(None)
                self.weight_var.set("")
                self.level_weight_var.set("")
                self.weight_entry.config(state=tk.DISABLED)
                self.level_weight_entry.config(state=tk.DISABLED)
                return

            skill = self.skills_by_name[name]
            self.name_label.config(text=skill.name)
            for field, value in self.info_value_labels.items():
                value.config(text=str(getattr(skill, field)))
            self._fill_description(skill)

            cached = self.cache[name]
            self.weight_var.set(cached["weight"])
            self.level_weight_var.set(cached["level_weight"])
            self.weight_entry.config(state="readonly")
            self.level_weight_entry.config(state="readonly")
        finally:
            self._suppress_trace = False

    def _on_weight_change(self, *_args: object) -> None:
        if self._suppress_trace or self.selected_name is None:
            return
        self.cache[self.selected_name]["weight"] = self.weight_var.get()
        self._refresh_selected_row()

    def _on_level_weight_change(self, *_args: object) -> None:
        if self._suppress_trace or self.selected_name is None:
            return
        self.cache[self.selected_name]["level_weight"] = self.level_weight_var.get()
        self._refresh_selected_row()

    def _effective_skills(self) -> list[Skill]:
        """The skills list with every cached (possibly unsaved) edit applied."""
        result = []
        for skill in self.skills:
            cached = self.cache[skill.name]
            try:
                weight = float(cached["weight"])
            except ValueError:
                weight = skill.weight
            try:
                level_weight = float(cached["level_weight"])
            except ValueError:
                level_weight = skill.level_weight
            result.append(replace(skill, weight=weight, level_weight=level_weight))
        return result

    def _has_unsaved_changes(self) -> bool:
        """True if any weight differs from the file it was loaded from."""
        return any(
            self.cache[skill.name]["weight"] != _weight_text(skill.weight)
            or self.cache[skill.name]["level_weight"] != _weight_text(skill.level_weight)
            for skill in self.skills
        )

    def _discard_changes_ok(self) -> bool:
        """Ask before throwing away edited weights; True means go ahead."""
        if not self._has_unsaved_changes():
            return True
        return messagebox.askokcancel(
            "Unsaved weights",
            "Some weights have been changed but not saved. Discard them?",
        )

    def _write_skills(self, output_path: Path) -> bool:
        """Write the edited weights; False if the target was refused.

        skills_default.yaml is refused outright. It is the data every weighted
        file is built from, and the Save box writes beside whatever file is
        loaded - usually that one - so typing its name would replace the
        master copy with no way back short of git.
        """
        if output_path.resolve() == Path(SKILLS_PATH).resolve():
            messagebox.showerror(
                "Save",
                f"{output_path.name} holds the default skill data and is never "
                "overwritten. Choose another file name.",
            )
            return False

        effective = self._effective_skills()
        output = [skill_record(skill) for skill in effective]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            yaml.dump(output, f, sort_keys=False, allow_unicode=True, default_flow_style=False)

        # What was just written is the new baseline for "unsaved changes".
        self.skills = effective
        self.skills_by_name = {s.name: s for s in self.skills}
        return True

    def _save(self) -> None:
        filename = self.output_name_var.get().strip()
        if not filename:
            messagebox.showerror("Save", "Enter an output file name.")
            return
        if not filename.lower().endswith((".yaml", ".yml")):
            filename += ".yaml"
            self.output_name_var.set(filename)

        output_path = self.output_dir / filename
        # Browse gets this prompt from the OS dialog; the plain Save button
        # has no dialog, so it asks here instead.
        if output_path.exists() and not messagebox.askyesno(
            "Save", f"{output_path.name} already exists. Replace it?"
        ):
            return
        if not self._write_skills(output_path):
            return
        self.status_var.set(f"Saved to {output_path.name}")
        messagebox.showinfo("Saved", f"Saved weights to {output_path}")

    def _browse_save(self) -> None:
        path_str = filedialog.asksaveasfilename(
            title="Save weighted skills as",
            initialdir=str(self.output_dir),
            initialfile=self.output_name_var.get() or "skills_weighted.yaml",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        output_path = Path(path_str)
        if not self._write_skills(output_path):
            return
        self.output_dir = output_path.parent
        self.output_name_var.set(output_path.name)
        self.status_var.set(f"Saved to {output_path.name}")
        messagebox.showinfo("Saved", f"Saved weights to {output_path}")

    # --- search profiles ------------------------------------------------------

    def _current_profile(self) -> SearchProfile:
        """Everything on the tab as a profile. Raises ValueError on a bad reserve."""
        reserve = int(self.reserved_slots_var.get())
        if reserve < 0:
            raise ValueError
        gogma_set = self.gogma_set_var.get()
        gogma_group = self.gogma_group_var.get()
        return SearchProfile(
            weights=weights_of(self._effective_skills()),
            pins=self._pinned_pieces(),
            exclude_sets=sorted(self.excluded_sets),
            exclude_pieces=sorted(self.excluded_pieces),
            weapon_slots=list(self._weapon_slots()),
            gogma_set_bonus=None if gogma_set in ("", NONE_OPTION) else gogma_set,
            gogma_group_skill=None if gogma_group in ("", NONE_OPTION) else gogma_group,
            reserve=reserve,
            relax=bool(self.relax_var.get()),
            custom_talismans=stored_path(self.custom_talismans_source),
            filters=self._current_filters(),
        )

    def _save_profile(self) -> None:
        try:
            profile = self._current_profile()
        except ValueError:
            messagebox.showerror(
                "Save Profile", "Reserved slots must be a non-negative integer."
            )
            return
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        path_str = filedialog.asksaveasfilename(
            title="Save search profile",
            initialdir=str(PROFILES_DIR),
            initialfile="my_build.yaml",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        self._write_profile(profile, Path(path_str))

    def _write_profile(self, profile: SearchProfile, path: Path) -> None:
        save_profile(profile, path)
        # The weights are now kept somewhere, so they stop counting as
        # unsaved: closing afterwards should not ask to discard them.
        self.skills = self._effective_skills()
        self.skills_by_name = {s.name: s for s in self.skills}
        self.file_label_var.set(f"Profile: {path}")
        self.status_var.set(f"Saved profile {path.name}")

    def _load_profile(self) -> None:
        if not self._discard_changes_ok():
            return
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        path_str = filedialog.askopenfilename(
            title="Load search profile",
            initialdir=str(PROFILES_DIR),
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        self._read_profile(Path(path_str))

    def _read_profile(self, path: Path) -> bool:
        """Load a profile into the tab; False, having said why, if refused.

        Refused whole rather than applied in part: a profile whose pins
        loaded but whose exclusions did not would run a search nobody asked
        for, with nothing on screen saying which half is missing.
        """
        try:
            profile = load_profile(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Load Profile", f"Could not load {path}:\n{exc}")
            return False
        problems = profile_problems(profile, self.game_data)
        talismans: list[Talisman] = []
        talismans_path = profile.custom_talismans_path()
        if talismans_path is not None and not problems:
            try:
                talismans = load_talismans(talismans_path)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"Custom talisman file {talismans_path}: {exc}")
        if problems:
            messagebox.showerror(
                "Load Profile",
                f"{path.name} does not match the current data:\n\n"
                + "\n".join(f"\u2022 {p}" for p in problems),
            )
            return False

        # game_data.skills is skills_default.yaml, freshly read at start-up,
        # which is the point: the profile's weights land on current data.
        self._use_skills(apply_weights(self.game_data.skills, profile.weights), Path(SKILLS_PATH))
        self.file_label_var.set(f"Profile: {path}")

        by_name = {p.name: p for p in self.game_data.armor}
        for piece_type in PIECE_TYPES:
            name = profile.pins.get(piece_type)
            self.pin_vars[piece_type].set(by_name[name].set if name else NONE_OPTION)
        self.excluded_sets = set(profile.exclude_sets)
        self.excluded_pieces = set(profile.exclude_pieces)
        self.exclusion_summary_var.set(self._exclusion_summary())
        window = self.exclusions_window
        if window is not None and window.winfo_exists():
            self._refresh_exclusion_states()
        sizes = list(profile.weapon_slots) + [0] * MAX_WEAPON_SLOTS
        for var, size in zip(self.weapon_slot_vars, sizes):
            var.set(str(size))
        self.gogma_set_var.set(profile.gogma_set_bonus or NONE_OPTION)
        self.gogma_group_var.set(profile.gogma_group_skill or NONE_OPTION)
        self.reserved_slots_var.set(str(profile.reserve))
        self.relax_var.set(profile.relax)
        self._set_filters(profile.filters)
        if talismans_path is None:
            self._clear_loaded_custom_talismans()
        else:
            self.custom_talismans_loaded = talismans
            self.custom_talismans_source = talismans_path
            self.custom_talismans_status_var.set(
                f"Custom talismans: {len(talismans)} loaded from {talismans_path.name}"
            )
            self._refresh_talisman_pin_options()
        # After the custom file, which may be where the pinned talisman lives.
        self.pin_vars[TALISMAN_SLOT].set(profile.pins.get(TALISMAN_SLOT) or NONE_OPTION)
        self.status_var.set(f"Loaded profile {path.name}")
        return True

    # --- custom talismans: loading a file for the optimiser to use ---------

    def _load_custom_talismans_for_optimiser(self) -> None:
        initial_dir = (
            self.custom_talisman_path.parent
            if self.custom_talisman_path is not None
            else CUSTOM_TALISMANS_DIR
        )
        CUSTOM_TALISMANS_DIR.mkdir(parents=True, exist_ok=True)
        path_str = filedialog.askopenfilename(
            title="Load custom talismans for the optimiser",
            initialdir=str(initial_dir),
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            talismans = load_talismans(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Failed to load file", f"Could not load {path}:\n{exc}")
            return

        self.custom_talismans_loaded = talismans
        self.custom_talismans_source = path
        self.custom_talismans_status_var.set(
            f"Custom talismans: {len(talismans)} loaded from {path.name}"
        )
        self._refresh_talisman_pin_options()

    def _clear_loaded_custom_talismans(self) -> None:
        self.custom_talismans_loaded = []
        self.custom_talismans_source = None
        self.custom_talismans_status_var.set("Custom talismans: none loaded")
        self._refresh_talisman_pin_options()

    # --- custom talismans tab: building/editing -----------------------------

    def _refresh_custom_talisman_skill_options(self) -> None:
        names = [NONE_OPTION] + sorted(
            s.name for s in self.skills if s.type == "Armor"
        )
        for name_combo, _level_spin in self.ct_skill_widgets:
            name_combo.config(values=names)
        for name_var, _level_var in self.ct_skill_rows:
            if name_var.get() not in names:
                name_var.set(NONE_OPTION)

    def _set_custom_talisman_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        combo_state = "readonly" if enabled else tk.DISABLED
        self.ct_name_entry.config(state=state)
        self.ct_rarity_spin.config(state=state)
        for name_combo, level_spin in self.ct_skill_widgets:
            name_combo.config(state=combo_state)
            level_spin.config(state=state)
        for combo in self.ct_armour_slot_widgets + self.ct_weapon_slot_widgets:
            combo.config(state=combo_state)
        self.ct_save_button.config(state=state)
        self.ct_delete_button.config(state=state)
        self.ct_new_button.config(state=state)

    def _select_custom_talisman_file(self) -> None:
        CUSTOM_TALISMANS_DIR.mkdir(parents=True, exist_ok=True)
        # A save dialog, because it is the only one that can name a file that
        # does not exist yet. confirmoverwrite is off because choosing an
        # existing file here opens it for editing and writes nothing, so
        # Windows' "replace it?" prompt would be asking about something that
        # never happens.
        path_str = filedialog.asksaveasfilename(
            title="Select or create a custom talismans file",
            initialdir=str(CUSTOM_TALISMANS_DIR),
            initialfile="my_talismans.yaml",
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
            confirmoverwrite=False,
        )
        if not path_str:
            return
        path = Path(path_str)

        talismans: list[Talisman] = []
        if path.exists():
            try:
                talismans = load_talismans(path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Failed to load file", f"Could not load {path}:\n{exc}")
                return

        self.custom_talisman_path = path
        self.custom_talismans = talismans
        self.ct_file_var.set(str(path))
        self._refresh_custom_talisman_skill_options()
        self._set_custom_talisman_controls_enabled(True)
        self._refresh_custom_talisman_listbox()
        self._clear_custom_talisman_form()
        self.ct_status_var.set(f"Using {path.name} ({len(talismans)} talismans)")

    def _refresh_custom_talisman_listbox(self) -> None:
        self.ct_listbox.delete(0, tk.END)
        for talisman in self.custom_talismans:
            self.ct_listbox.insert(tk.END, talisman.name)
        self.ct_selected_index = None

    def _on_custom_talisman_select(self, _event: object) -> None:
        selection = self.ct_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        self.ct_selected_index = index
        talisman = self.custom_talismans[index]

        self.ct_name_var.set(talisman.name)
        self.ct_rarity_var.set(str(talisman.rarity))

        skills = list(talisman.skills) + [None] * MAX_TALISMAN_SKILLS
        for (name_var, level_var), skill in zip(self.ct_skill_rows, skills):
            if skill is not None:
                name_var.set(skill.name)
                level_var.set(str(skill.level))
            else:
                name_var.set(NONE_OPTION)
                level_var.set("1")

        armour_sizes = _slot_sizes(talisman.decoration_slots.armour)
        armour_sizes += [0] * MAX_TALISMAN_SLOTS
        for var, size in zip(self.ct_armour_slot_vars, armour_sizes):
            var.set(str(size))

        weapon_sizes = _slot_sizes(talisman.decoration_slots.weapon)
        weapon_sizes += [0] * MAX_TALISMAN_SLOTS
        for var, size in zip(self.ct_weapon_slot_vars, weapon_sizes):
            var.set(str(size))

    def _clear_custom_talisman_form(self) -> None:
        self.ct_selected_index = None
        self.ct_listbox.selection_clear(0, tk.END)
        self.ct_name_var.set("")
        self.ct_rarity_var.set("5")
        for name_var, level_var in self.ct_skill_rows:
            name_var.set(NONE_OPTION)
            level_var.set("1")
        for var in self.ct_armour_slot_vars:
            var.set("0")
        for var in self.ct_weapon_slot_vars:
            var.set("0")

    def _save_custom_talisman(self) -> None:
        if self.custom_talisman_path is None:
            messagebox.showerror(
                "Save Talisman", "Select or create a custom talismans file first."
            )
            return

        name = self.ct_name_var.get().strip()
        if not name:
            messagebox.showerror("Save Talisman", "Enter a talisman name.")
            return

        try:
            rarity = int(self.ct_rarity_var.get())
        except ValueError:
            messagebox.showerror("Save Talisman", "Rarity must be an integer.")
            return

        skills: list[SkillLevel] = []
        for name_var, level_var in self.ct_skill_rows:
            skill_name = name_var.get()
            if not skill_name or skill_name == NONE_OPTION:
                continue
            try:
                level = int(level_var.get())
            except ValueError:
                messagebox.showerror(
                    "Save Talisman", f"'{skill_name}' has an invalid level."
                )
                return
            if level < 1:
                messagebox.showerror(
                    "Save Talisman", f"'{skill_name}' needs a level of at least 1."
                )
                return
            # Two rows of one skill would stack past what a single row allows,
            # getting round the max-level check below.
            if any(existing.name == skill_name for existing in skills):
                messagebox.showerror(
                    "Save Talisman", f"'{skill_name}' is chosen more than once."
                )
                return
            skill = self.skills_by_name.get(skill_name)
            if skill is not None and level > skill.max_level:
                messagebox.showerror(
                    "Save Talisman",
                    f"'{skill_name}' only goes up to level {skill.max_level}.",
                )
                return
            skills.append(SkillLevel(name=skill_name, level=level))

        if not skills:
            messagebox.showerror("Save Talisman", "Select at least one skill.")
            return

        armour_slots = [int(v.get()) for v in self.ct_armour_slot_vars if int(v.get())]
        weapon_slots = [int(v.get()) for v in self.ct_weapon_slot_vars if int(v.get())]

        duplicate_index = next(
            (i for i, t in enumerate(self.custom_talismans) if t.name == name), None
        )
        if duplicate_index is not None and duplicate_index != self.ct_selected_index:
            messagebox.showerror(
                "Save Talisman", f"A talisman named '{name}' already exists."
            )
            return

        talisman = Talisman(
            name=name,
            rarity=rarity,
            skills=skills,
            slots=[],
            decoration_slots=DecorationSlots(armour=armour_slots, weapon=weapon_slots),
            source_url="custom",
        )

        if self.ct_selected_index is not None:
            self.custom_talismans[self.ct_selected_index] = talisman
        else:
            self.custom_talismans.append(talisman)

        self._write_custom_talismans(self.custom_talisman_path)
        self._refresh_custom_talisman_listbox()
        self._clear_custom_talisman_form()
        self.ct_status_var.set(f"Saved '{name}' to {self.custom_talisman_path.name}")

    def _delete_custom_talisman(self) -> None:
        if self.ct_selected_index is None:
            return
        # The file is rewritten straight away, so there is no undo.
        name = self.custom_talismans[self.ct_selected_index].name
        if not messagebox.askyesno(
            "Delete Talisman",
            f"Delete '{name}' from {self.custom_talisman_path.name}?",
        ):
            return
        removed = self.custom_talismans.pop(self.ct_selected_index)
        self._write_custom_talismans(self.custom_talisman_path)
        self._refresh_custom_talisman_listbox()
        self._clear_custom_talisman_form()
        self.ct_status_var.set(f"Deleted '{removed.name}'")

    def _write_custom_talismans(self, path: Path) -> None:
        output = []
        for talisman in self.custom_talismans:
            output.append(
                {
                    "name": talisman.name,
                    "rarity": talisman.rarity,
                    "skills": [
                        {"name": s.name, "level": s.level} for s in talisman.skills
                    ],
                    "slots": list(talisman.slots),
                    "decoration_slots": {
                        "armour": talisman.decoration_slots.armour,
                        "weapon": talisman.decoration_slots.weapon,
                    },
                    "source_url": talisman.source_url,
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(output, f, sort_keys=False, allow_unicode=True, default_flow_style=False)

    # --- optimiser integration ------------------------------------------

    def _run_optimiser(self) -> None:
        if self._optimiser_running:
            return

        try:
            reserved_slots = int(self.reserved_slots_var.get())
            if reserved_slots < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Run Optimiser", "Reserved slots must be a non-negative integer."
            )
            return

        extra_bonus_pieces: dict[str, int] = {}
        set_bonus = self.gogma_set_var.get()
        if set_bonus and set_bonus != NONE_OPTION:
            extra_bonus_pieces[set_bonus] = extra_bonus_pieces.get(set_bonus, 0) + 1
        group_skill = self.gogma_group_var.get()
        if group_skill and group_skill != NONE_OPTION:
            extra_bonus_pieces[group_skill] = extra_bonus_pieces.get(group_skill, 0) + 1

        # Caught here rather than left to the optimiser's ValueError so the
        # message names the fix in GUI terms, not the engine's.
        pinned_pieces = self._pinned_pieces()
        for piece_type, name in pinned_pieces.items():
            piece_set = self.set_of_piece.get(name)
            if name in self.excluded_pieces or piece_set in self.excluded_sets:
                messagebox.showerror(
                    "Run Optimiser",
                    f"{name} is pinned to {piece_type} but also excluded. Unpin "
                    "it or include it again under Exclude Gear.",
                )
                return

        problems = self._filter_input_problems()
        filters = self._current_filters()
        problems += pin_conflicts(
            self.game_data, apply_filters(self.game_data, filters), pinned_pieces
        )
        if problems:
            messagebox.showerror(
                "Run Optimiser",
                "\n".join(problems) + "\n\nChange the pins or the Filters tab and run again.",
            )
            return

        request = RunRequest(
            skills=self._effective_skills(),
            reserved_slots=reserved_slots,
            extra_bonus_pieces=extra_bonus_pieces,
            custom_talismans=list(self.custom_talismans_loaded),
            pinned_pieces=pinned_pieces,
            excluded_sets=sorted(self.excluded_sets),
            excluded_pieces=sorted(self.excluded_pieces),
            strict=not self.relax_var.get(),
            weapon_slots=self._weapon_slots(),
            filters=filters,
            source_label=str(self.current_path)
            + (" (with unsaved edits)" if self._has_unsaved_changes() else ""),
        )

        self._optimiser_running = True
        self.run_button.config(state=tk.DISABLED, text="Working...")
        self.cancel_button.config(state=tk.NORMAL)
        self.status_var.set("")
        self.progress_var.set(0.0)
        self.progress_text_var.set("Starting...")

        # Tkinter is not thread-safe: the worker only ever writes to this queue
        # and reads this event, never touches self.root, and the main thread
        # polls the queue via after().
        self._optimiser_queue: queue.Queue = queue.Queue()
        self._cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._optimiser_thread,
            args=(request, self._optimiser_queue, self._cancel_event),
            daemon=True,
        )
        thread.start()
        self.root.after(100, self._poll_optimiser_queue)

    def _cancel_optimiser(self) -> None:
        """Ask the worker to stop; it notices within a fraction of a second."""
        event = getattr(self, "_cancel_event", None)
        if event is not None and self._optimiser_running:
            event.set()
            self.cancel_button.config(state=tk.DISABLED)
            self.progress_text_var.set("Cancelling...")

    def _optimiser_thread(
        self,
        request: RunRequest,
        result_queue: queue.Queue,
        cancel: threading.Event | None = None,
    ) -> None:
        def progress(fraction: float, message: str) -> None:
            result_queue.put(("progress", fraction, message, None))

        try:
            # Filters first, before the custom talismans join: the tier
            # filter is for craftable charms only (gear_filters.apply_filters).
            filtered = apply_filters(self.game_data, request.filters)
            game_data = filtered.game
            if request.custom_talismans:
                game_data = replace(
                    game_data,
                    talismans=list(game_data.talismans) + request.custom_talismans,
                )
            scoring = Scoring(request.skills)
            sets, constraint_level, optimiser = optimise(
                game_data,
                scoring,
                reserved_slots=request.reserved_slots,
                extra_bonus_pieces=request.extra_bonus_pieces,
                pinned_pieces=request.pinned_pieces,
                strict=request.strict,
                excluded_sets=request.excluded_sets,
                excluded_pieces=sorted(set(request.excluded_pieces) | set(filtered.removed)),
                weapon_slots=request.weapon_slots,
                progress=progress,
                should_stop=cancel.is_set if cancel is not None else None,
            )
            # Why nothing came back, gathered on this thread while the optimiser
            # is still in scope: impossible_requirements is a proof, so it wins
            # over unmet_requirements, which only reports what was not found.
            reasons = []
            if not sets:
                reasons = optimiser.impossible_requirements()
                if not reasons:
                    reasons = optimiser.unmet_requirements() + [
                        "The search is a heuristic, so this is what it did not "
                        "find, not proof that nothing exists."
                    ]
        except SearchCancelled:
            result_queue.put(("cancelled", None, None, None))
            return
        except Exception as exc:  # noqa: BLE001
            result_queue.put(("error", exc, None, None))
            return
        result_queue.put(
            ("ok", RunResult(sets, scoring, reasons, constraint_level, request), None, None)
        )

    def _poll_optimiser_queue(self) -> None:
        """Drain the worker's queue: progress updates, then at most one end.

        Everything waiting is read at once, because the worker can post many
        progress messages between two polls and drawing each in turn would
        leave the bar trailing behind the search.
        """
        while True:
            try:
                kind, first, second, third = self._optimiser_queue.get_nowait()
            except queue.Empty:
                self.root.after(100, self._poll_optimiser_queue)
                return
            if kind == "progress":
                self.progress_var.set(first)
                self.progress_text_var.set(second)
                continue
            break

        self.cancel_button.config(state=tk.DISABLED)
        self.progress_var.set(0.0)
        self.progress_text_var.set("")
        if kind == "cancelled":
            self._optimiser_running = False
            self.run_button.config(state=tk.NORMAL, text="Run Optimiser")
            self.status_var.set("Cancelled.")
        elif kind == "error":
            self._optimiser_done(None, first)
        else:
            self._optimiser_done(first, None)

    def _optimiser_done(
        self, result: RunResult | None, error: Exception | None
    ) -> None:
        self._optimiser_running = False
        self.run_button.config(state=tk.NORMAL, text="Run Optimiser")

        if error is not None:
            self.status_var.set("")
            messagebox.showerror("Optimiser failed", str(error))
            return

        sets, reasons = result.sets, result.reasons
        if not sets:
            self.status_var.set("")
            # The reason matters more here than anywhere: strict mode means an
            # empty result is a normal outcome, not a malfunction, and without
            # this the user is told only that nothing worked.
            detail = "\n".join(f"\u2022 {line}" for line in reasons or [])
            messagebox.showinfo(
                "Optimiser",
                "No gear set meets these requirements."
                + (f"\n\n{detail}" if detail else "")
                + "\n\nTick 'Allow sets missing a required skill' to search "
                "without the requirement.",
            )
            return

        self.status_var.set(f"Optimiser found {len(sets)} gear sets.")
        self.gear_result = result
        self.gear_sets = sets
        self.gear_scoring = result.scoring
        self.gear_set_index = 0
        status = getattr(self, "results_status_var", None)
        if status is not None:
            status.set("")  # an export message from the previous run
        self._show_results_window()

    def _show_results_window(self) -> None:
        if self.results_window is None or not self.results_window.winfo_exists():
            window = tk.Toplevel(self.root)
            window.title("Optimiser Results")
            window.geometry("780x640")
            window.protocol("WM_DELETE_WINDOW", self._close_results_window)
            self.results_window = window

            nav = ttk.Frame(window, padding=8)
            nav.pack(side=tk.TOP, fill=tk.X)
            self.prev_button = ttk.Button(
                nav, text="< Previous", command=self._show_prev_set
            )
            self.prev_button.pack(side=tk.LEFT)
            self.set_position_var = tk.StringVar()
            ttk.Label(nav, textvariable=self.set_position_var, anchor=tk.CENTER).pack(
                side=tk.LEFT, fill=tk.X, expand=True
            )
            self.next_button = ttk.Button(nav, text="Next >", command=self._show_next_set)
            self.next_button.pack(side=tk.RIGHT)

            exports = ttk.Frame(window, padding=(8, 0, 8, 8))
            exports.pack(side=tk.BOTTOM, fill=tk.X)
            ttk.Button(exports, text="Copy This Set", command=self._copy_current_set).pack(
                side=tk.LEFT
            )
            ttk.Button(exports, text="Save All Sets...", command=self._save_results).pack(
                side=tk.LEFT, padx=(8, 0)
            )
            self.results_status_var = tk.StringVar(value="")
            ttk.Label(
                exports, textvariable=self.results_status_var, style="Status.TLabel"
            ).pack(side=tk.LEFT, padx=(12, 0))

            view_frame = ttk.Frame(window)
            view_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
            view_frame.rowconfigure(0, weight=1)
            view_frame.columnconfigure(0, weight=1)
            self.results_canvas = tk.Canvas(view_frame, highlightthickness=0)
            self.results_canvas.grid(row=0, column=0, sticky="nsew")
            yscroll = ttk.Scrollbar(
                view_frame, orient=tk.VERTICAL, command=self.results_canvas.yview
            )
            yscroll.grid(row=0, column=1, sticky="ns")
            xscroll = ttk.Scrollbar(
                view_frame, orient=tk.HORIZONTAL, command=self.results_canvas.xview
            )
            xscroll.grid(row=1, column=0, sticky="ew")
            self.results_canvas.config(
                yscrollcommand=yscroll.set, xscrollcommand=xscroll.set
            )
            self.results_canvas.bind(
                "<MouseWheel>",
                lambda e: self.results_canvas.yview_scroll(-e.delta // 120, "units"),
            )
            self._results_sized = False
            self._theme_results_window()
        else:
            self.results_window.deiconify()
            self.results_window.lift()

        self._render_current_set()

    def _fit_results_window(self, width: int, height: int) -> None:
        """Size the window to the first set drawn, within the screen.

        Only once per window: after that the size is the user's, and a set a
        little taller than the last should scroll rather than make it jump.
        """
        window = self.results_window
        window.update_idletasks()
        chrome_w = window.winfo_reqwidth() - self.results_canvas.winfo_reqwidth()
        chrome_h = window.winfo_reqheight() - self.results_canvas.winfo_reqheight()
        max_w = window.winfo_screenwidth() - SCREEN_MARGIN
        max_h = window.winfo_screenheight() - SCREEN_MARGIN
        window.geometry(
            f"{min(width + chrome_w, max_w)}x{min(height + chrome_h, max_h)}"
        )
        self._results_sized = True

    def _close_results_window(self) -> None:
        if self.results_window is not None:
            self.results_window.destroy()
        self.results_window = None

    def _render_current_set(self) -> None:
        total = len(self.gear_sets)
        if total == 0 or self.gear_scoring is None:
            return
        view = set_view(
            self.gear_sets[self.gear_set_index],
            self.gear_set_index + 1,
            total,
            self.gear_scoring,
        )
        width, height = draw_set(self.results_canvas, view, self.dark_var.get())
        self.results_canvas.config(scrollregion=(0, 0, width, height))
        if not self._results_sized:
            self._fit_results_window(width, height)

        self.set_position_var.set(f"Set {self.gear_set_index + 1} of {total}")
        self.prev_button.config(
            state=tk.NORMAL if self.gear_set_index > 0 else tk.DISABLED
        )
        self.next_button.config(
            state=tk.NORMAL if self.gear_set_index < total - 1 else tk.DISABLED
        )

    # --- exporting results ----------------------------------------------------

    def _current_set_text(self) -> str:
        return render_set_inline(
            self.gear_sets[self.gear_set_index],
            self.gear_set_index + 1,
            len(self.gear_sets),
            self.gear_scoring,
        )

    def _copy_current_set(self) -> None:
        if not self.gear_sets:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._current_set_text())
        self.results_status_var.set(f"Set {self.gear_set_index + 1} copied.")

    def _results_as_text(self) -> str:
        """Every set in the console layout, header included - the same text
        optimiser.py prints, so a GUI export and a CLI run read alike."""
        result = self.gear_result
        request = result.request
        by_name = {p.name: p for p in self.game_data.armor}
        pinned = {
            slot: by_name[name]
            for slot, name in request.pinned_pieces.items()
            if slot != TALISMAN_SLOT
        }
        talisman_name = request.pinned_pieces.get(TALISMAN_SLOT)
        pinned_talisman = next(
            (
                t
                for t in [*self.game_data.talismans, *request.custom_talismans]
                if t.name == talisman_name
            ),
            None,
        )
        return render_console(
            result.sets,
            result.scoring,
            result.constraint_level,
            request.source_label,
            pinned=pinned,
            pinned_talisman=pinned_talisman,
            strict=request.strict,
            excluded=request.excluded_sets + request.excluded_pieces,
            filters=request.filters.describe(),
        )

    def _write_results(self, path: Path) -> None:
        """YAML for .yaml/.yml, the console text for anything else.

        YAML is the same structure optimiser.py writes, for anything reading
        results back; text is for reading or pasting.
        """
        result = self.gear_result
        if path.suffix.lower() in (".yaml", ".yml"):
            write_yaml(
                result.sets,
                result.scoring,
                result.constraint_level,
                result.request.source_label,
                path,
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._results_as_text() + "\n", encoding="utf-8")

    def _save_results(self) -> None:
        if not self.gear_sets:
            return
        OPTIMISER_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        path_str = filedialog.asksaveasfilename(
            parent=self.results_window,
            title="Save optimiser results",
            initialdir=str(OPTIMISER_OUTPUTS_DIR),
            initialfile=gear_set_filename(self.current_path),
            defaultextension=".yaml",
            filetypes=[("YAML results", "*.yaml *.yml"), ("Text", "*.txt")],
        )
        if not path_str:
            return
        path = Path(path_str)
        self._write_results(path)
        self.results_status_var.set(f"Saved {len(self.gear_sets)} sets to {path.name}.")

    def _show_prev_set(self) -> None:
        if self.gear_set_index > 0:
            self.gear_set_index -= 1
            self._render_current_set()

    def _show_next_set(self) -> None:
        if self.gear_set_index < len(self.gear_sets) - 1:
            self.gear_set_index += 1
            self._render_current_set()


def main() -> None:
    root = tk.Tk()
    SkillsGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
