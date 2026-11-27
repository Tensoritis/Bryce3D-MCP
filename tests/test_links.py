"""Joining objects, tracking, and Bryce's own path link."""

import glob
import math
import os
import struct

import pytest
from scene_files import Builder

from bryce3d_mcp import links, motion, preview, sceneops
from bryce3d_mcp.br3 import UNIT, Br3
from bryce3d_mcp.errors import BryceError

SQUARE = [[40, 10, 0], [0, 10, 40], [-40, 10, 0], [0, 10, -40]]


def scene_with_cubes() -> Br3:
    """Three cubes, a spot light and a path, each with its cached pose filled in the
    way every scene Bryce saves has it."""
    b = Builder()
    objects = []
    for class_name, tag, name, position in (
            ("C3dCube", "cube3dob", "Hub", (0, 1, 0)),
            ("C3dCube", "cube3dob", "Moon", (2, 1, 0)),
            ("C3dCube", "cube3dob", "Target", (0, 3, 4)),
            ("C3dConeLight", "conelght", "Spot", (-3, 6, -2)),
            ("C3dPath", "path3dob", "Old Path", (0, 2, 0))):
        if class_name == "C3dPath":
            body = bytearray(700 + 300)
            struct.pack_into("<3I", body, 640, 1, 2, 3)
            for i, point in enumerate(((-1, 0, 0), (0, 0, 1), (1, 0, 0))):
                struct.pack_into("<3d", body, 700 + 100 * i, *point)
        else:
            body = bytearray(848 if class_name == "C3dConeLight" else 640)
        struct.pack_into("<9d", body, 380, 1, 0, 0, 0, 1, 0, 0, 0, 1)
        struct.pack_into("<3d", body, 452, *position)
        objects.append((tag, b.solid(class_name, name, position, payload=body)))
    return Br3.from_bytes(b.build(objects))


def run(scene, *operations):
    scene = Br3.from_bytes(scene.data)          # leave the caller's scene alone
    sceneops.apply(scene, list(operations), library=None)
    return Br3.from_bytes(scene.data)


def where(scene, name):
    return scene.describe(*scene.find(name))["position"]


def units(*values):
    return pytest.approx([v * UNIT for v in values], abs=0.01)


def off_aim(scene, name, target="Target", axis=2, sign=1):
    """How far (degrees) an object's drawn axis is from pointing at the target."""
    ref = scene.find(name)[1]
    matrix, _ = scene._cache(ref)
    drawn = links.product(matrix, scene.frame_around(ref)[0])[axis]
    wanted = [a - b for a, b in zip(scene.scene_centre(scene.find(target)[1]),
                                    scene.scene_centre(ref))]
    dot = sum(sign * a * b for a, b in zip(drawn, wanted))
    return math.degrees(math.acos(min(1.0, dot / math.hypot(*drawn)
                                      / math.hypot(*wanted))))


def at_frame(scene, frame):
    copy = Br3.from_bytes(scene.data)
    copy.set_time({"frame": frame})
    return copy


def test_joining_moves_nothing_and_stores_the_child_inside_the_parent():
    scene = run(scene_with_cubes(), {"op": "join", "name": "Moon", "to": "Hub"})
    assert where(scene, "Moon") == units(2, 1, 0)
    assert scene.describe(*scene.find("Moon"))["joined_to"] == "Hub"
    hub, moon = scene.find("Hub")[1], scene.find("Moon")[1]
    assert [ref for _, ref, _ in scene._root()[2]].count(moon) == 0
    assert scene.children_of(hub) == [moon]
    entry = next(raw for _, ref, raw in scene._root()[2] if ref == hub)
    assert entry[12:] == struct.pack("<I", hub[1])    # marked with its list's id
    assert struct.unpack_from("<I", scene._payload(moon), 272)[0] & 4
    assert struct.unpack_from("<3d", scene._payload(moon), 452) \
        == pytest.approx((2, 0, 0))                   # measured from the parent


