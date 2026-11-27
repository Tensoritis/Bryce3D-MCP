"""Bryce path objects, and moving things along paths."""

import math
import os
import struct

import pytest
from scene_files import Builder, basic_scene

from bryce3d_mcp import motion, presets, sceneops
from bryce3d_mcp.br3 import UNIT, Br3
from bryce3d_mcp.errors import BryceError

SQUARE = [[40, 10, 0], [0, 10, 40], [-40, 10, 0], [0, 10, -40]]


def path_payload(points) -> bytearray:
    """A path record as Bryce lays it out, from byte 640 on."""
    body = bytearray(700 + 100 * len(points))
    struct.pack_into("<3I", body, 640, 1, 2, len(points))
    struct.pack_into("<6d", body, 652, *(min(p[a] for p in points) for a in range(3)),
                     *(max(p[a] for p in points) for a in range(3)))
    for i, point in enumerate(points):
        struct.pack_into("<3d4f", body, 700 + 100 * i, *point, 0.5, 0.25, -0.5, 1.0)
        body[700 + 100 * i + 72] = 7                      # leftover memory
    return body


def path_scene() -> Br3:
    b = Builder()
    ground = b.solid("C3dPlane", "Plane 1", (0, 0.001, 0))
    cube = b.solid("C3dCube", "Cube 1", (0, 1, 0))
    path = b.solid("C3dPath", "Old Path", (0, 2, 0),
                   payload=path_payload([(-1, 0, 0), (0, 0, 1), (1, 0, 0)]))
    return Br3.from_bytes(b.build(
        [("plne3dob", ground), ("cube3dob", cube), ("path3dob", path)]))


def run(scene, *operations, **more):
    results = sceneops.apply(scene, list(operations), library=None, **more)
    return Br3.from_bytes(scene.data), results


def at(scene, frame, name="Cube 1"):
    scene.set_time({"frame": frame})
    return scene.describe(*scene.find(name))


def test_a_path_object_is_described_with_its_real_size():
    scene = path_scene()
    seen = scene.describe(*scene.find("Old Path"))
    assert seen["kind"] == "path"
    assert seen["size"] == pytest.approx([2 * UNIT, 0, UNIT], abs=0.01)
    assert seen["path"] == {"points": 3, "closed": False}
    route, closed = scene.path_route(scene.find("Old Path")[1])
    assert not closed
    assert route[1] == pytest.approx([0, 2 * UNIT, UNIT])


def test_a_new_path_goes_through_the_points_asked_for():
    scene, results = run(path_scene(), {"op": "path", "name": "Loop", "points": SQUARE,
                                        "closed": True, "smooth": False})
    assert results[0]["path"] == {"points": 5, "closed": True}
    assert results[0]["position"] == pytest.approx([0, 10, 0])
    route, closed = scene.path_route(scene.find("Loop")[1])
    assert closed
    for got, wanted in zip(route, SQUARE):
        assert got == pytest.approx(wanted, abs=1e-6)
    old, _ = scene.path_route(scene.find("Old Path")[1])
    assert len(old) == 3                                   # the donor is untouched


def test_a_new_path_record_matches_bryces_layout():
    scene, _ = run(path_scene(), {"op": "path", "name": "Bend",
                                  "points": [[0, 0, 0], [10, 5, 10], [20, 0, 0]]})
    payload = scene._payload(scene.find("Bend")[1])
    count = struct.unpack_from("<I", payload, 648)[0]
    assert count == 17 and len(payload) == 700 + 100 * count
    assert struct.unpack_from("<2I", payload, 640) == (1, 2)
    box = struct.unpack_from("<6d", payload, 652)
    assert [box[i] + box[i + 3] for i in range(3)] == pytest.approx([0, 0, 0], abs=1e-9)
    for i in range(count):
        assert not any(payload[724 + 100 * i:740 + 100 * i])   # no tension or twist
        assert payload[772 + 100 * i] == 7


@pytest.mark.parametrize("points, message", [
    ([[0, 0, 0]], "2 to 500"),
    ([[0, 0, 0], [1, 2]], "x, y, z"),
    ([[1, 1, 1], [1, 1, 1]], "no length"),
])
def test_a_bad_path_changes_nothing(points, message):
    scene = path_scene()
    before = scene.data
    with pytest.raises(BryceError, match=message):
        run(scene, {"op": "path", "points": points})
    assert scene.data == before


