"""The batch operations br3_edit applies to a scene."""

from __future__ import annotations

from . import heightfield, terrain
from .br3 import KINDS, MATERIAL_COLOURS, MATERIAL_VALUES, Br3
from .errors import BryceError

MAX_OPERATIONS = 200
_PLACEMENT = ("position", "scale", "size", "rotation", "material")

# op -> (required fields, optional fields, help)
OPERATIONS = {
    "add": (("kind",), ("name", "template", *_PLACEMENT),
            "Add an object copied from the template file."),
    "set": (("name",), ("new_name", *_PLACEMENT), "Change an existing object."),
    "duplicate": (("name",), ("new_name", *_PLACEMENT), "Copy an object in this scene."),
    "delete": (("name",), (), "Remove an object."),
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


def apply(scene: Br3, operations: list[dict], library: Br3 | None = None) -> list:
    """Run the operations on `scene` in order. The caller writes the file only if
    this returns, so a failure leaves nothing half done on disk."""
    validate(operations)
    results = []
    for number, step in enumerate(operations):
        fields = {k: v for k, v in step.items() if k != "op" and v is not None}
        try:
            results.append(_run(scene, step["op"], fields, library))
        except BryceError as error:
            raise BryceError(f"operation {number} ({step['op']}): {error}. "
                             "Nothing was written.") from error
    return results


def _run(scene: Br3, op: str, fields: dict, library: Br3 | None):
    if op == "add":
        material = fields.pop("material", None)
        info = scene.add_object(fields.pop("kind"), library=library, **fields)
        if material:
            scene.set_material(info["name"], material, library)
            info = scene.describe(*scene.find(info["name"]))
        return info
    if op in ("set", "duplicate"):
        name = fields.pop("name")
        material = fields.pop("material", None)
        if op == "duplicate":
            info = scene.duplicate(name, fields.pop("new_name", None), **fields)
        else:
            info = scene.set_object(name, **fields)
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
