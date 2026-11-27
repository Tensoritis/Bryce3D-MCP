"""Tools of Bryce 3D's Terrain Editor, rewritten from what the program's code does.

These work on the heights as the editor holds them: integers from 0 to 65535 in a
square grid. Rounding, the order of the passes and the handling of the borders
follow Bryce. Tools that use random numbers use the same generator as Bryce's C
runtime, but Bryce seeds it from the clock or from whatever ran before, so the
pattern differs from run to run there too.

In the editor most buttons are dragged: the program works out the finished effect
once, then mixes it into the terrain by the distance dragged. `blend` is that mix,
and a strength of 1 here is the finished effect.
"""

from __future__ import annotations

import math

TOP = 0xFFFF
Heights = list[int]


class Rand:
    """rand() of the Microsoft C runtime: 0 to 32767."""

    def __init__(self, seed: int = 1):
        self.state = seed & 0xFFFFFFFF

    def __call__(self) -> int:
        self.state = (self.state * 214013 + 2531011) & 0xFFFFFFFF
        return (self.state >> 16) & 0x7FFF


def _limit(value: int) -> int:
    return 0 if value < 0 else TOP if value > TOP else value


def blend(source: Heights, target: Heights, strength: float,
          low: int = 0, high: int = TOP) -> Heights:
    """Move each height towards its target. Only heights from `low` to `high` change:
    the editor's filtering range. 256 steps make up a strength of 1."""
    amount = round(strength * 256)
    return [_limit(h + (((t - h) * amount) >> 8)) if low <= h <= high else h
            for h, t in zip(source, target)]


# -- filters ----------------------------------------------------------------------
# 3x3 weights row by row, and how far the sum is shifted down.
SMOOTH = ((30, 30, 30, 30, 20, 30, 30, 30, 30), 8)
SHARPEN = ((-378, -378, -378, -378, 3286, -378, -378, -378, -378), 8)
RELIEF = ((2330, 258, -1147, 924, -4474, 925, 1147, 258, 2330), 11)


def convolve(size: int, heights: Heights, kernel: tuple[tuple[int, ...], int]) -> Heights:
    """Bryce leaves the first column alone, and for the last one reads on into the
    start of the next row."""
    weights, shift = kernel
    out, last = list(heights), len(heights) - 1
    for y in range(size):
        rows = (max(y - 1, 0) * size, y * size, min(y + 1, size - 1) * size)
        for x in range(1, size):
            total, k = 0, 0
            for base in rows:
                at = base + x
                total += (weights[k] * heights[at - 1] + weights[k + 1] * heights[at]
                          + weights[k + 2] * heights[at + 1 if at < last else last])
                k += 3
            out[y * size + x] = _limit(total >> shift)
    return out


def soften(size: int, heights: Heights, low: int = 0, high: int = TOP) -> Heights:
    """A 1-2-1 pass along the rows, then one down the columns, leaving the border.
    The last inner sample counts itself in place of the border sample."""
    out = list(heights)
    for along, across in ((1, size), (size, 1)):
        before = list(out)
        for line in range(size):
            for step in range(1, size - 1):
                at = line * across + step * along
                value = before[at]
                if low <= value <= high:
                    ahead = before[at + along] if step < size - 2 else value
                    out[at] = (before[at - along] + 2 * value + ahead) >> 2
    return out


def equalize(size: int, heights: Heights) -> Heights:
    """Stretch the heights over the whole range."""
    bottom, top = min(heights), max(heights)
    if top == bottom:
        return list(heights)
    scale = TOP / (top - bottom)
    return [_limit(int((h - bottom) * scale)) for h in heights]


