"""Five example scenes made only with the same operations br3_edit takes: textured
materials, eroded terrains, preset skies and new meshes built by the code below.

    python examples/gallery.py build     writes the scenes and previews
    python examples/gallery.py render    renders them in the running Bryce

Needs your own templates.br3 (see br3_status) and your Bryce folder, found from
the running program or BRYCE3D_INSTALL. Files go to BRYCE3D_OUTPUT_DIR/gallery.
"""

from __future__ import annotations

import colorsys
import json
import math
import os
import random
import sys
import tempfile

import numpy as np
from PIL import Image

from bryce3d_mcp import preview, sceneops, server
from bryce3d_mcp.br3 import Br3


def add(kind, name, position, size, material=None, rotation=None):
    step = {"op": "add", "kind": kind, "name": name, "position": [round(v, 2) for v in position],
            "size": [round(v, 2) for v in size]}
    if material:
        step["material"] = material
    if rotation:
        step["rotation"] = rotation
    return step


def copy(of, name, position, **extra):
    return {"op": "duplicate", "name": of, "new_name": name,
            "position": [round(v, 2) for v in position], **extra}


def wear(name, preset, **material):
    step = {"op": "material_preset", "name": name, "preset": preset}
    if material:
        step["material"] = material
    return step


def paint(name, preset, **material):
    """A flat preset used as a clean base, then adjusted."""
    return wear(name, preset, **material)


def ripples(name, strength):
    return {"op": "texture", "name": name, "texture": "BasicSin24", "channels": ["bump"],
            "strength": strength}


def mountain(preset, name, x, z, width, height, depth=None):
    """A model from Bryce's mountain presets, sized to stand on the ground."""
    return [{"op": "insert_preset", "preset": preset, "name": name},
            {"op": "set", "name": name, "position": [x, height / 2, z],
             "size": [width, height, depth or width]}]


def sea(name, colour, reflection=0.55, bump=0.12):
    return [add("plane", name, [0, 0, 0], [20.48, 20.48, 20.48]),
            wear(name, "Mirror", color=colour, diffuse=0.45, ambient=0.2, reflection=reflection),
            ripples(name, bump)]


def chrome(name, position, diameter, tint="#dfe8ff"):
    return [add("sphere", name, position, [diameter] * 3),
            wear(name, "Mirror", color=tint, diffuse=0.12, reflection=0.92, specular=1.0)]


def sky(preset, azimuth, altitude):
    return [{"op": "sky_preset", "preset": preset},
            {"op": "sun", "azimuth": azimuth, "altitude": altitude}]


def camera(position, look_at, fov=60, bank=0):
    return {"op": "camera", "position": position, "look_at": look_at, "fov": fov,
            "bank": bank}


# Geometry builders ----------------------------------------------------------------
# Everything the primitives cannot say is made as a new mesh.
def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _unit(v):
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / length for c in v]


def mesh(name, vertices, faces, smooth_angle=40, material=None):
    step = {"op": "mesh", "shape": "custom", "name": name,
            "vertices": [[round(c, 3) for c in v] for v in vertices],
            "faces": faces, "smooth_angle": smooth_angle}
    if material:
        step["material"] = material
    return step


def sweep(points, section, up=(0.0, 0.0, 1.0)):
    """Run a closed cross-section of (side, up) points along a path, with end caps.
    `section` may be a function of the position along the path, from 0 to 1."""
    vertices, faces = [], []
    last = len(points) - 1
    for i, point in enumerate(points):
        along = _unit(_sub(points[min(i + 1, last)], points[max(i - 1, 0)]))
        across = _cross(along, list(up))
        if sum(c * c for c in across) < 1e-4:                 # the path runs along `up`
            across = _cross(along, [0.0, 1.0, 0.0])
        side = _unit(across)
        rise = _cross(side, along)
        shape = section(i / last) if callable(section) else section
        vertices += [[point[k] + side[k] * a + rise[k] * b for k in range(3)]
                     for a, b in shape]
    count = len(vertices) // len(points)
    for i in range(last):
        for j in range(count):
            a, b = i * count + j, i * count + (j + 1) % count
            faces += [[a, b, b + count], [a, b + count, a + count]]
    for end, flip in ((0, True), (last, False)):
        ring = [end * count + j for j in range(count)]
        faces.append(ring[::-1] if flip else ring)
    return vertices, faces


def circle(radius, sides=10):
    return [(radius * math.cos(math.tau * j / sides), radius * math.sin(math.tau * j / sides))
            for j in range(sides)]


def tube(name, points, radius, sides=10, material=None):
    return mesh(name, *sweep(points, circle(radius, sides)), 70, material)


def hoop(name, centre, radius, thickness, turn=(0, 0, 0), along=72, sides=14):
    """A thin ring, tilted by turning about x, then y, then z (degrees)."""
    def rotate(v):
        x, y, z = v
        for axis, degrees in enumerate(turn):
            c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
            if axis == 0:
                y, z = c * y - s * z, s * y + c * z
            elif axis == 1:
                x, z = c * x + s * z, -s * x + c * z
            else:
                x, y = c * x - s * y, s * x + c * y
        return [x, y, z]

    vertices, faces = [], []
    for i in range(along):
        a = math.tau * i / along
        for j in range(sides):
            b = math.tau * j / sides
            reach = radius + thickness * math.cos(b)
            offset = rotate([reach * math.cos(a), thickness * math.sin(b),
                             reach * math.sin(a)])
            vertices.append([centre[k] + offset[k] for k in range(3)])
    for i in range(along):
        for j in range(sides):
            p, q = i * sides + j, i * sides + (j + 1) % sides
            r, t = ((i + 1) % along) * sides + (j + 1) % sides, ((i + 1) % along) * sides + j
            faces += [[p, q, r], [p, r, t]]
    return mesh(name, vertices, faces, 70)


