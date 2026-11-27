# Bryce3D-MCP

MCP Server to allow AI Agents to interact with the Bryce3D modeling, rendering and animation software.

Built against the original MetaCreations **Bryce 3D** (1997) on Windows. Bryce 3D has no
scripting API, so the file formats and parts of the program were reverse engineered. The
server works two ways:

1. **Scene files (preferred).** The agent builds and edits `.br3` scenes directly: objects,
   exact placement, colors and material values, terrain heights, camera and sun. Each edit
   returns a preview picture. This uses **no mouse, keyboard or screen**.
2. **Interface (fallback).** For things the files do not cover yet, the server sends mouse
   and keyboard input to the real program and returns screenshots. This takes over your
   mouse and keyboard while it runs.

## The agent loop

```
br3_new / br3_edit  ->  look at the preview  ->  br3_edit again  ->  ...
        |
        +-> br3_open_in_bryce -> render -> br3_render_result  (the real picture, saved as PNG)
        +-> br3_export_obj                                    (geometry for other software)
```

Every edit writes a **new** file, so any earlier step can be reopened. A batch either
applies completely or writes nothing.

```json
[
  {"op": "camera", "position": [0, 60, -190], "look_at": [0, 28, 0], "fov": 60},
  {"op": "sun", "azimuth": 235, "altitude": 38},
  {"op": "add", "kind": "terrain", "name": "Island", "position": [0, 6, 45],
   "scale": [7, 2.2, 5], "material": {"color": "#8d7652"}},
  {"op": "terrain", "name": "Island", "operations": [
    {"op": "generate", "shape": "mountain", "seed": 11},
    {"op": "edge_falloff"}, {"op": "normalize"}]},
  {"op": "add", "kind": "cylinder", "name": "Tower", "position": [-48, 22, -20],
   "size": [14, 44, 14], "material": {"color": "#e8e2d2"}},
  {"op": "add", "kind": "cone", "name": "Roof", "position": [-48, 53, -20],
   "size": [20, 18, 20], "material": {"color": "#b32a1e"}}
]
```

| Operation | What it does |
| --- | --- |
| `add` | New plane, cube, sphere, torus, cylinder, pyramid, cone, disk, terrain, lattice, mesh or one of four lights |
| `set` | Rename, move, scale or size, rotate, recolor an object |
| `duplicate`, `delete` | Copy or remove an object |
| `terrain` | Reshape a terrain from its own heights or any picture, using the terrain operations below |
| `camera` | Position, heading, pitch or `look_at`, field of view |
| `sun` | Azimuth (relative to the camera: 0 ahead, 90 right, 180 behind), altitude, colors |
| `sky` | Sky settings by name. Which ones show depends on the sky mode |

Units are Bryce units and Y is up. `position` is the object's center. A cube at scale 1 is
20.48 on each side. Material colors are `#rrggbb`. Material numbers (`diffuse`, `ambient`,
`specular`, `transparency`, `reflection`, `bump`, `metallic`, `density`, `refraction`) use
1.0 for 100%.

### One-time setup: the template file

New objects are copied from a scene saved by **your own** Bryce, so none of Bryce's data is
shipped here. In Bryce, create one of each object you want from the Create palette, then
save the scene as `templates.br3` in the output folder (or point `BRYCE3D_TEMPLATES` at it).
`br3_status` reports which kinds it can supply.

## Status - read this first

Version 0.3.0. The Python code has automated tests. **The server has not yet been run on
Windows.** The file engine was developed and tested elsewhere, and its output files were
then opened by hand in a real copy of Bryce 3D.

| What | State |
| --- | --- |
| Opening scenes edited by this code | **Confirmed in Bryce** |
| Move, scale, rotate about X and Y, material color, delete, sun direction | **Confirmed in Bryce** |
| Adding a sphere, cone, cylinder and torus to a scene that never had them | **Confirmed in Bryce** |
| Rendering an edited scene | **Confirmed in Bryce** (render started by hand) |
| Camera and sun records | Byte for byte what Bryce itself saves. A camera written by the final code has not been rendered yet |
| Terrain heights in scenes | Bryce kept them exactly when it re-saved. An edited terrain has not been seen in a render yet |
| Adding pyramid, disk, plane, lattice, mesh and lights; rotation about Z; duplicate; rename; material numbers | Automated tests only |
| Sky settings | Bryce kept one edited value, with no visible change in that sky mode |
| `br3_open_in_bryce` | Message format read from Bryce's own code, tested against a stand-in. **Not tried live** |
| Terrain library (`.obp`) reading and encoding | All 32 bundled terrains re-encode byte for byte. Importing an edited one is untried |
| Windows input and capture (`win32.py`) | **Never run on real Windows** |
| Button positions and dialogs used by the interface tools | Measured on a running copy of Bryce 3D |

