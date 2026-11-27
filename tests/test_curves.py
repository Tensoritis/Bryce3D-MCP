"""Time curves, and keys for the camera, the sky, materials and lamps."""

import os
import struct

import pytest
from scene_files import Builder, basic_scene

from bryce3d_mcp import curve, keyframes, sceneops
from bryce3d_mcp.br3 import Br3
from bryce3d_mcp.errors import BryceError

# Bezier handles as Bryce 3D's own curve code builds them: the points of a curve
# (time, progress, flags), a stretch, and the four control points of that stretch.
SMOOTH = [(0, 0, 2), (0.3, 0.6, 2), (0.7, 0.7, 2), (1, 1, 2)]
MIXED = [(0, 0, 2), (0.25, 0.5, 0), (0.6, 0.2, 2), (1, 1, 0)]
LATE = [(0, 0, 0), (0.4, 0.1, 2), (0.7, 0.9, 0), (1, 1, 0)]
FROM_BRYCE = [
    (SMOOTH, 0, [(0, 0), (0.04756, 0.11017), (0.22028, 0.51031), (0.3, 0.6)]),
    (SMOOTH, 1, [(0.3, 0.6), (0.4063, 0.71959), (0.56432, 0.6152), (0.7, 0.7)]),
    (SMOOTH, 2, [(0.7, 0.7), (0.80176, 0.7636), (0.92289, 0.90805), (1, 1)]),
    (MIXED, 0, [(0, 0), (0.0736, 0.23892), (0.17407, 0.56508), (0.25, 0.5)]),
    (MIXED, 2, [(0.6, 0.2), (0.72148, 0.09587), (0.9529, 0.84709), (1, 1)]),
    (LATE, 1, [(0.4, 0.1), (0.51642, 0.1291), (0.63344, 0.80015), (0.7, 0.9)]),
]


@pytest.mark.parametrize("points, stretch, expected", FROM_BRYCE)
def test_curve_handles_match_bryce(points, stretch, expected):
    built = curve.controls(points, stretch)
    for mine, theirs in zip(built, expected):
        assert mine == pytest.approx(theirs, abs=2e-5)


def test_straight_stretches_and_two_point_curves_are_lines():
    assert curve.controls(MIXED, 1) is None
    assert curve.controls([(0, 0, 2), (1, 1, 2)], 0) is None
    assert curve.progress([(0, 0, 0), (0.5, 0.9, 0), (1, 1, 0)], 0.25) == pytest.approx(0.45)
    assert curve.progress(None, 0.3) == 0.3


@pytest.mark.parametrize("shape", curve.SHAPES)
def test_named_shapes_run_forwards_from_start_to_end(shape):
    points = curve.points(shape)
    walked = [curve.progress(points, i / 100) for i in range(101)]
    assert walked[0] == 0 and walked[-1] == 1
    assert all(b >= a - 1e-9 for a, b in zip(walked, walked[1:]))
    assert curve.describe(curve.decode(curve.encode(points))) == shape


def test_hold_stays_put_until_the_end():
    points = curve.points("hold")
    assert curve.progress(points, 0.99) == 0 and curve.progress(points, 1) == 1


def test_the_saved_curve_has_bryces_layout():
    blob = curve.encode([(0, 0, curve.CURVED), (1, 1, 0)])
    assert len(blob) == 44 + 2 * 28
    assert struct.unpack_from("<3I", blob) == (1, 1, 2) and not any(blob[12:44])
    assert struct.unpack_from("<ffI", blob, 44 + 28) == (1.0, 1.0, 0)


def test_a_big_endian_curve_can_be_read():
    blob = bytearray(44 + 56)
    struct.pack_into(">3I", blob, 0, 1, 0, 2)
    struct.pack_into(">ffI", blob, 72, 1.0, 1.0, 2)
    assert curve.decode(bytes(blob)) == [(0.0, 0.0, 0), (1.0, 1.0, 2)]