def turned(name, profile, base, lean=0.0, sides=48, flutes=0, depth=0.0):
    """A shape turned on a lathe from (radius, height) pairs, optionally fluted,
    then leaned sideways by `lean` degrees about its foot."""
    c, s = math.cos(math.radians(lean)), math.sin(math.radians(lean))
    vertices, faces = [], []
    for radius, height in profile:
        for j in range(sides):
            angle = math.tau * j / sides
            cut = 1 - depth * abs(math.sin(flutes * angle / 2)) if flutes else 1
            x, y, z = radius * cut * math.cos(angle), height, radius * cut * math.sin(angle)
            vertices.append([base[0] + c * x + s * y, base[1] - s * x + c * y, base[2] + z])
    for i in range(len(profile) - 1):
        for j in range(sides):
            a, b = i * sides + j, i * sides + (j + 1) % sides
            faces += [[a, b + sides, b], [a, a + sides, b + sides]]
    faces.append(list(range(sides)))
    faces.append([(len(profile) - 1) * sides + j for j in range(sides)][::-1])
    return mesh(name, vertices, faces, 32)


def catmull(points, steps):
    """A smooth curve through the points."""
    out = []
    for i in range(len(points) - 1):
        p0, p1 = points[max(i - 1, 0)], points[i]
        p2, p3 = points[i + 1], points[min(i + 2, len(points) - 1)]
        for n in range(steps):
            t = n / steps
            out.append([0.5 * (2 * p1[k] + (p2[k] - p0[k]) * t
                               + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                               + (3 * p1[k] - p0[k] - 3 * p2[k] + p3[k]) * t ** 3)
                        for k in range(3)])
    return out + [list(points[-1])]


_PHI = (1 + 5 ** 0.5) / 2


def _icosphere(levels):
    points = [_unit(p) for p in (
        (-1, _PHI, 0), (1, _PHI, 0), (-1, -_PHI, 0), (1, -_PHI, 0),
        (0, -1, _PHI), (0, 1, _PHI), (0, -1, -_PHI), (0, 1, -_PHI),
        (_PHI, 0, -1), (_PHI, 0, 1), (-_PHI, 0, -1), (-_PHI, 0, 1))]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9),
             (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2),
             (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
             (8, 6, 7), (9, 8, 1)]
    for _ in range(levels):
        middle = {}

        def half(a, b):
            key = (min(a, b), max(a, b))
            if key not in middle:
                middle[key] = len(points)
                points.append(_unit([(points[a][k] + points[b][k]) / 2 for k in range(3)]))
            return middle[key]

        finer = []
        for a, b, c in faces:
            ab, bc, ca = half(a, b), half(b, c), half(c, a)
            finer += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = finer
    return points, faces


def rock(name, centre, size, seed, levels=4, rough=0.34):
    """A craggy boulder: a finely divided ball pushed in and out by layered bumps."""
    rng = random.Random(seed)
    waves = []
    for octave, count in enumerate((4, 6, 10, 14)):
        for _ in range(count):
            direction = _unit([rng.gauss(0, 1) for _ in range(3)])
            waves.append((direction, 2.2 * 1.9 ** octave, rng.uniform(0, math.tau),
                          rough * 0.55 ** octave / count ** 0.5))
    points, faces = _icosphere(levels)
    vertices = []
    for p in points:
        push = 1.0
        for direction, frequency, phase, height in waves:
            wave = math.sin(frequency * sum(p[k] * direction[k] for k in range(3)) + phase)
            push += height * (1 - 2 * abs(wave))              # sharp creases
        vertices.append([centre[k] + p[k] * size[k] / 2 * max(0.35, push) for k in range(3)])
    return mesh(name, vertices, [list(f) for f in faces], 30)


_SPECK = ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1))
_SPECK_FACES = ((0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2))


def confetti(path, count=6000, bands=12, seed=7):
    """A rainbow cloud of tiny solid flecks along a curve: one mesh per colour."""
    rng = random.Random(seed)
    curve = catmull(path, 60)
    parts = [([], []) for _ in range(bands)]
    for n in range(count):
        t = n / count
        base = curve[int(t * (len(curve) - 1))]
        spread = 6.8 * (1 - 0.72 * t)
        off = [rng.gauss(0, spread * w) for w in (0.5, 0.62, 0.5)]
        band = min(bands - 1, max(0, int((off[1] / (spread * 2.2) + 0.5
                                          + rng.gauss(0, 0.05)) * bands)))
        size = rng.uniform(0.13, 0.42) * (1.15 - 0.5 * t)
        vertices, faces = parts[band]
        first = len(vertices)
        turn = rng.uniform(0, math.tau)
        c, s = math.cos(turn), math.sin(turn)
        for x, y, z in _SPECK:
            vertices.append([base[0] + off[0] + size * (c * x - s * y),
                             base[1] + off[1] + size * (s * x + c * y),
                             base[2] + off[2] + size * z])
        faces += [[first + i for i in face] for face in _SPECK_FACES]
    ops = []
    for band, (vertices, faces) in enumerate(parts):
        if not faces:
            continue
        red, green, blue = colorsys.hsv_to_rgb(0.82 * band / (bands - 1), 0.88, 1.0)
        colour = "#%02x%02x%02x" % (int(red * 255), int(green * 255), int(blue * 255))
        name = f"Confetti {band}"
        ops += [mesh(name, vertices, faces, 1),
                paint(name, "Flat White", color=colour, ambient=0.8, diffuse=0.55)]
    return ops


def texture(name, texture_name, channels, strength=None):
    step = {"op": "texture", "name": name, "texture": texture_name, "channels": channels}
    if strength is not None:
        step["strength"] = strength
    return step


COLOURED = ["color", "ambient_color"]


def land(name, centre, size, seed, shape="mountain", sharp=0.34, falloff=True):
    """A terrain with its own eroded shape. `centre` is the middle of its base."""
    steps = [{"op": "generate", "shape": shape, "seed": seed, "roughness": 0.62},
             {"op": "ridges", "amount": sharp, "cells": 6, "octaves": 5, "seed": seed + 1},
             {"op": "hydraulic_erosion", "droplets": 14000, "seed": seed + 2},
             {"op": "sharpen", "amount": 0.7, "radius": 2}]
    if falloff:
        steps.append({"op": "edge_falloff", "width": 0.16})
    steps.append({"op": "normalize"})
    return [add("terrain", name, [centre[0], centre[1] + size[1] / 2, centre[2]], size),
            {"op": "terrain", "name": name, "operations": steps}]


