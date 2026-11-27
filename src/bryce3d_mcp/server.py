"""MCP tool definitions."""

from __future__ import annotations

import functools
import io
import json
import math
import os
import re
import struct
import sys
import threading
from typing import Any, Callable, Literal

try:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp import Image
    from mcp.server.fastmcp.exceptions import ToolError

from . import animation, heightfield, layout, meshes, presets, preview, scene, sceneops, terrain
from .br3 import Br3
from .ccmf import CompositeFile
from .commands import COMMANDS
from .controller import BryceController
from .errors import BryceError

INSTRUCTIONS = """\
Builds, renders and edits MetaCreations Bryce 3D scenes.

BUILD with the br3_ tools. They work on scene files:
1. br3_new starts a scene from the user's template file (br3_status explains it).
2. br3_edit applies a batch of changes and writes a NEW file, returning a preview
   picture and a report of what is in frame. Feed the returned path back in to keep
   building. br3_info lists everything in a scene.
3. br3_preview is approximate (no textures, clouds or reflections). br3_render gives
   Bryce's real picture of a scene.
br3_edit can also make NEW 3D shapes (op mesh: extrude, lathe, custom, OBJ), tilt
the camera (bank), place and recolor procedural textures, set lights and moon phase. br3_animate makes
keyframe and path animations as one scene per frame; br3_render_animation renders
them and joins the pictures.

FILES: load_file opens, merges or imports into the running Bryce; save_scene saves
it and checks the file was written. Bryce's own "save your changes?" box is
answered with answer_save_box. Never discard unsaved work without the user's yes.

RUN BRYCE with bryce_command (any menu item), bryce_file_command, bryce_dialogs and
screenshot. These use window messages and pictures of Bryce's own windows: they do
not move the mouse, type, or take focus.

INSIDE BRYCE (open_lab for the Terrain Editor, Materials Lab, Sky Lab and Motion
Lab, and everything else on screen): ask the user, then call bryce_connect_bridge. After that, click,
drag, hover, press_keys, type_text, create_object, build_scene and the other
interface tools drive Bryce with a VIRTUAL mouse and keyboard that only Bryce
sees; the user's own are left alone. Without the bridge these tools refuse unless
the user has set BRYCE3D_REAL_INPUT=1. Then:
- Call screenshot first. Coordinates are UI pixels from the top-left of the 640x480
  interface; a screenshot starts at y=-20, so UI y = image y - 20.
- hover makes Bryce name the control in its bottom-left status text.
- Tools marked UNVERIFIED have not been observed working on Bryce 3D.
- If a tool reports that Bryce lost focus or the user moved the mouse, stop and ask.

Units are Bryce units, Y is up, position is the object's center, a cube of scale 1
is 20.48 on a side and rests on the ground at y=10.24. Pass confirm=true or
overwrite=true only after the user has agreed.
"""

mcp = MCPServer("bryce3d", instructions=INSTRUCTIONS)
_lock = threading.Lock()
_controller: BryceController | None = None


def get_controller() -> BryceController:
    global _controller
    if _controller is None:
        from .win32 import Win32Backend
        _controller = BryceController(Win32Backend())
    return _controller


def _bridged(controller: BryceController) -> bool:
    return hasattr(controller.backend, "bridge")


def _ui(action: Callable[[BryceController], Any]) -> Any:
    """Run one interface action, one at a time: with the bridge's virtual input, or,
    only if the user has opted in, with the real mouse after focusing Bryce."""
    with _lock:
        controller = get_controller()
        if not _bridged(controller) and os.environ.get("BRYCE3D_REAL_INPUT") != "1":
            raise BryceError(
                "this tool uses a mouse inside Bryce. Call bryce_connect_bridge (once the "
                "user agrees) for a virtual one that leaves theirs alone. To let it use "
                "the real mouse and keyboard instead, the user can set "
                "BRYCE3D_REAL_INPUT=1.")
        controller.backend.begin()
        try:
            return action(controller)
        finally:
            controller.backend.end()


def connect_bridge() -> dict:
    """Load the bridge into the running Bryce and route input through it."""
    from . import bridge
    from .virtual import BridgeBackend
    controller = get_controller()
    if _bridged(controller):
        try:
            controller.backend.bridge.hello()
            return {"already_connected": True}
        except BryceError:
            disconnect_bridge()
    base = controller.backend
    folder = base.install_folder()
    if not folder:
        raise BryceError("Bryce 3D is not running")
    client = bridge.attach(base.process_id(), folder)
    controller.backend = BridgeBackend(base, client)
    return {"connected": True, **controller.backend.info()["bridge"]}


def disconnect_bridge() -> bool:
    controller = get_controller()
    if not _bridged(controller):
        return False
    wrapped = controller.backend
    controller.backend = wrapped.base
    try:
        wrapped.bridge.virtual(False)
    except BryceError:
        pass                                  # Bryce has gone; nothing is held
    wrapped.bridge.close()
    return True


def _quiet(action: Callable[[BryceController], Any]) -> Any:
    """Run one action without taking focus: window messages and window pictures only."""
    with _lock:
        return action(get_controller())


def _shoot(action: Callable[[BryceController], Any]) -> Callable[[BryceController], list]:
    def run(c: BryceController):
        result = action(c)
        return [json.dumps(result if result is not None else {"ok": True}),
                Image(data=c.screenshot_png(), format="png")]
    return run


def _with_shot(action: Callable[[BryceController], Any]) -> list:
    return _ui(_shoot(action))