@pytest.mark.parametrize("spec, message", [
    ("bouncy", "curve must be one of"),
    ([[0, 0]], "2 to 64"),
    ([[0.1, 0], [1, 1]], "start at time 0"),
    ([[0, 0], [0.5, 0.2], [0.5, 0.4], [1, 1]], "times must increase"),
    ([[0, 0], [1, 2]], "between 0 and 1"),
    ([[0, 0], [1, 1, "wavy"]], '"smooth" or "straight"'),
])
def test_bad_curves_are_refused(spec, message):
    with pytest.raises(BryceError, match=message):
        curve.points(spec)


def slide(**more) -> Br3:
    scene = Br3.from_bytes(basic_scene())
    scene.set_keyframes("Cube 1", [{"frame": 0, "position": [0, 10, 0]},
                                   {"frame": 40, "position": [40, 10, 0]}], **more)
    return Br3.from_bytes(scene.data)


def track(scene, name="Cube 1"):
    return scene._payload(scene._linked(scene.find(name)[1], "Obj_ePosition"))


def where(scene, frame, name="Cube 1"):
    scene.set_time({"frame": frame})
    return scene.describe(*scene.find(name))["position"]


def test_a_curve_is_stored_after_the_keys_and_counted_in_the_header():
    plain, shaped = track(slide()), track(slide(curve="ease"))
    blob = curve.encode(curve.points("ease"))
    assert shaped[:16] == plain[:16] and shaped[148:len(plain)] == plain[148:]
    assert struct.unpack_from("<I", shaped, 16)[0] == len(blob)
    assert shaped[len(plain):] == blob
    assert keyframes.count(shaped) == 2
    assert keyframes.single_size(shaped) == keyframes.single_size(plain)


def test_a_curve_changes_the_pose_between_keys_but_not_on_them():
    straight, eased = slide(), slide(curve="ease_in")
    # With two keys Bryce starts fast, slows in the middle and speeds up again.
    assert where(straight, 10)[0] == pytest.approx(11.875, abs=0.01)
    assert where(straight, 20)[0] == pytest.approx(20, abs=0.01)
    assert where(eased, 10)[0] < 5
    assert where(eased, 0)[0] == 0 and where(eased, 40)[0] == pytest.approx(40, abs=0.01)
    assert eased.describe(*eased.find("Cube 1"))["curves"] == {"position": "ease_in"}


def test_a_curve_can_be_added_changed_and_removed_without_new_keys():
    scene = slide()
    scene.set_keyframes("Cube 1", curve="hold")
    assert where(scene, 39)[0] == pytest.approx(0, abs=0.01)
    scene.set_keyframes("Cube 1", curve={"position": [[0, 0], [0.5, 0.8], [1, 1]]})
    assert scene.curves_of(scene.find("Cube 1")[1])["position"] == [
        [0.0, 0.0, "smooth"], [0.5, 0.8, "smooth"], [1.0, 1.0, "smooth"]]
    scene.set_keyframes("Cube 1", curve="linear")
    assert track(scene) == track(slide())


def test_new_keys_keep_the_curve_and_a_single_key_drops_it():
    scene = slide(curve="ease")
    scene.set_keyframes("Cube 1", [{"frame": 0, "position": [0, 10, 0]},
                                   {"frame": 20, "position": [20, 10, 0]}])
    assert scene.curves_of(scene.find("Cube 1")[1]) == {"position": "ease"}
    scene.set_keyframes("Cube 1", [{"frame": 0, "position": [5, 10, 0]}])
    assert not any(track(scene)[16:20])
    assert len(track(scene)) == len(track(Br3.from_bytes(basic_scene())))


def test_a_plain_move_keeps_the_curve():
    scene = slide(curve="ease")
    scene.place(scene.find("Cube 1")[1], [100, 10, 0], [1, 1, 1], (0, 0, 0, 1))
    assert scene.curves_of(scene.find("Cube 1")[1]) == {"position": "ease"}


def test_a_curve_needs_something_to_shape():
    scene = Br3.from_bytes(basic_scene())
    with pytest.raises(BryceError, match="two keys or more"):
        scene.set_keyframes("Cube 1", curve="ease")


def camera_move(**more) -> Br3:
    scene = Br3.from_bytes(basic_scene())
    scene.set_keyframes(target="camera", keys=[
        {"frame": 0, "position": [0, 20, -100], "heading": 0, "fov": 60},
        {"frame": 30, "position": [60, 20, -100], "heading": 360, "fov": 30}], **more)
    return Br3.from_bytes(scene.data)


