"""A fast approximate picture of a .br3 scene, drawn without Bryce.

It lets an agent check layout, framing and colours without running or touching
the program. The camera model and the object transform rule were checked against
Bryce renders (shape outlines agree to within a few pixels). Textures, clouds,
reflections, transparency and object-to-object shadows are not drawn, so judge
the final look from a real Bryce render.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from . import heightfield
from .br3 import UNIT, Br3, camera_matrix, rotation_matrix
from .errors import BryceError

DEFAULT_SIZE = (480, 360)       # Bryce's default document
_NEAR = 1.0
_GREY = (0.62, 0.62, 0.62)
_GROUND = np.array((0.52, 0.53, 0.54))
_SKY_TOP = np.array((0.10, 0.17, 0.72))
_SKY_LOW = np.array((0.72, 0.86, 1.00))


def _grid_faces(rows: int, columns: int, wrap_u=False, wrap_v=False) -> list:
    faces = []
    for r in range(rows if wrap_u else rows - 1):
        for c in range(columns if wrap_v else columns - 1):
            a, b = r * columns + c, r * columns + (c + 1) % columns
            d = ((r + 1) % rows) * columns + c
            e = ((r + 1) % rows) * columns + (c + 1) % columns
            faces += [(a, b, e), (a, e, d)]
    return faces


def _revolve(profile, segments: int = 72):
    """Surface of revolution about Y from (radius, y) pairs."""
    points = [(r * math.cos(t), y, r * math.sin(t)) for r, y in profile
              for t in np.linspace(0, 2 * math.pi, segments, endpoint=False)]
    return np.array(points), _grid_faces(len(profile), segments, wrap_v=True)


def _cube():
    p = np.array([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return p, [f for a, b, c, d in quads for f in ((a, b, c), (a, c, d))]


def _pyramid():
    p = np.array([(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1), (0, 1, 0)], float)
    return p, [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (0, 2, 1), (0, 3, 2)]


def _torus(tube: float = 0.2):
    """Bryce's torus stands upright: its axis is Z."""
    u, v = 72, 28
    points = []
    for a in np.linspace(0, 2 * math.pi, u, endpoint=False):
        for b in np.linspace(0, 2 * math.pi, v, endpoint=False):
            k = 1 - tube + tube * math.cos(b)
            points.append((k * math.cos(a), k * math.sin(a), tube * math.sin(b)))
    return np.array(points), _grid_faces(u, v, wrap_u=True, wrap_v=True)


def _terrain(size: int, heights: list[int], detail: int = 64, mirrored: bool = False):
    grid = np.array(heights, float).reshape(size, size) / 65535
    keep = np.unique(np.linspace(0, size - 1, min(size, detail)).round().astype(int))
    grid = grid[np.ix_(keep, keep)]
    axis = keep / (size - 1) * 2 - 1
    x, z = np.meshgrid(axis, axis)
    n = len(keep)
    faces = _grid_faces(n, n)
    flat = grid.reshape(-1)                      # Bryce does not draw zero height
    faces = [f for f in faces if flat[list(f)].max() > 0]
    if mirrored:                                 # a symmetric lattice: both ways
        top = np.stack([x, grid, z], -1).reshape(-1, 3)
        under = np.stack([x, -grid, z], -1).reshape(-1, 3)
        return (np.concatenate([top, under]),
                faces + [(c + n * n, b + n * n, a + n * n) for a, b, c in faces])
    return np.stack([x, grid - 0.5, z], -1).reshape(-1, 3), faces


_SHAPES = {
    "cube": _cube,
    "sphere": lambda: _revolve([(math.sin(a), -math.cos(a))
                                for a in np.linspace(0, math.pi, 40)]),
    "cylinder": lambda: _revolve([(0, -1), (1, -1), (1, 1), (0, 1)]),
    "cone": lambda: _revolve([(0, -1), (1, -1), (0, 1)]),
    "pyramid": _pyramid,
    "torus": _torus,
    "disk": lambda: _revolve([(0, 0), (1, 0)]),
    "mesh": lambda: _revolve([(math.sin(a), -math.cos(a))
                              for a in np.linspace(0, math.pi, 6)], 7),
}


