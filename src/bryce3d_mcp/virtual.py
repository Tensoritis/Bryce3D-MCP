"""A backend that drives Bryce through the bridge's virtual mouse and keyboard.

Everything that is not input (menus, dialogs, pictures, opening files) goes to
the ordinary Windows backend. The user's own mouse and keyboard are never used.
"""

from __future__ import annotations

from . import layout
from .bridge import HOLD, BridgeClient
from .errors import BryceError
from .win32 import VK, detect_ui_origin

MAIN_CLASS = "CWinAppl"


class BridgeBackend:
    def __init__(self, base, client: BridgeClient) -> None:
        self.base = base
        self.bridge = client
        self._origin: tuple[int, int] | None = None
        self._manual: tuple[int, int] | None = None

    def __getattr__(self, name):                 # menu, dialogs, grab, open_file, ...
        return getattr(self.base, name)

    # -- where the interface is, in Bryce's own coordinates ---------------------
    def _main_window(self) -> dict:
        tops = [w for w in self.bridge.windows() if not w["parent"] and w["visible"]]
        for window in tops:
            if window["class"] == MAIN_CLASS:
                return window
        if tops:
            return max(tops, key=lambda w: (w["rect"][2] - w["rect"][0])
                       * (w["rect"][3] - w["rect"][1]))
        raise BryceError("Bryce has no visible window (is it minimized?)")

    def _locate_virtual(self) -> tuple[int, int]:
        window = self._main_window()
        left, top, right, bottom = window["client"]
        try:
            found = detect_ui_origin(self.bridge.capture(window["window"]))
        except BryceError:
            found = None
        if found:
            return left + found[0], top + found[1]
        return (left + max(0, (right - left - layout.UI_WIDTH) // 2),
                top + max(0, (bottom - top - layout.UI_HEIGHT) // 2))

    def _screen(self, x: int, y: int) -> tuple[int, int]:
        if self._origin is None:
            self._origin = self._manual or self._locate_virtual()
        return self._origin[0] + x, self._origin[1] + y

    # -- one action ---------------------------------------------------------------
    def begin(self) -> None:
        self._origin = None
        self._screen(0, 0)
        self.bridge.virtual(True)

    def end(self) -> None:
        self.bridge.virtual(False)

    # -- input ----------------------------------------------------------------------
    def move(self, x, y):
        self.bridge.move(*self._screen(x, y))
        self.sleep(0.12)

    def click(self, x, y, button="left", count=1):
        which = {"left": 1, "right": 2}[button]
        self.move(x, y)
        for _ in range(count):
            self.bridge.button(which, True)
            try:
                self.sleep(HOLD)
            finally:
                self.bridge.button(which, False)
            self.sleep(0.06)

    def drag(self, start, end, button="left", duration=0.5):
        which = {"left": 1, "right": 2}[button]
        self.move(*start)
        self.bridge.button(which, True)
        try:
            self.sleep(0.15)
            steps = max(2, int(duration / 0.02))
            for i in range(1, steps + 1):
                x = start[0] + (end[0] - start[0]) * i / steps
                y = start[1] + (end[1] - start[1]) * i / steps
                self.bridge.move(*self._screen(round(x), round(y)))
                self.sleep(duration / steps)
        finally:
            self.bridge.button(which, False)
        self.sleep(0.1)

    def key(self, combo):
        try:
            codes = [VK[part] for part in combo.split("+")]
        except KeyError as exc:
            raise BryceError(f"unknown key {exc.args[0]!r}") from None
        pressed = []
        try:
            for code in codes:
                self.bridge.key(code, True)
                pressed.append(code)
                self.sleep(0.03)
            self.sleep(0.08)
        finally:
            for code in reversed(pressed):
                self.bridge.key(code, False)
        self.sleep(0.08)

    def type_text(self, text):
        if any(not 32 <= ord(c) <= 126 for c in text):
            raise BryceError("only plain ASCII text can be typed into Bryce")
        for char in text:
            self.bridge.char(ord(char))
            self.sleep(0.03)

    def sleep(self, seconds):
        self.base.sleep(seconds)

    def foreground_class(self):
        return self._main_window()["class"]

    def set_origin(self, x, y):
        self._manual = self._origin = None
        self.base.set_origin(x, y)

    def set_virtual_origin(self, x: int, y: int) -> None:
        self._manual = (x, y)
        self._origin = None

    # -- what only the bridge can tell --------------------------------------------
    def activity(self) -> dict:
        return self.bridge.state()

    def unsaved_changes(self) -> bool:
        return bool(self.bridge.state()["unsaved"])

    def forget_changes(self) -> bool:
        return self.bridge.forget_changes()

    def info(self):
        info = self.base.info()
        state = self.bridge.state()
        info["bridge"] = {"connected": True, "unsaved_changes": bool(state["unsaved"])}
        return info
