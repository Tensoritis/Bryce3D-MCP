"""Bryce operations built on a Backend.

The important rule: never type into a dialog without first checking that it is
open. In the main view, digits and letters are shortcuts, so blind typing would
move the camera or change the scene.
"""

from __future__ import annotations

import io
import os
from collections.abc import Sequence

from PIL import Image, ImageChops

from . import layout
from .backend import Backend
from .commands import COMMANDS, is_blocked, is_destructive, normalize_keys
from .errors import BryceError, ConfirmationRequired, UnexpectedScreen

FILE_DIALOG_CLASS = "#32770"
Vector = Sequence[float]


def format_number(value: float) -> str:
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _brightness(rgb: Sequence[int]) -> float:
    return sum(rgb[:3]) / 3


class BryceController:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend

    # -- seeing -------------------------------------------------------------------
    def screenshot_png(self, box=layout.CAPTURE_BOX, scale: float = 1.0) -> bytes:
        image = self.backend.grab(box)
        if scale != 1.0:
            image = image.resize((max(1, round(image.width * scale)),
                                  max(1, round(image.height * scale))), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    def _wait_for(self, predicate, what: str, timeout: float = 5.0) -> None:
        waited = 0.0
        while True:
            if predicate():
                return
            if waited >= timeout:
                raise UnexpectedScreen(f"{what}. Take a screenshot to see Bryce's current state.")
            self.backend.sleep(0.25)
            waited += 0.25

    def _matches(self, probes) -> bool:
        for point, low, high in probes:
            rgb = self.backend.pixel(*point)[:3]
            if not (low <= _brightness(rgb) <= high and max(rgb) - min(rgb) <= 30):
                return False
        return True

    def attributes_open(self) -> bool:
        return self._matches(layout.ATTR_PROBES)

    def materials_open(self) -> bool:
        return self._matches(layout.MAT_PROBES)

    def library_open(self) -> bool:
        return self._matches(layout.LIBRARY_PROBES)

    def _selection_snapshot(self) -> tuple[Image.Image, Image.Image]:
        """The status text and viewport, with the pointer parked off the toolbar."""
        self.backend.move(*layout.VIEWPORT_EMPTY)
        self.backend.sleep(0.5)
        return self.backend.grab(layout.STATUS_BOX), self.backend.grab(layout.VIEWPORT_BOX)

    # -- commands and raw input ---------------------------------------------------
    def run_command(self, name: str, confirm: bool = False) -> None:
        command = COMMANDS.get(name)
        if command is None:
            raise BryceError(f"unknown command {name!r}; see list_commands")
        if command.destructive and not confirm:
            raise ConfirmationRequired(f"{name!r} can discard work; pass confirm=true "
                                       "once the user has agreed")
        self.backend.key(command.keys)
        self.backend.sleep(0.6)

    def press(self, keys: str, confirm: bool = False) -> str:
        combo = normalize_keys(keys)
        if is_blocked(combo):
            raise BryceError(f"{combo} is blocked")
        if is_destructive(combo) and not confirm:
            raise ConfirmationRequired(f"{combo} can discard work; pass confirm=true "
                                       "once the user has agreed")
        self.backend.key(combo)
        self.backend.sleep(0.4)
        return combo

    # -- creating objects ---------------------------------------------------------
    def create(self, kind: str) -> None:
        if kind not in layout.PRIMITIVES:
            raise BryceError(f"unknown primitive {kind!r}; see list_catalog")
        if self.attributes_open() or self.materials_open():
            raise UnexpectedScreen("a dialog is open; close it before creating objects")
        before = self._selection_snapshot()
        self.backend.click(*layout.TAB_CREATE)
        self.backend.sleep(0.5)
        self.backend.click(*layout.PRIMITIVES[kind])
        self.backend.sleep(0.9)
        after = self._selection_snapshot()
        if all(ImageChops.difference(a, b).getbbox() is None for a, b in zip(before, after)):
            # Carrying on would edit whatever was selected before.
            raise UnexpectedScreen(f"clicking {kind!r} changed nothing, so no object was created")

    def open_attributes(self) -> None:
        if not self.attributes_open():
            self.backend.key(COMMANDS["object_attributes"].keys)
        self._wait_for(self.attributes_open,
                       "Object Attributes did not open (is an object selected?)")
        self.backend.sleep(0.3)

    def set_attributes(self, name: str | None = None, position: Vector | None = None,
                       rotation: Vector | None = None, size: Vector | None = None,
                       boolean: str | None = None) -> None:
        validate_attributes(name, position, rotation, size, boolean)
        self.open_attributes()
        if name is not None:
            self.backend.click(*layout.ATTR_NAME, count=3)
            self.backend.type_text(name)
        if boolean is not None:
            self.backend.click(*layout.ATTR_BOOLEAN[boolean])
        # Size goes before position so that resizing cannot shift the final placement.
        for row, vector in (("size", size), ("rotation", rotation), ("position", position)):
            if vector is None:
                continue
            for point, value in zip(layout.ATTR_FIELDS[row], vector):
                if not self.attributes_open():
                    raise UnexpectedScreen("Object Attributes closed while it was being filled in")
                self.backend.click(*point, count=2)
                self.backend.type_text(format_number(value))
        self.backend.click(*layout.ATTR_OK)
        self._wait_for(lambda: not self.attributes_open(),
                       "Object Attributes stayed open; Bryce may have rejected a value")
        self.backend.sleep(0.4)

    # -- libraries and materials ----------------------------------------------------
    def apply_material(self, category: str = "simple&fast", row: int = 0, column: int = 0) -> None:
        key = category.lower()
        if key not in layout.MATERIAL_CATEGORIES:
            raise BryceError(f"unknown material category {category!r}")
        cell = layout.library_cell(row, column)
        self.backend.key(COMMANDS["materials_lab"].keys)
        self._wait_for(self.materials_open,
                       "the Materials Lab did not open (is an object selected?)", 8)
        self.backend.sleep(0.8)
        self.backend.click(*layout.MAT_LIBRARY_ARROW)
        self._wait_for(self.library_open, "the material library did not open")
        self.backend.sleep(0.6)
        self.backend.click(*layout.MATERIAL_CATEGORIES[key])
        self.backend.sleep(0.8)
        self.backend.click(*cell)
        self.backend.sleep(0.6)
        self.backend.click(*layout.LIBRARY_OK)
        self.backend.sleep(1.0)
        self.backend.click(*layout.MAT_OK)
        self._wait_for(lambda: not self.materials_open(), "the Materials Lab did not close", 10)
        self.backend.sleep(0.6)

    def open_object_library(self, category: str) -> None:
        key = category.lower()
        if key not in layout.OBJECT_CATEGORIES:
            raise BryceError(f"unknown object category {category!r}")
        self.backend.click(*layout.TAB_CREATE)
        self.backend.sleep(0.5)
        self.backend.click(*layout.CREATE_LIBRARY_ARROW)
        self._wait_for(self.library_open, "the object library did not open")
        self.backend.sleep(0.6)
        self.backend.click(*layout.OBJECT_CATEGORIES[key])
        self.backend.sleep(0.8)

    def insert_library_object(self, category: str, row: int, column: int) -> None:
        cell = layout.library_cell(row, column)
        self.open_object_library(category)
        self.backend.click(*cell)
        self.backend.sleep(0.6)
        self.backend.click(*layout.LIBRARY_OK)
        self.backend.sleep(2.0)

    # -- rendering --------------------------------------------------------------
    def render(self, timeout: float = 600, settle: float = 6, poll: float = 1.5) -> dict:
        """Start a render and wait until the viewport stops changing."""
        self.backend.click(*layout.RENDER_BUTTON)
        elapsed, still = 0.0, 0.0
        previous = None
        while elapsed < timeout:
            self.backend.sleep(poll)
            elapsed += poll
            current = self.backend.grab(layout.VIEWPORT_BOX)
            same = previous is not None and ImageChops.difference(previous, current).getbbox() is None
            still = still + poll if same else 0.0
            previous = current
            if still >= settle:
                return {"finished": True, "seconds": round(elapsed - still, 1)}
        return {"finished": False, "seconds": round(elapsed, 1)}

    # -- files ------------------------------------------------------------------
    def fill_file_dialog(self, path: str) -> None:
        """Type a path into a Windows file dialog, only if one is really in front."""
        if not path or any(c in path for c in '\r\n\t"'):
            raise BryceError("invalid path")
        self._wait_for(lambda: self.backend.foreground_class() == FILE_DIALOG_CLASS,
                       "no Windows file dialog appeared, so the path was not typed")
        self.backend.key("ctrl+a")
        self.backend.type_text(path)
        self.backend.key("enter")
        self.backend.sleep(1.5)

    def open_scene(self, path: str, confirm: bool = False) -> None:
        if not confirm:
            raise ConfirmationRequired("opening a scene can discard unsaved work; "
                                       "pass confirm=true once the user has agreed")
        if not os.path.isfile(path):
            raise BryceError(f"file not found: {path}")
        self.backend.key("ctrl+o")
        self.fill_file_dialog(os.path.abspath(path))

    def save_scene_as(self, path: str, overwrite: bool = False) -> str:
        target = os.path.abspath(path)
        if not os.path.splitext(target)[1]:
            target += ".br3"
        if os.path.exists(target) and not overwrite:
            raise ConfirmationRequired(f"{target} exists; pass overwrite=true once the user has agreed")
        if not os.path.isdir(os.path.dirname(target)):
            raise BryceError(f"folder does not exist: {os.path.dirname(target)}")
        self.backend.key("ctrl+shift+s")
        self.fill_file_dialog(target)
        if overwrite and self.backend.foreground_class() == FILE_DIALOG_CLASS:
            self.backend.key("alt+y")  # "Yes" in the replace-file prompt
            self.backend.sleep(1.0)
        return target

    # -- native menus --------------------------------------------------------------
    def invoke_menu(self, path: str, confirm: bool = False) -> str:
        wanted = _menu_key(path)
        items = self.backend.menu()
        if not items:
            raise BryceError("this Bryce window exposes no native Windows menu; "
                             "use screenshots with click and hover instead")
        matches = [i for i in items if _menu_key(i["path"]) == wanted]
        if len(matches) != 1:
            raise BryceError(f"menu item {path!r} not found; see list_menus")
        item = matches[0]
        if not item["enabled"]:
            raise BryceError(f"menu item {item['path']!r} is disabled right now")
        risky = ("new", "open", "revert", "delete", "clear", "cut", "exit", "quit", "close", "save")
        if any(word in _menu_key(item["path"].split("/")[-1]) for word in risky) and not confirm:
            raise ConfirmationRequired(f"{item['path']!r} may change files or discard work; "
                                       "pass confirm=true once the user has agreed")
        self.backend.invoke_menu(item["id"])
        self.backend.sleep(1.0)
        return item["path"]


def _menu_key(path: str) -> str:
    return "/".join(part.strip().rstrip(". …").lower() for part in path.split("/"))


def validate_attributes(name=None, position=None, rotation=None, size=None, boolean=None) -> None:
    if name is not None and (not 0 < len(name) <= 31 or not name.isascii() or not name.isprintable()):
        raise BryceError("name must be 1-31 printable ASCII characters")
    for label, vector in (("position", position), ("rotation", rotation), ("size", size)):
        if vector is None:
            continue
        if len(vector) != 3:
            raise BryceError(f"{label} needs exactly 3 numbers (x, y, z)")
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not -100000 <= value <= 100000:
                raise BryceError(f"{label} values must be finite numbers within +/-100000")
    if size is not None and any(float(format_number(v)) <= 0 for v in size):
        raise BryceError("size values must be at least 0.01")
    if boolean is not None and boolean not in layout.ATTR_BOOLEAN:
        raise BryceError(f"boolean must be one of {', '.join(layout.ATTR_BOOLEAN)}")