def test_a_path_can_be_moved_but_not_reshaped():
    scene, results = run(path_scene(), {"op": "set", "name": "Old Path",
                                        "position": [50, 8, 0]})
    assert results[0]["position"] == pytest.approx([50, 8, 0])
    for change in ({"scale": [2, 2, 2]}, {"rotation": [0, 90, 0]}):
        with pytest.raises(BryceError, match="moved but not resized"):
            run(scene, {"op": "set", "name": "Old Path", **change})


def test_a_corrupt_point_count_is_reported():
    with pytest.raises(BryceError, match="number of path points"):
        motion.read_points(bytes(path_payload([(0, 0, 0), (1, 0, 0)])[:750]))


def test_an_object_can_follow_a_path_in_the_scene_facing_forwards():
    scene, results = run(
        path_scene(),
        {"op": "path", "name": "Loop", "points": SQUARE, "closed": True},
        {"op": "follow_path", "name": "Cube 1", "path": "Loop", "end": 80, "face": True})
    assert results[1]["followed"]["closed"] is True
    assert at(scene, 0)["position"] == pytest.approx(SQUARE[0], abs=0.01)
    assert at(scene, 20)["position"] == pytest.approx(SQUARE[1], abs=0.2)
    assert at(scene, 40)["position"] == pytest.approx(SQUARE[2], abs=0.2)
    assert at(scene, 80)["position"] == pytest.approx(SQUARE[0], abs=0.01)
    assert at(scene, 0)["rotation"] == pytest.approx([0, 0, 0], abs=0.05)
    assert at(scene, 80)["rotation"] == pytest.approx([0, 0, 0], abs=0.05)
    assert at(scene, 20)["rotation"][1] == pytest.approx(90, abs=1.5)   # heading -x


def test_the_speed_along_a_path_is_steady():
    moments = motion.travel([[0, 0, 0], [10, 0, 0], [100, 0, 0]], False, 0, 50, 51)
    steps = [math.dist(a["position"], b["position"])
             for a, b in zip(moments, moments[1:])]
    assert max(steps) - min(steps) < 0.1


def test_headings_keep_turning_round_a_circle():
    circle = [[math.sin(i / 8 * math.tau), 0, math.cos(i / 8 * math.tau)]
              for i in range(8)]
    headings = [m["heading"] for m in motion.travel(circle, True, 0, 64, 33)]
    assert headings[-1] - headings[0] == pytest.approx(360, abs=0.5)
    back = [m["heading"] for m in motion.travel(circle, True, 0, 64, 33, reverse=True)]
    assert back[-1] - back[0] == pytest.approx(-360, abs=0.5)


def test_the_camera_can_follow_points_and_watch_a_spot():
    scene, _ = run(Br3.from_bytes(basic_scene()), {
        "op": "follow_path", "target": "camera", "points": SQUARE, "closed": True,
        "end": 40, "look_at": [0, 10, 0]})
    for frame, heading in ((0, 270), (10, 180), (20, 90)):
        scene.set_time({"frame": frame})
        assert scene.camera()["heading"] == pytest.approx(heading, abs=1.5)
        assert scene.camera()["pitch"] == pytest.approx(0, abs=0.1)


def test_a_facing_camera_tilts_with_a_climb():
    scene, _ = run(Br3.from_bytes(basic_scene()), {
        "op": "follow_path", "target": "camera", "points": [[0, 0, 0], [0, 100, 100]],
        "end": 30, "face": True})
    scene.set_time({"frame": 15})
    assert scene.camera()["pitch"] == pytest.approx(45, abs=0.5)


def test_a_route_can_be_moved_resized_and_swung():
    scene, _ = run(Br3.from_bytes(basic_scene()), {
        "op": "follow_path", "name": "Cube 1", "points": [[-1, 0, 0], [1, 0, 0]],
        "centre": [5, 20, 5], "scale": 10, "turn": 90, "end": 10})
    assert at(scene, 0)["position"] == pytest.approx([5, 20, 15], abs=0.01)
    assert at(scene, 10)["position"] == pytest.approx([5, 20, -5], abs=0.01)


