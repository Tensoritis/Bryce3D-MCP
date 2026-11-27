# Bryce3D-MCP

MCP Server to allow AI Agents to interact with the Bryce3D modeling, rendering and animation software.

Built against the original MetaCreations **Bryce 3D** (1997) on Windows. Bryce 3D has no
scripting API, so the file formats and parts of the program were reverse engineered. The
server works three ways:

1. **Scene files (preferred).** The agent builds and edits `.br3` scenes directly: objects,
   groups and booleans, materials, Bryce's own preset models, materials and skies, terrain
   heights, camera and sun. Each edit returns a preview picture.
2. **Window messages.** To render, run menu commands, answer Windows file dialogs and take
   pictures, the server talks to Bryce's window directly. Bryce can stay behind your other
   windows.
3. **Interface (last resort).** Mouse and keyboard input for whatever is left. This takes
   over your mouse and keyboard while it runs.

Ways 1 and 2 never move your mouse or type.

## The agent loop

```
br3_new / br3_edit  ->  look at the preview  ->  br3_edit again  ->  ...
        |
        +-> br3_render            the real Bryce picture, for the agent to review
        +-> br3_export_obj        geometry for other software
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
| `duplicate`, `delete` | Copy or remove an object or group |
| `group`, `ungroup` | Group objects. `negative` and `intersect` make booleans |
| `insert_preset` | One of Bryce's ready-made models: 30 mountains, 30 boolean models, tutorial scenes |
| `material_preset` | One of Bryce's 48 bundled materials (Mirror, Metallic Chrome, checkerboards, woods, metals), then adjust it |
| `texture` | Drive a material channel with a named Bryce texture. `BasicSin24` on `bump` makes ripples |
| `sky_preset` | The sky and sun of one of Bryce's 42 sky presets |
| `terrain` | Reshape a terrain from its own heights or any picture, using the terrain operations below |
| `camera` | Position, heading, pitch or `look_at`, field of view |
| `sun` | Azimuth (relative to the camera: 0 ahead, 90 right, 180 behind), altitude, colors |
| `sky` | Single sky settings by name. Which ones show depends on the sky mode |

`br3_presets` lists every preset by name, read from your own Bryce folder (found from the
running program, or set `BRYCE3D_INSTALL`).

Units are Bryce units and Y is up. `position` is the object's center. A cube at scale 1 is
20.48 on each side. Material colors are `#rrggbb`. Material numbers (`diffuse`, `ambient`,
`specular`, `transparency`, `reflection`, `bump`, `metallic`, `density`) use 1.0 for 100%;
`refraction` is the index (1.0 bends nothing, 1.33 is like water). Giving a plain value to a
textured channel removes the texture from that channel only.

### One-time setup: the template file

New objects are copied from a scene saved by **your own** Bryce, so none of Bryce's data is
shipped here. In Bryce, create one of each object you want from the Create palette, then
save the scene as `templates.br3` in the output folder (or point `BRYCE3D_TEMPLATES` at it).
`br3_status` reports which kinds it can supply.

## Examples

[examples/gallery.py](examples/gallery.py) holds five scenes written only with the
operations above: a mirrored checkerboard sea with a stairway and a rainbow ribbon (308
objects), silver orbs over still water, a gyroscope beside a glass citadel, an alien plain
with a spired arch under a giant planet, and floating glass cubes, one carved by a boolean.

On Windows, start Bryce, save your work and double-click `render-gallery.bat`. It builds the
scenes from your template and presets, renders each one and writes the pictures and a
`render_log.json` to `workspace\gallery`. **These scenes have not been rendered in Bryce
yet**, so expect to tune colors, ripple strength and sun angles after the first run.

## Status - read this first

Version 0.4.0. The Python code has automated tests, run on Linux. On Windows, only
installing and the first connection check have been run. Files made by earlier versions
were opened and rendered by hand in a real copy of Bryce 3D.

| What | State |
| --- | --- |
| Opening and rendering scenes edited by this code | **Confirmed in Bryce** |
| Adding sphere, cube, torus, cylinder, cone and pyramid; duplicate; move, scale, delete; rotation about X and Y; material color; sun direction | **Confirmed in Bryce** (the 13-object demo matched within about 2 pixels) |
| A generated terrain | **Confirmed in Bryce** to appear. Heights survived Bryce re-saving the file exactly |
| Camera | Rewritten after that render showed the earlier version was wrong. Reading matches the render within 1.7 pixels and Bryce's six built-in views. **Writing it has not been rendered** |
| Groups, booleans, preset models, preset materials, textures, preset skies, rotation about Z | Layouts read from Bryce's own files. All 154 bundled presets load into a scene with every link intact. **Not opened in Bryce yet** |
| Adding disks, planes, lattices, meshes and lights | Automated tests only |
| Window messages: menu commands, rendering, file dialogs, pictures of Bryce's window (`br3_render`, `bryce_command`, `screenshot`) | Protocol read from Bryce's own code, tested against stand-ins. **Not tried live** |
| `br3_open_in_bryce` | Same: **not tried live** |
| Terrain library (`.obp`) reading and encoding | All 32 bundled terrains re-encode byte for byte. Importing an edited one is untried |
| Interface tools (mouse and keyboard) | Button positions measured on a running copy. The server's input code is untried |

