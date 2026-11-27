"""Groups, booleans, materials, textures and Bryce-style preset libraries, all
against synthetic files."""

import math
import os
import struct

import numpy as np
import pytest

from bryce3d_mcp import presets, preview, sceneops
from bryce3d_mcp.br3 import (SKY_CACHE, Br3, camera_matrix, quaternion_from_euler,
                             quaternion_from_matrix, rotation_matrix)
from bryce3d_mcp.errors import BryceError
from scene_files import (basic_scene, group_donor, material_library, sky_library,
                         template_scene)


@pytest.fixture
def install(tmp_path):
    folder = tmp_path / "Bryce" / "Presets"
    folder.mkdir(parents=True)
    (folder / "Boolean Objects.obp").write_bytes(group_donor())
    (folder / "Simple&Fast.mat").write_bytes(material_library())
    (folder / "Sky Presets.bsk").write_bytes(sky_library())
    return str(tmp_path / "Bryce")


@pytest.fixture
def scene():
    return Br3.from_bytes(basic_scene())


@pytest.fixture
def library():
    return Br3.from_bytes(template_scene())


def named(scene):
    return {o["name"]: o for o in scene.objects()}


def reread(scene):
    again = Br3.from_bytes(scene.data)
    for ref in again._all_refs():
        again._links(ref)                       # every link must still resolve
    return again


def run(scene, library, install, *operations):
    sceneops.apply(scene, list(operations), library, install)
    return reread(scene)


@pytest.mark.parametrize("angles", [(0, 0, 0), (30, 40, 50), (180, 0, 0), (0, 90, 0),
                                    (-170, 20, 95), (0, 0, 180), (90, -45, 10)])
def test_matrix_and_quaternion_agree(angles):
    matrix = rotation_matrix(quaternion_from_euler(angles))
    back = rotation_matrix(quaternion_from_matrix(matrix))
    assert [v for row in back for v in row] == pytest.approx(
        [v for row in matrix for v in row], abs=1e-9)


def test_presets_are_listed_and_found_by_name(install):
    folder = presets.folder(install)
    assert [p.name for p in presets.entries(folder, "materials")] == ["Mirror", "Checker"]
    assert presets.find(folder, "skies", "sunset").name == "Sunset"
    with pytest.raises(BryceError, match="similar: Mirror"):
        presets.find(folder, "materials", "mirr")
    with pytest.raises(BryceError, match="BRYCE3D_INSTALL"):
        presets.folder(None)


def test_insert_a_model_preset_then_ungroup_it(scene, library, install):
    after = run(scene, library, install,
                {"op": "insert_preset", "preset": "Twin Balls", "position": [0, 50, 0],
                 "scale": 2})
    group = named(after)["Twin Balls"]
    assert group["kind"] == "group" and len(group["members"]) == 2
    assert group["position"] == pytest.approx([0, 50, 0], abs=1e-3)
    assert group["scale"] == [2, 2, 2]
    parts = run(after, library, install, {"op": "ungroup", "name": "Twin Balls"})
    balls = [o for o in parts.objects() if o["kind"] == "sphere"]
    # Members were 1 file unit either side, doubled by the group.
    assert sorted(o["position"][0] for o in balls) == pytest.approx([-20.48, 20.48], abs=1e-3)
    assert len({o["name"] for o in balls}) == 2
    assert not parts._rows.get("C3dGroupType") and not parts._rows.get("C3dObjList")


def test_group_and_ungroup_leave_objects_where_they_were(scene, library, install):
    first = run(scene, library, install,
                {"op": "add", "kind": "sphere", "name": "Ball", "position": [40, 30, 20],
                 "rotation": [0, 30, 0]})
    before = named(first)
    grouped = run(first, library, install,
                  {"op": "group", "names": ["Cube 1", "Ball"], "name": "Pair"})
    pair = named(grouped)["Pair"]
    assert set(named(grouped)) == {"Plane 1", "Pair"}
    member = {m["name"]: m for m in pair["members"]}["Ball"]
    assert [m + p for m, p in zip(member["position"], pair["position"])] == pytest.approx(
        before["Ball"]["position"], abs=0.01)
    apart = named(run(grouped, library, install, {"op": "ungroup", "name": "Pair"}))
    for name in ("Cube 1", "Ball"):
        for field in ("position", "scale", "rotation"):
            assert apart[name][field] == pytest.approx(before[name][field], abs=0.01)


def test_boolean_roles(scene, library, install):
    after = run(scene, library, install,
                {"op": "add", "kind": "sphere", "name": "Bite", "position": [0, 20, 0]},
                {"op": "group", "names": ["Cube 1", "Bite"], "negative": ["Bite"],
                 "name": "Bitten"})
    bitten = named(after)["Bitten"]
    assert bitten["boolean"] == "positive"
    assert {m["name"]: m["boolean"] for m in bitten["members"]} == {
        "Cube 1": "positive", "Bite": "negative"}
    report = preview.render(after, 160, 120)[1]
    assert "negative part" in next(r for r in report if r["name"] == "Bitten")["note"]


