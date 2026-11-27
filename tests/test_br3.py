import json
import math
import os
import struct

import pytest

from bryce3d_mcp import preview, sceneops, server
from bryce3d_mcp.br3 import (UNIT, Br3, camera_matrix, euler_from_quaternion,
                             quaternion_from_euler, rotation_matrix, world_centre)
from bryce3d_mcp.controller import BryceController
from bryce3d_mcp.errors import BryceError, ConfirmationRequired
from bryce3d_mcp.win32 import open_request
from fake import FakeBackend
from scene_files import basic_scene, template_scene


def counts(scene):
    return {name: len(rows) for name, rows in scene._rows.items() if rows}


def by_name(scene):
    return {o["name"]: o for o in scene.objects()}


@pytest.fixture
def scene():
    return Br3.from_bytes(basic_scene())


@pytest.fixture
def library():
    return Br3.from_bytes(template_scene())


# -- rules checked against what Bryce itself saved --------------------------------
def test_rotation_matrices_match_the_ones_bryce_saved():
    half = math.sqrt(0.5)
    expected_y45 = [[half, 0, half], [0, 1, 0], [-half, 0, half]]
    expected_x90 = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    for degrees, expected in (((0, 45, 0), expected_y45), ((90, 0, 0), expected_x90)):
        matrix = rotation_matrix(quaternion_from_euler(degrees))
        for got, want in zip(matrix, expected):
            assert got == pytest.approx(want, abs=1e-9)


def test_origin_is_scaled_and_rotated_but_position_is_not():
    # The ring from the live test: Bryce drew it at (0, 4.42, -6.12) file units.
    centre = world_centre((0, 1, 0), (0, 4.371, 2.441), (1.4, 1.4, 1.4),
                          quaternion_from_euler((90, 0, 0)))
    assert centre == pytest.approx([0, 4.4174, -6.1194], abs=1e-3)


def test_camera_matrix_matches_bryce():
    matrix = camera_matrix(-0.19173310621350803, math.pi)
    assert matrix[0] == pytest.approx([-1, 0, 0], abs=1e-9)
    assert matrix[1] == pytest.approx([0, 0.98167, 0.19057], abs=1e-3)
    assert matrix[2] == pytest.approx([0, 0.19057, -0.98167], abs=1e-3)


def test_euler_round_trip():
    angles = (12.0, -33.0, 71.0)
    assert euler_from_quaternion(quaternion_from_euler(angles)) == pytest.approx(angles)


# -- reading and editing ------------------------------------------------------------
def test_summary(scene):
    cube = by_name(scene)["Cube 1"]
    assert cube["kind"] == "cube" and cube["material"]["color"] == "#ff0000"
    assert cube["position"] == pytest.approx([25.6, 29.696, 0], abs=1e-3)
    assert scene.camera()["position"] == pytest.approx([0, 30.72, -143.36], abs=1e-3)
    assert scene.sky()["ShadowInt"] == 0.1


def test_moving_writes_position_and_clears_origin(scene):
    info = scene.set_object("Cube 1", position=[-10, 20, 30], scale=[2, 1, 1],
                            rotation=[0, 45, 0], material={"color": "#00ff00", "diffuse": 0.5})
    again = by_name(Br3.from_bytes(scene.data))["Cube 1"]
    assert again == info
    assert again["position"] == pytest.approx([-10, 20, 30])
    assert again["size"] == [40.96, 20.48, 20.48]
    assert again["material"] == {"color": "#00ff00", "diffuse": 0.5}
    key = scene.find("Cube 1")[1]
    assert scene._vector(key, "Obj_eOrigin") == (0, 0, 0)


def test_turning_keeps_the_centre(scene):
    before = by_name(scene)["Cube 1"]["position"]
    after = scene.set_object("Cube 1", rotation=[0, 90, 0])["position"]
    assert after == pytest.approx(before, abs=1e-3)


def test_rename(scene):
    scene.set_object("Cube 1", new_name="Box")
    assert "Box" in by_name(Br3.from_bytes(scene.data))
    with pytest.raises(BryceError, match="already exists"):
        scene.set_object("Box", new_name="Plane 1")


def test_add_kinds_the_scene_never_had(scene, library):
    scene.add_object("sphere", library=library, name="Moon", position=[0, 50, 0])
    scene.add_object("terrain", library=library, position=[1, 2, 3])
    reread = Br3.from_bytes(scene.data)
    assert set(by_name(reread)) == {"Plane 1", "Cube 1", "Moon", "Terrain 1"}
    assert counts(reread)["C3dSphere"] == 1 and counts(reread)["HF_eHFMem"] == 1
    assert reread.terrain_heights("Terrain 1") == library.terrain_heights("Terrain 1")
    ids = [struct.unpack_from(">I", row)[0] for rows in reread._rows.values() for row in rows]
    assert len(ids) == len(set(ids))
    # every record has its own copy of each setting
    reread.set_object("Moon", position=[5, 5, 5])
    assert by_name(reread)["Terrain 1"]["position"] == pytest.approx([1, 2, 3])


