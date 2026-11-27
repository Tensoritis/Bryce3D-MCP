"""Bryce's motion paths, and moving things along them.

A path object (record C3dPath, 700 bytes plus 100 per point) keeps its points
already in their final shape, measured from the object's centre in file units:
in Bryce's tilted and stretched presets the saved points are tilted and stretched.
At 640 are the numbers 1, 2 and the point count, at 652 the bounding box (minimum
then maximum corner, doubles), and from 700 the points: x, y, z as doubles, then
tension, continuity, bias and twist as floats. The remaining bytes of a point are
whatever was in memory when it was saved.

Between its points Bryce draws the curve in spline.py, shaped by the tension,
continuity and bias of each point; following a saved path uses that same curve.
The twist of a point is not used.

Bryce links a follower to a path in a way that is not decoded, so following is done
by writing ordinary position keys along the path, by default one for every frame.
"""

from __future__ import annotations

import math
import struct

from . import spline
from .animation import Path
from .errors import BryceError

PATH_TAG, PATH_CLASS = "path3dob", "C3dPath"
_HEAD, _COUNT, _BOX, _POINTS, _STRIDE = 640, 648, 652, 700, 100
_TCBT = 24                                  # tension, continuity, bias, twist
MAX_POINTS = 500
MAX_KEYS = 600
_CLOSE_ENOUGH = 0.03                        # of the path's size: ends that meet
SHAPE_FIELDS = ("tension", "continuity", "bias")
# How a route's points become a curve: "even" is a loop-free smooth curve through
# them, "bryce" is what Bryce draws through the points of a path object, and
# "bryce_loop" is Bryce's curve closed on itself.
SHAPES = ("even", "bryce", "bryce_loop")


def read_points(payload: bytes) -> list[tuple]:
    """The points of a path record, from its centre, in file units."""
    if len(payload) < _POINTS:
        raise BryceError("corrupt scene: the path record is too short")
    count = struct.unpack_from("<I", payload, _COUNT)[0]
    if not 2 <= count <= (len(payload) - _POINTS) // _STRIDE:
        raise BryceError("corrupt scene: impossible number of path points")
    points = [struct.unpack_from("<3d", payload, _POINTS + _STRIDE * i)
              for i in range(count)]
    if not all(math.isfinite(v) and abs(v) < 1e7 for p in points for v in p):
        raise BryceError("this path cannot be read (it may have been saved on a Mac)")
    return points


def read_shaping(payload: bytes) -> list[tuple]:
    """(tension, continuity, bias) of every point of a path record."""
    return [struct.unpack_from("<3f", payload, _POINTS + _STRIDE * i + _TCBT)
            for i in range(len(read_points(payload)))]


def extent(points) -> list[float]:
    return [max(p[a] for p in points) - min(p[a] for p in points) for a in range(3)]


def split_loop(points) -> tuple[list, bool]:
    """(points, closed). A loop is saved with its last point on top of its first."""
    points = [list(p) for p in points]
    size = max(extent(points)) or 1.0
    if len(points) > 3 and math.dist(points[0], points[-1]) <= _CLOSE_ENOUGH * size:
        return points[:-1], True
    return points, False


def parse_points(points, what: str = "points") -> tuple[list, list | None]:
    """(points, shaping). A point is [x, y, z], or {"at": [x, y, z]} with any of
    tension, continuity and bias from -1 to 1. `shaping` is None when no point
    sets any."""
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        raise BryceError(f"{what}: give 2 to {MAX_POINTS} [x, y, z] points")
    out, shaping, shaped = [], [], False
    for point in points:
        values = [0.0, 0.0, 0.0]
        if isinstance(point, dict):
            unknown = sorted(set(point) - {"at", *SHAPE_FIELDS})
            if unknown or "at" not in point:
                raise BryceError(f"{what}: a shaped point takes at, "
                                 f"{', '.join(SHAPE_FIELDS)}")
            for n, field in enumerate(SHAPE_FIELDS):
                if field in point:
                    value = point[field]
                    if not _is_number(value) or not -1 <= value <= 1:
                        raise BryceError(f"{what}: {field} goes from -1 to 1")
                    values[n], shaped = float(value), True
            point = point["at"]
        if not isinstance(point, (list, tuple)) or len(point) != 3 or not all(
                _is_number(v) and abs(v) < 1e7 for v in point):
            raise BryceError(f"{what}: each point is [x, y, z] numbers")
        out.append([float(v) for v in point])
        shaping.append(tuple(values))
    if max(extent(out)) <= 0:
        raise BryceError(f"{what}: the path has no length")
    return out, shaping if shaped else None


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def smooth(points, closed: bool, per_stretch: int = 8) -> list[list[float]]:
    """Points along the smooth curve through `points`, for drawing a path object."""
    path = Path(points, closed)
    stretches = len(points) if closed else len(points) - 1
    count = min(MAX_POINTS - 1, stretches * per_stretch)
    return [path.at(i / count)[0] for i in range(count + 1)]


