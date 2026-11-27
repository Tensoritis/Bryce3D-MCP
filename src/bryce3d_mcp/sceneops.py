"""The batch operations br3_edit applies to a scene."""

from __future__ import annotations

import os

from . import heightfield, meshes, motion, picture, presets, terrain, trimesh
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
    "join": (("name", "to"), (),
             "Join an object to another (Bryce's linking): moving, turning or resizing "
             "the parent `to` then carries name with it, keys included. Neither moves "
             "when joined. A group cannot be the parent. An unevenly stretched parent "
             "cannot take a child that is turned inside it."),
    "unjoin": (("name",), (), "Set a joined object free, where it stands."),
    "track": (("name", "target"), (),
              "Keep name (an object, a spot light, or \"camera\") facing the object "
              "target, as Bryce's tracking does: the +z side of objects, the beam of "
              "spot lights. Its own rotation is kept for when tracking ends."),
    "untrack": (("name",), (), "Stop tracking."),
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
    "path": (("points",), ("name", "closed", "smooth"),
             "Add a Bryce path object through points [[x,y,z],...] in Bryce units. closed "
             "makes a loop. smooth (true) saves many points along an even, loop-free curve "
             "through yours. false saves exactly your points and Bryce draws its own curve "
             "through them (uneven spacing can make it loop); then a point may also be "
             '{"at":[x,y,z],"tension":t,"continuity":c,"bias":b}, each -1 to 1: tension 1 '
             "pulls the curve tight (on every inner point it gives straight lines), "
             "continuity bends it sharply at the point, bias leans it before or after. "
             "Bryce's 30 ready-made paths are object presets: insert_preset with library "
             '"Paths". A path can be moved but not resized or turned.'),
    "follow_path": ((), ("name", "target", "path", "preset", "points", "closed", "centre",
                         "scale", "turn", "start", "end", "keys", "face", "heading_offset",
                         "look_at", "reverse", "curve", "duration", "native"),
                    "Move an object (name) or the camera (target camera) along a path at a "
                    "steady speed, by writing position keys (one per frame unless keys says "
                    "fewer; fewer are easier to edit in Bryce but follow less closely). Give one of path (a path "
                    "object in the scene), preset (a Bryce path by name) or points "
                    "[[x,y,z],...] (shaped points as for op path use Bryce's curve). A path "
                    "object or preset is followed along the curve Bryce draws. "
                    "centre, scale (one number) and turn (degrees) move, "
                    "resize and swing the route. start and end are frames; keys is how "
                    "many to write. face true points it the way it travels (an object's "
                    "+z side, or the camera, which then also tilts); heading_offset adds "
                    "degrees. look_at keeps the camera on one spot. curve (as for "
                    "keyframes) changes the speed. native true makes Bryce's own path link "
                    "instead of position keys (name and path only, with start, end, "
                    "reverse, curve and duration): the object is joined to the path, so "
                    "moving the path moves its route too."),
    "sky_preset": (("preset",), ("library",),
                   "Take the sky and sun of one of Bryce's sky presets."),
    "terrain": (("name",), ("operations", "heightmap_path"),
                "Reshape a terrain object: start from its heights or a picture, then apply "
                "terrain_operations."),
    "camera": ((), ("position", "heading", "pitch", "bank", "look_at", "fov"),
               "Move or aim the render camera. heading 0 looks along +z, 90 along +x; "
               "pitch is positive upwards; bank tilts the picture (positive makes the "
               "horizon drop on the right); fov is in degrees across the picture's diagonal."),
    "document": ((), ("size", "antialiasing", "optimisation", "render_type",
                      "report_render_time"),
                 "Picture size and render settings, saved in the scene. size is [width, "
                 "height] or one of Bryce's sizes: default 480x360, small 320x240, "
                 "standard 640x480, super_16mm 640x384, cinemascope 640x240, photo "
                 "768x512. antialiasing off, normal or fine_art (slow); optimisation "
                 "(spatial) off, low or high; render_type normal, mask, distance or "
                 "altitude. Preview keeps its own size."),
    "keyframes": ((), ("name", "keys", "target", "curve", "duration"),
                  "Animate on Bryce's own timeline. target is object (default), camera, "
                  "sun, sky, material or light; object, material and light also need "
                  "name. keys is a list like "
                  '[{"frame": 0, "position": [x,y,z]}, {"time": 2, "position": [...]}], '
                  "each with frame or time (seconds) and settings: object - position, "
                  "rotation (degrees), scale; camera - position, heading, pitch, bank, "
                  "look_at, fov; sun - azimuth, altitude, disc_color, halo_color; sky - "
                  "the names br3_info lists under sky; material - the set_material "
                  "names; light - color, intensity (0-100). It replaces the keys of the "
                  "channels it names (heading, pitch, bank and look_at are one channel, "
                  "orientation; azimuth and altitude are direction); a channel given "
                  "only once stops being animated. Camera headings are used as written, "
                  "so 0 to 360 is a full turn; look_at turns the short way. For an "
                  "object spin, put keys less than 180 degrees apart. duration "
                  "(seconds) sets the animation's length, which always reaches the last "
                  "key. curve shapes the timing from a channel's first key to its last "
                  "(this is Bryce's own time curve): "
                  '"linear", "ease", "ease_in", "ease_out", "hold", or points '
                  '[[time, progress], ..., [1, 1]] from [0, 0], both 0 to 1, each '
                  'optionally "smooth" (default) or "straight"; or one of these per '
                  'channel, {"position": "ease"}. Give curve without keys to reshape '
                  "existing motion. As in Bryce, position moves on a smooth curve through "
                  "all its keys (with only two keys it runs straight but starts and ends "
                  "faster than its middle; three or more keys can swing wide at a turn), "
                  "rotation turns smoothly from key to key, and other settings change "
                  "evenly. Position, rotation and scale keys have been seen "
                  "playing in Bryce; curves and the other targets have not yet."),
    "time_marker": ((), ("frame", "time"),
                    "Move Bryce's time marker (give frame or time in seconds). The scene "
                    "then opens, previews and renders at that moment, with every "
                    "animated object and setting worked out the way Bryce does it."),
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
    scene.refresh_tracking()             # later operations may have moved a target
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
    if op == "join":
        return scene.join(fields["name"], fields["to"], library)
    if op == "unjoin":
        return scene.unjoin(fields["name"], library)
    if op == "track":
        return scene.track(fields["name"], fields["target"])
    if op == "untrack":
        return scene.untrack(fields["name"], library)
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
    if op == "path":
        return scene.add_path(
            fields["points"], fields.get("name"), bool(fields.get("closed", False)),
            bool(fields.get("smooth", True)), donors=(library, sources.donor("Paths.obp")))
    if op == "follow_path":
        return _follow(scene, library, sources, fields)
    if op == "keyframes":
        return scene.set_keyframes(library=library, **fields)
    if op == "time_marker":
        return {"timeline": scene.set_time(fields)}
    if op == "document":
        return {"document": scene.set_document(**fields)}
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


