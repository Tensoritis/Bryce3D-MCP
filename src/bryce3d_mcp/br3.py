"""Read and edit Bryce 3D scene files (.br3) directly.

A scene is a CCmF composite file. Every object is a record in a class table
(C3dCube, C3dSphere, C3dHeightField, ...) that links, by a 32-bit id, to its
transform records (position, rotation, scale, shear, origin) and to a material.
The material links to one record per setting, and C3dRootObjList lists the
objects in the scene. Bryce trusts those setting records and refreshes its own
cached copies when it saves.

Record payloads are little-endian; the container and table rows are big-endian.
"""

from __future__ import annotations

import math
import struct

from . import heightfield
from .ccmf import CompositeFile
from .errors import BryceError

UNIT = 10.24            # Bryce units (as shown in the program) per file unit
PRIMITIVE_SIZE = 2 * UNIT
_ROOT = "C3dRootObjList"
_MATERIAL = "C3dBaseMaterial"
_CAMERA = "C3dBaseCamera"
_VALUE = 192            # most setting records keep their value here
_MATERIAL_LINK = 280
_LINKS = range(276, 976, 4)
_HF_MEMORY = 208        # heights in HF_eHFMem, big-endian
# Copies kept inside the camera and sun records themselves.
_CAMERA_MATRIX, _CAMERA_TRANSLATION = 380, 452    # 3x3 doubles, then 3 doubles
_CAMERA_ANGLES, _CAMERA_FOV = 644, 724            # pitch, (+8) heading; field of view
_SUN_VECTOR = 860

# user-facing kind -> root list tag
KINDS = {
    "plane": "plne3dob", "cube": "cube3dob", "sphere": "sphr3dob", "torus": "toru3dob",
    "cylinder": "cylr3dob", "pyramid": "pyra3dob", "cone": "cone3dob", "disk": "circ3dob",
    "terrain": "hfld3dob", "lattice": "symh3dob", "mesh": "tmsh3dob",
    "radial_light": "sphrlght", "spot_light": "conelght",
    "square_spot_light": "pyrmlght", "parallel_light": "cubelght",
}
_KIND_OF = {tag: kind for kind, tag in KINDS.items()}

MATERIAL_COLOURS = {
    "color": "Mat_eDiffuseC", "ambient_color": "Mat_eAmbientC",
    "specular_color": "Mat_eSpecularC", "transparent_color": "Mat_eTransC",
    "volume_color": "Mat_eVolC", "specular_halo_color": "Mat_eSpecExp",
}
MATERIAL_VALUES = {
    "diffuse": "Mat_eDiffuse", "ambient": "Mat_eAmbient", "specular": "Mat_eSpecular",
    "transparency": "Mat_eTrans",
    "reflection": "Mat_eReflect", "bump": "Mat_eBump", "metallic": "Mat_eMetallic",
    "density": "Mat_eHTDensity", "refraction": "Mat_eRefract",
}


def _tag(raw: bytes) -> str:
    return raw[::-1].decode("latin-1")


def _triple(value, label: str) -> tuple[float, float, float]:
    try:
        out = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        raise BryceError(f"{label} must be three numbers") from None
    if len(out) != 3 or not all(math.isfinite(v) for v in out):
        raise BryceError(f"{label} must be three finite numbers")
    return out


def _colour(value, label: str) -> tuple[float, float, float]:
    """Accepts "#rrggbb" or three numbers 0-1."""
    if isinstance(value, str):
        text = value.lstrip("#")
        if len(text) != 6:
            raise BryceError(f"{label} must look like #rrggbb")
        try:
            return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
        except ValueError:
            raise BryceError(f"{label} must look like #rrggbb") from None
    out = _triple(value, label)
    if not all(0 <= v <= 1 for v in out):
        raise BryceError(f"{label} components must be 0-1")
    return out


