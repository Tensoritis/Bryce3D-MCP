"""The curve Bryce draws through position keys and along paths."""

import glob
import math
import os
import struct

import pytest
from scene_files import basic_scene

from bryce3d_mcp import curve, keyframes, motion, sceneops, spline
from bryce3d_mcp.br3 import Br3
from bryce3d_mcp.errors import BryceError

BEND = [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 5]]


def test_inner_handles_come_from_the_points_either_side():
    first, second = spline.handles(BEND, 1, 0.19)
    assert first == pytest.approx([10 + 0.19 * 10, 0.19 * 10, 0])
    assert second == pytest.approx([10 - 0.19 * -10, 10 - 0.19 * 10, -0.19 * 5])


def test_an_end_handle_leans_on_the_handle_at_the_other_end():
    first, second = spline.handles(BEND, 0, 0.19)
    assert first == pytest.approx([v + 0.19 * (h - v) for v, h in zip(BEND[0], second)])
    first, second = spline.handles(BEND, 2, 0.19)
    assert second == pytest.approx([v + 0.19 * (h - v) for v, h in zip(BEND[3], first)])


def test_two_points_put_both_handles_halfway():
    first, second = spline.handles([[0, 0, 0], [8, 4, 2]], 0)
    assert first == second == [4, 2, 1]


def test_a_curve_reads_the_same_backwards_when_bias_changes_sides():
    shaping = [(0.2, -0.3, 0.5), (0.0, 0.4, -0.6), (-0.5, 0.0, 0.3), (0.1, 0.2, 0.0)]
    forward = spline.sample(BEND, steps=8, tcb=shaping)
    points, turned = motion.reversed_route(BEND, shaping)
    for a, b in zip(forward, reversed(spline.sample(points, steps=8, tcb=turned))):
        assert a == pytest.approx(b)


def test_full_tension_on_inner_points_gives_straight_lines():
    shaping = [(0, 0, 0), (1, 0, 0), (1, 0, 0), (0, 0, 0)]
    drawn = spline.sample(BEND, steps=10, tcb=shaping)
    for i in range(3):
        a, b = BEND[i], BEND[i + 1]
        along = [y - x for x, y in zip(a, b)]
        for point in drawn[i * 10:i * 10 + 11]:
            off = [y - x for x, y in zip(a, point)]
            cross = [off[1] * along[2] - off[2] * along[1],
                     off[2] * along[0] - off[0] * along[2],
                     off[0] * along[1] - off[1] * along[0]]
            assert math.hypot(*cross) == pytest.approx(0, abs=1e-9)


def test_position_keys_bend_and_pass_through_every_key():
    keys = [(float(i), tuple(map(float, p))) for i, p in enumerate(BEND)]
    for time, value in keys:
        assert keyframes.at(keys, time, keyframes.SPLINE) == value
    got = keyframes.at(keys, 1.5, keyframes.SPLINE)
    assert got == pytest.approx(spline.between(BEND, 1, 0.5))
    assert got[0] > 11                                  # it swings past the corner
    assert keyframes.at(keys, 1.5)[0] == 10             # other settings go straight


def test_each_kind_of_track_has_its_own_blend():
    assert keyframes.blend_of("Obj_ePosition") == keyframes.SPLINE
    assert keyframes.blend_of("Obj_eOrient") == keyframes.TURN
    assert keyframes.blend_of("Obj_eScale") == keyframes.STRAIGHT
    assert keyframes.blend_of("Cam_eFOV") == keyframes.STRAIGHT
    assert keyframes.blend_of("SunL_eSunVctr") == keyframes.DIRECTION


def test_the_sun_direction_stays_a_unit_vector():
    keys = [(0.0, (1.0, 0.0, 0.0)), (2.0, (0.0, 1.0, 0.0))]
    got = keyframes.at(keys, 0.5, keyframes.DIRECTION)
    assert math.hypot(*got) == pytest.approx(1)
    assert got == pytest.approx((3 / math.sqrt(10), 1 / math.sqrt(10), 0))


def test_an_object_swings_wide_between_position_keys_as_in_bryce():
    scene = Br3.from_bytes(basic_scene())
    scene.set_keyframes("Cube 1", [{"frame": 30 * i, "position": p}
                                   for i, p in enumerate(BEND)])
    scene.set_time({"frame": 45})
    got = scene.describe(*scene.find("Cube 1"))["position"]
    assert got == pytest.approx(spline.between(BEND, 1, 0.5), abs=0.01)


def shaped_path(**more):
    scene = Br3.from_bytes(basic_scene())
    donor = _donor()
    scene_operations = [{"op": "path", "name": "Shaped", **more}]
    sceneops.apply(scene, scene_operations, library=donor)
    return Br3.from_bytes(scene.data)


def _donor():
    from test_motion import path_scene
    return path_scene()


def test_shaped_points_are_saved_for_bryce_to_draw():
    points = [[0, 5, 0], {"at": [20, 25, 0], "tension": 0.5, "bias": -0.25},
              {"at": [40, 5, 0], "continuity": 1}]
    scene = shaped_path(points=points, smooth=False)
    ref = scene.find("Shaped")[1]
    payload = scene._payload(ref)
    assert struct.unpack_from("<I", payload, 648)[0] == 3
    assert motion.read_shaping(payload) == [(0, 0, 0), (0.5, 0, -0.25), (0, 1, 0)]
    route, closed, shaping = scene.path_route(ref)
    assert not closed and shaping[1] == (0.5, 0, -0.25)
    for got, wanted in zip(route, [[0, 5, 0], [20, 25, 0], [40, 5, 0]]):
        assert got == pytest.approx(wanted, abs=1e-5)


