"""A small synthetic .br3 scene laid out like Bryce's own (no Bryce data in it)."""

import struct

from bryce3d_mcp import heightfield
from bryce_files import composite, table, terrain_payload

OBJECT_TRACKS = ((492, "Obj_ePosition"), (496, "Obj_eOrient"), (500, "Obj_eScale"),
                 (508, "Obj_eOrigin"))


def cached(camera, offset, translation):
    """The values a camera record caches: looking along +z with a 60 degree lens."""
    struct.pack_into("<3d", camera, 356, *offset)
    struct.pack_into("<9d", camera, 380, 1, 0, 0, 0, 1, 0, 0, 0, 1)
    struct.pack_into("<3d", camera, 452, *translation)
    struct.pack_into("<d", camera, 724, 1.0471975511965976)


class Builder:
    def __init__(self):
        self.classes: dict[str, list[tuple[int, bytes, str]]] = {}
        self.next_key = 0x05000000

    def record(self, class_name, payload, name="", key=None):
        if key is None:
            self.next_key += 0x40
            key = self.next_key
        self.classes.setdefault(class_name, []).append((key, bytes(payload), name))
        return key

    def track(self, class_name, values, size=240, offset=192, form="d"):
        payload = bytearray(size)
        struct.pack_into(f"<{len(values)}{form}", payload, offset, *values)
        return self.record(class_name, payload)

    def material(self, colour):
        links = {900: self.track("Mat_eDiffuseC", colour, 248, 220, "f"),
                 920: self.track("Mat_eDiffuse", [1.0], 232, 220, "f")}
        payload = bytearray(976)
        for offset, key in links.items():
            struct.pack_into("<I", payload, offset, key)
        return self.record("C3dBaseMaterial", payload, "Material")

    def solid(self, class_name, name, position, origin=(0, 0, 0), scale=(1, 1, 1),
              colour=(1, 0, 0), payload=None, extra=()):
        body = bytearray(payload or bytes(640))
        body[8:8 + len(name) + 1] = name.encode() + b"\0"
        struct.pack_into("<I", body, 280, self.material(colour))
        values = {"Obj_ePosition": position, "Obj_eOrient": (0, 0, 0, 1),
                  "Obj_eScale": scale, "Obj_eOrigin": origin}
        for offset, track in OBJECT_TRACKS:
            struct.pack_into("<I", body, offset, self.track(track, values[track]))
        for offset, key in extra:
            struct.pack_into("<I", body, offset, key)
        return self.record(class_name, body, name)

    def build(self, objects, entries=512):
        """`objects` is a list of (root tag, id)."""
        camera = bytearray(808)
        camera[8:20] = b"perspective\0"
        cached(camera, offset=(0, 0, 0), translation=(0, 3, -14))
        for offset, key in ((492, self.track("Obj_ePosition", (0, 3, -14))),
                            (508, self.track("Obj_eOrigin", (0, 0, 0))),
                            (780, self.track("Cam_eCraOrient", (0, 0, 0))),
                            (788, self.track("Cam_eFOV", [1.0471975511965976], 200))):
            struct.pack_into("<I", camera, offset, key)
        camera_key = self.record("C3dBaseCamera", camera, "perspective")
        director = bytearray(808)                  # a view with no tracks, like Bryce's
        director[8:17] = b"director\0"
        cached(director, offset=(0, 3, -14), translation=(0, 0, 0))
        self.record("C3dBaseCamera", director, "director")
        sun = bytearray(896)
        struct.pack_into("<I", sun, 892, self.track("SunL_eSunVctr", (0.0, 0.6, -0.8)))
        self.record("C3dSunLight", sun)
        sky = bytearray(1344)
        struct.pack_into("<I", sky, 540, self.track("Sky_eBaseC", (0.2, 0.2, 1.0), 204, 192, "f"))
        struct.pack_into("<I", sky, 556, self.track("Sky_eShadowInt", [0.1], 200))
        self.record("C3dBaseSky", sky)
        items = [("basecamr", camera_key), *objects]
        root = struct.pack("<III", 1, 1, len(items)) + b"".join(
            tag.encode()[::-1] + struct.pack("<II", key, 0) for tag, key in items)
        self.record("C3dRootObjList", root)
        return self.pack(entries)

    def pack(self, entries=512):
        payloads, class_rows, entry = {}, [], 24
        for class_name, records in self.classes.items():
            rows = []
            for key, payload, name in records:
                payloads[entry] = payload
                payloads[entry + 1] = b""
                rows.append((struct.pack(">III", key, entry, entry + 1)
                             + name.encode()).ljust(60, b"\0"))
                entry += 2
            payloads[entry], payloads[entry + 1] = table(60, rows)
            class_rows.append(class_name.encode().ljust(16, b"\0")
                              + struct.pack(">II", entry, entry + 1))
            entry += 2
        payloads[16], payloads[17] = table(32, class_rows)
        return composite(payloads, entries=entries or max(payloads) + 1)   # 0: exactly full


def basic_scene(entries=512) -> bytes:
    b = Builder()
    ground = b.solid("C3dPlane", "Plane 1", (0, 0.001, 0))
    cube = b.solid("C3dCube", "Cube 1", (0, 1, 0), origin=(2.5, 1.9, 0))
    return b.build([("plne3dob", ground), ("cube3dob", cube)], entries)


