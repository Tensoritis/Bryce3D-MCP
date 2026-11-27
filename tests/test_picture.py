"""Pictures: Bryce's compression and picture textures, against synthetic files."""

import os
import random
import struct

import pytest
from PIL import Image

from bryce3d_mcp import picture, presets, sceneops
from bryce3d_mcp.br3 import Br3
from bryce3d_mcp.errors import BryceError
from scene_files import basic_scene, picture_donor


def noisy(width, height, seed=1, flat=0.5):
    """Runs and noise mixed, the two things the compression treats differently."""
    rng, pixels = random.Random(seed), []
    colour = (0, 0, 0, 255)
    for _ in range(width * height):
        if rng.random() > flat:
            colour = tuple(rng.randrange(256) for _ in range(4))
        pixels.append(colour)
    out = Image.new("RGBA", (width, height))
    out.putdata(pixels)
    return out


@pytest.mark.parametrize("size, flat", [((1, 1), 0), ((2, 1), 0), ((127, 3), 1), ((300, 7), 0),
                                        ((64, 33), 0.5), ((253, 5), 0.97)])
def test_pictures_survive_compression(size, flat):
    image = noisy(*size, flat=flat)
    data = picture.encode(image)
    assert data[:4] == b"rle2" and data[4:64] == bytes(60)
    assert picture.decode(data, *size).tobytes() == image.tobytes()


def test_planes_are_alpha_red_and_differences_from_red():
    data = picture.encode(Image.new("RGBA", (5, 1), (200, 210, 190, 77)))
    assert data[64:] == bytes((5, 77, 5, 200, 5, 10, 5, 246))     # 246 is -10


def test_counts_stay_within_what_bryce_writes():
    data = picture.encode(noisy(400, 4, flat=0.995))
    at = 64
    while at < len(data):
        count = data[at]
        assert 0 < count & 0x7F <= 126
        at += 1 + (count & 0x7F) if count & 0x80 else 2


def test_description_holds_the_size_the_way_bryce_does():
    described = picture.description(14, 13)
    assert len(described) == 80
    assert struct.unpack_from(">4I", described, 8) == (4, 14, 56, 13)
    assert struct.unpack_from(">2I", described, 40) == (13, 14)
    assert picture.size_of(described) == (14, 13)


@pytest.mark.parametrize("data", [b"nope" + bytes(70), b"rle2" + bytes(60),
                                  b"rle2" + bytes(60) + b"\x7e\x01" * 9])
def test_damaged_pictures_are_refused(data):
    with pytest.raises(BryceError):
        picture.decode(data, 4, 4)


@pytest.fixture
def files(tmp_path):
    path = tmp_path / "flag.png"
    noisy(40, 25).save(path)
    return Br3.from_bytes(basic_scene()), Br3.from_bytes(picture_donor()), str(path)


def reread(scene):
    again = Br3.from_bytes(scene.data)
    for ref in again._all_refs():
        again._links(ref)
    return again


def picture_texture(scene, name="Cube 1", channel="color"):
    return scene._textures(scene._own_material(name))[channel]


def test_a_picture_is_stored_and_linked(files):
    scene, donor, path = files
    sceneops.apply(scene, [{"op": "picture", "name": "Cube 1", "path": path,
                            "repeat": [3, 2, 1], "mapping": "world_top"}], donor)
    scene = reread(scene)
    texture = picture_texture(scene)
    assert texture == picture_texture(scene, channel="ambient_color")
    assert texture[0] == "CPictTexture"
    assert scene.picture_of(texture).tobytes() == Image.open(path).tobytes()
    payload = scene._payload(texture)
    assert payload[8:13] == b"flag\0"
    assert struct.unpack_from("<I", payload, 268)[0] == 4
    assert struct.unpack_from("<3d", payload, 276) == pytest.approx((2 / 17, 1 / 17, 0))
    assert not struct.unpack_from("<I", payload, 272)[0] & 0x2
    material = scene._payload(scene._own_material("Cube 1"))
    assert material[272:276] == b"xtcp" and material[328:332] == b"xtcp"
    assert struct.unpack_from("<I", material, 268)[0] & 0x40


