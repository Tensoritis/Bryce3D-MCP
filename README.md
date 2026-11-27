# Bryce3D-MCP

MCP Server to allow AI Agents to interact with the Bryce3D modeling, rendering and animation software.

Built against the original MetaCreations **Bryce 3D** (1997) on Windows. Bryce 3D has no
scripting API, so the file formats and parts of the program were reverse engineered. The
server works four ways:

1. **Scene files (preferred).** The agent builds and edits `.br3` scenes directly: objects,
   new 3D shapes, groups and booleans, materials, textures, Bryce's own preset models,
   materials and skies, terrain heights, camera, sun, sky and moon, keyframes on Bryce's
   own timeline, plus animations as a series of scenes. Each edit returns a preview picture.
2. **Window messages.** To render, run menu commands, load, save, answer Windows file
   dialogs and take pictures, the server talks to Bryce's window directly. Bryce can stay
   behind your other windows.
3. **The bridge (optional).** A small DLL from this repository, loaded into the running
   Bryce, gives it a virtual mouse and keyboard that only Bryce sees, reports unsaved
   work and shows when Bryce is busy. This reaches inside the Terrain Editor, Materials
   Lab, Sky Lab and Motion Lab while your own mouse stays yours.
4. **Real mouse and keyboard.** Off unless you set `BRYCE3D_REAL_INPUT=1`.

Ways 1 to 3 never move your mouse or type on your keyboard.

## The agent loop

```
br3_new / br3_edit  ->  look at the preview  ->  br3_edit again  ->  ...
        |
        +-> br3_render            the real Bryce picture, for the agent to review
        +-> br3_export_obj        geometry for other software
        +-> br3_animate           keyframes and paths, then br3_render_animation
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
| `document` | **Picture size and render settings**, saved in the scene: `size` (any width and height from 64 to 4096, or one of Bryce's six sizes), `antialiasing` (off, normal, fine art), spatial `optimisation` (off, low, high), `render_type` (normal, mask, distance, altitude) and `report_render_time` (turn it off so a report box does not appear after rendering) |
| `picture` | **Your own picture** (PNG, JPEG...) on an object, stored inside the scene, up to 1024 pixels a side. Choose the `channels` (color and ambient color by default), how often it tiles (`repeat`) and the wrapping `mapping`. The first picture is built from one in Bryce's `Tutorial.obp` preset file, so `BRYCE3D_INSTALL` must be found |
| `sky_preset` | The sky and sun of one of Bryce's 42 sky presets |
| `light` (a field of `add`, `set`, `duplicate`) | A light's `color`, `intensity` (0 to 100, as in Bryce's Edit Light box; new lights have 25), `falloff` (`linear`, `squared`, `none`), `edge_softness` for spot, square spot and parallel lights, and true/false `casts_shadows`, `infinite` and `visible` |
| `terrain` | Reshape a terrain from its own heights or any picture, using the terrain operations below |
| `mesh` | A **new 3D shape** written straight into the scene: `extrude` an outline, `lathe` a profile, `custom` points and faces, or an `obj` file. Edges sharper than `smooth_angle` (40) stay crisp |
| `texture_scale` | Multiply a procedural texture's three scale numbers. They count how often the pattern repeats, so 2 makes it half as big |
| `texture_edit` | How a procedural texture wraps (`mapping`: object_space, world_space, parametric, parametric_scaled, world_top, spherical, cylindrical, reflection_map, random, object_top, object_front), its `scale`, `offset` and `rotation`, and its colors: `replace_colors` swaps one color for another, `color_sets` sets any of its four sets of three |
| `keyframes` | **Animate an object on Bryce's own timeline**: keys with a `frame` or `time` and any of `position`, `rotation` and `scale` (see [Animation](#animation)) |
| `time_marker` | Move Bryce's time marker, so the scene opens, previews and renders at that moment |
| `camera` | Position, heading, pitch or `look_at`, `bank` (tilt: positive drops the right end of the horizon), field of view |
| `sun` | Azimuth (relative to the camera: 0 ahead, 90 right, 180 behind), altitude, colors |
| `sky` | `settings`: single sky values by name, including the moon phase (`MoonPhAng`, `MoonPhTA`, `MoonPhES`, `MoonPhHard`). `switches`: the sky mode (`soft`, `darker`, `custom`, `off`), `volumetric` (`off`, `rough`, `fine`) and true/false for stratus and cumulus clouds, cloud shadows, linked or fixed clouds, sun linked to view, haze and fog blended with the sun, rainbow and secondary rainbow, halo rings, illusion, moon image and phase, stars and comets |

`br3_presets` lists every preset by name, read from your own Bryce folder (found from the
running program, or set `BRYCE3D_INSTALL`).

Units are Bryce units and Y is up. `position` is the object's center. A cube at scale 1 is
20.48 on each side; a terrain at scale 1 is 20.48 wide and 10.24 tall, so one placed at half
its height stands on the ground. Giving `size` avoids the difference. Material colors are `#rrggbb`. Material numbers (`diffuse`, `ambient`,
`specular`, `transparency`, `reflection`, `bump`, `metallic`, `density`) use 1.0 for 100%;
`refraction` is the index (1.0 bends nothing, 1.33 is like water). Giving a plain value to a
textured channel removes the texture from that channel only.

