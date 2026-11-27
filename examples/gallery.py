"""Five example scenes made only with the same operations br3_edit takes.

    python examples/gallery.py build     writes the scenes and previews
    python examples/gallery.py render    renders them in the running Bryce

Needs your own templates.br3 (see br3_status) and your Bryce folder, found from
the running program or BRYCE3D_INSTALL. Files go to BRYCE3D_OUTPUT_DIR/gallery.
"""

from __future__ import annotations

import colorsys
import json
import math
import os
import random
import sys

from bryce3d_mcp import preview, sceneops, server
from bryce3d_mcp.br3 import Br3


def add(kind, name, position, size, material=None, rotation=None):
    step = {"op": "add", "kind": kind, "name": name, "position": [round(v, 2) for v in position],
            "size": [round(v, 2) for v in size]}
    if material:
        step["material"] = material
    if rotation:
        step["rotation"] = rotation
    return step


def copy(of, name, position, **extra):
    return {"op": "duplicate", "name": of, "new_name": name,
            "position": [round(v, 2) for v in position], **extra}


def wear(name, preset, **material):
    step = {"op": "material_preset", "name": name, "preset": preset}
    if material:
        step["material"] = material
    return step


def paint(name, preset, **material):
    """A flat preset used as a clean base, then adjusted."""
    return wear(name, preset, **material)


def ripples(name, strength):
    return {"op": "texture", "name": name, "texture": "BasicSin24", "channels": ["bump"],
            "strength": strength}


def mountain(preset, name, x, z, width, height, depth=None):
    """A model from Bryce's mountain presets, sized to stand on the ground."""
    return [{"op": "insert_preset", "preset": preset, "name": name},
            {"op": "set", "name": name, "position": [x, height / 2, z],
             "size": [width, height, depth or width]}]


def sea(name, colour, reflection=0.55, bump=0.12):
    return [add("plane", name, [0, 0, 0], [20.48, 20.48, 20.48]),
            wear(name, "Mirror", color=colour, diffuse=0.45, ambient=0.2, reflection=reflection),
            ripples(name, bump)]


def chrome(name, position, diameter, tint="#dfe8ff"):
    return [add("sphere", name, position, [diameter] * 3),
            wear(name, "Mirror", color=tint, diffuse=0.12, reflection=0.92, specular=1.0)]


def sky(preset, azimuth, altitude):
    return [{"op": "sky_preset", "preset": preset},
            {"op": "sun", "azimuth": azimuth, "altitude": altitude}]


def camera(position, look_at, fov=60, bank=0):
    return {"op": "camera", "position": position, "look_at": look_at, "fov": fov,
            "bank": bank}


def jagged_arch(centre, radius, thickness, seed=11, along=72, around=18):
    """A craggy stone arch bristling with spikes, made as one new mesh. It runs a
    little below the ground at both feet."""
    rng = random.Random(seed)
    start, end = math.radians(-14), math.radians(194)
    vertices, faces = [], []
    for i in range(along + 1):
        angle = start + (end - start) * i / along
        out = (math.cos(angle), math.sin(angle), 0.0)
        swell = 1 + 0.35 * abs(math.cos(angle)) ** 2          # heavier toward the feet
        for j in range(around):
            turn = math.tau * j / around
            rough = rng.uniform(0.74, 1.22)
            across = thickness * swell * rough
            # Spikes grow from the outer side.
            if math.cos(turn) > 0.2 and 3 < i < along - 3 and rng.random() < 0.075:
                across += thickness * rng.uniform(0.9, 2.1)
            lift = math.cos(turn) * across
            vertices.append([centre[0] + out[0] * (radius + lift),
                             centre[1] + out[1] * (radius + lift),
                             centre[2] + math.sin(turn) * thickness * swell * rough])
    for i in range(along):
        for j in range(around):
            a, b = i * around + j, i * around + (j + 1) % around
            c, d = b + around, a + around
            faces += [[a, b, c], [a, c, d]]
    for ring, flip in ((0, True), (along, False)):             # close both ends
        hub = len(vertices)
        points = vertices[ring * around:(ring + 1) * around]
        vertices.append([sum(p[k] for p in points) / around for k in range(3)])
        for j in range(around):
            a, b = ring * around + j, ring * around + (j + 1) % around
            faces.append([hub, b, a] if flip else [hub, a, b])
    return {"op": "mesh", "shape": "custom", "name": "Arch", "vertices": vertices,
            "faces": faces, "smooth_angle": 25}


