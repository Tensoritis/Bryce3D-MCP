"""Bryce's own preset libraries: ready-made objects, materials and skies.

They are read from the user's Bryce folder; nothing from them ships with this
project. Each preset has a description record with the same id as the thing it
describes: a material (.mat), a sky and sun (.bsk) or an object list (.obp).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from .br3 import Br3
from .ccmf import CompositeFile
from .errors import BryceError

CATEGORIES = {"objects": (".obp", "objpresetdesc"), "materials": (".mat", "CPresetDescr"),
              "skies": (".bsk", "CPresetDescr")}


class PresetFile(Br3):
    """A preset library read as a source of records. It has no scene of its own."""

    def __init__(self, composite: CompositeFile) -> None:
        self.file = composite
        self._reindex()


@dataclass(frozen=True)
class Preset:
    library: str          # file name
    path: str
    name: str
    description: str
    key: int


def folder(install: str | None) -> str:
    """The Presets folder of a Bryce installation."""
    if install:
        for candidate in (os.path.join(install, "Presets"), install):
            if os.path.isdir(candidate) and any(
                    f.lower().endswith((".obp", ".mat", ".bsk")) for f in os.listdir(candidate)):
                return candidate
    raise BryceError("Bryce's Presets folder was not found. Set BRYCE3D_INSTALL to the folder "
                     "that Bryce 3D is installed in.")


def _text(raw: bytes) -> str:
    return raw.split(b"\0")[0].split(b"\t")[0].decode("latin-1").strip()


def entries(directory: str, category: str) -> list[Preset]:
    if category not in CATEGORIES:
        raise BryceError(f"category must be one of {', '.join(CATEGORIES)}")
    extension, described_by = CATEGORIES[category]
    needs = {"objects": "C3dRootObjList", "materials": "C3dBaseMaterial",
             "skies": "C3dBaseSky"}[category]
    found = []
    for file_name in sorted(os.listdir(directory)):
        if not file_name.lower().endswith(extension):
            continue
        path = os.path.join(directory, file_name)
        library = PresetFile(CompositeFile.load(path))
        for row in library._rows.get(described_by, []):
            key = struct.unpack_from(">I", row)[0]
            if needs not in library._ids.get(key, {}):
                continue                                  # a placeholder entry
            payload = library._payload((described_by, key))
            name = _text(payload[:64]) or f"{os.path.splitext(file_name)[0]} {len(found) + 1}"
            found.append(Preset(file_name, path, name, _text(payload[64:]), key))
    return found


def find(directory: str, category: str, name: str, library: str | None = None) -> Preset:
    available = entries(directory, category)
    if library:
        available = [p for p in available
                     if library.lower() in (p.library.lower(),
                                            os.path.splitext(p.library)[0].lower())]
    wanted = name.strip().lower()
    matches = [p for p in available if p.name.lower() == wanted]
    if not matches:
        close = [p.name for p in available if wanted in p.name.lower()][:12]
        hint = f"; similar: {', '.join(close)}" if close else "; see br3_presets"
        raise BryceError(f"no {category[:-1]} preset named {name!r}{hint}")
    return matches[0]


def load(preset: Preset) -> PresetFile:
    return PresetFile(CompositeFile.load(preset.path))


def donor(directory: str, file_name: str) -> PresetFile | None:
    path = os.path.join(directory, file_name)
    return PresetFile(CompositeFile.load(path)) if os.path.isfile(path) else None
