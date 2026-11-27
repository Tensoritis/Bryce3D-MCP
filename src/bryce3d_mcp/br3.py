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

from . import heightfield, keyframes, motion, picture, settings_anim, trimesh
from . import curve as curves
from .ccmf import CompositeFile
from .errors import BryceError

UNIT = 10.24            # Bryce units (as shown in the program) per file unit
PRIMITIVE_SIZE = 2 * UNIT
# How far shapes reach in their own space. Primitives span -1 to 1. A mesh's stored
# points count double, and a terrain reaches half as far up and down as sideways: its
# heights run from -0.5 to 0.5 (a symmetric lattice from -1 to 1). Bryce's mountain
# presets all sit with their centre half their vertical scale above the ground, and
# renders of new meshes and terrains agree.
MESH_UNIT = 2.0
_TERRAIN_EXTENT = (2.0, 1.0, 2.0)
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

# The Sky & Fog tick boxes are bits of one word in the sky record. Each was read off
# the code that fills Bryce's own menu and dialog, and the mode bits agree with all
# of Bryce's sky presets.
_SKY_FLAGS = 268
_ATMOSPHERE_ON, _CLOUDS_ON = 0x1, 0x10
SKY_MODES = {"soft": 0x2, "darker": 0x4, "custom": 0x8, "off": 0}
SKY_SWITCHES = {
    "stratus_clouds": 0x20, "cumulus_clouds": 0x40, "cloud_shadows": 0x1000,
    "link_clouds": 0x80000, "fixed_clouds": 0x400000, "link_sun_to_view": 0x200,
    "blend_haze_with_sun": 0x2000, "blend_fog_with_sun": 0x200000,
    "rainbow": 0x4000, "secondary_rainbow": 0x8000000, "halo_rings": 0x10000,
    "illusion": 0x100000, "moon_image": 0x20000, "moon_phase": 0x40000,
}
_VOLUMETRIC = {"off": 0, "rough": 0x800000, "fine": 0x1000000}
_SKY_FIELDS = {"stars": 868, "comets": 872}     # whole numbers after the bits

# Light settings, read off the code behind Bryce's Edit Light dialog. The tick boxes
# and falloff choices are bits of the object's flag word at 272. The numbers are
# stored smaller than the dialog shows them: intensity by 5, edge softness by 20.
_LIGHT_COLOUR, _LIGHT_INTENSITY, _EDGE_SOFTNESS = 644, 656, 836
_INTENSITY_STEP, _SOFTNESS_STEP = 5.0, 20.0
_VISIBLE_LIGHT, _NO_FALLOFF, _NO_SHADOWS = 0x200, 0x400, 0x800
_INFINITE_LIGHT, _SQUARED_FALLOFF = 0x2000, 0x80000000
LIGHT_FALLOFFS = {"linear": 0, "squared": _SQUARED_FALLOFF, "none": _NO_FALLOFF}
LIGHT_SWITCHES = ("casts_shadows", "infinite", "visible")
LIGHT_SETTINGS = ("color", "intensity", "falloff", "edge_softness", *LIGHT_SWITCHES)
# kind -> (how the edge softness is stored, its setting records)
_SOFT_EDGES = {
    "spot_light": ("<d", ("ConL_eFallOff",)),
    "square_spot_light": ("<f", ("PyrL_eFallOffX", "PyrL_eFallOffY")),
    "parallel_light": ("<f", ("CubeL_eFalloffX", "CubeL_eFalloffY")),
}

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
# Kinds that can be read and edited in a scene but not added to one.
_FACE_TAG = "sqre3dob"
_KIND_OF[_FACE_TAG] = "2d_face"
_SLAB_TAG = "slab3dob"
_KIND_OF[_SLAB_TAG] = "slab"
_KIND_OF[motion.PATH_TAG] = "path"
# The class an object list entry points into. Needed because a 32-bit id is only
# unique within a class: a group and its member list share one, so do a sky preset
# and its sun, and preset libraries reuse old ids across classes.
_TAG_CLASS = {
    "basecamr": "C3dBaseCamera", "plne3dob": "C3dPlane", "cube3dob": "C3dCube",
    "sphr3dob": "C3dSphere", "toru3dob": "C3dTorusType", "cylr3dob": "C3dCylinder",
    "pyra3dob": "C3dPyramid", "cone3dob": "C3dCone", "circ3dob": "C3dCircle",
    "hfld3dob": "C3dHeightField", "symh3dob": "C3dHeightField",
    "tmsh3dob": "C3dTriMeshType2", "sphrlght": "C3dSphereLight",
    _FACE_TAG: "C3dSquare", _SLAB_TAG: "C3dSlab",
    "conelght": "C3dConeLight", "pyrmlght": "C3dPyrLightType",
    "cubelght": "C3dCubeLight", _GROUP_TAG: _GROUP,
    motion.PATH_TAG: motion.PATH_CLASS,
}
_NEVER_LINKED = frozenset((
    _LIST, _ROOT, "CPresetDescr", "objpresetdesc", "matprevcimage", "objprevcimage",
    "skyprevcimage", "brycedocset"))
# The document settings record ("brycedocset"): width and height twice, the picture's
# frame (top, left, bottom, right), then the Render Options menu. Unlike most of the
# file it is stored big-endian.
_DOCUMENT_BYTES = 416
_DOC_FRAME, _DOC_VIEW, _DOC_FLAGS, _DOC_REPORT = 16, 32, 56, 88
_DOC_ANTIALIASING, _DOC_OPTIMISATION = 80, 84
# Timeline, big-endian floats in seconds (the second is frames per second).
_DOC_DURATION, _DOC_FPS, _DOC_TIME, _DOC_RANGE = 60, 64, 68, 72
MAX_FRAME = 100_000
# channel -> (track, doubles in its value)
ANIMATED = {"position": ("Obj_ePosition", 3), "rotation": ("Obj_eOrient", 4),
            "scale": ("Obj_eScale", 3)}
_POSE_TRACKS = (("Obj_ePosition", 3), ("Obj_eOrigin", 3), ("Obj_eScale", 3),
                ("Obj_eOrient", 4))
DOCUMENT_SIZES = {"default": (480, 360), "small": (320, 240), "standard": (640, 480),
                  "super_16mm": (640, 384), "cinemascope": (640, 240), "photo": (768, 512)}
ANTIALIASING = {"off": 0, "normal": 1, "fine_art": 2}
OPTIMISATION = {"off": 0, "low": 1, "high": 2}
RENDER_TYPES = {"normal": 0, "mask": 0x400, "distance": 0x800, "altitude": 0x1000}


def _name_of(table: dict, value: int):
    return next((name for name, number in table.items() if number == value), value)


def _choice(what: str, name, table: dict) -> int:
    if name not in table:
        raise BryceError(f"{what} must be one of {', '.join(table)}")
    return table[name]


def _picture_size(size) -> tuple[int, int]:
    if isinstance(size, str):
        return _choice("size", size, DOCUMENT_SIZES)
    if not (isinstance(size, (list, tuple)) and len(size) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and 64 <= v <= 4096
                    for v in size)):
        raise BryceError("size must be [width, height], each 64 to 4096, or one of "
                         + ", ".join(DOCUMENT_SIZES))
    return size[0], size[1]


_SLOT_CLASS = {280: _MATERIAL, 492: "Obj_ePosition", 496: "Obj_eOrient",
               500: "Obj_eScale", 504: "Obj_eShear", 508: "Obj_eOrigin"}
_TRACK_SLOT = {name: slot for slot, name in _SLOT_CLASS.items() if slot >= 492}
# Boolean role of an object inside a group: bits of the flag word at 272.
_FLAGS, BOOLEAN_BITS = 272, {"neutral": 0, "positive": 0x08, "negative": 0x10,
                             "intersect": 0x20}
# In Bryce's own models a part with a boolean role inside a group also carries these
# two bits (all 179 such parts in the bundled presets). Without them a boolean
# group did not appear in a real render. Groups have their own two: one on every
# boolean group, one on a group that stands directly in the scene.
_IN_GROUP, _HAS_ROLE = 0x10000000, 0x40
_ROLE_MASK = 0x38 | _HAS_ROLE
_TOP_GROUP, _BOOLEAN_GROUP = 0x04000000, 0x00020000
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
# The rest of a procedural texture's placement. How it wraps is a number choosing
# one branch of Bryce's mapping code; the order is the one in Bryce's help file and
# agrees with what each branch computes. Rotation is a quaternion, like an object's.
_TEXTURE_MAPPING, _TEXTURE_OFFSET, _TEXTURE_TURN = 268, 300, 324
TEXTURE_MAPPINGS = (
    "object_space", "world_space", "parametric", "parametric_scaled", "world_top",
    "spherical", "cylindrical", "reflection_map", "random", "object_top", "object_front")