def test_a_parent_carries_what_is_joined_to_it():
    scene = run(scene_with_cubes(), {"op": "join", "name": "Moon", "to": "Hub"},
                {"op": "set", "name": "Hub", "position": [0, 5 * UNIT, 0]})
    assert where(scene, "Moon") == units(2, 5, 0)
    scene = run(scene, {"op": "set", "name": "Hub", "rotation": [0, 90, 0],
                        "scale": [2, 2, 2]})
    assert where(scene, "Moon") == units(0, 5, 4)
    moon = scene.describe(*scene.find("Moon"))   # reported as it stands in the scene
    assert moon["rotation"] == pytest.approx([0, 90, 0], abs=0.01)
    assert moon["scale"] == pytest.approx([2, 2, 2], abs=0.001)


def test_links_chain_and_unjoining_leaves_things_where_they_are():
    scene = run(scene_with_cubes(), {"op": "join", "name": "Moon", "to": "Hub"},
                {"op": "join", "name": "Target", "to": "Moon"},
                {"op": "set", "name": "Hub", "rotation": [0, 90, 0]})
    before = where(scene, "Target")
    scene = run(scene, {"op": "unjoin", "name": "Target"},
                {"op": "unjoin", "name": "Moon"})
    assert where(scene, "Target") == pytest.approx(before, abs=0.01)
    assert "joined_to" not in scene.describe(*scene.find("Moon"))
    assert "C3dObjList" not in scene._rows or not scene._rows["C3dObjList"]
    for _, _, raw in scene._root()[2]:
        assert raw[12:] == bytes(4)


def test_a_joined_object_is_still_set_in_scene_terms():
    scene = run(scene_with_cubes(), {"op": "join", "name": "Moon", "to": "Hub"},
                {"op": "set", "name": "Moon", "position": [0, 8 * UNIT, 0]})
    assert where(scene, "Moon") == units(0, 8, 0)
    assert scene.describe(*scene.find("Moon"))["joined_to"] == "Hub"


def test_deleting_a_parent_keeps_its_children():
    scene = run(scene_with_cubes(), {"op": "join", "name": "Moon", "to": "Hub"},
                {"op": "delete", "name": "Hub"})
    assert where(scene, "Moon") == units(2, 1, 0)
    assert "joined_to" not in scene.describe(*scene.find("Moon"))


@pytest.mark.parametrize("operations, message", [
    ([{"op": "join", "name": "Hub", "to": "Hub"}], "itself"),
    ([{"op": "join", "name": "Moon", "to": "Hub"},
      {"op": "join", "name": "Hub", "to": "Moon"}], "already joined under"),
    ([{"op": "unjoin", "name": "Moon"}], "not joined"),
    ([{"op": "join", "name": "Moon", "to": "Hub"},
      {"op": "duplicate", "name": "Moon"}], "unjoin it"),
    ([{"op": "set", "name": "Moon", "rotation": [0, 45, 0]},
      {"op": "set", "name": "Hub", "scale": [3, 1, 1]},
      {"op": "join", "name": "Moon", "to": "Hub"}], "slant"),
    ([{"op": "join", "name": "Moon", "to": "Hub"},
      {"op": "keyframes", "name": "Moon", "keys": [
          {"frame": 0, "position": [0, 0, 0]}, {"frame": 9, "position": [1, 1, 1]}]}],
     "Key the parent"),
    ([{"op": "track", "name": "Moon", "target": "Moon"}], "itself"),
    ([{"op": "track", "name": "Moon", "target": "Hub"},
      {"op": "track", "name": "Hub", "target": "Moon"}], "each other"),
    ([{"op": "untrack", "name": "Moon"}], "not tracking"),
    ([{"op": "follow_path", "name": "Moon", "path": "Old Path", "native": True,
       "face": True}], "native takes"),
])
def test_links_that_cannot_be_made_are_refused(operations, message):
    with pytest.raises(BryceError, match=message):
        run(scene_with_cubes(), *operations)


