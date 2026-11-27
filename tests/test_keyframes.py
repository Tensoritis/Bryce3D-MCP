import struct

import pytest
from scene_files import basic_scene

from bryce3d_mcp import keyframes, sceneops
from bryce3d_mcp.br3 import Br3
from bryce3d_mcp.errors import BryceError

SLIDE = [{"frame": 0, "position": [0, 10, 0]}, {"frame": 30, "position": [30, 10, 0]}]


def animated(keys=SLIDE, **more) -> Br3:
    scene = Br3.from_bytes(basic_scene())
    scene.set_keyframes("Cube 1", keys, **more)
    return Br3.from_bytes(scene.data)


def cube(scene, name="Cube 1"):
    return scene.describe(*scene.find(name))


def track(scene, name="Cube 1", class_name="Obj_ePosition"):
    return scene._payload(scene._linked(scene.find(name)[1], class_name))


def close(a, b):
    assert a == pytest.approx(b, abs=0.01)


def test_a_key_record_grows_by_one_key_each_time():
    before = track(Br3.from_bytes(basic_scene()))
    after = track(animated())
    stride = len(before) - keyframes.HEAD
    assert len(after) == len(before) + stride
    assert struct.unpack_from("<3I", after) == (1, 1, 2)
    assert after[12:keyframes.HEAD] == before[12:keyframes.HEAD]
    assert [keyframes.time_of(k) for k in keyframes.keys(after)] == [0.0, 2.0]


def test_keys_read_back_in_bryce_units_and_set_the_length():
    scene = animated()
    keys = cube(scene)["animation"]["position"]
    assert [k["frame"] for k in keys] == [0, 30]
    close(keys[1]["value"], [30, 10, 0])
    timeline = scene.timeline()
    assert (timeline["duration_frames"], timeline["working_range"]) == (30, [0.0, 2.0])
    assert scene.summary()["timeline"] == timeline
    close(cube(scene)["position"], [0, 10, 0])                  # the marker is at 0


def test_keys_may_use_seconds_in_any_order_and_a_longer_duration():
    scene = animated([{"time": 2, "position": [30, 10, 0]},
                      {"time": 0, "position": [0, 10, 0]}], duration=4)
    assert [k["frame"] for k in cube(scene)["animation"]["position"]] == [0, 30]
    assert scene.timeline()["duration_frames"] == 60


def test_the_time_marker_poses_what_is_drawn():
    scene = animated()
    assert scene.set_time({"frame": 30})["frame"] == 30
    close(cube(scene)["position"], [30, 10, 0])
    scene.set_time({"time": 1})
    close(cube(Br3.from_bytes(scene.data))["position"], [15, 10, 0])
    with pytest.raises(BryceError, match="ends at frame 30"):
        scene.set_time({"frame": 31})


def test_rotation_and_scale_can_be_animated():
    scene = animated([{"frame": 0, "rotation": [0, 0, 0], "scale": [1, 1, 1]},
                      {"frame": 10, "rotation": [0, 90, 0], "scale": [3, 1, 1]}])
    assert set(cube(scene)["animation"]) == {"rotation", "scale"}
    scene.set_time({"frame": 5})
    close(cube(scene)["rotation"], [0, 45, 0])
    close(cube(scene)["scale"], [2, 1, 1])


def test_a_turn_takes_the_short_way_between_keys():
    scene = animated([{"frame": 0, "rotation": [0, 170, 0]},
                      {"frame": 10, "rotation": [0, 190, 0]}])
    first, second = [v for _, v in keyframes.values(
        track(scene, class_name="Obj_eOrient"), 4)]
    assert sum(a * b for a, b in zip(first, second)) > 0


def test_moving_an_animated_object_moves_the_whole_motion():
    scene = animated()
    scene.set_time({"frame": 30})
    scene.set_object("Cube 1", position=[30, 25, 5])
    values = [k["value"] for k in cube(scene)["animation"]["position"]]
    close(values[0], [0, 25, 5])
    close(values[1], [30, 25, 5])
    close(cube(scene)["position"], [30, 25, 5])