def _number(value, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise BryceError(f"{label} must be a number") from None
    if not math.isfinite(out):
        raise BryceError(f"{label} must be finite")
    return out


def quaternion_from_euler(degrees) -> tuple[float, float, float, float]:
    """(x, y, z, w) for rotations applied about X, then Y, then Z."""
    x, y, z = (math.radians(a) / 2 for a in _triple(degrees, "rotation"))
    cx, sx, cy, sy, cz, sz = (math.cos(x), math.sin(x), math.cos(y), math.sin(y),
                              math.cos(z), math.sin(z))
    return (sx * cy * cz - cx * sy * sz, cx * sy * cz + sx * cy * sz,
            cx * cy * sz - sx * sy * cz, cx * cy * cz + sx * sy * sz)


def rotation_matrix(q) -> list[list[float]]:
    """Bryce's matrix for a quaternion. It multiplies row vectors: v' = v M."""
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def world_centre(position, origin, scale, rotation) -> list[float]:
    """Where Bryce draws an object, in file units: (origin * scale) rotation + position.

    "Position" is a plain world translation. "Origin" is an offset inside the
    object's own space, so scale and rotation both apply to it."""
    local = [o * s for o, s in zip(origin, scale)]
    matrix = rotation_matrix(rotation)
    return [position[j] + sum(local[i] * matrix[i][j] for i in range(3)) for j in range(3)]


def camera_matrix(pitch: float, heading: float) -> list[list[float]]:
    """The camera record's cached matrix, from its stored angles in radians."""
    cp, sp, cy, sy = math.cos(pitch), math.sin(pitch), math.cos(heading), math.sin(heading)
    return [[cy, 0.0, -sy], [sp * sy, cp, sp * cy], [cp * sy, -sp, cp * cy]]


def euler_from_quaternion(q) -> tuple[float, float, float]:
    x, y, z, w = q
    rx = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    ry = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    rz = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return tuple(math.degrees(a) for a in (rx, ry, rz))


class Br3:
    def __init__(self, composite: CompositeFile) -> None:
        self.file = composite
        self._reindex()
        if not self._rows.get(_ROOT):
            raise BryceError("this composite file is not a Bryce scene (no object list)")

    @classmethod
    def load(cls, path: str) -> "Br3":
        return cls(CompositeFile.load(path))

    @classmethod
    def from_bytes(cls, data: bytes) -> "Br3":
        return cls(CompositeFile(data))

    @property
    def data(self) -> bytes:
        return bytes(self.file.data)

    # ------------------------------------------------------------- records
    def _reindex(self) -> None:
        self._classes = self.file.classes()
        self._rows = {name: [r.raw for r in self.file.table(*where)]
                      for name, where in self._classes.items()}
        self._ids = {}
        for name, rows in self._rows.items():
            for row in rows:
                self._ids[struct.unpack_from(">I", row)[0]] = (name, row)

    def _lookup(self, key: int) -> tuple[str, bytes]:
        try:
            return self._ids[key]
        except KeyError:
            raise BryceError(f"corrupt scene: missing record {key:#x}") from None

    def _payload(self, key: int) -> bytes:
        return self.file.read(struct.unpack_from(">I", self._lookup(key)[1], 4)[0])

    def _write(self, key: int, offset: int, data: bytes) -> None:
        entry = struct.unpack_from(">I", self._lookup(key)[1], 4)[0]
        payload = bytearray(self.file.read(entry))
        if offset + len(data) > len(payload):
            raise BryceError("corrupt scene: record is too short")
        payload[offset:offset + len(data)] = data
        self.file.replace(entry, bytes(payload))

    def _links(self, key: int) -> list[tuple[int, int]]:
        """(offset, id) for every record this one points at."""
        name = self._lookup(key)[0]
        if not name.startswith("C3d") or name in (_ROOT, "C3dTexture"):
            return []
        payload = self._payload(key)
        found = []
        for offset in _LINKS:
            if offset + 4 > len(payload):
                break
            value = struct.unpack_from("<I", payload, offset)[0]
            if value > 0xFFFF and value != key and value in self._ids:
                found.append((offset, value))
        return found

    def _linked(self, key: int, class_name: str) -> int | None:
        for _, other in self._links(key):
            if self._ids[other][0] == class_name:
                return other
        return None

    def _closure(self, key: int) -> list[int]:
        """The record and everything it depends on, dependencies first."""
        order, seen = [], set()

        def visit(k):
            if k in seen:
                return
            seen.add(k)
            for _, other in self._links(k):
                visit(other)
            order.append(k)

        visit(key)
        return order

    def _first(self, class_name: str) -> int | None:
        rows = self._rows.get(class_name)
        return struct.unpack_from(">I", rows[0])[0] if rows else None

    # ------------------------------------------------------------- objects
    def _root(self) -> tuple[int, bytes, list[tuple[str, int, bytes]]]:
        key = self._first(_ROOT)
        payload = self._payload(key)
        count = struct.unpack_from("<I", payload, 8)[0]
        if 12 + 16 * count > len(payload):
            raise BryceError("corrupt scene: object list is too short")
        items = []
        for i in range(count):
            item = payload[12 + 16 * i:28 + 16 * i]
            items.append((_tag(item[:8]), struct.unpack_from("<I", item, 8)[0], item))
        return key, payload, items

    def _write_root(self, items: list[bytes]) -> None:
        key, payload, _ = self._root()
        header = bytearray(payload[:12])
        struct.pack_into("<I", header, 8, len(items))
        entry = struct.unpack_from(">I", self._lookup(key)[1], 4)[0]
        self.file.replace(entry, bytes(header) + b"".join(items))

    def _objects(self) -> list[tuple[str, int]]:
        return [(_KIND_OF.get(tag, tag), key) for tag, key, _ in self._root()[2]
                if tag != "basecamr"]

    def _name(self, key: int) -> str:
        return self._payload(key)[8:48].split(b"\0")[0].decode("latin-1")

    def find(self, name: str) -> tuple[str, int]:
        matches = [(kind, key) for kind, key in self._objects() if self._name(key) == name]
        if not matches:
            names = ", ".join(self._name(k) for _, k in self._objects())
            raise BryceError(f"no object named {name!r}; the scene has: {names}")
        if len(matches) > 1:
            raise BryceError(f"{len(matches)} objects are named {name!r}; rename one first")
        return matches[0]

    def _vector(self, key: int, class_name: str, count: int = 3):
        track = self._linked(key, class_name)
        if track is None:
            return None
        return struct.unpack_from(f"<{count}d", self._payload(track), _VALUE)

    def _set_vector(self, key: int, class_name: str, values) -> None:
        track = self._linked(key, class_name)
        if track is None:
            raise BryceError(f"this object has no {class_name} record")
        self._write(track, _VALUE, struct.pack(f"<{len(values)}d", *values))

    def _setting(self, track: int):
        """Decode a one-value setting record by its size."""
        payload = self._payload(track)
        size = len(payload)
        offset = 220 if size in (232, 248) else _VALUE
        if size in (248, 204):
            return offset, "colour", struct.unpack_from("<3f", payload, offset)
        if size in (232, 196):
            return offset, "float", struct.unpack_from("<f", payload, offset)[0]
        if size == 200:
            return offset, "double", struct.unpack_from("<d", payload, offset)[0]
        if size >= _VALUE + 24 and self._lookup(track)[0].endswith("Vctr"):
            return offset, "vector", struct.unpack_from("<3d", payload, offset)
        return offset, None, None

    def _set_setting(self, track: int, value, label: str) -> None:
        offset, form, _ = self._setting(track)
        if form == "colour":
            self._write(track, offset, struct.pack("<3f", *_colour(value, label)))
        elif form == "float":
            self._write(track, offset, struct.pack("<f", _number(value, label)))
        elif form == "double":
            self._write(track, offset, struct.pack("<d", _number(value, label)))
        elif form == "vector":
            self._write(track, offset, struct.pack("<3d", *_triple(value, label)))
        else:
            raise BryceError(f"{label} has a record layout that is not supported")

    def material(self, key: int) -> dict:
        material = self._linked(key, _MATERIAL)
        if material is None:
            return {}
        out = {}
        tracks = {self._ids[k][0]: k for _, k in self._links(material)}
        for table in (MATERIAL_COLOURS, MATERIAL_VALUES):
            for label, class_name in table.items():
                if class_name in tracks:
                    _, form, value = self._setting(tracks[class_name])
                    if form == "colour":
                        out[label] = "#" + "".join(
                            f"{round(max(0.0, min(1.0, v)) * 255):02x}" for v in value)
                    elif form:
                        out[label] = round(value, 4)
        return out

    def describe(self, kind: str, key: int) -> dict:
        position = self._vector(key, "Obj_ePosition")
        origin = self._vector(key, "Obj_eOrigin")
        scale = self._vector(key, "Obj_eScale")
        rotation = self._vector(key, "Obj_eOrient", 4)
        info = {"name": self._name(key), "kind": kind}
        if position:
            centre = world_centre(position, origin or (0, 0, 0), scale or (1, 1, 1),
                                  rotation or (0, 0, 0, 1))
            info["position"] = [round(v * UNIT, 3) + 0.0 for v in centre]
        if scale:
            info["scale"] = [round(v, 4) for v in scale]
            info["size"] = [round(v * PRIMITIVE_SIZE, 2) for v in scale]
        if rotation:
            info["rotation"] = [round(v, 2) for v in euler_from_quaternion(rotation)]
        material = self.material(key)
        if material:
            info["material"] = material
        return info

    def objects(self) -> list[dict]:
        return [self.describe(kind, key) for kind, key in self._objects()]

    def set_object(self, name: str, *, new_name: str | None = None, position=None,
                   scale=None, size=None, rotation=None, material: dict | None = None) -> dict:
        kind, key = self.find(name)
        if scale is not None and size is not None:
            raise BryceError("give scale or size, not both")
        if size is not None:
            scale = [v / PRIMITIVE_SIZE for v in _triple(size, "size")]
        if scale is not None:
            scale = _triple(scale, "scale")
            if min(scale) <= 0:
                raise BryceError("scale and size must be positive")
        turned = None if rotation is None else quaternion_from_euler(rotation)
        wanted = None if position is None else [v / UNIT for v in _triple(position, "position")]
        if wanted is not None or scale is not None or turned is not None:
            # Keep the centre where it is (or move it where asked) and fold any
            # local offset away, so scale and rotation act about that centre.
            origin = self._vector(key, "Obj_eOrigin")
            if wanted is None:
                wanted = world_centre(
                    self._vector(key, "Obj_ePosition"), origin,
                    self._vector(key, "Obj_eScale") or (1, 1, 1),
                    self._vector(key, "Obj_eOrient", 4) or (0, 0, 0, 1))
            self._set_vector(key, "Obj_ePosition", wanted)
            self._set_vector(key, "Obj_eOrigin", (0.0, 0.0, 0.0))
            if scale is not None:
                self._set_vector(key, "Obj_eScale", scale)
            if turned is not None:
                self._set_vector(key, "Obj_eOrient", turned)
        if material:
            self.set_material(name, material)
        if new_name is not None:
            self._rename(key, new_name)
        return self.describe(kind, key)

    def _rename(self, key: int, new_name: str) -> None:
        if any(self._name(k) == new_name for _, k in self._objects() if k != key):
            raise BryceError(f"an object named {new_name!r} already exists")
        packed = heightfield.pack_name(new_name, 32)
        self._write(key, 8, packed)
        class_name = self._lookup(key)[0]
        rows = self._rows[class_name]
        for i, row in enumerate(rows):
            if struct.unpack_from(">I", row)[0] == key:
                rows[i] = row[:12] + packed + row[12 + len(packed):]
        self.file.set_table(*self._classes[class_name], rows)
        self._reindex()

    def set_material(self, name: str, settings: dict, library: "Br3 | None" = None) -> dict:
        known = {**MATERIAL_COLOURS, **MATERIAL_VALUES}
        unknown = sorted(set(settings) - set(known))
        if unknown:
            raise BryceError(f"unknown material settings {unknown}; use {sorted(known)}")
        _, key = self.find(name)
        material = self._linked(key, _MATERIAL)
        tracks = {self._ids[k][0]: k for _, k in self._links(material)} if material else {}
        if any(known[label] not in tracks for label in settings):
            # Textured default materials have no editable setting records:
            # give the object its own copy of a plain material instead.
            source, donor = self._plain_material([known[label] for label in settings], library)
            material = self._copy(source, donor)
            self._write(key, _MATERIAL_LINK, struct.pack("<I", material))
            tracks = {self._ids[k][0]: k for _, k in self._links(material)}
        for label, value in settings.items():
            self._set_setting(tracks[known[label]], value, label)
        return self.material(key)

    def _plain_material(self, needed: list[str], library):
        for source in (self, library):
            if source is None:
                continue
            for row in source._rows.get(_MATERIAL, []):
                key = struct.unpack_from(">I", row)[0]
                if set(needed) <= {source._ids[k][0] for _, k in source._links(key)}:
                    return source, key
        raise BryceError("no editable material is available in the scene or the template file")

    # ------------------------------------------------------- copy and delete
    def _new_key(self) -> int:
        key = (max(k for k in self._ids if k < 0x7F000000) + 0x40) & ~7
        while key in self._ids:
            key += 8
        return key

    def _copy(self, source: "Br3", key: int) -> int:
        """Copy a record and its dependencies from `source`; returns the new id."""
        order = source._closure(key)
        # Read everything first: `source` may be this very scene.
        pending = []
        for old in order:
            class_name, row = source._lookup(old)
            names = list(source._classes)
            pending.append((old, class_name, row, source._payload(old),
                            source._links(old), names[:names.index(class_name)]))
        mapping: dict[int, int] = {}
        for old, class_name, row, payload, links, earlier in pending:
            where = self.file.add_class(class_name, len(row), after=earlier)
            self._classes = self.file.classes()
            self._rows.setdefault(class_name, [])
            new = self._new_key()
            body = bytearray(payload)
            for offset, target in links:
                struct.pack_into("<I", body, offset, mapping[target])
            entry = self.file.add_entry(bytes(body))
            companion = self.file.add_entry()
            fresh = struct.pack(">III", new, entry, companion) + row[12:]
            self._rows[class_name].append(fresh)
            self.file.set_table(*where, self._rows[class_name])
            self._ids[new] = (class_name, fresh)
            mapping[old] = new
        self._reindex()
        return mapping[key]

    def _unique_name(self, base: str) -> str:
        taken = {self._name(k) for _, k in self._objects()}
        stem = base.rstrip("0123456789").rstrip() or base
        number = 1
        while f"{stem} {number}" in taken:
            number += 1
        return f"{stem} {number}"[:31]

    def add_object(self, kind: str, *, name: str | None = None, library: "Br3 | None" = None,
                   template: str | None = None, **settings) -> dict:
        """Add an object by copying one of the same kind from `library` (preferred)
        or from this scene. `template` picks a particular source object by name."""
        if kind not in KINDS:
            raise BryceError(f"unknown kind {kind!r}; use one of {', '.join(KINDS)}")
        for source in (library, self):
            if source is None:
                continue
            matches = [k for found, k in source._objects()
                       if found == kind and (template is None or source._name(k) == template)]
            if matches:
                break
        else:
            raise BryceError(
                f"no {kind} to copy. Create one in Bryce, save the scene, and use it as the "
                "template file.")
        original = source._name(matches[0])
        final = name if name is not None else self._unique_name(original)
        if any(self._name(k) == final for _, k in self._objects()):
            raise BryceError(f"an object named {final!r} already exists")
        heightfield.pack_name(final, 32)
        if settings.get("position") is None:
            settings["position"] = self._resting_position(source, matches[0], settings)
        new = self._copy(source, matches[0])
        tag = KINDS[kind]
        item = tag.encode()[::-1] + struct.pack("<II", new, 0)
        self._write_root([raw for _, _, raw in self._root()[2]] + [item])
        # The copy still carries the original's name until it is renamed.
        self._rename(new, final)
        material = settings.pop("material", None)
        if material:
            self.set_material(final, material, library)
        return self.set_object(final, **settings)

    def _resting_position(self, source: "Br3", key: int, settings: dict):
        """When a resized solid has no position, keep it standing on the ground."""
        info = source.describe("", key)
        old = source._vector(key, "Obj_eScale")
        position = info.get("position")
        if position is None or old is None or abs(position[1] - old[1] * UNIT) > 0.05:
            return None
        if settings.get("scale") is not None:
            new = _triple(settings["scale"], "scale")[1]
        elif settings.get("size") is not None:
            new = _triple(settings["size"], "size")[1] / PRIMITIVE_SIZE
        else:
            return None
        return [position[0], new * UNIT, position[2]]

    def remove_all_except(self, keep: set[str]) -> int:
        """Delete every object whose name is not in `keep`. Returns how many went."""
        doomed = [self._name(k) for _, k in self._objects() if self._name(k) not in keep]
        for name in doomed:
            self.delete(name)
        return len(doomed)

    def duplicate(self, name: str, new_name: str | None = None, **settings) -> dict:
        kind, _ = self.find(name)
        return self.add_object(kind, template=name, name=new_name, **settings)

    def delete(self, name: str) -> None:
        _, key = self.find(name)
        doomed = set(self._closure(key))
        self._write_root([raw for _, k, raw in self._root()[2] if k != key])
        # Keep anything another record still points at (shared textures).
        while True:
            needed = {other for k in self._ids if k not in doomed
                      for _, other in self._links(k)}
            keep = doomed & needed
            if not keep:
                break
            doomed -= keep
        touched = {self._ids[k][0] for k in doomed}
        for class_name in touched:
            rows = [r for r in self._rows[class_name]
                    if struct.unpack_from(">I", r)[0] not in doomed]
            self.file.set_table(*self._classes[class_name], rows)
        self._reindex()

    # ------------------------------------------------------------- terrain
    def _terrain(self, name: str) -> int:
        kind, key = self.find(name)
        if kind not in ("terrain", "lattice"):
            raise BryceError(f"{name!r} is a {kind}, not a terrain")
        return key

    def terrain_heights(self, name: str) -> tuple[int, list[int]]:
        return heightfield.decode(self._payload(self._terrain(name)))

    def set_terrain_heights(self, name: str, heights: list[int]) -> None:
        """Write new heights to both places Bryce keeps them."""
        key = self._terrain(name)
        updated = heightfield.encode(self._payload(key), heights)
        memory = self._linked(key, "HF_eHFMem")
        stored = heightfield.decode(updated)[1]
        if memory is not None:
            self._write(memory, _HF_MEMORY, struct.pack(f">{len(stored)}H", *stored))
        entry = struct.unpack_from(">I", self._lookup(key)[1], 4)[0]
        self.file.replace(entry, updated)

    def sync_terrain_copy(self, entry: int) -> None:
        """After a terrain record was rewritten directly, refresh its second copy."""
        for kind, key in self._objects():
            if (kind in ("terrain", "lattice")
                    and struct.unpack_from(">I", self._lookup(key)[1], 4)[0] == entry):
                self.set_terrain_heights(self._name(key),
                                         heightfield.decode(self._payload(key))[1])

    # ------------------------------------------------- sky, sun and camera
    def _scene_settings(self, class_name: str, prefix: str) -> dict[str, int]:
        owner = self._first(class_name)
        if owner is None:
            return {}
        return {self._ids[k][0][len(prefix):]: k for _, k in self._links(owner)
                if self._ids[k][0].startswith(prefix)}

    def _read_settings(self, tracks: dict[str, int]) -> dict:
        out = {}
        for label, track in sorted(tracks.items()):
            _, form, value = self._setting(track)
            if form in ("colour", "vector"):
                out[label] = [round(v, 4) for v in value]
            elif form:
                out[label] = round(value, 4)
        return out

    def sky(self) -> dict:
        return self._read_settings(self._scene_settings("C3dBaseSky", "Sky_e"))

    def set_sky(self, settings: dict) -> dict:
        tracks = self._scene_settings("C3dBaseSky", "Sky_e")
        unknown = sorted(set(settings) - set(tracks))
        if unknown:
            raise BryceError(f"unknown sky settings {unknown}; use {sorted(tracks)}")
        for label, value in settings.items():
            self._set_setting(tracks[label], value, label)
        return self.sky()

    def sun(self) -> dict:
        tracks = self._scene_settings("C3dSunLight", "SunL_e")
        out = {}
        if "SunVctr" in tracks:
            x, y, z = self._setting(tracks["SunVctr"])[2]
            length = math.sqrt(x * x + y * y + z * z) or 1.0
            out["azimuth"] = round(math.degrees(math.atan2(x, z)) % 360, 2)
            out["altitude"] = round(math.degrees(math.asin(max(-1.0, min(1.0, y / length)))), 2)
        values = self._read_settings(tracks)
        out.update({"disc_color": values.get("DiscClr"), "halo_color": values.get("HaloClr")})
        return out

    def set_sun(self, azimuth=None, altitude=None, disc_color=None, halo_color=None) -> dict:
        tracks = self._scene_settings("C3dSunLight", "SunL_e")
        if azimuth is not None or altitude is not None:
            current = self.sun()
            a = math.radians(_number(current["azimuth"] if azimuth is None else azimuth,
                                     "azimuth"))
            h = math.radians(_number(current["altitude"] if altitude is None else altitude,
                                     "altitude"))
            vector = (math.cos(h) * math.sin(a), math.sin(h), math.cos(h) * math.cos(a))
            self._set_setting(tracks["SunVctr"], vector, "sun direction")
            self._write(self._first("C3dSunLight"), _SUN_VECTOR, struct.pack("<3d", *vector))
        for label, value in (("DiscClr", disc_color), ("HaloClr", halo_color)):
            if value is not None:
                self._set_setting(tracks[label], value, label)
        return self.sun()

    # The camera stores x and z negated and its heading turned by half a circle
    # relative to object coordinates (checked against real renders). When a scene
    # is opened Bryce draws from the copies inside the camera record, not from the
    # setting records, so both are written.
    def camera(self) -> dict:
        key = self._first(_CAMERA)
        position = self._vector(key, "Obj_ePosition")
        origin = self._vector(key, "Obj_eOrigin") or (0.0, 0.0, 0.0)
        x, y, z = ((p + o) * UNIT for p, o in zip(position, origin))
        orient = self._vector(key, "Cam_eCraOrient")
        fov = self._vector(key, "Cam_eFOV", 1)
        return {"position": [round(-x, 3), round(y, 3), round(-z, 3)],
                "heading": round((math.degrees(orient[1]) + 180) % 360, 2),
                "pitch": round(math.degrees(orient[0]), 2),
                "bank": round(math.degrees(self._vector(key, "Cam_eBank", 1)[0]), 2)
                if self._linked(key, "Cam_eBank") else 0.0,
                "fov": round(math.degrees(fov[0]), 2)}

    def set_camera(self, position=None, heading=None, pitch=None, fov=None,
                   look_at=None) -> dict:
        key = self._first(_CAMERA)
        current = self.camera()
        if look_at is not None:
            if heading is not None or pitch is not None:
                raise BryceError("give look_at or heading/pitch, not both")
            eye = _triple(current["position"] if position is None else position, "position")
            dx, dy, dz = (t - e for t, e in zip(_triple(look_at, "look_at"), eye))
            if not (dx or dz):
                raise BryceError("look_at must not be straight above or below the camera")
            heading = math.degrees(math.atan2(dx, dz))
            pitch = math.degrees(math.atan2(dy, math.hypot(dx, dz)))
        if position is not None:
            x, y, z = _triple(position, "position")
            origin = self._vector(key, "Obj_eOrigin") or (0.0, 0.0, 0.0)
            stored = (-x / UNIT, y / UNIT, -z / UNIT)
            self._set_vector(key, "Obj_ePosition", [s - o for s, o in zip(stored, origin)])
        if heading is not None or pitch is not None:
            orient = list(self._vector(key, "Cam_eCraOrient"))
            if heading is not None:
                orient[1] = math.radians((_number(heading, "heading") - 180) % 360)
            if pitch is not None:
                orient[0] = math.radians(_number(pitch, "pitch"))
            self._set_vector(key, "Cam_eCraOrient", orient)
        if fov is not None:
            degrees = _number(fov, "fov")
            if not 1 <= degrees <= 170:
                raise BryceError("fov must be 1-170 degrees")
            self._set_vector(key, "Cam_eFOV", [math.radians(degrees)])
        self._sync_camera(key)
        return self.camera()

    def _sync_camera(self, key: int) -> None:
        orient = self._vector(key, "Cam_eCraOrient")
        position = self._vector(key, "Obj_ePosition")
        origin = self._vector(key, "Obj_eOrigin") or (0.0, 0.0, 0.0)
        matrix = camera_matrix(orient[0], orient[1])
        self._write(key, _CAMERA_MATRIX,
                    struct.pack("<9d", *[v for row in matrix for v in row]))
        self._write(key, _CAMERA_TRANSLATION,
                    struct.pack("<3d", *[p + o for p, o in zip(position, origin)]))
        self._write(key, _CAMERA_ANGLES, struct.pack("<2d", orient[0], orient[1]))
        self._write(key, _CAMERA_FOV, struct.pack("<d", self._vector(key, "Cam_eFOV", 1)[0]))

    def summary(self) -> dict:
        return {"objects": self.objects(), "camera": self.camera(), "sun": self.sun()}