def test_a_tracking_link_is_recorded_and_both_kinds_point_at_the_target():
    scene = run(scene_with_cubes(), {"op": "track", "name": "Moon", "target": "Target"},
                {"op": "track", "name": "Spot", "target": "Target"})
    moon, target = scene.find("Moon")[1], scene.find("Target")[1]
    record = scene._payload(("C3dObjTrackList", 1))
    assert struct.unpack_from("<3I", record) == (1, 1, 2) and len(record) == 60
    assert record[12:36] == (b"cube3dob"[::-1] + struct.pack("<I", moon[1])
                             + b"cube3dob"[::-1] + struct.pack("<I", target[1]))
    assert off_aim(scene, "Moon") < 0.001
    assert off_aim(scene, "Spot", axis=1, sign=-1) < 0.001
    assert struct.unpack_from("<I", scene._payload(moon), 268)[0] & 0x1000
    spot = scene.find("Spot")[1]
    assert not struct.unpack_from("<I", scene._payload(spot), 268)[0] & 0x1000
    assert scene.describe("cube", moon)["tracks"] == "Target"
    assert where(scene, "Moon") == units(2, 1, 0)


def test_trackers_follow_a_target_that_moves_and_one_that_is_keyed():
    scene = run(scene_with_cubes(), {"op": "track", "name": "Moon", "target": "Target"},
                {"op": "set", "name": "Target", "position": [80, 20, -30]})
    assert off_aim(scene, "Moon") < 0.001
    scene = run(scene, {"op": "keyframes", "name": "Target", "duration": 2, "keys": [
        {"frame": 0, "position": [80, 20, -30]},
        {"frame": 30, "position": [-80, 90, 60]}]})
    for frame in (0, 15, 30):
        assert off_aim(at_frame(scene, frame), "Moon") < 0.001


def test_the_camera_can_track_and_untracking_restores_the_turn():
    scene = run(scene_with_cubes(), {"op": "set", "name": "Moon", "rotation": [0, 30, 0]},
                {"op": "track", "name": "camera", "target": "Target"},
                {"op": "track", "name": "Moon", "target": "Target"})
    seen = scene.camera()
    to = [a - b for a, b in zip(where(scene, "Target"), seen["position"])]
    assert seen["heading"] == pytest.approx(math.degrees(math.atan2(to[0], to[2])) % 360,
                                            abs=0.02)
    scene = run(scene, {"op": "untrack", "name": "Moon"})
    assert scene.describe(*scene.find("Moon"))["rotation"] == pytest.approx([0, 30, 0])
    assert len(scene.tracking()) == 1


def test_deleting_a_target_ends_its_tracking():
    scene = run(scene_with_cubes(), {"op": "track", "name": "Moon", "target": "Target"},
                {"op": "delete", "name": "Target"})
    assert scene.tracking() == []
    moon = scene.find("Moon")[1]
    assert not struct.unpack_from("<I", scene._payload(moon), 268)[0] & 0x1000


def rider_scene():
    return run(scene_with_cubes(), {"op": "path", "name": "Track", "points": SQUARE,
                                    "closed": True, "smooth": False},
               {"op": "follow_path", "name": "Moon", "path": "Track", "native": True,
                "end": 40})


def test_a_native_path_link_is_stored_the_way_bryce_stores_it():
    scene = rider_scene()
    rider, path = scene.find("Moon")[1], scene.find("Track")[1]
    payload = scene._payload(rider)
    assert scene.children_of(path) == [rider]
    assert struct.unpack_from("<I", payload, 268)[0] & 0x800
    assert struct.unpack_from("<I", payload, 272)[0] & 4
    assert struct.unpack_from("<I", payload, 492)[0] == 0        # no position track
    key = struct.unpack_from("<I", payload, 488)[0]
    record = scene._payload(("Obj_ePathT", key))
    count = struct.unpack_from("<I", scene._payload(path), 648)[0]
    assert struct.unpack_from("<I", record, 8)[0] == 2
    size = (len(record) - 148) // 2
    assert struct.unpack_from("<f", record, 148 + 44)[0] == 0
    assert struct.unpack_from("<f", record, 148 + size + 44)[0] == count - 1
    assert scene.describe("cube", rider)["rides_path"] == "Track"


