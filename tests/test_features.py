"""Groups, booleans, materials, textures and Bryce-style preset libraries, all
against synthetic files."""

import math
import os
import struct

import numpy as np
import pytest

from bryce3d_mcp import presets, preview, sceneops
from bryce3d_mcp.br3 import (SKY_CACHE, UNIT, Br3, camera_matrix, quaternion_from_euler,
                             quaternion_from_matrix, rotation_matrix)
from bryce3d_mcp.errors import BryceError
from scene_files import (basic_scene, group_donor, light_scene, material_library, sky_library,
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


def flag_word(scene, ref):
    return struct.unpack_from("<I", scene._payload(ref), 272)[0]


def test_boolean_parts_carry_the_flags_bryce_gives_them(scene, library, install):
    after = run(scene, library, install,
                {"op": "add", "kind": "sphere", "name": "Bite", "position": [0, 20, 0]},
                {"op": "group", "names": ["Cube 1", "Bite"], "negative": ["Bite"],
                 "name": "Bitten"})
    kind, group = after.find("Bitten")
    assert flag_word(after, group) == 0x04020008
    assert {after._name(r): flag_word(after, r) for _, r in after._members(group)} == {
        "Cube 1": 0x10000048, "Bite": 0x10000050}
    apart = run(after, library, install, {"op": "ungroup", "name": "Bitten"})
    assert flag_word(apart, apart.find("Bite")[1]) == 0x10


def test_a_plain_group_has_no_boolean_flags(scene, library, install):
    after = run(scene, library, install,
                {"op": "add", "kind": "sphere", "name": "Ball", "position": [30, 20, 0]},
                {"op": "group", "names": ["Cube 1", "Ball"], "name": "Pair"})
    _, group = after.find("Pair")
    assert flag_word(after, group) == 0x04000000
    assert {flag_word(after, r) for _, r in after._members(group)} == {0}


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test against real preset files")
def test_every_boolean_part_in_real_presets_has_both_flags():
    from bryce3d_mcp import presets
    folder = presets.folder(os.environ["BRYCE3D_INSTALL"])
    checked = 0
    for file_name in os.listdir(folder):
        source = presets.donor(folder, file_name)
        for row in source._rows.get("C3dObjList", []):
            for _, ref, _ in source._list_items(("C3dObjList",
                                                 struct.unpack_from(">I", row)[0])):
                flags = flag_word(source, ref)
                if flags & 0x38:
                    assert flags & 0x10000040 == 0x10000040
                    checked += 1
    assert checked > 150


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


def sky_flags(scene):
    owner = scene._first("C3dBaseSky")
    return struct.unpack_from("<I", scene._payload(owner), 268)[0]


def test_sky_switches_set_one_mode_at_a_time(scene):
    sceneops.apply(scene, [{"op": "sky", "switches": {"mode": "darker"}}])
    assert sky_flags(scene) & 0xF == 0x1 | 0x4
    sceneops.apply(scene, [{"op": "sky", "switches": {"mode": "custom"}}])
    assert sky_flags(scene) & 0xF == 0x1 | 0x8
    assert scene.sky_switches()["mode"] == "custom"
    sceneops.apply(scene, [{"op": "sky", "switches": {"mode": "off"}}])
    assert sky_flags(scene) & 0xF == 0
    assert scene.sky_switches()["mode"] == "off"


def test_sky_switches_leave_other_bits_alone(scene):
    owner = scene._first("C3dBaseSky")
    scene._write(owner, 268, struct.pack("<I", 0x34000180))
    state = scene.set_sky_switches({"rainbow": True, "volumetric": "fine", "stars": True})
    assert sky_flags(scene) == 0x34000180 | 0x4000 | 0x1000000
    assert state["rainbow"] and state["stars"] and state["volumetric"] == "fine"
    assert struct.unpack_from("<2I", scene._payload(owner), 868) == (1, 0)
    scene.set_sky_switches({"rainbow": False, "volumetric": "off"})
    assert sky_flags(scene) == 0x34000180


def test_cloud_layers_share_one_clouds_bit(scene):
    owner = scene._first("C3dBaseSky")
    scene._write(owner, 268, struct.pack("<I", 0x3 | 0x40))      # stale layer, clouds off
    scene.set_sky_switches({"stratus_clouds": True})
    assert sky_flags(scene) & 0x70 == 0x10 | 0x20
    scene.set_sky_switches({"stratus_clouds": False})
    assert sky_flags(scene) & 0x70 == 0


def test_sky_switches_reject_unknown_names_and_values(scene):
    before = sky_flags(scene)
    for bad in ({"mode": "stormy"}, {"fireworks": True}, {"rainbow": "yes"}):
        with pytest.raises(BryceError):
            sceneops.apply(scene, [{"op": "sky", "switches": bad}])
    with pytest.raises(BryceError):
        sceneops.apply(scene, [{"op": "sky"}])
    assert sky_flags(scene) == before


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test real presets")
def test_every_preset_sky_with_atmosphere_has_exactly_one_mode():
    lib = presets.donor(presets.folder(os.environ["BRYCE3D_INSTALL"]), "Sky Presets.bsk")
    checked = 0
    for row in lib._rows["C3dBaseSky"]:
        payload = lib._payload(("C3dBaseSky", struct.unpack_from(">I", row)[0]))
        flags = struct.unpack_from("<I", payload, 268)[0]
        if flags & 1:
            assert sum(bool(flags & bit) for bit in (2, 4, 8)) == 1
            checked += 1
    assert checked > 20


# -- how tall a terrain is --------------------------------------------------------
def test_a_terrain_is_as_tall_as_its_size_says(scene, library, install):
    after = run(scene, library, install,
                {"op": "add", "kind": "terrain", "name": "Hill", "position": [0, 6, 40],
                 "size": [60, 12, 50]})
    grid = after.terrain_heights("Hill")[0]
    heights = [0] * (grid * grid)
    heights[grid * grid // 2 + grid // 2] = 65535            # one peak, flat ground
    after.set_terrain_heights("Hill", heights)
    kind, key = after.find("Hill")
    points = preview.object_parts(after, kind, key)[0][0]
    assert points[:, 1].max() == pytest.approx(12, abs=0.2)      # stands on the ground
    assert points[:, 1].min() >= -1e-6
    assert after.describe(kind, key)["size"] == pytest.approx([60, 12, 50])
    assert after.transform(key)[2][1] == pytest.approx(12 / UNIT)


def test_a_symmetric_lattice_reaches_both_ways():
    flat = [0, 0, 0, 0, 65535, 0, 0, 0, 0]
    up = preview._terrain(3, flat)[0]
    both = preview._terrain(3, flat, mirrored=True)[0]
    assert (up[:, 1].min(), up[:, 1].max()) == (-0.5, 0.5)
    assert (both[:, 1].min(), both[:, 1].max()) == (-1.0, 1.0)


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test real presets")
def test_mountain_presets_stand_at_half_their_height():
    """Most of Bryce's mountains stand on the ground (some are sunk into it on
    purpose). Those sit at half their height, whatever that height is."""
    lib = presets.donor(presets.folder(os.environ["BRYCE3D_INSTALL"]), "Mountains.obp")
    standing = []
    for row in lib._rows["C3dHeightField"]:
        info = lib.describe("terrain",
                            ("C3dHeightField", struct.unpack_from(">I", row)[0]))
        if info["position"][1] == pytest.approx(info["size"][1] / 2, rel=0.01):
            standing.append(round(info["size"][1]))
    assert len(standing) >= 15 and len(set(standing)) >= 3


def test_light_settings_are_stored_as_bryce_stores_them():
    scene = Br3.from_bytes(light_scene())
    assert named(scene)["Radial 1"]["light"] == {
        "color": "#ffffff", "intensity": 25.0, "falloff": "linear", "casts_shadows": True,
        "infinite": False, "visible": False}
    assert named(scene)["Spot 1"]["light"]["edge_softness"] == 0
    scene.set_light("Spot 1", {"color": "#ff8000", "intensity": 80, "falloff": "squared",
                               "casts_shadows": False, "visible": True,
                               "edge_softness": 40})
    again = reread(scene)
    payload = again._payload(again.find("Spot 1")[1])
    assert struct.unpack_from("<I", payload, 272)[0] == 0x1100 | 0x80000000 | 0x800 | 0x200
    assert struct.unpack_from("<3f", payload, 644) == pytest.approx((1, 128 / 255, 0))
    assert struct.unpack_from("<d", payload, 656)[0] == pytest.approx(16.0)  # 80 / 5
    assert struct.unpack_from("<d", payload, 836)[0] == pytest.approx(2.0)   # 40 / 20
    assert named(again)["Spot 1"]["light"] == {
        "color": "#ff8000", "intensity": 80.0, "falloff": "squared", "casts_shadows": False,
        "infinite": False, "visible": True, "edge_softness": 40.0}
    # The falloff choices exclude each other, and a switch can be turned back off.
    again.set_light("Spot 1", {"falloff": "none", "casts_shadows": True, "infinite": True})
    flags = struct.unpack_from("<I", again._payload(again.find("Spot 1")[1]), 272)[0]
    assert flags == 0x1100 | 0x400 | 0x200 | 0x2000


def test_square_lights_take_a_softness_for_each_side():
    scene = Br3.from_bytes(light_scene())
    scene.set_light("Square 1", {"edge_softness": [20, 60]})
    payload = scene._payload(scene.find("Square 1")[1])
    assert struct.unpack_from("<2f", payload, 836) == (1.0, 3.0)
    assert named(scene)["Square 1"]["light"]["edge_softness"] == [20.0, 60.0]


@pytest.mark.parametrize("name, settings, message", [
    ("Radial 1", {"edge_softness": 10}, "no edge softness"),
    ("Radial 1", {"intensity": 101}, "0 to 100"),
    ("Radial 1", {"falloff": "cubic"}, "falloff must be"),
    ("Radial 1", {"infinite": 1}, "true or false"),
    ("Radial 1", {"brightness": 1}, "unknown light settings"),
    ("Radial 1", {}, "must be an object"),
])
def test_bad_light_settings_are_refused(name, settings, message):
    with pytest.raises(BryceError, match=message):
        Br3.from_bytes(light_scene()).set_light(name, settings)


def test_lights_can_be_set_while_being_added(library, install):
    source = Br3.from_bytes(light_scene())
    scene = run(Br3.from_bytes(basic_scene()), source, install,
                {"op": "add", "kind": "spot_light", "name": "Key", "position": [0, 40, 0],
                 "light": {"intensity": 60, "color": "#80c0ff"}},
                {"op": "duplicate", "name": "Key", "new_name": "Fill",
                 "light": {"intensity": 10}})
    assert named(scene)["Key"]["light"]["intensity"] == 60
    assert named(scene)["Fill"]["light"] == {**named(scene)["Key"]["light"], "intensity": 10}
    with pytest.raises(BryceError, match="is not a light"):
        sceneops.apply(scene, [{"op": "set", "name": "Cube 1", "light": {"intensity": 5}}])


@pytest.mark.skipif(not os.environ.get("BRYCE3D_SAMPLE_SCENE"),
                    reason="set BRYCE3D_SAMPLE_SCENE to test real lights")
def test_lights_bryce_made_read_as_new_lights():
    sample = Br3.load(os.environ["BRYCE3D_SAMPLE_SCENE"])
    lights = [o for o in sample.objects() if o["kind"].endswith("light")]
    if not lights:
        pytest.skip("the sample scene has no lights")
    for found in lights:
        assert found["light"]["color"] == "#ffffff"
        assert found["light"]["intensity"] == 25      # what Bryce shows for a new light
        assert found["light"]["falloff"] == "linear" and found["light"]["casts_shadows"]


def texture_record(scene, cube="Cube 1"):
    material = scene._own_material(cube)
    return scene._payload(scene._textures(material)["color"])


def test_texture_placement_is_written_where_bryce_reads_it(scene, library, install):
    scene = textured_scene(scene, library, install)
    scene = run(scene, library, install,
                {"op": "texture_edit", "name": "Cube 1", "mapping": "cylindrical",
                 "scale": [2, 3, 4], "offset": [0.5, 0, -1], "rotation": [0, 90, 0]})
    payload = texture_record(scene)
    assert struct.unpack_from("<I", payload, 268)[0] == 6
    assert struct.unpack_from("<3d", payload, 276) == (2, 3, 4)
    assert struct.unpack_from("<3d", payload, 300) == (0.5, 0, -1)
    assert struct.unpack_from("<4d", payload, 324) == pytest.approx(
        quaternion_from_euler((0, 90, 0)))
    details = named(scene)["Cube 1"]["material"]["texture_details"]["checks"]
    assert details["mapping"] == "cylindrical" and details["offset"] == [0.5, 0, -1]
    assert details["rotation"] == pytest.approx([0, 90, 0])
    assert named(scene)["Cube 1"]["material"]["texture_scales"]["checks"] == [2, 3, 4]


def test_mapping_numbers_follow_bryces_order():
    from bryce3d_mcp.br3 import TEXTURE_MAPPINGS
    assert TEXTURE_MAPPINGS.index("object_space") == 0
    assert TEXTURE_MAPPINGS.index("world_space") == 1
    assert TEXTURE_MAPPINGS.index("random") == 8 and len(TEXTURE_MAPPINGS) == 11


def test_texture_colours_are_stored_blue_first(scene, library, install):
    scene = textured_scene(scene, library, install)
    scene = run(scene, library, install,
                {"op": "texture_edit", "name": "Cube 1",
                 "color_sets": {"2": ["#102030", None, "#ffffff"]}})
    payload = texture_record(scene)
    assert payload[668:680] == bytes((0x30, 0x20, 0x10, 0, 0, 0, 0, 0, 255, 255, 255, 0))
    sets = named(scene)["Cube 1"]["material"]["texture_details"]["checks"]["color_sets"]
    assert sets[1] == ["#102030", "#000000", "#ffffff"]
    scene = run(scene, library, install, {"op": "texture_edit", "name": "Cube 1",
                                          "replace_colors": {"#000000": "#2060ff"}})
    sets = named(scene)["Cube 1"]["material"]["texture_details"]["checks"]["color_sets"]
    assert "#000000" not in sum(sets, []) and sets[1] == ["#102030", "#2060ff", "#ffffff"]
    assert sets[0] == ["#2060ff"] * 3


@pytest.mark.parametrize("change, message", [
    ({}, "at least one of"),
    ({"mapping": "sideways"}, "mapping must be one of"),
    ({"scale": 0}, "between 0.001"),
    ({"color_sets": {"5": [None, None, None]}}, "sets 1 to 4"),
    ({"replace_colors": {"#123456": "#000000"}}, "has no color"),
    ({"mapping": "random", "channels": ["bump"]}, "no texture on bump"),
])
def test_bad_texture_edits_write_nothing(scene, library, install, change, message):
    scene = textured_scene(scene, library, install)
    before = scene.data
    with pytest.raises(BryceError, match=message):
        sceneops.apply(scene, [{"op": "texture_edit", "name": "Cube 1", **change}],
                       library, install)
    assert texture_record(Br3.from_bytes(before)) == texture_record(scene)


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test real textures")
def test_every_bryce_texture_reads_with_a_known_mapping():
    folder = presets.folder(os.environ["BRYCE3D_INSTALL"])
    names, seen = set(), 0
    for category in ("materials", "objects"):
        loaded = {}
        for found in presets.entries(folder, category):
            source = loaded.setdefault(found.library, presets.load(found))
            for name, ref in source.textures().items():
                if source.texture_scale_of(ref) is None:
                    continue
                details = source.texture_details(ref)
                assert details["mapping"] == "world_space"      # all of Bryce's own are
                assert details["offset"] == [0, 0, 0]
                assert details["rotation"] == [0, 0, 0]
                names.add(name)
                seen += 1
    assert seen and any(n.lower().startswith("wood") for n in names)