def test_grouped_scenes_survive_copy_move_delete_and_export(scene, library, install):
    after = run(scene, library, install, {"op": "insert_preset", "preset": "Twin Balls"},
                {"op": "duplicate", "name": "Twin Balls", "new_name": "Again",
                 "position": [90, 10, 0]})
    assert len(after._rows["C3dGroupType"]) == len(after._rows["C3dObjList"]) == 2
    assert {r[:4] for r in after._rows["C3dGroupType"]} == {
        r[:4] for r in after._rows["C3dObjList"]}
    assert preview.to_obj(after)[1] >= 3
    gone = run(after, library, install, {"op": "delete", "name": "Again"})
    assert len(gone._rows["C3dGroupType"]) == 1
    assert len(named(gone)["Twin Balls"]["members"]) == 2


def test_material_preset_then_adjust(scene, library, install):
    after = run(scene, library, install,
                {"op": "material_preset", "name": "Cube 1", "preset": "Mirror",
                 "material": {"transparency": 0.5, "color": "#102030"}})
    material = named(after)["Cube 1"]["material"]
    assert material["reflection"] == 1.0 and material["transparency"] == 0.5
    assert material["color"] == "#102030"


def test_a_plain_value_replaces_only_that_channels_texture(scene, library, install):
    after = run(scene, library, install,
                {"op": "material_preset", "name": "Cube 1", "preset": "Checker"})
    assert named(after)["Cube 1"]["material"]["textures"] == {
        "color": "checks", "reflection": "checks"}
    one = run(after, library, install,
              {"op": "set", "name": "Cube 1", "material": {"color": "#ff0000"}})
    assert named(one)["Cube 1"]["material"]["textures"] == {"reflection": "checks"}
    none = run(one, library, install,
               {"op": "set", "name": "Cube 1", "material": {"reflection": 0.2}})
    assert "textures" not in named(none)["Cube 1"]["material"]
    assert not none._rows.get("C3dTexture")


def test_texture_by_name(scene, library, install):
    after = run(scene, library, install,
                {"op": "texture", "name": "Cube 1", "texture": "Checks",
                 "channels": ["bump"], "strength": 0.25})
    material = named(after)["Cube 1"]["material"]
    assert material["textures"] == {"bump": "checks"} and material["bump"] == 0.25
    with pytest.raises(BryceError, match="available: checks"):
        run(after, library, install,
            {"op": "texture", "name": "Cube 1", "texture": "nope", "channels": ["bump"]})


def texture_links(scene, name):
    """(type tag, link, textured flag) for the material channels of one object."""
    from bryce3d_mcp.br3 import MATERIAL_CHANNELS
    payload = scene._payload(scene._own_material(name))
    found = {channel: (bytes(payload[block - 4:block]),
                       struct.unpack_from("<I", payload, block)[0],
                       struct.unpack_from("<I", payload, block + 20)[0])
             for channel, (block, _, _) in MATERIAL_CHANNELS.items() if block}
    return found, struct.unpack_from("<I", payload, 268)[0]


def test_an_applied_texture_names_its_type_and_removing_it_clears_that(scene, library,
                                                                       install):
    after = run(scene, library, install,
                {"op": "texture", "name": "Cube 1", "texture": "checks",
                 "channels": ["color", "bump"]})
    links, flags = texture_links(after, "Cube 1")
    for channel in ("color", "bump"):
        tag, link, textured = links[channel]
        assert tag == b"xtrb" and link and textured == 0x10000000
    assert links["diffuse"][:2] == (bytes(4), 0) and flags & 0x40
    plain = run(after, library, install,
                {"op": "set", "name": "Cube 1", "material": {"bump": 0.1}})
    links, _ = texture_links(plain, "Cube 1")
    assert links["bump"] == (bytes(4), 0, 0) and links["color"][0] == b"xtrb"


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test against real preset files")
def test_every_textured_channel_in_real_presets_names_its_type():
    from bryce3d_mcp import presets
    from bryce3d_mcp.br3 import _TEXTURE_TAGS, MATERIAL_CHANNELS
    folder = presets.folder(os.environ["BRYCE3D_INSTALL"])
    checked = 0
    for file_name in os.listdir(folder):
        source = presets.donor(folder, file_name)
        for row in source._rows.get("C3dBaseMaterial", []):
            ref = ("C3dBaseMaterial", struct.unpack_from(">I", row)[0])
            payload, links = source._payload(ref), dict(source._links(ref))
            for channel in source._textures(ref):
                block = MATERIAL_CHANNELS[channel][0]
                assert payload[block - 4:block] == _TEXTURE_TAGS[links[block][0]]
                assert struct.unpack_from("<I", payload, 268)[0] & 0x40
                checked += 1
    assert checked > 100


def test_sky_preset_writes_the_scenes_own_records(scene, library, install):
    tracks = set(scene._scene_settings("C3dBaseSky", "Sky_e"))
    after = run(scene, library, install, {"op": "sky_preset", "preset": "Sunset"})
    assert set(after._scene_settings("C3dBaseSky", "Sky_e")) == tracks
    assert after.sky()["BaseC"] == [1.0, 0.5, 0.0] and after.sky()["ShadowInt"] == 0.7
    assert after.sun()["azimuth"] == pytest.approx(90)