# 1 -------------------------------------------------------------------------------
def dream_sea():
    ops = [*sky("The Big Sky", 235, 38),
           add("plane", "Checker Sea", [0, 0, 0], [20.48] * 3),
           wear("Checker Sea", "Mirrored Checkerboard", reflection=0.55),
           ripples("Checker Sea", 0.05),
           camera([0, 25, -180], [0, 38, -40])]
    for name, position, diameter in (("Orb Near", (30, 22, -76), 36),
                                     ("Orb High", (2, 62, -38), 34),
                                     ("Orb Far", (40, 78, -8), 15),
                                     ("Orb Farthest", (49, 100, 18), 11)):
        ops += chrome(name, position, diameter)
    # A stairway that leaves the water and keeps climbing.
    ops += [add("cube", "Step 0", [-52, 3, -46], [15, 1.5, 4.5], rotation=[0, -24, 0]),
            wear("Step 0", "White Metal", color="#e8e4ff", reflection=0.35)]
    for i in range(1, 46):
        t = i / 45
        ops.append(copy("Step 0", f"Step {i}", [-52 + 52 * t ** 1.35, 3 + 108 * t,
                                                  -46 + 74 * t]))
    # A leaning column with a carved-looking head.
    stone = {"color": "#9a96b4", "ambient": 0.25, "diffuse": 0.9, "specular": 0.1}
    ops += [add("cylinder", "Column", [4, 9, -88], [6, 26, 6], rotation=[0, 0, -24]),
            paint("Column", "Flat Gray", **stone),
            add("sphere", "Column Head", [10.6, 23.8, -88], [10, 9, 10]),
            paint("Column Head", "Flat Gray", **stone),
            add("torus", "Handle", [5.5, 28.5, -88], [6, 6, 6]),
            paint("Handle", "Flat Gray", **stone)]
    # A playground slide standing in the sea.
    for n, (dx, dz) in enumerate(((-4, -4), (4, -4), (-4, 4), (4, 4))):
        ops.append(add("cylinder", f"Leg {n}", [-20 + dx, 15, -52 + dz], [0.9, 30, 0.9]))
        ops.append(paint(f"Leg {n}", "Flat Gray", color="#b8b4d0"))
    ops += [add("cube", "Deck", [-20, 30, -52], [10, 1.4, 10]),
            paint("Deck", "Flat Gray", color="#b8b4d0"),
            add("pyramid", "Slide Roof", [-20, 41.5, -52], [13, 6, 13]),
            paint("Slide Roof", "Flat Gray", color="#8f8ab0")]
    for n, (dx, dz) in enumerate(((-4, -4), (4, -4), (-4, 4), (4, 4))):
        ops.append(add("cylinder", f"Post {n}", [-20 + dx, 34.5, -52 + dz], [0.8, 8, 0.8]))
        ops.append(paint(f"Post {n}", "Flat Gray", color="#b8b4d0"))
    ops += [add("cube", "Chute", [-8.5, 22.5, -52], [20, 1.1, 6], rotation=[0, 0, -38]),
            wear("Chute", "White Metal", color="#c9c6e0", reflection=0.3)]
    # A ribbon of rainbow confetti: thousands in spirit, a few hundred in fact.
    rng = random.Random(7)
    path = [(-68, 82, -72), (-34, 34, -66), (-14, 26, -50), (16, 22, -18), (46, 25, 14)]
    first = None
    for i in range(240):
        t = (i / 240) * (len(path) - 1)
        k = min(int(t), len(path) - 2)
        f = t - k
        base = [a + (b - a) * f for a, b in zip(path[k], path[k + 1])]
        spread = 6.5 * (1 - i / 300)
        position = [v + rng.gauss(0, spread * w) for v, w in zip(base, (0.55, 0.75, 0.55))]
        band = (position[1] - base[1]) / (spread * 2.4) + 0.5
        red, green, blue = colorsys.hsv_to_rgb(0.82 * min(1, max(0, band)), 0.85, 1.0)
        colour = "#%02x%02x%02x" % (int(red * 255), int(green * 255), int(blue * 255))
        edge = rng.uniform(0.45, 1.1)
        name = f"Spark {i}"
        turn = [rng.uniform(0, 90), rng.uniform(0, 90), 0]
        material = {"color": colour, "ambient": 0.75, "diffuse": 0.6}
        if first is None:
            first = name
            ops += [add("cube", name, position, [edge] * 3, rotation=turn),
                    paint(name, "Flat White", **material)]
        else:
            ops.append(copy(first, name, position, size=[round(edge, 2)] * 3,
                            rotation=turn, material=material))
    # Pink peaks behind the camera, there for the mirrors to find.
    for n, x in enumerate((-190, 20, 210)):
        ops += [{"op": "insert_preset", "preset": "Iceberg Terrain", "name": f"Pink Peaks {n}",
                 "position": [x, 20, -430], "scale": 0.55},
                paint(f"Pink Peaks {n}", "Flat White", color="#c88ac8", ambient=0.35)]
    return ops


