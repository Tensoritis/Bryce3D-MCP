"""Generate geometry and heightmaps for shapes the primitives cannot make.

Bryce 3D imports DXF, 3DMF and Wavefront OBJ; its Terrain Editor loads pictures.
Axes match Bryce: Y is up.
"""

from __future__ import annotations

import math
import random

from PIL import Image

from .errors import BryceError

Vertex = tuple[float, float, float]
Face = list[int]  # zero-based, counter-clockwise seen from outside
MAX_VERTICES = 200_000


def _signed_area(points: list[tuple[float, float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2


def _is_simple(points: list[tuple[float, float]]) -> bool:
    """No repeated points, and no two non-neighboring edges that touch or cross."""
    n = len(points)
    if len(set(points)) != n:
        return False

    def side(o, a, b):
        value = (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
        return 0 if abs(value) <= 1e-12 else (1 if value > 0 else -1)

    def within(a, b, p):
        return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
                and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))

    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        for j in range(i + 2, n - (1 if i == 0 else 0)):
            c, d = points[j], points[(j + 1) % n]
            o1, o2, o3, o4 = side(a, b, c), side(a, b, d), side(c, d, a), side(c, d, b)
            if o1 * o2 < 0 and o3 * o4 < 0:
                return False
            if any(o == 0 and within(*seg, p) for o, seg, p in
                   ((o1, (a, b), c), (o2, (a, b), d), (o3, (c, d), a), (o4, (c, d), b))):
                return False
    return True


def triangulate(points: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Ear clipping for a simple polygon; result is counter-clockwise in 2D."""
    if not _is_simple(points):
        raise BryceError("polygon is not simple (it crosses itself or has repeated points)")
    indices = list(range(len(points)))
    if _signed_area(points) < 0:
        indices.reverse()

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    triangles = []
    while len(indices) > 3:
        for i in range(len(indices)):
            a, b, c = indices[i - 1], indices[i], indices[(i + 1) % len(indices)]
            pa, pb, pc = points[a], points[b], points[c]
            if cross(pa, pb, pc) <= 1e-12:
                continue
            if any(cross(pa, pb, points[p]) >= 0 and cross(pb, pc, points[p]) >= 0
                   and cross(pc, pa, points[p]) >= 0 for p in indices if p not in (a, b, c)):
                continue
            triangles.append((a, b, c))
            del indices[i]
            break
        else:
            raise BryceError("polygon is not simple (it crosses itself or has repeated points)")
    triangles.append(tuple(indices))
    return triangles


def extrude(outline: list[list[float]], height: float) -> tuple[list[Vertex], list[Face]]:
    """Raise a 2D outline of [x, z] points into a solid prism from y=0 to y=height."""
    if len(outline) < 3 or height <= 0:
        raise BryceError("extrude needs at least 3 [x, z] points and a positive height")
    points = [(float(x), float(z)) for x, z in outline]
    if len(points) > 2000:
        raise BryceError("outline is limited to 2000 points")
    if abs(_signed_area(points)) < 1e-9:
        raise BryceError("outline has no area")
    if _signed_area(points) < 0:
        points.reverse()
    n = len(points)
    vertices = [(x, 0.0, z) for x, z in points] + [(x, float(height), z) for x, z in points]
    faces: list[Face] = []
    for a, b, c in triangulate(points):
        faces.append([a, b, c])               # bottom faces -Y
        faces.append([a + n, c + n, b + n])   # top faces +Y
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, i + n, j + n, j])
    return vertices, faces


def lathe(profile: list[list[float]], segments: int = 32) -> tuple[list[Vertex], list[Face]]:
    """Spin [radius, y] points, listed bottom to top, around the Y axis."""
    if len(profile) < 2 or not 3 <= segments <= 512:
        raise BryceError("lathe needs 2+ [radius, y] points and 3-512 segments")
    if any(r < 0 for r, _ in profile):
        raise BryceError("radius cannot be negative")
    vertices: list[Vertex] = []
    rings: list[list[int]] = []
    for radius, y in profile:
        count = 1 if radius == 0 else segments
        ring = []
        for s in range(count):
            angle = math.tau * s / segments
            ring.append(len(vertices))
            vertices.append((radius * math.cos(angle), float(y), radius * math.sin(angle)))
        rings.append(ring)
    faces: list[Face] = []
    for low, high in zip(rings, rings[1:]):
        for s in range(segments):
            t = (s + 1) % segments
            quad = [low[s % len(low)], high[s % len(high)], high[t % len(high)], low[t % len(low)]]
            face = [v for i, v in enumerate(quad) if v != quad[i - 1]]
            if len(face) >= 3:
                faces.append(face)
    return vertices, faces


def check_mesh(vertices, faces) -> None:
    if not vertices or not faces:
        raise BryceError("mesh needs vertices and faces")
    if len(vertices) > MAX_VERTICES or len(faces) > MAX_VERTICES * 2:
        raise BryceError("mesh is too large")
    for v in vertices:
        if len(v) != 3 or not all(math.isfinite(c) for c in v):
            raise BryceError("each vertex is 3 finite numbers")
    for f in faces:
        if len(f) < 3 or not all(isinstance(i, int) and 0 <= i < len(vertices) for i in f):
            raise BryceError("each face lists 3+ zero-based vertex indices")


def to_obj(vertices, faces, name: str = "mesh") -> str:
    check_mesh(vertices, faces)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "mesh"
    lines = [f"o {safe}"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices]
    lines += ["f " + " ".join(str(i + 1) for i in f) for f in faces]
    return "\n".join(lines) + "\n"


def to_dxf(vertices, faces) -> str:
    """ASCII DXF made of 3DFACE entities. DXF is Z-up, so (x, y, z) -> (x, -z, y)."""
    check_mesh(vertices, faces)
    out = ["0", "SECTION", "2", "ENTITIES"]
    for face in faces:
        for k in range(1, len(face) - 1):
            corners = [face[0], face[k], face[k + 1], face[k + 1]]
            out += ["0", "3DFACE", "8", "0"]
            for n, index in enumerate(corners):
                x, y, z = vertices[index]
                for code, value in ((10, x), (20, -z), (30, y)):
                    out += [str(code + n), f"{value:.6f}"]
    out += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(out) + "\n"


def _value_noise(size: int, cells: int, rng: random.Random) -> list[float]:
    grid = [[rng.random() for _ in range(cells + 1)] for _ in range(cells + 1)]
    values = []
    for j in range(size):
        v = j * cells / (size - 1)
        y0 = min(int(v), cells - 1)
        fy = (v - y0) ** 2 * (3 - 2 * (v - y0))
        for i in range(size):
            u = i * cells / (size - 1)
            x0 = min(int(u), cells - 1)
            fx = (u - x0) ** 2 * (3 - 2 * (u - x0))
            top = grid[y0][x0] * (1 - fx) + grid[y0][x0 + 1] * fx
            bottom = grid[y0 + 1][x0] * (1 - fx) + grid[y0 + 1][x0 + 1] * fx
            values.append(top * (1 - fy) + bottom * fy)
    return values


def heightmap(shape: str, size: int = 256, seed: int = 0, roughness: float = 0.5) -> Image.Image:
    """Grayscale terrain picture. White is high; edges fall to black."""
    if shape not in {"mountain", "island", "crater", "canyon", "dunes", "plateau"}:
        raise BryceError("shape: mountain, island, crater, canyon, dunes or plateau")
    if not 32 <= size <= 512 or not 0 <= roughness <= 1:
        raise BryceError("size must be 32-512 and roughness 0-1")
    rng = random.Random(seed)
    noise = [0.0] * (size * size)
    amplitude, total = 1.0, 0.0
    for cells in (4, 8, 16, 32, 64):
        if cells >= size:
            break
        layer = _value_noise(size, cells, rng)
        noise = [n + amplitude * v for n, v in zip(noise, layer)]
        total += amplitude
        amplitude *= 0.25 + 0.5 * roughness
    noise = [n / total for n in noise]

    heights = []
    for index, n in enumerate(noise):
        x = (index % size) / (size - 1) * 2 - 1
        y = (index // size) / (size - 1) * 2 - 1
        r = math.hypot(x, y)
        falloff = max(0.0, 1 - r * r)
        if shape == "mountain":
            h = falloff ** 1.5 * (0.45 + 1.1 * n)
        elif shape == "island":
            h = max(0.0, falloff * (0.2 + 1.4 * n) - 0.2)
        elif shape == "crater":
            rim = math.exp(-((r - 0.55) / 0.13) ** 2)
            h = falloff ** 0.3 * (0.15 + 0.65 * rim + 0.2 * n)
        elif shape == "canyon":
            channel = min(1.0, abs(x + (n - 0.5) * 0.9) * 3)
            h = (0.1 + 0.75 * channel + 0.15 * n) * min(1.0, falloff * 4)
        elif shape == "dunes":
            wave = (math.sin((x * 3 + y + n * 1.5) * math.pi * 1.5) + 1) / 2
            h = min(1.0, falloff * 3) * (0.1 + 0.4 * wave + 0.1 * n)
        else:
            h = min(1.0, falloff * 2.5) ** 3 * (0.7 + 0.15 * n)
        heights.append(max(0, min(255, round(h * 255))))
    image = Image.new("L", (size, size))
    image.putdata(heights)
    return image
