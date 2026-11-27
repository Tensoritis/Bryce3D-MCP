"""Bryce's time-mapping curve: how it shapes the motion between keys.

How a track gets from key to key depends on what it holds (see keyframes.py).
Separately, Bryce can attach a curve to any track to bend its timing. It maps time to progress: both run from 0
(the track's first key) to 1 (its last), and Bryce looks the current time up in
the curve before it picks the keys to blend. A point is (time, progress, flags);
bit 2 of the flags makes the stretch that starts at the point a curve.

A curved stretch is a cubic Bezier. Bryce places the two handles itself, usually
0.4 of the stretch's time span long. At the near end the handle lies on the
average of the two slopes when the stretch before is curved too, and continues
its line when that one is straight. At the far end it averages whenever there is
a stretch on both sides; for the very first stretch it follows the same rule as
the near end. At an open end the handle aims at the other handle. All of this
was read from Bryce 3D's code, and controls() agrees with that code.

In a file the curve follows the track's keys: a 44-byte header (1, 1, number of
points) and 28 bytes per point, of which Bryce uses the first 12.
"""

from __future__ import annotations

import math
import struct

from .errors import BryceError

HEADER, POINT = 44, 28
CURVED = 0x2
MAX_POINTS = 64
_HANDLE = 0.4

Point = tuple[float, float, int]

# Ready-made shapes, as [time, progress] pairs joined by curves.
SHAPES = {
    "ease": [(0, 0), (0.2, 0.08), (0.5, 0.5), (0.8, 0.92), (1, 1)],
    "ease_in": [(0, 0), (0.3, 0.07), (0.65, 0.4), (1, 1)],
    "ease_out": [(0, 0), (0.35, 0.6), (0.7, 0.93), (1, 1)],
}
NAMES = ("linear", *SHAPES, "hold")


def points(spec) -> list[Point] | None:
    """Points for a shape name, or for [[time, progress, "smooth"|"straight"], ...].
    None means no curve at all, which is steady motion."""
    if spec is None or spec == "linear":
        return None
    if spec == "hold":                       # stay put, then jump at the end
        return [(0.0, 0.0, 0), (0.999, 0.0, 0), (1.0, 1.0, 0)]
    if isinstance(spec, str):
        if spec not in SHAPES:
            raise BryceError(f"curve must be one of {', '.join(NAMES)}, or a list of points")
        return [(float(x), float(y), CURVED) for x, y in SHAPES[spec]]
    if not isinstance(spec, (list, tuple)) or not 2 <= len(spec) <= MAX_POINTS:
        raise BryceError(f"a curve needs 2 to {MAX_POINTS} points")
    out = []
    for item in spec:
        if not isinstance(item, (list, tuple)) or len(item) not in (2, 3):
            raise BryceError('a curve point is [time, progress] or [time, progress, "straight"]')
        x, y = item[0], item[1]
        kind = item[2] if len(item) == 3 else "smooth"
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v) for v in (x, y)):
            raise BryceError("curve times and progress values must be numbers")
        if kind not in ("smooth", "straight"):
            raise BryceError('a curve point is "smooth" or "straight"')
        out.append((float(x), float(y), CURVED if kind == "smooth" else 0))
    times = [p[0] for p in out]
    if times[0] != 0 or times[-1] != 1:
        raise BryceError("a curve must start at time 0 and end at time 1")
    if any(b - a < 1e-4 for a, b in zip(times, times[1:])):
        raise BryceError("curve times must increase")
    if any(not 0 <= p[1] <= 1 for p in out):
        raise BryceError("curve progress must stay between 0 and 1")
    return out


def _unit(x: float, y: float, length: float) -> tuple[float, float]:
    size = math.hypot(x, y) or 1.0
    return x / size * length, y / size * length


def _mean_slope(a, b, c, length: float) -> tuple[float, float]:
    """The direction at b whose slope is the average of the slopes a-b and b-c."""
    px, py, nx, ny = b[0] - a[0], b[1] - a[1], c[0] - b[0], c[1] - b[1]
    return _unit(2 * px * nx, py * nx + ny * px, length)


