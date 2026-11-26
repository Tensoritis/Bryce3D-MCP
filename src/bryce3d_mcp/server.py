"""MCP tool definitions."""

from __future__ import annotations

import functools
import io
import json
import os
import re
import threading
from typing import Any, Callable, Literal

try:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp import Image
    from mcp.server.fastmcp.exceptions import ToolError

from . import layout, meshes, scene
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
        except (BryceError, ValueError, OSError) as exc:
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
    """Named keyboard commands, each flagged verified and destructive."""
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
def render(timeout_seconds: float = 600, dismiss_report: bool = False) -> list:
    """Render and wait until the viewport stops changing, then return the picture.
    dismiss_report clicks the Render Report's check mark afterwards; use it only
    after a screenshot shows the report."""
    if not 10 <= timeout_seconds <= 3600:
        raise BryceError("timeout_seconds must be 10-3600")

    def run(c: BryceController):
        result = c.render(timeout_seconds)
        shot = Image(data=c.screenshot_png(), format="png")
        if dismiss_report and result["finished"]:
            c.backend.click(*layout.RENDER_REPORT_OK)
        return [json.dumps(result), shot]
    return _ui(run)


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
def save_scene_as(path: str, overwrite: bool = False) -> list:
    """Save the scene to a new path (Ctrl+Shift+S). UNVERIFIED: check file_exists."""
    def run(c):
        target = c.save_scene_as(path, overwrite)
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