# Four sets of three colours, one byte each for blue, green and red, then a zero.
_TEXTURE_COLOUR_SETS = (572, 668, 940, 1212)
# Four bytes in front of a channel's texture link name the kind of texture it
# points at. Without them the link cannot be followed. Every textured channel in
# Bryce's own files has them, with this bit also set in the material's flag word.
_TEXTURE_TAGS = {"C3dTexture": b"xtrb", "CPictTexture": b"xtcp"}      # 'brtx', 'pctx'
_MATERIAL_FLAGS = 268
# A picture texture shares the placement fields of a procedural one, except that its
# scale says how often the picture repeats less one, in seventeenths. With bit 2 of
# the word at 272 set it says instead how much of one picture is left out.
_PICTURE_FLAGS, _PICTURE_ZOOMS_IN = 272, 0x2
_PICTURE_IMAGE = 504           # link to the stored picture
_PICTURE_STEPS = 17.0
_USES_TEXTURES = 0x40
_SHOWN = ("color", "diffuse", "ambient", "specular", "transparency", "reflection",
          "bump", "refraction")


def _mac_list(payload: bytes) -> bool:
    """Whether an object list is big-endian: version 1, then 0 where a list saved
    on a PC has 1."""
    return payload[:8] == b"\0\0\0\1\0\0\0\0"


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