def record(donor: bytes, points, shaping=None) -> bytes:
    """A path record holding `points` (from the centre, file units), made from a
    path Bryce saved. Its bounding box covers the curve Bryce will draw, which can
    reach past the points."""
    read_points(donor)                       # refuses an unreadable donor
    blank = bytearray(donor[_POINTS:_POINTS + _STRIDE])
    blank[_TCBT:_TCBT + 16] = bytes(16)
    body = bytearray(donor[:_POINTS])
    struct.pack_into("<I", body, _COUNT, len(points))
    struct.pack_into("<6d", body, _BOX, *_box(spline.sample(points, tcb=shaping, steps=32)))
    for i, point in enumerate(points):
        item = bytearray(blank)
        struct.pack_into("<3d", item, 0, *point)
        if shaping:
            struct.pack_into("<3f", item, _TCBT, *shaping[i])
        body += item
    return bytes(body)


def _box(points) -> list[float]:
    return [*(min(p[a] for p in points) for a in range(3)),
            *(max(p[a] for p in points) for a in range(3))]


def place_route(points, centre, scale: float = 1.0, turn: float = 0.0) -> list:
    """Points measured from a centre, resized, turned about the upright axis
    (the way compass headings turn) and put at `centre`."""
    cos, sin = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    return [[centre[0] + scale * (x * cos + z * sin),
             centre[1] + scale * y,
             centre[2] + scale * (z * cos - x * sin)] for x, y, z in points]


def curve_through(points, closed: bool, shape: str = "even", shaping=None):
    """The curve of a route (see SHAPES), travelled by distance."""
    if shape == "even":
        return Path(points, closed)
    return spline.Route(points, shape == "bryce_loop", shaping)


def reversed_route(points, shaping) -> tuple[list, list | None]:
    """The same curve, travelled the other way: bias changes sides."""
    turned = None if shaping is None else [(t, c, -b) for t, c, b in reversed(shaping)]
    return list(reversed(points)), turned


def travel(points, closed: bool, first: int, last: int, count: int,
           reverse: bool = False, shape: str = "even", shaping=None) -> list[dict]:
    """`count` moments along a path at a steady speed: frame, position, and the
    compass heading and climb (degrees) of the direction of travel."""
    if reverse:
        points, shaping = reversed_route(points, shaping)
    path = curve_through(points, closed, shape, shaping)
    # A saved loop is drawn as an open curve whose ends may not quite meet.
    wraps = closed and math.dist(path.samples[0], path.samples[-1]) < 1e-6 * path.lengths[-1]
    frames = sorted({first + round(i * (last - first) / (count - 1))
                     for i in range(count)})
    step = 0.002
    found, heading = [], None
    for frame in frames:
        done = (frame - first) / (last - first)
        # On a loop, look across the join so that the last frame matches the first.
        behind = path.at((done - step) % 1.0 if wraps else max(0.0, done - step))[0]
        ahead = path.at((done + step) % 1.0 if wraps else min(1.0, done + step))[0]
        dx, dy, dz = (b - a for a, b in zip(behind, ahead))
        flat = math.hypot(dx, dz)
        if flat > 1e-9:
            raw = math.degrees(math.atan2(dx, dz))
            # Carry on from the last heading so that a full circle keeps turning.
            heading = raw if heading is None else heading + (raw - heading + 180) % 360 - 180
        climb = math.degrees(math.atan2(dy, flat)) if flat > 1e-9 or dy else 0.0
        found.append({"frame": frame, "position": [round(v, 4) for v in path.at(done)[0]],
                      "heading": heading or 0.0, "climb": climb})
    return found