# 1 -------------------------------------------------------------------------------
def dream_sea():
    ops = [*sky("The Big Sky", 235, 38),
           add("plane", "Checker Sea", [0, 0, 0], [20.48] * 3),
           wear("Checker Sea", "Mirrored Checkerboard", reflection=0.58),
           ripples("Checker Sea", 0.05),
           camera([0, 25, -180], [0, 38, -40])]
    for name, position, diameter in (("Orb Near", (30, 22, -76), 36),
                                     ("Orb High", (2, 62, -38), 34),
                                     ("Orb Far", (40, 78, -8), 15),
                                     ("Orb Farthest", (49, 100, 18), 11)):
        ops += chrome(name, position, diameter)
    # A glassy stairway that leaves the water and keeps climbing.
    ops += [add("cube", "Step 0", [-52, 1.2, -50], [15, 1.5, 4.2], rotation=[0, -24, 0]),
            wear("Step 0", "White Metal", color="#dcd8ff", reflection=0.5,
                 transparency=0.22, specular=1.0)]
    for i in range(1, 64):
        t = i / 63
        ops.append(copy("Step 0", f"Step {i}", [-52 + 52 * t ** 1.35, 1.2 + 112 * t,
                                                  -50 + 80 * t]))
    # A leaning fluted column carrying a carved jar.
    lean, foot = 23.0, (-7.0, -3.0, -88.0)

    def up(height, across=0.0, forward=0.0):
        c, s = math.cos(math.radians(lean)), math.sin(math.radians(lean))
        return [foot[0] + c * across + s * height, foot[1] - s * across + c * height,
                foot[2] + forward]

    ops += [turned("Column", [(3.6, 0), (3.6, 2.5), (3.0, 3.2), (2.8, 22), (2.55, 24.5)],
                   foot, lean, sides=64, flutes=18, depth=0.13),
            turned("Capital", [(2.6, 24.3), (3.7, 24.9), (3.7, 25.9), (3.0, 26.4),
                               (3.9, 27.0), (3.9, 27.5), (2.4, 28.0)], foot, lean),
            turned("Jar", [(2.4, 27.9), (4.3, 29.2), (5.4, 31.2), (4.9, 31.8), (5.6, 32.6),
                           (5.2, 35.0), (4.0, 36.3), (4.4, 36.9), (4.4, 38.4), (3.4, 38.9),
                           (1.0, 39.3)], foot, lean)]
    for name, height, radius in (("Band Low", 31.5, 5.25), ("Band Neck", 36.6, 4.35),
                                 ("Band Top", 38.5, 4.3)):
        ops.append(hoop(name, up(height), radius, 0.36, (0, 0, -lean)))
    ops += [hoop("Handle", up(40.9), 1.7, 0.5, (90, 0, -lean)),
            tube("Brow", [up(34.9, a, -5.0 - 0.5 * math.cos(a / 3)) for a in
                          (-3.2, -1.6, 0, 1.6, 3.2)], 0.55),
            tube("Grin", [up(32.9 - 1.1 * math.cos(a / 2.2), a, -5.35) for a in
                          (-2.8, -1.7, -0.6, 0.6, 1.7, 2.8)], 0.45)]
    for n, across in enumerate((-1.9, 1.9)):
        ops += [add("sphere", f"Eye {n}", up(34.0, across, -4.9), [1.9, 1.9, 1.9]),
                hoop(f"Lid {n}", up(34.0, across, -5.2), 1.15, 0.28, (90, 0, 0))]
    stone = ["Column", "Capital", "Jar", "Band Low", "Band Neck", "Band Top", "Handle",
             "Brow", "Grin", "Eye 0", "Eye 1", "Lid 0", "Lid 1"]
    for name in stone:
        ops += [paint(name, "Flat Gray", color="#9a96b4", ambient=0.24, diffuse=0.9,
                      specular=0.25),
                texture(name, "Sand1", ["bump"], 0.3)]
    # A playground slide standing in the sea.
    grey = {"color": "#b4b0cc", "specular": 0.5, "reflection": 0.12}
    parts = []
    for n, (dx, dz) in enumerate(((-4, -4), (4, -4), (-4, 4), (4, 4))):
        parts += [tube(f"Leg {n}", [[-20 + dx, -1, -52 + dz], [-20 + dx, 41, -52 + dz]], 0.42,
                       8)]
    parts += [add("cube", "Deck", [-20, 30, -52], [10, 1.2, 10]),
              mesh("Slide Roof", *sweep([[-20, 41, -58], [-20, 41, -46]],
                                        [(-6.4, 0), (6.4, 0), (0, 5.6)], up=(0, 1, 0)), 10)]
    for n, z in enumerate((-56, -48)):
        parts += [tube(f"Rail {n}", [[-24, 35.5, z], [-16, 35.5, z]], 0.35, 8),
                  tube(f"Ladder {n}", [[-33, -1, -52 + (z + 52) * 0.6],
                                       [-24.5, 30.5, -52 + (z + 52) * 0.6]], 0.35, 8)]
    for n in range(9):
        t = (n + 1) / 10
        parts.append(tube(f"Rung {n}", [[-33 + 8.5 * t, -1 + 31.5 * t, -54.4],
                                        [-33 + 8.5 * t, -1 + 31.5 * t, -49.6]], 0.3, 6))
    trough = [(-3.4, 2.1), (-3.0, 0.2), (-2.0, -0.55), (0, -0.8), (2.0, -0.55), (3.0, 0.2),
              (3.4, 2.1), (2.8, 2.1), (2.4, 0.55), (0, 0.05), (-2.4, 0.55), (-2.8, 2.1)]
    chute = catmull([[-15.5, 30.4, -52], [-9, 27.5, -53], [-3, 20, -56], [2, 14.5, -60],
                     [8, 12.5, -63], [12.5, 12.8, -64]], 12)
    parts.append(mesh("Chute", *sweep(chute, trough, up=(0, 0, 1)), 55))
    ops += parts
    for step in parts:
        ops.append(paint(step["name"], "White Metal", **grey))
    # A ribbon of rainbow confetti: thousands of flecks, one mesh per colour.
    ops += confetti([(-78, 90, -72), (-52, 52, -66), (-34, 31, -62), (-14, 27, -48),
                     (16, 22, -18), (46, 26, 14), (84, 24, 36)])
    # Jagged pink peaks behind the camera, there for the mirrors to find.
    for n, (x, z, wide, tall) in enumerate(((-230, -420, 260, 130), (-30, -470, 300, 100),
                                            (200, -430, 280, 150), (390, -250, 220, 90),
                                            (-400, -220, 230, 110))):
        name = f"Pink Peaks {n}"
        ops += [*land(name, (x, -3, z), [wide, tall, wide * 0.8], 20 + n * 7, "mountain", 0.45),
                paint(name, "Flat White", color="#c592cf", ambient=0.3, diffuse=0.85),
                texture(name, "Sand1", ["bump"], 0.5)]
    return ops


