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
        """Native Win32 menu items as {path, id, enabled}. Empty if none."""

    def invoke_menu(self, command_id: int) -> None: ...

    def info(self) -> dict: ...

    def set_origin(self, x: int, y: int) -> None: ...
