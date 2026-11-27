"""Bryce's own keyframes.

Every setting record ("track") has the same shape: a 148-byte header, whose third
32-bit number is the key count, then that many keys of equal size. A key is

    +0   time in seconds (double; frame / frames per second)
    +8   32-bit 2 (the only value seen)
    +44  the value
    ...  the rest, copied unchanged from key to key

A record with one key is a plain setting. The layout was read from a scene in which
Bryce itself had saved two position keys on five objects. Rotation and scale tracks
follow the same rule by size, but no Bryce-saved scene with two such keys has been
seen.
"""

from __future__ import annotations

import math
import struct

from .errors import BryceError

HEAD = 148
VALUE = 44                           # offset of the value inside a key
_COUNT = 8
_SHORTEST_KEY = 48
MAX_KEYS = 1000


def count(record: bytes) -> int:
    if len(record) < HEAD + _SHORTEST_KEY:
        raise BryceError("corrupt scene: a setting record is too short")
    number, = struct.unpack_from("<I", record, _COUNT)
    if not 1 <= number <= (len(record) - HEAD) // _SHORTEST_KEY \
            or (len(record) - HEAD) % number:
        raise BryceError("corrupt scene: a setting record has an impossible key count")
    return number


def keys(record: bytes) -> list[bytes]:
    number = count(record)
    stride = (len(record) - HEAD) // number
    return [record[HEAD + i * stride:HEAD + (i + 1) * stride] for i in range(number)]


def single_size(record: bytes) -> int:
    """How long the record would be with one key; this tells what it holds."""
    return HEAD + (len(record) - HEAD) // count(record)


def time_of(key: bytes) -> float:
    return struct.unpack_from("<d", key)[0]


def values(record: bytes, number: int) -> list[tuple[float, tuple[float, ...]]]:
    """(time, value) for a track of `number` doubles."""
    return [(time_of(key), struct.unpack_from(f"<{number}d", key, VALUE))
            for key in keys(record)]


def build(record: bytes, entries) -> bytes:
    """`record` with its keys replaced by `entries`, (time, doubles) in time order.
    Everything but the time and value is copied from the record's first key."""
    if not 1 <= len(entries) <= MAX_KEYS:
        raise BryceError(f"a track holds 1 to {MAX_KEYS} keys")
    first = keys(record)[0]
    out = bytearray(record[:HEAD])
    struct.pack_into("<I", out, _COUNT, len(entries))
    for time, value in entries:
        key = bytearray(first)
        struct.pack_into("<d", key, 0, time)
        struct.pack_into(f"<{len(value)}d", key, VALUE, *value)
        out += key
    return bytes(out)


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


def at(entries, time: float, rotation: bool = False):
    """The value at `time`. Exact on a key and outside the keys. Between two keys
    it is a straight-line estimate; Bryce draws its own curve there."""
    if time <= entries[0][0] + 1e-6:
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