# 2 -------------------------------------------------------------------------------
def silver_drops():
    ops = [*sky("Arctic Horizon", 300, 32), *sea("Still Water", "#4f8fe8", 0.62, 0.09),
           camera([0, 46, -170], [0, 34, -50], 64, bank=-9)]
    for name, position, diameter in (("Drop Giant", (-34, 67, -78), 36),
                                     ("Drop", (5, 14, -62), 27),
                                     ("Drop High", (34, 66, -48), 15)):
        ops += chrome(name, position, diameter, "#cfe4ff")
    for n, (position, diameter) in enumerate((((-60, 7, 90), 8), ((72, 5, 150), 7),
                                              ((24, 4, 240), 6))):
        ops += chrome(f"Distant Drop {n}", position, diameter, "#cfe4ff")
    ops += [add("sphere", "Halo Lens", [-34, 67, -78], [38.5, 38.5, 38.5]),
            wear("Halo Lens", "Mirror", color="#bfe8ff", diffuse=0.05, reflection=0.2,
                 transparency=0.86, refraction=1.04)]
    return ops


# 3 -------------------------------------------------------------------------------
def orrery():
    ops = [*sky("Wisp o'the Will", 245, 28), *sea("Ocean", "#1d4f93", 0.45, 0.2),
           camera([0, 30, -186], [0, 40, -40])]
    centre = (26, 48, -52)

    def metal(name, tint="#b8c4d8", shine=0.72):
        return wear(name, "Metallic Chrome", color=tint, diffuse=0.22, reflection=shine,
                    specular=1.0)

    for n, (radius, turn) in enumerate(((29, (9, 0, -9)), (28, (-17, 0, 19)),
                                        (27, (68, 0, -24)), (25.5, (-52, 34, 14)),
                                        (23.5, (84, -26, 6)))):
        ops += [hoop(f"Ring {n}", centre, radius, 1.35 if n < 3 else 1.0, turn, 96),
                metal(f"Ring {n}")]
    ops += [tube("Spindle", [[centre[0] - 29, centre[1], centre[2]],
                             [centre[0] + 29, centre[1], centre[2]]], 0.55), metal("Spindle")]
    for side, x in (("Left", -28.5), ("Right", 28.5)):
        ops += chrome(f"Hub {side}", (centre[0] + x, centre[1], centre[2]), 6.2)
        ops += [hoop(f"Collar {side}", (centre[0] + x * 0.86, centre[1], centre[2]), 2.3, 0.7,
                     (0, 0, 90)), metal(f"Collar {side}")]
    for n in range(8):
        angle = math.tau * n / 8
        spot = (centre[0] + 12.5 * math.cos(angle), centre[1] + 4.5 * math.sin(angle * 2),
                centre[2] + 12.5 * math.sin(angle))
        ops += chrome(f"Bead {n}", spot, 5.6, "#c8d4e8")
        ops += [tube(f"Spoke {n}", [list(centre), list(spot)], 0.28, 6), metal(f"Spoke {n}")]
    ops += chrome("Core", centre, 4.2)
    # The glass citadel on a cluster of eroded crags.
    for n, (x, z, wide, tall) in enumerate(((-114, 150, 124, 82), (-60, 176, 84, 44),
                                            (-182, 164, 100, 58), (-128, 118, 70, 30),
                                            (-86, 134, 52, 38))):
        name = f"Crag {n}"
        ops += [*land(name, (x, -2, z), [wide, tall, wide * 0.75], 40 + n * 5, "mountain", 0.5),
                paint(name, "Flat Gray", color="#7f93b4", ambient=0.24, diffuse=0.9,
                      reflection=0.16, specular=0.7),
                texture(name, "Sand1", ["bump"], 0.55)]
    crown = (-114, 90, 148)
    ops += chrome("Crown Pearl", crown, 30, "#e8f4ff")

    def crystal(name):
        return wear(name, "White Metal", color="#dcefff", reflection=0.6, transparency=0.24,
                    specular=1.0)

    for n in range(4):
        ops += [hoop(f"Crown Arch {n}", crown, 28, 2.2, (90, 45 * n, 0), 80),
                crystal(f"Crown Arch {n}")]
    ops += [hoop("Crown Girdle", (crown[0], crown[1] - 6, crown[2]), 29, 1.5),
            crystal("Crown Girdle")]
    for n in range(14):
        angle = math.tau * n / 14
        high = 1 if n % 2 else 0
        spot = (crown[0] + (32 - 9 * high) * math.cos(angle), crown[1] + 8 + 16 * high,
                crown[2] + (32 - 9 * high) * math.sin(angle))
        for ring, turn in enumerate(((90, 0, 0), (90, 90, 0), (0, 0, 0))):
            name = f"Charm {n}-{ring}"
            ops += [hoop(name, spot, 5.2, 0.55, turn, 32, 8), crystal(name)]
        ops += chrome(f"Charm Pearl {n}", spot, 3.4, "#f0f8ff")
    ops += [add("plane", "Sea Haze", [0, 3.2, 700], [20.48] * 3),
            paint("Sea Haze", "Flat White", color="#cfe6ff", ambient=0.6, diffuse=0.35,
                  transparency=0.86, refraction=1.0)]
    return ops


