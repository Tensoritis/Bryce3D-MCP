"""Reader and writer for the "CCmF Composite File" container used by Bryce 3D.

Layout, worked out from Axiom.dll's CCmFile class and checked against the files
that ship with Bryce. Container integers are big-endian.

    0x000  "CCmF - Universal - ..." signature
    0x24C  u32 size of the directory      0x250  u32 offset of its first block
    block  u32 allocated, u32 used, u32 next-block offset (0 = last), payload
    directory  12-byte entries: flags[4], u32 size, u32 offset of first block

Inside it, Bryce keeps typed tables: entry 16 is the list of class names and
entry 17 describes it. Each class record points at a data table and its
descriptor; each of those records points at one object's payload.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import BryceError

SIGNATURE = b"CCmF"
_DIRECTORY_SIZE, _DIRECTORY_OFFSET = 0x24C, 0x250
MAX_FILE = 256 * 1024 * 1024


@dataclass(frozen=True)
class Record:
    """One row of a class table."""
    raw: bytes

    @property
    def data_entry(self) -> int:
        return struct.unpack_from(">I", self.raw, 4)[0]


class CompositeFile:
    def __init__(self, data: bytes) -> None:
        if not data.startswith(SIGNATURE) or len(data) < 0x290:
            raise BryceError("not a Bryce composite (CCmF) file")
        self.data = bytearray(data)

    @classmethod
    def load(cls, path: str) -> "CompositeFile":
        with open(path, "rb") as handle:
            data = handle.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise BryceError("file is too large")
        return cls(data)

    def _u32(self, offset: int) -> int:
        if not 0 <= offset <= len(self.data) - 4:
            raise BryceError("corrupt file: offset outside the file")
        return struct.unpack_from(">I", self.data, offset)[0]

    def _spans(self, offset: int, size: int) -> list[tuple[int, int]]:
        """The (file offset, length) pieces that make up one chained payload."""
        spans, total, seen = [], 0, set()
        while offset:
            if offset in seen:
                raise BryceError("corrupt file: block chain loops")
            seen.add(offset)
            used, following = self._u32(offset + 4), self._u32(offset + 8)
            if offset + 12 + used > len(self.data):
                raise BryceError("corrupt file: block runs past the end")
            spans.append((offset + 12, used))
            total += used
            offset = following
        if total != size:
            raise BryceError("corrupt file: block sizes do not add up")
        return spans

    def _join(self, spans) -> bytes:
        return b"".join(bytes(self.data[a:a + n]) for a, n in spans)

    def _write_spans(self, spans, payload: bytes) -> None:
        done = 0
        for start, length in spans:
            self.data[start:start + length] = payload[done:done + length]
            done += length

    def _directory_spans(self):
        return self._spans(self._u32(_DIRECTORY_OFFSET), self._u32(_DIRECTORY_SIZE))

    def _directory(self) -> bytes:
        return self._join(self._directory_spans())

    def entry_count(self) -> int:
        return len(self._directory()) // 12

    def read(self, entry: int) -> bytes:
        directory = self._directory()
        if not 0 <= entry < len(directory) // 12 or not directory[entry * 12]:
            raise BryceError(f"corrupt file: entry {entry} does not exist")
        _, size, offset = struct.unpack_from(">4sII", directory, entry * 12)
        return self._join(self._spans(offset, size)) if size else b""

    def replace(self, entry: int, payload: bytes) -> None:
        """Overwrite in place when the size is unchanged; otherwise append one new
        block and repoint the directory entry (the old block is left unused)."""
        old_size = len(self.read(entry))
        directory = bytearray(self._directory())
        offset = struct.unpack_from(">I", directory, entry * 12 + 8)[0]
        if len(payload) == old_size:
            if old_size:
                self._write_spans(self._spans(offset, old_size), payload)
            return
        if not payload:
            raise BryceError("cannot write an empty entry")
        new_offset = len(self.data)
        self.data += struct.pack(">III", len(payload), len(payload), 0) + payload
        struct.pack_into(">II", directory, entry * 12 + 4, len(payload), new_offset)
        self._write_spans(self._directory_spans(), bytes(directory))

    def table(self, data_entry: int, descriptor_entry: int) -> list[Record]:
        data, descriptor = self.read(data_entry), self.read(descriptor_entry)
        if len(descriptor) < 16:
            return []
        record_size, _, _, count = struct.unpack_from(">4I", descriptor, 0)
        if not 0 < record_size <= 4096 or count * record_size > len(data):
            raise BryceError("corrupt file: bad table descriptor")
        return [Record(data[i * record_size:(i + 1) * record_size]) for i in range(count)]

    def classes(self) -> dict[str, tuple[int, int]]:
        found = {}
        for record in self.table(16, 17):
            name = record.raw[:16].split(b"\0")[0].decode("latin-1")
            found[name] = struct.unpack_from(">II", record.raw, 16)
        return found

    def records(self, class_name: str) -> list[Record]:
        location = self.classes().get(class_name)
        return self.table(*location) if location else []