### One-time setup: the template file

New objects are copied from a scene saved by **your own** Bryce, so none of Bryce's data is
shipped here. In Bryce, create one of each object you want from the Create palette, then
save the scene as `templates.br3` in the output folder (or point `BRYCE3D_TEMPLATES` at it).
`br3_status` reports which kinds it can supply. **The `mesh` operation needs one mesh in
it, so include a stone.**

## Animation

There are two ways. **Bryce's own timeline** puts keys inside one scene, which you can play,
change and render in Bryce:

```json
[
  {"op": "keyframes", "name": "Ball", "duration": 4,
   "keys": [{"frame": 0, "position": [-40, 10, 0]},
            {"frame": 30, "position": [0, 40, 0]},
            {"time": 4, "position": [40, 10, 0]}]},
  {"op": "time_marker", "frame": 30}
]
```

- A `keyframes` operation replaces the keys of the channels it names (`position`,
  `rotation`, `scale`). A channel given only one key stops being animated.
- The length always reaches the last key; `duration` can make it longer. Frames run at the
  scene's own rate (15 a second in Bryce's new scenes).
- `br3_info` lists each animated object's keys. To look at any moment, use `time_marker`,
  then preview or render. On a key this is exact. **Between two keys the server places the
  object on a straight line**, while Bryce draws its own curve, and the result says so.
- `set` with a new `position` moves the **whole motion**. An animated rotation or scale is
  changed with `keyframes`. Animated objects cannot be grouped or ungrouped. `duplicate`
  keeps the motion; an object newly added from a template starts still.
- Put rotation keys less than 180 degrees apart: each turn takes the short way.
- Only objects can be keyed. Camera, sun, sky, material and light keys, motion paths and
  the easing of Bryce's curves are not covered.

**One scene per frame** works for everything, including the camera and sky. `br3_animate` takes keyframes and paths and writes **one ordinary scene per frame**, with a
preview GIF. `br3_render_animation` renders the frames in Bryce a few at a time (it can be
resumed) and joins them into `render.gif`.

```json
[
  {"target": "camera", "look_at_object": "Vase",
   "path": {"points": [[0, 70, -160], [160, 55, 0], [0, 90, 160], [-160, 55, 0]],
            "closed": true},
   "keys": [{"frame": 0, "bank": -9}, {"frame": 12, "bank": 9}, {"frame": 23, "bank": -9}]},
  {"target": "object", "name": "Star",
   "keys": [{"frame": 0, "rotation": [-70, 0, 0]}, {"frame": 23, "rotation": [-70, 345, 0]}]}
]
```