# 4 -------------------------------------------------------------------------------
def _noise(size, cells, rng):
    """Gradient noise from 0 to 1 on a size x size grid. Each call turns its lattice
    by its own angle, so that layers of it show no square grain."""
    turn, shift = rng.uniform(0, math.tau), rng.uniform(0, 64, 2)
    angles = rng.uniform(0, math.tau, 512)
    order = rng.permutation(512)
    line = np.linspace(0, cells, size, endpoint=False)
    across, down = np.meshgrid(line, line)
    x = across * math.cos(turn) - down * math.sin(turn) + shift[0]
    y = across * math.sin(turn) + down * math.cos(turn) + shift[1]
    ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - ix, y - iy

    def slope(dx, dy):
        picked = angles[order[(order[(ix + dx) % 512] + iy + dy) % 512]]
        return np.cos(picked) * (fx - dx) + np.sin(picked) * (fy - dy)

    u = fx ** 3 * (fx * (fx * 6 - 15) + 10)
    v = fy ** 3 * (fy * (fy * 6 - 15) + 10)
    top = slope(0, 0) * (1 - u) + slope(1, 0) * u
    low = slope(0, 1) * (1 - u) + slope(1, 1) * u
    return np.clip(0.5 + 0.72 * (top * (1 - v) + low * v), 0, 1)


def _fractal(size, cells, octaves, rng, ridged=False):
    total, weight, height = 0.0, 0.0, 1.0
    for octave in range(octaves):
        layer = _noise(size, cells * 2 ** octave, rng)
        if ridged:
            layer = (1 - np.abs(2 * layer - 1)) ** 2          # sharp crests
        total, weight = total + height * layer, weight + height
        height *= 0.55
    return total / weight


def heights(kind, size, seed):
    """One round landform, as numbers from 0 to 1. Nothing in it depends on which
    way the grid runs: stretching is left to the terrain's size in the scene."""
    rng = np.random.default_rng(seed)
    axis = np.linspace(-1, 1, size)
    x, z = np.meshgrid(axis, axis)
    # A ragged round outline.
    reach = np.hypot(x, z) + 0.22 * (_fractal(size, 4, 4, rng) - 0.5)
    edge = np.clip((0.97 - reach) / 0.3, 0, 1)
    edge = edge * edge * (3 - 2 * edge)
    ridges = _fractal(size, 5, 6, rng, ridged=True)
    grit = _fractal(size, 24, 4, rng)
    if kind == "mound":                       # a broad hump of broken rock
        dome = np.clip(1 - reach ** 2, 0, 1) ** 0.8
        out = dome * (0.35 + 0.75 * ridges) + 0.09 * (grit - 0.5) * edge
    elif kind == "rubble":                    # rolling ground strewn with broken rock
        swell = _fractal(size, 3, 4, rng)
        out = edge * (0.05 + 0.5 * np.clip(swell - 0.38, 0, 1)
                      + 0.32 * np.clip(ridges - 0.3, 0, 1) + 0.09 * (grit - 0.45))
        step = 2 / (size - 1)
        for _ in range(230):                  # mostly small stones, a few outcrops
            radius = 0.014 + 0.085 * rng.random() ** 3
            turn, far = rng.uniform(0, math.tau), math.sqrt(rng.random()) * 0.84
            cx, cz = far * math.cos(turn), far * math.sin(turn)
            c0, c1 = (int((cx - radius * 2 + 1) / step), int((cx + radius * 2 + 1) / step) + 2)
            r0, r1 = (int((cz - radius * 2 + 1) / step), int((cz + radius * 2 + 1) / step) + 2)
            part = np.s_[max(r0, 0):r1, max(c0, 0):c1]
            # Each stone is stretched its own way and bitten by the finer noise.
            lie, long = rng.uniform(0, math.pi), rng.uniform(1.0, 1.9)
            dx, dz = x[part] - cx, z[part] - cz
            u = (dx * math.cos(lie) + dz * math.sin(lie)) / long
            v = dz * math.cos(lie) - dx * math.sin(lie)
            apart = np.hypot(u, v) * (0.35 + 1.3 * grit[part])
            body = np.clip(1 - (apart / radius) ** 2, 0, 1) ** rng.uniform(0.55, 1.5)
            top = radius * rng.uniform(1.6, 4.2) * body * (0.3 + 1.25 * ridges[part])
            out[part] += top * edge[part]
    elif kind == "range":                     # a massif of sharp peaks
        peaks = np.zeros_like(x)
        for n in range(9):
            cx = -0.62 + 0.155 * n + rng.uniform(-0.05, 0.05) if n % 2 else rng.uniform(-0.55, 0.55)
            cz = rng.uniform(-0.45, 0.45)
            spread, tall = rng.uniform(0.28, 0.5), rng.uniform(0.35, 1.0)
            peaks = np.maximum(peaks, tall * np.clip(
                1 - np.hypot(x - cx, z - cz) / spread, 0, 1) ** 1.15)
        out = edge * (peaks * (0.45 + 0.85 * ridges) + 0.06 * (grit - 0.5))
    else:
        raise ValueError(kind)
    out = np.clip(out, 0, None) * (edge > 0)
    # One freak spike should not set the height of everything else.
    return np.clip(out / np.quantile(out, 0.9995), 0, 1)


def landform(name, kind, centre, size, seed, preset="Big Sur"):
    """A terrain made from `heights`, on one of Bryce's finer preset terrains
    ("Big Sur" is 512 samples a side, "Basic River Terrain" 256). `centre` is the
    middle of its base."""
    detail = 512 if preset == "Big Sur" else 256
    folder_ = os.path.join(tempfile.gettempdir(), "bryce3d_gallery")
    os.makedirs(folder_, exist_ok=True)
    path = os.path.join(folder_, f"{kind}_{seed}_{detail}.png")
    grid = (heights(kind, detail, seed) * 65535).round().astype(np.uint16)
    Image.fromarray(grid).save(path)
    return [{"op": "insert_preset", "preset": preset, "name": name},
            {"op": "set", "name": name, "size": list(size),
             "position": [centre[0], centre[1] + size[1] / 2, centre[2]]},
            {"op": "terrain", "name": name, "heightmap_path": path}]