def _quiet_shot(action: Callable[[BryceController], Any]) -> list:
    return _quiet(_shoot(action))


def tool(fn):
    """Register a tool. Expected errors are re-raised as ToolError so the agent can
    read them; the SDK hides the text of other exceptions."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (BryceError, ValueError, OSError, struct.error) as exc:
            raise ToolError(str(exc)) from exc

    mcp.tool(structured_output=False)(wrapper)
    return wrapper


# -- looking --------------------------------------------------------------------
@tool
def bryce_status() -> str:
    """Find Bryce and report screen size, where its interface is and how many menu
    items it has. Does not take focus."""
    return json.dumps(_quiet(lambda c: c.backend.info()))


@tool
def screenshot(scale: float = 1.0) -> Image:
    """Picture Bryce's own windows (UI y = image y - 20). It does not take focus,
    works while Bryce is behind other windows, and never includes other programs.
    Bryce must not be minimized."""
    if not 0.25 <= scale <= 3:
        raise BryceError("scale must be between 0.25 and 3")
    return Image(data=_quiet(lambda c: c.screenshot_png(scale=scale)), format="png")


@tool
def set_ui_origin(x: int, y: int) -> list:
    """Manual calibration: screen pixel of the top-left corner of the 640x480 interface."""
    return _with_shot(lambda c: c.backend.set_origin(x, y))


@tool
def list_catalog() -> str:
    """Names accepted by the building tools: primitives, categories, colors."""
    return json.dumps({
        "primitives": list(layout.PRIMITIVES),
        "boolean_modes": list(layout.ATTR_BOOLEAN),
        "simple_colors": list(layout.SIMPLE_COLORS),
        "material_categories": list(layout.MATERIAL_CATEGORIES),
        "object_categories": list(layout.OBJECT_CATEGORIES),
        "preset_grid": "5 rows (0-4) x 6 columns (0-5)",
    })


@tool
def list_commands() -> str:
    """Named keyboard commands, with where each shortcut comes from and whether it
    can discard work."""
    return json.dumps({name: vars(command) for name, command in COMMANDS.items()})


@tool
def bryce_connect_bridge(confirm: bool = False) -> str:
    """Load the bridge DLL into the running Bryce 3D. It gives Bryce a virtual mouse
    and keyboard (so the interface tools reach the Terrain, Materials, Sky and
    Motion Labs without touching the user's own), shows whether Bryce has unsaved
    changes, and helps follow renders. It loads code into the running program, so
    pass confirm=true once the user agrees; advise saving work first. It only loads
    into the supported Bryce 3D build and is gone when Bryce is closed. Security
    software may block it."""
    if not confirm:
        raise BryceError("this loads a DLL into the running Bryce; pass confirm=true once "
                         "the user has agreed (saving their work first is wise)")
    with _lock:
        return json.dumps(connect_bridge())


@tool
def bryce_disconnect_bridge() -> str:
    """Stop using the bridge. The DLL stays loaded but idle until Bryce is closed."""
    with _lock:
        return json.dumps({"disconnected": disconnect_bridge()})


# -- building -------------------------------------------------------------------
@tool
def create_object(kind: str, name: str | None = None, position: list[float] | None = None,
                  size: list[float] | None = None, rotation: list[float] | None = None,
                  color: str | None = None) -> list:
    """Create one primitive or light and optionally place, size, rotate, name and color it."""
    obj = {"type": kind, "name": name, "position": position, "size": size,
           "rotation": rotation, "material": color}
    steps = scene.expand([{k: v for k, v in obj.items() if v is not None}])
    return _with_shot(lambda c: scene.build(c, steps))


@tool
def build_scene(objects: list[dict], dry_run: bool = False, start_at: int = 0) -> list | str:
    """Build many objects from one description. The whole list is validated first.

    Each object: type (see list_catalog), and optionally name, position [x,y,z],
    size [x,y,z], rotation [degrees x,y,z], on_ground (position y becomes the base),
    boolean (neutral|positive|negative|intersect), material (a simple color name or
    {category,row,column}), repeat ({count,offset:[x,y,z]} or {count,radius}).
    Takes several seconds per object. Use dry_run=true to preview the expanded plan.
    """
    steps = scene.expand(objects)
    if dry_run:
        return json.dumps({"total": len(steps), "steps": steps})
    return _with_shot(lambda c: scene.build(c, steps, start_at))


@tool
def set_attributes(name: str | None = None, position: list[float] | None = None,
                   rotation: list[float] | None = None, size: list[float] | None = None,
                   boolean: Literal["neutral", "positive", "negative", "intersect"] | None = None
                   ) -> list:
    """Edit the selected object's exact values in Object Attributes.
    Position, rotation and size entry are verified; name and boolean are UNVERIFIED."""
    return _with_shot(lambda c: c.set_attributes(name, position, rotation, size, boolean))


@tool
def apply_material(color: str | None = None, category: str = "simple&fast",
                   row: int = 0, column: int = 0) -> list:
    """Apply a material preset to the selection: a simple color name, or a library cell.
    Verified for Simple&Fast; switching to other categories is UNVERIFIED."""
    if color is not None:
        if color.lower() not in layout.SIMPLE_COLORS:
            raise BryceError(f"unknown color; use one of {', '.join(layout.SIMPLE_COLORS)}")
        category = "simple&fast"
        row, column = layout.SIMPLE_COLORS[color.lower()]
    return _with_shot(lambda c: c.apply_material(category, row, column))


@tool
def browse_object_library(category: str) -> list:
    """Open an object library category (rocks&trees, mountains, ...) and show it. UNVERIFIED
    beyond opening the library. Close it with click on the X, or use insert_library_object."""
    return _with_shot(lambda c: c.open_object_library(category))


@tool
def insert_library_object(category: str, row: int, column: int) -> list:
    """Insert a preset from the object library into the scene. UNVERIFIED."""
    return _with_shot(lambda c: c.insert_library_object(category, row, column))


@tool
def run_command(command: str, confirm: bool = False) -> list:
    """Run a named command from list_commands (select_all, duplicate, undo, group, ...)."""
    return _with_shot(lambda c: c.run_command(command, confirm))


@tool
def render(mode: Literal["resume", "clear", "button"] = "clear", wait: bool = True,
           timeout_seconds: float = 600, settle_seconds: float = 6,
           dismiss_report: bool = False) -> list:
    """Start a render of whatever Bryce has open. clear (File/Clear and Render) and
    resume (File/Render) go through Bryce's menu by window message, without focus,
    the bridge. button clicks the render sphere (bridge or real input). With wait=true
    this returns once the picture has not changed for settle_seconds; raise that for
    heavy scenes. For long renders use wait=false, then render_status.
    dismiss_report clicks the Render Report's check mark; like mode="button" it needs
    the bridge (or real input)."""
    if not 10 <= timeout_seconds <= 3600:
        raise BryceError("timeout_seconds must be 10-3600")
    if not 3 <= settle_seconds <= 120:
        raise BryceError("settle_seconds must be 3-120")

    def run(c: BryceController):
        if wait:
            result = c.render(timeout_seconds, settle_seconds, mode=mode)
        else:
            c.start_render(mode)
            c.backend.sleep(1.0)
            result = {"started": True}
        shot = Image(data=c.screenshot_png(), format="png")
        if dismiss_report and result.get("finished"):
            c.backend.click(*layout.RENDER_REPORT_OK)
        return [json.dumps(result), shot]
    return _ui(run) if mode == "button" or dismiss_report else _quiet(run)


@tool
def render_status(watch_seconds: float = 15) -> list:
    """Watch the picture for up to watch_seconds. finished=true means it stopped
    changing, which a very slow render can also look like. Does not take focus."""
    if not 6 <= watch_seconds <= 50:
        raise BryceError("watch_seconds must be 6-50")
    return _quiet_shot(lambda c: c.wait_for_still_picture(watch_seconds, settle=5))


@tool
def save_render_image(name: str) -> list:
    """Save what Bryce's viewport shows now as a PNG, at on-screen size. For the
    full render file use br3_render_result."""
    picture = _quiet(lambda c: c.render_picture())
    path = _output_path(name, "png")
    picture.save(path)
    return [json.dumps({"path": path, "size": picture.size}), _png(picture)]


@tool
def br3_render(path: str, confirm: bool = False, timeout_seconds: float = 600,
               settle_seconds: float = 8, discard_changes: bool = False) -> list:
    """The real render of a scene file, without the user's mouse or keyboard: opens it
    in the running Bryce, runs Clear and Render from Bryce's menu (with the bridge,
    falls back to the render button), follows the render and returns the picture. It
    replaces the scene open in Bryce, so pass confirm=true once the user agrees.
    Unsaved work in Bryce is never thrown away: with the bridge it is detected and
    the tool stops unless discard_changes=true (only when the user says so); without
    it, Bryce's own save question stops the tool. finished=false means check the
    picture; a blank picture is never reported as finished. Bryce must not be
    minimized. A Render Report box may cover part of the picture."""
    if not 10 <= timeout_seconds <= 3600:
        raise BryceError("timeout_seconds must be 10-3600")
    if not 3 <= settle_seconds <= 120:
        raise BryceError("settle_seconds must be 3-120")

    def run(c: BryceController):
        result = c.open_and_render(path, confirm, timeout_seconds, settle_seconds,
                                   discard_changes=discard_changes)
        return [json.dumps(result), _png(c.render_picture())]
    return _quiet(run)


# -- terrain files ---------------------------------------------------------------
def _png(picture) -> Image:
    out = io.BytesIO()
    picture.save(out, "PNG")
    return Image(data=out.getvalue(), format="png")


def _enlarged(picture, height: int = 384):
    if picture.height >= height:
        return picture
    return picture.resize((picture.width * height // picture.height, height))


@tool
def terrain_list(path: str, first: int = 0, count: int = 36) -> list:
    """List the terrains stored in a Bryce file (an .obp object library such as
    Bryce's Presets/Mountains.obp, or a saved .br3 scene), with thumbnails.
    The terrain tools work on files and do not touch the running program."""
    if first < 0 or not 1 <= count <= 72:
        raise BryceError("first must be 0 or more and count 1-72")
    found = terrain.terrains(CompositeFile.load(path))
    shown = found[first:first + count]
    rows = [{"index": t.index, "name": t.name, "supported": heightfield.is_supported(t.payload)}
            for t in shown]
    summary = json.dumps({"total": len(found), "terrains": rows})
    return [summary, _png(terrain.contact_sheet(shown))] if shown else [summary]


@tool
def terrain_preview(path: str, index: int = 0) -> list:
    """Show one terrain from a file: height as gray (left) and lit relief (right)."""
    chosen = terrain.pick(CompositeFile.load(path), index)
    size, grid = terrain.read_grid(chosen)
    info = {"name": chosen.name, "grid": size, "lowest": round(min(grid), 4),
            "highest": round(max(grid), 4)}
    return [json.dumps(info), _png(_enlarged(terrain.preview(size, grid)))]


@tool
def terrain_export(path: str, name: str, index: int = 0) -> list:
    """Save a terrain's exact heights as a 16-bit grayscale PNG."""
    size, grid = terrain.read_grid(terrain.pick(CompositeFile.load(path), index))
    target = _output_path(name, "png")
    terrain.to_image16(size, grid).save(target)
    return [json.dumps({"path": target, "grid": size})]


@tool
def terrain_operations() -> str:
    """The editing operations terrain_edit accepts, with defaults and limits."""
    return json.dumps(terrain.describe_operations())


@tool
def terrain_edit(source_path: str, output_name: str, operations: list[dict] | None = None,
                 index: int = 0, heightmap_path: str | None = None,
                 terrain_name: str | None = None, description: str | None = None) -> list:
    """Edit a terrain exactly, writing a NEW copy of a Bryce file; the source is not
    changed. Optionally start from heightmap_path (any picture, white is high; 16-bit
    PNG keeps precision), then apply operations in order, e.g.
    [{"op":"generate","shape":"mountain","seed":3},{"op":"hydraulic_erosion"}].
    See terrain_operations. The grid size stays that of terrain number `index`, so
    pick one with the detail you want (terrain_preview shows it). source_path can be
    one of Bryce's Presets/*.obp libraries or a saved .br3 scene.
    For terrains inside scenes you build, br3_edit's "terrain" operation is simpler."""
    extension = os.path.splitext(source_path)[1].lower()
    if extension not in (".obp", ".br3"):
        raise BryceError("source_path must be an .obp library or a .br3 scene")
    target = _output_path(output_name, extension.lstrip("."))  # a name not yet in use
    document = CompositeFile.load(source_path)
    chosen = terrain.pick(document, index)
    size, grid = terrain.read_grid(chosen)
    if heightmap_path is not None:
        from PIL import Image as Picture
        with Picture.open(heightmap_path) as picture:
            grid = terrain.grid_from_image(picture, size)
    grid = terrain.apply(size, grid, operations or [])
    terrain.write_grid(document, chosen, grid, terrain_name, description)
    if extension == ".br3":
        Br3(document).sync_terrain_copy(chosen.entry)
    with open(target, "xb") as handle:
        handle.write(document.data)
    next_step = ("In Bryce: open the object library (the arrow beside Create), choose Import, "
                 "pick this file, then select the terrain and accept." if extension == ".obp"
                 else "Render it with br3_render, or open it with br3_open_in_bryce.")
    result = {"path": target, "index": index, "grid": size, "next": next_step}
    return [json.dumps(result), _png(_enlarged(terrain.preview(size, grid)))]


# -- scene files: no mouse, keyboard or screen needed ----------------------------
def _templates_path(given: str | None) -> str:
    return (given or os.environ.get("BRYCE3D_TEMPLATES")
            or os.path.join(output_directory(), "templates.br3"))


def _library(given: str | None) -> Br3 | None:
    path = _templates_path(given)
    if os.path.isfile(path):
        return Br3.load(path)
    if given:
        raise BryceError(f"template file not found: {path}")
    return None


def _install_folder() -> str | None:
    """Where Bryce is installed: BRYCE3D_INSTALL, else wherever it is running from."""
    configured = os.environ.get("BRYCE3D_INSTALL")
    if configured:
        return configured
    try:
        return get_controller().backend.install_folder()
    except (BryceError, OSError, AttributeError):
        return None


def _write_scene(document: Br3, output_name: str) -> str:
    target = _output_path(output_name, "br3")
    with open(target, "xb") as handle:
        handle.write(document.data)
    return target


def _preview_result(document: Br3, info: dict, width: int = 480, height: int = 360) -> list:
    picture, report = preview.render(document, width, height)
    info["in_preview"] = report
    return [json.dumps(info), _png(picture)]


@tool
def br3_status(templates_path: str | None = None) -> str:
    """Report where scene files are written, and whether the template file exists
    and which object kinds it can supply. The template file is any .br3 saved by
    the user's own Bryce that contains one object of each kind they want to add."""
    path = _templates_path(templates_path)
    info: dict[str, Any] = {"output_folder": output_directory(), "templates_path": path,
                            "templates_found": os.path.isfile(path)}
    if info["templates_found"]:
        have = sorted({o["kind"] for o in Br3.load(path).objects()})
        info["can_add"] = have
        info["missing"] = sorted(set(sceneops.KINDS) - set(have))
    else:
        info["how_to_make_templates"] = (
            "In Bryce, create one of each object from the Create palette, then save the "
            "scene as templates.br3 in the output folder.")
    return json.dumps(info)


@tool
def br3_presets(category: Literal["objects", "materials", "skies"],
                contains: str = "") -> str:
    """Names and descriptions of Bryce's own presets, read from the user's Bryce
    folder: ready-made models (insert_preset), materials such as Mirror and
    Metallic Chrome (material_preset) and skies (sky_preset). Also lists the
    texture names the texture operation can use when category is materials."""
    folder = presets.folder(_install_folder())
    wanted = contains.lower()
    result: dict[str, Any] = {"presets": [
        {"name": p.name, "library": p.library, "about": p.description[:160]}
        for p in presets.entries(folder, category)
        if wanted in (p.name + " " + p.description).lower()]}
    if category == "materials":
        names: set[str] = set()
        for name in ("Boolean Objects.obp", "Tutorial.obp", "Simple&Fast.mat"):
            donor = presets.donor(folder, name)
            names.update(donor.textures() if donor else ())
        result["textures"] = sorted(names)
    return json.dumps(result)


@tool
def br3_operations() -> str:
    """The operations br3_edit accepts, with their fields."""
    return json.dumps(sceneops.describe())


@tool
def br3_info(path: str) -> str:
    """Everything editable in a scene: objects (exact position, size, rotation,
    material), camera, sun and sky settings."""
    document = Br3.load(path)
    return json.dumps({**document.summary(), "sky": document.sky(),
                       "sky_switches": document.sky_switches()})


@tool
def br3_new(output_name: str, keep: list[str] | None = None,
            templates_path: str | None = None, operations: list[dict] | None = None) -> list:
    """Start a new scene: the template file with every object removed except those
    named in `keep` (default: the ground, "Plane 1"). Optionally apply br3_edit
    operations at once. Camera, sun and sky come from the template file."""
    library = _library(templates_path)
    if library is None:
        raise BryceError(f"no template file at {_templates_path(templates_path)}; "
                         "see br3_status")
    document = Br3.from_bytes(library.data)
    document.remove_all_except(set(["Plane 1"] if keep is None else keep))
    results = sceneops.apply(document, operations or [], library, _install_folder())
    path = _write_scene(document, output_name)
    return _preview_result(document, {"path": path, "results": results})


@tool
def br3_edit(source_path: str, output_name: str, operations: list[dict],
             templates_path: str | None = None) -> list:
    """Apply a batch of changes to a scene and write the result as a NEW .br3 file
    (the source is never changed). Either every operation succeeds or nothing is
    written. See br3_operations. Example:
    [{"op":"add","kind":"sphere","name":"Moon","position":[0,60,40],
      "scale":[2,2,2],"material":{"color":"#d8d8c8"}},
     {"op":"camera","position":[0,45,-160],"look_at":[0,25,0]},
     {"op":"sun","azimuth":220,"altitude":35}]"""
    document = Br3.load(source_path)
    results = sceneops.apply(document, operations, _library(templates_path),
                             _install_folder())
    path = _write_scene(document, output_name)
    return _preview_result(document, {"path": path, "results": results})


@tool
def br3_animate(source_path: str, output_name: str, frames: int, tracks: list[dict],
                fps: int = 12, easing: Literal["linear", "smooth", "ease"] = "smooth",
                templates_path: str | None = None) -> list:
    """Make an animation: one scene file per frame from keyframes and paths, plus a
    preview GIF. (Bryce's own timeline is not used; render the frames with
    br3_render_animation.) Each track is {"target": "camera"|"object"|"sun"|"sky",
    "name": object name, "keys": [{"frame": 0, ...}], "path": {...}}.
    Keys: camera position, look_at, heading, pitch, bank, fov; object position,
    rotation, scale, size, material; sun azimuth, altitude, disc_color, halo_color;
    sky settings. A camera or object may instead follow
    "path": {"points": [[x,y,z],...], "closed": false, "follow": false,
    "heading_offset": 0, "start": 0, "end": last} at a steady speed; follow turns it
    to face the way it travels. A camera track may give "look_at_object": name.
    Example: orbit is a camera track with a closed path and look_at_object."""
    if not 1 <= fps <= 60:
        raise BryceError("fps must be 1-60")
    original = Br3.load(source_path)
    where = {o["name"]: o["position"] for o in original.objects()}
    steps = animation.plan(frames, tracks, easing, where.get)
    library, install = _library(templates_path), _install_folder()
    folder = _new_folder(output_name)
    shown = sorted({round(i * (frames - 1) / (min(frames, 48) - 1))
                    for i in range(min(frames, 48))})
    pictures = []
    for number, operations in enumerate(steps):
        document = Br3.from_bytes(original.data)
        try:
            sceneops.apply(document, operations, library, install)
        except BryceError as error:
            raise BryceError(f"frame {number}: {error}") from error
        with open(os.path.join(folder, f"frame_{number:04d}.br3"), "xb") as handle:
            handle.write(document.data)
        if number in shown:
            pictures.append(preview.render(document, 320, 240)[0].convert("RGB"))
    gif = os.path.join(folder, "preview.gif")
    _save_gif(pictures, gif, round(1000 * frames / fps / len(pictures)))
    with open(os.path.join(folder, "animation.json"), "x", encoding="utf-8") as handle:
        json.dump({"frames": frames, "fps": fps}, handle)
    sheet = _contact_sheet(pictures)
    return [json.dumps({"folder": folder, "frames": frames, "fps": fps,
                        "preview_gif": gif, "next": "br3_render_animation"}), _png(sheet)]


@tool
def br3_render_animation(folder: str, confirm: bool = False,
                         discard_changes: bool = False, count: int = 5,
                         timeout_seconds: float = 900) -> str:
    """Render an animation's frames in the running Bryce, `count` per call so that
    a call does not run for hours: call again until none remain. Finished frames are
    kept, so it can be resumed. When every frame is rendered, render.gif and the PNG
    frames are the result. Each frame replaces what Bryce has open (confirm=true).
    Unsaved work stops it unless discard_changes. Connect the bridge first."""
    if not confirm:
        raise BryceError("rendering replaces the scene open in Bryce; pass confirm=true "
                         "once the user has agreed")
    if not 1 <= count <= 100:
        raise BryceError("count must be 1-100")
    try:
        with open(os.path.join(folder, "animation.json"), encoding="utf-8") as handle:
            about = json.load(handle)
        total, fps = int(about["frames"]), int(about["fps"])
    except (OSError, KeyError, TypeError, ValueError):
        raise BryceError(f"{folder} is not a folder made by br3_animate") from None

    def frame(number, extension):
        return os.path.join(folder, f"frame_{number:04d}.{extension}")

    waiting = [n for n in range(total) if not os.path.isfile(frame(n, "png"))]
    done, problem = [], None
    for index, number in enumerate(waiting[:count]):
        def run(c, n=number, first=index == 0):
            # After the first frame, "unsaved" means the frame just rendered.
            discard = discard_changes if first else c.backend.unsaved_changes() is True
            return c.render_to_file(frame(n, "br3"), frame(n, "png"),
                                    timeout=timeout_seconds, discard_changes=discard)

        try:
            result = _quiet(run)
        except BryceError as error:
            result = {"finished": False, "reason": str(error)}
        if not result.get("finished"):
            problem = {"frame": number, **{k: v for k, v in result.items()
                                           if k != "timeline"}}
            break
        done.append(number)
    remaining = len(waiting) - len(done)
    report: dict[str, Any] = {"rendered_now": done, "remaining": remaining}
    if problem:
        report["stopped"] = problem
    if not remaining:
        from PIL import Image as Picture
        pictures = []
        for number in range(total):
            with Picture.open(frame(number, "png")) as handle:
                pictures.append(handle.convert("RGB"))
        report["gif"] = os.path.join(folder, "render.gif")
        _save_gif(pictures, report["gif"], round(1000 / fps))
    return json.dumps(report)


def _new_folder(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\- ]{1,60}", name):
        raise BryceError("name: 1-60 letters, digits, spaces, - or _")
    stem = os.path.join(output_directory(), name)
    path, number = stem, 2
    while os.path.exists(path):  # never overwrite
        path, number = f"{stem}-{number}", number + 1
    os.makedirs(path)
    return path


def _save_gif(pictures, path: str, milliseconds: int) -> None:
    pictures[0].save(path, save_all=True, append_images=pictures[1:],
                     duration=max(20, milliseconds), loop=0)


def _contact_sheet(pictures, columns: int = 4, rows: int = 2):
    """A few frames side by side, so that motion can be reviewed in one picture."""
    from PIL import Image as Picture
    number = min(len(pictures), columns * rows)
    chosen = [pictures[round(i * (len(pictures) - 1) / max(1, number - 1))]
              for i in range(number)]
    width, height = chosen[0].size
    sheet = Picture.new("RGB", (width * min(columns, number),
                                height * math.ceil(number / columns)), (0, 0, 0))
    for index, picture in enumerate(chosen):
        sheet.paste(picture, (width * (index % columns), height * (index // columns)))
    return sheet


@tool
def br3_preview(path: str, width: int = 640, height: int = 480) -> list:
    """Draw an approximate picture of a scene without Bryce, with a report of where
    each object lands (in frame, out of frame, hidden). Shows shapes, flat colors,
    the sun and ground shadows; not textures, clouds, reflections or lights."""
    return _preview_result(Br3.load(path), {"path": path, "approximate": True}, width, height)


@tool
def br3_terrain_picture(path: str, name: str) -> list:
    """Show the heights of one terrain object in a scene."""
    size, values = Br3.load(path).terrain_heights(name)
    grid = [v / heightfield.FULL for v in values]
    return [json.dumps({"name": name, "grid": size}),
            _png(_enlarged(terrain.preview(size, grid)))]


@tool
def br3_export_obj(path: str, output_name: str) -> str:
    """Export a scene's shapes and terrains as a Wavefront OBJ file for use in other
    software (geometry only; planes, lights and exact mesh objects are not exact)."""
    text, count = preview.to_obj(Br3.load(path))
    if not count:
        raise BryceError("the scene has nothing with a surface to export")
    target = _output_path(output_name, "obj")
    with open(target, "x", encoding="utf-8") as handle:
        handle.write(text)
    return json.dumps({"path": target, "objects": count})


@tool
def br3_open_in_bryce(path: str, confirm: bool = False,
                      discard_changes: bool = False) -> list:
    """Open a scene in the running Bryce, using the message a second copy of Bryce
    sends when it is launched with a file. No focus, mouse or keyboard. It can
    replace the open scene, so pass confirm=true once the user agrees. Unsaved work
    stops it (see br3_render about discard_changes)."""
    return _quiet_shot(lambda c: c.open_scene(path, confirm, discard_changes))


@tool
def br3_render_result(scene_path: str, export_name: str | None = None) -> list:
    """Return the picture Bryce wrote next to a scene (same name, .bmp): a
    full-resolution real render, read from disk with no screen capture. Bryce has
    been seen writing it around the end of a render or on save. With export_name,
    also save it as a PNG."""
    from PIL import Image as Picture
    bitmap = os.path.splitext(scene_path)[0] + ".bmp"
    if not os.path.isfile(bitmap):
        raise BryceError(f"{bitmap} does not exist yet: open the scene in Bryce, render, "
                         "and if it still does not appear, save in Bryce")
    with Picture.open(bitmap) as handle:
        picture = handle.convert("RGB")
    info: dict[str, Any] = {"bitmap": bitmap, "size": picture.size}
    if os.path.isfile(scene_path) and os.path.getmtime(bitmap) < os.path.getmtime(scene_path):
        info["warning"] = "the picture is older than the scene file; render again"
    if export_name:
        info["exported"] = _output_path(export_name, "png")
        picture.save(info["exported"])
    return [json.dumps(info), _png(picture)]


# -- raw interaction: reaches everything else ------------------------------------
@tool
def hover(x: int, y: int) -> list:
    """Point at a control without clicking. Bryce shows its name bottom-left."""
    def run(c):
        c.backend.move(x, y)
        c.backend.sleep(0.6)
    return _with_shot(run)


@tool
def click(x: int, y: int, button: Literal["left", "right"] = "left", count: int = 1) -> list:
    """Click at UI coordinates. A click can change or delete things: look first."""
    if not 1 <= count <= 3:
        raise BryceError("count must be 1-3")

    def run(c):
        c.backend.click(x, y, button, count)
        c.backend.sleep(0.6)
    return _with_shot(run)


@tool
def drag(start_x: int, start_y: int, end_x: int, end_y: int,
         button: Literal["left", "right"] = "left", duration: float = 0.6) -> list:
    """Drag: camera trackball, sliders, terrain painting, transform tools."""
    if not 0.05 <= duration <= 10:
        raise BryceError("duration must be 0.05-10 seconds")

    def run(c):
        c.backend.drag((start_x, start_y), (end_x, end_y), button, duration)
        c.backend.sleep(0.6)
    return _with_shot(run)


@tool
def press_keys(keys: str, confirm: bool = False) -> list:
    """Press a combination such as ctrl+g, escape, shift+left or pagedown."""
    return _with_shot(lambda c: {"pressed": c.press(keys, confirm)})


@tool
def type_text(text: str) -> list:
    """Type into the focused field. Click the field first: in the main view,
    characters act as shortcuts."""
    if not 0 < len(text) <= 260 or not text.isprintable():
        raise BryceError("text must be 1-260 printable characters")
    return _with_shot(lambda c: c.backend.type_text(text))


@tool
def list_menus() -> str:
    """List Bryce's menu (File, Edit, Objects, Help) with what is enabled right now.
    Does not take focus."""
    items = _quiet(lambda c: c.backend.menu())
    return json.dumps([{"path": i["path"], "enabled": i["enabled"]} for i in items])


@tool
def bryce_command(command: str, confirm: bool = False) -> list:
    """Run any item of Bryce's menu by window message, without focus, mouse or
    keyboard: for example render, undo, duplicate, replicate, select_all,
    group_objects, ungroup_objects, create_path, edit_object (the Terrain Editor for
    a terrain), attributes, edit_material, motion_lab, or a path such as
    "Edit/Multi-Replicate". Commands act on what is selected in Bryce. For commands
    that ask for a file use bryce_file_command. See list_menus."""
    return _quiet_shot(lambda c: {"ran": c.command(command, confirm)})


@tool
def bryce_file_command(command: Literal["open", "merge", "import_object", "export_image",
                                        "render_to_disk", "save_as"],
                       path: str, confirm: bool = False) -> list:
    """Run a menu command that asks for a file and answer its Windows dialog, all by
    window message. open, merge and import_object read an existing file; the others
    write a new one. Bryce may then show its own options box; check the picture."""
    if not confirm:
        raise BryceError("this reads into the open scene or writes a file; pass "
                         "confirm=true once the user has agreed")
    writes = command in ("export_image", "render_to_disk", "save_as")
    return _quiet_shot(lambda c: c.run_with_path(
        "File/" + command.replace("_", " "), path,
        ".br3" if command == "save_as" else "", new_file=writes))


@tool
def open_lab(lab: Literal["terrain", "materials", "sky", "motion"]) -> list:
    """Open one of Bryce's editors and show it. Select the object in Bryce first
    (a terrain for the Terrain Editor, any object for the Materials Lab). Work
    inside it with hover, click and drag, which use the bridge's virtual mouse once
    bryce_connect_bridge has been called, so the user's mouse stays free. Close it
    with its own OK or Cancel button. The sky lab is opened with a key press and is
    UNVERIFIED. Terrains, materials and most sky settings can also be edited in the
    scene file with br3_edit, which needs no lab at all."""
    if lab == "sky":
        return _with_shot(lambda c: c.run_command("sky_lab"))
    command = {"terrain": "edit_object", "materials": "edit_material",
               "motion": "motion_lab"}[lab]
    return _quiet_shot(lambda c: {"ran": c.command(command)})


@tool
def bryce_dialogs() -> str:
    """Standard Windows dialogs Bryce has open (file pickers, questions), with their
    text and buttons. Boxes that Bryce draws itself show only in screenshot."""
    return json.dumps(_quiet(lambda c: c.dialogs()))


@tool
def press_dialog_button(button: str, confirm: bool = False) -> list:
    """Press a button, by its label, on a Windows dialog Bryce has open."""
    return _quiet_shot(lambda c: {"pressed": c.press_dialog_button(button, confirm)})


# -- files ----------------------------------------------------------------------
@tool
def save_scene(path: str | None = None, confirm: bool = False,
               overwrite: bool = False) -> list:
    """Save the scene open in Bryce. Without a path it saves over the scene's own
    file; with a path (.br3) it uses Save As, and waits until the file is really
    written. Existing files are kept unless overwrite=true. By window message."""
    return _quiet_shot(lambda c: c.save_scene(path, confirm, overwrite))


@tool
def load_file(path: str, how: Literal["open", "merge", "import_object"] = "open",
              unsaved: Literal["stop", "save", "discard"] = "stop",
              save_current_to: str | None = None, confirm: bool = False) -> list:
    """Load a file into the running Bryce: open a .br3 scene, merge a scene into the
    current one, or import a model (such as OBJ or DXF). When opening, `unsaved`
    decides about unsaved work: stop (default), save it first (to save_current_to),
    or discard it (only if the user said so). With the bridge connected, Bryce's
    "save your changes?" question is prevented or answered."""
    return _quiet_shot(lambda c: c.load_file(path, how, unsaved, save_current_to, confirm))


@tool
def answer_save_box(choice: Literal["dont_save", "cancel", "save"],
                    confirm: bool = False) -> list:
    """Answer Bryce's own "Do you want to save your changes?" box, which is not a
    Windows dialog. dont_save and save need confirm=true. Uses the bridge's mouse."""
    return _with_shot(lambda c: c.answer_save_box(choice, confirm))


@tool
def submit_file_dialog(path: str) -> list:
    """Put a path in a Windows file dialog that Bryce already has open (for example
    one opened by the Terrain Editor's Picture button) and accept it."""
    return _quiet_shot(lambda c: c.fill_file_dialog(path))


def output_directory() -> str:
    path = os.environ.get("BRYCE3D_OUTPUT_DIR") or os.path.join(
        os.path.expanduser("~"), "Documents", "Bryce3D-MCP")
    os.makedirs(path, exist_ok=True)
    return path


def _output_path(name: str, extension: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\- ]{1,60}", name):
        raise BryceError("name: 1-60 letters, digits, spaces, - or _")
    path = os.path.join(output_directory(), f"{name}.{extension}")
    stem, number = os.path.join(output_directory(), name), 2
    while os.path.exists(path):  # never overwrite
        path = f"{stem}-{number}.{extension}"
        number += 1
    return path


@tool
def generate_mesh(name: str, shape: Literal["extrude", "lathe", "custom"],
                  outline: list[list[float]] | None = None, height: float | None = None,
                  profile: list[list[float]] | None = None, segments: int = 32,
                  vertices: list[list[float]] | None = None,
                  faces: list[list[int]] | None = None,
                  file_format: Literal["obj", "dxf"] = "obj") -> str:
    """Write a model file for import into Bryce. (To put a new shape straight into a
    scene file, use br3_edit's mesh operation instead.)
    extrude: outline [[x,z],...] and height. lathe: profile [[radius,y],...] bottom to
    top. custom: vertices and zero-based faces, counter-clockwise from outside."""
    if shape == "extrude":
        if outline is None or height is None:
            raise BryceError("extrude needs outline and height")
        vertices, faces = meshes.extrude(outline, height)
    elif shape == "lathe":
        if profile is None:
            raise BryceError("lathe needs profile")
        vertices, faces = meshes.lathe(profile, segments)
    elif vertices is None or faces is None:
        raise BryceError("custom needs vertices and faces")
    text = (meshes.to_obj(vertices, faces, name) if file_format == "obj"
            else meshes.to_dxf(vertices, faces))
    path = _output_path(name, file_format)
    with open(path, "w", encoding="ascii", newline="\r\n") as handle:
        handle.write(text)
    return json.dumps({"path": path, "vertices": len(vertices), "faces": len(faces)})


@tool
def generate_heightmap(name: str, shape: str, size: int = 256, seed: int = 0,
                       roughness: float = 0.5) -> list:
    """Write a grayscale BMP for the Terrain Editor's picture import.
    shape: mountain, island, crater, canyon, dunes or plateau."""
    image = meshes.heightmap(shape, size, seed, roughness)
    path = _output_path(name, "bmp")
    image.save(path)
    preview = io.BytesIO()
    image.save(preview, "PNG")
    return [json.dumps({"path": path}), Image(data=preview.getvalue(), format="png")]


def check() -> int:
    """Find the running Bryce and save a screenshot: a test of the Windows layer."""
    try:
        controller = get_controller()
        print(json.dumps(controller.backend.info(), indent=2))
        path = _output_path("check-screenshot", "png")
        with open(path, "wb") as handle:
            handle.write(controller.screenshot_png())
    except (BryceError, OSError) as exc:
        print(f"CHECK FAILED: {exc}")
        return 1
    print(f"Screenshot saved to {path}")
    print("OK if it shows the whole Bryce interface and nothing else.")
    return 0


def check_bridge() -> int:
    """Load the bridge into the running Bryce and report what it sees. Sends no
    input and changes nothing in the scene."""
    try:
        print(json.dumps(connect_bridge()))
        controller = get_controller()
        client = controller.backend.bridge
        report = {"hello": client.hello(), "state": client.state(),
                  "windows": client.windows()}
        samples = []
        for _ in range(5):                         # idle activity, for tuning
            controller.backend.sleep(1.0)
            state = client.state()
            samples.append({k: state[k] for k in ("ticks", "peeks", "checks", "blits",
                                                   "cpu_ms")})
        report["idle_samples"] = samples
        path = _output_path("bridge-report", "json")
        with open(path, "x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
        main_window = controller.backend._main_window()
        picture = _output_path("bridge-capture", "png")
        client.capture(main_window["window"]).save(picture)
        disconnect_bridge()
    except (BryceError, OSError) as exc:
        print(f"BRIDGE CHECK FAILED: {exc}")
        return 1
    print(f"Bridge works. Report: {path}\nPicture: {picture}")
    return 0


def main() -> None:
    if "--check-bridge" in sys.argv[1:]:
        raise SystemExit(check_bridge())
    if "--check" in sys.argv[1:]:
        raise SystemExit(check())
    mcp.run()


if __name__ == "__main__":
    main()