def test_a_rider_is_posed_along_the_curve_and_moves_with_its_path():
    scene = rider_scene()
    route, _, shaping = scene.path_route(scene.find("Track")[1])
    curve = motion.curve_through(route, True, "bryce", shaping)
    for frame in (0, 10, 25, 40):
        assert where(at_frame(scene, frame), "Moon") == pytest.approx(
            curve.at(frame / 40)[0], abs=0.01)
    start = where(scene, "Moon")
    path = where(scene, "Track")
    moved = run(scene, {"op": "set", "name": "Track",
                        "position": [path[0], path[1] + 20, path[2]]})
    assert where(moved, "Moon") == pytest.approx(
        [start[0], start[1] + 20, start[2]], abs=0.01)
    freed = run(scene, {"op": "unjoin", "name": "Moon"})
    assert where(freed, "Moon") == pytest.approx(start, abs=0.01)
    moon = freed.find("Moon")[1]
    assert not struct.unpack_from("<I", freed._payload(moon), 268)[0] & 0x800
    assert "Obj_ePathT" not in freed._rows or not freed._rows["Obj_ePathT"]


def test_the_preview_draws_a_joined_object_where_its_parent_puts_it():
    scene = run(scene_with_cubes(), {"op": "join", "name": "Moon", "to": "Hub"},
                {"op": "set", "name": "Hub", "position": [0, 5 * UNIT, 0]})
    parts = preview.object_parts(scene, "cube", scene.find("Moon")[1])
    middle = parts[0][0].mean(0)
    assert list(middle) == units(2, 5, 0)


# -- against links Bryce saved --------------------------------------------------

CD_SCENES = os.environ.get("BRYCE3D_CD_SCENES")
_NOT_OBJECTS = ("C3dObjList", "C3dRootObjList", "C3dObjTrackList", "C3dBaseMaterial",
                "C3dTexture", "C3dCImage", "C3dBaseSky")


def _aims(path):
    """For every tracking link of a scene saved on a Mac: how nearly the follower's
    drawn axis points at its target, worked out through the joins."""
    scene = Br3.load(path)
    kept = {}

    def payload(ref):
        if ref not in kept:
            kept[ref] = scene._payload(ref)
        return kept[ref]

    def number(data, at):
        return struct.unpack_from(">I", data, at)[0]

    parent, wanted, objects = {}, [], {}
    for ref in scene._all_refs():
        if ref[0] == "C3dObjList":
            data = payload(ref)
            # (scenes saved on Windows read as a huge count here, and hold no links)
            for i in range(min(number(data, 8), (len(data) - 12) // 16)):
                parent[number(data, 20 + 16 * i)] = ref[1]
        elif ref[0] == "C3dObjTrackList":
            data = payload(ref)
            wanted += [(number(data, 20 + 24 * i), number(data, 32 + 24 * i))
                       for i in range(min(number(data, 8), (len(data) - 12) // 24))]
    if not wanted:
        return
    for ref in scene._all_refs():
        if ref[0].startswith("C3d") and ref[0] not in _NOT_OBJECTS \
                and len(payload(ref)) >= 512:
            objects[ref[1]] = ref

    def drawn(key):
        data = payload(objects[key])
        flat = struct.unpack_from(">9d", data, 380)
        matrix = [list(flat[i * 3:i * 3 + 3]) for i in range(3)]
        centre = list(struct.unpack_from(">3d", data, 452))
        if parent.get(key) in objects:
            outer, shift = drawn(parent[key])
            return (links.product(matrix, outer),
                    [a + b for a, b in zip(links.times(centre, outer), shift)])
        return matrix, centre

    for follower, target in wanted[::7] if len(wanted) > 30 else wanted:
        matrix, centre = drawn(follower)
        to = [a - b for a, b in zip(drawn(target)[1], centre)]
        axis, sign = (1, -1) if objects[follower][0] == "C3dConeLight" else (2, 1)
        yield sum(sign * a * b for a, b in zip(matrix[axis], to)) \
            / math.hypot(*matrix[axis]) / math.hypot(*to)


@pytest.mark.skipif(not CD_SCENES, reason="set BRYCE3D_CD_SCENES to the folder of "
                    "scenes from the Bryce CD")
def test_every_tracking_link_bryce_saved_points_as_read_here():
    found = [value for path in glob.glob(os.path.join(CD_SCENES, "**", "*.br3"),
                                         recursive=True) for value in _aims(path)]
    assert len(found) > 30
    assert min(found) > 0.996                   # one camera is 4.5 degrees behind
    assert sum(value > 0.9999 for value in found) >= len(found) - 1