def test_an_animated_channel_cannot_be_overwritten_by_set():
    scene = animated([{"frame": 0, "scale": [1, 1, 1]}, {"frame": 9, "scale": [2, 2, 2]}])
    with pytest.raises(BryceError, match="animated scale"):
        scene.set_object("Cube 1", scale=[5, 5, 5])
    scene.set_object("Cube 1", rotation=[0, 20, 0])             # not animated: allowed
    close(cube(scene)["rotation"], [0, 20, 0])


def test_one_key_removes_the_animation():
    scene = animated()
    scene.set_keyframes("Cube 1", [{"frame": 12, "position": [5, 10, 0]}])
    info = cube(Br3.from_bytes(scene.data))
    assert "animation" not in info
    close(info["position"], [5, 10, 0])
    assert len(track(scene)) == len(track(Br3.from_bytes(basic_scene())))
    assert keyframes.time_of(keyframes.keys(track(scene))[0]) == 0.0


def test_a_duplicate_keeps_the_motion_and_deleting_it_leaves_the_original():
    scene = animated()
    scene.duplicate("Cube 1", "Twin", position=[0, 10, 20])
    values = [k["value"] for k in cube(scene, "Twin")["animation"]["position"]]
    close(values[1], [30, 10, 20])
    scene.delete("Twin")
    close(cube(scene)["animation"]["position"][1]["value"], [30, 10, 0])


def test_a_new_object_copied_from_an_animated_one_starts_still():
    library = animated()
    scene = Br3.from_bytes(basic_scene())
    info = scene.add_object("cube", name="Fresh", library=library, position=[1, 2, 3])
    assert "animation" not in info
    close(info["position"], [1, 2, 3])


def test_animated_objects_cannot_be_grouped():
    scene = animated()
    with pytest.raises(BryceError, match="animated and cannot be grouped"):
        scene.group(["Cube 1", "Plane 1"])


@pytest.mark.parametrize("keys, message", [
    ([], "keys, a curve, or both"),
    ([{"position": [0, 0, 0]}], "either frame or time"),
    ([{"frame": 1, "time": 1, "position": [0, 0, 0]}], "either frame or time"),
    ([{"frame": 1}], "has no position"),
    ([{"frame": 1.5, "position": [0, 0, 0]}], "whole number"),
    ([{"frame": -1, "position": [0, 0, 0]}], "frames run from"),
    ([{"frame": 1, "colour": [0, 0, 0]}], "unknown field"),
    ([{"frame": 1, "scale": [0, 1, 1]}], "must be positive"),
    ([{"frame": 1, "position": [0, 0, 0]}, {"time": 0.07, "position": [1, 0, 0]}],
     "two position keys"),
])
def test_bad_keys_are_refused_before_anything_changes(keys, message):
    scene = Br3.from_bytes(basic_scene())
    before = scene.data
    with pytest.raises(BryceError, match=message):
        scene.set_keyframes("Cube 1", keys)
    assert scene.data == before


def test_the_edit_operations():
    scene = Br3.from_bytes(basic_scene())
    done = sceneops.apply(scene, [
        {"op": "keyframes", "name": "Cube 1", "keys": SLIDE, "duration": 3},
        {"op": "time_marker", "frame": 30}])
    assert done[0]["timeline"]["duration_frames"] == 45
    assert done[1]["timeline"]["frame"] == 30
    close(cube(scene)["position"], [30, 10, 0])


@pytest.mark.parametrize("number", [0, 7])
def test_an_impossible_key_count_is_reported(number):
    record = bytearray(240)
    struct.pack_into("<3I", record, 0, 1, 1, number)
    with pytest.raises(BryceError, match="key count"):
        keyframes.count(bytes(record))
