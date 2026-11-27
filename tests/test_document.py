"""Document and render settings."""

import os
import struct

import pytest

from bryce3d_mcp import sceneops
from bryce3d_mcp.br3 import Br3
from bryce3d_mcp.errors import BryceError
from scene_files import basic_scene


def raw(scene):
    return scene._document_record()[1]


@pytest.fixture
def scene():
    return Br3.from_bytes(basic_scene())


def test_a_new_document(scene):
    assert scene.document() == {"size": [480, 360], "antialiasing": "normal",
                                "optimisation": "low", "render_type": "normal",
                                "report_render_time": True}
    assert scene.summary()["document"]["size"] == [480, 360]


def test_a_size_is_written_everywhere_bryce_keeps_it(scene):
    before = raw(scene)
    sceneops.apply(scene, [{"op": "document", "size": "photo"}])
    scene = Br3.from_bytes(scene.data)
    after = raw(scene)
    assert struct.unpack_from(">8I", after) == (768, 512, 768, 512, 0, 0, 512, 768)
    assert after[32:56] == bytes(24) and after[56:] == before[56:]
    assert scene.document()["size"] == [768, 512]


@pytest.mark.parametrize("name, mode, normal_bit", [("off", 0, 0), ("normal", 1, 0x2000),
                                                    ("fine_art", 2, 0)])
def test_antialiasing_sets_what_the_menu_sets(scene, name, mode, normal_bit):
    scene.set_document(antialiasing=name)
    data = raw(scene)
    assert struct.unpack_from(">I", data, 80)[0] == mode
    assert struct.unpack_from(">I", data, 56)[0] == 0x23BF & ~0x2000 | normal_bit


def test_render_types_are_exclusive_and_reversible(scene):
    for kind, bit in (("mask", 0x400), ("altitude", 0x1000), ("distance", 0x800),
                      ("normal", 0)):
        assert scene.set_document(render_type=kind)["render_type"] == kind
        assert struct.unpack_from(">I", raw(scene), 56)[0] == 0x23BF | bit


def test_the_other_choices(scene):
    result = scene.set_document(optimisation="high", report_render_time=False,
                                size=[1024, 768])
    assert result["optimisation"] == "high" and not result["report_render_time"]
    assert result["size"] == [1024, 768]


@pytest.mark.parametrize("change", [{"size": [10, 100]}, {"size": "huge"}, {"size": [480.5, 360]},
                                    {"antialiasing": "best"}, {"optimisation": 2},
                                    {"render_type": "360"}, {"report_render_time": 1}])
def test_bad_settings_are_refused_and_change_nothing(scene, change):
    before = scene.data
    with pytest.raises(BryceError):
        scene.set_document(**change)
    assert scene.data == before


@pytest.mark.skipif(not os.environ.get("BRYCE3D_SAMPLE_SCENE"),
                    reason="set BRYCE3D_SAMPLE_SCENE to test a real scene")
def test_a_real_scene():
    scene = Br3.load(os.environ["BRYCE3D_SAMPLE_SCENE"])
    assert scene.document()["size"] == [480, 360]
    before = raw(scene)
    scene.set_document(size="default", antialiasing=scene.document()["antialiasing"])
    changed = [i for i in range(416) if raw(scene)[i] != before[i]]
    assert all(32 <= i < 56 for i in changed)
    scene.set_document(size="standard", antialiasing="fine_art")
    again = Br3.from_bytes(scene.data)
    assert again.document()["size"] == [640, 480]
    assert again.document()["antialiasing"] == "fine_art"
    assert len(again.objects()) == len(Br3.load(os.environ["BRYCE3D_SAMPLE_SCENE"]).objects())