@pytest.mark.skipif(not (os.environ.get("BRYCE3D_INSTALL")
                         and os.environ.get("BRYCE3D_SAMPLE_SCENE")),
                    reason="set BRYCE3D_INSTALL and BRYCE3D_SAMPLE_SCENE to test real presets")
def test_every_real_preset_applies():
    folder = presets.folder(os.environ["BRYCE3D_INSTALL"])
    sample = Br3.load(os.environ["BRYCE3D_SAMPLE_SCENE"])
    target = sample.objects()[-1]["name"]
    for category in presets.CATEGORIES:
        for found in presets.entries(folder, category):
            scene = Br3.from_bytes(sample.data)
            source = presets.load(found)
            if category == "objects":
                scene.insert_preset(source, found.key, found.name, library=sample,
                                    position=[0, 20, 0])
            elif category == "materials":
                scene.apply_material_preset(target, source, found.key)
            else:
                scene.apply_sky_preset(source, found.key)
            reread(scene).summary()


# -- camera tilt, sky caches, texture scale ---------------------------------------
@pytest.mark.parametrize("bank", [-90, -12.5, 0, 12.5, 180])
def test_banked_camera_matrix_stays_a_rotation(bank):
    rows = camera_matrix(0.3, -1.1, math.radians(bank))
    for i in range(3):
        for j in range(3):
            assert sum(a * b for a, b in zip(rows[i], rows[j])) == pytest.approx(
                float(i == j), abs=1e-12)
    assert rows[2] == camera_matrix(0.3, -1.1)[2]       # it rolls about the view axis


def test_bank_is_written_to_the_cache_and_both_views(scene):
    scene.set_camera(bank=15)
    again = reread(scene)
    for view in ("director", "perspective"):
        assert again.camera(view)["bank"] == 15
    key = again._camera_key("perspective")
    assert struct.unpack_from("<9d", again._payload(key), 380)[1] == pytest.approx(
        math.sin(math.radians(15)))
    again.set_camera(fov=50)                            # kept unless given
    assert again.camera()["bank"] == 15
    with pytest.raises(BryceError, match="bank"):
        again.set_camera(bank=400)


def test_positive_bank_drops_the_right_end_of_the_horizon(scene):
    def horizon(bank):
        scene.set_camera(position=[0, 30, -140], heading=0, pitch=0, bank=bank)
        view = preview._View(scene.camera(), 400, 300)
        far = np.array([[-50000.0, 30, 100000], [50000.0, 30, 100000]])
        return view.to_pixels(view.to_camera(far))[:, 1]

    left, right = horizon(0)
    assert left == pytest.approx(right)
    left, right = horizon(10)
    assert right > left + 20                            # picture rows count downwards
    picture, _ = preview.render(scene, 200, 150)
    assert picture.size == (200, 150)


def test_sky_and_sun_changes_reach_the_cached_copies(scene):
    scene.set_sky({"BaseC": [0.9, 0.1, 0.2], "ShadowInt": 0.6})
    sky = reread(scene)._payload(scene._first("C3dBaseSky"))
    assert struct.unpack_from("<3f", sky, SKY_CACHE["BaseC"]) == pytest.approx((0.9, 0.1, 0.2))
    assert struct.unpack_from("<d", sky, SKY_CACHE["ShadowInt"])[0] == pytest.approx(0.6)


def test_moon_phase_settings_have_cache_places():
    assert [SKY_CACHE[n] for n in ("MoonPhAng", "MoonPhES", "MoonPhTA", "MoonPhHard")] \
        == [680, 688, 696, 704]


def textured_scene(scene, library, install):
    return run(scene, library, install,
               {"op": "material_preset", "name": "Cube 1", "preset": "Checker"})


def test_texture_scale(scene, library, install):
    scene = textured_scene(scene, library, install)
    assert named(scene)["Cube 1"]["material"]["texture_scales"] == {
        "checks": [0.25, 0.25, 0.5]}
    scene = run(scene, library, install,
                {"op": "texture_scale", "name": "Cube 1", "factor": 2})
    assert named(scene)["Cube 1"]["material"]["texture_scales"]["checks"] == [0.5, 0.5, 1.0]
    scene = run(scene, library, install, {"op": "texture_scale", "name": "Cube 1",
                                          "factor": [1, 3, 0.5], "channels": ["color"]})
    assert named(scene)["Cube 1"]["material"]["texture_scales"]["checks"] == [0.5, 1.5, 0.5]


@pytest.mark.parametrize("change, message", [
    ({"factor": 0}, "between"), ({"factor": 2, "channels": ["bump"]}, "no texture on bump")])
def test_texture_scale_refusals(scene, library, install, change, message):
    scene = textured_scene(scene, library, install)
    with pytest.raises(BryceError, match=message):
        sceneops.apply(scene, [{"op": "texture_scale", "name": "Cube 1", **change}],
                       library, install)