def _placed(points, transform):
    """Apply an object's (position, origin, scale, rotation) to local points."""
    position, origin, scale, rotation = (np.array(v) for v in transform)
    return ((points + origin) * scale) @ np.array(rotation_matrix(rotation)) + position


def _local_parts(scene: Br3, kind: str, ref) -> list:
    """The surfaces of one object in its parent's coordinates (file units) as
    (vertices, triangles, "#rrggbb" or None, cuts). A group gives one part per
    member; `cuts` marks a boolean negative, which has no surface of its own."""
    if kind == "group":
        transform = scene.transform(ref)
        return [(_placed(points, transform), faces, colour, cuts)
                for member_kind, member in scene._members(ref)
                for points, faces, colour, cuts in _local_parts(scene, member_kind, member)]
    if kind == "mesh":
        try:
            points, faces = scene.mesh_geometry(ref)
            points = np.array(points)
        except BryceError:
            points, faces = _SHAPES[kind]()      # a rough stand-in
    elif kind in ("terrain", "lattice"):
        points, faces = _terrain(*heightfield.decode(scene._payload(ref)),
                                 mirrored=kind == "lattice")
    elif kind in _SHAPES:
        points, faces = _SHAPES[kind]()
    else:
        return []
    return [(_placed(points, scene.transform(ref)), faces,
             scene.material(ref).get("color"), scene.boolean_of(ref) == "negative")]


def object_parts(scene: Br3, kind: str, ref) -> list:
    """_local_parts with the vertices in Bryce units."""
    return [(points * UNIT, faces, colour, cuts)
            for points, faces, colour, cuts in _local_parts(scene, kind, ref)]


_LENS = 1.037


class _View:
    def __init__(self, camera: dict, width: int, height: int) -> None:
        h, p = math.radians(camera["heading"]), math.radians(camera["pitch"])
        self.eye = np.array(camera["position"], float)
        rows = camera_matrix(-p, h, math.radians(camera.get("bank", 0.0)))
        self.right, self.up, self.forward = (np.array(row) for row in rows)
        self.heading = h
        self.width, self.height = width, height
        # The field of view spans about the picture's diagonal. _LENS was measured
        # from a Bryce render at 480x360 (11 points fit to within 1.4 pixels).
        self.focal = (_LENS * math.hypot(width, height) / 2
                      / math.tan(math.radians(camera["fov"]) / 2))

    def to_camera(self, points):
        d = points - self.eye
        return np.stack([d @ self.right, d @ self.up, d @ self.forward], -1)

    def to_pixels(self, cam):
        return np.stack([self.width / 2 + self.focal * cam[..., 0] / cam[..., 2],
                         self.height / 2 - self.focal * cam[..., 1] / cam[..., 2]], -1)

    def rays(self):
        x, y = np.meshgrid(np.arange(self.width) + 0.5 - self.width / 2,
                           self.height / 2 - np.arange(self.height) - 0.5)
        return (self.forward + x[..., None] / self.focal * self.right
                + y[..., None] / self.focal * self.up)

    def light(self, sun: dict) -> np.ndarray:
        """Direction towards the sun. Bryce keeps it relative to the camera."""
        a, h = math.radians(sun.get("azimuth", 45)), math.radians(sun.get("altitude", 40))
        ahead = np.array((math.sin(self.heading), 0.0, math.cos(self.heading)))
        return (math.cos(h) * math.sin(a) * self.right + math.sin(h) * np.array((0, 1.0, 0))
                + math.cos(h) * math.cos(a) * ahead)


def _clip_near(triangle):
    """Clip a camera-space triangle against the near plane."""
    out = []
    for i in range(3):
        a, b = triangle[i], triangle[(i + 1) % 3]
        if a[2] >= _NEAR:
            out.append(a)
        if (a[2] >= _NEAR) != (b[2] >= _NEAR):
            out.append(a + (b - a) * (_NEAR - a[2]) / (b[2] - a[2]))
    return [(out[0], out[i], out[i + 1]) for i in range(1, len(out) - 1)]


