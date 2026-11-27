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

from . import heightfield, trimesh
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
_CAMERA_ANGLES, _CAMERA_FOV = 644, 724            # pitch, heading, bank; field of view
_CAMERA_OFFSET = 356                              # 3 doubles turned by the matrix
_SUN_VECTOR = 860

_OFFSET, _MATRIX, _TRANSLATION = 356, 380, 452     # same cache layout as cameras
# Where the sky and sun records cache each setting, confirmed by comparing a scene
# Bryce saved against its own setting records. Bryce's sky presets only have these.
SKY_CACHE = {
    "SkyLevel": 272, "CumulusC": 280, "CirrusC": 292, "Cover": 320, "Amplitude": 328,
    "Frequency": 336, "BaseC": 368, "HorizontC": 380, "DomeC": 392, "DefaultC": 404,
    "ShadowInt": 416, "AmbientC": 424, "AmbientInt": 436, "HazeC": 444,
    "HazeAmount": 456, "FogC": 464, "FogThick": 476, "FogAmount": 484,
    "MaxIllScl": 720, "LinkColBl": 728, "LinkValBl": 736, "RingInt": 744,
    "RingPriWid": 752, "RingSecWid": 760, "RBowVisi": 776, "RBowPriWid": 784,
    "RBowSecWid": 792, "VisiAirDen": 832, "WindDir": 840, "WindSpeed": 848,
    "WindTurb": 856, "MoonPhAng": 680, "MoonPhES": 688, "MoonPhTA": 696,
    "MoonPhHard": 704,
}
SUN_CACHE = {"DiscClr": 836, "HaloClr": 848, "SunVctr": 860}

# user-facing kind -> root list tag
KINDS = {
    "plane": "plne3dob", "cube": "cube3dob", "sphere": "sphr3dob", "torus": "toru3dob",
    "cylinder": "cylr3dob", "pyramid": "pyra3dob", "cone": "cone3dob", "disk": "circ3dob",
    "terrain": "hfld3dob", "lattice": "symh3dob", "mesh": "tmsh3dob",
    "radial_light": "sphrlght", "spot_light": "conelght",
    "square_spot_light": "pyrmlght", "parallel_light": "cubelght",
}
_GROUP_TAG, _GROUP, _LIST = "grou3dob", "C3dGroupType", "C3dObjList"
KINDS["group"] = _GROUP_TAG
_KIND_OF = {tag: kind for kind, tag in KINDS.items()}
# The class an object list entry points into. Needed because a 32-bit id is only
# unique within a class: a group and its member list share one, so do a sky preset
# and its sun, and preset libraries reuse old ids across classes.
_TAG_CLASS = {
    "basecamr": "C3dBaseCamera", "plne3dob": "C3dPlane", "cube3dob": "C3dCube",
    "sphr3dob": "C3dSphere", "toru3dob": "C3dTorusType", "cylr3dob": "C3dCylinder",
    "pyra3dob": "C3dPyramid", "cone3dob": "C3dCone", "circ3dob": "C3dCircle",
    "hfld3dob": "C3dHeightField", "symh3dob": "C3dHeightField",
    "tmsh3dob": "C3dTriMeshType2", "sphrlght": "C3dSphereLight",
    "conelght": "C3dConeLight", "pyrmlght": "C3dPyrLightType",
    "cubelght": "C3dCubeLight", _GROUP_TAG: _GROUP,
}
_NEVER_LINKED = frozenset((
    _LIST, _ROOT, "CPresetDescr", "objpresetdesc", "matprevcimage", "objprevcimage",
    "skyprevcimage", "brycedocset"))
_SLOT_CLASS = {280: _MATERIAL, 492: "Obj_ePosition", 496: "Obj_eOrient",
               500: "Obj_eScale", 504: "Obj_eShear", 508: "Obj_eOrigin"}
_TRACK_SLOT = {name: slot for slot, name in _SLOT_CLASS.items() if slot >= 492}
# Boolean role of an object inside a group: bits of the flag word at 272.
_FLAGS, BOOLEAN_BITS = 272, {"neutral": 0, "positive": 0x08, "negative": 0x10,
                             "intersect": 0x20}
# A record is named by (class name, id).
Ref = tuple[str, int]

# A material record holds one block per channel: a texture link at its start, a
# "textured" flag 20 bytes in and the plain value 24 bytes in. Read off Bryce's own
# presets (Mirror reflects 1.0; the mirrored checkerboard has a texture in the
# reflection slot, the woods in the bump slot). setting -> (block, value, record)
MATERIAL_CHANNELS = {
    "color": (276, 300, "Mat_eDiffuseC"),
    "ambient_color": (332, 356, "Mat_eAmbientC"),
    "specular_color": (388, 412, "Mat_eSpecularC"),
    "specular_halo_color": (444, 468, "Mat_eSpecExp"),
    "transparent_color": (500, 524, "Mat_eTransC"),
    "volume_color": (None, 872, "Mat_eVolC"),
    "diffuse": (556, 580, "Mat_eDiffuse"),
    "ambient": (596, 620, "Mat_eAmbient"),
    "specular": (636, 660, "Mat_eSpecular"),
    "transparency": (676, 700, "Mat_eTrans"),
    "reflection": (716, 740, "Mat_eReflect"),
    "bump": (756, 780, "Mat_eBump"),
    "metallic": (796, 820, "Mat_eMetallic"),
    "density": (836, 860, "Mat_eHTDensity"),
    "refraction": (None, 884, "Mat_eRefract"),
}
MATERIAL_COLOURS = {name: spec[2] for name, spec in MATERIAL_CHANNELS.items()
                    if name.endswith("color")}
MATERIAL_VALUES = {name: spec[2] for name, spec in MATERIAL_CHANNELS.items()
                   if not name.endswith("color")}
_TEXTURED_FLAG = 0x10000000
_TEXTURE_SCALE = 276          # three doubles in a texture record, one per axis
_SHOWN = ("color", "diffuse", "ambient", "specular", "transparency", "reflection",
          "bump", "refraction")


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


def camera_matrix(pitch: float, heading: float, bank: float = 0.0) -> list[list[float]]:
    """The camera record's cached matrix, from its stored angles in radians. Its rows
    are the camera's right, up and forward directions; bank rolls the first two."""
    cp, sp, cy, sy = math.cos(pitch), math.sin(pitch), math.cos(heading), math.sin(heading)
    right, up = [cy, 0.0, -sy], [sp * sy, cp, sp * cy]
    cb, sb = math.cos(bank), math.sin(bank)
    return [[cb * r + sb * u for r, u in zip(right, up)],
            [cb * u - sb * r for r, u in zip(right, up)],
            [cp * sy, -sp, cp * cy]]


