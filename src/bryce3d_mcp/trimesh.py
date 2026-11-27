"""The geometry inside a C3dTriMeshType2 record (Bryce's mesh objects: stones and
imported models).

After a 640-byte object header, all little-endian:

    640  int32    2, point count, normal count, triangle count, 0
    660  float64  bounding box: low x y z, high x y z, each side 1% wider
    860  float64  the points (x y z), then the normals (x y z)
    then 76 bytes per triangle:
         int32 x 3   point numbers
         int32 x 3   normal numbers
         12 bytes    leftover memory, not meaningful
         float64 x 3 the triangle's unit normal
         int32 x 4   2, 2, 2, 1

A stored triangle's normal is (b - a) x (c - a), which points INTO the solid, and a
stored normal at a point is the plain average of the unit normals of the triangles
that share it (not stretched back to length one). These rules reproduce the numbers
in a stone made by Bryce exactly.
"""

from __future__ import annotations

import math
import struct

from . import meshes
from .errors import BryceError

HEADER, _BOX, DATA, _FACE = 640, 660, 860, 76
_TAIL = (2, 2, 2, 1)
MAX_TRIANGLES = 50_000
DEFAULT_CREASE = 40.0


def decode(payload: bytes) -> tuple[list, list, list]:
    """(points, normals, triangles). A triangle is three point numbers followed by
    three normal numbers."""
    if len(payload) < DATA:
        raise BryceError("corrupt scene: mesh record is too short")
    _, points, normals, count, _ = struct.unpack_from("<5i", payload, HEADER)
    if min(points, normals, count) < 0 \
            or DATA + 24 * (points + normals) + _FACE * count > len(payload):
        raise BryceError("corrupt scene: mesh counts do not fit the record")

    def vectors(start, number):
        return [struct.unpack_from("<3d", payload, start + 24 * i) for i in range(number)]

    faces_at = DATA + 24 * (points + normals)
    triangles = [struct.unpack_from("<6i", payload, faces_at + _FACE * i)
                 for i in range(count)]
    if any(not 0 <= t[i] < (points if i < 3 else normals)
           for t in triangles for i in range(6)):
        raise BryceError("corrupt scene: a mesh triangle points past the end")
    return vectors(DATA, points), vectors(DATA + 24 * points, normals), triangles


def bounding_box(points) -> list[float]:
    low = [min(p[a] for p in points) for a in range(3)]
    high = [max(p[a] for p in points) for a in range(3)]
    pad = [0.01 * (h - lo) for lo, h in zip(low, high)]
    return [lo - p for lo, p in zip(low, pad)] + [h + p for h, p in zip(high, pad)]


def _cross(a, b, c) -> tuple[float, float, float]:
    u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def _unit(vector):
    length = math.sqrt(sum(v * v for v in vector))
    return None if length < 1e-12 else tuple(v / length for v in vector)


def _split(points, face) -> list[tuple[int, int, int]]:
    """Triangles for one polygon, which may be concave, keeping its orientation."""
    if len(face) == 3:
        return [tuple(face)]
    # Newell's normal says which way to flatten the polygon.
    normal = [0.0, 0.0, 0.0]
    for i, index in enumerate(face):
        a, b = points[index], points[face[(i + 1) % len(face)]]
        for axis in range(3):
            u, v = (axis + 1) % 3, (axis + 2) % 3
            normal[axis] += (a[u] - b[u]) * (a[v] + b[v])
    axis = max(range(3), key=lambda i: abs(normal[i]))
    u, v = (axis + 1) % 3, (axis + 2) % 3
    flat = [(points[i][u], points[i][v]) for i in face]
    keep_order = normal[axis] > 0          # triangulate() answers counter-clockwise
    out = []
    for a, b, c in meshes.triangulate(flat):
        out.append((face[a], face[b], face[c]) if keep_order
                   else (face[a], face[c], face[b]))
    return out