def controls(pts: list[Point], i: int):
    """The Bezier (P0, P1, P2, P3) of the stretch from point i, or None when
    that stretch is a straight line."""
    if not pts[i][2] & CURVED:
        return None
    before = pts[i - 1] if i > 0 else None
    after = pts[i + 2] if i + 2 < len(pts) else None
    if before is None and after is None:
        return None
    p0, p3 = pts[i][:2], pts[i + 1][:2]
    length = _HANDLE * (p3[0] - p0[0])
    first = second = None
    if before is not None:
        if before[2] & CURVED:
            dx, dy = _mean_slope(before, p0, p3, length)
        else:
            dx, dy = _unit(p0[0] - before[0], p0[1] - before[1], length)
        first = (p0[0] + dx, p0[1] + dy)
    if after is not None:
        if before is not None or pts[i + 1][2] & CURVED:
            dx, dy = _mean_slope(p0, p3, after, length)
        else:
            dx, dy = _unit(after[0] - p3[0], after[1] - p3[1], length)
        second = (p3[0] - dx, p3[1] - dy)
    if first is None:
        # Bryce makes this handle longer when the next stretch is straight.
        reach = length if pts[i + 1][2] & CURVED else p3[0] - p0[0]
        dx, dy = _unit(second[0] - p0[0], second[1] - p0[1], reach)
        first = (p0[0] + dx, p0[1] + dy)
    if second is None:
        dx, dy = _unit(first[0] - p3[0], first[1] - p3[1], length)
        second = (p3[0] + dx, p3[1] + dy)
    return p0, first, second, p3


def _bezier(a, b, c, d, u: float) -> float:
    v = 1 - u
    return v * v * v * a + 3 * v * v * u * b + 3 * v * u * u * c + u * u * u * d


def progress(pts: list[Point] | None, time: float) -> float:
    """How far along the track is (0 to 1) at a fraction of its time."""
    time = min(1.0, max(0.0, time))
    if not pts:
        return time
    if time <= pts[0][0]:
        return pts[0][1]
    if time >= pts[-1][0]:
        return pts[-1][1]
    i = max(k for k in range(len(pts) - 1) if pts[k][0] <= time)
    handles = controls(pts, i)
    (x0, y0, _), (x1, y1, _) = pts[i], pts[i + 1]
    if handles is None:
        return y0 + (time - x0) / (x1 - x0) * (y1 - y0)
    xs, ys = [p[0] for p in handles], [p[1] for p in handles]
    low, high = 0.0, 1.0
    for _ in range(50):                      # time only increases along the Bezier
        middle = (low + high) / 2
        if _bezier(*xs, middle) < time:
            low = middle
        else:
            high = middle
    return _bezier(*ys, (low + high) / 2)


def encode(pts: list[Point] | None) -> bytes:
    if not pts:
        return b""
    out = bytearray(HEADER + POINT * len(pts))
    struct.pack_into("<3I", out, 0, 1, 1, len(pts))
    for index, (x, y, flags) in enumerate(pts):
        struct.pack_into("<ffI", out, HEADER + POINT * index, x, y, flags)
    return bytes(out)


def decode(blob: bytes) -> list[Point] | None:
    """The points of a saved curve, or None when there is none."""
    if len(blob) < HEADER:
        return None
    for order in "<>":
        version, marker, count = struct.unpack_from(order + "3I", blob, 0)
        if version == 1 and marker == (1 if order == "<" else 0):
            if not 2 <= count <= (len(blob) - HEADER) // POINT:
                return None
            return [struct.unpack_from(order + "ffI", blob, HEADER + POINT * k)
                    for k in range(count)]
    return None


def describe(pts: list[Point]):
    """A shape name where the points match one, otherwise the points themselves."""
    def close(other):
        return other is not None and len(other) == len(pts) and all(
            abs(a[0] - b[0]) < 1e-5 and abs(a[1] - b[1]) < 1e-5
            and a[2] & CURVED == b[2] & CURVED for a, b in zip(pts, other))
    for name in NAMES[1:]:
        if close(points(name)):
            return name
    return [[round(x, 4), round(y, 4), "smooth" if f & CURVED else "straight"]
            for x, y, f in pts]
