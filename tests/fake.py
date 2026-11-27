"""A simulated Bryce screen: enough state to exercise the controller's checks."""

from PIL import Image

from bryce3d_mcp import layout
from bryce3d_mcp.errors import FocusLost

BLACK, WHITE, GRAY = (0, 0, 0), (245, 245, 245), (110, 110, 110)


class FakeBackend:
    def __init__(self):
        self.log = []
        self.attributes = False
        self.materials = False
        self.library = False
        self.library_never_opens = False
        self.created = 0
        self.focused = True
        # failure switches
        self.attributes_never_opens = False
        self.create_does_nothing = False
        self.backdrop = BLACK
        self.window_class = "Bryce"
        self.menu_items = []
        self.render_frames = 0
        self._frame = 0

    def _input(self, *event):
        if not self.focused:
            raise FocusLost("not focused")
        self.log.append(event)

    def begin(self):
        if not self.focused:
            raise FocusLost("not focused")

    def move(self, x, y):
        self._input("move", x, y)

    def click(self, x, y, button="left", count=1):
        self._input("click", (x, y), count)
        if (x, y) in layout.PRIMITIVES.values() and not self.create_does_nothing:
            self.created += 1
        elif (x, y) in (layout.ATTR_OK, layout.ATTR_CANCEL):
            self.attributes = False
        elif (x, y) in (layout.MAT_LIBRARY_ARROW, layout.CREATE_LIBRARY_ARROW):
            self.library = not self.library_never_opens
        elif (x, y) in (layout.LIBRARY_OK, layout.LIBRARY_CANCEL):
            self.library = False
        elif (x, y) == layout.MAT_OK:
            self.materials = False

    def drag(self, start, end, button="left", duration=0.5):
        self._input("drag", start, end)

    def key(self, combo):
        self._input("key", combo)
        if combo == "ctrl+alt+e" and not self.attributes_never_opens:
            self.attributes = True
        elif combo == "ctrl+m":
            self.materials = True

    def type_text(self, text):
        self._input("type", text)

    def pixel(self, x, y):
        for is_open, probes in ((self.library, layout.LIBRARY_PROBES),
                                (self.attributes, layout.ATTR_PROBES),
                                (self.materials, layout.MAT_PROBES)):
            for point, _low, high in probes:
                if is_open and point == (x, y):
                    return WHITE if high == 255 else GRAY
        return self.backdrop

    def grab(self, box):
        size = (box[2] - box[0], box[3] - box[1])
        if box == layout.STATUS_BOX:
            return Image.new("RGB", size, (self.created % 256, 0, 0))
        if box == layout.VIEWPORT_BOX and self._frame < self.render_frames:
            self._frame += 1
            return Image.new("RGB", size, (0, self._frame % 256, 0))
        return Image.new("RGB", size, (9, 9, 9))

    def sleep(self, seconds):
        pass

    def open_file(self, path):
        self.log.append(("open_file", path))

    def foreground_class(self):
        return self.window_class

    def menu(self):
        return self.menu_items

    def invoke_menu(self, command_id):
        self._input("menu", command_id)

    def info(self):
        return {}

    def set_origin(self, x, y):
        self.log.append(("origin", x, y))

    def typed(self):
        return [e[1] for e in self.log if e[0] == "type"]
