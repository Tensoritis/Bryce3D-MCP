"""Keyboard commands, with how well each one is known.

source="tested"     : pressed on a real copy of Bryce 3D and the effect observed.
source="menu_table" : read from the menu definitions in Bryce's Data/menus.dat,
                      which give each item's label, shortcut and command code.
source="unverified" : taken from shortcut lists for later versions of Bryce.
See "How the files work" in README.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    keys: str
    description: str
    source: str = "menu_table"
    destructive: bool = False


COMMANDS: dict[str, Command] = {
    "object_attributes": Command("ctrl+alt+e", "Open Object Attributes", "tested"),
    "materials_lab": Command("ctrl+m", "Edit Material (Materials Lab)", "tested"),
    "duplicate": Command("ctrl+d", "Duplicate the selection in place", "tested"),
    "select_all": Command("ctrl+a", "Select all objects", "tested"),
    "undo": Command("ctrl+z", "Undo the last action", "tested"),
    "delete": Command("delete", "Delete the selection", "tested", True),
    "invert_selection": Command("ctrl+shift+a", "Invert Selection"),
    "group": Command("ctrl+g", "Group Objects"),
    "ungroup": Command("ctrl+u", "UnGroup Objects"),
    "edit_object": Command("ctrl+e", "Edit Object (Terrain Editor for a terrain)"),
    "show_as_box": Command("ctrl+b", "Show Object As Box"),
    "show_as_lattice": Command("ctrl+l", "Show Object As Lattice"),
    "replicate": Command("alt+d", "Replicate"),
    "multi_replicate": Command("alt+shift+d", "Multi-Replicate"),
    "create_path": Command("alt+p", "Create Path"),
    "edit_2d_pictures": Command("ctrl+alt+m", "Edit 2d Pictures"),
    "edit_3d_textures": Command("alt+m", "Edit 3d Textures"),
    "copy_material": Command("ctrl+alt+c", "Copy Material"),
    "paste_material": Command("ctrl+alt+v", "Paste Material"),
    "copy_matrix": Command("alt+c", "Copy Matrix (size, rotation, position)"),
    "paste_matrix": Command("alt+v", "Paste Matrix"),
    "copy": Command("ctrl+c", "Copy"),
    "paste": Command("ctrl+v", "Paste"),
    "cut": Command("ctrl+x", "Cut", destructive=True),
    "render": Command("ctrl+r", "Render"),
    "clear_and_render": Command("ctrl+alt+r", "Clear and Render"),
    "render_animation": Command("ctrl+shift+r", "Render Animation"),
    "flyaround": Command("ctrl+y", "Flyaround"),
    "motion_lab": Command("ctrl+t", "Motion Lab"),
    "document_setup": Command("ctrl+alt+n", "Document Setup"),
    "animation_setup": Command("ctrl+shift+n", "Animation Setup"),
    "new_document": Command("ctrl+n", "New Document", destructive=True),
    "print": Command("ctrl+p", "Print"),
    "sky_lab": Command("ctrl+k", "Open the Sky Lab", "unverified"),
    "next_object": Command("tab", "Select the next object", "unverified"),
    "view_camera": Command("1", "Camera view", "unverified"),
    "view_top": Command("2", "Top view", "unverified"),
    "view_right": Command("3", "Right view", "unverified"),
    "view_front": Command("4", "Front view", "unverified"),
    "zoom_in": Command("plus", "Zoom in", "unverified"),
    "zoom_out": Command("minus", "Zoom out", "unverified"),
}
# Combinations that could leave Bryce or close it.
BLOCKED_KEYS = {"alt+f4", "alt+tab", "ctrl+escape", "ctrl+shift+escape", "ctrl+alt+delete"}
# Raw key presses that need confirm=True.
DESTRUCTIVE_KEYS = {c.keys for c in COMMANDS.values() if c.destructive} | {
    "ctrl+o", "ctrl+q", "ctrl+w", "ctrl+s",
}


def is_blocked(combo: str) -> bool:
    """For a normalized combination."""
    *modifiers, key = combo.split("+")
    return (combo in BLOCKED_KEYS or (key == "tab" and "alt" in modifiers)
            or (key == "escape" and ("ctrl" in modifiers or "alt" in modifiers))
            or (key in ("f4", "space") and "alt" in modifiers))


def is_destructive(combo: str) -> bool:
    return combo in DESTRUCTIVE_KEYS or combo.split("+")[-1] in ("delete", "backspace")


def normalize_keys(keys: str) -> str:
    """Canonical form: lower case, modifiers first in a fixed order."""
    aliases = {"control": "ctrl", "del": "delete", "esc": "escape", "return": "enter"}
    parts = [aliases.get(p, p) for p in (s.strip().lower() for s in keys.split("+")) if p]
    if not parts:
        raise ValueError("empty key combination")
    order = ["ctrl", "alt", "shift"]
    modifiers = [m for m in order if m in parts]
    others = [p for p in parts if p not in order]
    if len(others) != 1:
        raise ValueError(f"need exactly one non-modifier key in {keys!r} (use 'plus' for +)")
    if others[0] in {"win", "meta", "super", "cmd"}:
        raise ValueError("the Windows key is not allowed")
    return "+".join(modifiers + others)
