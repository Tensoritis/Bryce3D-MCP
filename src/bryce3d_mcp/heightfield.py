"""Bryce 3D terrain (C3dHeightField) payloads.

    8    name, NUL terminated
    268  u32 flags              272  u32 8 = heights use the top 11 bits
    736  u16 grid size N (N x N samples)
    744  "_elr" when run-length encoded    748  u32 length of the encoded data
    900  heights: N*N little-endian u16, or run-length pairs of big-endian
         (u16 count, u16 height) followed by four zero bytes

Everything else in the header is preserved as found.
"""

from __future__ import annotations

import array
import struct
import sys

from .errors import BryceError

HEADER = 900
_NAME = slice(8, 40)
_RLE_MARK = b"_elr"
_MAX_RUN = 0xFFFF
SIZES = (16, 32, 64, 128, 256, 512, 1024)
FULL = 0xFFFF


def _check(payload: bytes) -> int:
    if len(payload) < HEADER:
        raise BryceError("unsupported terrain record: too short")
    size = struct.unpack_from("<H", payload, 736)[0]
    if size not in SIZES:
        raise BryceError(f"unsupported terrain grid size {size}")
    return size


def is_supported(payload: bytes) -> bool:
    """Some Bryce versions wrote a shorter header; those are not handled."""
    try:
        decode(payload)
    except BryceError:
        return False
    return True


def name_of(payload: bytes) -> str:
    return payload[_NAME].split(b"\0")[0].decode("latin-1")


def decode(payload: bytes) -> tuple[int, list[int]]:
    size = _check(payload)
    total, body = size * size, payload[HEADER:]
    if payload[744:748] != _RLE_MARK:
        if len(body) != total * 2:
            raise BryceError("unsupported terrain record: unexpected data length")
        values = array.array("H", body)
        if sys.byteorder == "big":
            values.byteswap()
        return size, values.tolist()
    if struct.unpack_from("<I", payload, 748)[0] != len(body):
        raise BryceError("unsupported terrain record: encoded length mismatch")
    heights: list[int] = []
    offset = 0
    while len(heights) < total:
        if offset + 4 > len(body):
            raise BryceError("corrupt terrain: run-length data ends early")
        count, value = struct.unpack_from(">HH", body, offset)
        if count == 0 or len(heights) + count > total:
            raise BryceError("corrupt terrain: bad run length")
        heights.extend([value] * count)
        offset += 4
    if body[offset:] != bytes(4):
        raise BryceError("unsupported terrain record: unexpected trailing data")
    return size, heights


def _run_length(heights: list[int]) -> bytes:
    out = bytearray()
    start, total = 0, len(heights)
    while start < total:
        end = start + 1
        while end < total and end - start < _MAX_RUN and heights[end] == heights[start]:
            end += 1
        out += struct.pack(">HH", end - start, heights[start])
        start = end
    return bytes(out + bytes(4))


def uses_11_bits(payload: bytes) -> bool:
    return struct.unpack_from("<I", payload, 272)[0] == 8


def encode(payload: bytes, heights: list[int], name: str | None = None) -> bytes:
    """A copy of `payload` carrying new heights (same grid size)."""
    size = _check(payload)
    if len(heights) != size * size:
        raise BryceError(f"need exactly {size}x{size} heights")
    if min(heights) < 0 or max(heights) > FULL:
        raise BryceError("heights must be 0-65535")
    if uses_11_bits(payload):
        heights = [h & 0xFFE0 for h in heights]
    header = bytearray(payload[:HEADER])
    if name is not None:
        header[_NAME] = pack_name(name, 32)
    raw = array.array("H", heights)
    if sys.byteorder == "big":
        raw.byteswap()
    body = raw.tobytes()
    packed = _run_length(heights)
    # Keep whichever is smaller, as the bundled files do.
    if len(packed) < len(body):
        body = packed
        header[744:748] = _RLE_MARK
        struct.pack_into("<I", header, 748, len(body))
    else:
        header[744:752] = bytes(8)
    return bytes(header) + body


def pack_name(name: str, width: int) -> bytes:
    if not 0 < len(name) < min(width, 32) or not name.isascii() or not name.isprintable():
        raise BryceError("name must be 1-31 printable ASCII characters")
    return name.encode().ljust(width, b"\0")
