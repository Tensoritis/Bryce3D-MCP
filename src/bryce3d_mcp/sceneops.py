"""The batch operations br3_edit applies to a scene."""

from __future__ import annotations

import os

from . import heightfield, meshes, picture, presets, terrain, trimesh
from .br3 import (BOOLEAN_BITS, KINDS, LIGHT_FALLOFFS, LIGHT_SETTINGS, MATERIAL_COLOURS,
                  MATERIAL_VALUES, TEXTURE_MAPPINGS, Br3)
from .errors import BryceError

MAX_OPERATIONS = 500
_PLACEMENT = ("position", "scale", "size", "rotation", "material", "boolean", "light")

# op -> (required fields, optional fields, help)
OPERATIONS = {
    "add": (("kind",), ("name", "template", *_PLACEMENT),
            "Add an object copied from the template file."),
    "mesh": (("shape",), ("name", "outline", "height", "profile", "segments", "vertices",
                          "faces", "obj_path", "smooth_angle", "position", "scale", "size",
                          "rotation", "material", "boolean"),
             "Create a NEW 3D shape. shape extrude (outline [[x,z],...] and height), lathe "
             "(profile [[radius,y],...] bottom to top, segments), custom (vertices and "
             "zero-based faces, counter-clockwise from outside) or obj (obj_path). Numbers "
             "are Bryce units. Edges sharper than smooth_angle degrees (40) stay crisp. "
             "Needs one mesh, such as a stone, in the template scene."),
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
    "texture_scale": (("name", "factor"), ("channels",),
                      "Multiply a procedural texture's scale numbers (one factor, or [x, "
                      "y, z]). They say how often the pattern repeats, so 2 makes ripples "
                      "or checks half as big. br3_info lists the current numbers."),
    "texture_edit": (("name",), ("channels", "mapping", "scale", "offset", "rotation",
                                 "replace_colors", "color_sets"),
                     "Edit the procedural textures on an object. mapping is how it wraps ("
                     + ", ".join(TEXTURE_MAPPINGS) + "). scale (set, not multiply; bigger "
                     "means smaller pattern), offset [x,y,z] and rotation [x,y,z] degrees "
                     'place it. replace_colors {"#old": "#new"} recolors it; color_sets '
                     '{"2": [c1, c2, c3]} sets one of its four sets of three colors. '
                     "br3_info shows the current values under texture_details. A texture "
                     "shared by several channels changes in all of them."),
    "picture": (("name", "path"), ("channels", "repeat", "mapping"),
                "Put your own picture file (PNG, JPEG...) on an object. channels default "
                "to color and ambient_color; add others such as bump to drive them from "
                "the picture. repeat is how many times it tiles (one number or [x,y,z]; "
                "under 1 shows part of it). mapping defaults to parametric, which fits "
                "the picture to each face; the other names are those of texture_edit. "
                "The picture is stored inside the scene, at most 1024 pixels a side. "
                "Preview does not show it."),
    "sky_preset": (("preset",), ("library",),
                   "Take the sky and sun of one of Bryce's sky presets."),
    "terrain": (("name",), ("operations", "heightmap_path"),
                "Reshape a terrain object: start from its heights or a picture, then apply "
                "terrain_operations."),
    "camera": ((), ("position", "heading", "pitch", "bank", "look_at", "fov"),
               "Move or aim the render camera. heading 0 looks along +z, 90 along +x; "
               "pitch is positive upwards; bank tilts the picture (positive makes the "
               "horizon drop on the right); fov is in degrees across the picture's diagonal."),
    "sun": ((), ("azimuth", "altitude", "disc_color", "halo_color"),
            "Aim the sun. Bryce stores it relative to the camera: azimuth 0 is ahead of "
            "the camera, 90 to its right, 180 behind it."),
    "sky": ((), ("settings", "switches"),
            "Change the sky. settings are numbers and colours by the names br3_info "
            "lists. switches are the Sky & Fog choices: mode (soft, darker, custom or "
            "off), volumetric (off, rough or fine), and true/false for stratus_clouds, "
            "cumulus_clouds, cloud_shadows, link_clouds, fixed_clouds, link_sun_to_view, "
            "blend_haze_with_sun, blend_fog_with_sun, rainbow, secondary_rainbow, "
            "halo_rings, illusion, moon_image, moon_phase, stars, comets. Which numbers "
            "show depends on the mode."),
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
        "light_settings": {
            "for": "add, set and duplicate on a light, as light: {...}",
            "names": list(LIGHT_SETTINGS),
            "color": "#rrggbb (a light's color is here, not in material)",
            "intensity": "0 to 100 as in Bryce's Edit Light box; new lights have 25",
            "edge_softness": "0 to 100; spot, square spot and parallel lights; "
                             "[x, y] for the last two",
            "falloff": list(LIGHT_FALLOFFS),
            "true/false": "casts_shadows, infinite, visible (visible light in the air)",
        },
        "presets": "br3_presets lists the names for insert_preset, material_preset and "
                   "sky_preset",
        "scale": "multipliers [x, y, z]; or give size in Bryce units (for primitives "
                 "scale 1 = 20.48; a terrain at scale 1 is 20.48 wide and 10.24 tall)",
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
    if op == "texture_scale":
        return scene.scale_texture(fields["name"], fields["factor"], fields.get("channels"))
    if op == "texture_edit":
        return scene.edit_texture(fields.pop("name"), fields.pop("channels", None), **fields)
    if op == "picture":
        path = fields.pop("path")
        return scene.set_picture(
            fields.pop("name"), picture.load(path), fields.pop("channels", None),
            sources=(library, sources.donor("Tutorial.obp")),
            label=os.path.splitext(os.path.basename(path))[0], **fields)
    if op == "sky_preset":
        found, source = sources.preset("skies", fields)
        scene.apply_sky_preset(source, found.key)
        return {"sky_preset": found.name, "sun": scene.sun()}
    if op == "mesh":
        return _mesh(scene, library, fields)
    if op in ("add", "set", "duplicate"):
        material, boolean = fields.pop("material", None), fields.pop("boolean", None)
        light = fields.pop("light", None)
        if op == "add":
            info = scene.add_object(fields.pop("kind"), library=library, **fields)
        elif op == "set":
            info = scene.set_object(fields.pop("name"), library=library, **fields)
        else:
            info = scene.duplicate(fields.pop("name"), fields.pop("new_name", None),
                                   library, **fields)
        # In this order: giving a material can renumber what the name points at.
        if boolean:
            scene.set_boolean(scene.find(info["name"])[1], boolean)
        if material:
            scene.set_material(info["name"], material, library)
        if light is not None:
            scene.set_light(info["name"], light)
        return scene.describe(*scene.find(info["name"]))
    if op == "delete":
        scene.delete(fields["name"])
        return {"deleted": fields["name"]}
    if op == "terrain":
        name = fields["name"]
        size, values = scene.terrain_heights(name)
        grid = [v / heightfield.FULL for v in values]
        if "heightmap_path" in fields:
            from PIL import Image
            with Image.open(fields["heightmap_path"]) as heightmap:
                grid = terrain.grid_from_image(heightmap, size)
        grid = terrain.apply(size, grid, fields.get("operations", []))
        scene.set_terrain_heights(
            name, [round(min(1.0, max(0.0, h)) * heightfield.FULL) for h in grid])
        return {"terrain": name, "grid": size}
    if op == "camera":
        return {"camera": scene.set_camera(**fields)}
    if op == "sun":
        return {"sun": scene.set_sun(**fields)}
    settings, switches = fields.get("settings"), fields.get("switches")
    if not settings and not switches:
        raise BryceError("give settings, switches or both")
    if switches:
        scene.set_sky_switches(switches)
    if settings:
        scene.set_sky(settings)
    return {"sky_changed": sorted({**(settings or {}), **(switches or {})})}


def _mesh(scene: Br3, library: Br3 | None, fields: dict) -> dict:
    shape, boolean = fields.pop("shape"), fields.pop("boolean", None)
    outline, height = fields.pop("outline", None), fields.pop("height", None)
    profile, segments = fields.pop("profile", None), fields.pop("segments", 32)
    vertices, faces = fields.pop("vertices", None), fields.pop("faces", None)
    obj_path = fields.pop("obj_path", None)
    if shape == "extrude" and outline is not None and height is not None:
        vertices, faces = meshes.extrude(outline, height)
    elif shape == "lathe" and profile is not None:
        vertices, faces = meshes.lathe(profile, segments)
    elif shape == "obj" and obj_path is not None:
        if os.path.getsize(obj_path) > 20_000_000:
            raise BryceError("the OBJ file is larger than 20 MB")
        with open(obj_path, encoding="utf-8", errors="replace") as handle:
            vertices, faces = trimesh.parse_obj(handle.read())
    elif not (shape == "custom" and vertices is not None and faces is not None):
        raise BryceError("shape needs: extrude with outline and height, lathe with "
                         "profile, custom with vertices and faces, or obj with obj_path")
    info = scene.add_mesh(vertices, faces, library=library, **fields)
    if boolean:
        scene.set_boolean(scene.find(info["name"])[1], boolean)
        info["boolean"] = boolean
    return info
