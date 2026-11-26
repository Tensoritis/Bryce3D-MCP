import json
import os
import random

import pytest
from PIL import Image

from bryce3d_mcp import heightfield, server, terrain
from bryce3d_mcp.ccmf import CompositeFile
from bryce3d_mcp.errors import BryceError
from bryce_files import library, terrain_payload

N = 32
RAMP = [(i % N) * 2048 for i in range(N * N)]
_RNG = random.Random(7)
NOISE = [_RNG.randrange(0, 2048) * 32 for _ in range(N * N)]


@pytest.mark.parametrize("heights, compressed", [([0] * (N * N), True), (NOISE, False)])
def test_codec_round_trip_picks_the_smaller_storage(heights, compressed):
    payload = terrain_payload("t", N, heights)
    assert (payload[744:748] == b"_elr") is compressed
    assert heightfield.decode(payload) == (N, heights)
    assert heightfield.encode(payload, heights) == payload
    assert payload[800:804] == b"keep"


def test_long_runs_are_split():
    flat = [64] * (512 * 512)
    payload = terrain_payload("flat", 512, flat)
    assert heightfield.decode(payload)[1] == flat


def test_precision_follows_the_record():
    odd = [1234] * (N * N)
    assert heightfield.decode(terrain_payload("a", N, odd))[1][0] == 1216
    assert heightfield.decode(terrain_payload("b", N, odd, eleven_bits=False))[1][0] == 1234


@pytest.mark.parametrize("damage", [lambda p: p[:500], lambda p: p[:-3],
                                    lambda p: p[:900] + b"\0\0\0\0" + p[904:]])
def test_damaged_terrain_is_rejected(damage):
    payload = damage(terrain_payload("t", N, [0] * (N * N)))
    with pytest.raises(BryceError):
        heightfield.decode(payload)


@pytest.mark.parametrize("split", [None, 100])
def test_editing_one_terrain_leaves_everything_else_alone(split):
    original = library([terrain_payload("first", N, RAMP), terrain_payload("second", N, [0] * N * N)],
                       split=split)
    document = CompositeFile(original)
    chosen = terrain.pick(document, 1)
    grid = [v / 65535 for v in NOISE]
    terrain.write_grid(document, chosen, grid, "new name", "new text")
    reread = CompositeFile(bytes(document.data))
    first, second = terrain.terrains(reread)
    assert first.payload == terrain.pick(CompositeFile(original), 0).payload
    assert second.name == "new name"
    assert max(abs(a - b) for a, b in zip(terrain.read_grid(second)[1], grid)) < 32 / 65535
    assert reread.read(terrain.preset_entry(reread, second))[:9] == b"new name\0"
    assert reread.read(terrain.preset_entry(reread, first)).startswith(b"Preset 0\0")


def test_same_size_edits_stay_in_place():
    original = library([terrain_payload("a", N, NOISE)], split=333)
    document = CompositeFile(original)
    terrain.write_grid(document, terrain.pick(document, 0), [v / 65535 for v in NOISE[::-1]])
    assert len(document.data) == len(original)
    assert terrain.read_grid(terrain.pick(document, 0))[1][0] == NOISE[-1] / 65535


@pytest.mark.parametrize("junk", [b"", b"hello" * 200, b"CCmF" + bytes(700)])
def test_other_files_are_rejected(junk):
    with pytest.raises(BryceError):
        terrain.terrains(CompositeFile(junk))


def _grid():
    return [((x - 16) ** 2 + (y - 12) ** 2) % 97 / 97 for y in range(N) for x in range(N)]


@pytest.mark.parametrize("name", [n for n in terrain.OPERATIONS if n != "path"])
def test_every_operation_keeps_a_valid_grid(name):
    out = terrain.apply(N, _grid(), [{"op": name}])
    assert len(out) == N * N and all(0 <= h <= 1 for h in out)