def test_a_curve_changes_the_speed_along_the_path():
    line = {"op": "follow_path", "name": "Cube 1", "points": [[0, 5, 0], [100, 5, 0]],
            "end": 40}
    steady, _ = run(Br3.from_bytes(basic_scene()), line)
    eased, _ = run(Br3.from_bytes(basic_scene()), {**line, "curve": "ease_in"})
    assert at(steady, 10)["position"][0] == pytest.approx(25, abs=0.5)
    assert at(eased, 10)["position"][0] < 15


@pytest.mark.parametrize("extra, message", [
    ({}, "give one of path, preset or points"),
    ({"points": SQUARE, "path": "Old Path"}, "give one of path, preset or points"),
    ({"path": "Cube 1"}, "not a path object"),
    ({"points": SQUARE}, "which object"),
    ({"points": SQUARE, "name": "Cube 1", "look_at": [0, 0, 0]}, "for the camera"),
    ({"points": SQUARE, "name": "Cube 1", "end": 0}, "end must be after start"),
    ({"points": SQUARE, "name": "Cube 1", "keys": 1}, "keys must be"),
    ({"points": SQUARE, "target": "sun"}, "only objects and the camera"),
])
def test_a_bad_follow_changes_nothing(extra, message):
    scene = path_scene()
    before = scene.data
    with pytest.raises(BryceError, match=message):
        run(scene, {"op": "follow_path", **extra})
    assert scene.data == before


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test Bryce's paths")
def test_all_of_bryces_paths_can_be_read_and_followed():
    folder = presets.folder(os.environ["BRYCE3D_INSTALL"])
    found = [p for p in presets.entries(folder, "objects") if p.library == "Paths.obp"]
    assert len(found) == 30
    shelf = presets.load(found[0])
    for preset in found:
        tag, ref, _ = [i for i in shelf._list_items(("C3dRootObjList", preset.key))
                       if i[0] != "basecamr"][0]
        assert tag == "path3dob"
        route, closed = shelf.path_route(ref)
        assert len(motion.travel(route, closed, 0, 60, 31)) == 31
        points = motion.read_points(shelf._payload(ref))
        box = struct.unpack_from("<6d", shelf._payload(ref), 652)
        # Bryce's own bounding box covers the stored points.
        for axis in range(3):
            assert min(p[axis] for p in points) >= box[axis] - 0.05
            assert max(p[axis] for p in points) <= box[axis + 3] + 0.05


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL")
                    or not os.environ.get("BRYCE3D_SAMPLE_SCENE"),
                    reason="needs Bryce's presets and a real scene")
def test_a_path_can_be_built_and_a_preset_followed_in_a_real_scene():
    install = os.environ["BRYCE3D_INSTALL"]
    scene = Br3.load(os.environ["BRYCE3D_SAMPLE_SCENE"])
    count = len(scene.objects())
    scene, results = run(
        scene, {"op": "path", "name": "My Path", "points": SQUARE, "closed": True},
        {"op": "follow_path", "target": "camera", "preset": "Horse Shoe",
         "centre": [0, 30, 0], "end": 60, "look_at": [0, 5, 0]},
        install=install)
    assert len(scene.objects()) == count + 1
    route, closed = scene.path_route(scene.find("My Path")[1])
    assert closed and route[0] == pytest.approx(SQUARE[0], abs=1e-3)
    assert results[1]["followed"]["frames"] == [0, 60]


def test_moving_a_path_leaves_its_shape_alone():
    scene = path_scene()
    before = scene._payload(scene.find("Old Path")[1])[380:452]
    scene, _ = run(scene, {"op": "set", "name": "Old Path", "position": [1, 2, 3]})
    assert scene._payload(scene.find("Old Path")[1])[380:452] == before


def test_unevenly_spaced_points_do_not_make_the_path_double_back():
    moments = motion.travel([[0, 0, 0], [10, 0, 0], [100, 0, 0], [101, 0, 0]],
                            False, 0, 100, 101)
    across = [m["position"][0] for m in moments]
    assert all(b > a for a, b in zip(across, across[1:]))