def _covered(points, width, height):
    """Pixels inside a screen triangle: (rows, columns, barycentric weights)."""
    low = np.maximum(np.floor(points.min(0)).astype(int), 0)
    high = np.minimum(np.ceil(points.max(0)).astype(int), (width - 1, height - 1))
    if (high < low).any():
        return None
    (x0, y0), (x1, y1), (x2, y2) = points
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(area) < 1e-9:
        return None
    xs, ys = np.meshgrid(np.arange(low[0], high[0] + 1) + 0.5,
                         np.arange(low[1], high[1] + 1) + 0.5)
    w1 = ((xs - x0) * (y2 - y0) - (x2 - x0) * (ys - y0)) / area
    w2 = ((x1 - x0) * (ys - y0) - (xs - x0) * (y1 - y0)) / area
    w0 = 1 - w1 - w2
    inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    if not inside.any():
        return None
    rows, columns = np.nonzero(inside)
    weights = np.stack([w0[inside], w1[inside], w2[inside]], -1)
    return rows + low[1], columns + low[0], weights


def _rgb(text: str | None):
    if not text:
        return np.array(_GREY)
    return np.array([int(text[i:i + 2], 16) / 255 for i in (1, 3, 5)])


def _above(polygon, level: float):
    """The part of a polygon (N x 3) at or above the height `level`."""
    kept = []
    for index, point in enumerate(polygon):
        before = polygon[index - 1]
        if (point[1] >= level) != (before[1] >= level):
            amount = (level - before[1]) / (point[1] - before[1])
            kept.append(before + (point - before) * amount)
        if point[1] >= level:
            kept.append(point)
    return np.array(kept)


