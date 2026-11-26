"""MCP tool definitions."""

from __future__ import annotations

import functools
import io
import json
import os
import re
import struct
import threading
from typing import Any, Callable, Literal

try:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp import Image
    from mcp.server.fastmcp.exceptions import ToolError

from . import heightfield, layout, meshes, scene, terrain
from .ccmf import CompositeFile
from .commands import COMMANDS
from .controller import BryceController
from .errors import BryceError

INSTRUCTIONS = """\
Controls MetaCreations Bryce 3D by driving its real interface on this Windows PC.
Bryce has no scripting API, so look before and after acting:
- Call bryce_status then screenshot first.
- Prefer build_scene / create_object / set_attributes: they take exact numbers.
  Units are Bryce units, Y is up, position is the object's center, a default cube
  is 20.48 on each side and rests on the ground at y=10.24.
- For anything without a dedicated tool, use hover (Bryce names the control in its
  bottom-left status text), then click, drag, press_keys or list_menus.
- Terrain: use terrain_edit. It edits exact heights inside Bryce's own file format,
  which is far more precise than painting in the Terrain Editor.
- Tools marked UNVERIFIED have not been observed working on Bryce 3D: check the result.
- Coordinates are UI pixels from the top-left of the 640x480 interface. A screenshot
  starts at y=-20, so UI y = image y - 20 and UI x = image x.
- Pass confirm=true or overwrite=true only after the user has agreed.
- If a tool reports that Bryce lost focus or the user moved the mouse, stop and ask.
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


def _ui(action: Callable[[BryceController], Any]) -> Any:
    """Run one UI action: one at a time, with Bryce focused first."""
    with _lock:
        controller = get_controller()
        controller.backend.begin()
        return action(controller)


def _with_shot(action: Callable[[BryceController], Any]) -> list:
    def run(c: BryceController):
        result = action(c)
        return [json.dumps(result if result is not None else {"ok": True}),
                Image(data=c.screenshot_png(), format="png")]
    return _ui(run)


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
    """Find Bryce, focus it, and report screen size, UI origin and native menu count."""
    return json.dumps(_ui(lambda c: c.backend.info()))


@tool
def screenshot(scale: float = 1.0) -> Image:
    """Capture the Bryce interface and the pop-ups around it (UI y = image y - 20)."""
    if not 0.25 <= scale <= 3:
        raise BryceError("scale must be between 0.25 and 3")
    return Image(data=_ui(lambda c: c.screenshot_png(scale=scale)), format="png")


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
def render(mode: Literal["button", "resume", "clear"] = "button", wait: bool = True,
           timeout_seconds: float = 600, settle_seconds: float = 6,
           dismiss_report: bool = False) -> list:
    """Start a render. mode: button (the render sphere, tested by hand), resume
    (Ctrl+R) or clear (Ctrl+Alt+R, restarts from nothing); the shortcuts come from
    Bryce's menu table. With wait=true this returns once the picture has not changed
    for settle_seconds; raise that for heavy scenes. For long renders use wait=false,
    then render_status. dismiss_report clicks the Render Report's check mark; use it
    only after a screenshot shows the report."""
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
    return _ui(run)


@tool
def render_status(watch_seconds: float = 15) -> list:
    """Watch the picture for up to watch_seconds. finished=true means it stopped
    changing, which a very slow render can also look like."""
    if not 6 <= watch_seconds <= 50:
        raise BryceError("watch_seconds must be 6-50")
    return _with_shot(lambda c: c.wait_for_still_picture(watch_seconds, settle=5))


@tool
def save_render_image(name: str) -> list:
    """Save what the viewport shows now as a PNG. This is a screen capture, so its
    resolution is the on-screen size."""
    picture = _ui(lambda c: c.render_picture())
    path = _output_path(name, "png")
    picture.save(path)
    return [json.dumps({"path": path, "size": picture.size}), _png(picture)]


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
    NOT YET CONFIRMED: loading the written file into a running Bryce."""
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
    with open(target, "xb") as handle:
        handle.write(document.data)
    next_step = ("In Bryce: open the object library (the arrow beside Create), choose Import, "
                 "pick this file, then select the terrain and accept." if extension == ".obp"
                 else "Open it in Bryce with open_scene (this discards unsaved work).")
    result = {"path": target, "index": index, "grid": size, "next": next_step}
    return [json.dumps(result), _png(_enlarged(terrain.preview(size, grid)))]


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
    """List Bryce's native Windows menu items, if the window has them. UNVERIFIED."""
    return json.dumps(_ui(lambda c: c.backend.menu()))


@tool
def invoke_menu(path: str, confirm: bool = False) -> list:
    """Run a menu item by path from list_menus, e.g. File/Import Object. UNVERIFIED."""
    return _with_shot(lambda c: {"invoked": c.invoke_menu(path, confirm)})


# -- files ----------------------------------------------------------------------
@tool
def open_scene(path: str, confirm: bool = False) -> list:
    """Open a Bryce scene (Ctrl+O). UNVERIFIED."""
    return _with_shot(lambda c: c.open_scene(path, confirm))


@tool
def save_scene(path: str | None = None, confirm: bool = False) -> list:
    """Press Save (Ctrl+S). A scene that already has a file is saved over it. An
    unnamed scene asks for a name, and `path` is typed there. UNVERIFIED."""
    def run(c):
        target = c.save_scene(path, confirm)
        if target is None:
            return {"saved": "in place (Bryce did not ask for a name)"}
        return {"requested_path": target, "file_exists": os.path.isfile(target)}
    return _with_shot(run)


@tool
def submit_file_dialog(path: str) -> list:
    """Type a path into a Windows file dialog that Bryce already has open, e.g. after
    invoke_menu File/Import Object or the Terrain Editor's Picture button."""
    return _with_shot(lambda c: c.fill_file_dialog(path))


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
    """Write a model file for import into Bryce, for shapes primitives cannot make.
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