def template_scene() -> bytes:
    """A scene holding object kinds the basic one lacks."""
    b = Builder()
    ball = b.solid("C3dSphere", "Sphere 1", (0, 1, 0), colour=(0.5, 0.5, 0.5))
    size = 16
    heights = [(x * y * 97) % 65536 for y in range(size) for x in range(size)]
    land = bytearray(terrain_payload("Terrain 1", size, heights, eleven_bits=False))
    memory = bytearray(208 + size * size * 2)
    struct.pack_into(f">{size * size}H", memory, 208, *heightfield.decode(bytes(land))[1])
    memory_key = b.record("HF_eHFMem", memory)
    terrain = b.solid("C3dHeightField", "Terrain 1", (0, 1, 0), scale=(8, 2, 8),
                      payload=land, extra=[(756, memory_key)])
    return b.build([("sphr3dob", ball), ("hfld3dob", terrain)])


def mesh_record(name="Stone 1"):
    """A one-triangle mesh object body."""
    body = bytearray(860 + 24 * 4 + 76)
    struct.pack_into("<5i", body, 640, 2, 3, 1, 1, 0)
    for i, point in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        struct.pack_into("<3d", body, 860 + 24 * i, *point)
    struct.pack_into("<6i", body, 860 + 96, 0, 1, 2, 0, 0, 0)
    body[8:8 + len(name) + 1] = name.encode() + b"\0"
    return body


def mesh_scene() -> bytes:
    """A scene holding one stone, to copy meshes from."""
    b = Builder()
    b.next_key = 0x0C000000
    stone = b.solid("C3dTriMeshType2", "Stone 1", (0, 1, 0), payload=mesh_record())
    return b.build([("tmsh3dob", stone)])


def object_list(items):
    """The payload of a root or group object list, from (tag, id) pairs."""
    return struct.pack("<III", 1, 1, len(items)) + b"".join(
        tag.encode()[::-1] + struct.pack("<II", key, 0) for tag, key in items)


def description(name, about=""):
    return name.encode().ljust(64, b"\0") + about.encode().ljust(257, b"\0")


def group_donor() -> bytes:
    """A preset library whose one preset is a group of two spheres. As in Bryce's
    files, the group and its member list share an id, and so does the description."""
    b = Builder()
    b.next_key = 0x09000000
    members = [("sphr3dob", b.solid("C3dSphere", name, (x, 0, 0)))
               for name, x in (("", -1), ("", 1))]
    group = b.solid("C3dGroupType", "Group 1", (0, 1, 0))
    b.record("C3dObjList", object_list(members), key=group)
    root = b.record("C3dRootObjList", object_list([("grou3dob", group)]))
    b.record("objpresetdesc", description("Twin Balls", "Two grouped spheres."), key=root)
    return b.pack(0)


def flat_material(colour=(1, 1, 1), **values):
    """A material as Bryce's presets store it: values in the record, no tracks."""
    body = bytearray(976)
    struct.pack_into("<3f", body, 300, *colour)
    for offset, value in values.items():
        struct.pack_into("<f", body, int(offset[1:]), value)
    return body


def material_library() -> bytes:
    b = Builder()
    b.next_key = 0x0A000000
    mirror = b.record("C3dBaseMaterial", flat_material((0, 0, 1), o740=1.0))
    b.record("CPresetDescr", description("Mirror\tx", "Reflects."), key=mirror)
    texture = bytearray(1588)
    texture[8:14] = b"checks"
    struct.pack_into("<3d", texture, 276, 0.25, 0.25, 0.5)     # its scale
    texture_key = b.record("C3dTexture", texture)
    checker = flat_material((0.5, 0.5, 0.5))
    for block in (276, 716):                      # colour and reflection use it
        checker[block - 4:block] = b"xtrb"
        struct.pack_into("<I", checker, block, texture_key)
        struct.pack_into("<I", checker, block + 20, 0x10000000)
    struct.pack_into("<I", checker, 268, 0x40)
    key = b.record("C3dBaseMaterial", checker)
    b.record("CPresetDescr", description("Checker"), key=key)
    return b.pack(0)


def sky_library() -> bytes:
    b = Builder()
    b.next_key = 0x0B000000
    sky = bytearray(1344)
    struct.pack_into("<3f", sky, 368, 1.0, 0.5, 0.0)       # BaseC
    struct.pack_into("<d", sky, 416, 0.7)                  # ShadowInt
    key = b.record("C3dBaseSky", sky)
    sun = bytearray(896)
    struct.pack_into("<3d", sun, 860, 1.0, 0.0, 0.0)
    b.record("C3dSunLight", sun, key=key)                  # same id as its sky
    b.record("CPresetDescr", description("Sunset"), key=key)
    return b.pack(0)


def light_scene() -> bytes:
    """Lights as a new one is stored: white, intensity 25, no setting records."""
    b = Builder()
    objects = []
    for class_name, tag, name, size in (("C3dSphereLight", "sphrlght", "Radial 1", 832),
                                        ("C3dConeLight", "conelght", "Spot 1", 848),
                                        ("C3dPyrLightType", "pyrmlght", "Square 1", 852)):
        body = bytearray(size)
        struct.pack_into("<I", body, 272, 0x1100)
        struct.pack_into("<I3f3d", body, 640, 1, 1.0, 1.0, 1.0, 5.0, 1.0, 0.0)
        objects.append((tag, b.solid(class_name, name, (0, 5, 0), payload=body)))
    return b.build(objects)


def picture_donor() -> bytes:
    """A file with one picture texture, to be copied."""
    b = Builder()
    b.next_key = 0x0C000000
    stored = b.record("C3dCImage", b"rle2" + bytes(60) + bytes((1, 0)) * 4, "old")
    texture = bytearray(636)
    texture[8:12] = b"old\0"
    struct.pack_into("<3I", texture, 264, 1, 2, 0)
    struct.pack_into("<4d", texture, 324, 0, 0, 0, 1)
    struct.pack_into("<2I", texture, 500, 1, stored)
    b.record("CPictTexture", texture, "old")
    return b.build([])
