from scene_files import face_scene

from bryce3d_mcp.br3 import Br3


def test_2d_face_is_named():
    scene = Br3.from_bytes(face_scene())
    assert [(o["name"], o["kind"]) for o in scene.objects()] == [("2D Face 1", "2d_face")]


def test_2d_face_can_be_duplicated_moved_and_deleted():
    scene = Br3.from_bytes(face_scene())
    scene.duplicate("2D Face 1", "Copy", position=[10, 5, 0])
    scene = Br3.from_bytes(scene.data)
    copy = next(o for o in scene.objects() if o["name"] == "Copy")
    assert copy["kind"] == "2d_face" and copy["position"] == [10, 5, 0]
    assert [tag for tag, _, _ in scene._root()[2]].count("sqre3dob") == 2
    scene.delete("2D Face 1")
    assert [o["name"] for o in Br3.from_bytes(scene.data).objects()] == ["Copy"]