### How good is the preview?

`br3_preview` draws shapes, flat colors, the ground, the sun and ground shadows in about a
second, and says for each object whether it is in frame, off an edge, behind the camera or
hidden. Compared with Bryce's own render of the same test scene, object outlines and
shadows lined up to within about 8 pixels at 480 x 360. It does **not** show textures,
clouds, atmosphere, reflections, transparency, object-on-object shadows or light objects.
Mesh objects appear as stand-ins. Use it for layout, and a Bryce render for the look.

## Install (Windows)

Needs Python 3.10 or newer on the same PC as Bryce 3D. Start Bryce, then double-click
`setup.bat`. It installs the server into `.venv`, checks that it can find and capture Bryce,
sets
the output folder to `workspace` inside this folder, and prints the command that registers it.
To do the same by hand:

```powershell
git clone https://github.com/Tensoritis/Bryce3D-MCP.git
cd Bryce3D-MCP
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m bryce3d_mcp --check
```

Claude Code:

```powershell
claude mcp add bryce3d -- C:\path\to\Bryce3D-MCP\.venv\Scripts\python.exe -m bryce3d_mcp
```

Claude Desktop and other clients (`claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "bryce3d": {
      "command": "C:\\path\\to\\Bryce3D-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "bryce3d_mcp"]
    }
  }
}
```

Then ask the agent to call `br3_status`. The `br3_` file tools work without Bryce running.

### Settings (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `BRYCE3D_OUTPUT_DIR` | `Documents\Bryce3D-MCP` | Where scenes, terrain files, renders and exports are written |
| `BRYCE3D_TEMPLATES` | `templates.br3` in the output folder | The template scene |
| `BRYCE3D_EXE` | `bryce 3d.exe` | Executable name used to find Bryce |
| `BRYCE3D_UI_ORIGIN` | auto | `x,y` screen pixel of the top-left of the 640x480 interface |

## Tools (42)

| Group | Tools | Uses your mouse or keyboard? |
| --- | --- | --- |
| Scene files | `br3_status`, `br3_operations`, `br3_new`, `br3_edit`, `br3_info`, `br3_preview`, `br3_terrain_picture`, `br3_export_obj`, `br3_render_result` | No |
| Open in Bryce | `br3_open_in_bryce` (sends a window message) | No |
| Terrain libraries | `terrain_list`, `terrain_preview`, `terrain_operations`, `terrain_edit`, `terrain_export` | No |
| Geometry files | `generate_mesh` (extrude, lathe, custom to OBJ or DXF), `generate_heightmap` | No |
| Render | `render`, `render_status`, `save_render_image` | Yes |
| Look | `bryce_status`, `screenshot`, `set_ui_origin`, `list_catalog`, `list_commands` | Brings Bryce to the front |
| Build by interface | `build_scene`, `create_object`, `set_attributes`, `apply_material`, `browse_object_library`, `insert_library_object`, `run_command` | Yes |
| Reach anything else | `hover`, `click`, `drag`, `press_keys`, `type_text`, `list_menus`, `invoke_menu` | Yes |
| Bryce files | `open_scene`, `save_scene`, `submit_file_dialog` | Yes |

### Rendering

Bryce does the real rendering. `br3_render_result` reads the `.bmp` that Bryce writes
beside a scene (same name), straight from disk, and can save it as a PNG. In testing the
bitmap sometimes appeared only after saving in Bryce. The tool warns if the bitmap is older
than the scene.

To start a render without touching Bryce yourself, the agent uses `render`, which briefly
brings Bryce to the front and presses Ctrl+R or the render button. I found no window
message in Bryce that starts a render, so this one step is not input-free. You can also just
press render yourself.

### Terrain operations

The `terrain` scene operation and the `terrain_edit` tool (for terrain libraries) share these
operations. Either can start from any picture (a 16-bit PNG keeps full precision):

```json
[
  {"op": "generate", "shape": "mountain", "seed": 11},
  {"op": "ridges", "amount": 0.3},
  {"op": "hydraulic_erosion", "droplets": 40000},
  {"op": "path", "points": [[0, 0.75], [0.45, 0.8], [1, 0.9]], "level": 0.04},
  {"op": "plateau", "x": 0.72, "y": 0.3, "radius": 0.07, "height": 0.42},
  {"op": "edge_falloff", "width": 0.06}
]
```

Operations: `generate`, `hill`, `crater`, `plateau`, `path`, `noise`, `ridges`,
`thermal_erosion`, `hydraulic_erosion`, `terrace`, `smooth`, `sharpen`, `normalize`,
`scale`, `offset`, `gamma`, `clip`, `invert`, `flip`, `rotate`, `edge_falloff`.

The grid size is that of the terrain you start from. Bryce's own libraries include 128, 256
and 512 grids.

## Interface tools

The interface tools can create all 30 objects on the Create palette (planes, terrain,
stone, lattice, solids, 2D objects, lights) with exact values and material presets. Sky
Lab, Materials Lab sliders, animation, booleans and importing meshes have no dedicated
tool: they are reachable through screenshots, `hover`, `click`, `drag` and keys.

### `build_scene` description

```json
[
  {"type": "cylinder", "name": "Column", "position": [0, 6, 0], "size": [8, 50, 8],
   "on_ground": true, "material": "white", "repeat": {"count": 10, "radius": 55}}
]
```

Units are Bryce units and Y is up. `position` is the object's center, unless `on_ground` is
set, which makes the Y value the base. A default cube is 20.48 on each side. See
[examples/temple.json](examples/temple.json) (13 objects). The whole list is validated before
any input is sent. If a step fails, the result says where, and `start_at` resumes.

## How the files work

Worked out by static analysis of an installed Bryce 3D (built November 1997) with
`objdump`. No Bryce code or data is in this repository, the program was not modified, and
licensing data was not examined.

**Container ("CCmF Composite File": `.br3`, `.obp`, `.mat`, `.bsk`, `Data/*.dat`).** Taken from
`CCmFile` in `Axiom.dll`. Container integers are big-endian. `0x24C` and `0x250` give the
directory's size and first block. A block is `allocated, used, next` (u32) and a payload. A
directory entry is 12 bytes: flags, size, offset; `0x260`, `0x264` and `0x268` hold its
capacity, used count and growth step. In `.obp` and `.br3` files, entry 16 lists class names
and entry 17 describes that list; each class row names a data entry and a descriptor entry;
descriptors give record size (offset 0) and count (offset 12); the u32 at offset 4 of a
record is the entry holding that object's data. Container integers are big-endian, but the
contents of records are little-endian. The older `.mat` and `.bsk` preset records use a
different layout that is not supported.

**Scenes (`.br3`).** Each object is one record with its name at 8, a material link at 280 and
links to separate *track* records that hold its settings. Position, orientation (a
quaternion), scale and origin are separate tracks (links at 492-508), and an object is drawn
with its center at:

```
(Origin * Scale) rotated by Orientation, then + Position
```

File units are Bryce units divided by 10.24. A new record gets its own copies of every
track, a fresh id, a row in its class table (the table is created if needed) and, for scene
objects, an item in the root object list.

- **Camera:** stored position is `(-x, y, -z)` and stored yaw is heading minus 180 degrees.
  When loading, Bryce draws from a cached matrix, position and field of view inside the
  camera record, not from the tracks, so both are written. The field of view spans the
  picture's diagonal.
- **Sun:** the direction is stored relative to the camera as (right, up, forward), in a track
  and in a cached copy in the sun record.
- **Terrain:** a scene keeps heights twice: in the terrain record, and as big-endian u16 in
  a second record (`HF_eHFMem`, from offset 208). Both are written.

**Opening a file.** When Bryce is launched while already running, the new copy finds the
window of class `CWinAppl`, sends `WM_COPYDATA` holding the quoted file path, and exits.
`br3_open_in_bryce` sends that same message. The key handler reads Ctrl, Alt and Shift with
`GetKeyState`, and menu commands are four-letter codes dispatched inside the program.

**Terrain (`C3dHeightField`).** Name at 8; u32 at 272 is 8 when heights use the top 11 bits;
grid size N (u16 little-endian) at 736; `_elr` at 744 and the length at 748 when compressed;
heights from 900: N x N little-endian u16, or big-endian (run, height) pairs followed by four
zero bytes. All 32 terrains in the bundled `Mountains.obp` and `Tutorial.obp` decode to
recognizable shapes and re-encode **byte for byte**. To repeat that on your copy, set
`BRYCE3D_INSTALL` to the Bryce folder and run `pytest`.

**Menus (`Data/menus.dat`).** 40-byte records: label (0-32), shortcut character (33), Shift
(34), modifier bits (35: `0x10` has shortcut, `0x20` Ctrl, `0x40` Alt), command code
(36-39). Save As, Merge, Import Object, Export Image and Render to Disk have no shortcut.
The executable has no standard Windows menu resources.

**Memory.** The executable has no relocations, so it loads at `0x400000`. Reading or writing
the running program's memory is not implemented.

## Interface notes

Coordinates are in `src/bryce3d_mcp/layout.py`, measured on a 1920x1080 display. Done by
hand on a running copy: naming a control by hovering (bottom-left text), creating a cube,
exact values in Object Attributes, applying a Simple&Fast preset, rendering with the large
sphere, opening the object library, Ctrl+D, Ctrl+A, Ctrl+Z, Delete, and selecting in the
viewport with a held click. Ctrl+S on an unnamed scene opens a standard Windows Save As box.
Sky Lab, view, zoom and next-object shortcuts are not in the menu table and come from a
[Bryce 7 list](https://tutorialtactic.com/blog/bryce-7-shortcuts/).

## Safety

- Input is sent only while a Bryce window is in the foreground. Clicks are also refused if
  another window covers the target point.
- Move the mouse during a running sequence and it stops.
- Nothing is typed into a dialog or clicked in a preset library unless that pop-up is
  detected on screen. Failed object creation stops the build, so the previous object is not
  edited by mistake.
- File tools never change the source file or overwrite an existing one.
- Named destructive commands, delete keys, new, open and save need `confirm=true`. Alt+Tab,
  Alt+F4, the Windows key and similar are blocked.
- Screenshots cover only the Bryce interface area, not the whole desktop. Anything
  overlapping that area would be included.
- Raw `click` and `drag` cannot know what a button does. Keep your client's tool approval on
  for work you care about, and save a copy of important scenes first.

`confirm` and `overwrite` are guard rails against accidents. They are set by the agent, so
they are not a security boundary.

## First run checklist

File tools (safe: nothing is sent to your mouse or keyboard):

1. `br3_status`: the template file should be found.
2. `br3_new`, then open the written file in Bryce and render. Compare it with the preview.
3. `br3_open_in_bryce` with another file while Bryce is open. If nothing happens, open files
   by hand and please report it.

Interface tools, only if you want them, in a new empty scene:

1. `bryce_status`, then `screenshot`: the whole interface should be in frame. If it is
   offset, use `set_ui_origin` or `BRYCE3D_UI_ORIGIN`.
2. `hover` x=385, y=53: Bryce should show "Create Cube" bottom-left.
3. `create_object` with `kind="cube"`, `position=[25.5, 30, 0]`, `color="red"`.
4. `render`.

If a step reports that a dialog did not open although it is visible, adjust the probe ranges
in `src/bryce3d_mcp/layout.py`.

## Limitations

- Not everything Bryce can do is editable through files yet: procedural and picture textures,
  cloud and haze controls, sky modes, animation keyframes, booleans and grouping, paths and
  imported mesh geometry. Set those up in Bryce, put them in your template or scene, or use
  the interface tools. File edits keep what they do not change.
- A `mesh` added from the template is a copy of the template's mesh. New mesh shapes need
  `generate_mesh` and Bryce's own Import Object.
- Terrain operations are this project's own code, not the Terrain Editor's.
- `br3_open_in_bryce` needs a path made of Windows ANSI characters, under 260 bytes.
- Only Bryce 3D is supported. Bryce 2, 4, 5 and later use other layouts.
- Interface tools: Bryce must not be covered, you cannot use the mouse during a sequence,
  and the layout was measured at 1920x1080. Windows display scaling is untested. If Bryce
  runs as administrator, the server must too.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests need no Windows or Bryce: they build small synthetic scene files and simulate the
screen. Two extra tests use your own copy if you set `BRYCE3D_INSTALL` (the Bryce folder)
or `BRYCE3D_SAMPLE_SCENE` (a `.br3` saved by Bryce).
