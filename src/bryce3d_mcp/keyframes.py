"""Bryce's own keyframes.

Every setting record ("track") has the same shape: a 148-byte header, that many
keys of equal size, and then the track's time curve if it has one (see curve.py).
The third 32-bit number of the header is the key count and the fifth is the length
of the curve in bytes. A key is

    +0   time in seconds (double; frame / frames per second)
    +8   32-bit 2 (the only value seen)
    +44  the value (+72 in material tracks, whose key repeats the channel's block)
    ...  the rest, copied unchanged from key to key

A record with one key is a plain setting. The layout was read from a scene in which
Bryce itself had saved two position keys on five objects, and from the code that
loads and saves all the other kinds of track.

Between two keys Bryce blends in straight lines; the time curve, which spans the
whole track from its first key to its last, shapes the timing.
"""

from __future__ import annotations

import math
import struct

from . import curve
from .errors import BryceError

HEAD = 148
VALUE = 44                           # offset of the value inside a key
_COUNT, _EXTRA = 8, 16
_SHORTEST_KEY = 48
MAX_KEYS = 1000


def _extra(record: bytes) -> int:
    """The length of the time curve that follows the keys."""
    return struct.unpack_from("<I", record, _EXTRA)[0]


def count(record: bytes) -> int:
    if len(record) < HEAD + _SHORTEST_KEY:
        raise BryceError("corrupt scene: a setting record is too short")
    number, = struct.unpack_from("<I", record, _COUNT)
    room = len(record) - HEAD - _extra(record)
    if room < _SHORTEST_KEY or not 1 <= number <= room // _SHORTEST_KEY or room % number:
        raise BryceError("corrupt scene: a setting record has an impossible key count")
    return number


def stride(record: bytes) -> int:
    return (len(record) - HEAD - _extra(record)) // count(record)


def keys(record: bytes) -> list[bytes]:
    size = stride(record)
    return [record[HEAD + i * size:HEAD + (i + 1) * size] for i in range(count(record))]


def single_size(record: bytes) -> int:
    """How long the record would be with one key and no curve; this tells what it
    holds."""
    return HEAD + stride(record)


def time_curve(record: bytes):
    """The track's curve points, or None."""
    extra = _extra(record)
    count(record)
    return curve.decode(record[len(record) - extra:]) if extra else None


def with_curve(record: bytes, points) -> bytes:
    """`record` with its curve replaced; None removes it."""
    end = HEAD + stride(record) * count(record)
    blob = curve.encode(points)
    out = bytearray(record[:end]) + blob
    struct.pack_into("<I", out, _EXTRA, len(blob))
    return bytes(out)


def time_of(key: bytes) -> float:
    return struct.unpack_from("<d", key)[0]


def values(record: bytes, number: int, form: str = "d",
           offset: int = VALUE) -> list[tuple[float, tuple[float, ...]]]:
    """(time, value) for a track of `number` doubles ("d") or floats ("f")."""
    return [(time_of(key), struct.unpack_from(f"<{number}{form}", key, offset))
            for key in keys(record)]


def build(record: bytes, entries, form: str = "d", offset: int = VALUE) -> bytes:
    """`record` with its keys replaced by `entries`, (time, numbers) in time order.
    Everything but the time and value is copied from the record's first key, and
    the curve, if there is one, is kept while there are two keys or more."""
    if not 1 <= len(entries) <= MAX_KEYS:
        raise BryceError(f"a track holds 1 to {MAX_KEYS} keys")
    first = keys(record)[0]
    end = HEAD + stride(record) * count(record)
    tail = record[end:] if len(entries) > 1 else b""
    out = bytearray(record[:HEAD])
    struct.pack_into("<I", out, _COUNT, len(entries))
    struct.pack_into("<I", out, _EXTRA, len(tail))
    for time, value in entries:
        key = bytearray(first)
        struct.pack_into("<d", key, 0, time)
        struct.pack_into(f"<{len(value)}{form}", key, offset, *value)
        out += key
    return bytes(out) + tail


def fresh(number: int, form: str, offset: int, value) -> bytes:
    """A new one-key track, for a setting that has none."""
    width = offset + number * struct.calcsize(form)
    record = bytearray(HEAD + max(width, _SHORTEST_KEY))
    struct.pack_into("<3I", record, 0, 1, 1, 1)
    struct.pack_into("<I", record, HEAD + 8, 2)
    struct.pack_into(f"<{number}{form}", record, HEAD + offset, *value)
    return bytes(record)


def _slerp(a, b, t: float):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = [-v for v in b], -dot
    if dot > 0.9995:
        mixed = [x + (y - x) * t for x, y in zip(a, b)]
    else:
        angle = math.acos(dot)
        mixed = [(x * math.sin((1 - t) * angle) + y * math.sin(t * angle)) / math.sin(angle)
                 for x, y in zip(a, b)]
    length = math.sqrt(sum(v * v for v in mixed)) or 1.0
    return tuple(v / length for v in mixed)


def at(entries, time: float, rotation: bool = False, points=None):
    """The value at `time`, the way Bryce works it out: the time is looked up in
    the track's curve (`points`), then the two keys around the result are blended
    in a straight line (turns are blended as quaternions)."""
    first, last = entries[0][0], entries[-1][0]
    if time <= first + 1e-6:
        return tuple(entries[0][1])
    if time >= last - 1e-6:
        return tuple(entries[-1][1])
    if points:
        time = first + curve.progress(points, (time - first) / (last - first)) * (last - first)
        if time <= first + 1e-6:
            return tuple(entries[0][1])
    for (t0, v0), (t1, v1) in zip(entries, entries[1:]):
        if time < t1 - 1e-6:
            share = (time - t0) / (t1 - t0)
            if rotation:
                return _slerp(v0, v1, share)
            return tuple(x + (y - x) * share for x, y in zip(v0, v1))
        if time <= t1 + 1e-6:
            return tuple(v1)
    return tuple(entries[-1][1])