def test_the_path_box_covers_the_curve_not_just_the_points():
    scene = shaped_path(points=[[0, 0, 0], [30, 40, 0], [60, 40, 0]], smooth=False)
    payload = scene._payload(scene.find("Shaped")[1])
    box = struct.unpack_from("<6d", payload, 652)
    points = motion.read_points(payload)
    assert box[4] > max(p[1] for p in points) + 0.05
    assert [box[i] + box[i + 3] for i in range(3)] == pytest.approx([0, 0, 0], abs=1e-9)


@pytest.mark.parametrize("points, smooth, message", [
    ([[0, 0, 0], {"at": [1, 1, 1], "tension": 2}], False, "-1 to 1"),
    ([[0, 0, 0], {"at": [1, 1, 1], "twist": 1}], False, "shaped point takes"),
    ([[0, 0, 0], {"tension": 1}], False, "shaped point takes"),
    ([[0, 0, 0], {"at": [1, 1, 1], "tension": 1}], True, "set smooth to false"),
])
def test_bad_shaping_is_refused(points, smooth, message):
    with pytest.raises(BryceError, match=message):
        shaped_path(points=points, smooth=smooth)


# -- against positions Bryce saved ---------------------------------------------

CD_SCENES = os.environ.get("BRYCE3D_CD_SCENES")
_ANIMATED_OBJECT_SKIPS = ("C3dBaseMaterial", "C3dTexture", "C3dRootObjList",
                          "C3dObjList", "C3dObjTrackList", "C3dCImage", "C3dBaseSky")


def _saved_positions(path):
    """(keys, curve points, time marker, saved position) of every object that is
    between two different position keys, from a scene in either byte order."""
    scene = Br3.load(path)
    marker = struct.unpack_from(">f", scene._document_record()[1], 68)[0]
    for ref in scene._all_refs():
        if not ref[0].startswith("C3d") or ref[0] in _ANIMATED_OBJECT_SKIPS:
            continue
        body = scene._payload(ref)
        if len(body) < 512:
            continue
        order = "<" if body[:4] == b"\x01\0\0\0" else ">"
        key = struct.unpack_from(order + "I", body, 492)[0]
        if not key or "Obj_ePosition" not in scene._ids.get(key, ()):
            continue
        track = scene._payload(("Obj_ePosition", key))
        count, extra = (struct.unpack_from(order + "I", track, o)[0] for o in (8, 16))
        if count < 2:
            continue
        size = (len(track) - 148 - extra) // count
        keys = [(struct.unpack_from(order + "d", track, 148 + i * size)[0],
                 struct.unpack_from(order + "3d", track, 192 + i * size))
                for i in range(count)]
        if len({value for _, value in keys}) < 2:
            continue
        shape = None
        if extra:
            blob = track[len(track) - extra:]
            total = struct.unpack_from(order + "I", blob, 8)[0]
            shape = [struct.unpack_from(order + "2fI", blob, 44 + 28 * i)
                     for i in range(total)]
        yield keys, shape, marker, struct.unpack_from(order + "3d", body, 452)


@pytest.mark.skipif(not CD_SCENES, reason="set BRYCE3D_CD_SCENES to the folder of "
                    "scenes from the Bryce CD")
def test_positions_between_keys_match_what_bryce_saved():
    """Poses that sit on a key are left out: no blending happens there, and a few
    of Bryce's own scenes hold an object that was moved without keying it."""
    checked = 0
    for path in glob.glob(os.path.join(CD_SCENES, "**", "*.br3"), recursive=True):
        for keys, shape, marker, saved in _saved_positions(path):
            first, last = keys[0][0], keys[-1][0]
            moment = min(last, max(first, marker))
            if shape:
                share = (moment - first) / (last - first)
                moment = first + curve.progress(shape, share) * (last - first)
            if min(abs(moment - time) for time, _ in keys) < 1e-4:
                continue
            got = keyframes.at(keys, marker, keyframes.SPLINE, shape)
            assert got == pytest.approx(saved, abs=1e-6), os.path.basename(path)
            checked += 1
    assert checked > 400


def test_a_curve_that_stops_short_never_reaches_the_last_key():
    keys = [(0.0, (0.0,)), (4.0, (100.0,))]
    short = [(0.0, 0.0, 2), (0.5, 0.4, 2), (0.9, 0.8, 2)]
    for time in (4.0, 9.0):
        assert keyframes.at(keys, time, points=short) == pytest.approx((80.0,))


def test_a_scene_saved_on_a_mac_is_named_as_such():
    scene = Br3.from_bytes(basic_scene())
    root = scene._first("C3dRootObjList")
    scene.file.replace(scene._entry(root),
                       b"\0\0\0\1\0\0\0\0" + scene._payload(root)[8:])
    with pytest.raises(BryceError, match="saved on a Mac"):
        Br3.from_bytes(scene.data).camera()
