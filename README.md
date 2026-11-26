# Bryce3D-MCP

MCP Server to allow AI Agents to interact with the Bryce3D modeling, rendering and animation software.

Built against the original MetaCreations **Bryce 3D** on Windows. Bryce 3D has no scripting
API, so this server drives the real interface: it sends mouse and keyboard input and returns
screenshots so the agent can see what happened.

## Status - read this first

Version 0.1.0 is **not yet tested end to end on Windows**.

| What | State |
| --- | --- |
| Button positions, primitive names, dialogs | Measured on a running copy of Bryce 3D |
| Create, exact placement, material preset, render, duplicate, select all, delete, undo | Performed by hand-driven input on that copy |
| This server's logic, safety checks, mesh and heightmap output, MCP protocol | Automated tests on Linux with a simulated Bryce |
| The Windows input and capture layer (`win32.py`) | Guards tested against a stub. **Never run on real Windows** |
| Dialog detection brightness thresholds | Estimated from screenshots. If wrong, the server refuses to type |
| Tools marked UNVERIFIED | Not observed working on Bryce 3D |

See [docs/UI_MAP.md](docs/UI_MAP.md) for the evidence and the
[first run checklist](#first-run-checklist) before trusting it with real work.

## Install (Windows)

Needs Python 3.10 or newer on the same PC as Bryce 3D.

```powershell
git clone https://github.com/Tensoritis/Bryce3D-MCP.git
cd Bryce3D-MCP
python -m venv .venv
.venv\Scripts\pip install -e .
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

Start Bryce 3D, then ask the agent to call `bryce_status` and `screenshot`.

### Settings (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `BRYCE3D_EXE` | `bryce 3d.exe` | Executable name used to find Bryce |
| `BRYCE3D_UI_ORIGIN` | auto | `x,y` screen pixel of the top-left of the 640x480 interface |
| `BRYCE3D_OUTPUT_DIR` | `Documents\Bryce3D-MCP` | Where meshes and heightmaps are written |

## Tools (25)

| Group | Tools |
| --- | --- |
| Look | `bryce_status`, `screenshot`, `set_ui_origin`, `list_catalog`, `list_commands` |
| Build | `build_scene`, `create_object`, `set_attributes`, `apply_material`, `browse_object_library`, `insert_library_object`, `run_command`, `render` |
| Reach anything else | `hover`, `click`, `drag`, `press_keys`, `type_text`, `list_menus`, `invoke_menu` |
| Files | `open_scene`, `save_scene_as`, `submit_file_dialog` |
| Geometry | `generate_mesh` (extrude, lathe, custom to OBJ or DXF), `generate_heightmap` |

### How far "build anything" goes

- **Direct:** all 30 objects on the Create palette (planes, terrain, stone, lattice, solids,
  2D objects, lights) with exact position, rotation and size, plus material presets.
- **Custom shapes:** `generate_mesh` writes OBJ or DXF, which Bryce 3D can import.
  `generate_heightmap` writes a picture for the Terrain Editor. Running the import inside
  Bryce is not automated yet: use `list_menus` and `invoke_menu` if the window has a native
  menu, otherwise `hover` and `click`, then `submit_file_dialog`.
- **Everything else** (Terrain Editor, Sky Lab, Materials Lab sliders, camera, animation
  timeline, booleans) is reachable through screenshots, `hover`, `click`, `drag` and keys.
  It has no dedicated tool, so it depends on the agent reading the screen correctly.

### Scene description

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

## Safety

- Input is sent only while a Bryce window is in the foreground. Clicks are also refused if
  another window covers the target point.
- Move the mouse during a running sequence and it stops.
- Nothing is typed into a dialog or clicked in a preset library unless that pop-up is
  detected on screen. Failed object creation stops the build, so the previous object is not
  edited by mistake.
- Named destructive commands, delete keys, new, open and save need `confirm=true`. Alt+Tab,
  Alt+F4, the Windows key and similar are blocked.
- Screenshots cover only the Bryce interface area, not the whole desktop. Anything
  overlapping that area would be included.
- Raw `click` and `drag` cannot know what a button does. Keep your client's tool approval on
  for work you care about, and save a copy of important scenes first.

`confirm` and `overwrite` are guard rails against accidents. They are set by the agent, so
they are not a security boundary.

## First run checklist

Use a new, empty scene.

1. `bryce_status`: check `ui_origin`.
2. `screenshot`: the whole interface should be in frame. If it is offset, use
   `set_ui_origin` or `BRYCE3D_UI_ORIGIN`.
3. `hover` x=385, y=53: Bryce should show "Create Cube" bottom-left.
4. `create_object` with `kind="cube"`, `position=[25.5, 30, 0]`, `color="red"`.
5. `render`.

If a step reports that a dialog did not open although it is visible, adjust the probe ranges
in `src/bryce3d_mcp/layout.py`.

## Limitations

- The interface must not be covered, and you cannot use the mouse while a sequence runs.
- Layout was measured at 1920x1080. The interface is a fixed 640x480, but other resolutions
  and Windows display scaling are untested.
- If Windows will not let the server focus Bryce, click Bryce and retry.
- If Bryce runs as administrator, the server must too.
- `render` treats an unchanged picture as finished. A very slow render that pauses longer
  than a few seconds between updates could be reported early.
- Object count and names are not read back: the agent reads them from screenshots.
- Other Bryce versions have different layouts and are not supported.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests need no Windows or Bryce: `tests/fake.py` simulates the screen states.