def render(scene: Br3, width: int = DEFAULT_SIZE[0], height: int = DEFAULT_SIZE[1]):
    """Returns (picture, report). The report says where each object lands."""
    if not (64 <= width <= 1600 and 48 <= height <= 1200):
        raise ValueError("preview size must be between 64x48 and 1600x1200")
    view = _View(scene.camera(), width, height)
    light = view.light(scene.sun())

    rays = view.rays()
    slope = rays[..., 1] / np.linalg.norm(rays, axis=-1)
    blend = np.clip(1 - np.abs(slope) * 3.2, 0, 1)[..., None] ** 2
    picture = _SKY_TOP * (1 - blend) + _SKY_LOW * blend
    depth = np.full((height, width), np.inf)

    solids, report, floor = [], [], None
    for kind, key in scene._objects():
        info = scene.describe(kind, key)
        if kind == "plane":
            y = info["position"][1]
            if y < 1 and floor is None:
                floor = y
            report.append({"name": info["name"], "kind": kind,
                           "preview": "ground" if y < 1 else "not drawn (raised plane)"})
            continue
        parts = object_parts(scene, kind, key)
        drawn = [part for part in parts if not part[3]]
        if not drawn:
            report.append({"name": info["name"], "kind": kind, "preview": "not drawn"})
            continue
        solids.append((info, drawn, len(parts) - len(drawn)))

    on_floor = np.zeros((height, width), bool)
    if floor is not None:
        down = rays[..., 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            distance = (floor - view.eye[1]) / down
        on_floor = np.isfinite(distance) & (distance > 0)
        far = np.clip(distance[on_floor] / 4000, 0, 1)[:, None]
        shade = 0.45 + 0.55 * max(light[1], 0)
        picture[on_floor] = _GROUND * (0.55 + 0.45 * shade) * (1 - far) + _SKY_LOW * far
        depth[on_floor] = distance[on_floor]       # rays have unit forward length

        # Ground shadows: flatten every solid along the sunlight.
        shadow = np.zeros_like(on_floor)
        if light[1] > 0.02:
            for points, faces in ((p[0], p[1]) for _, parts, _ in solids for p in parts):
                for face in faces:
                    # Only what stands above the ground can shade it.
                    part = _above(points[list(face)], floor + 0.01)
                    if len(part) < 3:
                        continue
                    flat = part - light * ((part[:, 1] - floor) / light[1])[:, None]
                    corners = view.to_camera(flat)
                    for first in range(1, len(corners) - 1):
                        fan = corners[[0, first, first + 1]]
                        for triangle in _clip_near(fan):
                            hit = _covered(view.to_pixels(np.array(triangle)), width, height)
                            if hit:
                                shadow[hit[0], hit[1]] = True
        picture[shadow & on_floor] *= 0.5

    owner = np.full((height, width), -1)
    surfaces = [(index, points, faces, colour)
                for index, (_, parts, _) in enumerate(solids)
                for points, faces, colour, _ in parts]
    for index, points, faces, shade_of in surfaces:
        colour = _rgb(shade_of)
        camera_points = view.to_camera(points)
        for face in faces:
            a, b, c = points[list(face)]
            normal = np.cross(b - a, c - a)
            length = np.linalg.norm(normal)
            if length < 1e-9:
                continue
            normal /= length
            if normal @ (view.eye - a) < 0:        # light the side we can see
                normal = -normal
            tone = np.clip(colour * (0.14 + 0.96 * max(normal @ light, 0)), 0, 1)
            for triangle in _clip_near(camera_points[list(face)]):
                triangle = np.array(triangle)
                hit = _covered(view.to_pixels(triangle), width, height)
                if not hit:
                    continue
                rows, columns, weights = hit
                z = 1 / (weights @ (1 / triangle[:, 2]))
                nearer = z < depth[rows, columns]
                rows, columns = rows[nearer], columns[nearer]
                depth[rows, columns] = z[nearer]
                picture[rows, columns] = tone
                owner[rows, columns] = index

    for index, (info, parts, cutting) in enumerate(solids):
        entry = {"name": info["name"], "kind": info["kind"]}
        if cutting:
            entry["note"] = (f"{cutting} negative part(s) not drawn: the preview does not "
                             "cut shapes, use br3_render to see the boolean")
        cam = view.to_camera(np.concatenate([part[0] for part in parts]))
        ahead = cam[cam[:, 2] >= _NEAR]
        pixels = int((owner == index).sum())
        if len(ahead) == 0:
            entry["preview"] = "behind the camera"
        else:
            screen = view.to_pixels(ahead)
            x0, y0 = screen.min(0)
            x1, y1 = screen.max(0)
            if x1 < 0 or y1 < 0 or x0 > width or y0 > height:
                where = [w for w, miss in (("left", x1 < 0), ("right", x0 > width),
                                           ("above", y1 < 0), ("below", y0 > height)) if miss]
                entry["preview"] = "out of frame: " + " and ".join(where)
            else:
                entry["box"] = [int(max(x0, 0)), int(max(y0, 0)),
                                int(min(x1, width)), int(min(y1, height))]
                entry["preview"] = "visible" if pixels else "hidden behind other objects"
                entry["pixels"] = pixels
                if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
                    entry["cut_off_by_frame"] = True
        if info["kind"] == "mesh":
            entry["note"] = "mesh shown as a rough stand-in"
        report.append(entry)

    image = Image.fromarray((np.clip(picture, 0, 1) * 255).astype(np.uint8), "RGB")
    return image, report


def to_obj(scene: Br3) -> tuple[str, int]:
    """The scene's surfaces as Wavefront OBJ text. Returns (text, objects written)."""
    lines, offset, written = ["# exported from a Bryce 3D scene by bryce3d-mcp"], 1, 0
    for kind, key in scene._objects():
        parts = [] if kind == "plane" else [
            part for part in object_parts(scene, kind, key) if not part[3]]
        if not parts:
            continue
        lines.append("o " + (scene._name(key) or kind).replace(" ", "_"))
        for points, faces, _, _ in parts:
            lines += [f"v {x:.5f} {y:.5f} {z:.5f}" for x, y, z in points]
            lines += ["f " + " ".join(str(i + offset) for i in face) for face in faces]
            offset += len(points)
        written += 1
    return "\n".join(lines) + "\n", written
