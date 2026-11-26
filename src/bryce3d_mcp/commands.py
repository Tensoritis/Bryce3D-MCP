"""Keyboard commands, with how well each one is known.

verified=True  : pressed on a real copy of Bryce 3D and the effect observed.
verified=False : taken from published Bryce shortcut lists for later versions
                 (see docs/UI_MAP.md). Check the screenshot after using one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    keys: str
    description: str
    verified: bool = False
    destructive: bool = False


COMMANDS: dict[str, Command] = {
    "object_attributes": Command("ctrl+alt+e", "Open Object Attributes", True),
    "materials_lab": Command("ctrl+m", "Open the Materials Lab", True),
    "duplicate": Command("ctrl+d", "Duplicate the selection in place", True),
    "select_all": Command("ctrl+a", "Select all objects", True),
    "undo": Command("ctrl+z", "Undo the last action", True),
    "delete": Command("delete", "Delete the selection", True, True),
    "group": Command("ctrl+g", "Group the selection"),
    "ungroup": Command("ctrl+u", "Ungroup the selection"),
    "edit_object": Command("ctrl+e", "Open the object's editor (Terrain Editor)"),
    "render": Command("ctrl+r", "Render or resume rendering"),
    "clear_and_render": Command("ctrl+alt+r", "Clear and render"),
    "sky_lab": Command("ctrl+k", "Open the Sky Lab"),
    "next_object": Command("tab", "Select the next object"),
    "view_camera": Command("1", "Camera view"),
    "view_top": Command("2", "Top view"),
    "view_right": Command("3", "Right view"),
    "view_front": Command("4", "Front view"),
    "zoom_in": Command("plus", "Zoom in"),
    "zoom_out": Command("minus", "Zoom out"),
    "copy": Command("ctrl+c", "Copy"),
    "paste": Command("ctrl+v", "Paste"),
    "cut": Command("ctrl+x", "Cut", destructive=True),
    "document_setup": Command("ctrl+alt+n", "Document Setup"),
    "new_document": Command("ctrl+n", "New document", destructive=True),
    "revert": Command("ctrl+alt+s", "Revert to saved", destructive=True),
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