def test_the_camera_can_be_animated_and_both_views_follow():
    scene = camera_move()
    scene.set_time({"frame": 15})
    for view in ("director", "perspective"):
        seen = scene.camera(view)
        assert seen["position"] == pytest.approx([30, 20, -100], abs=0.01)
        assert seen["heading"] == pytest.approx(180) and seen["fov"] == pytest.approx(45)
    assert set(scene.animated_settings()["camera"]) == {"position", "orientation", "fov"}


def test_each_camera_channel_has_its_own_curve():
    scene = camera_move(curve={"fov": "hold"})
    scene.set_time({"frame": 15})
    assert scene.camera()["fov"] == pytest.approx(60)
    assert scene.camera()["position"][0] == pytest.approx(30, abs=0.01)
    assert scene.animated_settings()["camera"]["fov"]["curve"] == "hold"


def test_look_at_keys_turn_the_short_way():
    scene = Br3.from_bytes(basic_scene())
    result = scene.set_keyframes(target="camera", keys=[
        {"frame": 0, "position": [-5, 10, 20], "look_at": [0, 10, 0]},
        {"frame": 30, "position": [5, 10, 20], "look_at": [0, 10, 0]}])
    first, last = (k["value"]["heading"] for k in
                   result["animation"]["orientation"]["keys"])
    assert abs(last - first) < 180


def test_a_material_can_be_animated():
    scene = Br3.from_bytes(basic_scene())
    scene.set_keyframes("Cube 1", target="material", keys=[
        {"frame": 0, "color": "#ff0000", "diffuse": 0},
        {"frame": 20, "color": "#0000ff", "diffuse": 1}])
    scene.set_time({"frame": 10})
    reread = Br3.from_bytes(scene.data)
    found = reread.material(reread.find("Cube 1")[1])
    assert found["color"] in ("#7f007f", "#800080", "#7f0080", "#80007f")
    assert found["diffuse"] == pytest.approx(0.5)
    assert set(reread.animated_settings()["materials"]["Cube 1"]) == {"color", "diffuse"}


def lamp_scene() -> bytes:
    b = Builder()
    lamp = bytearray(848)
    struct.pack_into("<3f", lamp, 644, 1.0, 1.0, 1.0)
    struct.pack_into("<d", lamp, 656, 10.0)
    ground = b.solid("C3dPlane", "Plane 1", (0, 0.001, 0))
    light = b.solid("C3dConeLight", "Spotlight 1", (0, 5, 0), payload=lamp)
    return b.build([("plne3dob", ground), ("conelght", light)])


def test_a_lamp_without_tracks_is_given_them():
    scene = Br3.from_bytes(lamp_scene())
    scene.set_keyframes("Spotlight 1", target="light", keys=[
        {"frame": 0, "intensity": 0, "color": "#000000"},
        {"frame": 10, "intensity": 100, "color": "#ffffff"}])
    scene = Br3.from_bytes(scene.data)
    ref = scene.find("Spotlight 1")[1]
    payload = scene._payload(ref)
    assert [struct.unpack_from("<I", payload, o)[0] != 0 for o in (688, 692)] == [True, True]
    assert scene._linked(ref, "Light_C") and scene._linked(ref, "Light_Int")
    names = list(scene._classes)
    assert names.index("Light_Int") < names.index("C3dConeLight")
    scene.set_time({"frame": 5})
    shown = scene.light(*scene.find("Spotlight 1"))
    assert shown["intensity"] == pytest.approx(50) and shown["color"] == "#808080"


@pytest.mark.parametrize("change", [
    lambda s: s.set_camera(fov=50),
    lambda s: s.set_material("Cube 1", {"color": "#112233"}),
])
def test_a_plain_change_cannot_wipe_out_motion(change):
    scene = camera_move()
    scene.set_keyframes("Cube 1", target="material", keys=[
        {"frame": 0, "color": "#ff0000"}, {"frame": 30, "color": "#00ff00"}])
    before = scene.data
    with pytest.raises(BryceError, match="animated"):
        change(scene)
    assert scene.data == before


