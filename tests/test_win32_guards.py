"""The Windows backend's safety guards, run against a stand-in for user32."""

import pytest

from bryce3d_mcp import win32
from bryce3d_mcp.errors import BryceError, FocusLost, UserInterrupted

BRYCE, OTHER = 1, 2


class StubUser32:
    def __init__(self):
        self.foreground = BRYCE
        self.under_pointer = BRYCE
        self.cursor = (0, 0)
        self.events = []  # (kind, key or mouse flags, key flags)

    def GetForegroundWindow(self):
        return self.foreground

    def WindowFromPoint(self, point):
        return self.under_pointer

    def SetCursorPos(self, x, y):
        self.cursor = (x, y)
        return True

    def MapVirtualKeyW(self, vk, kind):
        return vk

    def SendInput(self, count, array, size):
        for item in array:
            if item.type == 1:
                self.events.append(("key", item.u.ki.wVk, item.u.ki.dwFlags))
            elif item.u.mi.dwFlags != 0x0001:  # ignore the movement nudge
                self.events.append(("mouse", item.u.mi.dwFlags, 0))
        return count


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(win32.time, "sleep", lambda s: None)
    b = win32.Win32Backend.__new__(win32.Win32Backend)
    b.user32 = StubUser32()
    b.hwnd, b._last_cursor = BRYCE, None
    b._origin, b._origin_source = (640, 320), "manual"
    b._is_bryce = lambda hwnd: hwnd == BRYCE
    b._cursor = lambda: b.user32.cursor
    return b


def test_click_is_offset_by_origin_and_released(backend):
    backend.click(10, 20, count=2)
    assert backend.user32.cursor == (650, 340)
    assert [e[1] for e in backend.user32.events] == [2, 4, 2, 4]


def test_nothing_sent_when_another_app_is_in_front(backend):
    backend.user32.foreground = OTHER
    for action in (lambda: backend.click(1, 1), lambda: backend.key("delete"),
                   lambda: backend.drag((0, 0), (9, 9)), lambda: backend.move(1, 1)):
        with pytest.raises(FocusLost):
            action()
    assert backend.user32.events == [] and backend.user32.cursor == (0, 0)


def test_click_refused_when_a_window_covers_the_point(backend):
    backend.user32.under_pointer = OTHER
    with pytest.raises(FocusLost):
        backend.click(1, 1)
    assert backend.user32.events == []


def test_stops_when_the_user_moves_the_mouse(backend):
    backend.click(10, 10)
    backend.user32.events.clear()
    backend.user32.cursor = (5, 5)
    with pytest.raises(UserInterrupted):
        backend.key("ctrl+a")
    assert backend.user32.events == []


def test_chord_releases_in_reverse_order(backend):
    backend.key("ctrl+alt+e")
    downs = [vk for _, vk, flags in backend.user32.events if not flags & 2]
    ups = [vk for _, vk, flags in backend.user32.events if flags & 2]
    assert downs == [0x11, 0x12, ord("E")] and ups == downs[::-1]


def test_button_released_if_focus_is_lost_mid_drag(backend):
    original = backend._set_cursor
    calls = []

    def steal_focus(x, y):
        original(x, y)
        calls.append(1)
        if len(calls) == 4:
            backend.user32.foreground = OTHER

    backend._set_cursor = steal_focus
    with pytest.raises(FocusLost):
        backend.drag((0, 0), (100, 100))
    assert [e[1] for e in backend.user32.events] == [2, 4]


def test_unknown_key(backend):
    with pytest.raises(BryceError):
        backend.key("ctrl+banana")
    assert backend.user32.events == []


# -- window messages: menus, dialogs and window pictures ----------------------------
class MessageStub:
    def __init__(self):
        self.sent = []

    def SendMessageTimeoutW(self, window, message, wparam, lparam, flags, timeout, answer):
        self.sent.append(("send", window, message, wparam, lparam))
        return 1

    def PostMessageW(self, window, message, wparam, lparam):
        self.sent.append(("post", window, message, wparam, lparam))
        return 1


def test_menu_command_selects_the_submenu_then_runs_the_item():
    backend = win32.Win32Backend.__new__(win32.Win32Backend)
    backend.user32 = MessageStub()
    backend._menu_window = lambda: 77
    backend.run_menu({"path": "File/Render", "id": 19, "menu": 0x5000, "enabled": True})
    assert backend.user32.sent == [
        ("send", 77, 0x011F, (0x0080 << 16) | 19, 0x5000),   # WM_MENUSELECT
        ("post", 77, 0x0111, 19, 0)]                          # WM_COMMAND, from a menu


def test_pictures_come_only_from_bryce_windows():
    from PIL import Image

    backend = win32.Win32Backend.__new__(win32.Win32Backend)
    main, popup = 1, 2
    rects = {main: (100, 50, 900, 650), popup: (200, 100, 260, 140)}
    colours = {main: (0, 0, 200), popup: (0, 200, 0)}
    metrics = {76: 0, 77: 0, 78: 1000, 79: 700}

    class Stub:
        def GetSystemMetrics(self, index):
            return metrics[index]

        def IsIconic(self, hwnd):
            return False

    backend.user32 = Stub()
    backend._find_window = lambda window_class=None: main
    backend._windows = lambda visible_only=True: [popup, main]        # front first
    backend._rect = lambda hwnd: rects[hwnd]
    backend._print_window = lambda hwnd: Image.new(
        "RGB", (rects[hwnd][2] - rects[hwnd][0], rects[hwnd][3] - rects[hwnd][1]),
        colours[hwnd])
    picture, corner = backend._bryce_picture()
    assert corner == (0, 0)
    assert picture.getpixel((10, 10)) == (0, 0, 0)          # the desktop is never seen
    assert picture.getpixel((150, 60)) == (0, 0, 200)
    assert picture.getpixel((210, 110)) == (0, 200, 0)      # pop-up drawn on top