# 2 -------------------------------------------------------------------------------
def silver_drops():
    ops = [*sky("Arctic Horizon", 300, 32), *sea("Still Water", "#4f8fe8", 0.62, 0.09),
           camera([0, 46, -170], [0, 34, -50], 64, bank=-9)]
    for name, position, diameter in (("Drop Giant", (-34, 67, -78), 36),
                                     ("Drop", (5, 14, -62), 27),
                                     ("Drop High", (34, 66, -48), 15)):
        ops += chrome(name, position, diameter, "#cfe4ff")
    for n, (position, diameter) in enumerate((((-60, 7, 90), 8), ((72, 5, 150), 7),
                                              ((24, 4, 240), 6))):
        ops += chrome(f"Distant Drop {n}", position, diameter, "#cfe4ff")
    ops += [add("sphere", "Halo Lens", [-34, 67, -78], [38.5, 38.5, 38.5]),
            wear("Halo Lens", "Mirror", color="#bfe8ff", diffuse=0.05, reflection=0.2,
                 transparency=0.86, refraction=1.04)]
    return ops


# 3 -------------------------------------------------------------------------------
def orrery():
    ops = [*sky("Wisp o'the Will", 245, 28), *sea("Ocean", "#1d4f93", 0.45, 0.2),
           camera([0, 30, -186], [0, 40, -40])]
    centre = (26, 48, -52)
    for n, (turn, across) in enumerate((([78, 0, -14], 56), ([90, 0, 32], 53),
                                        ([8, 62, 8], 52), ([6, -58, -6], 50))):
        name = f"Ring {n}"
        ops += [add("torus", name, centre, [across, across, across * 0.34], rotation=turn),
                wear(name, "Metallic Chrome", color="#b8c4d8", diffuse=0.2, reflection=0.75)]
    for n in range(8):
        angle = math.tau * n / 8
        ops += chrome(f"Bead {n}", (centre[0] + 13 * math.cos(angle),
                                    centre[1] + 4.5 * math.sin(angle * 2),
                                    centre[2] + 13 * math.sin(angle)), 5.6, "#c8d4e8")
    ops += chrome("Axle Left", (centre[0] - 27, centre[1], centre[2]), 5)
    ops += chrome("Axle Right", (centre[0] + 27, centre[1], centre[2]), 5)
    # The glass citadel on its island.
    for n, (x, z, width, height) in enumerate(((-114, 150, 118, 76), (-62, 172, 74, 40),
                                               (-176, 162, 90, 52))):
        ops += [*mountain("Apex", f"Crag {n}", x, z, width, height),
                wear(f"Crag {n}", "White Metal", color="#8aa4c8", reflection=0.18,
                     ambient=0.2)]
    crown = (-114, 78, 148)
    ops += chrome("Crown Pearl", crown, 30, "#e8f4ff")
    for n in range(4):
        name = f"Crown Arch {n}"
        ops += [add("torus", name, crown, [62, 62, 13], rotation=[0, 45 * n, 0]),
                wear(name, "White Metal", color="#dcefff", reflection=0.55, transparency=0.2)]
    for n in range(10):
        angle = math.tau * n / 10
        spot = (crown[0] + 31 * math.cos(angle), crown[1] + 14 + 7 * math.sin(2 * angle),
                crown[2] + 31 * math.sin(angle))
        for ring, turn in enumerate(([0, 0, 0], [0, 90, 0])):
            name = f"Charm {n}-{ring}"
            ops += [add("torus", name, spot, [11, 11, 3], rotation=turn),
                    wear(name, "White Metal", color="#e4f2ff", reflection=0.6)]
    return ops


