"""Animation: keyframes and paths turned into one scene per frame.

Bryce's own timeline is not written (its keyframe records have not been decoded).
Instead every frame is an ordinary scene made with the same verified edits as a
still picture; Bryce renders them one by one and the pictures are joined up.
"""

from __future__ import annotations

import bisect
import math

from .errors import BryceError

MAX_FRAMES = 600
EASINGS = ("linear", "smooth", "ease")
# target -> fields a keyframe may set
FIELDS = {
    "camera": ("position", "look_at", "heading", "pitch", "bank", "fov"),
    "object": ("position", "rotation", "scale", "size", "material"),
    "sun": ("azimuth", "altitude", "disc_color", "halo_color"),
    "sky": ("settings",),
}
_PATH_KEYS = ("points", "closed", "start", "end", "follow", "heading_offset")


def _colour_numbers(value):
    if isinstance(value, str):
        text = value.lstrip("#")
        try:
            if len(text) == 6:
                return [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        except ValueError:
            pass
        raise BryceError(f"colour must look like #rrggbb, not {value!r}")
    return value


def _flatten(prefix: str, value, out: dict) -> None:
    """Nested settings become separate channels such as material.color."""
    if isinstance(value, dict):
        for name, inner in value.items():
            _flatten(f"{prefix}.{name}" if prefix else name, inner, out)
        return
    value = _colour_numbers(value)
    numbers = value if isinstance(value, (list, tuple)) else [value]
    if not numbers or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                              and math.isfinite(v) for v in numbers):
        raise BryceError(f"keyframe value for {prefix} must be a number, a list of "
                         "numbers or a #rrggbb colour")
    out[prefix] = [float(v) for v in numbers]


def _spline(p0, p1, p2, p3, t: float):
    """Catmull-Rom: passes through p1 (t=0) and p2 (t=1)."""
    return [0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t * t
                   + (3 * b - a - 3 * c + d) * t ** 3)
            for a, b, c, d in zip(p0, p1, p2, p3)]


def _channel(keys: list[tuple[int, list]], frame: int, easing: str) -> list:
    """The value of one channel at a frame; held before the first and after the last."""
    frames = [f for f, _ in keys]
    if frame <= frames[0]:
        return keys[0][1]
    if frame >= frames[-1]:
        return keys[-1][1]
    after = bisect.bisect_right(frames, frame)
    (f1, v1), (f2, v2) = keys[after - 1], keys[after]
    t = (frame - f1) / (f2 - f1)
    if len(v1) != len(v2):
        raise BryceError("keyframe values for one setting must have the same size")
    if easing == "ease":
        t = t * t * (3 - 2 * t)
    if easing != "smooth":
        return [a + (b - a) * t for a, b in zip(v1, v2)]
    v0 = keys[after - 2][1] if after >= 2 else [2 * a - b for a, b in zip(v1, v2)]
    v3 = keys[after + 1][1] if after + 1 < len(keys) else [2 * b - a for a, b in zip(v1, v2)]
    return _spline(v0, v1, v2, v3, t)


class Path:
    """A smooth curve through points, travelled at a steady speed."""

    def __init__(self, points, closed: bool = False, steps: int = 64) -> None:
        if not isinstance(points, list) or len(points) < 2:
            raise BryceError("a path needs at least two [x, y, z] points")
        try:
            pts = [[float(v) for v in p] for p in points]
        except (TypeError, ValueError):
            raise BryceError("path points are [x, y, z] numbers") from None
        if any(len(p) != 3 or not all(map(math.isfinite, p)) for p in pts):
            raise BryceError("path points are [x, y, z] numbers")
        count = len(pts)
        segments = count if closed else count - 1

        def point(i):
            if closed:
                return pts[i % count]
            if i < 0:
                return [2 * a - b for a, b in zip(pts[0], pts[1])]
            if i >= count:
                return [2 * a - b for a, b in zip(pts[-1], pts[-2])]
            return pts[i]

        self.samples = [pts[0]]
        for s in range(segments):
            corners = [point(s + k) for k in (-1, 0, 1, 2)]
            self.samples += [_spline(*corners, k / steps) for k in range(1, steps + 1)]
        self.lengths = [0.0]
        for a, b in zip(self.samples, self.samples[1:]):
            self.lengths.append(self.lengths[-1] + math.dist(a, b))
        if self.lengths[-1] <= 0:
            raise BryceError("the path has no length")
        self.closed = closed

    def at(self, fraction: float) -> tuple[list[float], float]:
        """(position, direction of travel in degrees: 0 along +z, 90 along +x)."""
        fraction = fraction % 1.0 if self.closed and fraction != 1 else min(1.0, max(0.0, fraction))
        wanted = fraction * self.lengths[-1]
        i = min(max(bisect.bisect_right(self.lengths, wanted), 1), len(self.samples) - 1)
        a, b = self.samples[i - 1], self.samples[i]
        span = self.lengths[i] - self.lengths[i - 1]
        t = (wanted - self.lengths[i - 1]) / span if span else 0.0
        position = [x + (y - x) * t for x, y in zip(a, b)]
        return position, math.degrees(math.atan2(b[0] - a[0], b[2] - a[2]))


