"""Bryce operations built on a Backend.

The important rule: never type into a dialog without first checking that it is
open. In the main view, digits and letters are shortcuts, so blind typing would
move the camera or change the scene.
"""

from __future__ import annotations

import io
import os
import struct
from collections.abc import Sequence

import numpy as np
from PIL import Image, ImageChops

from . import layout
from .backend import Backend
from .ccmf import CompositeFile
from .commands import COMMANDS, is_blocked, is_destructive, normalize_keys
from .errors import BryceError, ConfirmationRequired, UnexpectedScreen

FILE_DIALOG_CLASS = "#32770"
RENDER_MODES = {"button": None, "resume": "Render", "clear": "Clear and Render"}
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
    def start_render(self, mode: str = "button") -> None:
        """button: click the large render sphere (uses the mouse). resume and clear:
        File/Render and File/Clear and Render from Bryce's menu, without input."""
        if mode not in RENDER_MODES:
            raise BryceError(f"render mode must be one of {', '.join(RENDER_MODES)}")
        if mode == "button" and (self.attributes_open() or self.materials_open()
                                 or self.library_open()):
            raise UnexpectedScreen("a dialog is open; close it before rendering")
        if mode == "button":
            self.backend.click(*layout.RENDER_BUTTON)
        else:
            self.command("File/" + RENDER_MODES[mode])

    def wait_for_still_picture(self, timeout: float = 600, settle: float = 6,
                               poll: float = 1.5) -> dict:
        """Wait until the viewport stops changing for `settle` seconds. A blank
        picture never counts: Bryce shows plain white before it has rendered."""
        elapsed, still = 0.0, 0.0
        previous = None
        while elapsed < timeout:
            self.backend.sleep(poll)
            elapsed += poll
            current = self.backend.grab(layout.VIEWPORT_BOX)
            same = previous is not None and _same_picture(previous, current)
            still = still + poll if same else 0.0
            previous = current
            if still >= settle and not is_blank(current):
                return {"finished": True, "seconds": round(elapsed - still, 1)}
        result = {"finished": False, "seconds": round(elapsed, 1)}
        if previous is not None and is_blank(previous):
            result["reason"] = "the picture is blank, so nothing has been rendered"
        return result

    def watch_render(self, start, timeout: float = 600, settle: float = 8,
                     poll: float = 2.0, start_wait: float = 30) -> dict:
        """Call start() and follow the render. Done means: Bryce showed signs of
        working, has been quiet for `settle` seconds, and the picture is not blank.
        With the bridge, Bryce's own activity counters are used as well as the
        picture, and a timeline is returned to help tune this."""
        def activity():
            state = self.backend.activity()
            return None if state is None else (state["blits"], state["checks"])

        first = activity()
        self.backend.sleep(poll)
        second = activity()
        idle = (0, 0) if first is None else tuple(b - a for a, b in zip(first, second))
        previous = self.backend.grab(layout.VIEWPORT_BOX)
        windows = self.backend.window_ids()
        start()
        last_counts = second
        elapsed, quiet, started = 0.0, 0.0, False
        timeline: list[dict] = []
        while elapsed < timeout:
            self.backend.sleep(poll)
            elapsed += poll
            current = self.backend.grab(layout.VIEWPORT_BOX)
            changed = not _same_picture(previous, current)
            previous = current
            counts = activity()
            busy = changed
            sample = {"t": round(elapsed, 1), "changed": changed, "blank": is_blank(current)}
            if counts is not None:
                blits, checks = (b - a for a, b in zip(last_counts, counts))
                last_counts = counts
                sample.update(blits=blits, checks=checks)
                busy = busy or blits > idle[0] * 1.5 + 2 or checks > idle[1] * 1.5 + 5
            new_box = bool(self.backend.window_ids() - windows)
            sample["new_box"] = new_box
            if len(timeline) < 60 or changed or new_box:
                timeline.append(sample)
            started = started or busy
            quiet = 0.0 if busy else quiet + poll
            result = {"seconds": round(elapsed - quiet, 1), "new_box": new_box,
                      "idle_per_sample": list(idle), "timeline": timeline[-200:]}
            if started and quiet >= settle and not is_blank(current):
                return {"finished": True, "started": True, **result}
            if not started and elapsed >= start_wait:
                return {"finished": False, "started": False, **result,
                        "reason": "Bryce showed no sign of rendering"}
            if started and quiet >= max(settle * 4, 90):
                return {"finished": False, "started": True, **result,
                        "reason": "Bryce went quiet with a blank picture"}
        return {"finished": False, "started": started, "seconds": round(elapsed, 1),
                "timeline": timeline[-200:], "reason": "timed out"}

    def render(self, timeout: float = 600, settle: float = 6, poll: float = 1.5,
               mode: str = "button") -> dict:
        self.start_render(mode)
        return self.wait_for_still_picture(timeout, settle, poll)

    def render_picture(self) -> Image.Image:
        """The viewport as it is on screen now."""
        return self.backend.grab(layout.VIEWPORT_BOX)

    # -- menu commands and file dialogs: window messages only ---------------------
    def menu_item(self, name: str) -> dict:
        """The native menu item named by "Menu/Item", an item label, or a name such
        as clear_and_render."""
        wanted = _menu_key(name.replace("_", " "))
        items = self.backend.menu()
        if not items:
            raise BryceError("Bryce's menu could not be read")
        matches = [i for i in items if _menu_key(i["path"]) == wanted] or [
            i for i in items if _menu_key(i["path"]).split("/")[-1] == wanted]
        if len(matches) != 1:
            raise BryceError(f"menu item {name!r} not found; see list_menus")
        return matches[0]

    def command(self, name: str, confirm: bool = False) -> str:
        """Run any item of Bryce's menu without focus, mouse or keyboard."""
        item = self.menu_item(name)
        if _menu_key(item["path"]).split("/")[-1] in NEEDS_CONFIRM and not confirm:
            raise ConfirmationRequired(f"{item['path']!r} may change files or discard work; "
                                       "pass confirm=true once the user has agreed")
        if not item["enabled"]:
            raise BryceError(f"menu item {item['path']!r} is disabled right now")
        if self.backend.dialogs():
            raise UnexpectedScreen("Bryce has a Windows dialog open; see bryce_dialogs")
        self.backend.run_menu(item)
        self.backend.sleep(1.0)
        return item["path"]

    def dialogs(self) -> list[dict]:
        return [{k: v for k, v in d.items() if k != "window"} for d in self.backend.dialogs()]

    def press_dialog_button(self, label: str, confirm: bool = False) -> str:
        if not confirm:
            raise ConfirmationRequired("answering a Bryce question can overwrite or discard "
                                       "work; pass confirm=true once the user has agreed")
        wanted = label.replace("&", "").strip().lower()
        for dialog in self.backend.dialogs():
            for button in dialog["buttons"]:
                if button["label"].strip().lower() == wanted:
                    self.backend.press_dialog_button(dialog["window"], button["id"])
                    self.backend.sleep(1.0)
                    return button["label"]
        raise BryceError(f"no open dialog has a button {label!r}; see bryce_dialogs")

    def fill_file_dialog(self, path: str) -> dict:
        """Put a path in the open Windows file dialog and accept it."""
        if not path or any(c in path for c in '\r\n\t"*?<>|'):
            raise BryceError("invalid path")
        found: list[dict] = []

        def ready():
            found[:] = [d for d in self.backend.dialogs() if d["takes_path"]]
            return bool(found)

        self._wait_for(ready, "no Windows file dialog appeared", timeout=8)
        self.backend.fill_dialog(found[0]["window"], path)
        self.backend.sleep(1.5)
        return {"submitted": path, "dialogs_still_open": self.dialogs()}

    # -- saving and loading -------------------------------------------------------
    def save_box(self) -> dict | None:
        """Where the buttons of Bryce's "Do you want to save your changes?" box
        are, or None. Bryce draws this box itself, so it is found in the picture."""
        return find_save_box(self.backend.grab(layout.CAPTURE_BOX), layout.CAPTURE_BOX[:2])

    def answer_save_box(self, choice: str, confirm: bool = False) -> dict:
        """Press "dont_save", "cancel" or "save" on that box. Needs input: the
        bridge's virtual mouse, so the user's own mouse is not used."""
        if choice not in SAVE_BOX_CHOICES:
            raise BryceError(f"choice must be one of {', '.join(SAVE_BOX_CHOICES)}")
        if choice != "cancel" and not confirm:
            raise ConfirmationRequired("this discards or overwrites work; pass "
                                       "confirm=true once the user has agreed")
        buttons = self.save_box()
        if buttons is None:
            raise UnexpectedScreen("Bryce's save question is not on screen; take a screenshot")
        self.backend.begin()
        try:
            self.backend.click(*buttons[choice])
            self.backend.sleep(1.5)
        finally:
            self.backend.end()
        if self.save_box() is not None:
            raise UnexpectedScreen("the box is still there after pressing the button")
        return {"answered": choice, "dialogs": self.dialogs()}

    def open_scene(self, path: str, confirm: bool = False,
                   discard_changes: bool = False) -> dict:
        """Hand Bryce a file the way a second launch of Bryce would. Unsaved work in
        Bryce is never discarded unless discard_changes is set."""
        if not confirm:
            raise ConfirmationRequired("opening a scene can discard unsaved work; "
                                       "pass confirm=true once the user has agreed")
        target = os.path.abspath(path)
        if os.path.splitext(target)[1].lower() != ".br3":
            raise BryceError("only .br3 scenes can be opened this way")
        if not os.path.isfile(target):
            raise BryceError(f"file not found: {target}")
        if self.save_box() is not None:
            raise UnexpectedScreen("Bryce is already asking whether to save. Answer it first "
                                   "(answer_save_box). Nothing was sent.")
        unsaved = self.backend.unsaved_changes()
        if unsaved and not discard_changes:
            raise ConfirmationRequired(
                "Bryce has unsaved changes. Save them first (save_scene), or pass "
                "discard_changes=true once the user has agreed to lose them. Nothing "
                "was opened.")
        if discard_changes and unsaved is not None:
            self.backend.forget_changes()
        windows = self.backend.window_ids()
        failure = None
        try:
            self.backend.open_file(target)
        except BryceError as exc:          # a question box can keep Bryce from answering
            failure = exc
        self.backend.sleep(1.5)
        asking = self.save_box() is not None
        if asking and discard_changes and self.backend.activity() is not None:
            self.answer_save_box("dont_save", confirm=True)
            asking, failure = False, None
        if asking or self.backend.window_ids() - windows:
            raise UnexpectedScreen(
                "Bryce put up a box of its own instead of just opening the file, usually "
                "'Do you want to save your changes?'. See screenshot. Answer it with "
                "answer_save_box (needs the bridge) or in Bryce itself; Cancel keeps "
                "everything as it was.")
        if failure:
            raise failure
        return {"sent": target, "dialogs": self.dialogs()}

    def load_file(self, path: str, how: str = "open", unsaved: str = "stop",
                  save_current_to: str | None = None, confirm: bool = False) -> dict:
        """Load a file into Bryce: open a scene, merge one into the current scene,
        or import a model. `unsaved` says what happens to unsaved work when opening."""
        if how not in ("open", "merge", "import_object"):
            raise BryceError("how must be open, merge or import_object")
        if unsaved not in ("stop", "save", "discard"):
            raise BryceError("unsaved must be stop, save or discard")
        if not confirm:
            raise ConfirmationRequired("loading changes the scene open in Bryce; pass "
                                       "confirm=true once the user has agreed")
        target = os.path.abspath(path)
        if not os.path.isfile(target):
            raise BryceError(f"file not found: {target}")
        if how != "open":                     # adds to the scene, discards nothing
            command = "File/" + how.replace("_", " ")
            return {"how": how, **self.run_with_path(command, target)}
        result: dict = {"how": "open"}
        if unsaved == "save":
            if self.backend.unsaved_changes() is False:
                result["saved_first"] = "not needed: nothing was unsaved"
            elif save_current_to is None:
                raise BryceError("give save_current_to, the file to save the current scene "
                                 "as before opening the other one")
            else:
                result["saved_first"] = self.save_scene(save_current_to, confirm=True)
        # After a verified Save As, anything still marked unsaved is not user work.
        discard = unsaved == "discard" or (
            isinstance(result.get("saved_first"), dict)
            and result["saved_first"].get("verified") is True)
        result.update(self.open_scene(target, True, discard))
        return result

    def save_scene(self, path: str | None = None, confirm: bool = False,
                   overwrite: bool = False, timeout: float = 120) -> dict:
        """Save over the scene's file, or with a path, Save As under that name. The
        written file is checked before this reports success."""
        if not confirm:
            raise ConfirmationRequired("saving writes files; pass confirm=true once the "
                                       "user has agreed")
        if self.save_box() is not None:
            raise UnexpectedScreen("Bryce is asking whether to save; use answer_save_box")
        if path is None:
            self.command("File/Save", confirm=True)
            self.backend.sleep(1.0)
            open_dialogs = self.dialogs()
            if any(d.get("takes_path") for d in open_dialogs):
                return {"saved": None, "verified": False, "dialogs": open_dialogs,
                        "note": "this scene has no file yet, so Bryce asks for a name; "
                                "use submit_file_dialog, or save_scene with a path"}
            unsaved = self.backend.unsaved_changes()
            return {"saved": "File/Save", "dialogs": open_dialogs,
                    "verified": None if unsaved is None else not unsaved}
        target = os.path.abspath(path)
        if not os.path.splitext(target)[1]:
            target += ".br3"
        if os.path.splitext(target)[1].lower() != ".br3":
            raise BryceError("scenes are saved as .br3 files")
        if os.path.exists(target) and not overwrite:
            raise BryceError(f"{target} already exists; choose a new name or pass "
                             "overwrite=true once the user has agreed")
        if not os.path.isdir(os.path.dirname(target)):
            raise BryceError(f"folder does not exist: {os.path.dirname(target)}")
        before = _stamp(target)
        self.command("File/Save As", confirm=True)
        self.fill_file_dialog(target)
        waited, last = 0.0, None
        while waited <= timeout:
            if before is not None and overwrite:      # Windows asks "replace it?"
                for dialog in self.backend.dialogs():
                    for button in dialog["buttons"]:
                        if button["label"].strip().lower() == "yes":
                            self.backend.press_dialog_button(dialog["window"], button["id"])
            now = _stamp(target)
            if now is not None and now != before and now == last and _is_scene(target):
                return {"saved": target, "bytes": now[0], "verified": True,
                        "dialogs": self.dialogs()}
            last = now
            self.backend.sleep(1.0)
            waited += 1.0
        raise UnexpectedScreen(
            f"Bryce did not write {target}. Dialogs open: {self.dialogs()}. Take a "
            "screenshot to see why.")

    def run_with_path(self, command: str, path: str, extension: str = "",
                      new_file: bool = False) -> dict:
        """Run a command that asks for a file, and answer with `path`."""
        target = os.path.abspath(path)
        if extension and not os.path.splitext(target)[1]:
            target += extension
        if new_file and os.path.exists(target):
            raise BryceError(f"{target} already exists; choose a new name")
        if new_file and not os.path.isdir(os.path.dirname(target)):
            raise BryceError(f"folder does not exist: {os.path.dirname(target)}")
        if not new_file and not os.path.isfile(target):
            raise BryceError(f"file not found: {target}")
        self.command(command, confirm=True)
        result = self.fill_file_dialog(target)
        result["file_exists"] = os.path.isfile(target)
        return result

    # -- rendering without input --------------------------------------------------
    def open_and_render(self, path: str, confirm: bool = False, timeout: float = 600,
                        settle: float = 8, poll: float = 2,
                        discard_changes: bool = False) -> dict:
        """Open a scene in the running Bryce, render it from nothing and wait."""
        opened = self.open_scene(path, confirm, discard_changes)
        if opened["dialogs"]:
            raise UnexpectedScreen(f"Bryce asked a question while opening: {opened['dialogs']}. "
                                   "Answer it with press_dialog_button, then try again.")
        self.wait_for_still_picture(timeout=30, settle=3, poll=1)   # let it load
        result = self.watch_render(lambda: self.command("File/Clear and Render"),
                                   timeout, settle, poll)
        result["started_by"] = "menu"
        if not result["started"] and self.backend.activity() is not None:
            # The bridge can press the render button without the user's mouse.
            result = self.watch_render(self._press_render_button, timeout, settle, poll)
            result["started_by"] = "render button (virtual mouse)"
        result["dialogs"] = self.dialogs()
        return result

    def render_to_file(self, scene: str, picture: str, timeout: float = 600,
                       discard_changes: bool = False, settle: float = 20,
                       poll: float = 4) -> dict:
        """One scene of a series: open, render, close the Render Report box and keep
        the picture. The caller must already have the user's agreement."""
        result = self.open_and_render(scene, confirm=True, timeout=timeout,
                                      settle=settle, poll=poll,
                                      discard_changes=discard_changes)
        if result["finished"]:
            if result.get("new_box") and self.backend.activity() is not None:
                self.backend.begin()
                try:
                    self.backend.click(*layout.RENDER_REPORT_OK)
                    self.backend.sleep(2)
                finally:
                    self.backend.end()
            self.render_picture().save(picture)
            result["picture"] = picture
        return result

    def _press_render_button(self) -> None:
        self.backend.begin()
        try:
            self.backend.click(*layout.RENDER_BUTTON)
        finally:
            self.backend.end()


