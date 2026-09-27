"""Draws one gear set onto a Tk Canvas for the GUI's results window.

What to show comes from optimiser_report.set_view; this module is only
presentation - fonts, colours and geometry. The plain-text renderers stay the
source for copying and saving, since a picture cannot be pasted into a chat.

Layout, top to bottom:
    header band        set number, tier, score
    Equipment          one row per piece, decoration slots in aligned columns
    Skills | Set bonuses / Slots / Elemental resistance (bottom right)
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont

from optimiser_report import ELEMENTS, SetView, SlotCell

FAMILY = "Segoe UI"
BODY = (FAMILY, 10)
BOLD = (FAMILY, 10, "bold")
SMALL = (FAMILY, 9)
HEADER = (FAMILY, 14, "bold")
ELEMENT_FONT = (FAMILY, 12, "bold")

MARGIN = 12  # canvas edge to the first box
GAP = 12  # between boxes
PAD = 10  # inside a box, either side of its content
TITLE_H = 24  # a box's title strip
ROW_H = 22
COL_GAP = 16  # between table columns
PIP = 9  # decoration pip diameter
PIP_GAP = 3
MAX_PIPS = 3  # the largest slot in the game
SQUARE = 11  # one level of a skill meter
SQUARE_GAP = 3

# Chosen to read on both the light and dark box background; the black outline
# drawn around each is what keeps the pale ones (ice, thunder) legible on white.
ELEMENT_COLOURS = {
    "fire": "#e53935",
    "water": "#1e63d6",
    "thunder": "#d4a800",
    "ice": "#8fd8ff",
    "dragon": "#9c3fd6",
}

COLOURS = {
    "light": {
        "bg": "#eceef2",
        "box": "#ffffff",
        "border": "#c3c8d0",
        "title_bg": "#dde5f2",
        "title_fg": "#1f3b63",
        "header_bg": "#2c4a78",
        "header_fg": "#ffffff",
        "header_dim": "#c9d6ea",
        "text": "#1a1a1a",
        "dim": "#6b7078",
        "stripe": "#f5f7fa",
        "pip_armour": "#2c6fd1",
        "pip_weapon": "#d97a00",
        "pip_empty": "#b5bac2",
        "meter": "#4d7fc4",
        "meter_empty": "#b5bac2",
        "max": "#2e9e3e",
        "low": "#d23a2e",
        "pin": "#d97a00",
        "tag_bg": "#eef1f6",
    },
    "dark": {
        "bg": "#1f1f1f",
        "box": "#2b2b2b",
        "border": "#444444",
        "title_bg": "#33404f",
        "title_fg": "#cfe0f5",
        "header_bg": "#094771",
        "header_fg": "#ffffff",
        "header_dim": "#a9c7e0",
        "text": "#e6e6e6",
        "dim": "#9a9a9a",
        "stripe": "#303030",
        "pip_armour": "#5b9cf0",
        "pip_weapon": "#f0a040",
        "pip_empty": "#5f5f5f",
        "meter": "#5b8fd6",
        "meter_empty": "#5f5f5f",
        "max": "#4cc35c",
        "low": "#f0584a",
        "pin": "#f0a040",
        "tag_bg": "#383838",
    },
}


class _Painter:
    def __init__(self, canvas: tk.Canvas, colours: dict[str, str]):
        self.c = canvas
        self.col = colours
        self._fonts: dict[tuple, tkfont.Font] = {}

    def measure(self, text: str, font: tuple = BODY) -> int:
        if font not in self._fonts:
            self._fonts[font] = tkfont.Font(root=self.c, font=font)
        return self._fonts[font].measure(text)

    def text(self, x, y, text, font=BODY, fill=None, anchor="w") -> None:
        self.c.create_text(
            x, y, text=text, font=font, fill=fill or self.col["text"], anchor=anchor
        )

    def outlined_text(self, x, y, text, font, fill, anchor="center") -> None:
        """Text with a one-pixel black outline: eight black copies behind it."""
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    self.c.create_text(
                        x + dx, y + dy, text=text, font=font, fill="black", anchor=anchor
                    )
        self.c.create_text(x, y, text=text, font=font, fill=fill, anchor=anchor)

    def box(self, x, y, w, h, title: str) -> None:
        self.c.create_rectangle(
            x, y, x + w, y + h, fill=self.col["box"], outline=self.col["border"]
        )
        self.c.create_rectangle(
            x, y, x + w, y + TITLE_H, fill=self.col["title_bg"], outline=self.col["border"]
        )
        self.text(x + PAD, y + TITLE_H / 2, title, BOLD, self.col["title_fg"])

    def star(self, x, cy, r=6) -> None:
        """The pinned-piece marker, drawn so it needs no font glyph."""
        points = []
        for i in range(10):
            radius = r if i % 2 == 0 else r * 0.45
            angle = math.pi / 2 + i * math.pi / 5
            points += [x + r + radius * math.cos(angle), cy - radius * math.sin(angle)]
        self.c.create_polygon(*points, fill=self.col["pin"], outline=self.col["pin"])

    def pips(self, x, cy, cell: SlotCell) -> None:
        """Filled pips for the jewel's level, hollow ones for unused capacity.

        Weapon slots use diamonds so they cannot be mistaken for armour slots
        on the talisman, the one row where both kinds can sit side by side.
        """
        filled = self.col["pip_weapon" if cell.weapon else "pip_armour"]
        for i in range(cell.size):
            left = x + i * (PIP + PIP_GAP)
            used = i < cell.level
            fill = filled if used else ""
            outline = filled if used else self.col["pip_empty"]
            if cell.weapon:
                r = PIP / 2
                cx = left + r
                self.c.create_polygon(
                    cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy,
                    fill=fill, outline=outline, width=1.5,
                )
            else:
                self.c.create_oval(
                    left, cy - PIP / 2, left + PIP, cy + PIP / 2,
                    fill=fill, outline=outline, width=1.5,
                )


PIPS_W = MAX_PIPS * PIP + (MAX_PIPS - 1) * PIP_GAP


# --- sections ---------------------------------------------------------------


def _equipment_columns(p: _Painter, view: SetView) -> dict[str, int]:
    rows = view.equipment
    slot_count = max((len(r.slots) for r in rows), default=0)
    slot_w = []
    for i in range(slot_count):
        names = [r.slots[i].decoration or "empty" for r in rows if len(r.slots) > i]
        slot_w.append(PIPS_W + 6 + max(p.measure(n) for n in names))
    return {
        "pin": 14,
        "kind": max(p.measure(r.kind) for r in rows),
        "name": max(p.measure(r.name, BOLD) for r in rows),
        "detail": max(p.measure(r.detail, SMALL) for r in rows),
        "def": p.measure("def 000"),
        "slots": slot_w,
    }


def _equipment_width(cols: dict) -> int:
    fixed = cols["pin"] + cols["kind"] + cols["name"] + cols["detail"] + cols["def"]
    return 2 * PAD + fixed + 4 * COL_GAP + sum(w + COL_GAP for w in cols["slots"])


def _draw_equipment(p: _Painter, view: SetView, x, y, w, cols) -> int:
    h = TITLE_H + PAD / 2 + ROW_H * (len(view.equipment) + 1) + PAD
    p.box(x, y, w, h, "Equipment")
    p.text(
        x + w - PAD, y + TITLE_H / 2,
        f"Total defence {view.defence_total}", BOLD, p.col["title_fg"], "e",
    )
    top = y + TITLE_H + PAD / 2
    for i, row in enumerate(view.equipment):
        ry = top + i * ROW_H
        cy = ry + ROW_H / 2
        if i % 2:
            p.c.create_rectangle(
                x + 1, ry, x + w - 1, ry + ROW_H, fill=p.col["stripe"], width=0
            )
        cx = x + PAD
        if row.pinned:
            p.star(cx - 2, cy)
        cx += cols["pin"]
        p.text(cx, cy, row.kind, BODY, p.col["dim"])
        cx += cols["kind"] + COL_GAP
        p.text(cx, cy, row.name, BOLD)
        cx += cols["name"] + COL_GAP
        p.text(cx, cy, row.detail, SMALL, p.col["dim"])
        cx += cols["detail"] + COL_GAP
        if row.defence is not None:
            p.text(cx + cols["def"], cy, f"def {row.defence}", BODY, anchor="e")
        cx += cols["def"] + COL_GAP
        for cell, cw in zip(row.slots, cols["slots"]):
            p.pips(cx, cy, cell)
            if cell.decoration:
                p.text(cx + PIPS_W + 6, cy, cell.decoration)
            else:
                p.text(cx + PIPS_W + 6, cy, "empty", BODY, p.col["dim"])
            cx += cw + COL_GAP

    legend_y = top + len(view.equipment) * ROW_H + ROW_H / 2
    legend = [
        (SlotCell(1, 1, "x"), "armour jewel"),
        (SlotCell(1, 1, "x", weapon=True), "weapon jewel"),
        (SlotCell(1, 0, ""), "unused slot capacity"),
    ]
    lx = x + PAD
    for cell, label in legend:
        p.pips(lx, legend_y, cell)
        p.text(lx + PIP + 5, legend_y, label, SMALL, p.col["dim"])
        lx += PIP + 5 + p.measure(label, SMALL) + 18
    if any(r.pinned for r in view.equipment):
        p.star(lx - 2, legend_y)
        p.text(lx + 14, legend_y, "pinned piece", SMALL, p.col["dim"])
    return h


def _skills_width(p: _Painter, view: SetView) -> int:
    if not view.skills:
        return 2 * PAD + p.measure("No weighted skills")
    name_w = max(p.measure(s.name) for s in view.skills)
    meter_w = max(s.max_level for s in view.skills) * (SQUARE + SQUARE_GAP)
    tags_w = max(p.measure("  ".join(s.tags), SMALL) + 12 * len(s.tags) for s in view.skills)
    return 2 * PAD + name_w + COL_GAP + meter_w + p.measure("0/0", BOLD) + COL_GAP + tags_w


def _draw_skills(p: _Painter, view: SetView, x, y, w, min_h=0) -> int:
    h = max(min_h, TITLE_H + PAD + ROW_H * max(1, len(view.skills)))
    p.box(x, y, w, h, "Skills")
    top = y + TITLE_H + PAD / 2
    if not view.skills:
        p.text(x + PAD, top + ROW_H / 2, "No weighted skills", BODY, p.col["dim"])
        return h
    name_w = max(p.measure(s.name) for s in view.skills)
    meter_x = x + PAD + name_w + COL_GAP
    meter_w = max(s.max_level for s in view.skills) * (SQUARE + SQUARE_GAP)
    for i, skill in enumerate(view.skills):
        cy = top + i * ROW_H + ROW_H / 2
        if skill.maxed:
            colour = p.col["max"]
        elif skill.minimal:
            colour = p.col["low"]
        else:
            colour = p.col["meter"]
        p.text(x + PAD, cy, skill.name, BODY)
        for lvl in range(skill.max_level):
            left = meter_x + lvl * (SQUARE + SQUARE_GAP)
            filled = lvl < skill.level
            p.c.create_rectangle(
                left, cy - SQUARE / 2, left + SQUARE, cy + SQUARE / 2,
                fill=colour if filled else "",
                outline=colour if filled else p.col["meter_empty"],
            )
        label_x = meter_x + meter_w + 4
        p.text(label_x, cy, f"{skill.level}/{skill.max_level}", BOLD, colour)
        tx = label_x + p.measure("0/0", BOLD) + COL_GAP
        for tag in skill.tags:
            tw = p.measure(tag, SMALL) + 10
            p.c.create_rectangle(
                tx, cy - 8, tx + tw, cy + 8,
                fill=p.col["tag_bg"], outline=p.col["border"],
            )
            p.text(tx + tw / 2, cy, tag, SMALL, p.col["dim"], "center")
            tx += tw + 4
    return h


def _bonus_detail(b) -> str:
    weight = f"  ·  w{b.weight:g}" if b.weight else ""
    return f"{b.kind} · {b.pieces}{weight}  —  {b.effects}"


def _side_width(p: _Painter, view: SetView) -> int:
    widths = [
        p.measure(b.name, BOLD) + COL_GAP + p.measure(f"level {b.level}", BOLD)
        for b in view.bonuses
    ]
    widths += [p.measure(_bonus_detail(b), SMALL) for b in view.bonuses]
    widths += [p.measure(n) for n in view.free_slots]
    widths.append(_resistance_cell_w(p) * len(ELEMENTS))
    return 2 * PAD + max(widths)


def _draw_bonuses(p: _Painter, view: SetView, x, y, w) -> int:
    if not view.bonuses:
        return 0
    h = TITLE_H + PAD + ROW_H * 2 * len(view.bonuses)
    p.box(x, y, w, h, "Set / group bonuses")
    cy = y + TITLE_H + PAD / 2 + ROW_H / 2
    for b in view.bonuses:
        p.text(x + PAD, cy, b.name, BOLD)
        p.text(x + w - PAD, cy, f"level {b.level}", BOLD, p.col["title_fg"], "e")
        cy += ROW_H - 4
        p.text(x + PAD, cy, _bonus_detail(b), SMALL, p.col["dim"])
        cy += ROW_H + 4
    return h


def _draw_free_slots(p: _Painter, view: SetView, x, y, w) -> int:
    h = TITLE_H + PAD + ROW_H * len(view.free_slots)
    p.box(x, y, w, h, "Slots")
    cy = y + TITLE_H + PAD / 2 + ROW_H / 2
    for line in view.free_slots:
        p.text(x + PAD, cy, line)
        cy += ROW_H
    return h


def _resistance_cell_w(p: _Painter) -> int:
    return max(p.measure(e.capitalize(), ELEMENT_FONT) for e in ELEMENTS) + 14


RESISTANCE_H = TITLE_H + PAD + 58


def _draw_resistances(p: _Painter, view: SetView, x, y, w) -> int:
    p.box(x, y, w, RESISTANCE_H, "Elemental resistance")
    cell_w = (w - 2 * PAD) / len(ELEMENTS)
    top = y + TITLE_H + PAD / 2
    for i, element in enumerate(ELEMENTS):
        cx = x + PAD + cell_w * i + cell_w / 2
        colour = ELEMENT_COLOURS[element]
        p.outlined_text(cx, top + 14, element.capitalize(), ELEMENT_FONT, colour)
        value = view.resistances[element]
        p.outlined_text(cx, top + 40, f"{value:+d}" if value else "0", HEADER, colour)
    return RESISTANCE_H


def _draw_header(p: _Painter, view: SetView, x, y, w) -> int:
    h = 44
    p.c.create_rectangle(x, y, x + w, y + h, fill=p.col["header_bg"], width=0)
    title = f"Set {view.rank} of {view.total}"
    p.text(x + PAD + 4, y + h / 2, title, HEADER, p.col["header_fg"])
    tx = x + PAD + 4 + p.measure(title, HEADER) + 12
    if view.tier:
        tw = p.measure(view.tier, BOLD) + 14
        p.c.create_rectangle(
            tx, y + h / 2 - 10, tx + tw, y + h / 2 + 10,
            fill="", outline=p.col["header_dim"],
        )
        p.text(tx + tw / 2, y + h / 2, view.tier, BOLD, p.col["header_fg"], "center")
    p.text(
        x + w - PAD - 4, y + h / 2 - 8, f"score {view.total_score:.2f}",
        (FAMILY, 12, "bold"), p.col["header_fg"], "e",
    )
    p.text(
        x + w - PAD - 4, y + h / 2 + 10,
        f"skills {view.skill_score:.2f}  +  defence {view.defence_score:.2f}",
        SMALL, p.col["header_dim"], "e",
    )
    return h


# --- entry point --------------------------------------------------------------


def draw_set(canvas: tk.Canvas, view: SetView, dark: bool) -> tuple[int, int]:
    """Clear the canvas and draw the set. Returns the drawing's (width, height)."""
    colours = COLOURS["dark" if dark else "light"]
    canvas.delete("all")
    canvas.configure(background=colours["bg"])
    p = _Painter(canvas, colours)

    cols = _equipment_columns(p, view)
    left_w = _skills_width(p, view)
    right_w = _side_width(p, view)
    width = max(_equipment_width(cols), left_w + GAP + right_w, 640)
    # The lower row fills the width; any slack goes to the skills column.
    left_w = width - GAP - right_w

    x, y = MARGIN, MARGIN
    y += _draw_header(p, view, x, y, width) + GAP
    y += _draw_equipment(p, view, x, y, width, cols) + GAP

    right_x = x + left_w + GAP
    ry = y
    bonus_h = _draw_bonuses(p, view, right_x, ry, right_w)
    ry += bonus_h + (GAP if bonus_h else 0)
    ry += _draw_free_slots(p, view, right_x, ry, right_w) + GAP
    right_h = ry - y + RESISTANCE_H

    skills_h = _draw_skills(p, view, x, y, left_w, right_h)
    # Resistances sit in the bottom-right corner, level with the skills box's
    # bottom edge however long either column runs.
    _draw_resistances(p, view, right_x, y + skills_h - RESISTANCE_H, right_w)

    return int(width + 2 * MARGIN), int(y + skills_h + MARGIN)