### How good is the preview?

`br3_preview` draws shapes, flat colors, the ground, a stand-in sky, the sun and ground
shadows in about a second, and says for each object whether it is in frame, off an edge,
behind the camera or hidden. Against Bryce's render of a test scene, object outlines lined
up within about 2 pixels. It does **not** show textures, ripples, Bryce's skies and clouds,
atmosphere, reflections, transparency, boolean cuts, object-on-object shadows or lights.
Use it for layout, and `br3_render` for the look.

## Install (Windows)

Needs Python 3.10 or newer on the same PC as Bryce 3D. Start Bryce, then double-click
`setup.bat`. It installs the server into `.venv`, checks that it can find and picture
Bryce, sets
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
| `BRYCE3D_INSTALL` | found from the running Bryce | The Bryce folder, for its presets |
| `BRYCE3D_EXE` | `bryce 3d.exe` | Executable name used to find Bryce |
| `BRYCE3D_UI_ORIGIN` | auto | `x,y` screen pixel of the top-left of the 640x480 interface |

## Tools (46)

| Group | Tools | Uses your mouse or keyboard? |
| --- | --- | --- |
| Scene files | `br3_status`, `br3_operations`, `br3_presets`, `br3_new`, `br3_edit`, `br3_info`, `br3_preview`, `br3_terrain_picture`, `br3_export_obj`, `br3_render_result` | No |
| Run Bryce | `br3_render`, `br3_open_in_bryce`, `bryce_command`, `bryce_file_command`, `list_menus`, `bryce_dialogs`, `press_dialog_button`, `submit_file_dialog`, `save_scene`, `open_scene` | No (window messages) |
| Look | `bryce_status`, `screenshot`, `save_render_image`, `render_status`, `render` (unless `mode="button"`) | No |
| Terrain libraries | `terrain_list`, `terrain_preview`, `terrain_operations`, `terrain_edit`, `terrain_export` | No |
| Geometry files | `generate_mesh` (extrude, lathe, custom to OBJ or DXF), `generate_heightmap` | No |
| Reference | `set_ui_origin`, `list_catalog`, `list_commands` | No |
| Build by interface | `build_scene`, `create_object`, `set_attributes`, `apply_material`, `browse_object_library`, `insert_library_object`, `run_command` | Yes |
| Reach anything else | `hover`, `click`, `drag`, `press_keys`, `type_text` | Yes |

### Rendering

Bryce does the real rendering. `br3_render` opens a scene in the running Bryce, chooses
Clear and Render from its menu, waits until the picture stops changing and returns it at
on-screen size. All of it is window messages and a picture of Bryce's own window. Bryce
must not be minimized. A Windows question box is reported with its buttons; a box that
Bryce draws itself, such as the Render Report, shows only in the picture.

`br3_render_result` reads the `.bmp` that Bryce writes beside a scene (same name), straight
from disk. In testing the bitmap sometimes appeared only after saving in Bryce. For other
formats, `bryce_file_command` runs Export Image or Render to Disk and fills in the file
name; any options box Bryce then shows still needs handling.

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
stone, lattice, solids, 2D objects, lights) with exact values and material presets.
Anything else can be reached through `screenshot`, `hover`, `click`, `drag` and keys.

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
contents of records are little-endian.

**Ids.** Every record has a 32-bit id that is unique only within its class. A group and its
member list share one, as do a sky preset and its sun, and preset libraries reuse old ids
across classes. Links are therefore resolved by the class expected at that position, and
object list entries by their tag.

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

- **Camera:** each view has its own record; the Director's View (`director`) and Camera
  View (`perspective`) are both written. Cameras share the objects' coordinates. Bryce
  draws from the record's cache: an offset (356) turned by a matrix (380) plus a
  translation (452), angles at 644 as pitch (positive is down), heading and bank, and the
  field of view at 724. The field of view spans the picture's diagonal; against a real
  render the focal length is 1.037 times the plain pinhole value.
- **Sun:** the direction is stored relative to the camera as (right, up, forward), in a track
  and in a cached copy in the sun record.
- **Object cache:** objects keep the same matrix and translation cache. Objects from
  Bryce's preset libraries often have no setting records at all, only the cache.
