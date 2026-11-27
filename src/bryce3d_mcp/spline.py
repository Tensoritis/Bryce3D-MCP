"""The curve Bryce draws through points, for keys and for motion paths.

Each stretch between two points is a cubic Bezier curve whose handles come from
the neighbouring points: a Kochanek-Bartels (tension, continuity, bias) spline
with a short handle. The rules below were read from Bryce's segment builder and
checked against positions and bounding boxes that Bryce itself saved:

* between two inner points, with no tension, continuity or bias, each handle is
  `tightness` times the step from the point before to the point after;
* at the two ends of an open curve, the end handle is built from the handle at the
  other end of that stretch instead of from a missing neighbour;
* a curve of only two points puts both handles halfway.

Bryce uses one tightness between position keys and a longer one along path
objects. The handles depend only on the points, never on how far apart in time
the keys are.
"""

from __future__ import annotations

import bisect
import math

from .errors import BryceError

KEY_TIGHTNESS = 0.19
PATH_TIGHTNESS = 0.25
PLAIN = (0.0, 0.0, 0.0)                   # tension, continuity, bias


def _mix(a, b, share: float):
    return [x + (y - x) * share for x, y in zip(a, b)]


def _leaving(before, here, after, tcb, k: float):
    """The handle on the far side of `here`, toward `after`."""
    t, c, b = tcb
    ahead = k * (1 - t) * (1 - c) * (1 - b)
    behind = k * (1 - t) * (1 + c) * (1 + b)
    return [p + ahead * (n - p) + behind * (p - q) for q, p, n in zip(before, here, after)]


def _arriving(before, here, after, tcb, k: float):
    """The handle on the near side of `here`, coming from `before`."""
    t, c, b = tcb
    ahead = k * (1 - t) * (1 + c) * (1 - b)
    behind = k * (1 - t) * (1 - c) * (1 + b)
    return [p - ahead * (n - p) - behind * (p - q) for q, p, n in zip(before, here, after)]


def handles(points, i: int, tightness: float = KEY_TIGHTNESS, tcb=None,
            closed: bool = False):
    """The two Bezier handles of the stretch from point `i` to the next one.
    `tcb` gives (tension, continuity, bias) for each point; a closed curve lists
    each point once and runs from the last back to the first."""
    count = len(points)

    def shape(n):
        return PLAIN if tcb is None else tuple(tcb[n % count][:3])

    start, end = points[i % count], points[(i + 1) % count]
    if closed:
        return (_leaving(points[(i - 1) % count], start, end, shape(i), tightness),
                _arriving(start, end, points[(i + 2) % count], shape(i + 1), tightness))
    if count == 2:
        middle = _mix(start, end, 0.5)
        return middle, list(middle)
    first = second = None
    if i > 0:
        first = _leaving(points[i - 1], start, end, shape(i), tightness)
    if i < count - 2:
        second = _arriving(start, end, points[i + 2], shape(i + 1), tightness)
    # An end of the curve leans on the handle at the other end of its stretch.
    if i == 0:
        tension, continuity, _ = shape(0)
        lean = _mix(end, second, 1 + continuity)
        first = _mix(start, lean, tension + tightness)
    if i == count - 2:
        tension, continuity, _ = shape(count - 1)
        lean = _mix(start, first, 1 + continuity)
        second = _mix(end, lean, tension + tightness)
    return first, second


def bezier(start, first, second, end, u: float):
    v = 1 - u
    return [v * v * v * a + 3 * u * v * v * b + 3 * u * u * v * c + u * u * u * d
            for a, b, c, d in zip(start, first, second, end)]


def between(points, i: int, u: float, tightness: float = KEY_TIGHTNESS, tcb=None,
            closed: bool = False):
    """The point a share `u` of the way through stretch `i`."""
    first, second = handles(points, i, tightness, tcb, closed)
    return bezier(points[i], first, second, points[(i + 1) % len(points)], u)


def sample(points, closed: bool = False, steps: int = 64,
           tightness: float = PATH_TIGHTNESS, tcb=None) -> list[list[float]]:
    """`steps` points on every stretch, plus the starting point."""
    stretches = len(points) if closed else len(points) - 1
    found = [list(map(float, points[0]))]
    for i in range(stretches):
        first, second = handles(points, i, tightness, tcb, closed)
        end = points[(i + 1) % len(points)]
        found += [bezier(points[i], first, second, end, n / steps)
                  for n in range(1, steps + 1)]
    return found


class Route:
    """Bryce's path curve through points, travelled by distance the way Bryce
    moves a follower along a path."""

    def __init__(self, points, closed: bool = False, tcb=None,
                 steps: int = 64) -> None:
        if len(points) < 2:
            raise BryceError("a path needs at least two [x, y, z] points")
        self.closed = closed
        self.samples = sample(points, closed, steps, PATH_TIGHTNESS, tcb)
        self.lengths = [0.0]
        for a, b in zip(self.samples, self.samples[1:]):
            self.lengths.append(self.lengths[-1] + math.dist(a, b))
        if self.lengths[-1] <= 0:
            raise BryceError("the path has no length")

    def at(self, fraction: float) -> tuple[list[float], float]:
        """(position, direction of travel in degrees: 0 along +z, 90 along +x)."""
        if self.closed and fraction != 1:
            fraction %= 1.0
        else:
            fraction = min(1.0, max(0.0, fraction))
        wanted = fraction * self.lengths[-1]
        i = min(max(bisect.bisect_right(self.lengths, wanted), 1), len(self.samples) - 1)
        a, b = self.samples[i - 1], self.samples[i]
        span = self.lengths[i] - self.lengths[i - 1]
        position = _mix(a, b, (wanted - self.lengths[i - 1]) / span if span else 0.0)
        return position, math.degrees(math.atan2(b[0] - a[0], b[2] - a[2]))