def test_showing_part_of_a_picture_uses_the_zoom_bit(files):
    scene, donor, path = files
    sceneops.apply(scene, [{"op": "picture", "name": "Cube 1", "path": path,
                            "repeat": 0.25}], donor)
    payload = scene._payload(picture_texture(scene))
    assert struct.unpack_from("<I", payload, 272)[0] & 0x2
    assert struct.unpack_from("<3d", payload, 276) == (0.75, 0.75, 0.75)


def test_a_second_picture_replaces_the_first(files, tmp_path):
    scene, donor, path = files
    other = tmp_path / "other.png"
    noisy(9, 30, seed=5).save(other)
    for one in (path, str(other)):
        sceneops.apply(scene, [{"op": "picture", "name": "Cube 1", "path": one}], donor)
    scene = reread(scene)
    assert scene.picture_of(picture_texture(scene)).size == (9, 30)
    assert len(scene._rows["CPictTexture"]) == len(scene._rows["C3dCImage"]) == 1
    sceneops.apply(scene, [{"op": "set", "name": "Cube 1",
                            "material": {"color": "#ff0000", "ambient_color": "#000000"}}])
    scene = reread(scene)
    assert not scene._rows.get("CPictTexture") and not scene._rows.get("C3dCImage")


def test_big_pictures_are_made_smaller(tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (3000, 1000), "red").save(path)
    assert picture.load(str(path)).size == (1024, 341)


@pytest.mark.parametrize("change, message", [
    ({"repeat": [2, 0.5, 1]}, "all 1 or more"),
    ({"repeat": 0}, "between 0.01"),
    ({"channels": ["refraction"]}, "channels must be"),
    ({"mapping": "flat"}, "mapping must be"),
    ({"path": "/no/such/file.png"}, "cannot read"),
])
def test_bad_picture_requests_are_refused(files, change, message):
    scene, donor, path = files
    with pytest.raises(BryceError, match=message):
        sceneops.apply(scene, [{"op": "picture", "name": "Cube 1", "path": path, **change}],
                       donor)


def test_without_a_picture_to_copy_it_says_what_is_needed(files):
    scene, _, path = files
    with pytest.raises(BryceError, match="Tutorial.obp"):
        sceneops.apply(scene, [{"op": "picture", "name": "Cube 1", "path": path}])


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to test real pictures")
def test_bryces_own_pictures_decode_and_reencode():
    folder = presets.folder(os.environ["BRYCE3D_INSTALL"])
    source = Br3.load(os.path.join(folder, "Tutorial.obp"))
    stored = [("C3dCImage", struct.unpack_from(">I", row)[0])
              for row in source._rows["C3dCImage"]]
    assert stored
    for ref in stored:
        second = struct.unpack_from(">I", source._row(ref), 8)[0]
        described = source.file.read(second)
        width, height = picture.size_of(described)
        assert described == picture.description(width, height)
        image = picture.decode(source._payload(ref), width, height)
        assert picture.decode(picture.encode(image), width, height).tobytes() == image.tobytes()


@pytest.mark.skipif(not (os.environ.get("BRYCE3D_INSTALL")
                         and os.environ.get("BRYCE3D_SAMPLE_SCENE")),
                    reason="set BRYCE3D_INSTALL and BRYCE3D_SAMPLE_SCENE for a real scene")
def test_a_picture_goes_into_a_real_scene(tmp_path):
    path = tmp_path / "photo.jpg"
    noisy(320, 200, flat=0.9).convert("RGB").save(path)
    scene = Br3.load(os.environ["BRYCE3D_SAMPLE_SCENE"])
    name = next(o["name"] for o in scene.objects() if o["kind"] == "cube")
    sceneops.apply(scene, [{"op": "picture", "name": name, "path": str(path)}],
                   install=os.environ["BRYCE3D_INSTALL"])
    scene = reread(scene)
    texture = picture_texture(scene, name)
    assert scene.picture_of(texture).tobytes() == picture.load(str(path)).tobytes()
    material = next(o for o in scene.objects() if o["name"] == name)["material"]
    assert material["textures"] == {"color": "photo", "ambient_color": "photo"}