# 4 -------------------------------------------------------------------------------
def far_world():
    rock = {"color": "#5d5348", "ambient": 0.16, "diffuse": 0.95, "specular": 0.05}

    def rocky(name, **over):
        return [paint(name, "Flat Gray", **{**rock, **over}),
                {"op": "texture", "name": name, "texture": "Sand1", "channels": ["bump"],
                 "strength": 0.6}]

    ops = [*sky("Caribbean Clear", 285, 24),
           add("plane", "Ground", [0, 0, 0], [20.48] * 3), *rocky("Ground", color="#6f6755"),
           camera([0, 34, -190], [0, 40, -40], 62),
           add("sphere", "Giant Planet", [-330, 250, 620], [520, 520, 520]),
           paint("Giant Planet", "Flat White", color="#e3a6b8", ambient=0.72, diffuse=0.35,
                 transparency=0.12, refraction=1.0)]
    # The arch: one new mesh, craggy and bristling with spikes.
    rng = random.Random(11)
    ops += [jagged_arch((42, 8, 40), 42, 9), *rocky("Arch", color="#3f3a38")]
    for n, (preset, x, z, width, height) in enumerate((
            ("Stalagmites", 80, 52, 62, 34), ("Mt. Baldy", 0, 56, 56, 24),
            ("Mt. Baldy", 104, 70, 70, 20))):
        ops += [*mountain(preset, f"Footing {n}", x, z, width, height),
                *rocky(f"Footing {n}", color="#48423c")]
    for n, (x, z, width, height) in enumerate(((-300, 330, 250, 78), (-130, 440, 190, 52),
                                               (260, 470, 280, 88), (460, 300, 220, 66))):
        ops += [*mountain("Sharpies" if n < 2 else "Mt. Baldy", f"Range {n}", x, z, width,
                          height),
                paint(f"Range {n}", "Flat Gray", color="#5f6f92", ambient=0.3)]
    for n in range(34):
        if n < 4:
            x, z, wide = ((-48, -120, 25), (-14, -96, 13), (60, -128, 30), (8, -140, 11))[n]
        else:
            x, z, wide = rng.uniform(-150, 150), rng.uniform(-100, 190), rng.uniform(3, 13)
        size = [wide, wide * rng.uniform(0.45, 0.7), wide * rng.uniform(0.7, 1.1)]
        turn = [0, rng.uniform(0, 180), rng.uniform(-14, 14)]
        if n == 0:
            ops += [add("sphere", "Boulder 0", [x, size[1] * 0.3, z], size, rotation=turn),
                    *rocky("Boulder 0")]
        else:
            ops.append(copy("Boulder 0", f"Boulder {n}", [x, size[1] * 0.3, z], size=size,
                            rotation=turn))
    for n, (position, wide) in enumerate((((6, 98, -20), 17), ((-36, 62, 10), 14),
                                          ((14, 56, 110), 8))):
        ops.append(copy("Boulder 0", f"Asteroid {n}", position, size=[wide, wide * 0.82, wide],
                        rotation=[25 * n, 40, 15], material={"color": "#8f8a86"}))
    ops += [add("plane", "Low Mist", [0, 2.4, 0], [20.48] * 3),
            paint("Low Mist", "Flat White", color="#a8e0d8", ambient=0.65, diffuse=0.35,
                  transparency=0.68, refraction=1.0)]
    return ops


# 5 -------------------------------------------------------------------------------
def colour_generation():
    ops = [*sky("Cold Storm Coming", 250, 30), *sea("Violet Sea", "#28307a", 0.4, 0.26),
           camera([0, 52, -192], [0, 40, -40], bank=11)]

    def glass(name, colour, see_through=0.55):
        return wear(name, "Mirror", color=colour, diffuse=0.55, ambient=0.18, reflection=0.22,
                    transparency=see_through, transparent_color=colour, refraction=1.12,
                    specular=0.8)

    for name, position, edge, turn, colour in (
            ("Teal", (-37, 36, -62), 23, [8, 20, -10], "#10868c"),
            ("Crimson", (-14, 56, -4), 23, [4, -28, 6], "#c01868"),
            ("Olive", (10, 26, -66), 18, [24, 34, 8], "#98a818")):
        ops += [add("cube", name, position, [edge] * 3, rotation=turn), glass(name, colour)]
    # A glass cube hollowed out by a boolean: the sphere is cut from the cube.
    ops += [add("cube", "Shell", [34, 56, -50], [30] * 3, rotation=[28, 38, 14]),
            glass("Shell", "#8088d0", 0.5),
            add("sphere", "Hollow", [34, 56, -50], [37.5] * 3),
            glass("Hollow", "#8088d0", 0.5),
            {"op": "group", "names": ["Shell", "Hollow"], "negative": ["Hollow"],
             "name": "Carved Cube"}]
    return ops


