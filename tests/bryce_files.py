"""Builds small synthetic files with the same structure as Bryce's own."""

import struct

from bryce3d_mcp import heightfield

SIGNATURE = b"CCmF - Universal - synthetic test file"


def composite(payloads: dict[int, bytes], entries: int = 64, split: int | None = None) -> bytes:
    """A CCmF file holding `payloads`; with `split`, chains are cut into pieces."""
    data = bytearray(0x290)
    data[:len(SIGNATURE)] = SIGNATURE
    directory_offset = len(data)
    directory = bytearray(entries * 12)
    data += struct.pack(">III", len(directory), len(directory), 0) + directory
    for number, payload in payloads.items():
        offset = 0
        if payload:
            size = split or len(payload)
            pieces = [payload[i:i + size] for i in range(0, len(payload), size)]
            offset = len(data)
            for position, piece in enumerate(pieces):
                following = len(data) + 12 + len(piece) if position + 1 < len(pieces) else 0
                data += struct.pack(">III", len(piece), len(piece), following) + piece
        struct.pack_into(">4sII", directory, number * 12, b"\1\0\0\0", len(payload), offset)
    data[directory_offset + 12:directory_offset + 12 + len(directory)] = directory
    struct.pack_into(">II", data, 0x24C, len(directory), directory_offset)
    return bytes(data)


def table(record_size: int, records: list[bytes]) -> tuple[bytes, bytes]:
    body = b"".join(r.ljust(record_size, b"\0") for r in records)
    return body, struct.pack(">4I", record_size, 16, 16, len(records)).ljust(32, b"\0")


def terrain_payload(name: str, size: int, heights: list[int], eleven_bits: bool = True) -> bytes:
    header = bytearray(heightfield.HEADER)
    header[8:8 + len(name)] = name.encode()
    struct.pack_into("<I", header, 272, 8 if eleven_bits else 0)
    struct.pack_into("<H", header, 736, size)
    header[800:804] = b"keep"
    return heightfield.encode(bytes(header) + bytes(size * size * 2), heights)


def library(terrains: list[bytes], split: int | None = None) -> bytes:
    payloads, rows, descriptions = {}, [], []
    entry = 24
    for number, payload in enumerate(terrains):
        payloads[entry] = payload
        rows.append(struct.pack(">III", 1, entry, entry + 1))
        payloads[entry + 2] = (f"Preset {number}".encode().ljust(64, b"\0")
                               + b"original text".ljust(257, b"\0"))
        descriptions.append(struct.pack(">III", 1, entry + 2, entry + 3))
        entry += 4
    payloads[18], payloads[19] = table(60, rows)
    payloads[20], payloads[21] = table(60, descriptions)
    classes = [b"C3dHeightField".ljust(16, b"\0") + struct.pack(">II", 18, 19),
               b"objpresetdesc".ljust(16, b"\0") + struct.pack(">II", 20, 21)]
    payloads[16], payloads[17] = table(32, classes)
    return composite(payloads, split=split)