- **Groups and booleans:** a group is an object plus a member list with the same id.
  Members are positioned relative to the group. Bits `0x08`, `0x10` and `0x20` of the word
  at 272 mean positive, negative and intersect.
- **Materials:** the 976-byte record has a block per channel: a texture link, a "textured"
  flag (`0x10000000`) 20 bytes in and the plain value 24 bytes in. Color blocks are 56
  bytes from 276, number blocks 40 bytes from 556.
- **Sky and sun:** values are cached in the records in link order. 31 sky and 3 sun offsets
  were matched exactly against a scene Bryce saved. Moon phase is not mapped.
- **Terrain:** a scene keeps heights twice: in the terrain record, and as big-endian u16 in
  a second record (`HF_eHFMem`, from offset 208). Both are written.

**Opening a file.** When Bryce is launched while already running, the new copy finds the
window of class `CWinAppl`, sends `WM_COPYDATA` holding the quoted file path, and exits.
`br3_open_in_bryce` sends that same message.

**Running a menu command.** Bryce notes which submenu is highlighted from `WM_MENUSELECT`,
and runs item number *n* of it on `WM_COMMAND` with a zero `lParam`
(`CWinApplWndProc` in `Axiom.dll`, handlers at `0x4f66e0` and `0x4f6630` in the program).
So the server sends `WM_MENUSELECT` with the submenu handle and posts `WM_COMMAND`, to the
window that owns the menu. Menu commands are four-letter codes dispatched inside the
program. File dialogs are the standard Windows ones and are filled with `WM_SETTEXT`.

**Terrain (`C3dHeightField`).** Name at 8; u32 at 272 is 8 when heights use the top 11 bits;
grid size N (u16 little-endian) at 736; `_elr` at 744 and the length at 748 when compressed;
heights from 900: N x N little-endian u16, or big-endian (run, height) pairs followed by four
zero bytes. All 32 terrains in the bundled `Mountains.obp` and `Tutorial.obp` decode to
recognizable shapes and re-encode **byte for byte**. To repeat that on your copy, set
`BRYCE3D_INSTALL` to the Bryce folder and run `pytest`.

**Menus (`Data/menus.dat`).** 40-byte records: label (0-32), shortcut character (33), Shift
(34), modifier bits (35: `0x10` has shortcut, `0x20` Ctrl, `0x40` Alt), command code
(36-39). Save As, Merge, Import Object, Export Image and Render to Disk have no shortcut.
The executable has no menu resources; it builds its Windows menu
from this table as it runs (50 items, which matches what a live connection counted).

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
- Commands that can discard work or write files, delete keys, and answering a Bryce question
  need `confirm=true`. Alt+Tab,
  Alt+F4, the Windows key and similar are blocked.
- Pictures are drawn by Bryce's own windows (`PrintWindow`), never copied from the screen,
  so other programs cannot appear in them. (An earlier version captured the screen and
  could save a picture of whatever was in front.)
- Raw `click` and `drag` cannot know what a button does. Keep your client's tool approval on
  for work you care about, and save a copy of important scenes first.

`confirm` and `overwrite` are guard rails against accidents. They are set by the agent, so
they are not a security boundary.

## First run checklist

Safe, because nothing is sent to your mouse or keyboard:

1. `br3_status`: the template file should be found.
2. Save your work in Bryce, then double-click `render-gallery.bat`, or ask the agent to
   `br3_render` one scene.
3. If a step fails, open the scene in Bryce by hand and render it, and please report what
   happened (`workspace\gallery\render_log.json`).

## Limitations

Not everything in Bryce 3D is covered yet:

- No file editing of animation and keyframes, paths, camera bank, moon phase, sky modes and
  cloud switches (use a sky preset), texture scale and mapping, texture or picture editing
  (only reusing existing textures), or new mesh geometry inside a scene.
- The Terrain Editor, Materials Lab and Sky Lab windows can be opened by command, but have
  no input-free control inside them.
- A `mesh` added from the template is a copy of the template's mesh. New mesh shapes need
  `generate_mesh` and `bryce_file_command` with `import_object`.
- Terrain operations are this project's own code, not the Terrain Editor's.
- `br3_open_in_bryce` needs a path made of Windows ANSI characters, under 260 bytes.
- Only Bryce 3D is supported. Bryce 2, 4, 5 and later use other layouts.
- Interface tools: Bryce must not be covered, you cannot use the mouse during a sequence,
  and the layout was measured at 1920x1080. Windows display scaling is untested.
- If Bryce runs as administrator, the server must too.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests need no Windows or Bryce: they build small synthetic scene files and simulate the
screen. Three extra tests use your own copy if you set `BRYCE3D_INSTALL` (the Bryce folder)
or `BRYCE3D_SAMPLE_SCENE` (a `.br3` saved by Bryce).