def _check_track(number: int, track: dict, frames: int) -> None:
    where = f"track {number}"
    if not isinstance(track, dict) or track.get("target") not in FIELDS:
        raise BryceError(f"{where}: target must be one of {', '.join(FIELDS)}")
    target = track["target"]
    unknown = sorted(set(track) - {"target", "name", "keys", "path", "look_at_object"})
    if unknown:
        raise BryceError(f"{where}: unknown field(s) {', '.join(unknown)}")
    if target == "object" and not track.get("name"):
        raise BryceError(f"{where}: an object track needs the object's name")
    if "path" in track:
        if target not in ("camera", "object"):
            raise BryceError(f"{where}: only the camera and objects follow paths")
        path = track["path"]
        if not isinstance(path, dict) or set(path) - set(_PATH_KEYS) or "points" not in path:
            raise BryceError(f"{where}: path takes {', '.join(_PATH_KEYS)}")
    if "look_at_object" in track and target != "camera":
        raise BryceError(f"{where}: look_at_object is for the camera")
    keys = track.get("keys", [])
    if not keys and "path" not in track and "look_at_object" not in track:
        raise BryceError(f"{where}: give keys or a path")
    for key in keys:
        if not isinstance(key, dict) or not isinstance(key.get("frame"), int) \
                or not 0 <= key["frame"] < frames:
            raise BryceError(f"{where}: each key needs a frame from 0 to {frames - 1}")
        bad = sorted(set(key) - {"frame", *FIELDS[target]})
        if bad:
            raise BryceError(f"{where}: a {target} key cannot set {', '.join(bad)}")


def plan(frames: int, tracks: list[dict], easing: str = "smooth",
         position_of=None) -> list[list[dict]]:
    """The scene edit operations for every frame. `position_of(name)` gives an
    object's starting position, needed when the camera watches an object that has
    no track of its own."""
    if not isinstance(frames, int) or not 2 <= frames <= MAX_FRAMES:
        raise BryceError(f"frames must be 2 to {MAX_FRAMES}")
    if easing not in EASINGS:
        raise BryceError(f"easing must be one of {', '.join(EASINGS)}")
    if not isinstance(tracks, list) or not tracks:
        raise BryceError("give at least one track")
    prepared = []
    for number, track in enumerate(tracks):
        _check_track(number, track, frames)
        channels: dict[str, list] = {}
        for key in sorted(track.get("keys", []), key=lambda k: k["frame"]):
            values: dict = {}
            _flatten("", {k: v for k, v in key.items() if k != "frame"}, values)
            for name, value in values.items():
                series = channels.setdefault(name, [])
                if series and series[-1][0] == key["frame"]:
                    raise BryceError(f"track {number}: two keys at frame {key['frame']}")
                series.append((key["frame"], value))
        path = None
        if "path" in track:
            spec = track["path"]
            path = Path(spec["points"], bool(spec.get("closed", False)))
            if "position" in channels:
                raise BryceError(f"track {number}: give a path or position keys, not both")
        prepared.append((track, channels, path))

    result = []
    for frame in range(frames):
        placed: dict[str, list] = {}
        operations, cameras = [], []
        for track, channels, path in prepared:
            fields: dict = {}
            for name, series in channels.items():
                value = _channel(series, frame, easing)
                parts, into = name.split("."), fields
                for part in parts[:-1]:
                    into = into.setdefault(part, {})
                into[parts[-1]] = _shape(parts, value)
            if path is not None:
                spec = track["path"]
                first, last = spec.get("start", 0), spec.get("end", frames - 1)
                if not 0 <= first < last <= frames - 1:
                    raise BryceError("path start and end must be frames, start first")
                done = min(1.0, max(0.0, (frame - first) / (last - first)))
                if path.closed:              # the loop closes just after the last frame
                    done = min(done * (last - first) / (last - first + 1), 1.0)
                position, direction = path.at(done)
                fields["position"] = position
                if spec.get("follow"):
                    turn = direction + float(spec.get("heading_offset", 0))
                    if track["target"] == "camera":
                        fields["heading"] = turn % 360
                    else:
                        # Object rotation +y turns +x toward +z: opposite to heading.
                        base = fields.get("rotation", [0.0, 0.0, 0.0])
                        fields["rotation"] = [base[0], (-turn + 180) % 360 - 180, base[2]]
            target = track["target"]
            if target == "camera":
                cameras.append((track, fields))
                continue
            if target == "object":
                if "position" in fields:
                    placed[track["name"]] = fields["position"]
                operations.append({"op": "set", "name": track["name"], **fields})
            else:
                operations.append({"op": target, **fields})
        for track, fields in cameras:
            watched = track.get("look_at_object")
            if watched:
                spot = placed.get(watched) or (position_of(watched) if position_of else None)
                if spot is None:
                    raise BryceError(f"look_at_object: no position for {watched!r}")
                fields["look_at"] = spot
            if "look_at" in fields:
                fields.pop("heading", None)
                fields.pop("pitch", None)
            if "heading" in fields:
                fields["heading"] %= 360
            operations.append({"op": "camera", **fields})
        result.append(operations)
    return result


def _shape(parts: list[str], value: list[float]):
    """Back from a list of numbers to what the edit operation expects."""
    name = parts[-1]
    if name.endswith("color"):
        return [min(1.0, max(0.0, v)) for v in value]
    if name == "fov":
        return min(170.0, max(1.0, value[0]))
    if name == "pitch":
        return min(90.0, max(-90.0, value[0]))
    if name == "bank":
        return (value[0] + 180) % 360 - 180 if abs(value[0]) > 180 else value[0]
    if name in ("scale", "size"):
        return [max(0.01, v) for v in value]
    if len(value) == 1:
        return value[0]
    return value