def arch_mountain(name, centre, scale, seed=5, step=1.15):
    """A rock massif with a round hole through it, as one closed mesh. The outline
    and the hole are in units that `scale` turns into scene units; the thickness
    shrinks to nothing at every edge, so the front and back faces meet there."""
    rng = np.random.default_rng(seed)
    # (across, height) along the top, measured from under the middle of the hole.
    outline = [(-76, -3), (-66, 5), (-57, 8), (-49, 17), (-42, 22), (-35, 30),
               (-30, 37), (-26, 55), (-22, 47), (-17, 51), (-13, 43), (-8, 49),
               (-1, 55), (8, 57), (15, 49), (20, 43), (24, 51), (29, 40), (35, 31),
               (40, 22), (46, 15), (52, 17), (58, 8), (66, 6), (76, -3)]
    across = np.arange(-76, 76 + step / 2, step)
    up = np.arange(-4, 62, step)
    X, Y = np.meshgrid(across, up)
    waves = _fractal(256, 6, 5, rng, ridged=True)
    fine = _fractal(256, 20, 4, rng)

    def rough(field, x, y, zoom=1.0, shift=0):
        i = ((x * zoom + 90 + shift) % 180 / 180 * 255).astype(int)
        j = ((y * zoom + 20 + shift) % 180 / 180 * 255).astype(int)
        return field[j, i]

    top = np.interp(X, *zip(*outline)) + 9 * (rough(fine, X, X * 0.37, 2.1) - 0.5)
    hole_x, hole_y = 3.0, 15.0
    around = np.arctan2(Y - hole_y, X - hole_x)
    ragged = 1 + 0.07 * np.sin(5 * around + 1) + 0.05 * np.sin(11 * around)
    hole = (np.hypot((X - hole_x) / 14.5, (Y - hole_y) / 13.5) - ragged) * 14
    inside = np.minimum.reduce([top - Y, hole, Y + 3.5])

    def thickness(x, y, f, shift):
        body = 15 * np.clip(1 - y / 80, 0.25, 1)
        bite = (0.45 + 0.95 * rough(waves, x, y, 1.0, shift)
                + 0.35 * (rough(fine, x, y, 1.6, shift) - 0.5))
        return body * np.sqrt(np.clip(f / 9, 0, 1)) * np.clip(bite, 0.2, None)

    rows, columns = X.shape
    points: list[tuple[float, float, float]] = []       # x, y, f
    seen: dict = {}

    def corner(r, c):
        key = (r, c)
        if key not in seen:
            seen[key] = len(points)
            points.append((X[r, c], Y[r, c], inside[r, c]))
        return seen[key]

    def crossing(a, b):
        key = (min(a, b), max(a, b))
        if key not in seen:
            (ra, ca), (rb, cb) = a, b
            t = inside[ra, ca] / (inside[ra, ca] - inside[rb, cb])
            seen[key] = len(points)
            points.append((X[ra, ca] + (X[rb, cb] - X[ra, ca]) * t,
                           Y[ra, ca] + (Y[rb, cb] - Y[ra, ca]) * t, 0.0))
        return seen[key]

    triangles = []
    for r in range(rows - 1):
        for c in range(columns - 1):
            for half in (((r, c), (r, c + 1), (r + 1, c + 1)),
                         ((r, c), (r + 1, c + 1), (r + 1, c))):
                polygon = []
                for i, here in enumerate(half):
                    there = half[(i + 1) % 3]
                    if inside[here] > 0:
                        polygon.append(corner(*here))
                    if (inside[here] > 0) != (inside[there] > 0):
                        polygon.append(crossing(here, there))
                triangles += [[polygon[0], polygon[i], polygon[i + 1]]
                              for i in range(1, len(polygon) - 1)]

    x, y, f = (np.array(v) for v in zip(*points))
    front, back = -thickness(x, y, f, 0), thickness(x, y, f, 47)
    rim = f <= 0
    vertices = [[x[i], y[i], front[i]] for i in range(len(x))]
    behind = {}
    for i in range(len(x)):
        if rim[i]:
            behind[i] = i                                  # shared with the front
        else:
            behind[i] = len(vertices)
            vertices.append([x[i], y[i], back[i]])
    # Counter-clockwise from outside: the front looks towards -z.
    faces = [[a, c, b] for a, b, c in triangles]
    faces += [[behind[a], behind[b], behind[c]] for a, b, c in triangles
              if not (rim[a] and rim[b] and rim[c])]
    placed = [[centre[k] + v[k] * scale for k in range(3)] for v in vertices]
    return mesh(name, placed, faces, 24)


