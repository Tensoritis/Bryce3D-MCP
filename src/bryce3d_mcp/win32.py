"""Windows backend: ctypes input injection and Pillow screen capture.

Safety rules enforced here, so no tool can bypass them:
  * input is only sent while the foreground window belongs to Bryce;
  * a click is only sent if the window under the point belongs to Bryce;
  * if the user moves the mouse during a sequence, the sequence stops.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

from PIL import Image, ImageGrab

from . import layout
from .errors import BryceError, BryceNotFound, FocusLost, UserInterrupted

DEFAULT_EXE = "bryce 3d.exe"
USER_MOVE_TOLERANCE = 25

VK = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "enter": 0x0D, "escape": 0x1B,
    "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pageup": 0x21, "pagedown": 0x22, "home": 0x24, "end": 0x23,
    "plus": 0x6B, "minus": 0x6D,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
}
EXTENDED_KEYS = {0x2E, 0x25, 0x26, 0x27, 0x28, 0x21, 0x22, 0x24, 0x23}


def detect_ui_origin(image: Image.Image, threshold: int = 24) -> tuple[int, int] | None:
    """Find the 640x480 main interface on a black backdrop.

    Returns its top-left corner within `image`, or None if the bright area is
    not the size of the main interface (a pop-up is open, or no black surround).
    """
    mask = image.convert("L").point(lambda v: 255 if v > threshold else 0)
    box = mask.getbbox()
    if box is None:
        return None
    left, top, right, bottom = box
    if abs((right - left) - layout.UI_WIDTH) <= 3 and abs((bottom - top) - layout.UI_HEIGHT) <= 3:
        return left, top
    return None


_WM_COPYDATA = 0x004A
_MAIN_WINDOW_CLASS = "CWinAppl"


def open_request(path: str, encoding: str = "mbcs") -> bytes:
    """The text Bryce expects in WM_COPYDATA: the quoted path in the Windows ANSI code
    page, ending with a NUL. Bryce takes what is between the quotes."""
    if '"' in path or "\0" in path:
        raise BryceError("the path contains a character Windows file names cannot have")
    try:
        encoded = f'"{path}"'.encode(encoding)
    except UnicodeError as exc:
        raise BryceError("Bryce cannot take this path: it has characters outside the "
                         "Windows ANSI code page. Use a simpler folder or file name.") from exc
    if len(encoded) > 259:                      # Bryce copies it into a 260-byte buffer
        raise BryceError("the path is too long for Bryce; use a shorter one")
    return encoded + b"\0"


class _COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", ctypes.c_uint32),
                ("lpData", ctypes.c_void_p)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class Win32Backend:
    def __init__(self, exe_name: str | None = None) -> None:
        if sys.platform != "win32":
            raise BryceError("the live backend only runs on Windows, next to Bryce 3D")
        self.exe_name = (exe_name or os.environ.get("BRYCE3D_EXE") or DEFAULT_EXE).lower()
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare()
        try:  # per-monitor DPI awareness, so coordinates are physical pixels
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except OSError:
            self.user32.SetProcessDPIAware()
        self.hwnd = None
        self._origin: tuple[int, int] | None = None
        self._origin_source = "unset"
        self._last_cursor: tuple[int, int] | None = None
        env = os.environ.get("BRYCE3D_UI_ORIGIN")
        if env:
            x, y = (int(v) for v in env.split(","))
            self.set_origin(x, y)
            self._origin_source = "BRYCE3D_UI_ORIGIN"

    def _declare(self) -> None:
        """Declare signatures so 64-bit handles are not truncated."""
        u, k = self.user32, self.kernel32
        H, D, B, I, U = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_int, wintypes.UINT
        for fn, res, args in (
            (u.GetForegroundWindow, H, []),
            (u.SetForegroundWindow, B, [H]),
            (u.AttachThreadInput, B, [D, D, B]),
            (k.GetCurrentThreadId, D, []),
            (u.ShowWindow, B, [H, I]),
            (u.IsIconic, B, [H]),
            (u.IsWindowVisible, B, [H]),
            (u.GetWindowThreadProcessId, D, [H, ctypes.POINTER(D)]),
            (u.WindowFromPoint, H, [wintypes.POINT]),
            (u.GetCursorPos, B, [ctypes.POINTER(wintypes.POINT)]),
            (u.SetCursorPos, B, [I, I]),
            (u.GetSystemMetrics, I, [I]),
            (u.SendInput, U, [U, ctypes.POINTER(_INPUT), I]),
            (u.MapVirtualKeyW, U, [U, U]),
            (u.VkKeyScanW, ctypes.c_short, [wintypes.WCHAR]),
            (u.GetClassNameW, I, [H, wintypes.LPWSTR, I]),
            (u.GetMenu, H, [H]),
            (u.GetMenuItemCount, I, [H]),
            (u.GetSubMenu, H, [H, I]),
            (u.GetMenuItemID, U, [H, I]),
            (u.GetMenuStringW, I, [H, U, wintypes.LPWSTR, I, U]),
            (u.GetMenuState, U, [H, U, U]),
            (u.PostMessageW, B, [H, U, wintypes.WPARAM, wintypes.LPARAM]),
            (u.SendMessageTimeoutW, ctypes.c_ssize_t,
             [H, U, wintypes.WPARAM, wintypes.LPARAM, U, U, ctypes.POINTER(ctypes.c_size_t)]),
            (k.OpenProcess, H, [D, B, D]),
            (k.CloseHandle, B, [H]),
            (k.QueryFullProcessImageNameW, B, [H, D, wintypes.LPWSTR, ctypes.POINTER(D)]),
        ):
            fn.restype, fn.argtypes = res, args

    # -- process and window discovery -------------------------------------------
    def _pid(self, hwnd) -> int:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _exe_of(self, hwnd) -> str:
        handle = self.kernel32.OpenProcess(0x1000, False, self._pid(hwnd))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return os.path.basename(buf.value).lower()
        finally:
            self.kernel32.CloseHandle(handle)

    def _is_bryce(self, hwnd) -> bool:
        return bool(hwnd) and self._exe_of(hwnd) == self.exe_name

    def _find_window(self, window_class: str | None = None):
        found: list[int] = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            if self.user32.IsWindowVisible(hwnd) and self._is_bryce(hwnd):
                found.append(hwnd)
            return True

        self.user32.EnumWindows(proc(visit), 0)
        if not found:
            raise BryceNotFound(f"no visible window from {self.exe_name!r}. Start Bryce 3D, "
                                "or set BRYCE3D_EXE to its executable name.")
        if window_class:
            matching = [h for h in found if self._class_of(h) == window_class]
            if not matching:
                raise BryceNotFound(f"Bryce is running but no window of class {window_class} "
                                    "was found")
            return matching[0]
        with_menu = [h for h in found if self.user32.GetMenu(h)]
        return (with_menu or found)[0]

    def _class_of(self, hwnd) -> str:
        name = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, name, len(name))
        return name.value

    def begin(self) -> None:
        self.hwnd = self._find_window()
        if not self._is_bryce(self.user32.GetForegroundWindow()):
            if self.user32.IsIconic(self.hwnd):
                self.user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            self._bring_forward()
            time.sleep(0.4)
        self._guard_focus()
        self._last_cursor = None
        self._locate()

    def _guard_focus(self) -> None:
        if not self._is_bryce(self.user32.GetForegroundWindow()):
            raise FocusLost("Bryce 3D is not the foreground window, so nothing was sent. "
                            "Close or minimize whatever is on top, or click Bryce, then retry.")

    def _bring_forward(self) -> None:
        """Windows often refuses SetForegroundWindow from a background process.
        Briefly sharing the foreground thread's input queue usually allows it."""
        ours = self.kernel32.GetCurrentThreadId()
        theirs = self.user32.GetWindowThreadProcessId(self.user32.GetForegroundWindow(), None)
        attached = bool(theirs and theirs != ours
                        and self.user32.AttachThreadInput(ours, theirs, True))
        try:
            self.user32.SetForegroundWindow(self.hwnd)
        finally:
            if attached:
                self.user32.AttachThreadInput(ours, theirs, False)

    # -- opening files without input ---------------------------------------------
    def open_file(self, path: str) -> None:
        """Send Bryce the message a second copy of Bryce sends when it is launched
        with a file. No focus, mouse or keyboard is involved."""
        data = open_request(path)
        window = self._find_window(_MAIN_WINDOW_CLASS)
        buffer = ctypes.create_string_buffer(data, len(data))
        request = _COPYDATASTRUCT(0, len(data), ctypes.cast(buffer, ctypes.c_void_p))
        answer = ctypes.c_size_t()
        # SMTO_ABORTIFHUNG; Windows copies the text into Bryce's memory.
        sent = self.user32.SendMessageTimeoutW(window, _WM_COPYDATA, 0, ctypes.addressof(request),
                                               0x0002, 15000, ctypes.byref(answer))
        if not sent:
            raise BryceError("Bryce did not accept the message. It may be busy or showing a "
                             "question. If it runs as administrator, run this server the "
                             "same way.")

    # -- coordinates and capture -------------------------------------------------
    def set_origin(self, x: int, y: int) -> None:
        self._origin, self._origin_source = (x, y), "manual"

    def _locate(self) -> None:
        """Resolve the UI origin once per tool call. Detected and manual values stick."""
        if self._origin_source in ("manual", "detected", "BRYCE3D_UI_ORIGIN"):
            return
        screen = ImageGrab.grab()
        found = detect_ui_origin(screen)
        if found:
            self._origin, self._origin_source = found, "detected"
        else:
            # Observed layout: centered horizontally, 20 px below vertical center.
            width, height = screen.size
            self._origin = ((width - layout.UI_WIDTH) // 2,
                            (height - layout.UI_HEIGHT) // 2 + 20)
            self._origin_source = "assumed (centered)"

    def origin(self) -> tuple[int, int]:
        if self._origin is None:
            self._locate()
        return self._origin

    def _screen(self, x: int, y: int) -> tuple[int, int]:
        ox, oy = self.origin()
        return ox + x, oy + y

    def grab(self, box):
        self._guard_focus()
        left, top = self._screen(box[0], box[1])
        right, bottom = self._screen(box[2], box[3])
        return ImageGrab.grab(bbox=(left, top, right, bottom)).convert("RGB")

    def pixel(self, x, y):
        return self.grab((x, y, x + 1, y + 1)).getpixel((0, 0))

    # -- input -------------------------------------------------------------------
    def _cursor(self) -> tuple[int, int]:
        point = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def _guard_user(self) -> None:
        if self._last_cursor is None:
            return
        x, y = self._cursor()
        if max(abs(x - self._last_cursor[0]), abs(y - self._last_cursor[1])) > USER_MOVE_TOLERANCE:
            self._last_cursor = None
            raise UserInterrupted("the mouse was moved by someone else; the sequence was stopped")

    def _send(self, *inputs: _INPUT) -> None:
        array = (_INPUT * len(inputs))(*inputs)
        if self.user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT)) != len(inputs):
            raise BryceError("Windows rejected the input (is Bryce running as administrator?)")

    def _set_cursor(self, x: int, y: int) -> None:
        self.user32.SetCursorPos(x, y)
        self._last_cursor = self._cursor()
        # A zero-length relative move makes polling apps notice the new position.
        self._send(_INPUT(0, _INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, 0x0001, 0, 0))))

    def move(self, x, y):
        self._guard_user()
        self._guard_focus()
        sx, sy = self._screen(x, y)
        self._set_cursor(sx - 2, sy)
        time.sleep(0.03)
        self._set_cursor(sx, sy)
        time.sleep(0.12)

    def _button(self, button: str, down: bool) -> None:
        flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}[button]
        self._send(_INPUT(0, _INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, flags[0 if down else 1], 0, 0))))

    def _guard_point(self) -> None:
        point = wintypes.POINT(*self._cursor())
        if not self._is_bryce(self.user32.WindowFromPoint(point)):
            raise FocusLost("another window covers that point, so the click was not sent")

    def click(self, x, y, button="left", count=1):
        self.move(x, y)  # once, so the pointer is still between double-clicks
        for _ in range(count):
            self._guard_user()
            self._guard_focus()
            self._guard_point()
            self._button(button, True)
            try:
                # An instantaneous click failed to select in the viewport; a held one worked.
                time.sleep(0.12)
            finally:
                self._button(button, False)
            time.sleep(0.06)

    def drag(self, start, end, button="left", duration=0.5):
        self.move(*start)
        self._guard_point()
        self._button(button, True)
        try:
            time.sleep(0.15)
            steps = max(2, int(duration / 0.02))
            for i in range(1, steps + 1):
                self._guard_user()
                self._guard_focus()
                x = start[0] + (end[0] - start[0]) * i / steps
                y = start[1] + (end[1] - start[1]) * i / steps
                self._set_cursor(*self._screen(round(x), round(y)))
                time.sleep(duration / steps)
        finally:
            self._button(button, False)
        time.sleep(0.1)

    def _key_event(self, vk: int, down: bool) -> None:
        flags = (0 if down else 0x0002) | (0x0001 if vk in EXTENDED_KEYS else 0)
        scan = self.user32.MapVirtualKeyW(vk, 0)
        self._send(_INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(vk, scan, flags, 0, 0))))

    def _chord(self, codes: list[int]) -> None:
        self._guard_user()
        self._guard_focus()
        pressed: list[int] = []
        try:
            for vk in codes:
                self._key_event(vk, True)
                pressed.append(vk)
                time.sleep(0.02)
            time.sleep(0.05)
        finally:
            for vk in reversed(pressed):
                self._key_event(vk, False)
        time.sleep(0.05)

    def key(self, combo):
        try:
            codes = [VK[part] for part in combo.split("+")]
        except KeyError as exc:
            raise BryceError(f"unknown key {exc.args[0]!r}") from None
        self._chord(codes)

    def type_text(self, text):
        for char in text:
            scan = self.user32.VkKeyScanW(char)
            if scan == -1:
                raise BryceError(f"cannot type {char!r} with the current keyboard layout")
            shift_state = (scan >> 8) & 0xFF
            codes = [VK[m] for m, bit in (("shift", 1), ("ctrl", 2), ("alt", 4)) if shift_state & bit]
            self._chord(codes + [scan & 0xFF])

    def sleep(self, seconds):
        time.sleep(seconds)

    # -- dialogs and native menus ------------------------------------------------
    def foreground_class(self):
        self._guard_focus()
        buf = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(self.user32.GetForegroundWindow(), buf, len(buf))
        return buf.value

    def menu(self):
        items: list[dict] = []

        def walk(handle, prefix, depth):
            for index in range(max(0, self.user32.GetMenuItemCount(handle))):
                buf = ctypes.create_unicode_buffer(256)
                self.user32.GetMenuStringW(handle, index, buf, len(buf), 0x400)
                label = buf.value.split("\t")[0].replace("&&", "\0").replace("&", "").replace("\0", "&")
                if not label:
                    continue
                sub = self.user32.GetSubMenu(handle, index)
                if sub and depth < 5:
                    walk(sub, prefix + [label], depth + 1)
                else:
                    state = self.user32.GetMenuState(handle, index, 0x400)
                    items.append({"path": "/".join(prefix + [label]),
                                  "id": self.user32.GetMenuItemID(handle, index),
                                  "enabled": not state & 0x3})

        root = self.user32.GetMenu(self.hwnd)
        if root:
            walk(root, [], 0)
        return items

    def invoke_menu(self, command_id):
        self._guard_focus()
        self.user32.PostMessageW(self.hwnd, 0x0111, command_id, 0)  # WM_COMMAND

    def info(self):
        origin = self.origin()
        return {
            "executable": self.exe_name,
            "screen": [self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)],
            "ui_origin": list(origin),
            "ui_origin_source": self._origin_source,
            "native_menu_items": len(self.menu()),
        }