SCENES = {"01_dream_sea": dream_sea, "02_silver_drops": silver_drops, "03_orrery": orrery,
          "04_far_world": far_world, "05_colour_generation": colour_generation}


def folder() -> str:
    path = os.path.join(server.output_directory(), "gallery")
    os.makedirs(path, exist_ok=True)
    return path


def build(names, templates_path=None, install=None, out=None) -> list[str]:
    library = server._library(templates_path)
    if library is None:
        raise SystemExit(f"No template file at {server._templates_path(templates_path)}. "
                         "See br3_status in the README.")
    install = install or server._install_folder()
    out = out or folder()
    written = []
    for name in names:
        scene = Br3.from_bytes(library.data)
        scene.remove_all_except(set())
        operations = SCENES[name]()
        for start in range(0, len(operations), sceneops.MAX_OPERATIONS):
            sceneops.apply(scene, operations[start:start + sceneops.MAX_OPERATIONS],
                           library, install)
        target = os.path.join(out, name + ".br3")
        with open(target, "wb") as handle:
            handle.write(scene.data)
        picture, _ = preview.render(scene, 640, 480)
        picture.save(os.path.join(out, name + "_preview.png"))
        print(f"built {name}: {len(operations)} operations, "
              f"{len(scene.objects())} objects -> {target}")
        written.append(target)
    return written


def prepare(ask=input) -> bool:
    """Offer the bridge and settle what happens to unsaved work, before anything
    in Bryce is touched. Returns False if the user stops here."""
    print("The bridge is a small DLL from this project, loaded into the running Bryce. It")
    print("detects unsaved work, follows renders and can press Render with a virtual")
    print("mouse. It is gone when you close Bryce.")
    if ask("Load the bridge? [Y/n] ").strip().lower() in ("", "y", "yes"):
        try:
            print(json.dumps(server.connect_bridge()))
        except Exception as error:
            print(f"The bridge could not be loaded: {error}")
            if ask("Continue without it? [y/N] ").strip().lower() not in ("y", "yes"):
                return False
    unsaved = server.get_controller().backend.unsaved_changes()
    if unsaved:
        print("Bryce has UNSAVED CHANGES. Save them in Bryce now, or type DISCARD.")
        if ask("Press Enter after saving, or type DISCARD: ").strip() == "DISCARD":
            server.get_controller().backend.forget_changes()
        elif server.get_controller().backend.unsaved_changes():
            print("There are still unsaved changes, so nothing was opened.")
            return False
    elif unsaved is None:
        print("Without the bridge, save your work in Bryce now.")
        ask("Press Enter when it is saved: ")
    return True


def render(names, timeout=1800) -> bool:
    """Open and render each scene in the running Bryce. Stops at the first problem,
    so that one failed step cannot upset the following ones."""
    results = {}
    log = os.path.join(folder(), "render_log.json")
    for number, name in enumerate(names):
        scene = os.path.join(folder(), name + ".br3")
        if not os.path.isfile(scene):
            build([name])
        print(f"rendering {name} (this can take several minutes)...", flush=True)
        controller = server.get_controller()
        try:
            # After the first scene, what is "unsaved" is the render of the one
            # this script opened itself.
            picture = os.path.join(folder(), name + "_render.png")
            result = controller.render_to_file(
                scene, picture, timeout=timeout,
                discard_changes=number > 0 and controller.backend.unsaved_changes() is True)
            if not result["finished"]:
                controller.render_picture().save(picture)
                result["picture"] = picture
        except Exception as error:
            result = {"finished": False, "error": f"{type(error).__name__}: {error}"}
            try:
                picture = os.path.join(folder(), name + "_problem.png")
                with open(picture, "wb") as handle:
                    handle.write(controller.screenshot_png())
                result["picture"] = picture
            except Exception:
                pass
        results[name] = result
        print(json.dumps({k: v for k, v in result.items() if k != "timeline"}), flush=True)
        with open(log, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1)
        if not result.get("finished"):
            print(f"Stopped at {name}. Send {log} and the pictures in {folder()}.")
            return False
    print(f"Done. Pictures and render_log.json are in {folder()}")
    return True


def main(argv) -> None:
    action = argv[0] if argv else "build"
    names = argv[1:] or list(SCENES)
    unknown = [n for n in names if n not in SCENES]
    if action not in ("build", "render") or unknown:
        raise SystemExit(f"usage: gallery.py build|render [{' '.join(SCENES)}]")
    if action == "build":
        build(names)
    elif prepare():
        render(names)


if __name__ == "__main__":
    main(sys.argv[1:])
