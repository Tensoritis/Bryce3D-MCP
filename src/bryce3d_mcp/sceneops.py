"""The batch operations br3_edit applies to a scene."""

from __future__ import annotations

from . import heightfield, presets, terrain
from .br3 import BOOLEAN_BITS, KINDS, MATERIAL_COLOURS, MATERIAL_VALUES, Br3
from .errors import BryceError

MAX_OPERATIONS = 500
_PLACEMENT = ("position", "scale", "size", "rotation", "material", "boolean")

# op -> (required fields, optional fields, help)
OPERATIONS = {
    "add": (("kind",), ("name", "template", *_PLACEMENT),
            "Add an object copied from the template file."),
    "set": (("name",), ("new_name", *_PLACEMENT), "Change an existing object."),
    "duplicate": (("name",), ("new_name", *_PLACEMENT), "Copy an object in this scene."),
    "delete": (("name",), (), "Remove an object or a whole group."),
    "group": (("names",), ("name", "negative", "intersect"),
              "Group objects so they move as one. List some in negative to cut them out "
              "of the others, or in intersect to keep only the overlap."),
    "ungroup": (("name",), (), "Take a group apart."),
    "insert_preset": (("preset",), ("library", "name", "position", "scale", "rotation"),
                      "Add a ready-made model from Bryce's object presets (mountains, "
                      "boolean lessons, tutorial models). scale is one number."),
    "material_preset": (("name", "preset"), ("library", "material"),
                        "Give an object one of Bryce's materials (Mirror, Metallic Chrome, "
                        "Mirrored Checkerboard, woods, metals...); material then adjusts it."),
    "texture": (("name", "texture", "channels"), ("strength",),
                "Drive material channels (for example bump) with a Bryce texture, found "
                "by name. BasicSin24 on bump makes ripples."),
    "sky_preset": (("preset",), ("library",),
                   "Take the sky and sun of one of Bryce's sky presets."),
    "terrain": (("name",), ("operations", "heightmap_path"),
                "Reshape a terrain object: start from its heights or a picture, then apply "
                "terrain_operations."),
    "camera": ((), ("position", "heading", "pitch", "look_at", "fov"),
               "Move or aim the render camera. heading 0 looks along +z, 90 along +x; "
               "pitch is positive upwards; fov is in degrees across the picture's diagonal."),
    "sun": ((), ("azimuth", "altitude", "disc_color", "halo_color"),
            "Aim the sun. Bryce stores it relative to the camera: azimuth 0 is ahead of "
            "the camera, 90 to its right, 180 behind it."),
    "sky": (("settings",), (), "Change sky settings by the names br3_info lists. "
            "Which of them show depends on the sky mode."),
}


def describe() -> dict:
    return {
        "operations": {name: {"required": list(r), "optional": list(o), "help": text}
                       for name, (r, o, text) in OPERATIONS.items()},
        "kinds": list(KINDS),
        "material_settings": {"colors (#rrggbb)": list(MATERIAL_COLOURS),
                              "numbers (1.0 is 100%)": list(MATERIAL_VALUES)},
        "position": "center of the object in Bryce units [x, y, z]; y is up",
        "boolean": list(BOOLEAN_BITS),
        "presets": "br3_presets lists the names for insert_preset, material_preset and "
                   "sky_preset",
        "scale": "multipliers [x, y, z]; or give size in Bryce units (scale 1 = 20.48)",
        "rotation": "degrees [x, y, z]; +y turns +x toward +z, +x tips +y toward -z",
    }


def validate(operations) -> None:
    if not isinstance(operations, list):
        raise BryceError("operations must be a list")
    if len(operations) > MAX_OPERATIONS:
        raise BryceError(f"at most {MAX_OPERATIONS} operations per call")
    for number, step in enumerate(operations):
        if not isinstance(step, dict) or step.get("op") not in OPERATIONS:
            raise BryceError(f"operation {number}: 'op' must be one of {', '.join(OPERATIONS)}")
        required, optional, _ = OPERATIONS[step["op"]]
        missing = [f for f in required if step.get(f) is None]
        if missing:
            raise BryceError(f"operation {number} ({step['op']}): missing {', '.join(missing)}")
        unknown = sorted(set(step) - {"op", *required, *optional})
        if unknown:
            raise BryceError(f"operation {number} ({step['op']}): unknown field(s) "
                             f"{', '.join(unknown)}; allowed: {', '.join(required + optional)}")