def test_other_material_settings_stay_editable_and_one_key_stills_a_channel():
    scene = camera_move()
    scene.set_keyframes("Cube 1", target="material", keys=[
        {"frame": 0, "color": "#ff0000"}, {"frame": 30, "color": "#00ff00"}])
    scene.set_material("Cube 1", {"diffuse": 0.25})
    scene.set_keyframes(target="camera", keys=[
        {"frame": 0, "position": [0, 5, -50], "heading": 0, "fov": 55}])
    assert "camera" not in scene.animated_settings()
    scene.set_camera(fov=40)


@pytest.mark.parametrize("target, name, keys, message", [
    ("moon", None, [{"frame": 0}], "target must be one of"),
    ("camera", "Cube 1", [{"frame": 0, "fov": 50}], "needs no name"),
    ("material", None, [{"frame": 0, "color": "#000000"}], "needs the object's name"),
    ("camera", None, [{"frame": 0, "zoom": 2}], "unknown camera setting"),
    ("camera", None, [{"frame": 0}], "sets nothing"),
    ("camera", None, [{"frame": 0, "pitch": 120}], "pitch must be"),
    ("camera", None, [{"frame": 0, "look_at": [0, 0, 0], "heading": 1}], "not both"),
    ("light", "Cube 1", [{"frame": 0, "intensity": 5}], "is not a light"),
    ("sun", None, [{"frame": 0, "size": 5}], "unknown sun setting"),
])
def test_bad_setting_keys_change_nothing(target, name, keys, message):
    scene = Br3.from_bytes(basic_scene())
    before = scene.data
    with pytest.raises(BryceError, match=message):
        scene.set_keyframes(name, keys, target=target)
    assert scene.data == before


def test_the_keyframes_operation_reaches_every_target():
    data = basic_scene()
    scene = Br3.from_bytes(data)
    sceneops.apply(scene, [
        {"op": "keyframes", "target": "camera", "curve": "ease",
         "keys": [{"frame": 0, "fov": 60}, {"frame": 15, "fov": 30}]},
        {"op": "keyframes", "target": "camera", "curve": "linear"},
        {"op": "time_marker", "frame": 5}], library=None)
    assert scene.camera()["fov"] == pytest.approx(50)


@pytest.mark.skipif(not os.environ.get("BRYCE3D_SAMPLE_SCENE"),
                    reason="set BRYCE3D_SAMPLE_SCENE to test a real scene")
def test_sun_and_sky_keys_in_a_real_scene():
    sample = os.environ["BRYCE3D_SAMPLE_SCENE"]
    scene = Br3.load(sample)
    scene.set_keyframes(target="sun", keys=[
        {"frame": 0, "azimuth": 90, "altitude": 0, "disc_color": "#ff0000"},
        {"frame": 30, "azimuth": 90, "altitude": 60, "disc_color": "#ffffff"}])
    scene.set_keyframes(target="sky", keys=[
        {"frame": 0, "HazeAmount": 0, "HorizontC": "#ff0000"},
        {"frame": 30, "HazeAmount": 20, "HorizontC": "#0000ff"}])
    scene.set_time({"frame": 15})
    scene = Br3.from_bytes(scene.data)
    assert scene.sun()["altitude"] == pytest.approx(30, abs=0.1)
    assert scene.sun()["disc_color"] == pytest.approx([1.0, 0.5, 0.5], abs=0.01)
    assert scene.sky()["HazeAmount"] == pytest.approx(10)
    owner = scene._payload(scene._first("C3dBaseSky"))
    assert struct.unpack_from("<d", owner, 456)[0] == pytest.approx(10)       # drawn copy
    assert struct.unpack_from("<3f", owner, 380) == pytest.approx((0.5, 0, 0.5), abs=0.01)
    with pytest.raises(BryceError, match="animated"):
        scene.set_sun(altitude=10)
    with pytest.raises(BryceError, match="animated"):
        scene.set_sky({"HazeAmount": 3})


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test real presets")
def test_mac_models_are_left_out_of_the_object_list():
    from bryce3d_mcp import presets
    folder = presets.folder(os.environ["BRYCE3D_INSTALL"])
    for found in presets.entries(folder, "objects"):
        assert not presets.load(found).mac_model(found.key)
