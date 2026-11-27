"""The thin interface between Bryce logic and the operating system."""

from __future__ import annotations

from typing import Protocol

from PIL import Image


class Backend(Protocol):
    def begin(self) -> None:
        """Find Bryce, bring it forward and reset the interruption check."""

    def move(self, x: int, y: int) -> None: ...

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None: ...

    def drag(self, start: tuple[int, int], end: tuple[int, int],
             button: str = "left", duration: float = 0.5) -> None: ...

    def key(self, combo: str) -> None: ...

    def type_text(self, text: str) -> None: ...

    def grab(self, box: tuple[int, int, int, int]) -> Image.Image:
        """Capture a box given in UI pixels."""

    def pixel(self, x: int, y: int) -> tuple[int, int, int]: ...

    def sleep(self, seconds: float) -> None: ...

    def foreground_class(self) -> str:
        """Window class of the foreground Bryce window."""

    def menu(self) -> list[dict]:
        """Native Win32 menu items as {path, id, menu, enabled}."""

    def run_menu(self, item: dict) -> None:
        """Run one of those items by window message."""

    def open_file(self, path: str) -> None: ...

    def window_ids(self) -> set[int]:
        """Bryce's visible top-level windows."""

    def end(self) -> None:
        """Finish one input action (the virtual input is handed back)."""

    def activity(self) -> dict | None:
        """The bridge's activity counters, or None without the bridge."""

    def unsaved_changes(self) -> bool | None:
        """Whether Bryce has unsaved changes, or None if it cannot be known."""

    def forget_changes(self) -> bool | None: ...

    def dialogs(self) -> list[dict]:
        """Open Windows dialogs as {window, title, text, buttons, takes_path}."""

    def fill_dialog(self, dialog: int, text: str) -> None: ...

    def press_dialog_button(self, dialog: int, button_id: int) -> None: ...

    def info(self) -> dict: ...

    def set_origin(self, x: int, y: int) -> None: ...