def test_directory_grows(library):
    full = Br3.from_bytes(basic_scene(entries=0))
    before = full.file.entry_count()
    for _ in range(4):
        full.add_object("sphere", library=library, position=[0, 10, 0])
    reread = Br3.from_bytes(full.data)
    assert reread.file.entry_count() > before
    assert len(reread.objects()) == 6


def test_delete_removes_exactly_what_was_added(scene, library):
    start = counts(scene)
    scene.add_object("terrain", library=library, name="Land")
    scene.duplicate("Cube 1", "Cube Copy")
    scene.delete("Land")
    scene.delete("Cube Copy")
    assert counts(Br3.from_bytes(scene.data)) == start


def test_resized_object_still_rests_on_the_ground(scene, library):
    info = scene.add_object("sphere", library=library, scale=[3, 3, 3])
    assert info["position"][1] == pytest.approx(3 * UNIT, abs=1e-3)


def test_missing_template(scene):
    with pytest.raises(BryceError, match="no torus to copy"):
        scene.add_object("torus")


def test_terrain_writes_both_copies(scene, library):
    scene.add_object("terrain", library=library, name="Land")
    wanted = [(i * 13) % 65536 for i in range(256)]
    scene.set_terrain_heights("Land", wanted)
    reread = Br3.from_bytes(scene.data)
    stored = reread.terrain_heights("Land")[1]
    memory = reread._payload(reread._linked(reread.find("Land")[1], "HF_eHFMem"))
    assert stored == wanted
    assert list(struct.unpack_from(">256H", memory, 208)) == wanted


def test_camera_updates_the_cached_copy(scene):
    got = scene.set_camera(position=[0, 55, -170], look_at=[0, 22, 0], fov=50)
    assert got["position"] == pytest.approx([0, 55, -170]) and got["heading"] == 0
    assert got["pitch"] == pytest.approx(-10.99, abs=0.01)
    payload = scene._payload(scene._first("C3dBaseCamera"))
    assert struct.unpack_from("<3d", payload, 452) == pytest.approx(
        (0, 55 / UNIT, 170 / UNIT))
    flat = [v for row in camera_matrix(math.radians(got["pitch"]), math.pi) for v in row]
    assert struct.unpack_from("<9d", payload, 380) == pytest.approx(flat, abs=1e-3)
    assert struct.unpack_from("<d", payload, 724)[0] == pytest.approx(math.radians(50))


def test_sun_updates_the_cached_copy(scene):
    got = scene.set_sun(azimuth=225, altitude=40)
    assert (got["azimuth"], got["altitude"]) == (225, 40)
    payload = scene._payload(scene._first("C3dSunLight"))
    assert struct.unpack_from("<3d", payload, 860) == pytest.approx(
        (-0.5417, 0.6428, -0.5417), abs=1e-3)


# -- batches --------------------------------------------------------------------------
@pytest.mark.parametrize("operations, message", [
    ([{"op": "explode"}], "'op' must be one of"),
    ([{"op": "add"}], "missing kind"),
    ([{"op": "set", "name": "Cube 1", "colour": "#fff"}], "unknown field"),
    ([{"op": "set", "name": "Nope", "position": [0, 0, 0]}], "no object named"),
    ([{"op": "set", "name": "Cube 1", "material": {"color": "red"}}], "#rrggbb"),
    ([{"op": "set", "name": "Cube 1", "scale": [0, 1, 1]}], "positive"),
    ([{"op": "sky", "settings": {"Nope": 1}}], "unknown sky settings"),
])
def test_bad_operations(scene, operations, message):
    with pytest.raises(BryceError, match=message):
        sceneops.apply(scene, operations)


# -- previewer ------------------------------------------------------------------------
def test_preview_reports_where_objects_land(scene, library):
    scene.set_camera(position=[0, 30, -150], heading=0, pitch=0)
    scene.set_object("Cube 1", position=[0, 30, 0])
    scene.add_object("sphere", library=library, name="Far Right", position=[900, 30, 0])
    scene.add_object("sphere", library=library, name="Behind", position=[0, 30, -400])
    picture, report = preview.render(scene, 480, 360)
    found = {r["name"]: r for r in report}
    assert found["Cube 1"]["preview"] == "visible"
    left, top, right, bottom = found["Cube 1"]["box"]
    assert (left + right) / 2 == pytest.approx(240, abs=2)
    assert (top + bottom) / 2 == pytest.approx(180, abs=3)
    assert found["Far Right"]["preview"] == "out of frame: right"
    assert found["Behind"]["preview"] == "behind the camera"
    red, green, blue = picture.getpixel((240, 180))
    assert red > green and red > blue


def test_obj_export(scene):
    text, count = preview.to_obj(scene)
    assert count == 1 and text.count("\nv ") == 8 and text.count("\nf ") == 12


