"""The Terrain Editor tools rewritten from Bryce."""

import pytest

from bryce3d_mcp import editor_tools as tools
from bryce3d_mcp import terrain
from bryce3d_mcp.errors import BryceError

SIZE = 64
OURS = [name for name in terrain.OPERATIONS if name.startswith("bryce_")]


def slope_grid():
    return [(i % SIZE + i // SIZE) / (2 * SIZE - 2) * 0.7 + 0.1 for i in range(SIZE * SIZE)]


def hill(top=50000):
    return [max(0, top - 90 * ((i % SIZE - 32) ** 2 + (i // SIZE - 32) ** 2))
            for i in range(SIZE * SIZE)]


def test_random_numbers_are_the_c_runtimes():
    rand = tools.Rand(1)
    assert [rand() for _ in range(5)] == [41, 18467, 6334, 26500, 19169]


def test_blend_mixes_in_256ths_inside_the_range():
    source, target = [100, 1000, 60000], [356, 1256, 0]
    assert tools.blend(source, target, 1) == target
    assert tools.blend(source, target, 0.5) == [228, 1128, 30000]
    assert tools.blend(source, target, -1) == [0, 744, tools.TOP]
    assert tools.blend(source, target, 1, low=500, high=2000) == [100, 1256, 60000]


def test_smoothing_uses_bryces_weights():
    grid = [0] * (SIZE * SIZE)
    grid[10 * SIZE + 10] = 25600
    out = tools.convolve(SIZE, grid, tools.SMOOTH)
    assert out[10 * SIZE + 10] == 2000                       # 20/256
    assert out[9 * SIZE + 9] == out[11 * SIZE + 10] == 3000  # 30/256
    assert sum(1 for v in out if v) == 9


def test_filters_leave_the_first_column_alone():
    grid = list(range(SIZE * SIZE))
    out = tools.convolve(SIZE, grid, tools.RELIEF)
    assert out[::SIZE] == grid[::SIZE] and len(out) == len(grid)


def test_equalize_uses_the_whole_range():
    out = tools.equalize(SIZE, [1000, 2000, 3000, 3000])
    assert out == [0, 32767, 65535, 65535]
    assert tools.equalize(SIZE, [7] * 4) == [7] * 4


def test_gaussian_edges_keep_the_middle():
    out = tools.gaussian_edges(SIZE, [50000] * (SIZE * SIZE))
    assert out[32 * SIZE + 32] == 50000
    assert out[0] < out[32 * SIZE] < out[32 * SIZE + 16] < 50000


def test_slope_noise_leaves_flat_ground_alone_and_height_noise_only_raises():
    flat = [30000] * (SIZE * SIZE)
    assert tools.slope_noise(SIZE, flat, tools.Rand(3)) == flat
    raised = tools.height_noise(SIZE, flat, tools.Rand(3))
    assert all(30000 <= v <= 30000 + 293 for v in raised) and raised != flat
    assert tools.height_noise(SIZE, [0] * 16, tools.Rand(3)) == [0] * 16


def test_soften_keeps_a_straight_slope():
    grid = [i % SIZE * 100 for i in range(SIZE * SIZE)]
    out = tools.soften(SIZE, grid)
    assert out[5 * SIZE:5 * SIZE + SIZE - 2] == grid[5 * SIZE:5 * SIZE + SIZE - 2]


def test_subplateaus_widen_a_peak():
    out = tools.subplateaus(SIZE, hill(), tools.Rand(4))
    assert sum(v > 40000 for v in out) > sum(v > 40000 for v in hill())


def test_erosion_cuts_channels_without_raising_the_peak():
    before = hill()
    after = tools.erode(SIZE, before, tools.Rand(5))
    assert before == hill()                       # the input is not changed
    assert sum(a < b for a, b in zip(after, before)) > 200
    assert max(after) <= max(before) and min(after) >= 0


def test_droplets_run_downhill():
    path = tools._trickle(SIZE, hill(), 30, 30, tools.Rand(2), 0, tools.TOP)
    heights = [p[2] for p in path]
    assert len(path) > 9 and heights == sorted(heights, reverse=True)


@pytest.mark.parametrize("name", OURS)
def test_every_tool_runs_repeatably_and_stays_in_range(name):
    first = terrain.apply(SIZE, slope_grid(), [{"op": name}])
    assert first == terrain.apply(SIZE, slope_grid(), [{"op": name}])
    assert len(first) == SIZE * SIZE and 0 <= min(first) <= max(first) <= 1
    assert name in terrain.describe_operations()


def test_the_filtering_range_protects_other_heights():
    grid = slope_grid()
    out = terrain.apply(SIZE, grid, [{"op": "bryce_raise_lower", "amount": 0.5,
                                      "low": 0.5, "high": 1.0}])
    for before, after in zip(grid, out):
        if before < 0.499:
            assert after == pytest.approx(before, abs=1e-4)
        elif before > 0.501:
            assert after == pytest.approx(min(1, before * 1.5), abs=1e-4)


@pytest.mark.parametrize("step", [{"op": "bryce_erode", "times": 21},
                                  {"op": "bryce_smooth", "times": 1.5},
                                  {"op": "bryce_smooth", "strength": 2}])
def test_bad_values_are_refused(step):
    with pytest.raises(BryceError):
        terrain.validate([step], SIZE)


def test_work_per_call_is_limited():
    with pytest.raises(BryceError, match="too much work"):
        terrain.validate([{"op": "bryce_smooth", "times": 50}], 1024)