def far_world():
    """A ringless gas giant over a plain of broken rock and low mist. Colour
    textures go only on terrains and the planet: on new meshes Bryce drew them as
    bright rectangles, so those get plain colours and a bump."""
    def stone(name, colour, shade, ambient=0.42, diffuse=0.92, bump=0.8,
              grain="DesertBump"):
        return [paint(name, "Flat Gray", color=colour, ambient_color=shade,
                      ambient=ambient, diffuse=diffuse, specular=0.04),
                texture(name, grain, ["bump"], bump)]

    def ground(name, light="#b9b08c", middle="#7c7154", dark="#383023"):
        """Mottled khaki rock: Bryce's DesertBump with its oranges replaced."""
        return [*stone(name, middle, "#33424c", 0.42, 0.95, 0.75),
                texture(name, "DesertBump", ["color"]),
                {"op": "texture_edit", "name": name, "channels": ["color"],
                 "color_sets": {"2": [light, middle, dark],
                                "4": [light, middle, dark]}}]

    # The camera is level, so the horizon sits across the middle of the picture.
    ops = [*sky("Wisp o'the Will", 118, 27),   # lights the side of the giant we see
           camera([0, 30, -190], [0, 30, 0], 62),
           # The giant: Bryce's own banded Jupiter, tipped, with a soft pink fill in
           # its shadows so that it sits in the hazy sky like a far-off world.
           add("sphere", "Giant Planet", [-590, 345, 1310], [1040] * 3,
               rotation=[0, 20, -17]),
           wear("Giant Planet", "Jupiter"),
           {"op": "set", "name": "Giant Planet",
            "material": {"ambient_color": "#c79bb8", "ambient": 0.3, "diffuse": 0.95,
                         "specular": 0.12}}]

    ops += [add("plane", "Plain", [0, 0, 0], [20.48] * 3), *ground("Plain")]
    # Foreground: a broken ridge under the camera, with rubble to either side.
    for n, (kind, x, z, wide, tall, deep, seed) in enumerate((
            ("mound", -4, -101, 118, 13.5, 66, 4),
            ("mound", 52, -112, 66, 10, 52, 12),
            ("rubble", -62, -76, 140, 15, 120, 7),
            ("rubble", 66, -62, 140, 14, 130, 19),
            ("rubble", -6, -18, 170, 9, 130, 23))):
        name = f"Near Ground {n}"
        ops += [*landform(name, kind, (x, -1.2, z), (wide, tall, deep), seed),
                *ground(name)]
    # The middle distance: fields of small sharp rocks running to the mountains.
    for n, (x, z, wide, tall, seed) in enumerate((
            (-150, 70, 250, 18, 31), (30, 105, 230, 12.5, 37), (190, 55, 240, 16, 41),
            (-80, 215, 300, 17, 43), (190, 240, 300, 18, 47), (-300, 200, 300, 20, 53),
            (20, 370, 460, 19, 59))):
        name = f"Far Ground {n}"
        ops += [*landform(name, "rubble", (x, -1.4, z), (wide, tall, wide * 0.85), seed),
                *ground(name, "#aaa68e", "#706a58", "#34333a")]

    # The great arch, and the broken rock around its feet.
    ops += [arch_mountain("Arch", (74, 0.5, 205), 1.12),
            *stone("Arch", "#716873", "#27344e", 0.5, 0.82, 0.85)]
    for n, (x, z, wide, tall, seed) in enumerate(((8, 192, 78, 17, 61),
                                                  (146, 198, 96, 19, 67),
                                                  (92, 172, 64, 8, 71))):
        name = f"Arch Foot {n}"
        ops += [*landform(name, "range", (x, -1.5, z), (wide, tall, wide * 0.62), seed,
                          "Basic River Terrain"),
                *stone(name, "#6d6570", "#26334c", 0.5, 0.82, 0.7, "Sand1")]
    rng = random.Random(11)
    for n in range(26):
        side = rng.choice((-1, 1))
        x = 77 + side * rng.uniform(33, 92)
        tall = rng.uniform(5, 17) * (1.25 - abs(x - 77) / 120)
        name = f"Spire {n}"
        ops += [turned(name, [(tall * 0.19, 0), (tall * 0.12, tall * 0.34),
                              (tall * 0.085, tall * 0.4), (tall * 0.05, tall * 0.7),
                              (0.1, tall)],
                       (x, 1, 192 + rng.uniform(-14, 6)), rng.uniform(-11, 11), sides=6),
                *stone(name, "#5d5663", "#222d45", 0.5, 0.8, 0.6, "Sand1")]

    # Blue ranges along the horizon: paler and bluer the farther they stand.
    for n, (x, z, wide, tall, deep, seed, tone, shade) in enumerate((
            (-292, 430, 330, 78, 150, 83, "#8a93b4", "#4d628f"),
            (-96, 520, 270, 46, 130, 89, "#97a6c6", "#637ca9"),
            (330, 560, 300, 92, 160, 97, "#a3b3d2", "#718bb8"),
            (112, 640, 330, 40, 140, 101, "#a9bad8", "#7d97c2"),
            (500, 430, 280, 58, 150, 103, "#8f9ab9", "#546893"),
            (-520, 560, 320, 70, 150, 107, "#a3b3d2", "#718bb8"))):
        name = f"Range {n}"
        ops += [*landform(name, "range", (x, -2, z), (wide, tall, deep), seed,
                          "Basic River Terrain"),
                *stone(name, tone, shade, 0.58, 0.5, 0.45, "Sand1")]

    # Rounded boulders sunk into the ground. (centre, width, height)
    for n, (spot, wide, tall) in enumerate((((-44, 7.5, -66), 19, 14),
                                            ((-23, 4.5, -52), 8, 7),
                                            ((-3, 5.5, -20), 14, 9),
                                            ((52, 7.5, -66), 20, 15),
                                            ((38, 3.5, -4), 11, 8),
                                            ((-74, 6, -12), 15, 11),
                                            ((-26, 3.5, 44), 10, 7),
                                            ((24, 8.5, -118), 9, 5))):
        name = f"Boulder {n}"
        ops += [rock(name, spot, (wide, tall, wide * 0.88), 100 + n, 4, 0.085),
                *stone(name, "#6f614a", "#272f36", 0.45, 0.95, 0.9, "Sand1")]
    for n, (position, wide) in enumerate((((-46, 62, 208), 15), ((11, 108, 236), 19),
                                          ((52, 58, 420), 12), ((-6, 66, 520), 9))):
        ops += [rock(f"Asteroid {n}", position, (wide, wide * 0.84, wide * 0.92),
                     300 + n, 4, 0.24),
                *stone(f"Asteroid {n}", "#a29ba0", "#3a4868", 0.42, 0.9, 0.9, "Sand1")]

    # Mist lying in the hollows: two thin sheets, one above the other.
    for n, (level, clear) in enumerate(((1.9, 0.55), (3.3, 0.82))):
        name = f"Mist {n}"
        ops += [add("plane", name, [0, level, 0], [20.48] * 3),
                paint(name, "Flat White", color="#bfe3df", ambient_color="#a6d2d4",
                      ambient=0.55, diffuse=0.4, specular=0.0, transparency=clear,
                      refraction=1.0)]
    return ops