# -- tools ----------------------------------------------------------------------------
@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("BRYCE3D_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("BRYCE3D_TEMPLATES", raising=False)
    fake = FakeBackend()
    monkeypatch.setattr(server, "_controller", BryceController(fake))
    (tmp_path / "templates.br3").write_bytes(template_scene())
    (tmp_path / "start.br3").write_bytes(basic_scene())
    return tmp_path, fake


def test_edit_loop_uses_no_input(workspace):
    folder, fake = workspace
    first = server.br3_edit(str(folder / "start.br3"), "step", [
        {"op": "add", "kind": "sphere", "name": "Ball", "position": [0, 20, 0],
         "material": {"color": "#0000ff"}}])
    path = json.loads(first[0])["path"]
    second = json.loads(server.br3_edit(path, "step", [{"op": "delete", "name": "Ball"}])[0])
    assert second["path"] != path
    assert (folder / "start.br3").read_bytes() == basic_scene()
    assert fake.log == []


def test_a_failed_batch_writes_nothing(workspace):
    folder, _ = workspace
    before = sorted(os.listdir(folder))
    with pytest.raises(server.ToolError, match="operation 1 .*Nothing was written"):
        server.br3_edit(str(folder / "start.br3"), "step", [
            {"op": "delete", "name": "Cube 1"}, {"op": "delete", "name": "Cube 1"}])
    assert sorted(os.listdir(folder)) == before


def test_new_scene_from_templates(workspace):
    info = json.loads(server.br3_new("fresh", keep=["Sphere 1"])[0])
    assert [o["name"] for o in Br3.load(info["path"]).objects()] == ["Sphere 1"]
    status = json.loads(server.br3_status())
    assert status["can_add"] == ["sphere", "terrain"]


def test_render_result_reads_the_bitmap(workspace):
    from PIL import Image
    folder, _ = workspace
    with pytest.raises(server.ToolError, match="does not exist yet"):
        server.br3_render_result(str(folder / "start.br3"))
    Image.new("RGB", (480, 360), "navy").save(folder / "start.bmp")
    info = json.loads(server.br3_render_result(str(folder / "start.br3"), "shot")[0])
    assert info["size"] == [480, 360] and os.path.isfile(info["exported"])


def test_open_by_message_needs_confirmation_and_sends_no_input(workspace):
    folder, fake = workspace
    controller = server.get_controller()
    with pytest.raises(ConfirmationRequired):
        controller.open_by_message(str(folder / "start.br3"))
    controller.open_by_message(str(folder / "start.br3"), confirm=True)
    assert fake.log == [("open_file", str(folder / "start.br3"))]


def test_open_request():
    assert open_request("C:\\my scenes\\a.br3", "cp1252") == b'"C:\\my scenes\\a.br3"\x00'
    for bad in ('C:\\a"b.br3', "C:\\\u732b.br3", "C:\\" + "x" * 300 + ".br3"):
        with pytest.raises(BryceError):
            open_request(bad, "cp1252")


@pytest.mark.skipif(not os.environ.get("BRYCE3D_SAMPLE_SCENE"),
                    reason="set BRYCE3D_SAMPLE_SCENE to a .br3 saved by Bryce")
def test_real_scene_round_trip():
    path = os.environ["BRYCE3D_SAMPLE_SCENE"]
    scene, library = Br3.load(path), Br3.load(path)
    start = counts(scene)
    kinds = sorted({o["kind"] for o in library.objects()})
    added = [scene.add_object(kind, library=library, position=[0, 30, 0])["name"]
             for kind in kinds]
    scene = Br3.from_bytes(scene.data)
    for name in added:
        scene.delete(name)
    assert counts(Br3.from_bytes(scene.data)) == start
    assert Br3.from_bytes(scene.data).objects() == library.objects()


def test_open_file_message(monkeypatch):
    """Read the message back from memory the way the receiving window does."""
    import ctypes
    from types import SimpleNamespace

    from bryce3d_mcp import win32

    seen = {}

    def send(window, message, wparam, address, flags, timeout, answer):
        request = ctypes.cast(address, ctypes.POINTER(win32._COPYDATASTRUCT)).contents
        seen.update(window=window, message=message,
                    text=ctypes.string_at(request.lpData, request.cbData))
        return 1

    monkeypatch.setattr(win32, "open_request",
                        lambda path, real=win32.open_request: real(path, "cp1252"))
    backend = win32.Win32Backend.__new__(win32.Win32Backend)
    backend.user32 = SimpleNamespace(SendMessageTimeoutW=send)
    backend._find_window = lambda window_class=None: (seen.update(wanted=window_class), 42)[1]
    backend.open_file("C:\\a b\\scene.br3")
    assert seen == {"wanted": "CWinAppl", "window": 42, "message": 0x4A,
                    "text": b'"C:\\a b\\scene.br3"\x00'}


def test_terrain_edit_on_a_scene_updates_both_copies(workspace):
    folder, _ = workspace
    result = server.terrain_edit(str(folder / "templates.br3"), "edited",
                                 [{"op": "invert"}])
    path = next(str(p) for p in folder.iterdir() if p.name.startswith("edited"))
    scene = Br3.load(path)
    key = scene.find("Terrain 1")[1]
    stored = scene.terrain_heights("Terrain 1")[1]
    memory = scene._payload(scene._linked(key, "HF_eHFMem"))
    assert stored != Br3.load(str(folder / "templates.br3")).terrain_heights("Terrain 1")[1]
    assert list(struct.unpack_from(f">{len(stored)}H", memory, 208)) == stored
    assert result