Tracks can drive the camera (position, look_at, heading, pitch, bank, fov), any object
(position, rotation, scale, size, material, a light's numbers and color), the sun and sky
settings. A camera or object can follow a smooth path at a steady speed, optionally facing
the way it travels. Easing is `linear`, `ease` or `smooth`. Up to 600 frames; each is a
full scene file, so large scenes use a lot of disk space.

## Saving and loading

| Tool | What it does |
| --- | --- |
| `load_file` | Open a scene, merge one into the current scene, or import a model into the running Bryce. For unsaved work choose `stop` (default), `save` (to `save_current_to`, then open) or `discard` |
| `save_scene` | Save, or Save As to a `.br3` path. With a path, success is reported only once a complete scene file has been written. Existing files need `overwrite` |
| `answer_save_box` | Press Don't Save, Cancel or Save on Bryce's own "Do you want to save your changes?" box (needs the bridge) |

That box is drawn by Bryce, so Windows tools cannot see it. The server recognizes it in a
picture of Bryce's window (checked against the two pictures of it from a real run). The
`br3_` file tools read and write scene files without Bryce running at all.

## The bridge

`bryce_connect_bridge` (with your agreement) loads `bryce3d_bridge.dll` into the running
Bryce. Source: [bridge/bridge.c](bridge/bridge.c). To check it on your PC first, without
any input or scene changes:

```powershell
.venv\Scripts\python -m bryce3d_mcp --check-bridge
```

It writes `bridge-report.json` and `bridge-capture.png` to the output folder.

- It intercepts the Windows calls Bryce and `Axiom.dll` use to read the mouse and keyboard,
  and two Axiom functions (is Bryce in front, is input waiting). While virtual input is off
  they all pass straight through. It is switched off after each tool call, when the
  connection ends, and after 30 idle seconds.
- It talks over a local named pipe for that one process and refuses remote clients.
- Apart from the temporary hooks in memory, it changes one thing in Bryce only when asked:
  the "unsaved changes" flag, when you agree to discard work. No file on disk is modified.
  Closing Bryce removes it.
- It supports **only this build** of Bryce 3D and refuses anything else (it checks the
  files' hashes and the code it is about to patch).
- Loading a DLL into another program is something antivirus software may block or flag.
- The built DLL is included. To rebuild it you need LLVM (`clang`, `lld-link`,
  `llvm-dlltool`): `python bridge/build.py`. The build is reproducible byte for byte.

## Examples

[examples/gallery.py](examples/gallery.py) holds five scenes written only with the
operations above, using textured materials, eroded terrains, preset skies and new meshes
that the script builds itself:

- **dream_sea** - mirrored checkerboard water, a glass stairway, a fluted column with a
  carved jar, a curved slide, jagged pink peaks and a ribbon of 6,000 rainbow flecks
- **silver_drops** - chrome spheres over rippled water under a tilted horizon
- **orrery** - a gyroscope of thin chrome rings beside a crystal citadel on eroded crags
- **far_world** - rough textured ground, a spined rock arch, craggy boulders and asteroids,
  and a pink planet
- **colour_generation** - tilted glass cubes, one carved by a boolean

They are rendered in the order that tests plain shapes first and new meshes last. The
larger scenes are 10 to 15 MB each.

On Windows, start Bryce and double-click `render-gallery.bat`. Your template needs a stone.
It asks whether to load the bridge, never discards unsaved work unless you type `DISCARD`,
stops at the first problem and writes the pictures and a `render_log.json` to
`workspace\gallery`. To redo one scene, run `render-gallery.bat 04_far_world`.

All five rendered on Windows with version 0.5.1, with no dialogs, in 8 seconds to 10
minutes each. Those renders showed that new meshes came out at double size and terrains at
half the stated height, plus a missing boolean part. All three are fixed in 0.6.0, which
**has not been rendered yet.** Colors still differ from what the scenes aim for.

## Status - read this first

Version 0.11.0. The Python code has automated tests, run on Linux. The bridge DLL is tested
in a harness that loads your real `Bryce 3D.exe` and `Axiom.dll` on Linux with stand-ins for
Windows. **Parts of it have not run on Windows yet; see the table.**

| What | State |
| --- | --- |
| Opening and rendering scenes edited by this code | **Confirmed in Bryce** (earlier version) |
| Adding sphere, cube, torus, cylinder, cone and pyramid; duplicate; move, scale, delete; rotation about X and Y; material color; sun direction | **Confirmed in Bryce** (a 13-object demo matched within about 2 pixels) |
| A generated terrain | **Confirmed in Bryce** to appear. Heights survived Bryce re-saving the file exactly |
| Opening a scene, rendering from the menu by message, and pictures of Bryce's window | **Confirmed in Bryce** with the bridge loaded |
| New mesh shapes | **Rendered in Bryce.** **Fixed in 0.6.0:** they were double size. With that one change the preview matches the renders shape for shape |
| Terrain height | **Fixed in 0.6.0:** terrains are half as tall as earlier versions said. Bryce's mountain presets sit at half their height, and a render agrees |
| Sky and sun settings | **Fixed in 0.5.0:** earlier versions did not write the copy Bryce draws from. All 38 cached positions agree with the settings in four Bryce-saved scenes. Preset skies and the sun direction rendered; single values not checked in a render |
| Sky mode and switches | New in 0.6.0. Each bit was read from the code that fills Bryce's menu and Sky & Fog window, and every preset sky with its atmosphere on has exactly one mode. **Not rendered** |
| Light settings | New in 0.7.0. Each was read from the code behind Bryce's Edit Light window, and lights saved by Bryce read back as new lights do in Bryce (white, intensity 25). Where Bryce saved separate color and intensity records for a light (seen once, on a scene's sun), they match the values read from the light. Light gels and the light radius and shadow ambience values that animations can drive are not covered. **Not rendered** |
| Picture textures | New in 0.9.0. The picture format was read from Bryce's graphics library: its tutorial pictures and interface art decode with correct colors and readable text, and pictures written here decode back exactly. A picture stored in a scene Bryce itself saved (64 x 64), decoded and written again, matches **byte for byte**, size record included. **Not rendered**, so how `repeat` looks on a surface is unconfirmed |
| Terrain Editor tools | New in 0.10.0. Read instruction by instruction and checked here for behavior on Bryce's mountain presets. **Not compared with Bryce's editor** on the same terrain, and the drag direction of two-way buttons is a choice made here |
| Document and render settings | New in 0.11.0. Matched field by field to the code with which Bryce loads them and to its Render Options menu, and the settings of ten saved scenes read correctly. **Changed settings have not been opened in Bryce.** The 360 panorama render type is not covered, and capturing the rendered picture is only measured for the default size |
| Camera including bank | Reading matches a real render within 1.7 pixels. Writing and bank **rendered**: a negative bank raises the right end of the horizon |
| Textures from the `texture` operation | **Rendered** (water ripples, rock color) after the 0.5.1 fix: each texture link needs its type marker |
| Texture scale and wrapping | New in 0.8.0. Read from Bryce's mapping code: the coordinate is multiplied by the scale, so bigger numbers repeat more often. The 11 wrapping modes follow the order in Bryce's help file, which agrees with what the code computes for each. All of Bryce's own textures read as world space with no offset or rotation. Colors are stored blue first (checked with the wood and desert textures). **No edited texture has been rendered.** Which of the four color sets a given texture draws with depends on how it is built |
| Preset materials and skies, rotation about Z | **Rendered** |
| Booleans | In the first render the carved cube was missing. 0.6.0 writes the extra flags Bryce's own boolean parts have. **Not rendered since** |
| Preset models, plain groups, lights, disk, lattice | Layouts read from Bryce's own files; all 154 bundled presets load with every link intact. **Not opened in Bryce yet** |
| Position keyframes | New in 0.12.0. Starting from a scene saved before animating, adding the same keys rebuilds the five two-key records Bryce saved **byte for byte**, and all of its document settings. **A scene keyed here has not been played in Bryce** |
| Rotation and scale keyframes | New in 0.12.0. They follow the same record layout, but no scene in which Bryce saved two of these keys has been seen. **Untested in Bryce** |
| Frame-by-frame animation | Frames and previews tested here. Rendering a series is tested against a stand-in only |
| Saving and loading | Save-box detection works on real pictures. The Save As and load steps are **not tried live** |
| The bridge | **Loaded on Windows.** Opening scenes, starting renders from the menu, noticing when each finished and capturing the picture worked for all five gallery scenes |
| Labs (`open_lab`, then click and drag) | Depends on the two rows above. **Not tried live**; the Sky Lab key is unverified |
| Terrain library (`.obp`) reading and encoding | All 32 bundled terrains re-encode byte for byte. Importing an edited one is untried |
| Real mouse and keyboard tools | Button positions measured on a running copy. The input code is untried |

### How good is the preview?

`br3_preview` draws shapes, flat colors, the ground, a stand-in sky, the sun and ground
shadows in about a second, and says for each object whether it is in frame, off an edge,
behind the camera or hidden. Against Bryce's render of a test scene, object outlines lined
up within about 2 pixels, and new meshes and terrains line up with the gallery renders. It does **not** show textures, ripples, Bryce's skies and clouds,
atmosphere, reflections, transparency, boolean cuts, object-on-object shadows or light
from light objects.
It draws new meshes and camera tilt. Use it for layout, and `br3_render` for the look: a
textured, reflective scene looks flat and plain in the preview.

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
| `BRYCE3D_REAL_INPUT` | off | `1` lets the interface tools use your real mouse and keyboard when the bridge is not connected |

## Tools (53)

| Group | Tools | Input needed |
| --- | --- | --- |
| Scene files | `br3_status`, `br3_operations`, `br3_presets`, `br3_new`, `br3_edit`, `br3_info`, `br3_preview`, `br3_animate`, `br3_terrain_picture`, `br3_export_obj`, `br3_render_result` | None |
| Run Bryce | `br3_render`, `br3_render_animation`, `br3_open_in_bryce`, `bryce_command`, `open_lab` (Sky Lab needs input), `bryce_file_command`, `list_menus`, `bryce_dialogs`, `press_dialog_button`, `submit_file_dialog` | None (window messages) |
| Files | `load_file`, `save_scene`, `answer_save_box` (needs input) | None |
| Look | `bryce_status`, `screenshot`, `save_render_image`, `render_status`, `render` (`mode="button"` needs input) | None |
| Bridge | `bryce_connect_bridge`, `bryce_disconnect_bridge` | None |
| Terrain libraries | `terrain_list`, `terrain_preview`, `terrain_operations`, `terrain_edit`, `terrain_export` | None |
| Geometry files | `generate_mesh` (OBJ or DXF), `generate_heightmap` | None |
| Reference | `set_ui_origin`, `list_catalog`, `list_commands` | None |
| Build by interface | `build_scene`, `create_object`, `set_attributes`, `apply_material`, `browse_object_library`, `insert_library_object`, `run_command` | Bridge or real input |
| Reach anything else | `hover`, `click`, `drag`, `press_keys`, `type_text` | Bridge or real input |

### Rendering

Bryce does the real rendering. `br3_render` opens a scene in the running Bryce, chooses
Clear and Render from its menu, waits until the picture stops changing and returns it at
on-screen size. All of it is window messages and a picture of Bryce's own window. Bryce
must not be minimized. It refuses to replace unsaved work (known only with the bridge)
unless `discard_changes` is set, and never reports a blank picture as finished. A Windows question box is reported with its buttons; a box that
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

**Bryce's own Terrain Editor tools**, rewritten from the program's code (the filter weights,
rounding, pass order and border handling are Bryce's):

| Operation | Editor button |
| --- | --- |
| `bryce_erode` | Erode: 1500 raindrops a press, each cutting the channel it runs down |
| `bryce_smooth`, `bryce_sharpen` | Smoothing, Sharpening |
| `bryce_raise_lower` | Raise/Lower |
| `bryce_gaussian_edges` | Gaussian Edges |
| `bryce_height_noise`, `bryce_slope_noise` | Height Noise, Slope Noise |
| `bryce_equalize`, `bryce_subplateaus` | Equalize / Subplateaus |
| `bryce_spikes` | the Spikes half of Spikes / Blob Maker |
| `bryce_sawtooth` | the Sawtooth half of Sawtooth / Cross Ridge2 |
| `bryce_relief_noise` | the Relief Noise half of Mounds / Relief Noise |

Where you would drag a button, `strength` 1 is the finished effect. `low` and `high` are the
editor's filtering range. `seed` makes random patterns repeatable; Bryce's own differ from
run to run. Which half of a two-name button is which was read from its click code.
**Not rewritten:** Fractal, Eroded, Mounds, Dampen / Raise Edges, Posterize, Subcontours,
the brush tools (Square Edges, Round Edges, Basic Noise, painting with the mouse) and the
Filtering tab's curve. The operations above them cover similar ground with this project's
own methods.

The grid size is that of the terrain you start from. Bryce's own libraries include 128, 256
and 512 grids.

## Interface tools

With the bridge connected, these use its virtual mouse and keyboard. The interface tools can create all 30 objects on the Create palette (planes, terrain,
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
- **Sky and sun:** each value is kept in a setting record and cached in the sky or sun
  record, which is what Bryce draws from, so both are written. 35 sky offsets (the four moon
  phase values at 680-704) and 3 sun offsets agree with Bryce-saved scenes. The Sky Lab's
  on/off switches are bits of the word at 268 in the sky record (stars and comets are two
  whole numbers at 868 and 872).
- **Sizes:** primitives span -1 to 1 before scaling. A mesh's stored points count double,
  and a terrain's heights run from -0.5 to 0.5 (a symmetric lattice's from -1 to 1). One
  file unit is 10.24 Bryce units.
- **Texture placement:** a procedural texture record keeps the wrapping mode at 268, an
  offset at 300, a rotation quaternion at 324 and four sets of three blue-green-red colors
  at 572, 668, 940 and 1212.
- **Document settings:** the 416-byte companion of the `brycedocset` record, **big-endian**:
  width and height twice, the picture frame at 16, render flags at 56 (`0x2000` normal
  antialiasing, `0x400` mask, `0x800` distance, `0x1000` altitude), the frame rate (float) at
  64, antialiasing at 80, optimisation at 84 and the render report switch at 88.
- **Pictures:** a picture texture (`pctx`) links to a stored picture at 504. Its scale is
  `(repeat - 1) / 17`, or `1 - repeat` when bit `0x2` of the word at 272 is set. The
  picture is `rle2`: four run-length coded planes (alpha, red, green minus red, blue minus
  red), with an 80-byte size record. See `src/bryce3d_mcp/picture.py`.
- **Textures:** in a material, each textured channel block is preceded by a four-byte type
  (`brtx` procedural, `pctx` picture), then the texture's id; bit `0x40` of the word at 268
  is set. A procedural texture record keeps three scale numbers (doubles at 276).
- **Terrain:** a scene keeps heights twice: in the terrain record, and as big-endian u16 in
  a second record (`HF_eHFMem`, from offset 208). Both are written.

**Keyframes.** Every setting record is a 148-byte header, whose third u32 is the key count,
then keys of equal size: the time in seconds (double), a u32 that is 2, and the value at
+44. A one-key record is an ordinary setting. An object also keeps the pose at the time
marker in its own record, which is what Bryce draws. In the document settings (big-endian
floats, seconds) the duration is at 60, frames per second at 64, the time marker at 68 and
the working range at 72 and 76. See `src/bryce3d_mcp/keyframes.py`.

**Meshes (`C3dTriMeshType2`).** After the 640-byte object header: `2, points, normals,
triangles, 0`; at 660 a bounding box padded by 1% per side; from 860 the points and then
the normals as doubles; then 76 bytes per triangle (three point numbers, three normal
numbers, 12 unused bytes, the face normal, and `2, 2, 2, 1`). Face normals are
`(b - a) x (c - a)` and point inward; a point's normal is the plain average of the face
normals around it. The layout is described in `src/bryce3d_mcp/trimesh.py`.

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

**Memory.** The executable has no relocations, so it loads at `0x400000`. A pointer to the
open document is at `0x635018`; bit 0 of the word at offset `0x4d8` in it means unsaved
changes. Only the bridge reads it.

## Interface notes

Coordinates are in `src/bryce3d_mcp/layout.py`, measured on a 1920x1080 display. Done by
hand on a running copy: naming a control by hovering (bottom-left text), creating a cube,
exact values in Object Attributes, applying a Simple&Fast preset, rendering with the large
sphere, opening the object library, Ctrl+D, Ctrl+A, Ctrl+Z, Delete, and selecting in the
viewport with a held click. Ctrl+S on an unnamed scene opens a standard Windows Save As box.
Sky Lab, view, zoom and next-object shortcuts are not in the menu table and come from a
[Bryce 7 list](https://tutorialtactic.com/blog/bryce-7-shortcuts/).

## Safety

- Real input needs `BRYCE3D_REAL_INPUT=1`. It is sent only while a Bryce window is in the foreground. Clicks are also refused if
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

Nothing here uses your mouse or keyboard. Keep a saved copy of any scene you care about.

1. Run `setup.bat` again after updating.
2. Save your work in Bryce, then run `.venv\Scripts\python -m bryce3d_mcp --check-bridge`
   and look at the two files it writes.
3. Double-click `render-gallery.bat` (your template needs a stone).
4. If anything fails, send `workspace\gallery\render_log.json`, the pictures beside it and
   `bridge-report.json`.

## Limitations

Not everything in Bryce 3D is covered by the file tools:

- On Bryce's own timeline only object position, rotation and scale can be keyed. Motion
  path objects, camera and sky keys and the shape of Bryce's curves are not covered.
- A procedural texture's noise types, filters and the way its parts are combined cannot
  be edited.
- Inside the Labs, the agent works by looking and clicking; there are no per-control tools.
- New meshes carry no texture coordinates; up to 50,000 triangles each.
- Only some Terrain Editor tools are rewritten (see Terrain operations); the other terrain
  operations are this project's own.
- `br3_open_in_bryce` needs a path made of Windows ANSI characters, under 260 bytes.
- Only Bryce 3D is supported, and the bridge only the build it was made for.
- Without the bridge, unsaved work cannot be detected in advance.
- Interface coordinates were measured at 1920x1080. Windows display scaling is untested.
- If Bryce runs as administrator, the server must too.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests need no Windows or Bryce: they build small synthetic scene files and simulate the
screen. The bridge harness tests run on Linux with `clang` when `BRYCE3D_INSTALL` is set.
A few extra tests use your own copy if you set `BRYCE3D_INSTALL` (the Bryce folder)
or `BRYCE3D_SAMPLE_SCENE` (a `.br3` saved by Bryce).