def gaussian_edges(size: int, heights: Heights) -> Heights:
    """Fade towards the edges along a bell curve centred on the middle."""
    centre, rate = size // 2, 6.4 / (size * size)
    return [int(h * math.exp(-((i % size - centre) ** 2 + (i // size - centre) ** 2) * rate))
            for i, h in enumerate(heights)]


# -- noise ------------------------------------------------------------------------
def height_noise(size: int, heights: Heights, rand: Rand) -> Heights:
    """Upward noise that grows with height."""
    return [_limit(h + int((rand() / 32767 * (h >> 1) + 0.5) * 1280 / TOP)) for h in heights]


def slopes(size: int, heights: Heights) -> Heights:
    """The sum of the four differences across each sample: sides, then diagonals."""
    out = []
    for y in range(size):
        up, here, down = (max(y - 1, 0) * size, y * size, min(y + 1, size - 1) * size)
        for x in range(size):
            west, east = max(x - 1, 0), min(x + 1, size - 1)
            total = (abs(heights[here + west] - heights[here + east])
                     + abs(heights[up + x] - heights[down + x])
                     + abs(heights[up + west] - heights[down + east])
                     + abs(heights[down + west] - heights[up + east]))
            out.append(total & TOP)
    return out


def slope_noise(size: int, heights: Heights, rand: Rand,
                low: int = 0, high: int = TOP) -> Heights:
    """Noise that grows with steepness, so flat ground stays flat."""
    steep = soften(size, slopes(size, heights), low, high)
    return [_limit(h + int((rand() / 32767 * (2 * s) + 0.5 - s) * 4096 / TOP))
            for h, s in zip(heights, steep)]


def spikes(size: int, heights: Heights, rand: Rand) -> Heights:
    """Small spikes, one at most in every 3x3 cell, added where the ground is even."""
    marks = [0] * (size * size)
    cells = (size - 5) // 3
    for row in range(cells):
        start = (1 + 3 * row) * size + 1
        for column in range(cells):
            value = int((rand() / 32767 - 0.5) * TOP)
            mark = TOP if value > 28000 else 0xF7FF if value > 16000 else 0
            at = start + 3 * column + max(value, 0) % 3
            marks[at] = mark
            for near in (at - 1, at + 1, at - size, at + size):
                marks[near] = mark >> 1
    out = list(marks)
    for y in range(size - 1):
        for x in range(size - 1):
            at = y * size + x
            value = heights[at]
            if abs(heights[at + 1] - 2 * value + heights[at + size]) < 0x400:
                value += marks[at] * 0x4000 // TOP
            out[at] = _limit(value)
    return out


def sawtooth(size: int, heights: Heights, rand: Rand) -> Heights:
    """Sixteen sets of slanting sawtooth waves laid over each other."""
    out = list(heights)
    for _ in range(16):
        across, down, start = rand(), rand(), rand()
        for y in range(size):
            for x in range(size):
                at = y * size + x
                phase = (start + x) * across + y * down
                wave = (phase // size) % 0x7FFF
                out[at] = _limit(out[at] + ((wave - 0x3FFF) >> 1))
    return out


def _widen(size: int, source: Heights, into: Heights, rand: Rand) -> None:
    """Each inner sample takes the highest of one of its four 2x2 blocks, less 12."""
    for y in range(1, size - 1):
        for x in range(1, size - 1):
            at = y * size + x
            pick = int((rand() / 32767 - 0.5) * TOP)
            side = -1 if pick < -16000 or 0 <= pick <= 16000 else 1
            line = -size if pick < 0 else size
            value = max(source[at], source[at + side], source[at + line],
                        source[at + line + side]) - 12
            into[at] = value if value > 0 else 0


def subplateaus(size: int, heights: Heights, rand: Rand,
                low: int = 0, high: int = TOP) -> Heights:
    """Spread high points sideways into small flats, then soften."""
    first, second = list(heights), list(heights)
    for _ in range(2):
        _widen(size, first, second, rand)
        _widen(size, second, first, rand)
    return soften(size, first, low, high)


# -- erosion ----------------------------------------------------------------------
# The eight neighbours, clockwise.
_STEPS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
_LONGEST = 320


def _trickle(size: int, heights: Heights, x: int, y: int, rand: Rand,
             low: int, high: int) -> list[list[int]]:
    """The way one raindrop runs: [x, y, height] points. The grid wraps around."""
    path = [[x, y, heights[y * size + x]]]
    heading, flat = 2, 0
    while True:
        near = []
        for dx, dy in _STEPS:
            nx, ny = (path[-1][0] + dx) % size, (path[-1][1] + dy) % size
            near.append([nx, ny, heights[ny * size + nx]])
        best, lowest = heading, near[heading][2]
        for k in range(8):
            if k % 2 and rand() >= 6540:  # diagonals are looked at one time in five
                continue
            if near[k][2] < lowest:
                best, lowest = k, near[k][2]
        if rand() < 3270:
            heading = (best + 1) % 8
        elif rand() < 3270:
            heading = (best + 7) % 8
        else:
            heading = best
        step = near[heading]
        drop = path[-1][2] - step[2]
        flat += 1 if drop == 0 else -1
        if (len(path) > _LONGEST or drop < 0 or flat > 5
                or step[2] > high or step[2] <= low):
            return path
        path.append(step)


def _cut(path: list[list[int]], rate: int = 24) -> None:
    """Lower the path, deepest half way along, never below where it ends."""
    count = len(path) - 1
    floor, middle, depth = path[count][2], count // 2, 64
    for k in range(middle):
        path[k][2] = max(path[k][2] - (min(depth >> 6, 256) << 5), floor)
        depth += rate
    for k in range(middle, count):
        path[k][2] = max(path[k][2] - ((depth >> 6) << 5), floor)
        depth -= rate
    for _ in range(2):
        before = [p[2] for p in path]
        for k in range(count + 1):
            path[k][2] = (before[min(k + 2, count)] + before[min(k + 1, count)]
                          + 2 * before[k]) >> 2


def _carve(size: int, heights: Heights, path: list[list[int]]) -> None:
    for x, y, height in path:
        heights[y * size + x] = height & TOP
    count, edge = len(path) - 1, size - 1
    if count <= 20:
        return
    for k in range(10, count + 1):  # the channel widens as the drop gathers water
        x, y, height = path[k]
        weight = min((k - 10) / 54, 0.75)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            at = min(max(ny, 0), edge) * size + min(max(nx, 0), edge)
            heights[at] = int((height - heights[at]) * weight + heights[at])


def erode(size: int, heights: Heights, rand: Rand, low: int = 0, high: int = TOP,
          drops: int = 1500) -> Heights:
    """One press of Erode: 1500 raindrops, each cutting the channel it runs down."""
    out = list(heights)
    for _ in range(drops):
        x, y = rand() % (size - 4) + 2, rand() % (size - 4) + 2
        path = _trickle(size, out, x, y, rand, low, high)
        if len(path) - 1 > 8:
            _cut(path)
            _carve(size, out, path)
    return out
