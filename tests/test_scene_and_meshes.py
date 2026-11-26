import json
import math
import pathlib
from collections import Counter

import pytest
from PIL import Image

from bryce3d_mcp import layout, meshes, scene
from bryce3d_mcp.controller import BryceController
from bryce3d_mcp.errors import BryceError
from bryce3d_mcp.win32 import detect_ui_origin
from fake import FakeBackend

EXAMPLES = pathlib.Path(__file__).parent.parent / "examples"


def test_example_scene_builds():
    steps = scene.expand(json.loads((EXAMPLES / "temple.json").read_text()))
    assert len(steps) == 13
    result = scene.build(BryceController(FakeBackend()), steps)
    assert result == {"completed": True, "built": 13, "total": 13}


def test_on_ground_and_repeat():
    steps = scene.expand([{"type": "cube", "name": "b", "position": [0, 0, 0],
                           "size": [10, 20, 10], "on_ground": True,
                           "repeat": {"count": 3, "offset": [15, 0, 0]}}])
    assert [s["position"] for s in steps] == [[0, 10, 0], [15, 10, 0], [30, 10, 0]]
    assert [s["name"] for s in steps] == ["b 1", "b 2", "b 3"]
    ring = scene.expand([{"type": "sphere", "position": [5, 1, 5],
                          "repeat": {"count": 8, "radius": 10}}])
    assert all(math.isclose(math.hypot(s["position"][0] - 5, s["position"][2] - 5), 10)
               for s in ring)


@pytest.mark.parametrize("bad", [
    {"type": "teapot"},
    {"type": "cube", "colour": "red"},
    {"type": "cube", "position": [1, 2]},
    {"type": "cube", "position": [1, 2, float("nan")]},
    {"type": "cube", "size": [0, 1, 1]},
    {"type": "cube", "material": "mauve"},
    {"type": "cube", "material": {"row": 9}},
    {"type": "cube", "boolean": "maybe"},
    {"type": "cube", "name": "x" * 40},
    {"type": "cube", "position": [0, 0, 0], "repeat": {"count": 9999, "offset": [1, 0, 0]}},
])
def test_whole_scene_is_rejected_before_any_input(bad):
    with pytest.raises(BryceError):
        scene.expand([{"type": "cube"}, bad])


def test_layout_is_consistent():
    left, top, right, bottom = layout.CAPTURE_BOX
    points = [*layout.PRIMITIVES.values(), layout.ATTR_OK, layout.MAT_OK, layout.LIBRARY_OK,
              layout.ATTR_NAME, layout.RENDER_BUTTON,
              *(p for row in layout.ATTR_FIELDS.values() for p in row),
              *(probe[0] for probe in
                layout.ATTR_PROBES + layout.MAT_PROBES + layout.LIBRARY_PROBES),
              *(layout.library_cell(r, c) for r in range(5) for c in range(6))]
    assert all(left <= x < right and top <= y < bottom for x, y in points)
    assert len(set(layout.PRIMITIVES.values())) == len(layout.PRIMITIVES)
    assert layout.VIEWPORT_BOX == (141, 82, 620, 440)


def assert_closed_and_outward(vertices, faces):
    """Every edge is shared once in each direction, and the volume is positive."""
    edges = Counter((f[i - 1], f[i]) for f in faces for i in range(len(f)))
    assert all(n == 1 and edges[(b, a)] == 1 for (a, b), n in edges.items())
    volume = 0
    for f in faces:
        for k in range(1, len(f) - 1):
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (vertices[i] for i in (f[0], f[k], f[k + 1]))
            volume += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    assert volume > 0
    return volume / 6


@pytest.mark.parametrize("reverse", [False, True])
def test_extrude_concave_outline(reverse):
    outline = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]]  # an L, area 7
    if reverse:
        outline.reverse()
    assert math.isclose(assert_closed_and_outward(*meshes.extrude(outline, 2)), 14)


@pytest.mark.parametrize("profile", [
    [[0, 0], [5, 0], [5, 10], [0, 10]],
    [[0, 0], [6, 2], [2, 8], [0, 12]],
])
def test_lathe_is_closed_and_outward(profile):
    assert_closed_and_outward(*meshes.lathe(profile, 24))


def test_self_intersecting_outline_rejected():
    with pytest.raises(BryceError):
        meshes.extrude([[0, 0], [4, 4], [4, 0], [0, 3]], 1)


def test_obj_and_dxf_output():
    v, f = meshes.extrude([[0, 0], [1, 0], [1, 1], [0, 1]], 1)
    obj = meshes.to_obj(v, f, "my box!")
    assert obj.startswith("o my_box_\n") and obj.count("\nv ") == 8
    assert min(int(i) for line in obj.splitlines() if line.startswith("f ")
               for i in line.split()[1:]) == 1
    dxf = meshes.to_dxf(v, f)
    assert dxf.count("3DFACE") == 12 and dxf.endswith("EOF\n")
    with pytest.raises(BryceError):
        meshes.to_obj(v, [[0, 1, 99]])


@pytest.mark.parametrize("shape", ["mountain", "island", "crater", "canyon", "dunes", "plateau"])
def test_heightmaps(shape):
    image = meshes.heightmap(shape, 64, seed=3)
    assert image.size == (64, 64) and image.mode == "L"
    low, high = image.getextrema()
    assert low < 30 and high > 80
    assert image.tobytes() == meshes.heightmap(shape, 64, seed=3).tobytes()


def test_ui_origin_detection():
    screen = Image.new("RGB", (1920, 1080))
    screen.paste((200, 200, 200), (640, 320, 1280, 800))
    assert detect_ui_origin(screen) == (640, 320)
    screen.paste((200, 200, 200), (1300, 700, 1340, 900))  # a pop-up sticks out
    assert detect_ui_origin(screen) is None
    assert detect_ui_origin(Image.new("RGB", (1920, 1080))) is None
