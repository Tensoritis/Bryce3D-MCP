"""A small synthetic .br3 scene laid out like Bryce's own (no Bryce data in it)."""

import struct

from bryce3d_mcp import heightfield
from bryce_files import composite, table, terrain_payload

OBJECT_TRACKS = ((492, "Obj_ePosition"), (496, "Obj_eOrient"), (500, "Obj_eScale"),
                 (508, "Obj_eOrigin"))


class Builder:
    def __init__(self):
        self.classes: dict[str, list[tuple[int, bytes, str]]] = {}
        self.next_key = 0x05000000

    def record(self, class_name, payload, name=""):
        self.next_key += 0x40
        self.classes.setdefault(class_name, []).append((self.next_key, bytes(payload), name))
        return self.next_key

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
        for offset, key in ((492, self.track("Obj_ePosition", (0, 3, 14))),
                            (508, self.track("Obj_eOrigin", (0, 0, 0))),
                            (780, self.track("Cam_eCraOrient", (0, 3.141592653589793, 0))),
                            (788, self.track("Cam_eFOV", [1.0471975511965976], 200))):
            struct.pack_into("<I", camera, offset, key)
        camera_key = self.record("C3dBaseCamera", camera, "perspective")
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