SAVE_BOX_CHOICES = ("dont_save", "cancel", "save")
# Button centres, measured from the top left corner of the dark "Save" button in a
# picture of Bryce's box.
_SAVE_BOX_BUTTONS = {"dont_save": (-146, 10), "cancel": (-41, 10), "save": (32, 10)}


def find_save_box(picture: Image.Image, offset=(0, 0)) -> dict | None:
    """Button centres of Bryce's save question in `picture`, or None. The box is
    recognised by its dark default button and the two light ones beside it."""
    grey = np.asarray(picture.convert("RGB"), dtype=np.int16)
    dark = (np.abs(grey - 116) <= 3).all(axis=-1)
    run = 40                                   # width of an unbroken stretch of button
    if dark.shape[1] < run or dark.shape[0] < 4:
        return None
    totals = np.cumsum(np.pad(dark, ((0, 0), (1, 0))), axis=1)
    starts = (totals[:, run:] - totals[:, :-run]) == run
    height, width = dark.shape
    for y, x in zip(*np.nonzero(starts)):
        if (x and starts[y, x - 1]) or (y and starts[y - 1, x]):
            continue                           # not the top left corner
        if x < 190 or x + 62 > width or y < 25 or y + 22 > height:
            continue

        def light(dx, dy):
            pixel = grey[y + dy, x + dx]
            return bool(((pixel >= 226) & (pixel <= 242)).all()
                        and pixel.max() - pixel.min() <= 3)

        if (light(-64, 2) and light(-182, 2) and light(-100, -25)
                and light(-190, -25) and dark[y + 2, x:x + 4].all()):
            return {name: (int(x + dx + offset[0]), int(y + dy + offset[1]))
                    for name, (dx, dy) in _SAVE_BOX_BUTTONS.items()}
    return None


def _stamp(path: str) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def _is_scene(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            CompositeFile(handle.read())
    except (OSError, BryceError, ValueError, struct.error, IndexError):
        return False
    return True


def is_blank(picture: Image.Image) -> bool:
    """One flat white or black: what Bryce shows when there is no render."""
    extremes = picture.convert("RGB").getextrema()
    return all(low == high and (high >= 250 or high <= 3) for low, high in extremes)


def _same_picture(a: Image.Image, b: Image.Image) -> bool:
    return a.size == b.size and ImageChops.difference(a, b).getbbox() is None


# Items (as _menu_key leaf names) that can discard work, write files or end Bryce.
NEEDS_CONFIRM = frozenset((
    "new document", "open", "merge", "revert to saved", "save", "save as", "export image",
    "render to disk", "quit", "cut", "clear", "reset to defaults", "print"))


def _menu_key(path: str) -> str:
    return "/".join(part.strip().rstrip(". \u2026").lower() for part in path.split("/"))


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