def test_operation_results():
    grid = _grid()
    assert terrain.apply(N, grid, [{"op": "rotate", "quarter_turns": 1}] * 4) == grid
    assert terrain.apply(N, grid, [{"op": "flip", "axis": "y"}] * 2) == grid
    turned = terrain.apply(N, [1.0] + [0.0] * (N * N - 1), [{"op": "rotate"}])
    assert turned.index(1.0) == N - 1  # top-left moves to top-right
    cut = terrain.apply(N, [0.8] * N * N,
                        [{"op": "path", "points": [[0, 0.5], [1, 0.5]], "level": 0.1}])
    assert cut[16 * N + 5] == pytest.approx(0.1) and cut[0] == pytest.approx(0.8)
    flat = terrain.apply(N, grid, [{"op": "smooth", "radius": 32, "passes": 10}])
    assert max(flat) - min(flat) < 0.05


def test_blur_is_not_transposed():
    out = terrain.apply(N, [(i % N) / N for i in range(N * N)], [{"op": "smooth", "radius": 1}])
    assert out[5] == pytest.approx(out[10 * N + 5]) and out[5] < out[9]


@pytest.mark.parametrize("bad", [[{"op": "explode"}], [{"op": "hill", "size": 1}],
                                 [{"op": "smooth", "radius": 1.5}], [{"op": "path"}],
                                 [{"op": "noise", "amount": float("nan")}],
                                 [{"op": "flip", "axis": "z"}],
                                 [{"op": "hydraulic_erosion", "droplets": 200000}] * 2])
def test_bad_operations_are_rejected_before_anything_runs(bad):
    with pytest.raises(BryceError):
        terrain.validate([{"op": "invert"}] + bad, 512)


def test_pictures_keep_16_bit_precision():
    grid = [i / (N * N - 1) for i in range(N * N)]
    back = terrain.grid_from_image(terrain.to_image16(N, grid), N)
    assert max(abs(a - b) for a, b in zip(grid, back)) < 2 / 65535
    assert terrain.grid_from_image(Image.new("RGB", (5, 9), (255, 255, 255)), N) == [1.0] * N * N


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("BRYCE3D_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(server, "_controller", None)  # file tools must not need Bryce
    path = tmp_path / "lib.obp"
    path.write_bytes(library([terrain_payload("hill", N, RAMP)]))
    return path


def test_terrain_tools_work_without_bryce(source):
    before = source.read_bytes()
    listing = json.loads(server.terrain_list(str(source))[0])
    assert listing == {"total": 1, "terrains": [{"index": 0, "name": "hill", "supported": True}]}
    result = json.loads(server.terrain_edit(str(source), "made", [{"op": "invert"}])[0])
    assert source.read_bytes() == before and result["path"].endswith("made.obp")
    _, grid = terrain.read_grid(terrain.pick(CompositeFile.load(result["path"]), 0))
    assert grid[0] > 0.99
    second = json.loads(server.terrain_edit(str(source), "made")[0])
    assert second["path"] != result["path"]  # never overwrites
    exported = json.loads(server.terrain_export(str(source), "raw")[0])
    assert Image.open(exported["path"]).size == (N, N)
    assert server.terrain_preview(str(source))


def test_terrain_tool_errors_are_readable(source, tmp_path):
    other = tmp_path / "x.txt"
    other.write_text("no")
    with pytest.raises(server.ToolError, match="obp library or a .br3"):
        server.terrain_edit(str(other), "o")
    with pytest.raises(server.ToolError, match="index must be"):
        server.terrain_preview(str(source), 5)


@pytest.mark.skipif(not os.environ.get("BRYCE3D_INSTALL"),
                    reason="set BRYCE3D_INSTALL to a Bryce 3D folder to test its files")
def test_bundled_terrains_re_encode_byte_for_byte():
    checked = 0
    presets = os.path.join(os.environ["BRYCE3D_INSTALL"], "Presets")
    for name in os.listdir(presets):
        document = CompositeFile.load(os.path.join(presets, name))
        for item in terrain.terrains(document):
            _, heights = heightfield.decode(item.payload)
            assert heightfield.encode(item.payload, heights) == item.payload, (name, item.index)
            checked += 1
    assert checked