def quaternion_from_matrix(m) -> tuple[float, float, float, float]:
    """The inverse of rotation_matrix."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        w = math.sqrt(1 + trace) / 2
        return ((m[2][1] - m[1][2]) / (4 * w), (m[0][2] - m[2][0]) / (4 * w),
                (m[1][0] - m[0][1]) / (4 * w), w)
    i = max(range(3), key=lambda a: m[a][a])
    j, k = (i + 1) % 3, (i + 2) % 3
    major = math.sqrt(max(0.0, 1 + m[i][i] - m[j][j] - m[k][k])) / 2 or 1e-12
    out = [0.0, 0.0, 0.0, (m[k][j] - m[j][k]) / (4 * major)]
    out[i] = major
    out[j] = (m[j][i] + m[i][j]) / (4 * major)
    out[k] = (m[k][i] + m[i][k]) / (4 * major)
    return tuple(out)


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


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
        self._ids: dict[int, dict[str, bytes]] = {}
        for name, rows in self._rows.items():
            for row in rows:
                self._ids.setdefault(struct.unpack_from(">I", row)[0], {})[name] = row

    def _row(self, ref: Ref) -> bytes:
        try:
            return self._ids[ref[1]][ref[0]]
        except KeyError:
            raise BryceError(f"corrupt scene: missing {ref[0]} record {ref[1]:#x}") from None

    def _entry(self, ref: Ref) -> int:
        return struct.unpack_from(">I", self._row(ref), 4)[0]

    def _payload(self, ref: Ref) -> bytes:
        return self.file.read(self._entry(ref))

    def _write(self, ref: Ref, offset: int, data: bytes) -> None:
        entry = self._entry(ref)
        payload = bytearray(self.file.read(entry))
        if offset + len(data) > len(payload):
            raise BryceError("corrupt scene: record is too short")
        payload[offset:offset + len(data)] = data
        self.file.replace(entry, bytes(payload))

    def _all_refs(self) -> list[Ref]:
        return [(name, key) for key, classes in self._ids.items() for name in classes]

    def _resolve(self, source: str, offset: int, value: int) -> Ref | None:
        """Which record the id found at `offset` of a `source` record means."""
        found = [name for name in self._ids.get(value, ()) if name not in _NEVER_LINKED]
        if len(found) < 2:
            return (found[0], value) if found else None
        if source == _MATERIAL:
            wanted = ("Mat_",) if offset >= 896 else ("C3dTexture", "CPictTexture")
        elif source == "CPictTexture":
            wanted = ("C3dCImage",)
        elif source == "C3dBaseSky":
            wanted = ("C3dTexture",) if offset < 508 else ("Sky_",)
        elif offset in _SLOT_CLASS:
            wanted = (_SLOT_CLASS[offset],)
        else:
            wanted = ("C3d", "HF_", "Cam_", "SunL_")
        for prefix in wanted:
            for name in found:
                if name.startswith(prefix):
                    return name, value
        return None

    def _list_items(self, ref: Ref) -> list[tuple[str, Ref, bytes]]:
        """(tag, record, raw 16 bytes) for each entry of an object list."""
        payload = self._payload(ref)
        count = struct.unpack_from("<I", payload, 8)[0]
        if 12 + 16 * count > len(payload):
            raise BryceError("corrupt scene: object list is too short")
        items = []
        for i in range(count):
            raw = payload[12 + 16 * i:28 + 16 * i]
            tag, key = _tag(raw[:8]), struct.unpack_from("<I", raw, 8)[0]
            name = _TAG_CLASS.get(tag)
            if name is None or name not in self._ids.get(key, ()):
                guess = self._resolve(ref[0], 0, key)
                if guess is None:
                    raise BryceError(f"corrupt scene: object list entry {tag!r} is missing")
                name = guess[0]
            items.append((tag, (name, key), raw))
        return items

    def _links(self, ref: Ref) -> list[tuple[int | None, Ref]]:
        """(offset, record) for everything this record points at. The offset is
        None for a group's member list, which is tied to it by sharing its id."""
        name, key = ref
        self._row(ref)
        if name == _LIST:
            return [(20 + 16 * i, item[1]) for i, item in enumerate(self._list_items(ref))]
        if name in (_ROOT, "C3dTexture") or not (name.startswith("C3d")
                                                 or name == "CPictTexture"):
            return []
        payload = self._payload(ref)
        found: list[tuple[int | None, Ref]] = []
        for offset in _LINKS:
            if offset + 4 > len(payload):
                break
            value = struct.unpack_from("<I", payload, offset)[0]
            if value > 0xFFFF and value != key and value in self._ids:
                target = self._resolve(name, offset, value)
                if target:
                    found.append((offset, target))
        if name == _GROUP and _LIST in self._ids.get(key, ()):
            found.append((None, (_LIST, key)))
        return found

    def _linked(self, ref: Ref, class_name: str) -> Ref | None:
        for _, other in self._links(ref):
            if other[0] == class_name:
                return other
        return None

    def _closure(self, ref: Ref, skip=frozenset()) -> list[Ref]:
        """The record and everything it depends on, dependencies first."""
        order: list[Ref] = []
        seen: set[Ref] = set()

        def visit(current):
            if current in seen or current in skip:
                return
            seen.add(current)
            for _, other in self._links(current):
                visit(other)
            order.append(current)

        visit(ref)
        return order

    def _first(self, class_name: str) -> Ref | None:
        rows = self._rows.get(class_name)
        return (class_name, struct.unpack_from(">I", rows[0])[0]) if rows else None

    # ------------------------------------------------------------- objects
    def _root(self) -> tuple[Ref, bytes, list[tuple[str, Ref, bytes]]]:
        ref = self._first(_ROOT)
        return ref, self._payload(ref), self._list_items(ref)

    def _write_list(self, ref: Ref, items: list[bytes]) -> None:
        header = bytearray(self._payload(ref)[:12])
        struct.pack_into("<I", header, 8, len(items))
        self.file.replace(self._entry(ref), bytes(header) + b"".join(items))

    def _write_root(self, items: list[bytes]) -> None:
        self._write_list(self._first(_ROOT), items)

    def _objects(self) -> list[tuple[str, Ref]]:
        return [(_KIND_OF.get(tag, tag), ref) for tag, ref, _ in self._root()[2]
                if tag != "basecamr"]

    def _members(self, group: Ref) -> list[tuple[str, Ref]]:
        listing = (_LIST, group[1])
        if _LIST not in self._ids.get(group[1], ()):
            return []
        return [(_KIND_OF.get(tag, tag), ref) for tag, ref, _ in self._list_items(listing)]

    def _name(self, key: Ref) -> str:
        return self._payload(key)[8:48].split(b"\0")[0].decode("latin-1")

    def find(self, name: str) -> tuple[str, Ref]:
        matches = [(kind, key) for kind, key in self._objects() if self._name(key) == name]
        if not matches:
            names = ", ".join(self._name(k) for _, k in self._objects())
            raise BryceError(f"no object named {name!r}; the scene has: {names}")
        if len(matches) > 1:
            raise BryceError(f"{len(matches)} objects are named {name!r}; rename one first")
        return matches[0]

    def _vector(self, key: Ref, class_name: str, count: int = 3):
        track = self._linked(key, class_name)
        if track is None:
            return None
        return struct.unpack_from(f"<{count}d", self._payload(track), _VALUE)

    def _set_vector(self, key: Ref, class_name: str, values) -> None:
        track = self._linked(key, class_name)
        if track is None:
            raise BryceError(f"this object has no {class_name} record")
        self._write(track, _VALUE, struct.pack(f"<{len(values)}d", *values))

    def _setting(self, track: Ref):
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
        if size >= _VALUE + 24 and track[0].endswith("Vctr"):
            return offset, "vector", struct.unpack_from("<3d", payload, offset)
        return offset, None, None

    def _set_setting(self, track: Ref, value, label: str) -> None:
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

    def _textures(self, material: Ref) -> dict[str, Ref]:
        """channel -> texture, for the channels a texture drives."""
        payload = self._payload(material)
        links = dict(self._links(material))
        return {name: links[block] for name, (block, _, _) in MATERIAL_CHANNELS.items()
                if block in links
                and struct.unpack_from("<I", payload, block + 20)[0] & _TEXTURED_FLAG}

    def material(self, key: Ref) -> dict:
        """The settings in force: the setting record where there is one, otherwise
        the value kept in the material itself (as in Bryce's presets)."""
        material = self._linked(key, _MATERIAL)
        if material is None:
            return {}
        payload = self._payload(material)
        tracks = {k[0]: k for _, k in self._links(material)}
        out = {}
        for label, (_, offset, class_name) in MATERIAL_CHANNELS.items():
            colour = label in MATERIAL_COLOURS
            if class_name in tracks:
                _, form, value = self._setting(tracks[class_name])
                if form is None:
                    continue
            elif label in _SHOWN and offset + (12 if colour else 4) <= len(payload):
                form = "colour" if colour else "float"
                value = struct.unpack_from("<3f" if colour else "<f", payload, offset)
                value = value if colour else value[0]
            else:
                continue
            if form == "colour":
                if all(-1e-6 <= v <= 1 + 1e-6 for v in value):
                    out[label] = "#" + "".join(
                        f"{round(max(0.0, min(1.0, v)) * 255):02x}" for v in value)
            elif math.isfinite(value) and abs(value) < 1000:
                out[label] = round(value, 4)
        used = self._textures(material)
        textured = {name: self._texture_name(ref) for name, ref in used.items()}
        if textured:
            out["textures"] = textured
            scales = {self._texture_name(ref): self.texture_scale_of(ref)
                      for ref in used.values()}
            if any(value is not None for value in scales.values()):
                out["texture_scales"] = scales
        return out

    def texture_scale_of(self, ref: Ref) -> list[float] | None:
        """The three scale numbers of a texture record, if it has plausible ones."""
        if ref[0] != "C3dTexture":
            return None
        payload = self._payload(ref)
        if len(payload) < _TEXTURE_SCALE + 24:
            return None
        values = struct.unpack_from("<3d", payload, _TEXTURE_SCALE)
        if not all(math.isfinite(v) and 1e-6 < abs(v) < 1e6 for v in values):
            return None
        return [round(v, 5) for v in values]

    def scale_texture(self, name: str, factor, channels: list[str] | None = None) -> dict:
        """Multiply the scale numbers of the textures on an object's material."""
        factors = ([_number(factor, "factor")] * 3
                   if isinstance(factor, (int, float)) and not isinstance(factor, bool)
                   else list(_triple(factor, "factor")))
        if not all(1e-3 <= f <= 1e3 for f in factors):
            raise BryceError("texture scale factors must be between 0.001 and 1000")
        material = self._own_material(name)
        used = self._textures(material)
        wanted = list(used) if channels is None else channels
        missing = [c for c in wanted if c not in used]
        if missing or not wanted:
            raise BryceError(f"no texture on {', '.join(missing) or 'any channel'} of "
                             f"{name!r}; textured channels: {', '.join(used) or 'none'}")
        for ref in {used[c] for c in wanted}:
            if self.texture_scale_of(ref) is None:
                raise BryceError(f"the scale of {self._texture_name(ref)!r} cannot be "
                                 "changed (it is not a procedural 3D texture)")
            values = struct.unpack_from("<3d", self._payload(ref), _TEXTURE_SCALE)
            self._write(ref, _TEXTURE_SCALE, struct.pack(
                "<3d", *(v * f for v, f in zip(values, factors))))
        return self.material(self.find(name)[1])

    def _texture_name(self, ref: Ref) -> str:
        return self._payload(ref)[8:40].split(b"\0")[0].decode("latin-1") or ref[0]

    def textures(self) -> dict[str, Ref]:
        """Every named texture in this file."""
        found = {}
        for row in self._rows.get("C3dTexture", []):
            ref = ("C3dTexture", struct.unpack_from(">I", row)[0])
            found.setdefault(self._texture_name(ref), ref)
        found.pop("C3dTexture", None)
        return found

    def _own_material(self, name: str) -> Ref:
        _, key = self.find(name)
        material = self._linked(key, _MATERIAL)
        if material is None:
            raise BryceError(f"{name!r} has no material")
        return material

    def set_material(self, name: str, settings: dict, library: "Br3 | None" = None) -> dict:
        unknown = sorted(set(settings) - set(MATERIAL_CHANNELS))
        if unknown:
            raise BryceError(f"unknown material settings {unknown}; "
                             f"use {sorted(MATERIAL_CHANNELS)}")
        material = self._own_material(name)
        released = set()
        for label, value in settings.items():
            block, offset, class_name = MATERIAL_CHANNELS[label]
            if label in MATERIAL_COLOURS:
                data = struct.pack("<3f", *_colour(value, label))
            else:
                data = struct.pack("<f", _number(value, label))
            texture = self._textures(material).get(label)
            if texture is not None:                  # a plain value replaces a texture
                self._write(material, block, bytes(4))
                self._write(material, block + 20, bytes(4))
                released.add(texture)
            self._write(material, offset, data)
            track = self._linked(material, class_name)
            if track is not None:
                self._set_setting(track, value, label)
        if released:
            self._discard(released)
        return self.material(self.find(name)[1])

    def set_texture(self, name: str, texture: str, channels: list[str],
                    sources=(), strength: float | None = None) -> dict:
        """Drive material channels with one of Bryce's textures, found by name."""
        usable = [c for c, spec in MATERIAL_CHANNELS.items() if spec[0] is not None]
        if not channels or any(c not in usable for c in channels):
            raise BryceError(f"channels must be some of {', '.join(usable)}")
        material = self._own_material(name)
        for source in (self, *sources):
            if source is None:
                continue
            ref = next((r for n, r in source.textures().items()
                        if n.lower() == texture.lower()), None)
            if ref is not None:
                break
        else:
            available = sorted({n for s in (self, *sources) if s for n in s.textures()})
            raise BryceError(f"no texture named {texture!r}; available: {', '.join(available)}")
        copy = self._copy(source, ref)
        material = self._own_material(name)
        for channel in channels:
            block = MATERIAL_CHANNELS[channel][0]
            self._write(material, block, struct.pack("<I", copy[1]))
            self._write(material, block + 20, struct.pack("<I", _TEXTURED_FLAG))
        if strength is not None:
            for channel in channels:
                if channel in MATERIAL_VALUES:
                    _, offset, class_name = MATERIAL_CHANNELS[channel]
                    self._write(material, offset,
                                struct.pack("<f", _number(strength, "strength")))
                    track = self._linked(material, class_name)
                    if track is not None:
                        self._set_setting(track, strength, channel)
        return self.material(self.find(name)[1])

    # A transform is (position, origin, scale, rotation quaternion) in file units.
    # Bryce keeps it in setting records and caches the resulting matrix in the
    # object. Objects from preset libraries often have only the cache.
    def _cached_transform(self, ref: Ref):
        payload = self._payload(ref)
        offset = struct.unpack_from("<3d", payload, _OFFSET)
        flat = struct.unpack_from("<9d", payload, _MATRIX)
        rows = [list(flat[i * 3:i * 3 + 3]) for i in range(3)]
        move = struct.unpack_from("<3d", payload, _TRANSLATION)
        scale = [math.sqrt(sum(v * v for v in row)) for row in rows]
        if min(scale) < 1e-9:
            return list(move), [0.0] * 3, [1.0] * 3, (0.0, 0.0, 0.0, 1.0)
        turn = [[v / size for v in row] for row, size in zip(rows, scale)]
        determinant = sum(turn[0][i] * (turn[1][(i + 1) % 3] * turn[2][(i + 2) % 3]
                          - turn[1][(i + 2) % 3] * turn[2][(i + 1) % 3]) for i in range(3))
        if determinant < 0:                       # mirrored: carried by the scale
            scale[0], turn[0] = -scale[0], [-v for v in turn[0]]
        position = [move[axis] + sum(offset[i] * rows[i][axis] for i in range(3))
                    for axis in range(3)]
        return position, [0.0] * 3, scale, quaternion_from_matrix(turn)

    def transform(self, ref: Ref):
        position = self._vector(ref, "Obj_ePosition")
        origin = self._vector(ref, "Obj_eOrigin")
        scale = self._vector(ref, "Obj_eScale")
        rotation = self._vector(ref, "Obj_eOrient", 4)
        if None not in (position, origin, scale, rotation):
            return list(position), list(origin), list(scale), tuple(rotation)
        cached = self._cached_transform(ref)
        return (list(position) if position is not None else cached[0],
                list(origin) if origin is not None else [0.0] * 3,
                list(scale) if scale is not None else cached[2],
                tuple(rotation) if rotation is not None else cached[3])

    def centre_of(self, ref: Ref) -> list[float]:
        return world_centre(*self.transform(ref))

    def _ensure_track(self, ref: Ref, class_name: str, library: "Br3 | None") -> Ref:
        track = self._linked(ref, class_name)
        if track is not None:
            return track
        for source in (self, library):
            if source is not None and source._rows.get(class_name):
                track = self._copy(source, source._first(class_name))
                self._write(ref, _TRACK_SLOT[class_name], struct.pack("<I", track[1]))
                return track
        raise BryceError(f"no {class_name} record to copy; save a scene with a moved object "
                         "in Bryce and use it as the template file")

    def place(self, ref: Ref, centre, scale, rotation, library: "Br3 | None" = None) -> None:
        """Put an object's centre at `centre` (file units). The local offset is
        folded away so that scale and rotation act about the centre."""
        cached = self._cached_transform(ref)
        wanted = (("Obj_ePosition", centre, None),
                  ("Obj_eOrigin", (0.0, 0.0, 0.0), None),
                  ("Obj_eScale", scale, cached[2]),
                  ("Obj_eOrient", rotation, cached[3]))
        for class_name, value, already in wanted:
            if self._linked(ref, class_name) is None:
                unchanged = already is not None and all(
                    abs(x - y) < 1e-9 for x, y in zip(value, already))
                if class_name == "Obj_eOrigin" or unchanged:
                    continue                       # the cache already says this
                self._ensure_track(ref, class_name, library)
            self._set_vector(ref, class_name, value)
        rows = rotation_matrix(rotation)
        self._write(ref, _OFFSET, struct.pack("<3d", 0.0, 0.0, 0.0))
        self._write(ref, _MATRIX, struct.pack(
            "<9d", *(rows[i][j] * scale[i] for i in range(3) for j in range(3))))
        self._write(ref, _TRANSLATION, struct.pack("<3d", *centre))

    def boolean_of(self, ref: Ref) -> str:
        flags = struct.unpack_from("<I", self._payload(ref), _FLAGS)[0]
        return next((name for name, bit in BOOLEAN_BITS.items()
                     if bit and flags & bit), "neutral")

    def set_boolean(self, ref: Ref, mode: str) -> None:
        if mode not in BOOLEAN_BITS:
            raise BryceError(f"boolean must be one of {', '.join(BOOLEAN_BITS)}")
        flags = struct.unpack_from("<I", self._payload(ref), _FLAGS)[0]
        self._write(ref, _FLAGS, struct.pack("<I", (flags & ~0x38) | BOOLEAN_BITS[mode]))

    def _local_extent(self, kind: str, key: Ref) -> list[float]:
        """How big an object is in its own space. Bryce's shapes span -1 to 1; a
        mesh has whatever its points span."""
        if kind == "mesh":
            try:
                points = trimesh.decode(self._payload(key))[0]
            except BryceError:
                points = []
            if points:
                spans = [max(p[a] for p in points) - min(p[a] for p in points)
                         for a in range(3)]
                return [span if span > 1e-9 else 2.0 for span in spans]
        return [2.0] * 3

    def describe(self, kind: str, key: Ref) -> dict:
        info = {"name": self._name(key), "kind": kind}
        position, origin, scale, rotation = self.transform(key)
        info["position"] = [round(v * UNIT, 3) + 0.0
                            for v in world_centre(position, origin, scale, rotation)]
        info["scale"] = [round(v, 4) for v in scale]
        info["size"] = [round(v * extent * UNIT, 2)
                        for v, extent in zip(scale, self._local_extent(kind, key))]
        info["rotation"] = [round(v, 2) + 0.0 for v in euler_from_quaternion(rotation)]
        if self.boolean_of(key) != "neutral":
            info["boolean"] = self.boolean_of(key)
        material = self.material(key)
        if material:
            info["material"] = material
        if kind == "group":
            # Members are positioned relative to their group.
            info["members"] = [self.describe(k, r) for k, r in self._members(key)]
        return info

    def objects(self) -> list[dict]:
        return [self.describe(kind, key) for kind, key in self._objects()]

    def set_object(self, name: str, *, new_name: str | None = None, position=None,
                   scale=None, size=None, rotation=None, material: dict | None = None,
                   boolean: str | None = None, library: "Br3 | None" = None) -> dict:
        kind, key = self.find(name)
        if scale is not None and size is not None:
            raise BryceError("give scale or size, not both")
        if size is not None:
            scale = [v / (extent * UNIT) for v, extent in
                     zip(_triple(size, "size"), self._local_extent(kind, key))]
        if scale is not None:
            scale = _triple(scale, "scale")
            if min(scale) <= 0:
                raise BryceError("scale and size must be positive")
        turned = None if rotation is None else quaternion_from_euler(rotation)
        wanted = None if position is None else [v / UNIT for v in _triple(position, "position")]
        if wanted is not None or scale is not None or turned is not None:
            old = self.transform(key)
            self.place(key, world_centre(*old) if wanted is None else wanted,
                       old[2] if scale is None else scale,
                       old[3] if turned is None else turned, library)
        if boolean is not None:
            self.set_boolean(key, boolean)
        if material:
            self.set_material(name, material, library)
        if new_name is not None:
            self._rename(key, new_name)
        return self.describe(kind, key)

    def _rename(self, key: Ref, new_name: str) -> None:
        if any(self._name(k) == new_name for _, k in self._objects() if k != key):
            raise BryceError(f"an object named {new_name!r} already exists")
        packed = heightfield.pack_name(new_name, 32)
        self._write(key, 8, packed)
        class_name = key[0]
        rows = self._rows[class_name]
        for i, row in enumerate(rows):
            if struct.unpack_from(">I", row)[0] == key[1]:
                rows[i] = row[:12] + packed + row[12 + len(packed):]
        self.file.set_table(*self._classes[class_name], rows)
        self._reindex()

    # ------------------------------------------------------- copy and delete
    def _new_key(self, taken=()) -> int:
        key = (max(k for k in self._ids if k < 0x7F000000) + 0x40) & ~7
        while key in self._ids or key in taken:
            key += 8
        return key

    def _add_record(self, class_name: str, key: int, payload: bytes, row_tail: bytes,
                    companion: bytes = b"", after: list[str] | None = None) -> Ref:
        where = self.file.add_class(class_name, 12 + len(row_tail), after=after)
        self._classes = self.file.classes()
        rows = self._rows.setdefault(class_name, [])
        entry = self.file.add_entry(payload)
        second = bytearray(companion)
        if len(second) == 80 and second.startswith(b"Imge"):    # picture description
            struct.pack_into(">I", second, 20, entry)
            struct.pack_into(">I", second, 40, entry)
        row = struct.pack(">III", key, entry, self.file.add_entry(bytes(second))) + row_tail
        rows.append(row)
        self.file.set_table(*where, rows)
        self._ids.setdefault(key, {})[class_name] = row
        return class_name, key

    def _copy(self, source: "Br3", ref: Ref, skip=frozenset()) -> Ref:
        """Copy a record and its dependencies from `source`; returns the new record."""
        order = source._closure(ref, skip)
        names = list(source._classes)
        # Read everything first: `source` may be this very scene.
        pending = []
        for old in order:
            row = source._row(old)
            second = struct.unpack_from(">I", row, 8)[0]
            pending.append((old, row[12:], source._payload(old), source._links(old),
                            source.file.read(second) if second else b"",
                            names[:names.index(old[0])]))
        # A group and its member list must keep sharing one id.
        shared: dict[int, int] = {}
        fresh: dict[Ref, int] = {}
        for old in order:
            paired = old[0] in (_GROUP, _LIST)
            key = shared.get(old[1]) if paired else None
            if key is None:
                key = self._new_key(set(fresh.values()))
            if paired:
                shared[old[1]] = key
            fresh[old] = key
        for old, tail, payload, links, companion, earlier in pending:
            body = bytearray(payload)
            for offset, target in links:
                if offset is not None and target in fresh:
                    struct.pack_into("<I", body, offset, fresh[target])
            self._add_record(old[0], fresh[old], bytes(body), tail, companion, earlier)
        self._reindex()
        return ref[0], fresh[ref]

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
        self._append(KINDS[kind], new)
        # The copy still carries the original's name until it is renamed.
        self._rename(new, final)
        material = settings.pop("material", None)
        if material:
            self.set_material(final, material, library)
        return self.set_object(final, library=library, **settings)

    def add_mesh(self, vertices, faces, *, name: str | None = None,
                 library: "Br3 | None" = None, position=None, scale=None, size=None,
                 rotation=None, material: dict | None = None,
                 smooth_angle: float = trimesh.DEFAULT_CREASE) -> dict:
        """Create a new mesh object from points (Bryce units) and faces listed
        counter-clockwise from outside. Without a position it stays where its points
        are. An existing mesh (a stone) supplies everything but the geometry."""
        if scale is not None and size is not None:
            raise BryceError("give scale or size, not both")
        points, triangles = trimesh.prepare(vertices, faces)
        low = [min(p[a] for p in points) for a in range(3)]
        high = [max(p[a] for p in points) for a in range(3)]
        middle = [(lo + hi) / 2 for lo, hi in zip(low, high)]
        half = max(hi - lo for lo, hi in zip(low, high)) / 2
        if not half > 0:
            raise BryceError("the mesh has no size")
        # Like Bryce's own shapes, keep the geometry within -1..1 and let the
        # object's scale give it its size.
        local = [[(p[a] - middle[a]) / half for a in range(3)] for p in points]
        if size is not None:
            wanted = _triple(size, "size")
            extent = [hi - lo for lo, hi in zip(low, high)]
            scale = [w / e if e > 1e-9 else 1.0 for w, e in zip(wanted, extent)]
        factors = [1.0] * 3 if scale is None else _triple(scale, "scale")
        if min(factors) <= 0:
            raise BryceError("scale and size must be positive")
        kind = "mesh"
        for source in (library, self):
            if source is None:
                continue
            donor = next((k for found, k in source._objects() if found == kind), None)
            if donor is not None:
                break
        else:
            raise BryceError("no mesh to copy. In Bryce, add a stone (or import any model) "
                             "to your template scene and save it.")
        payload = trimesh.encode(source._payload(donor), local, triangles, smooth_angle)
        self.add_object(kind, name=name if name is not None else self._unique_name("Mesh"),
                        library=library, template=source._name(donor), position=[0, 0, 0])
        final = self._name(self._objects()[-1][1])
        ref = self.find(final)[1]
        current = self._payload(ref)
        self.file.replace(self._entry(ref),
                          current[:trimesh.HEADER] + payload[trimesh.HEADER:])
        centre = middle if position is None else _triple(position, "position")
        self.place(ref, [c / UNIT for c in centre],
                   [f * half / UNIT for f in factors],
                   quaternion_from_euler(rotation or (0, 0, 0)), library)
        if material:
            self.set_material(final, material, library)
        info = self.describe(kind, ref)
        info["triangles"] = len(triangles)
        return info

    def mesh_geometry(self, ref: Ref) -> tuple[list, list]:
        """A mesh object's points (in its own space) and outward triangles."""
        points, _, triangles = trimesh.decode(self._payload(ref))
        return points, [(t[0], t[2], t[1]) for t in triangles]

    def _resting_position(self, source: "Br3", key: Ref, settings: dict):
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

    def duplicate(self, name: str, new_name: str | None = None,
                  library: "Br3 | None" = None, **settings) -> dict:
        kind, original = self.find(name)
        final = new_name if new_name is not None else self._unique_name(name)
        if any(self._name(k) == final for _, k in self._objects()):
            raise BryceError(f"an object named {final!r} already exists")
        heightfield.pack_name(final, 32)
        if settings.get("position") is None:
            settings["position"] = self._resting_position(self, original, settings)
        copy = self._copy(self, original)
        self._append(KINDS[kind], copy)
        self._rename(copy, final)
        material = settings.pop("material", None)
        if material:
            self.set_material(final, material, library)
        return self.set_object(final, library=library, **settings)

    def _append(self, tag: str, ref: Ref) -> None:
        item = tag.encode()[::-1] + struct.pack("<II", ref[1], 0)
        self._write_root([raw for _, _, raw in self._root()[2]] + [item])

    def delete(self, name: str) -> None:
        _, ref = self.find(name)
        self._write_root([raw for _, other, raw in self._root()[2] if other != ref])
        self._discard(set(self._closure(ref)))

    def _discard(self, doomed: set[Ref]) -> None:
        """Drop these records, except any that something else still points at."""
        while True:
            needed = {other for ref in self._all_refs() if ref not in doomed
                      for _, other in self._links(ref)}
            keep = doomed & needed
            if not keep:
                break
            doomed -= keep
        for class_name in {name for name, _ in doomed}:
            rows = [r for r in self._rows[class_name]
                    if (class_name, struct.unpack_from(">I", r)[0]) not in doomed]
            self.file.set_table(*self._classes[class_name], rows)
        self._reindex()

    # -------------------------------------------------- groups and booleans
    def _group_donor(self, donors) -> tuple["Br3", Ref]:
        for donor in donors:
            if donor is None or not donor._rows.get(_GROUP):
                continue
            return donor, (_GROUP, struct.unpack_from(">I", donor._rows[_GROUP][0])[0])
        raise BryceError("no group to copy: set BRYCE3D_INSTALL to your Bryce folder "
                         "(its Boolean Objects presets contain groups)")

    def group(self, names: list[str], name: str | None = None, *,
              donors=(), library: "Br3 | None" = None,
              negative: list[str] = (), intersect: list[str] = ()) -> dict:
        """Group objects. Naming any member in negative or intersect makes a boolean:
        the other members are then positive."""
        if not isinstance(names, list) or len(names) < 2 or len(set(names)) != len(names):
            raise BryceError("names must list at least two different objects")
        odd = [n for n in (*negative, *intersect) if n not in names]
        if odd or set(negative) & set(intersect):
            raise BryceError("negative and intersect must be separate parts of names")
        members = [self.find(n)[1] for n in names]
        final = name if name is not None else self._unique_name("Group")
        if any(self._name(k) == final for _, k in self._objects()):
            raise BryceError(f"an object named {final!r} already exists")
        heightfield.pack_name(final, 32)
        source, template = self._group_donor((self, library, *donors))
        boolean = bool(negative or intersect)
        centres = [self.centre_of(ref) for ref in members]
        middle = [(min(c[a] for c in centres) + max(c[a] for c in centres)) / 2
                  for a in range(3)]

        # Copy the group itself, not what it holds, then give it its own member list.
        group = self._copy(source, template, skip=frozenset({(_LIST, template[1])}))
        items = {ref: raw for _, ref, raw in self._root()[2]}
        header = struct.pack("<III", 1, 1, len(members))
        classes = list(self._classes)
        self._add_record(_LIST, group[1], header + b"".join(items[r] for r in members),
                         bytes(48), after=classes[:classes.index(_ROOT)])
        self._reindex()
        for member_name, ref, centre in zip(names, members, centres):
            _, _, scale, rotation = self.transform(ref)
            self.place(ref, [c - m for c, m in zip(centre, middle)], scale, rotation, library)
            if boolean:
                self.set_boolean(ref, "negative" if member_name in negative else
                                 "intersect" if member_name in intersect else "positive")
        self.place(group, middle, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), library or source)
        self.set_boolean(group, "positive" if boolean else "neutral")
        self._write_root([raw for _, ref, raw in self._root()[2] if ref not in members])
        self._append(_GROUP_TAG, group)
        self._rename(group, final)
        return self.describe("group", group)

    def ungroup(self, name: str, library: "Br3 | None" = None) -> dict:
        kind, group = self.find(name)
        if kind != "group":
            raise BryceError(f"{name!r} is not a group")
        items = self._list_items((_LIST, group[1]))
        position, origin, gscale, gturn = self.transform(group)
        centre = world_centre(position, origin, gscale, gturn)
        gmatrix = rotation_matrix(gturn)
        uniform = max(gscale) - min(gscale) < 1e-6
        moves = []
        for _, ref, _ in items:
            _, _, scale, rotation = self.transform(ref)
            if not uniform and any(abs(v) > 1e-6 for v in rotation[:3]):
                raise BryceError("a stretched group with rotated members cannot be taken "
                                 "apart exactly; ungroup it in Bryce")
            local = [c * s for c, s in zip(self.centre_of(ref), gscale)]
            world = [centre[a] + sum(local[i] * gmatrix[i][a] for i in range(3))
                     for a in range(3)]
            size = [s * gscale[0 if uniform else i] for i, s in enumerate(scale)]
            turn = quaternion_from_matrix(_matmul(rotation_matrix(rotation), gmatrix))
            moves.append((ref, world, size, turn))
        for ref, world, size, turn in moves:
            self.place(ref, world, size, turn, library)
        released = frozenset(ref for _, ref, _ in items)
        doomed = set(self._closure(group, skip=released))
        self._write_root([raw for _, ref, raw in self._root()[2] if ref != group]
                         + [raw for _, _, raw in items])
        self._discard(doomed)
        names = []
        for _, ref, _ in items:
            current = self._name(ref)
            taken = [self._name(r) for _, r in self._objects()]
            if not current or taken.count(current) > 1:
                self._rename(ref, self._unique_name(current or f"{name[:24]} part"))
            names.append(self._name(ref))
        return {"ungrouped": name, "objects": names}

    # -------------------------------------------------------------- presets
    def insert_preset(self, source: "Br3", preset: int, base_name: str, *,
                      position=None, scale=None, rotation=None,
                      name: str | None = None, library: "Br3 | None" = None) -> list[dict]:
        """Copy every object of one object-library preset into the scene."""
        items = [item for item in source._list_items((_ROOT, preset)) if item[0] != "basecamr"]
        if not items:
            raise BryceError("that preset holds no objects")
        factor = 1.0 if scale is None else _number(scale, "scale")
        if factor <= 0:
            raise BryceError("scale must be positive")
        if rotation is not None and len(items) > 1:
            raise BryceError("rotation needs a one-object preset; group the objects "
                             "afterwards and rotate the group")
        copies = []
        for tag, ref, _ in items:
            copy = self._copy(source, ref)
            self._append(tag, copy)
            copies.append((tag, copy))
        centres = [self.centre_of(ref) for _, ref in copies]
        middle = [sum(c[a] for c in centres) / len(centres) for a in range(3)]
        target = middle if position is None else [v / UNIT for v in _triple(position, "position")]
        stem = (name or base_name)[:24].strip() or "Preset"
        added = []
        for number, ((tag, ref), centre) in enumerate(zip(copies, centres), 1):
            final = stem if len(copies) == 1 else f"{stem} {number}"
            if any(self._name(k) == final for _, k in self._objects() if k != ref):
                final = self._unique_name(final)
            self._rename(ref, final)
            if position is not None or scale is not None or rotation is not None:
                _, _, size, turn = self.transform(ref)
                if rotation is not None:
                    turn = quaternion_from_euler(rotation)
                self.place(ref, [t + (c - m) * factor
                                 for t, c, m in zip(target, centre, middle)],
                           [v * factor for v in size], turn, library)
            added.append(self.describe(_KIND_OF.get(tag, tag), ref))
        return added

    def apply_material_preset(self, name: str, source: "Br3", preset: int) -> dict:
        _, key = self.find(name)
        old = self._linked(key, _MATERIAL)
        new = self._copy(source, (_MATERIAL, preset))
        self._write(key, _MATERIAL_LINK, struct.pack("<I", new[1]))
        self._reindex()
        if old is not None:
            self._discard(set(self._closure(old)))
        return self.describe(*self.find(name))

    def apply_sky_preset(self, source: "Br3", preset: int) -> None:
        """Take a sky preset's sky and sun. This scene's setting records are kept
        and given the preset's values; everything else in the two records is copied."""
        for class_name, prefix, cache in (("C3dBaseSky", "Sky_e", SKY_CACHE),
                                          ("C3dSunLight", "SunL_e", SUN_CACHE)):
            if class_name not in source._ids.get(preset, {}):
                if class_name == "C3dSunLight":
                    continue
                raise BryceError("that preset holds no sky")
            owner = self._first(class_name)
            if owner is None:
                raise BryceError("the scene has no sky record")
            theirs = (class_name, preset)
            donor = source._payload(theirs)
            current = self._payload(owner)
            if len(donor) != len(current):
                raise BryceError("the preset's sky record has a different layout")
            donor_links = dict(source._links(theirs))
            body = bytearray(donor)
            body[:40] = current[:40]                           # version and name
            replaced = []
            for offset, target in self._links(owner):
                body[offset:offset + 4] = current[offset:offset + 4]
                theirs_here = donor_links.get(offset)
                if target[0] == "C3dTexture" and theirs_here:
                    copy = self._copy(source, theirs_here)
                    struct.pack_into("<I", body, offset, copy[1])
                    replaced.append(target)
            self.file.replace(self._entry(owner), bytes(body))
            for _, track in self._links(owner):
                label = track[0][len(prefix):]
                if not track[0].startswith(prefix) or label not in cache:
                    continue
                offset, form, _ = self._setting(track)
                if form is None:
                    continue
                if form in ("colour", "vector"):
                    shape = "<3f" if form == "colour" else "<3d"
                    self._write(track, offset, struct.pack(
                        shape, *struct.unpack_from(shape, donor, cache[label])))
                else:
                    value = struct.unpack_from("<d", donor, cache[label])[0]
                    self._write(track, offset, struct.pack(
                        "<f" if form == "float" else "<d", value))
            if replaced:
                self._discard(set(replaced))

    # ------------------------------------------------------------- terrain
    def _terrain(self, name: str) -> Ref:
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
        self.file.replace(self._entry(key), updated)

    def sync_terrain_copy(self, entry: int) -> None:
        """After a terrain record was rewritten directly, refresh its second copy."""
        for kind, key in self._objects():
            if kind in ("terrain", "lattice") and self._entry(key) == entry:
                self.set_terrain_heights(self._name(key),
                                         heightfield.decode(self._payload(key))[1])

    # ------------------------------------------------- sky, sun and camera
    def _scene_settings(self, class_name: str, prefix: str) -> dict[str, Ref]:
        owner = self._first(class_name)
        if owner is None:
            return {}
        return {k[0][len(prefix):]: k for _, k in self._links(owner)
                if k[0].startswith(prefix)}

    def _read_settings(self, tracks: dict[str, Ref]) -> dict:
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
            self._cache_setting("C3dBaseSky", SKY_CACHE.get(label), tracks[label])
        return self.sky()

    def _cache_setting(self, owner_class: str, offset: int | None, track: Ref) -> None:
        """Copy a setting record's value into its owner's cached copy, which is
        what Bryce draws from when it opens a scene."""
        if offset is None:
            return
        _, form, value = self._setting(track)
        if form is None:
            return
        shape = {"colour": "<3f", "vector": "<3d"}.get(form, "<d")
        values = value if form in ("colour", "vector") else (value,)
        self._write(self._first(owner_class), offset, struct.pack(shape, *values))

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
                self._cache_setting("C3dSunLight", SUN_CACHE[label], tracks[label])
        return self.sun()

    # The camera stores x and z negated and its heading turned by half a circle
    # relative to object coordinates (checked against real renders). When a scene
    # is opened Bryce draws from the copies inside the camera record, not from the
    # setting records, so both are written.
    # Bryce keeps a separate camera for each view. "director" is the Director's View
    # and "perspective" is the Camera View. Both use the same coordinates as objects.
    # Bryce stores pitch positive DOWNWARDS (its Top camera has pitch +90 degrees);
    # this code reports and accepts positive as upwards.
    def _camera_key(self, name: str) -> Ref | None:
        for row in self._rows.get(_CAMERA, []):
            if row[12:].split(b"\0")[0] == name.encode():
                return _CAMERA, struct.unpack_from(">I", row)[0]
        return None

    def camera(self, view: str = "director") -> dict:
        """The camera of one view, from the values cached in its record: a point
        (offset) turned by the matrix, plus a translation."""
        key = self._camera_key(view) or self._first(_CAMERA)
        payload = self._payload(key)
        offset = struct.unpack_from("<3d", payload, _CAMERA_OFFSET)
        flat = struct.unpack_from("<9d", payload, _CAMERA_MATRIX)
        translation = struct.unpack_from("<3d", payload, _CAMERA_TRANSLATION)
        position = [(sum(offset[row] * flat[row * 3 + axis] for row in range(3))
                     + translation[axis]) * UNIT for axis in range(3)]
        pitch, heading, bank = struct.unpack_from("<3d", payload, _CAMERA_ANGLES)
        fov = struct.unpack_from("<d", payload, _CAMERA_FOV)[0]
        return {"position": [round(v, 3) + 0.0 for v in position],
                "heading": round(math.degrees(heading) % 360, 2),
                "pitch": round(-math.degrees(pitch), 2) + 0.0,
                "bank": round(math.degrees(bank), 2),
                "fov": round(math.degrees(fov), 2)}

    def set_camera(self, position=None, heading=None, pitch=None, fov=None,
                   look_at=None, bank=None) -> dict:
        """Move both the Director's View and the Camera View, so the render is the
        same whichever of the two Bryce is showing. Anything not given is taken from
        the Director's View."""
        current = self.camera()
        if look_at is not None and (heading is not None or pitch is not None):
            raise BryceError("give look_at or heading/pitch, not both")
        position = current["position"] if position is None else _triple(position, "position")
        if look_at is not None:
            dx, dy, dz = (t - e for t, e in zip(_triple(look_at, "look_at"), position))
            if not (dx or dz):
                raise BryceError("look_at must not be straight above or below the camera")
            heading = math.degrees(math.atan2(dx, dz))
            pitch = math.degrees(math.atan2(dy, math.hypot(dx, dz)))
        heading = current["heading"] if heading is None else _number(heading, "heading")
        pitch = current["pitch"] if pitch is None else _number(pitch, "pitch")
        fov = current["fov"] if fov is None else _number(fov, "fov")
        bank = current["bank"] if bank is None else _number(bank, "bank")
        if not -180 <= bank <= 180:
            raise BryceError("bank must be -180 to 180 degrees")
        if not -90 <= pitch <= 90:
            raise BryceError("pitch must be -90 to 90 degrees")
        if not 1 <= fov <= 170:
            raise BryceError("fov must be 1-170 degrees")

        turn = math.radians((heading + 180) % 360 - 180)
        tilt, lens, roll = math.radians(-pitch), math.radians(fov), math.radians(bank)
        stored = [v / UNIT for v in position]
        matrix = [v for row in camera_matrix(tilt, turn, roll) for v in row]
        keys = [k for k in (self._camera_key("perspective"), self._camera_key("director"))
                if k is not None] or [self._first(_CAMERA)]
        for key in keys:
            if self._linked(key, "Obj_ePosition") is not None:
                self._set_vector(key, "Obj_ePosition", stored)
                if self._linked(key, "Obj_eOrigin") is not None:
                    self._set_vector(key, "Obj_eOrigin", [0.0, 0.0, 0.0])
                self._set_vector(key, "Cam_eCraOrient", [tilt, turn, roll])
                self._set_vector(key, "Cam_eFOV", [lens])
            self._write(key, _CAMERA_OFFSET, struct.pack("<3d", 0.0, 0.0, 0.0))
            self._write(key, _CAMERA_MATRIX, struct.pack("<9d", *matrix))
            self._write(key, _CAMERA_TRANSLATION, struct.pack("<3d", *stored))
            self._write(key, _CAMERA_ANGLES, struct.pack("<3d", tilt, turn, roll))
            self._write(key, _CAMERA_FOV, struct.pack("<d", lens))
        return self.camera()

    def summary(self) -> dict:
        result = {"objects": self.objects(), "camera": self.camera(), "sun": self.sun()}
        other = self.camera("perspective")
        if other != result["camera"]:
            result["camera_view"] = other   # differs until set_camera aligns the two
        return result
