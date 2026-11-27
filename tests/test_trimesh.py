"""The mesh record: encoding rules, shape checks and adding meshes to a scene."""

import math
import os
import struct

import pytest

from bryce3d_mcp import preview, sceneops, trimesh
from bryce3d_mcp.br3 import Br3
from bryce3d_mcp.errors import BryceError
from scene_files import basic_scene, mesh_scene

TETRA = [[0, 0, 0], [10, 0, 0], [0, 0, 10], [0, 10, 0]]
OUTWARD = [[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]]
CUBE = [[x, y, z] for z in (0, 4) for y in (0, 4) for x in (0, 4)]
SIDES = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2],
         [1, 3, 7, 5]]


def volume(points, triangles):
    total = 0.0
    for t in triangles:
        a, b, c = (points[i] for i in t)
        total += sum(a[i] * (b[(i + 1) % 3] * c[(i + 2) % 3]
                             - b[(i + 2) % 3] * c[(i + 1) % 3]) for i in range(3))
    return total / 6


def empty_record():
    payload = bytearray(trimesh.DATA)
    struct.pack_into("<5i", payload, trimesh.HEADER, 2, 0, 0, 0, 0)
    return bytes(payload)


def test_inside_out_solids_are_turned_the_right_way():
    for faces in (OUTWARD, [list(reversed(f)) for f in OUTWARD]):
        points, triangles = trimesh.prepare(TETRA, faces)
        assert volume(points, triangles) == pytest.approx(1000 / 6)


def test_concave_faces_keep_their_area_and_orientation():
    outline = [[0, 0, 0], [4, 0, 0], [4, 0, 4], [2, 0, 1], [0, 0, 4]]
    for face in ([0, 1, 2, 3, 4], [4, 3, 2, 1, 0]):
        points, triangles = trimesh.prepare(outline, [face])
        normals = [trimesh._cross(*(points[i] for i in t))[1] for t in triangles]
        assert len({n > 0 for n in normals}) == 1
        assert sum(abs(n) for n in normals) / 2 == pytest.approx(10)


def test_record_layout_and_stored_rules():
    points, triangles = trimesh.prepare(TETRA, OUTWARD)
    payload = trimesh.encode(empty_record(), points, triangles, crease=180)
    assert struct.unpack_from("<5i", payload, 640) == (2, 4, 4, 4, 0)
    assert len(payload) == 860 + 24 * 8 + 76 * 4
    assert struct.unpack_from("<6d", payload, 660) == pytest.approx(
        (-0.1, -0.1, -0.1, 10.1, 10.1, 10.1))
    got_points, normals, stored = trimesh.decode(payload)
    assert got_points == points
    for number, t in enumerate(stored):
        face = struct.unpack_from("<3d", payload, 860 + 24 * 8 + 76 * number + 36)
        cross = trimesh._cross(*(points[i] for i in t[:3]))
        length = math.dist(cross, (0, 0, 0))
        assert face == pytest.approx([c / length for c in cross])
        middle = [sum(points[i][a] for i in t[:3]) / 3 - 2.5 for a in range(3)]
        assert sum(f * m for f, m in zip(face, middle)) < 0      # points inward
        assert struct.unpack_from("<4i", payload,
                                  860 + 24 * 8 + 76 * number + 60) == (2, 2, 2, 1)
    # a smooth normal is the plain average of the face normals around the point
    corner = stored[0][3]
    around = [struct.unpack_from("<3d", payload, 860 + 24 * 8 + 76 * n + 36)
              for n, t in enumerate(stored) if corner in t[3:]]
    assert normals[corner] == pytest.approx(
        [sum(f[a] for f in around) / len(around) for a in range(3)])


@pytest.mark.parametrize("crease, normals", [(40, 24), (100, 8)])
def test_sharp_edges_stay_hard(crease, normals):
    points, triangles = trimesh.prepare(CUBE, SIDES)
    payload = trimesh.encode(empty_record(), points, triangles, crease)
    assert struct.unpack_from("<3i", payload, 644) == (8, normals, 12)


def test_decoding_refuses_nonsense():
    with pytest.raises(BryceError, match="too short"):
        trimesh.decode(b"x" * 10)
    payload = bytearray(empty_record())
    struct.pack_into("<i", payload, 644, 9999)
    with pytest.raises(BryceError, match="do not fit"):
        trimesh.decode(bytes(payload))


@pytest.mark.parametrize("vertices, faces", [
    ([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1, 2]]),
    (TETRA, [[0, 1, 9]]),
    ([[0, 0, 0]], [])])
def test_unusable_meshes_are_refused(vertices, faces):
    with pytest.raises(BryceError):
        trimesh.prepare(vertices, faces)