def prepare(vertices, faces) -> tuple[list, list]:
    """Check a mesh and turn it into points and triangles, counter-clockwise from
    outside. A closed mesh that is inside out is turned the right way."""
    meshes.check_mesh(vertices, faces)
    points = [tuple(float(c) for c in v) for v in vertices]
    triangles = []
    for face in faces:
        if len(set(face)) < 3:
            continue
        if len(face) > 2000:
            raise BryceError("a face may have at most 2000 corners")
        for triangle in _split(points, list(face)):
            if len(set(triangle)) == 3 and _unit(_cross(*(points[i] for i in triangle))):
                triangles.append(triangle)
        if len(triangles) > MAX_TRIANGLES:
            raise BryceError(f"a mesh may have at most {MAX_TRIANGLES} triangles")
    if not triangles:
        raise BryceError("the mesh has no triangle with any area")
    used = sorted({i for t in triangles for i in t})
    renumber = {old: new for new, old in enumerate(used)}
    points = [points[i] for i in used]
    triangles = [tuple(renumber[i] for i in t) for t in triangles]
    edges = {}
    for t in triangles:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edges[a, b] = edges.get((a, b), 0) + 1
    if all(n == 1 and (b, a) in edges for (a, b), n in edges.items()):
        volume = 0.0
        for t in triangles:
            a, b, c = (points[i] for i in t)
            volume += sum(a[i] * (b[(i + 1) % 3] * c[(i + 2) % 3]
                                  - b[(i + 2) % 3] * c[(i + 1) % 3]) for i in range(3))
        if volume < 0:
            triangles = [(a, c, b) for a, b, c in triangles]
    return points, triangles


def smooth_normals(stored, faces_normals, crease: float) -> tuple[list, list]:
    """Normals for stored triangles. At each point, triangles that meet along an
    edge flatter than `crease` degrees share one normal; sharper edges stay hard.
    Returns (normals, three normal numbers per triangle)."""
    limit = math.cos(math.radians(crease)) - 1e-9
    around: dict[int, list[tuple[int, int]]] = {}
    for number, triangle in enumerate(stored):
        for corner, point in enumerate(triangle):
            around.setdefault(point, []).append((number, corner))
    normals, chosen = [], [[0, 0, 0] for _ in stored]
    for point in sorted(around):
        touching = around[point]
        parent = list(range(len(touching)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        by_edge: dict[int, list[int]] = {}
        for i, (number, _) in enumerate(touching):
            for other in stored[number]:
                if other != point:
                    by_edge.setdefault(other, []).append(i)
        for sharing in by_edge.values():
            for i in sharing[1:]:
                a, b = faces_normals[touching[sharing[0]][0]], faces_normals[touching[i][0]]
                if sum(x * y for x, y in zip(a, b)) >= limit:
                    parent[root(i)] = root(sharing[0])
        groups: dict[int, list[tuple[int, int]]] = {}
        for i, item in enumerate(touching):
            groups.setdefault(root(i), []).append(item)
        for members in groups.values():
            for number, corner in members:
                chosen[number][corner] = len(normals)
            normals.append(tuple(sum(faces_normals[n][a] for n, _ in members) / len(members)
                                 for a in range(3)))
    return normals, chosen


def encode(donor: bytes, points, triangles, crease: float = DEFAULT_CREASE) -> bytes:
    """A mesh record with the donor's object header and new geometry. `points` are in
    the object's own space and `triangles` counter-clockwise seen from outside."""
    if len(donor) < DATA or not 0 <= crease <= 180:
        raise BryceError("a mesh needs a mesh record to start from and a crease of 0-180")
    stored = [(a, c, b) for a, b, c in triangles]          # Bryce's winding
    flat = [_unit(_cross(*(points[i] for i in t))) for t in stored]
    normals, chosen = smooth_normals(stored, flat, crease)
    old = decode(donor)
    first_face = DATA + 24 * (len(old[0]) + len(old[1]))
    leftover = donor[first_face + 24:first_face + 36] if old[2] else bytes(12)
    head = bytearray(donor[:DATA])
    struct.pack_into("<5i", head, HEADER, 2, len(points), len(normals), len(stored), 0)
    struct.pack_into("<6d", head, _BOX, *bounding_box(points))
    body = bytearray()
    for vector in (*points, *normals):
        body += struct.pack("<3d", *vector)
    for triangle, numbers, normal in zip(stored, chosen, flat):
        body += (struct.pack("<6i", *triangle, *numbers) + leftover
                 + struct.pack("<3d4i", *normal, *_TAIL))
    return bytes(head) + bytes(body)


def parse_obj(text: str) -> tuple[list, list]:
    """Points and faces from Wavefront OBJ text (geometry only)."""
    vertices, faces = [], []
    for number, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        try:
            if parts[0] == "v":
                vertices.append([float(v) for v in parts[1:4]])
                if len(vertices[-1]) != 3:
                    raise ValueError
            elif parts[0] == "f":
                face = []
                for corner in parts[1:]:
                    index = int(corner.split("/")[0])
                    face.append(index - 1 if index > 0 else len(vertices) + index)
                if len(face) < 3:
                    raise ValueError
                faces.append(face)
        except ValueError:
            raise BryceError(f"OBJ line {number} is not understood: {line[:60]!r}") from None
    if not vertices or not faces:
        raise BryceError("the OBJ file has no points or no faces")
    return vertices, faces