class PathObjects:
    """Path objects in a scene, and following paths. Mixed into Br3."""

    def path_route(self, ref) -> tuple[list, bool, list]:
        """A path object's points in Bryce units, whether it is a loop, and the
        shaping of each point. Every saved point is listed (a loop ends where it
        began), so that spline.Route draws what Bryce draws."""
        payload = self._payload(ref)
        points = read_points(payload)
        centre = self.centre_of(ref)
        return ([[(c + v) * self.UNIT for c, v in zip(centre, p)] for p in points],
                split_loop(points)[1], read_shaping(payload))

    def add_path(self, points: list, name: str | None = None, closed: bool = False,
                 smooth_curve: bool = True, donors=()) -> dict:
        """Add a Bryce path object through points given in Bryce units."""
        points, shaping = parse_points(points)
        if shaping and smooth_curve:
            raise BryceError("tension, continuity and bias shape the points you give: "
                             "set smooth to false so that exactly those points are saved")
        if smooth_curve and len(points) > 2:
            points = smooth(points, closed)
        elif closed:
            points = points + [points[0]]
            shaping = shaping and shaping + [shaping[0]]
        if len(points) > MAX_POINTS:
            raise BryceError(f"a path takes at most {MAX_POINTS} points")
        box = _box(spline.sample(points, tcb=shaping, steps=32))
        middle = [(box[a] + box[a + 3]) / 2 for a in range(3)]
        local = [[(v - m) / self.UNIT for v, m in zip(p, middle)] for p in points]
        source, donor = self._path_donor(donors)
        data = record(source._payload(donor), local, shaping)   # fails first
        stem = (name or "Path 1")[:24].strip() or "Path 1"
        if any(self._name(k) == stem for _, k in self._objects()):
            stem = self._unique_name(stem)
        copy = self._copy(source, donor)
        self._append(PATH_TAG, copy)
        self._make_still(copy)
        self._rename(copy, stem)
        kept = self._payload(copy)[:_HEAD]       # the new name and the new links
        self.file.replace(self._entry(copy), kept + data[_HEAD:])
        self._reindex()
        self.place(copy, [m / self.UNIT for m in middle], [1.0] * 3, (0.0, 0.0, 0.0, 1.0))
        return self.describe("path", copy)

    def _path_donor(self, donors):
        """A path Bryce saved that is neither resized nor turned."""
        for source in (self, *donors):
            if source is None:
                continue
            for ref in sorted(r for r in source._all_refs() if r[0] == PATH_CLASS):
                _, _, scale, turn = source._cached_transform(ref)
                if all(abs(s - 1) < 1e-6 for s in scale) and abs(turn[3]) > 1 - 1e-9:
                    try:
                        read_points(source._payload(ref))
                    except BryceError:
                        continue
                    return source, ref
        raise BryceError("no Bryce path to copy: set BRYCE3D_INSTALL so that Paths.obp "
                         "can be read, or use a scene that has a path object")

    def follow(self, route: list, closed: bool, *, name: str | None = None,
               target: str = "object", start: int = 0, end: int | None = None,
               keys: int | None = None, face: bool = False,
               heading_offset: float = 0.0, look_at=None, reverse: bool = False,
               curve=None, duration: float | None = None, library=None,
               shape: str = "even", shaping=None) -> dict:
        """Key an object or the camera along `route` at a steady speed (a time curve
        changes the speed)."""
        if target not in ("object", "camera"):
            raise BryceError("only objects and the camera can follow a path")
        if target == "object" and not name:
            raise BryceError("say which object follows the path (name)")
        if target == "camera" and name:
            raise BryceError("the camera needs no name")
        if look_at is not None and target != "camera":
            raise BryceError("look_at is for the camera; objects can use face")
        if look_at is not None and face:
            raise BryceError("give face or look_at, not both")
        if end is None:
            end = max(self.timeline()["duration_frames"], start + 90)
        for label, value in (("start", start), ("end", end)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise BryceError(f"{label} must be a frame number")
        if end <= start:
            raise BryceError("end must be after start")
        if keys is None:
            # Bryce curves between keys with a short handle, so keys far apart make
            # the speed ripple and cut sharp turns. One per frame follows the route.
            keys = max(2, min(end - start + 1, MAX_KEYS))
        if not isinstance(keys, int) or not 2 <= keys <= MAX_KEYS:
            raise BryceError(f"keys must be 2 to {MAX_KEYS}")
        keys = min(keys, end - start + 1)
        if not math.isfinite(heading_offset):
            raise BryceError("heading_offset must be a number of degrees")
        moments = travel(route, closed, start, end, keys, reverse, shape, shaping)
        base = None
        if target == "object" and face:
            base = self.describe(*self.find(name))["rotation"]
        items = []
        for moment in moments:
            item = {"frame": moment["frame"], "position": moment["position"]}
            turned = moment["heading"] + heading_offset
            if target == "camera":
                if face:
                    item["heading"] = round(turned, 3)
                    item["pitch"] = round(min(89.0, max(-89.0, moment["climb"])), 3)
                if look_at is not None:
                    item["look_at"] = look_at
            elif face:
                # Object rotation +y turns +x toward +z: opposite to a heading.
                item["rotation"] = [base[0], round((-turned + 180) % 360 - 180, 3), base[2]]
            items.append(item)
        result = self.set_keyframes(name, items, duration, library, target=target,
                                    curve="linear" if curve is None else curve)
        length = curve_through(route, closed, shape, shaping).lengths[-1]
        return {"followed": {"target": name or "camera", "keys": len(items),
                             "frames": [start, end], "length": round(length, 1),
                             "closed": closed},
                "timeline": result["timeline"]}