def test_obj_text():
    vertices, faces = trimesh.parse_obj(
        "# x\no a\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 1\nf 1/1/1 2//1 -1\n")
    assert len(vertices) == 3 and faces == [[0, 1, 2]]
    with pytest.raises(BryceError, match="line 1"):
        trimesh.parse_obj("v 1 two 3")
    with pytest.raises(BryceError, match="no points or no faces"):
        trimesh.parse_obj("v 1 2 3")


def add(scene, **step):
    result = sceneops.apply(scene, [{"op": "mesh", **step}],
                            Br3.from_bytes(mesh_scene()), None)[0]
    again = Br3.from_bytes(scene.data)
    for ref in again._all_refs():
        again._links(ref)
    return again, result


def test_a_new_mesh_stays_where_its_points_are():
    scene, info = add(Br3.from_bytes(basic_scene()), shape="custom", name="Wedge",
                      vertices=TETRA, faces=OUTWARD)
    assert info["position"] == [5, 5, 5] and info["size"] == pytest.approx([10, 10, 10])
    points, triangles = scene.mesh_geometry(scene.find("Wedge")[1])
    assert max(abs(c) for p in points for c in p) == pytest.approx(1)
    assert volume(points, triangles) > 0
    world = preview.object_parts(scene, *scene.find("Wedge"))[0][0]
    assert world.min(0) == pytest.approx([0, 0, 0], abs=1e-6)
    assert world.max(0) == pytest.approx([10, 10, 10], abs=1e-6)


def test_mesh_placement_size_and_later_resizing():
    scene, info = add(Br3.from_bytes(basic_scene()), shape="extrude",
                      outline=[[0, 0], [30, 0], [30, 10], [0, 10]], height=5,
                      position=[0, 20, 0], size=[60, 5, 10],
                      material={"color": "#ff0000"})
    assert info["position"] == [0, 20, 0] and info["size"] == pytest.approx([60, 5, 10])
    assert info["material"]["color"] == "#ff0000"
    scene.set_object(info["name"], size=[12, 12, 12])
    world = preview.object_parts(scene, *scene.find(info["name"]))[0][0]
    assert world.max(0) - world.min(0) == pytest.approx([12, 12, 12])


def test_the_donor_mesh_is_not_changed_and_two_can_be_added():
    scene = Br3.from_bytes(mesh_scene())
    before = scene.mesh_geometry(scene.find("Stone 1")[1])
    scene, first = add(scene, shape="lathe", profile=[[0, 0], [5, 0], [0, 9]],
                       segments=6)
    scene, second = add(scene, shape="custom", vertices=TETRA, faces=OUTWARD)
    assert first["name"] != second["name"]
    assert scene.mesh_geometry(scene.find("Stone 1")[1]) == before
    assert len(scene.mesh_geometry(scene.find(second["name"])[1])[1]) == 4


def test_obj_files_and_booleans(tmp_path):
    path = tmp_path / "t.obj"
    path.write_text("".join(f"v {x} {y} {z}\n" for x, y, z in TETRA)
                    + "".join("f " + " ".join(str(i + 1) for i in f) + "\n"
                              for f in OUTWARD))
    scene, info = add(Br3.from_bytes(basic_scene()), shape="obj", obj_path=str(path),
                      boolean="negative")
    assert info["triangles"] == 4 and info["boolean"] == "negative"


def test_a_donor_is_required_and_shapes_need_their_fields():
    scene = Br3.from_bytes(basic_scene())
    with pytest.raises(BryceError, match="no mesh to copy"):
        sceneops.apply(scene, [{"op": "mesh", "shape": "custom", "vertices": TETRA,
                                "faces": OUTWARD}], None, None)
    with pytest.raises(BryceError, match="shape needs"):
        sceneops.apply(scene, [{"op": "mesh", "shape": "extrude"}], None, None)


@pytest.mark.skipif(not os.environ.get("BRYCE3D_SAMPLE_SCENE"),
                    reason="set BRYCE3D_SAMPLE_SCENE to a scene with a stone")
def test_bryce_own_stone_is_reproduced():
    scene = Br3.load(os.environ["BRYCE3D_SAMPLE_SCENE"])
    ref = next(key for kind, key in scene._objects() if kind == "mesh")
    original = scene._payload(ref)
    points, normals, stored = trimesh.decode(original)
    again = trimesh.encode(original, points, [(t[0], t[2], t[1]) for t in stored], 180)
    assert len(again) == len(original)
    assert again[:trimesh.DATA] == original[:trimesh.DATA]
    got_points, got_normals, got = trimesh.decode(again)
    assert got_points == points and [t[:3] for t in got] == [t[:3] for t in stored]
    for mine, theirs in zip(got, stored):
        for corner in (3, 4, 5):
            assert got_normals[mine[corner]] == pytest.approx(
                normals[theirs[corner]], abs=1e-12)