def apply(scene: Br3, operations: list[dict], library: Br3 | None = None,
          install: str | None = None) -> list:
    """Run the operations on `scene` in order. The caller writes the file only if
    this returns, so a failure leaves nothing half done on disk."""
    validate(operations)
    results = []
    for number, step in enumerate(operations):
        fields = {k: v for k, v in step.items() if k != "op" and v is not None}
        try:
            results.append(_run(scene, step["op"], fields, _Sources(library, install)))
        except BryceError as error:
            raise BryceError(f"operation {number} ({step['op']}): {error}. "
                             "Nothing was written.") from error
    return results


class _Sources:
    """Where objects can be copied from: the template file and Bryce's presets."""

    def __init__(self, library: Br3 | None, install: str | None) -> None:
        self.library, self.install = library, install

    def folder(self) -> str:
        return presets.folder(self.install)

    def preset(self, category: str, fields: dict):
        found = presets.find(self.folder(), category, fields.pop("preset"),
                             fields.pop("library", None))
        return found, presets.load(found)

    def donor(self, file_name: str):
        try:
            return presets.donor(self.folder(), file_name)
        except BryceError:
            return None


def _run(scene: Br3, op: str, fields: dict, sources: _Sources):
    library = sources.library
    if op == "group":
        return scene.group(fields.pop("names"), fields.pop("name", None), library=library,
                           donors=(sources.donor("Boolean Objects.obp"),), **fields)
    if op == "ungroup":
        return scene.ungroup(fields["name"], library)
    if op == "insert_preset":
        found, source = sources.preset("objects", fields)
        return scene.insert_preset(source, found.key, found.name, library=library, **fields)
    if op == "material_preset":
        name, material = fields.pop("name"), fields.pop("material", None)
        found, source = sources.preset("materials", fields)
        info = scene.apply_material_preset(name, source, found.key)
        if material:
            scene.set_material(name, material, library)
            info = scene.describe(*scene.find(name))
        return info
    if op == "texture":
        others = [library, sources.donor("Boolean Objects.obp"),
                  sources.donor("Tutorial.obp"), sources.donor("Simple&Fast.mat")]
        return scene.set_texture(fields["name"], fields["texture"], fields["channels"],
                                 others, fields.get("strength"))
    if op == "sky_preset":
        found, source = sources.preset("skies", fields)
        scene.apply_sky_preset(source, found.key)
        return {"sky_preset": found.name, "sun": scene.sun()}
    if op == "add":
        material, boolean = fields.pop("material", None), fields.pop("boolean", None)
        info = scene.add_object(fields.pop("kind"), library=library, **fields)
        if boolean:
            scene.set_boolean(scene.find(info["name"])[1], boolean)
            info = scene.describe(*scene.find(info["name"]))
        if material:
            scene.set_material(info["name"], material, library)
            info = scene.describe(*scene.find(info["name"]))
        return info
    if op in ("set", "duplicate"):
        name = fields.pop("name")
        material, boolean = fields.pop("material", None), fields.pop("boolean", None)
        if op == "duplicate":
            info = scene.duplicate(name, fields.pop("new_name", None), library, **fields)
        else:
            info = scene.set_object(name, library=library, **fields)
        if boolean:
            scene.set_boolean(scene.find(info["name"])[1], boolean)
            info = scene.describe(*scene.find(info["name"]))
        if material:
            scene.set_material(info["name"], material, library)
            info = scene.describe(*scene.find(info["name"]))
        return info
    if op == "delete":
        scene.delete(fields["name"])
        return {"deleted": fields["name"]}
    if op == "terrain":
        name = fields["name"]
        size, values = scene.terrain_heights(name)
        grid = [v / heightfield.FULL for v in values]
        if "heightmap_path" in fields:
            from PIL import Image
            with Image.open(fields["heightmap_path"]) as picture:
                grid = terrain.grid_from_image(picture, size)
        grid = terrain.apply(size, grid, fields.get("operations", []))
        scene.set_terrain_heights(
            name, [round(min(1.0, max(0.0, h)) * heightfield.FULL) for h in grid])
        return {"terrain": name, "grid": size}
    if op == "camera":
        return {"camera": scene.set_camera(**fields)}
    if op == "sun":
        return {"sun": scene.set_sun(**fields)}
    scene.set_sky(fields["settings"])
    return {"sky_changed": sorted(fields["settings"])}