def _follow(scene: Br3, library: Br3 | None, sources: _Sources, fields: dict) -> dict:
    if fields.pop("native", False):
        extra = sorted(set(fields) - {"name", "path", "start", "end", "reverse",
                                      "curve", "duration"})
        if extra or not fields.get("name") or not fields.get("path"):
            raise BryceError("native takes name and path, with start, end, reverse, "
                             f"curve and duration{'; not ' + ', '.join(extra) if extra else ''}")
        return scene.follow_native(fields.pop("name"), fields.pop("path"),
                                   library=library, **fields)
    given = [key for key in ("path", "preset", "points") if fields.get(key) is not None]
    if len(given) != 1:
        raise BryceError("give one of path, preset or points")
    source = given[0]
    value = fields.pop(source)
    centre, scale = fields.pop("centre", None), fields.pop("scale", None)
    turn, closed = fields.pop("turn", None), fields.pop("closed", None)
    if source == "path":
        kind, ref = scene.find(value)
        if kind != "path":
            raise BryceError(f"{value!r} is not a path object")
        route, loops, shaping = scene.path_route(ref)
        shape = "bryce"
    elif source == "preset":
        found = presets.find(sources.folder(), "objects", value, "Paths")
        shelf = presets.load(found)
        refs = [ref for tag, ref, _ in shelf._list_items(("C3dRootObjList", found.key))
                if tag == motion.PATH_TAG]
        if not refs:
            raise BryceError(f"{value!r} holds no path")
        route, loops, shaping = shelf.path_route(refs[0])
        shape = "bryce"
    else:
        route, shaping = motion.parse_points(value)
        loops = False
        # Shaped points get Bryce's curve; plain ones the loop-free one.
        shape = "even" if shaping is None else "bryce_loop" if closed else "bryce"
    if centre is not None or scale is not None or turn is not None:
        middle = [(max(p[a] for p in route) + min(p[a] for p in route)) / 2
                  for a in range(3)]
        factor = 1.0 if scale is None else scale
        if not isinstance(factor, (int, float)) or not 0 < factor < 1e4:
            raise BryceError("scale must be one positive number")
        if centre is not None:
            centre = motion.parse_points([centre, [v + 1 for v in centre]], "centre")[0][0]
        route = motion.place_route(
            [[v - m for v, m in zip(p, middle)] for p in route],
            middle if centre is None else centre, factor, float(turn or 0))
    return scene.follow(route, loops if closed is None else bool(closed),
                        library=library, shape=shape, shaping=shaping, **fields)


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
