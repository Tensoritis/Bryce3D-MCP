"""Where things are in the Bryce 3D interface.

All coordinates are "UI pixels": (0, 0) is the top-left corner of the 640x480
main interface. Some pop-ups extend outside that box, so negative values and
values past 640/480 are valid.

The points were measured on a running copy of Bryce 3D, on a 1920x1080 display
captured at 1456x819. They are stored in that capture frame and converted here,
so each one can be checked against docs/UI_MAP.md.
"""

from __future__ import annotations

UI_WIDTH, UI_HEIGHT = 640, 480
_SCALE = 1920 / 1456
_ORIGIN = (485, 243)

Point = tuple[int, int]


def _p(x: float, y: float) -> Point:
    return round((x - _ORIGIN[0]) * _SCALE), round((y - _ORIGIN[1]) * _SCALE)


# Region captured for screenshots: the main UI plus the pop-ups that overhang it.
CAPTURE_BOX = (0, -20, 704, 640)

# Top tabs ---------------------------------------------------------------------
TAB_CREATE = _p(617, 251)
CREATE_LIBRARY_ARROW = _p(641, 251)
TAB_EDIT = _p(672, 251)
TAB_SKY = _p(733, 251)

# Create palette. Every name below was read from Bryce's own hover label. --------
PRIMITIVES: dict[str, Point] = {
    "water_plane": _p(601, 292),
    "cloud_plane": _p(620, 280),
    "ground_plane": _p(640, 292),
    "terrain": _p(656, 284),
    "stone": _p(678, 285),
    "symmetrical_lattice": _p(695, 280),
    "sphere": _p(709, 290),
    "ellipsoid": _p(716, 276),
    "squashed_sphere": _p(725, 289),
    "torus": _p(739, 276),
    "tuboid": _p(741, 291),
    "cylinder": _p(750, 283),
    "stretched_cylinder": _p(764, 276),
    "squashed_cylinder": _p(765, 292),
    "cube": _p(777, 283),
    "stretched_cube": _p(793, 276),
    "brickoid": _p(788, 292),
    "pyramid": _p(803, 285),
    "stretched_pyramid": _p(818, 277),
    "squashed_pyramid": _p(814, 292),
    "cone": _p(828, 285),
    "stretched_cone": _p(840, 277),
    "squashed_cone": _p(838, 292),
    "picture_object": _p(865, 278),
    "disk_horizontal": _p(853, 291),
    "face_horizontal": _p(877, 292),
    "radial_light": _p(897, 291),
    "spotlight": _p(913, 291),
    "square_spotlight": _p(929, 291),
    "parallel_light": _p(945, 291),
}

# Object Attributes dialog (Ctrl+Alt+E) ----------------------------------------
ATTR_NAME = _p(925, 540)
ATTR_FIELDS: dict[str, tuple[Point, Point, Point]] = {
    row: (_p(893, y), _p(933, y), _p(973, y))
    for row, y in (("origin", 643), ("position", 659), ("rotation", 675), ("size", 691))
}
ATTR_BOOLEAN: dict[str, Point] = {
    "neutral": _p(863, 558),
    "positive": _p(863, 570),
    "negative": _p(863, 581),
    "intersect": _p(863, 593),
}
ATTR_CANCEL = _p(990, 717)
ATTR_OK = _p(1006, 717)
# A dialog counts as open only if ALL of its probe points have the expected look.
# Each probe is (point, lowest brightness, highest brightness); all must be neutral
# gray or white. Any mismatch means "not open", so nothing gets typed.
Probe = tuple[Point, int, int]
ATTR_PROBES: tuple[Probe, ...] = (
    (_p(842, 625), 200, 255),  # white body, left of the coordinate heading
    (_p(845, 702), 200, 255),  # white body, left of the X axis label
    (_p(900, 716), 60, 175),   # gray footer bar
)

# Materials Lab (Ctrl+M) -------------------------------------------------------
MAT_LIBRARY_ARROW = _p(592, 289)
MAT_CANCEL = _p(923, 578)
MAT_OK = _p(953, 578)
MAT_PROBES: tuple[Probe, ...] = (
    (_p(700, 238), 40, 185),   # title bar, above the main interface
    (_p(500, 450), 40, 185),   # left side of the metal panel
    (_p(860, 300), 40, 185),   # empty right-hand panel
)

# Preset library pop-up (shared by the object and material libraries) -----------
_GRID_X = (704, 740, 777, 813, 849, 885)
_GRID_Y = (371, 407, 442, 478, 514)
LIBRARY_PROBES: tuple[Probe, ...] = (
    (_p(760, 300), 220, 255),  # white preview area, upper middle
    (_p(880, 300), 220, 255),  # white preview area, upper right
    (_p(830, 548), 60, 175),   # gray footer bar
)
LIBRARY_CANCEL = _p(885, 548)
LIBRARY_OK = _p(901, 548)
LIBRARY_NEXT_PAGE = _p(887, 523)


def library_cell(row: int, column: int) -> Point:
    """Zero-based cell in the 5 x 6 preset grid."""
    if not (0 <= row < len(_GRID_Y) and 0 <= column < len(_GRID_X)):
        raise ValueError("preset grid is 5 rows (0-4) by 6 columns (0-5)")
    return _p(_GRID_X[column], _GRID_Y[row])


OBJECT_CATEGORIES: dict[str, Point] = {
    name: _p(660, y)
    for name, y in (
        ("boolean objects", 365), ("mountains", 377), ("rocks&trees", 389),
        ("paths", 401), ("imported objects", 413), ("tutorial", 425), ("user", 437),
    )
}

MATERIAL_CATEGORIES: dict[str, Point] = {
    name: _p(660, y)
    for name, y in (
        ("simple&fast", 365), ("planes&terrains", 377), ("rocks&stones", 389),
        ("waters&liquids", 401), ("clouds&fogs", 413), ("wild&fun", 425),
        ("complex fx", 437), ("miscellaneous", 449), ("glasses", 461),
        ("metals", 474), ("volume", 486), ("tutorial", 498), ("user", 510),
    )
}

# First two rows of Simple&Fast, named by their swatch color.
SIMPLE_COLORS: dict[str, tuple[int, int]] = {
    "white": (0, 0), "gray": (0, 1), "black": (0, 2),
    "red": (0, 3), "green": (0, 4), "blue": (0, 5),
    "salmon": (1, 0), "teal": (1, 1), "brown": (1, 2),
    "cyan": (1, 3), "magenta": (1, 4), "yellow": (1, 5),
}

# Main screen -------------------------------------------------------------------
RENDER_BUTTON = _p(537, 545)
RENDER_REPORT_OK = _p(833, 520)
VIEWPORT_BOX = (*_p(592, 305), *_p(955, 577))
# Empty upper-left part of the viewport, used to deselect.
VIEWPORT_EMPTY = _p(640, 340)
STATUS_BOX = (0, 408, 140, 462)
