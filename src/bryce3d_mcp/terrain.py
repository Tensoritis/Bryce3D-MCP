"""Terrain editing on Bryce files: no mouse involved.

Heights are floats from 0 (lowest) to 1 (highest) in a square grid, row by row.
Operation coordinates are fractions of the grid, with (0, 0) at the first sample
(the top-left of the previews). How those axes line up with Bryce's X and Z has
not been checked yet.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable

from PIL import Image, ImageDraw

from . import heightfield
from .ccmf import CompositeFile
from .errors import BryceError
from .meshes import _value_noise, heightmap

Grid = list[float]
_DESC_NAME, _DESC_TEXT = slice(0, 64), slice(64, None)


# -- files ----------------------------------------------------------------------
@dataclass
class Terrain:
    index: int
    entry: int
    payload: bytes

    @property
    def name(self) -> str:
        return heightfield.name_of(self.payload)


def terrains(document: CompositeFile) -> list[Terrain]:
    return [Terrain(i, r.data_entry, document.read(r.data_entry))
            for i, r in enumerate(document.records("C3dHeightField"))]


def pick(document: CompositeFile, index: int) -> Terrain:
    found = terrains(document)
    if not found:
        raise BryceError("this file contains no terrain")
    if not 0 <= index < len(found):
        raise BryceError(f"terrain index must be 0-{len(found) - 1}")
    return found[index]


def read_grid(terrain: Terrain) -> tuple[int, Grid]:
    size, values = heightfield.decode(terrain.payload)
    return size, [v / heightfield.FULL for v in values]


def preset_entry(document: CompositeFile, terrain: Terrain) -> int | None:
    """In an object library, the name/description record that goes with a terrain:
    the first one written after it."""
    later = [r.data_entry for r in document.records("objpresetdesc") if r.data_entry > terrain.entry]
    return min(later) if later else None


def write_grid(document: CompositeFile, terrain: Terrain, grid: Grid,
               name: str | None = None, description: str | None = None) -> None:
    values = [round(min(1.0, max(0.0, h)) * heightfield.FULL) for h in grid]
    document.replace(terrain.entry, heightfield.encode(terrain.payload, values, name))
    entry = preset_entry(document, terrain)
    if entry is None or (name is None and description is None):
        return
    record = bytearray(document.read(entry))
    if name is not None:
        record[_DESC_NAME] = heightfield.pack_name(name, 64)
    if description is not None:
        text = description.encode("ascii", "replace")[:len(record) - 65]
        record[_DESC_TEXT] = text.ljust(len(record) - 64, b"\0")
    document.replace(entry, bytes(record))


# -- pictures -------------------------------------------------------------------
def to_image16(size: int, grid: Grid) -> Image.Image:
    image = Image.new("I;16", (size, size))
    image.putdata([round(min(1.0, max(0.0, h)) * 65535) for h in grid])
    return image


def grid_from_image(image: Image.Image, size: int) -> Grid:
    """Any picture to a grid. White is high. 16-bit grayscale keeps its precision."""
    if image.mode.startswith("I"):
        scale, source = 65535.0, image.convert("F")
    else:
        scale, source = 255.0, image.convert("L").convert("F")
    if source.size != (size, size):
        source = source.resize((size, size), Image.BILINEAR)
    return [min(1.0, max(0.0, source.getpixel((x, y)) / scale))
            for y in range(size) for x in range(size)]


def preview(size: int, grid: Grid) -> Image.Image:
    """Height as gray on the left, lit relief on the right."""
    shade = []
    strength = size * 0.18
    for y in range(size):
        for x in range(size):
            here = grid[y * size + x]
            dx = (grid[y * size + min(x + 1, size - 1)] - grid[y * size + max(x - 1, 0)]) * strength
            dy = (grid[min(y + 1, size - 1) * size + x] - grid[max(y - 1, 0) * size + x]) * strength
            light = (0.5 * dx + 0.5 * dy + 0.7) / math.sqrt(dx * dx + dy * dy + 1)
            shade.append(int(max(0.0, min(1.0, 0.15 + 0.8 * light + 0.1 * here)) * 255))
    gray = Image.new("L", (size, size))
    gray.putdata([int(max(0.0, min(1.0, h)) * 255) for h in grid])
    lit = Image.new("L", (size, size))
    lit.putdata(shade)
    out = Image.new("L", (size * 2 + 4, size), 128)
    out.paste(gray, (0, 0))
    out.paste(lit, (size + 4, 0))
    return out


def contact_sheet(items: list[Terrain], cell: int = 128, columns: int = 6) -> Image.Image:
    rows = max(1, math.ceil(len(items) / columns))
    sheet = Image.new("L", (columns * (cell + 2), rows * (cell + 14)), 96)
    draw = ImageDraw.Draw(sheet)
    for slot, item in enumerate(items):
        x, y = slot % columns * (cell + 2), slot // columns * (cell + 14)
        draw.text((x + 2, y + 1), f"{item.index}: {item.name}"[:20], fill=255)
        if heightfield.is_supported(item.payload):
            size, values = heightfield.decode(item.payload)
            image = Image.new("L", (size, size))
            image.putdata([v >> 8 for v in values])
            sheet.paste(image.resize((cell, cell)), (x, y + 13))
    return sheet


# -- operations -----------------------------------------------------------------
def _box_blur(size: int, grid: Grid, radius: int) -> Grid:
    def pass_rows(values: Grid) -> Grid:
        out = [0.0] * (size * size)
        for y in range(size):
            row = values[y * size:(y + 1) * size]
            padded = [row[0]] * radius + row + [row[-1]] * radius
            total = sum(padded[:2 * radius + 1])
            for x in range(size):
                out[x * size + y] = total / (2 * radius + 1)  # transposed
                if x + 1 < size:
                    total += padded[x + 2 * radius + 1] - padded[x]
        return out

    return pass_rows(pass_rows(grid))


def _clamp(grid: Grid) -> Grid:
    return [0.0 if h < 0 else 1.0 if h > 1 else h for h in grid]


def _ramp(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def _each_point(size: int, grid: Grid, fn: Callable[[float, float, float], float]) -> Grid:
    step = 1 / (size - 1)
    return [fn(i % size * step, i // size * step, h) for i, h in enumerate(grid)]


def _fractal(size: int, cells: int, octaves: int, seed: int) -> Grid:
    rng = random.Random(seed)
    total, weight, amplitude = [0.0] * (size * size), 0.0, 1.0
    for octave in range(octaves):
        count = cells << octave
        if count >= size:
            break
        layer = _value_noise(size, count, rng)
        total = [t + amplitude * v for t, v in zip(total, layer)]
        weight += amplitude
        amplitude *= 0.5
    return [t / weight for t in total] if weight else [0.5] * (size * size)


def op_normalize(size, grid, low=0.0, high=1.0):
    bottom, top = min(grid), max(grid)
    if top - bottom < 1e-9:
        return [low] * len(grid)
    return [low + (h - bottom) / (top - bottom) * (high - low) for h in grid]


def op_scale(size, grid, factor=1.0):
    return [h * factor for h in grid]


def op_offset(size, grid, amount=0.0):
    return [h + amount for h in grid]


def op_invert(size, grid):
    return [1 - h for h in grid]


def op_gamma(size, grid, value=1.0):
    return [max(0.0, h) ** value for h in grid]


def op_clip(size, grid, low=0.0, high=1.0):
    return [min(high, max(low, h)) for h in grid]


def op_terrace(size, grid, steps=8, sharpness=0.8):
    out = []
    for h in grid:
        level = h * steps
        base = math.floor(level)
        part = level - base
        flat = _ramp((part - sharpness / 2) / max(1 - sharpness, 1e-6))
        out.append((base + flat) / steps)
    return out


def op_smooth(size, grid, radius=2, passes=1):
    for _ in range(int(passes)):
        grid = _box_blur(size, grid, int(radius))
    return grid


def op_sharpen(size, grid, amount=1.0, radius=2):
    soft = _box_blur(size, grid, int(radius))
    return [h + (h - s) * amount for h, s in zip(grid, soft)]


def op_noise(size, grid, amount=0.1, cells=8, octaves=4, seed=0):
    noise = _fractal(size, int(cells), int(octaves), int(seed))
    # Zero stays zero so the terrain keeps a clean outline.
    return [h + (n - 0.5) * 2 * amount * min(1.0, h * 8) for h, n in zip(grid, noise)]


def op_ridges(size, grid, amount=0.25, cells=5, octaves=4, seed=0):
    noise = _fractal(size, int(cells), int(octaves), int(seed))
    return [h + amount * ((1 - abs(2 * n - 1)) ** 2 - 0.5) * min(1.0, h * 4)
            for h, n in zip(grid, noise)]


def op_thermal_erosion(size, grid, iterations=20, talus=1.5):
    """Material slides downhill wherever a slope is steeper than `talus`
    (height change per grid width)."""
    grid, limit = list(grid), talus / size
    for _ in range(int(iterations)):
        delta = [0.0] * len(grid)
        for y in range(1, size - 1):
            for i in range(y * size + 1, y * size + size - 1):
                h, lowest, drop = grid[i], i, limit
                for j in (i - 1, i + 1, i - size, i + size):
                    if h - grid[j] > drop:
                        lowest, drop = j, h - grid[j]
                if lowest != i:
                    moved = (drop - limit) * 0.5
                    delta[i] -= moved
                    delta[lowest] += moved
        grid = [h + d for h, d in zip(grid, delta)]
    return grid


def op_hydraulic_erosion(size, grid, droplets=20000, strength=0.3, seed=0):
    """Rain drops run downhill, cutting gullies and leaving sediment."""
    grid, rng = list(grid), random.Random(int(seed))
    for _ in range(int(droplets)):
        x, y = rng.uniform(0, size - 1.001), rng.uniform(0, size - 1.001)
        dx = dy = sediment = 0.0
        speed = water = 1.0
        for _step in range(40):
            cx, cy = int(x), int(y)
            u, v, i = x - cx, y - cy, cy * size + cx
            a, b, c, d = grid[i], grid[i + 1], grid[i + size], grid[i + size + 1]
            here = a * (1 - u) * (1 - v) + b * u * (1 - v) + c * (1 - u) * v + d * u * v
            dx = dx * 0.1 - ((b - a) * (1 - v) + (d - c) * v) * 0.9
            dy = dy * 0.1 - ((c - a) * (1 - u) + (d - b) * u) * 0.9
            length = math.hypot(dx, dy)
            if length < 1e-9:
                break
            dx, dy = dx / length, dy / length
            x, y = x + dx, y + dy
            if not (0 <= x < size - 1 and 0 <= y < size - 1):
                break
            nx, ny = int(x), int(y)
            s, t, j = x - nx, y - ny, ny * size + nx
            there = (grid[j] * (1 - s) * (1 - t) + grid[j + 1] * s * (1 - t)
                     + grid[j + size] * (1 - s) * t + grid[j + size + 1] * s * t)
            change = there - here
            capacity = max(-change, 0.002) * speed * water * 4
            if change > 0 or sediment > capacity:
                amount = min(change, sediment) if change > 0 else (sediment - capacity) * 0.3
                sediment -= amount
            else:
                amount = -min((capacity - sediment) * strength, -change)
                sediment -= amount
            for index, share in ((i, (1 - u) * (1 - v)), (i + 1, u * (1 - v)),
                                 (i + size, (1 - u) * v), (i + size + 1, u * v)):
                grid[index] += amount * share
            speed = math.sqrt(max(0.0, speed * speed - change * 4))
            water *= 0.98
    return grid


def op_flip(size, grid, axis="x"):
    rows = [grid[y * size:(y + 1) * size] for y in range(size)]
    rows = [row[::-1] for row in rows] if axis == "x" else rows[::-1]
    return [h for row in rows for h in row]


def op_rotate(size, grid, quarter_turns=1):
    """Clockwise as seen in the preview."""
    for _ in range(int(quarter_turns) % 4):
        grid = [grid[(size - 1 - x) * size + y] for y in range(size) for x in range(size)]
    return grid


def op_edge_falloff(size, grid, width=0.15):
    return _each_point(size, grid, lambda x, y, h: h * _ramp(min(x, y, 1 - x, 1 - y) / width))


def op_hill(size, grid, x=0.5, y=0.5, radius=0.2, height=0.4):
    """A smooth bump; a negative height digs a basin."""
    return _each_point(size, grid, lambda px, py, h:
                       h + height * _ramp(1 - math.hypot(px - x, py - y) / radius))


def op_crater(size, grid, x=0.5, y=0.5, radius=0.2, depth=0.25, rim=0.1):
    def point(px, py, h):
        r = math.hypot(px - x, py - y) / radius
        bowl = -depth * _ramp((1 - r) * 1.6)
        return h + bowl + rim * math.exp(-((r - 1) / 0.18) ** 2)
    return _each_point(size, grid, point)


def op_plateau(size, grid, x=0.5, y=0.5, radius=0.2, height=0.5, blend=0.1):
    """Level a round area, e.g. as a building site."""
    def point(px, py, h):
        weight = _ramp((radius + blend - math.hypot(px - x, py - y)) / blend)
        return h + (height - h) * weight
    return _each_point(size, grid, point)


def op_path(size, grid, points=None, width=0.04, level=0.15, blend=0.05):
    """Cut or build a river bed or road along a line of [x, y] points, bringing it
    to `level` with sloping banks `blend` wide."""
    segments = list(zip(points, points[1:]))

    def point(px, py, h):
        best = float("inf")
        for (ax, ay), (bx, by) in segments:
            vx, vy = bx - ax, by - ay
            squared = vx * vx + vy * vy
            t = 0.0 if not squared else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / squared))
            best = min(best, math.hypot(px - ax - t * vx, py - ay - t * vy))
        weight = _ramp((width / 2 + blend - best) / blend)
        return h + (level - h) * weight if weight else h
    return _each_point(size, grid, point)


def op_generate(size, grid, shape="mountain", seed=0, roughness=0.5, mode="replace", weight=1.0):
    """Bring in a built-in landform: replace, add, max, min or multiply."""
    new = grid_from_image(heightmap(shape, min(size, 512), int(seed), roughness), size)
    combine = {"replace": lambda a, b: b, "add": lambda a, b: a + b, "max": max, "min": min,
               "multiply": lambda a, b: a * b}[mode]
    return [a + (combine(a, b) - a) * weight for a, b in zip(grid, new)]


# name -> (function, {parameter: (lowest, highest) or allowed values or None})
_UNIT, _SEED = (0.0, 1.0), (0, 2 ** 31)
OPERATIONS: dict[str, tuple[Callable, dict[str, Any]]] = {
    "normalize": (op_normalize, {"low": _UNIT, "high": _UNIT}),
    "scale": (op_scale, {"factor": (0.0, 10.0)}),
    "offset": (op_offset, {"amount": (-1.0, 1.0)}),
    "invert": (op_invert, {}),
    "gamma": (op_gamma, {"value": (0.1, 10.0)}),
    "clip": (op_clip, {"low": _UNIT, "high": _UNIT}),
    "terrace": (op_terrace, {"steps": (2, 100), "sharpness": (0.0, 1.0)}),
    "smooth": (op_smooth, {"radius": (1, 32), "passes": (1, 10)}),
    "sharpen": (op_sharpen, {"amount": (0.0, 5.0), "radius": (1, 32)}),
    "noise": (op_noise, {"amount": _UNIT, "cells": (2, 128), "octaves": (1, 8), "seed": _SEED}),
    "ridges": (op_ridges, {"amount": _UNIT, "cells": (2, 128), "octaves": (1, 8), "seed": _SEED}),
    "thermal_erosion": (op_thermal_erosion, {"iterations": (1, 200), "talus": (0.1, 20.0)}),
    "hydraulic_erosion": (op_hydraulic_erosion,
                          {"droplets": (1, 200000), "strength": _UNIT, "seed": _SEED}),
    "flip": (op_flip, {"axis": ("x", "y")}),
    "rotate": (op_rotate, {"quarter_turns": (0, 3)}),
    "edge_falloff": (op_edge_falloff, {"width": (0.01, 0.5)}),
    "hill": (op_hill, {"x": (-1.0, 2.0), "y": (-1.0, 2.0), "radius": (0.01, 2.0),
                       "height": (-1.0, 1.0)}),
    "crater": (op_crater, {"x": (-1.0, 2.0), "y": (-1.0, 2.0), "radius": (0.01, 2.0),
                           "depth": _UNIT, "rim": _UNIT}),
    "plateau": (op_plateau, {"x": (-1.0, 2.0), "y": (-1.0, 2.0), "radius": (0.0, 2.0),
                             "height": _UNIT, "blend": (0.001, 1.0)}),
    "path": (op_path, {"points": None, "width": (0.0, 1.0), "level": _UNIT,
                       "blend": (0.001, 1.0)}),
    "generate": (op_generate, {"shape": ("mountain", "island", "crater", "canyon", "dunes",
                                         "plateau"),
                               "seed": _SEED, "roughness": _UNIT,
                               "mode": ("replace", "add", "max", "min", "multiply"),
                               "weight": _UNIT}),
}
_WHOLE = {"steps", "passes", "cells", "octaves", "seed", "iterations",
          "droplets", "quarter_turns"}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_value(op: str, key: str, value: Any, rule: Any) -> None:
    where = f"{op}.{key}"
    if key == "points":
        if not (isinstance(value, list) and 2 <= len(value) <= 64 and all(
                isinstance(p, (list, tuple)) and len(p) == 2 and all(map(_number, p)) for p in value)):
            raise BryceError(f"{where} must be 2-64 [x, y] pairs")
    elif isinstance(rule[0], str):
        if value not in rule:
            raise BryceError(f"{where} must be one of {', '.join(rule)}")
    else:
        if not _number(value) or not rule[0] <= value <= rule[1]:
            raise BryceError(f"{where} must be a number from {rule[0]} to {rule[1]}")
        whole = key in _WHOLE or (key == "radius" and op in ("smooth", "sharpen"))
        if whole and value != int(value):
            raise BryceError(f"{where} must be a whole number")


def validate(operations: list[dict], size: int) -> None:
    """Check everything before running anything."""
    if len(operations) > 50:
        raise BryceError("at most 50 operations per call")
    thermal = droplets = 0
    for position, step in enumerate(operations):
        if not isinstance(step, dict) or step.get("op") not in OPERATIONS:
            raise BryceError(f"operation {position}: 'op' must be one of {', '.join(OPERATIONS)}")
        name, rules = step["op"], OPERATIONS[step["op"]][1]
        for key, value in step.items():
            if key == "op":
                continue
            if key not in rules:
                raise BryceError(f"{name} has no parameter {key!r}; it takes {', '.join(rules) or 'none'}")
            _check_value(name, key, value, rules[key])
        if name == "path" and "points" not in step:
            raise BryceError("path needs points")
        if name == "thermal_erosion":
            thermal += step.get("iterations", 20) * size * size
        if name == "hydraulic_erosion":
            droplets += step.get("droplets", 20000)
    # The engine is plain Python; keep a single call to a couple of minutes.
    if thermal > 30_000_000 or droplets > 300_000:
        raise BryceError("too much erosion for one call; use fewer iterations or droplets")


def apply(size: int, grid: Grid, operations: list[dict]) -> Grid:
    validate(operations, size)
    for step in operations:
        arguments = {k: v for k, v in step.items() if k != "op"}
        grid = _clamp(OPERATIONS[step["op"]][0](size, grid, **arguments))
    return grid


def describe_operations() -> dict:
    import inspect
    out = {}
    for name, (function, rules) in OPERATIONS.items():
        defaults = {p.name: p.default for p in inspect.signature(function).parameters.values()
                    if p.name not in ("size", "grid")}
        out[name] = {"parameters": defaults, "limits": rules,
                     "notes": " ".join((function.__doc__ or "").split())}
    return out