class Br3(settings_anim.SettingAnimation, motion.PathObjects):
    UNIT = UNIT

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

    def mac_model(self, key: int) -> bool:
        """Whether an object preset was saved on a Mac."""
        return _mac_list(self._payload(("C3dRootObjList", key)))

    def _list_items(self, ref: Ref) -> list[tuple[str, Ref, bytes]]:
        """(tag, record, raw 16 bytes) for each entry of an object list."""
        payload = self._payload(ref)
        if _mac_list(payload):
            raise BryceError("this model was saved on a Mac, with its bytes in the "
                             "other order; reading those is not supported yet")
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
        # A stored picture is compressed data: nothing in it is a link.
        if name in (_ROOT, "C3dTexture", "C3dCImage") or not (name.startswith("C3d")
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
        self._write_value(track, _VALUE, struct.pack(f"<{len(values)}d", *values))

    def _write_value(self, track: Ref, offset: int, data: bytes) -> None:
        """Write a value, given by its offset in a one-key record, into every key."""
        payload = self._payload(track)
        step = keyframes.stride(payload)
        for i in range(keyframes.count(payload)):
            self._write(track, offset + i * step, data)

    def _setting(self, track: Ref):
        """Decode a one-value setting record by its size."""
        payload = self._payload(track)
        size = keyframes.single_size(payload)
        offset = 220 if size in (232, 248) else _VALUE
        if size in (248, 204):
            form, code, number = "colour", "f", 3
        elif size in (232, 196):
            form, code, number = "float", "f", 1
        elif size == 200:
            form, code, number = "double", "d", 1
        elif size >= _VALUE + 24 and track[0].endswith("Vctr"):
            form, code, number = "vector", "d", 3
        else:
            return offset, None, None
        if keyframes.count(payload) > 1:         # as it stands at the time marker
            entries = keyframes.values(payload, number, code, offset - keyframes.HEAD)
            try:
                moment = self.timeline()["time"]
            except BryceError:                   # a preset library has no timeline
                moment = 0.0
            value = keyframes.at(entries, moment, points=keyframes.time_curve(payload))
        else:
            value = struct.unpack_from(f"<{number}{code}", payload, offset)
        return offset, form, value if number == 3 else value[0]

    def _set_setting(self, track: Ref, value, label: str) -> None:
        offset, form, _ = self._setting(track)
        if form == "colour":
            self._write_value(track, offset, struct.pack("<3f", *_colour(value, label)))
        elif form == "float":
            self._write_value(track, offset, struct.pack("<f", _number(value, label)))
        elif form == "double":
            self._write_value(track, offset, struct.pack("<d", _number(value, label)))
        elif form == "vector":
            self._write_value(track, offset, struct.pack("<3d", *_triple(value, label)))
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
            details = {self._texture_name(ref): self.texture_details(ref)
                       for ref in used.values() if self.texture_scale_of(ref) is not None}
            if details:
                out["texture_details"] = details
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

    def texture_details(self, ref: Ref) -> dict:
        """How a procedural texture is placed on a surface, and its colours."""
        payload = self._payload(ref)
        mapping = struct.unpack_from("<I", payload, _TEXTURE_MAPPING)[0]
        details = {
            "mapping": (TEXTURE_MAPPINGS[mapping] if mapping < len(TEXTURE_MAPPINGS)
                        else f"unknown ({mapping})"),
            "offset": [round(v, 5) + 0.0 for v in
                       struct.unpack_from("<3d", payload, _TEXTURE_OFFSET)],
        }
        turn = struct.unpack_from("<4d", payload, _TEXTURE_TURN)
        if sum(v * v for v in turn) > 0.5:
            details["rotation"] = [round(v, 2) + 0.0 for v in euler_from_quaternion(turn)]
        details["color_sets"] = [
            ["#" + payload[start + 4 * i:start + 4 * i + 3][::-1].hex() for i in range(3)]
            for start in _TEXTURE_COLOUR_SETS]
        return details

    def _procedural_textures(self, name: str, channels: list[str] | None) -> set[Ref]:
        """The procedural textures on some channels (default: all textured ones),
        after checking that each can be edited."""
        material = self._own_material(name)
        used = self._textures(material)
        wanted = list(used) if channels is None else channels
        missing = [c for c in wanted if c not in used]
        if missing or not wanted:
            raise BryceError(f"no texture on {', '.join(missing) or 'any channel'} of "
                             f"{name!r}; textured channels: {', '.join(used) or 'none'}")
        refs = {used[c] for c in wanted}
        for ref in refs:
            if self.texture_scale_of(ref) is None:
                raise BryceError(f"{self._texture_name(ref)!r} cannot be changed (it is "
                                 "not a procedural 3D texture)")
        return refs

    def edit_texture(self, name: str, channels: list[str] | None = None, *,
                     mapping: str | None = None, scale=None, offset=None, rotation=None,
                     replace_colors: dict | None = None,
                     color_sets: dict | None = None) -> dict:
        """Change how a material's procedural textures wrap and what colours they use.
        A texture used by several channels changes in all of them."""
        if all(v is None for v in (mapping, scale, offset, rotation, replace_colors,
                                   color_sets)):
            raise BryceError("give at least one of mapping, scale, offset, rotation, "
                             "replace_colors or color_sets")
        writes: list[tuple[int, bytes]] = []
        if mapping is not None:
            if mapping not in TEXTURE_MAPPINGS:
                raise BryceError(f"mapping must be one of {', '.join(TEXTURE_MAPPINGS)}")
            writes.append((_TEXTURE_MAPPING,
                           struct.pack("<I", TEXTURE_MAPPINGS.index(mapping))))
        if scale is not None:
            values = ([_number(scale, "scale")] * 3
                      if isinstance(scale, (int, float)) and not isinstance(scale, bool)
                      else list(_triple(scale, "scale")))
            if not all(1e-3 <= v <= 1e4 for v in values):
                raise BryceError("texture scale must be between 0.001 and 10000")
            writes.append((_TEXTURE_SCALE, struct.pack("<3d", *values)))
        if offset is not None:
            values = _triple(offset, "offset")
            if max(map(abs, values)) > 1e4:
                raise BryceError("texture offset must be within 10000")
            writes.append((_TEXTURE_OFFSET, struct.pack("<3d", *values)))
        if rotation is not None:
            writes.append((_TEXTURE_TURN,
                           struct.pack("<4d", *quaternion_from_euler(rotation))))

        def packed(value) -> bytes:
            red, green, blue = (round(c * 255) for c in _colour(value, "texture color"))
            return bytes((blue, green, red, 0))

        slots: dict[int, bytes] = {}              # (set, place) -> new colour
        if color_sets is not None:
            if not isinstance(color_sets, dict) or not color_sets:
                raise BryceError('color_sets is like {"2": ["#ff0000", null, "#000000"]}')
            for number, colours in color_sets.items():
                if str(number) not in ("1", "2", "3", "4") \
                        or not isinstance(colours, list) or len(colours) != 3:
                    raise BryceError("color_sets takes sets 1 to 4, each a list of three "
                                     "colors (null keeps one)")
                start = _TEXTURE_COLOUR_SETS[int(number) - 1]
                slots.update({start + 4 * i: packed(c) for i, c in enumerate(colours)
                              if c is not None})
        if replace_colors is not None and (not isinstance(replace_colors, dict)
                                           or not replace_colors):
            raise BryceError('replace_colors is like {"#ffffff": "#2060ff"}')

        refs = self._procedural_textures(name, channels)
        planned = []
        for ref in sorted(refs):                  # check everything before writing
            changes = dict(slots)
            payload = self._payload(ref)
            for old, new in (replace_colors or {}).items():
                wanted = packed(old)[:3]
                places = [start + 4 * i for start in _TEXTURE_COLOUR_SETS
                          for i in range(3)
                          if payload[start + 4 * i:start + 4 * i + 3] == wanted]
                if not places:
                    have = sorted({c for row in self.texture_details(ref)["color_sets"]
                                   for c in row})
                    raise BryceError(f"{self._texture_name(ref)!r} has no color {old}; "
                                     f"it has {', '.join(have)}")
                changes.update({place: packed(new) for place in places})
            planned.append((ref, writes + sorted(changes.items())))
        for ref, todo in planned:
            for where, data in todo:
                self._write(ref, where, data)
        return self.material(self.find(name)[1])

    def scale_texture(self, name: str, factor, channels: list[str] | None = None) -> dict:
        """Multiply the scale numbers of the textures on an object's material."""
        factors = ([_number(factor, "factor")] * 3
                   if isinstance(factor, (int, float)) and not isinstance(factor, bool)
                   else list(_triple(factor, "factor")))
        if not all(1e-3 <= f <= 1e3 for f in factors):
            raise BryceError("texture scale factors must be between 0.001 and 1000")
        for ref in self._procedural_textures(name, channels):
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
        self._refuse_animated_settings(self._material_channels(material, set(settings)),
                                       f"the material of {name!r}")
        released = set()
        for label, value in settings.items():
            block, offset, class_name = MATERIAL_CHANNELS[label]
            if label in MATERIAL_COLOURS:
                data = struct.pack("<3f", *_colour(value, label))
            else:
                data = struct.pack("<f", _number(value, label))
            texture = self._textures(material).get(label)
            if texture is not None:                  # a plain value replaces a texture
                self._write(material, block - 4, bytes(4))
                self._write(material, block, bytes(4))
                self._write(material, block + 20, bytes(4))
                released.update(self._closure(texture))
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
        material = self._link_texture(name, self._copy(source, ref), channels)
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

    def _set_record_name(self, ref: Ref, name: str) -> None:
        """Rename a texture or picture, in the record's row and, for a texture, in it."""
        row = self._row(ref)
        table = self._rows[ref[0]]
        changed = row[:12] + heightfield.pack_name(name, len(row) - 12)
        table[table.index(row)] = changed
        self.file.set_table(*self._classes[ref[0]], table)
        self._ids[ref[1]][ref[0]] = changed
        if ref[0] == "CPictTexture":
            self._write(ref, 8, heightfield.pack_name(name, 32))

    def picture_of(self, ref: Ref):
        """The stored picture of a picture texture, as an image."""
        stored = self._linked(ref, "C3dCImage")
        if stored is None:
            raise BryceError("the picture texture has no picture")
        second = struct.unpack_from(">I", self._row(stored), 8)[0]
        return picture.decode(self._payload(stored),
                              *picture.size_of(self.file.read(second)))

    def set_picture(self, name: str, image, channels: list[str] | None = None, *,
                    sources=(), repeat=1, mapping: str = "parametric",
                    label: str = "Picture") -> dict:
        """Put a picture on material channels. `image` is a PIL image; its
        transparency is kept as the picture's alpha."""
        channels = ["color", "ambient_color"] if channels is None else channels
        usable = [c for c, spec in MATERIAL_CHANNELS.items() if spec[0] is not None]
        if not channels or any(c not in usable for c in channels):
            raise BryceError(f"channels must be some of {', '.join(usable)}")
        if mapping not in TEXTURE_MAPPINGS:
            raise BryceError(f"mapping must be one of {', '.join(TEXTURE_MAPPINGS)}")
        times = ([_number(repeat, "repeat")] * 3
                 if isinstance(repeat, (int, float)) and not isinstance(repeat, bool)
                 else list(_triple(repeat, "repeat")))
        if not all(0.01 <= t <= 1000 for t in times):
            raise BryceError("repeat must be between 0.01 and 1000")
        zoom = min(times) < 1
        if zoom and max(times) > 1:
            raise BryceError("repeat numbers must be all 1 or more, or all 1 or less")
        stored = [1 - t if zoom else (t - 1) / _PICTURE_STEPS for t in times]
        self._own_material(name)                   # fail before copying anything
        for source in (self, *sources):
            if source is None:
                continue
            donor = next((("CPictTexture", struct.unpack_from(">I", row)[0])
                          for row in source._rows.get("CPictTexture", [])), None)
            if donor is not None and source._linked(donor, "C3dCImage") is not None:
                break
        else:
            raise BryceError("no picture texture to copy: this needs Bryce's Tutorial.obp "
                             "preset file, or a template scene that uses a picture")
        copy = self._copy(source, donor)
        holder = self._linked(copy, "C3dCImage")
        row = self._row(holder)
        data_entry, second = struct.unpack_from(">II", row, 4)
        self.file.replace(data_entry, picture.encode(image))
        self.file.replace(second, picture.description(*image.size))
        label = label.encode("ascii", "replace").decode()[:30] or "Picture"
        self._set_record_name(holder, label)
        self._set_record_name(copy, label)
        flags = struct.unpack_from("<I", self._payload(copy), _PICTURE_FLAGS)[0]
        flags = flags | _PICTURE_ZOOMS_IN if zoom else flags & ~_PICTURE_ZOOMS_IN
        for offset, data in (
                (_TEXTURE_MAPPING, struct.pack("<I", TEXTURE_MAPPINGS.index(mapping))),
                (_PICTURE_FLAGS, struct.pack("<I", flags)),
                (_TEXTURE_SCALE, struct.pack("<3d", *stored)),
                (_TEXTURE_OFFSET, struct.pack("<3d", 0, 0, 0)),
                (_TEXTURE_TURN, struct.pack("<4d", 0, 0, 0, 1))):
            self._write(copy, offset, data)
        self._link_texture(name, copy, channels)
        return self.material(self.find(name)[1])

    def _link_texture(self, name: str, texture: Ref, channels: list[str]) -> Ref:
        """Make channels of an object's material draw from a texture record."""
        material = self._own_material(name)
        replaced = {ref for channel, ref in self._textures(material).items()
                    if channel in channels and ref != texture}
        flags = struct.unpack_from("<I", self._payload(material), _MATERIAL_FLAGS)[0]
        self._write(material, _MATERIAL_FLAGS, struct.pack("<I", flags | _USES_TEXTURES))
        for channel in channels:
            block = MATERIAL_CHANNELS[channel][0]
            self._write(material, block - 4, _TEXTURE_TAGS[texture[0]])
            self._write(material, block, struct.pack("<I", texture[1]))
            self._write(material, block + 20, struct.pack("<I", _TEXTURED_FLAG))
        replaced -= set(self._textures(material).values())
        if replaced:
            self._discard({part for ref in replaced for part in self._closure(ref)})
        return self._own_material(name)

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
        if self.animated_channels(ref):          # the pose at the time marker
            return self._cached_transform(ref)
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
        if self.animated_channels(ref):
            self._refuse_reshaped_path(ref, scale, rotation)
            self._place_animated(ref, centre, scale, rotation, library)
            return
        if ref[0] == motion.PATH_CLASS:
            self._refuse_reshaped_path(ref, scale, rotation)
            self._move_path(ref, centre, library)
            return
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
        self._write_pose(ref, centre, scale, rotation)

    def _refuse_reshaped_path(self, ref: Ref, scale, rotation) -> None:
        """A path's points are saved already resized and turned."""
        if ref[0] != motion.PATH_CLASS:
            return
        # One of Bryce's presets has settings that disagree with what it draws, so
        # either of the two counts as unchanged.
        for _, _, size, turn in (self.transform(ref), self._cached_transform(ref)):
            if all(abs(a - b) <= 1e-6 for a, b in zip(scale, size)) \
                    and abs(sum(a * b for a, b in zip(rotation, turn))) >= 1 - 1e-9:
                return
        raise BryceError("a path can be moved but not resized or turned; make a new "
                             "one with the path operation, or use scale and turn in "
                             "follow_path")

    def _move_path(self, ref: Ref, centre, library: "Br3 | None") -> None:
        """Move a path, leaving everything about its shape as Bryce saved it."""
        self._ensure_track(ref, "Obj_ePosition", library)
        self._set_vector(ref, "Obj_ePosition", centre)
        if self._linked(ref, "Obj_eOrigin") is not None:
            self._set_vector(ref, "Obj_eOrigin", (0.0, 0.0, 0.0))
        self._write(ref, _OFFSET, struct.pack("<3d", 0.0, 0.0, 0.0))
        self._write(ref, _TRANSLATION, struct.pack("<3d", *centre))

    def _write_pose(self, ref: Ref, centre, scale, rotation) -> None:
        """The copy of an object's placement that Bryce draws from."""
        rows = rotation_matrix(rotation)
        self._write(ref, _OFFSET, struct.pack("<3d", 0.0, 0.0, 0.0))
        self._write(ref, _MATRIX, struct.pack(
            "<9d", *(rows[i][j] * scale[i] for i in range(3) for j in range(3))))
        self._write(ref, _TRANSLATION, struct.pack("<3d", *centre))

    # -------------------------------------------------------------- timeline
    def _track_values(self, ref: Ref, class_name: str, number: int):
        track = self._linked(ref, class_name)
        return None if track is None else keyframes.values(self._payload(track), number)

    def animated_channels(self, ref: Ref) -> list[str]:
        """Which of position, rotation and scale have more than one key."""
        found = []
        for channel, (class_name, _) in ANIMATED.items():
            track = self._linked(ref, class_name)
            if track is not None and keyframes.count(self._payload(track)) > 1:
                found.append(channel)
        return found

    def _track_curve(self, ref: Ref, class_name: str):
        return keyframes.time_curve(self._payload(self._linked(ref, class_name)))

    def _pose_at(self, ref: Ref, time: float):
        """(centre, scale, rotation) at `time`, from the keys."""
        cached = self._cached_transform(ref)
        fallback = (cached[0], (0.0, 0.0, 0.0), cached[2], cached[3])
        parts = []
        for (class_name, number), otherwise in zip(_POSE_TRACKS, fallback):
            entries = self._track_values(ref, class_name, number)
            parts.append(tuple(otherwise) if entries is None else
                         keyframes.at(entries, time, class_name == "Obj_eOrient",
                                      self._track_curve(ref, class_name)))
        position, origin, scale, rotation = parts
        return world_centre(position, origin, scale, rotation), list(scale), rotation

    def _offset_at(self, ref: Ref, time: float, scale, rotation) -> list[float]:
        """What the local origin adds to the stored position at `time`."""
        entries = self._track_values(ref, "Obj_eOrigin", 3)
        origin = ((0.0, 0.0, 0.0) if entries is None else keyframes.at(
            entries, time, points=self._track_curve(ref, "Obj_eOrigin")))
        return world_centre((0.0, 0.0, 0.0), origin, scale, rotation)

    def animation(self, ref: Ref) -> dict:
        """The keys of each animated channel, in the units the other tools use."""
        fps = self.timeline()["fps"]
        found = {}
        for channel in self.animated_channels(ref):
            class_name, number = ANIMATED[channel]
            listed = []
            for time, value in self._track_values(ref, class_name, number):
                if channel == "position":
                    _, scale, rotation = self._pose_at(ref, time)
                    offset = self._offset_at(ref, time, scale, rotation)
                    shown = [round((v + o) * UNIT, 3) + 0.0 for v, o in zip(value, offset)]
                elif channel == "rotation":
                    shown = [round(v, 2) + 0.0 for v in euler_from_quaternion(value)]
                else:
                    shown = [round(v, 4) for v in value]
                listed.append({"frame": round(time * fps), "time": round(time, 4),
                               "value": shown})
            found[channel] = listed
        return found

    def timeline(self) -> dict:
        _, data = self._document_record()
        duration, fps, time, start, end = struct.unpack_from(">5f", data, _DOC_DURATION)
        if not 0 < fps <= 240:
            raise BryceError("corrupt scene: impossible frame rate")
        return {"fps": round(fps, 3), "duration": round(duration, 4),
                "duration_frames": round(duration * fps),
                "time": round(time, 4), "frame": round(time * fps),
                "working_range": [round(start, 4), round(end, 4)]}

    def _timeline_floats(self):
        entry, data = self._document_record()
        return entry, bytearray(data), list(struct.unpack_from(">5f", data, _DOC_DURATION))

    def _moment(self, item: dict, fps: float, what: str) -> int:
        """The frame named by an item's "frame" or "time"."""
        if ("frame" in item) == ("time" in item):
            raise BryceError(f"{what} needs either frame or time (seconds)")
        if "frame" in item:
            frame = item["frame"]
            if not isinstance(frame, int) or isinstance(frame, bool):
                raise BryceError(f"{what}: frame must be a whole number")
        else:
            frame = round(_number(item["time"], f"{what}: time") * fps)
        if not 0 <= frame <= MAX_FRAME:
            raise BryceError(f"{what}: frames run from 0 to {MAX_FRAME}")
        return frame

    def set_time(self, moment: dict) -> dict:
        """Move Bryce's time marker and pose every animated object to match."""
        entry, data, (duration, fps, *_) = self._timeline_floats()
        time = self._moment(moment, fps, "time") / fps
        if time > duration + 0.5 / fps:
            raise BryceError(f"the animation ends at frame {round(duration * fps)}; "
                             "set a longer duration with the keyframes operation")
        struct.pack_into(">f", data, _DOC_TIME, time)
        self.file.replace(entry, bytes(data))
        self._repose(time)
        self._pose_settings(time)
        return self.timeline()

    def _repose(self, time: float, only: Ref | None = None) -> None:
        """Write the pose at `time` for animated objects."""
        for _, ref in self._every_object():
            if (only is None or ref == only) and self.animated_channels(ref):
                self._write_pose(ref, *self._pose_at(ref, time))

    def curves_of(self, ref: Ref) -> dict:
        found = {}
        for channel in self.animated_channels(ref):
            points = keyframes.time_curve(self._payload(
                self._linked(ref, ANIMATED[channel][0])))
            if points:
                found[channel] = curves.describe(points)
        return found

    # -- what can be keyed besides object poses ----------------------------------------
    def _camera_channels(self) -> list:
        views = [k for k in (self._camera_key("perspective"), self._camera_key("director"))
                 if k is not None] or [self._first(_CAMERA)]
        owners = [k for k in views if self._linked(k, "Obj_ePosition") is not None]
        if not owners:
            return []
        # Both views show the same shot; one of them may have no tracks of its own.
        mirrors = tuple(k for k in views if k not in owners)

        def position(key, v):
            self._write(key, _CAMERA_OFFSET, struct.pack("<3d", 0.0, 0.0, 0.0))
            self._write(key, _CAMERA_TRANSLATION, struct.pack("<3d", *v))

        def orientation(key, v):
            matrix = [n for row in camera_matrix(*v) for n in row]
            self._write(key, _CAMERA_MATRIX, struct.pack("<9d", *matrix))
            self._write(key, _CAMERA_ANGLES, struct.pack("<3d", *v))

        def fov(key, v):
            self._write(key, _CAMERA_FOV, struct.pack("<d", *v))

        def angles(v):
            return {"heading": round(math.degrees(v[1]), 2) + 0.0,
                    "pitch": round(-math.degrees(v[0]), 2) + 0.0,
                    "bank": round(math.degrees(v[2]), 2) + 0.0}

        def make(name, class_name, number, cache, show):
            return settings_anim.Channel(name, class_name, owners, _VALUE, "d", number,
                                         cache, show, mirrors=mirrors)
        return [make("position", "Obj_ePosition", 3, position,
                     lambda v: [round(n * UNIT, 3) + 0.0 for n in v]),
                make("orientation", "Cam_eCraOrient", 3, orientation, angles),
                make("fov", "Cam_eFOV", 1, fov, lambda v: round(math.degrees(v[0]), 2))]

    def _sun_channels(self) -> list:
        owner = self._first("C3dSunLight")
        tracks = self._scene_settings("C3dSunLight", "SunL_e")
        out = []
        for name, label, form, shape, show in (
                ("direction", "SunVctr", "d", "<3d", settings_anim.sun_angles),
                ("disc_color", "DiscClr", "f", "<3f", settings_anim.hex_colour),
                ("halo_color", "HaloClr", "f", "<3f", settings_anim.hex_colour)):
            if label in tracks:
                def cache(key, v, offset=SUN_CACHE[label], shape=shape):
                    self._write(key, offset, struct.pack(shape, *v))
                out.append(settings_anim.Channel(
                    name, "SunL_e" + label, [owner], _VALUE, form, 3, cache, show))
        return out

    def _sky_channels(self, only=None) -> list:
        tracks = self._scene_settings("C3dBaseSky", "Sky_e")
        out = []
        for label, track in sorted(tracks.items()):
            if only is not None and label not in only:
                continue
            offset, form, _ = self._setting(track)
            if form is None:
                continue
            colour = form == "colour"
            cached = SKY_CACHE.get(label)

            def cache(key, v, offset=cached, colour=colour):
                if offset is not None:
                    self._write(key, offset, struct.pack("<3f" if colour else "<d", *v))
            out.append(settings_anim.Channel(
                label, "Sky_e" + label, [self._first("C3dBaseSky")], offset,
                "d" if form == "double" else "f", 3 if colour else 1, cache,
                settings_anim.hex_colour if colour else settings_anim._plain))
        return out

    def _material_channels(self, material: Ref, only=None) -> list:
        out = []
        for label, (_, cached, class_name) in MATERIAL_CHANNELS.items():
            if only is not None and label not in only:
                continue
            colour = label in MATERIAL_COLOURS
            track = self._linked(material, class_name)
            if track is None:
                offset = 220
            else:
                offset, form, _ = self._setting(track)
                if form not in ("colour", "float"):
                    continue

            def cache(key, v, offset=cached, colour=colour):
                self._write(key, offset, struct.pack("<3f" if colour else "<f", *v))
            out.append(settings_anim.Channel(
                label, class_name, [material], offset, "f", 3 if colour else 1, cache,
                settings_anim.hex_colour if colour else settings_anim._plain,
                settings_anim.MATERIAL_SLOTS[class_name]))
        return out

    def _light_channels(self, light: Ref) -> list:
        def colour(key, v):
            self._write(key, _LIGHT_COLOUR, struct.pack("<3f", *v))

        def intensity(key, v):
            self._write(key, _LIGHT_INTENSITY, struct.pack("<d", *v))

        make = settings_anim.Channel
        return [make("color", "Light_C", [light], _VALUE, "f", 3, colour,
                     settings_anim.hex_colour, settings_anim.LIGHT_SLOTS["Light_C"]),
                make("intensity", "Light_Int", [light], _VALUE, "d", 1, intensity,
                     lambda v: round(v[0] * _INTENSITY_STEP, 2),
                     settings_anim.LIGHT_SLOTS["Light_Int"])]

    def _every_object(self):
        waiting = list(self._objects())
        while waiting:
            tag, ref = waiting.pop(0)
            if ref[0] == _GROUP:
                waiting.extend(self._members(ref))
            yield tag, ref

    def _setting_groups(self):
        """(target, object name or None, channels) for everything keyable."""
        if self._first(_CAMERA) is not None:
            yield "camera", None, self._camera_channels()
        if self._first("C3dSunLight") is not None:
            yield "sun", None, self._sun_channels()
        if self._first("C3dBaseSky") is not None:
            yield "sky", None, self._sky_channels()
        for tag, ref in self._every_object():
            material = self._linked(ref, _MATERIAL)
            if material is not None:
                yield "material", self._name(ref), self._material_channels(material)
            if ref[0].endswith(("Light", "LightType")):
                yield "light", self._name(ref), self._light_channels(ref)

    def _target_channels(self, target: str, name: str | None, fields=None):
        """(channels by name, object ref or None) for one keyframes target."""
        if target in ("camera", "sun", "sky"):
            if name:
                raise BryceError(f"a {target} track needs no name")
            channels = {"camera": self._camera_channels, "sun": self._sun_channels,
                        "sky": self._sky_channels}[target]()
        else:
            if not name:
                raise BryceError(f"a {target} track needs the object's name")
            kind, ref = self.find(name)
            if target == "light":
                if not kind.endswith("light"):
                    raise BryceError(f"{name!r} is not a light")
                channels = self._light_channels(ref)
            else:
                channels = self._material_channels(self._own_material(name))
        if not channels:
            raise BryceError(f"this scene has no {target} tracks")
        return {c.name: c for c in channels}

    def _stored_setting(self, channel) -> tuple:
        """A material setting that has no track yet, from the material record."""
        payload = self._payload(channel.owners[0])
        offset = MATERIAL_CHANNELS[channel.name][1]
        return struct.unpack_from(f"<{channel.number}f", payload, offset)

    def _key_settings(self, target, name, keys, duration, curve, library) -> dict:
        channels = self._target_channels(target, name)
        fields = {"camera": settings_anim.CAMERA_FIELDS,
                  "sun": settings_anim.SUN_FIELDS}.get(target)
        if fields is None:
            fields = {c: c for c in channels}
        _, _, (old_duration, fps, now, *_) = self._timeline_floats()
        if target == "light":
            ref = self.find(name)[1]
            shown = self.light(self.find(name)[0], ref)
            present = {"color": _colour(shown["color"], "color"),
                       "intensity": (shown["intensity"] / _INTENSITY_STEP,)}
        else:
            ref = None

        by_frame: dict[int, dict] = {}
        for number, item in enumerate(keys or []):
            if not isinstance(item, dict):
                raise BryceError("each key is an object with frame or time and values")
            unknown = sorted(set(item) - {"frame", "time", *fields})
            if unknown:
                raise BryceError(f"key {number}: unknown {target} setting(s) "
                                 f"{', '.join(unknown)}; use {', '.join(fields)}")
            frame = self._moment(item, fps, f"key {number}")
            values = {k: v for k, v in item.items() if k in fields}
            if not values:
                raise BryceError(f"key {number} sets nothing")
            slot = by_frame.setdefault(frame, {})
            again = sorted(set(slot) & set(values))
            if again:
                raise BryceError(f"two {again[0]} keys are on frame {frame}")
            slot.update(values)

        named = {fields[k] for values in by_frame.values() for k in values}
        if isinstance(curve, dict):
            stray = sorted(set(curve) - set(channels))
            if stray:
                raise BryceError(f"curve names unknown channel(s) {', '.join(stray)}; "
                                 f"use {', '.join(channels)}")
        if not named:
            if curve is None:
                raise BryceError("give keys, a curve, or both")
            moving = [c for c in channels if self._is_animated(channels[c])
                      and (not isinstance(curve, dict) or c in curve)]
            if not moving:
                raise BryceError("a curve needs a channel with two keys or more")
            named = set(moving)

        if target == "camera" and not any(map(self._is_animated, channels.values())):
            self.set_camera()          # fold away offsets and align both cameras
        for channel_name in named:
            if self._channel_record(channels[channel_name]) is None:
                value = (present[channel_name] if target == "light"
                         else self._stored_setting(channels[channel_name]))
                self._seed_track(channels[channel_name], library, value)
        channels = self._target_channels(target, name)

        entries: dict[str, list] = {}
        if by_frame:
            frames = sorted(by_frame)
            if target in ("camera", "sun"):
                build = self._camera_entries if target == "camera" else self._sun_entries
                entries = build(channels, by_frame, frames, fps, now)
            else:
                for frame in frames:
                    for key, value in by_frame[frame].items():
                        label = f"frame {frame} {key}"
                        channel = channels[key]
                        if channel.number == 3:
                            stored = tuple(_colour(value, label))
                        else:
                            number = _number(value, label)
                            if target == "light":
                                if not 0 <= number <= 100:
                                    raise BryceError(f"{label} must be from 0 to 100")
                                number /= _INTENSITY_STEP
                            stored = (number,)
                        entries.setdefault(key, []).append((frame / fps, stored))
        if target == "material":
            textured = sorted(set(entries) & set(self._textures(self._own_material(name))))
            if textured:
                raise BryceError(f"{', '.join(textured)} is driven by a texture; set a "
                                 "plain value with set_material first, then key it")

        for channel_name in sorted(named):
            spec = curve.get(channel_name) if isinstance(curve, dict) else curve
            self._write_channel(channels[channel_name], entries.get(channel_name), spec)

        last = max((f for f in by_frame), default=0) / fps
        longest = self._extend_timeline(last if len(by_frame) > 1 else 0.0, duration)
        now = min(now, longest)
        for channel_name in named:
            channel = channels[channel_name]
            self._show_channel(channel, self._channel_value(channel, now))
        result = {"target": target, "animation": {
            c: self._channel_report(channels[c], fps) for c in sorted(named)},
            "timeline": self.timeline()}
        if name:
            result["name"] = name
        if duration is not None and longest > duration + 1e-4:
            result["note"] = "the duration was lengthened to reach the last key"
        return result

    def _camera_entries(self, channels, by_frame, frames, fps, now) -> dict:
        view = self.camera()
        entries: dict[str, list] = {}
        for frame in frames:
            item = by_frame[frame]
            if "position" in item:
                position = _triple(item["position"], f"frame {frame} position")
                entries.setdefault("position", []).append(
                    (frame / fps, tuple(v / UNIT for v in position)))
            if "fov" in item:
                fov = _number(item["fov"], f"frame {frame} fov")
                if not 1 <= fov <= 170:
                    raise BryceError("fov must be 1-170 degrees")
                entries.setdefault("fov", []).append((frame / fps, (math.radians(fov),)))

        # Heading, pitch and bank share one track: a key that leaves one out keeps
        # it from the key before.
        held = {"heading": view["heading"], "pitch": view["pitch"], "bank": view["bank"]}
        held["heading"] = (held["heading"] + 180) % 360 - 180
        first = True
        for frame in frames:
            item = by_frame[frame]
            turned = [k for k in ("heading", "pitch", "bank", "look_at") if k in item]
            if not turned:
                continue
            if "look_at" in item:
                if "heading" in item or "pitch" in item:
                    raise BryceError("give look_at or heading/pitch, not both")
                if "position" in item:
                    eye = _triple(item["position"], "position")
                elif entries.get("position"):
                    eye = [v * UNIT for v in
                           keyframes.at(entries["position"], frame / fps)]
                elif self._is_animated(channels["position"]):
                    eye = [v * UNIT for v in
                           self._channel_value(channels["position"], frame / fps)]
                else:
                    eye = view["position"]
                target = _triple(item["look_at"], f"frame {frame} look_at")
                dx, dy, dz = (b - a for a, b in zip(eye, target))
                if not (dx or dz):
                    raise BryceError("look_at must not be straight above or below the camera")
                heading = math.degrees(math.atan2(dx, dz))
                if not first:                          # turn the short way round
                    heading += 360 * round((held["heading"] - heading) / 360)
                held["heading"] = heading
                held["pitch"] = math.degrees(math.atan2(dy, math.hypot(dx, dz)))
            for angle in ("heading", "pitch", "bank"):
                if angle in item:
                    held[angle] = _number(item[angle], f"frame {frame} {angle}")
            if not -90 <= held["pitch"] <= 90:
                raise BryceError("pitch must be -90 to 90 degrees")
            first = False
            entries.setdefault("orientation", []).append((frame / fps, (
                math.radians(-held["pitch"]), math.radians(held["heading"]),
                math.radians(held["bank"]))))
        return entries

    def _sun_entries(self, channels, by_frame, frames, fps, now) -> dict:
        held = self.sun()
        entries: dict[str, list] = {}
        for frame in frames:
            item = by_frame[frame]
            if "azimuth" in item or "altitude" in item:
                for angle in ("azimuth", "altitude"):
                    if angle in item:
                        held[angle] = _number(item[angle], f"frame {frame} {angle}")
                entries.setdefault("direction", []).append(
                    (frame / fps, settings_anim.unit_vector(held["azimuth"],
                                                            held["altitude"])))
            for colour in ("disc_color", "halo_color"):
                if colour in item:
                    entries.setdefault(colour, []).append(
                        (frame / fps, tuple(_colour(item[colour], colour))))
        return entries

    def _extend_timeline(self, last: float, duration: float | None) -> float:
        """Make the animation long enough for `last`; returns its length."""
        entry, data, (old_duration, fps, now, start, end) = self._timeline_floats()
        longest = max(last, old_duration if duration is None else duration)
        if abs(end - old_duration) < 1e-4 or end > longest:
            end = longest                    # the working range followed the length
        start = min(start, end)
        now = min(now, longest)
        struct.pack_into(">5f", data, _DOC_DURATION, longest, fps, now, start, end)
        self.file.replace(entry, bytes(data))
        return longest

    def set_keyframes(self, name: str | None = None, keys: list | None = None,
                      duration: float | None = None, library: "Br3 | None" = None,
                      target: str = "object", curve=None) -> dict:
        """Give an object or a setting its keys and time curve. All or nothing: the
        work is done on a copy, so a refused request leaves the scene as it was."""
        work = Br3.from_bytes(self.data)
        result = work._set_keyframes(name, keys, duration, library, target, curve)
        self.__dict__.update(work.__dict__)
        return result

    def _set_keyframes(self, name, keys, duration, library, target, curve) -> dict:
        """Replace the keys of the channels named in `keys`, a list such as
        [{"frame": 0, "position": [x, y, z]}, {"time": 2, "position": [...],
        "rotation": [...]}]. A channel given once stops being animated."""
        if target not in settings_anim.TARGETS:
            raise BryceError(f"target must be one of {', '.join(settings_anim.TARGETS)}")
        if keys is not None and not isinstance(keys, list):
            raise BryceError("keys must be a list")
        if keys is not None and len(keys) > keyframes.MAX_KEYS:
            raise BryceError(f"at most {keyframes.MAX_KEYS} keys")
        if duration is not None:
            duration = _number(duration, "duration")
            fps = self.timeline()["fps"]
            if not 0 <= duration <= MAX_FRAME / fps:
                raise BryceError("duration (seconds) is out of range")
        if target != "object":
            return self._key_settings(target, name, keys, duration, curve, library)
        if not name:
            raise BryceError("an object track needs the object's name")
        kind, ref = self.find(name)
        if not keys and curve is None:
            raise BryceError("give keys, a curve, or both")
        keys = keys or []
        if isinstance(curve, dict):
            stray = sorted(set(curve) - set(ANIMATED))
            if stray:
                raise BryceError(f"curve names unknown channel(s) {', '.join(stray)}")
        if len(keys) > keyframes.MAX_KEYS:
            raise BryceError(f"at most {keyframes.MAX_KEYS} keys")
        _, _, (old_duration, fps, now, start, end) = self._timeline_floats()
        wanted: dict[str, dict[int, object]] = {}
        for number, item in enumerate(keys):
            if not isinstance(item, dict):
                raise BryceError("each key is an object with frame or time and values")
            unknown = sorted(set(item) - {"frame", "time", *ANIMATED})
            if unknown:
                raise BryceError(f"key {number}: unknown field(s) {', '.join(unknown)}; "
                                 f"use frame or time, and {', '.join(ANIMATED)}")
            frame = self._moment(item, fps, f"key {number}")
            named = [channel for channel in ANIMATED if channel in item]
            if not named:
                raise BryceError(f"key {number} has no {' / '.join(ANIMATED)}")
            for channel in named:
                label = f"key {number} {channel}"
                value = _triple(item[channel], label)
                if channel == "scale" and min(value) <= 0:
                    raise BryceError(f"{label} must be positive")
                if frame in wanted.setdefault(channel, {}):
                    raise BryceError(f"two {channel} keys are on frame {frame}")
                wanted[channel][frame] = value
        if not wanted and not self.animated_channels(ref):
            raise BryceError("a curve needs a channel with two keys or more")

        if wanted and not self.animated_channels(ref):
            # Fold away the local offset and make sure there is a position track.
            position, origin, scale, rotation = self.transform(ref)
            self.place(ref, world_centre(position, origin, scale, rotation), scale,
                       rotation, library)
        shown = self._cached_transform(ref)
        for channel, still in (("position", shown[0]), ("scale", shown[2]),
                               ("rotation", shown[3])):
            if channel in wanted and self._linked(ref, ANIMATED[channel][0]) is None:
                track = self._ensure_track(ref, ANIMATED[channel][0], library)
                record = self._payload(track)
                self.file.replace(self._entry(track),
                                  keyframes.build(record, [(0.0, tuple(still))]))
        # Position last: where it is stored can depend on scale and rotation.
        for channel in ("scale", "rotation", "position"):
            if channel not in wanted:
                continue
            frames = sorted(wanted[channel])
            entries = []
            for frame in frames:
                time, value = frame / fps, wanted[channel][frame]
                if channel == "rotation":
                    value = quaternion_from_euler(value)
                    if entries and sum(a * b for a, b in zip(value, entries[-1][1])) < 0:
                        value = tuple(-v for v in value)      # take the short way round
                elif channel == "position":
                    _, scale, rotation = self._pose_at(ref, time)
                    offset = self._offset_at(ref, time, scale, rotation)
                    value = tuple(v / UNIT - o for v, o in zip(value, offset))
                entries.append((time, tuple(value)))
            if len(entries) == 1:
                entries = [(0.0, entries[0][1])]
            track = self._linked(ref, ANIMATED[channel][0])
            self.file.replace(self._entry(track),
                              keyframes.build(self._payload(track), entries))

        for channel, (class_name, _) in ANIMATED.items():
            spec = curve.get(channel) if isinstance(curve, dict) else curve
            if spec is None or (wanted and channel not in wanted):
                continue
            track = self._linked(ref, class_name)
            if track is None or keyframes.count(self._payload(track)) < 2:
                continue
            self.file.replace(self._entry(track), keyframes.with_curve(
                self._payload(track), curves.points(spec)))

        last = max((max(c) for c in wanted.values() if len(c) > 1), default=0) / fps
        longest = self._extend_timeline(last, duration)
        now = min(now, longest)
        self._write_pose(ref, *self._pose_at(ref, now))
        result = {"name": name, "animation": self.animation(ref),
                  "timeline": self.timeline()}
        shapes = self.curves_of(ref)
        if shapes:
            result["curves"] = shapes
        if duration is not None and longest > duration + 1e-4:
            result["note"] = "the duration was lengthened to reach the last key"
        return result

    def _place_animated(self, ref: Ref, centre, scale, rotation, library) -> None:
        """Move an animated object: a new position moves the whole motion with it."""
        channels = self.animated_channels(ref)
        shown_centre, shown_scale, shown_turn = self._shown(ref)
        changed_scale = any(abs(a - b) > 1e-6 for a, b in zip(scale, shown_scale))
        same_turn = abs(sum(a * b for a, b in zip(rotation, shown_turn))) > 1 - 1e-9
        for channel, changed in (("scale", changed_scale), ("rotation", not same_turn)):
            if changed and channel in channels:
                raise BryceError(f"{self._name(ref)!r} has animated {channel}; change it "
                                 "with the keyframes operation")
        moved = [a - b for a, b in zip(centre, shown_centre)]
        for channel, changed, value in (("scale", changed_scale, scale),
                                        ("rotation", not same_turn, rotation)):
            if changed:
                self._ensure_track(ref, ANIMATED[channel][0], library)
                self._set_vector(ref, ANIMATED[channel][0], value)
        track = self._linked(ref, "Obj_ePosition")
        if track is None:
            track = self._ensure_track(ref, "Obj_ePosition", library)
        record = self._payload(track)
        self.file.replace(self._entry(track), keyframes.build(record, [
            (time, tuple(v + d for v, d in zip(value, moved)))
            for time, value in keyframes.values(record, 3)]))
        self._write_pose(ref, centre, scale, rotation)

    def _shown(self, ref: Ref):
        """(centre, scale, rotation) as Bryce is drawing the object now."""
        position, origin, scale, rotation = self._cached_transform(ref)
        return world_centre(position, origin, scale, rotation), scale, rotation

    def _refuse_animated(self, refs, done: str) -> None:
        for ref in refs:
            if self.animated_channels(ref):
                raise BryceError(f"{self._name(ref)!r} is animated and cannot be {done}; "
                                 "remove its animation first (one key per channel)")

    def _make_still(self, ref: Ref) -> None:
        """Drop an object's animation, leaving it posed as it is shown."""
        channels = self.animated_channels(ref)
        if not channels:
            return
        centre, scale, rotation = self._shown(ref)
        values = {"position": centre, "scale": scale, "rotation": rotation}
        for channel in channels:
            track = self._linked(ref, ANIMATED[channel][0])
            self.file.replace(self._entry(track), keyframes.build(
                self._payload(track), [(0.0, tuple(values[channel]))]))
        if "position" in channels and self._linked(ref, "Obj_eOrigin") is not None:
            self._set_vector(ref, "Obj_eOrigin", (0.0, 0.0, 0.0))

    def boolean_of(self, ref: Ref) -> str:
        flags = struct.unpack_from("<I", self._payload(ref), _FLAGS)[0]
        return next((name for name, bit in BOOLEAN_BITS.items()
                     if bit and flags & bit), "neutral")

    def set_boolean(self, ref: Ref, mode: str) -> None:
        if mode not in BOOLEAN_BITS:
            raise BryceError(f"boolean must be one of {', '.join(BOOLEAN_BITS)}")
        flags = struct.unpack_from("<I", self._payload(ref), _FLAGS)[0]
        flags = (flags & ~_ROLE_MASK) | BOOLEAN_BITS[mode]
        if flags & _IN_GROUP and BOOLEAN_BITS[mode]:
            flags |= _HAS_ROLE
        self._write(ref, _FLAGS, struct.pack("<I", flags))

    def _set_membership(self, ref: Ref, inside: bool, role: str) -> None:
        """Mark an object as a part of a group, or as standing in the scene again."""
        flags = struct.unpack_from("<I", self._payload(ref), _FLAGS)[0]
        flags &= ~(_IN_GROUP | _ROLE_MASK)
        if ref[0] == _GROUP:
            flags &= ~(_TOP_GROUP | _BOOLEAN_GROUP)
            if role != "neutral":
                flags |= _BOOLEAN_GROUP
            flags |= _IN_GROUP if inside else _TOP_GROUP
        elif inside and role != "neutral":
            flags |= _IN_GROUP
        self._write(ref, _FLAGS, struct.pack("<I", flags))
        self.set_boolean(ref, role)

    def light(self, kind: str, key: Ref) -> dict:
        """A light's settings, with numbers as Bryce's Edit Light dialog shows them."""
        payload = self._payload(key)
        flags = struct.unpack_from("<I", payload, _FLAGS)[0]
        colour = struct.unpack_from("<3f", payload, _LIGHT_COLOUR)
        stored = struct.unpack_from("<d", payload, _LIGHT_INTENSITY)[0]
        found = {
            "color": "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}"
                                   for c in colour),
            "intensity": round(stored * _INTENSITY_STEP, 2),
            "falloff": ("squared" if flags & _SQUARED_FALLOFF
                        else "none" if flags & _NO_FALLOFF else "linear"),
            "casts_shadows": not flags & _NO_SHADOWS,
            "infinite": bool(flags & _INFINITE_LIGHT),
            "visible": bool(flags & _VISIBLE_LIGHT),
        }
        if kind in _SOFT_EDGES:
            form, names = _SOFT_EDGES[kind]
            values = [round(v * _SOFTNESS_STEP, 2) for v in struct.unpack_from(
                f"<{len(names)}{form[1]}", payload, _EDGE_SOFTNESS)]
            found["edge_softness"] = values[0] if len(set(values)) == 1 else values
        return found

    def set_light(self, name: str, settings: dict) -> dict:
        kind, key = self.find(name)
        if not kind.endswith("light"):
            raise BryceError(f"{name!r} is not a light")
        if not isinstance(settings, dict) or not settings:
            raise BryceError(f"light must be an object with any of {list(LIGHT_SETTINGS)}")
        unknown = sorted(set(settings) - set(LIGHT_SETTINGS))
        if unknown:
            raise BryceError(f"unknown light settings {unknown}; use {list(LIGHT_SETTINGS)}")

        def percent(value, label):
            number = _number(value, label)
            if not 0 <= number <= 100:
                raise BryceError(f"{label} must be from 0 to 100")
            return number

        def update(offset, form, value, record, label):
            self._write(key, offset, struct.pack(form, *value) if form == "<3f"
                        else struct.pack(form, value))
            track = self._linked(key, record)
            if track is not None:
                self._set_setting(track, value, label)

        self._refuse_animated_settings(
            [c for c in self._light_channels(key) if c.name in settings], f"light {name!r}")
        flags = struct.unpack_from("<I", self._payload(key), _FLAGS)[0]
        for label, value in settings.items():
            if label == "color":
                update(_LIGHT_COLOUR, "<3f", _colour(value, label), "Light_C", label)
            elif label == "intensity":
                update(_LIGHT_INTENSITY, "<d", percent(value, label) / _INTENSITY_STEP,
                       "Light_Int", label)
            elif label == "falloff":
                if value not in LIGHT_FALLOFFS:
                    raise BryceError(f"falloff must be one of {list(LIGHT_FALLOFFS)}")
                flags = flags & ~(_SQUARED_FALLOFF | _NO_FALLOFF) | LIGHT_FALLOFFS[value]
            elif label == "edge_softness":
                if kind not in _SOFT_EDGES:
                    raise BryceError("a radial light has no edge softness")
                form, records = _SOFT_EDGES[kind]
                values = value if isinstance(value, (list, tuple)) else [value] * len(records)
                if len(values) != len(records):
                    raise BryceError("edge_softness is one number, or [x, y] for square "
                                     "spot and parallel lights")
                for i, (one, record) in enumerate(zip(values, records)):
                    update(_EDGE_SOFTNESS + i * struct.calcsize(form), form,
                           percent(one, label) / _SOFTNESS_STEP, record, label)
            else:
                if not isinstance(value, bool):
                    raise BryceError(f"{label} must be true or false")
                bit = {"casts_shadows": _NO_SHADOWS, "infinite": _INFINITE_LIGHT,
                       "visible": _VISIBLE_LIGHT}[label]
                on = value if label != "casts_shadows" else not value
                flags = flags | bit if on else flags & ~bit
        self._write(key, _FLAGS, struct.pack("<I", flags))
        return self.light(kind, key)

    def _local_extent(self, kind: str, key: Ref) -> list[float]:
        """How big an object is in its own space: what its scale multiplies."""
        if kind == "terrain":
            return list(_TERRAIN_EXTENT)
        if kind == "mesh":
            try:
                points = trimesh.decode(self._payload(key))[0]
            except BryceError:
                points = []
            if points:
                spans = [max(p[a] for p in points) - min(p[a] for p in points)
                         for a in range(3)]
                return [span * MESH_UNIT if span > 1e-9 else 2.0 for span in spans]
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
        if kind == "path":
            try:
                points = motion.read_points(self._payload(key))
            except BryceError:
                info.pop("size")
            else:
                info["size"] = [round(v * UNIT, 2) for v in motion.extent(points)]
                loop, closed = motion.split_loop(points)
                info["path"] = {"points": len(points), "closed": closed}
        if self.animated_channels(key):
            info["animation"] = self.animation(key)
            shapes = self.curves_of(key)
            if shapes:
                info["curves"] = shapes
        if self.boolean_of(key) != "neutral":
            info["boolean"] = self.boolean_of(key)
        material = self.material(key)
        if material:
            info["material"] = material
        if kind.endswith("light"):
            info["light"] = self.light(kind, key)
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
            settings["position"] = self._resting_position(source, kind, matches[0], settings)
        new = self._copy(source, matches[0])
        self._append(KINDS[kind], new)
        self._make_still(new)
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
        # Keep the stored geometry within -1..1 and let the object's scale give it
        # its size.
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
                   [f * half / (MESH_UNIT * UNIT) for f in factors],
                   quaternion_from_euler(rotation or (0, 0, 0)), library)
        if material:
            self.set_material(final, material, library)
        info = self.describe(kind, ref)
        info["triangles"] = len(triangles)
        return info

    def mesh_geometry(self, ref: Ref) -> tuple[list, list]:
        """A mesh object's points and outward triangles, in the same object space as
        the primitives (so its scale and UNIT apply directly)."""
        points, _, triangles = trimesh.decode(self._payload(ref))
        return ([tuple(c * MESH_UNIT for c in p) for p in points],
                [(t[0], t[2], t[1]) for t in triangles])

    def _resting_position(self, source: "Br3", kind: str, key: Ref, settings: dict):
        """When a resized solid has no position, keep it standing on the ground."""
        position = source.describe(kind, key).get("position")
        old = source._vector(key, "Obj_eScale")
        reach = source._local_extent(kind, key)[1] / 2     # centre to underside
        if position is None or old is None \
                or abs(position[1] - old[1] * reach * UNIT) > 0.05:
            return None
        if settings.get("scale") is not None:
            new = _triple(settings["scale"], "scale")[1] * reach * UNIT
        elif settings.get("size") is not None:
            new = _triple(settings["size"], "size")[1] / 2
        else:
            return None
        return [position[0], new, position[2]]

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
            settings["position"] = self._resting_position(self, kind, original, settings)
        tag = next(tag for tag, ref, _ in self._root()[2] if ref == original)
        copy = self._copy(self, original)
        self._append(tag, copy)
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
        self._refuse_animated(members, "grouped")
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
            role = "neutral" if not boolean else (
                "negative" if member_name in negative else
                "intersect" if member_name in intersect else "positive")
            self._set_membership(ref, True, role)
        self.place(group, middle, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), library or source)
        self._set_membership(group, False, "positive" if boolean else "neutral")
        self._write_root([raw for _, ref, raw in self._root()[2] if ref not in members])
        self._append(_GROUP_TAG, group)
        self._rename(group, final)
        return self.describe("group", group)

    def ungroup(self, name: str, library: "Br3 | None" = None) -> dict:
        kind, group = self.find(name)
        if kind != "group":
            raise BryceError(f"{name!r} is not a group")
        items = self._list_items((_LIST, group[1]))
        self._refuse_animated([group, *(ref for _, ref, _ in items)], "ungrouped")
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
            self._set_membership(ref, False, self.boolean_of(ref))
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
            self._make_still(copy)
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
        self._refuse_animated_settings(self._sky_channels(set(settings)), "the sky")
        for label, value in settings.items():
            self._set_setting(tracks[label], value, label)
            self._cache_setting("C3dBaseSky", SKY_CACHE.get(label), tracks[label])
        return self.sky()

    def sky_switches(self) -> dict:
        """The sky mode and the Sky & Fog tick boxes."""
        owner = self._first("C3dBaseSky")
        if owner is None:
            return {}
        payload = self._payload(owner)
        flags = struct.unpack_from("<I", payload, _SKY_FLAGS)[0]
        mode = "off"
        if flags & _ATMOSPHERE_ON:
            mode = next((n for n, bit in SKY_MODES.items() if flags & bit), "soft")
        out = {"mode": mode}
        out.update({name: bool(flags & bit) for name, bit in SKY_SWITCHES.items()})
        for name in ("stratus_clouds", "cumulus_clouds"):
            out[name] = out[name] and bool(flags & _CLOUDS_ON)
        out["volumetric"] = next((n for n, bit in _VOLUMETRIC.items() if flags & bit), "off")
        if len(payload) >= max(_SKY_FIELDS.values()) + 4:
            out.update({name: bool(struct.unpack_from("<I", payload, at)[0])
                        for name, at in _SKY_FIELDS.items()})
        return out

    def set_sky_switches(self, switches: dict) -> dict:
        owner = self._first("C3dBaseSky")
        if owner is None:
            raise BryceError("the scene has no sky record")
        allowed = ["mode", "volumetric", *SKY_SWITCHES, *_SKY_FIELDS]
        unknown = sorted(set(switches) - set(allowed))
        if unknown:
            raise BryceError(f"unknown sky switches {unknown}; use {allowed}")
        before = self.sky_switches()
        flags = struct.unpack_from("<I", self._payload(owner), _SKY_FLAGS)[0]
        for name, value in switches.items():
            if name in ("mode", "volumetric"):
                table = SKY_MODES if name == "mode" else _VOLUMETRIC
                if value not in table:
                    raise BryceError(f"{name} must be one of {', '.join(table)}")
                mask = 0
                for bit in table.values():
                    mask |= bit
                if name == "mode":
                    mask |= _ATMOSPHERE_ON
                flags = flags & ~mask | table[value]
                if name == "mode" and value != "off":
                    flags |= _ATMOSPHERE_ON
            elif not isinstance(value, bool):
                raise BryceError(f"{name} must be true or false")
            elif name in SKY_SWITCHES:
                flags = flags | SKY_SWITCHES[name] if value else flags & ~SKY_SWITCHES[name]
            else:
                self._write(owner, _SKY_FIELDS[name], struct.pack("<I", int(value)))
        if "stratus_clouds" in switches or "cumulus_clouds" in switches:
            # Both layers hang off one "clouds" bit; a layer that was off while
            # that bit was clear must not appear just because the other is turned on.
            for name in ("stratus_clouds", "cumulus_clouds"):
                on = switches.get(name, before[name])
                flags = flags | SKY_SWITCHES[name] if on else flags & ~SKY_SWITCHES[name]
            layers = SKY_SWITCHES["stratus_clouds"] | SKY_SWITCHES["cumulus_clouds"]
            flags = flags | _CLOUDS_ON if flags & layers else flags & ~_CLOUDS_ON
        self._write(owner, _SKY_FLAGS, struct.pack("<I", flags))
        return self.sky_switches()

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

    # -- document and render settings ---------------------------------------------
    def _document_record(self) -> tuple[int, bytes]:
        """The entry holding the document settings, and its contents. Bryce copies
        these 416 bytes straight into the document when it opens a scene."""
        rows = self._rows.get("brycedocset")
        if rows:
            entry = struct.unpack_from(">I", rows[0], 8)[0]
            data = self.file.read(entry)
            if len(data) == _DOCUMENT_BYTES:
                return entry, data
        raise BryceError("this file has no document settings to read")

    def document(self) -> dict:
        _, data = self._document_record()
        flags, = struct.unpack_from(">I", data, _DOC_FLAGS)
        antialiasing, optimisation = struct.unpack_from(">2I", data, _DOC_ANTIALIASING)
        kinds = [name for name, bit in RENDER_TYPES.items() if bit and flags & bit]
        return {
            "size": list(struct.unpack_from(">2I", data, 0)),
            "antialiasing": _name_of(ANTIALIASING, antialiasing),
            "optimisation": _name_of(OPTIMISATION, optimisation),
            "render_type": kinds[0] if kinds else "normal",
            "report_render_time": any(data[_DOC_REPORT:_DOC_REPORT + 4]),
        }

    def set_document(self, *, size=None, antialiasing=None, optimisation=None,
                     render_type=None, report_render_time=None) -> dict:
        """Change the picture size and the choices of Bryce's Render Options menu,
        the way that menu changes them."""
        entry, old = self._document_record()
        data = bytearray(old)
        flags, = struct.unpack_from(">I", data, _DOC_FLAGS)
        if size is not None:
            width, height = _picture_size(size)
            struct.pack_into(">4I", data, 0, width, height, width, height)
            struct.pack_into(">4I", data, _DOC_FRAME, 0, 0, height, width)
            data[_DOC_VIEW:_DOC_FLAGS] = bytes(_DOC_FLAGS - _DOC_VIEW)
        if antialiasing is not None:
            value = _choice("antialiasing", antialiasing, ANTIALIASING)
            struct.pack_into(">I", data, _DOC_ANTIALIASING, value)
            flags = flags | 0x2000 if antialiasing == "normal" else flags & ~0x2000
        if optimisation is not None:
            value = _choice("optimisation", optimisation, OPTIMISATION)
            struct.pack_into(">I", data, _DOC_OPTIMISATION, value)
            flags |= 0x80
        if render_type is not None:
            bit = _choice("render_type", render_type, RENDER_TYPES)
            for other in RENDER_TYPES.values():
                flags &= ~other
            flags |= bit
        if report_render_time is not None:
            if not isinstance(report_render_time, bool):
                raise BryceError("report_render_time must be true or false")
            struct.pack_into(">I", data, _DOC_REPORT, report_render_time)
        struct.pack_into(">I", data, _DOC_FLAGS, flags)
        self.file.replace(entry, bytes(data))
        return self.document()

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
        touched = {name for name, value in (
            ("direction", azimuth if azimuth is not None else altitude),
            ("disc_color", disc_color), ("halo_color", halo_color)) if value is not None}
        self._refuse_animated_settings(
            [c for c in self._sun_channels() if c.name in touched], "the sun")
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
        self._refuse_animated_settings(self._camera_channels(), "the camera")
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
        if self._rows.get("brycedocset"):
            result["document"] = self.document()
            result["timeline"] = self.timeline()
        if self._rows.get("brycedocset"):
            moving = self.animated_settings()
            if moving:
                result["animated_settings"] = moving
        other = self.camera("perspective")
        if other != result["camera"]:
            result["camera_view"] = other   # differs until set_camera aligns the two
        return result