# 5 -------------------------------------------------------------------------------
def colour_generation():
    ops = [*sky("Cold Storm Coming", 250, 30), *sea("Violet Sea", "#28307a", 0.4, 0.26),
           camera([0, 52, -192], [0, 40, -40], bank=-11)]

    def glass(name, colour, see_through=0.55):
        return wear(name, "Mirror", color=colour, diffuse=0.55, ambient=0.18, reflection=0.22,
                    transparency=see_through, transparent_color=colour, refraction=1.12,
                    specular=0.8)

    for name, position, edge, turn, colour in (
            ("Teal", (-37, 36, -62), 23, [8, 20, -10], "#10868c"),
            ("Crimson", (-14, 56, -4), 23, [4, -28, 6], "#c01868"),
            ("Olive", (10, 26, -66), 18, [24, 34, 8], "#98a818")):
        ops += [add("cube", name, position, [edge] * 3, rotation=turn), glass(name, colour),
                texture(name, "Sand1", ["bump"], 0.06)]
    # A glass cube hollowed out by a boolean: the sphere is cut from the cube.
    ops += [add("cube", "Shell", [34, 56, -50], [30] * 3, rotation=[28, 38, 14]),
            glass("Shell", "#8088d0", 0.5),
            add("sphere", "Hollow", [34, 56, -50], [37.5] * 3),
            glass("Hollow", "#8088d0", 0.5),
            {"op": "group", "names": ["Shell", "Hollow"], "negative": ["Hollow"],
             "name": "Carved Cube"}]
    return ops


# In the order they are rendered: textures and booleans on plain shapes first, then
# the scenes that rely on new meshes, so a problem shows up as early as possible.
SCENES = {"02_silver_drops": silver_drops, "05_colour_generation": colour_generation,
          "03_orrery": orrery, "04_far_world": far_world, "01_dream_sea": dream_sea}


def folder() -> str:
    path = os.path.join(server.output_directory(), "gallery")
    os.makedirs(path, exist_ok=True)
    return path


def build(names, templates_path=None, install=None, out=None) -> list[str]:
    library = server._library(templates_path)
    if library is None:
        raise SystemExit(f"No template file at {server._templates_path(templates_path)}. "
                         "See br3_status in the README.")
    install = install or server._install_folder()
    out = out or folder()
    written = []
    for name in names:
        scene = Br3.from_bytes(library.data)
        scene.remove_all_except(set())
        operations = SCENES[name]()
        for start in range(0, len(operations), sceneops.MAX_OPERATIONS):
            sceneops.apply(scene, operations[start:start + sceneops.MAX_OPERATIONS],
                           library, install)
        target = os.path.join(out, name + ".br3")
        with open(target, "wb") as handle:
            handle.write(scene.data)
        picture, _ = preview.render(scene, 640, 480)
        picture.save(os.path.join(out, name + "_preview.png"))
        print(f"built {name}: {len(operations)} operations, "
              f"{len(scene.objects())} objects -> {target}")
        written.append(target)
    return written


def prepare(ask=input) -> bool:
    """Offer the bridge and settle what happens to unsaved work, before anything
    in Bryce is touched. Returns False if the user stops here."""
    print("The bridge is a small DLL from this project, loaded into the running Bryce. It")
    print("detects unsaved work, follows renders and can press Render with a virtual")
    print("mouse. It is gone when you close Bryce.")
    if ask("Load the bridge? [Y/n] ").strip().lower() in ("", "y", "yes"):
        try:
            print(json.dumps(server.connect_bridge()))
        except Exception as error:
            print(f"The bridge could not be loaded: {error}")
            if ask("Continue without it? [y/N] ").strip().lower() not in ("y", "yes"):
                return False
    unsaved = server.get_controller().backend.unsaved_changes()
    if unsaved:
        print("Bryce has UNSAVED CHANGES. Save them in Bryce now, or type DISCARD.")
        if ask("Press Enter after saving, or type DISCARD: ").strip() == "DISCARD":
            server.get_controller().backend.forget_changes()
        elif server.get_controller().backend.unsaved_changes():
            print("There are still unsaved changes, so nothing was opened.")
            return False
    elif unsaved is None:
        print("Without the bridge, save your work in Bryce now.")
        ask("Press Enter when it is saved: ")
    return True


def render(names, timeout=1800) -> bool:
    """Open and render each scene in the running Bryce. Stops at the first problem,
    so that one failed step cannot upset the following ones."""
    results = {}
    log = os.path.join(folder(), "render_log.json")
    for number, name in enumerate(names):
        scene = os.path.join(folder(), name + ".br3")
        if not os.path.isfile(scene):
            build([name])
        print(f"rendering {name} (this can take several minutes)...", flush=True)
        controller = server.get_controller()
        try:
            # After the first scene, what is "unsaved" is the render of the one
            # this script opened itself.
            picture = os.path.join(folder(), name + "_render.png")
            result = controller.render_to_file(
                scene, picture, timeout=timeout,
                discard_changes=number > 0 and controller.backend.unsaved_changes() is True)
            if not result["finished"]:
                controller.render_picture().save(picture)
                result["picture"] = picture
        except Exception as error:
            result = {"finished": False, "error": f"{type(error).__name__}: {error}"}
            try:
                picture = os.path.join(folder(), name + "_problem.png")
                with open(picture, "wb") as handle:
                    handle.write(controller.screenshot_png())
                result["picture"] = picture
            except Exception:
                pass
        results[name] = result
        print(json.dumps({k: v for k, v in result.items() if k != "timeline"}), flush=True)
        with open(log, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1)
        if not result.get("finished"):
            print(f"Stopped at {name}. Send {log} and the pictures in {folder()}.")
            return False
    print(f"Done. Pictures and render_log.json are in {folder()}")
    return True


def main(argv) -> None:
    action = argv[0] if argv else "build"
    names = argv[1:] or list(SCENES)
    unknown = [n for n in names if n not in SCENES]
    if action not in ("build", "render") or unknown:
        raise SystemExit(f"usage: gallery.py build|render [{' '.join(SCENES)}]")
    if action == "build":
        build(names)
    elif prepare():
        render(names)


if __name__ == "__main__":
    main(sys.argv[1:])
