"""Bryce's stored pictures (C3dCImage records).

A picture has two entries. The first is "rle2", 60 zero bytes, then four whole
planes, top row first: alpha, red, green minus red and blue minus red (both
wrapping at 256). Each plane is run-length coded: a byte n below 128 repeats the
next byte n times; 128 + n is followed by n bytes to copy. The second entry is an
80-byte description holding the size. The layout was read from Axiom.dll, Bryce's
graphics library, and checked by decoding Bryce's own interface pictures.
"""

from __future__ import annotations

import struct

from PIL import Image, ImageChops

from .errors import BryceError

MAGIC = b"rle2"
HEADER = 64
MAX_SIDE = 1024
_LONGEST = 126                       # the longest run or copy Bryce writes


def _pack(plane: bytes) -> bytes:
    out = bytearray()
    start = at = 0                   # bytes from start to at still need copying
    end = len(plane)

    def copy(upto):
        for i in range(start, upto, _LONGEST):
            piece = plane[i:min(i + _LONGEST, upto)]
            out.append(0x80 | len(piece))
            out.extend(piece)

    while at < end:
        run = 1
        while run < _LONGEST and at + run < end and plane[at + run] == plane[at]:
            run += 1
        if run >= 3:
            copy(at)
            out.extend((run, plane[at]))
            at += run
            start = at
        else:
            at += run
    copy(end)
    return bytes(out)


def encode(image: Image.Image) -> bytes:
    """The first entry for a picture."""
    red, green, blue, alpha = image.convert("RGBA").split()
    planes = (alpha, red, ImageChops.subtract_modulo(green, red),
              ImageChops.subtract_modulo(blue, red))
    return MAGIC + bytes(HEADER - len(MAGIC)) + b"".join(
        _pack(plane.tobytes()) for plane in planes)


def decode(data: bytes, width: int, height: int) -> Image.Image:
    if not data.startswith(MAGIC) or not 0 < width * height <= 4096 * 4096:
        raise BryceError("not a picture this code can read")
    wanted, out, at = 4 * width * height, bytearray(), HEADER
    while len(out) < wanted:
        if at >= len(data):
            raise BryceError("corrupt picture: data ends early")
        count = data[at]
        if count & 0x80:
            count &= 0x7F
            out += data[at + 1:at + 1 + count]
            at += 1 + count
        else:
            out += data[at + 1:at + 2] * count
            at += 2
    if len(out) != wanted:
        raise BryceError("corrupt picture: data has the wrong size")
    size = (width, height)
    alpha, red, green, blue = (
        Image.frombytes("L", size, bytes(out[i * width * height:(i + 1) * width * height]))
        for i in range(4))
    return Image.merge("RGBA", (red, ImageChops.add_modulo(green, red),
                                ImageChops.add_modulo(blue, red), alpha))


def description(width: int, height: int) -> bytes:
    """The 80-byte second entry."""
    return (b"imgeiv32" + struct.pack(">4I", 4, width, 4 * width, height) + bytes(16)
            + struct.pack(">2I", height, width)).ljust(80, b"\0")


def size_of(described: bytes) -> tuple[int, int]:
    if len(described) < 80 or not described.startswith(b"imgeiv32"):
        raise BryceError("not a picture description this code can read")
    _, width, _, height = struct.unpack_from(">4I", described, 8)
    return width, height


def load(path: str) -> Image.Image:
    """A picture file ready to store: RGBA, no side longer than MAX_SIDE."""
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > 40_000_000:
                raise BryceError("the picture is larger than 40 megapixels")
            image = opened.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise BryceError(f"cannot read the picture {path!r}: {error}") from None
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return image
