"""Keyframes, paths, and rendering a series of frames."""

import json
import math
import os

import pytest
from PIL import Image

from bryce3d_mcp import animation, server
from bryce3d_mcp.br3 import Br3
from bryce3d_mcp.controller import BryceController
from bryce3d_mcp.errors import BryceError
from fake import FakeBackend
from scene_files import basic_scene

CIRCLE = [[100, 20, 0], [0, 20, 100], [-100, 20, 0], [0, 20, -100]]


def values(plan, op, field):
    return [next(o for o in frame if o["op"] == op)[field] for frame in plan]


@pytest.mark.parametrize("easing", animation.EASINGS)
def test_keys_are_met_exactly_and_held_outside(easing):
    keys = [{"frame": 2, "position": [0, 0, 0]}, {"frame": 6, "position": [10, 20, 30]},
            {"frame": 8, "position": [0, 0, 0]}]
    got = values(animation.plan(11, [{"target": "object", "name": "a", "keys": keys}],
                                easing), "set", "position")
    assert got[0] == got[2] == [0, 0, 0] and got[8] == got[10] == [0, 0, 0]
    assert got[6] == pytest.approx([10, 20, 30])


def test_two_keys_move_evenly_and_a_full_turn_is_a_full_turn():
    track = {"target": "object", "name": "a", "keys": [
        {"frame": 0, "rotation": [0, 0, 0]}, {"frame": 8, "rotation": [0, 360, 0]}]}
    for easing in ("linear", "smooth"):
        got = values(animation.plan(9, [track], easing), "set", "rotation")
        assert [r[1] for r in got] == pytest.approx([45 * i for i in range(9)])


def test_colours_numbers_and_nested_settings():
    tracks = [{"target": "object", "name": "a", "keys": [
        {"frame": 0, "material": {"color": "#000000", "transparency": 0}},
        {"frame": 2, "material": {"color": "#ffffff", "transparency": 1}}]},
        {"target": "sky", "keys": [{"frame": 0, "settings": {"HazeAmount": 0}},
                                   {"frame": 2, "settings": {"HazeAmount": 2}}]},
        {"target": "sun", "keys": [{"frame": 0, "altitude": 10},
                                   {"frame": 2, "altitude": 50}]}]
    middle = animation.plan(3, tracks, "linear")[1]
    assert middle[0]["material"] == {"color": [0.5, 0.5, 0.5], "transparency": 0.5}
    assert middle[1]["settings"] == {"HazeAmount": 1.0}
    assert middle[2]["altitude"] == 30


def test_a_path_is_travelled_at_a_steady_speed():
    # unevenly spaced points: equal times must still cover equal distances
    path = animation.Path([[0, 0, 0], [10, 0, 5], [60, 0, 20], [100, 0, 0]])
    points = [path.at(i / 40)[0] for i in range(41)]
    steps = [math.dist(a, b) for a, b in zip(points, points[1:])]
    assert points[0] == [0, 0, 0] and points[-1] == pytest.approx([100, 0, 0])
    assert max(steps) < 1.05 * sorted(steps)[len(steps) // 2]
    assert min(steps) > 0.85 * sorted(steps)[len(steps) // 2]
    assert animation.Path([[0, 0, 0], [100, 0, 0]]).at(0.5)[1] == pytest.approx(90)


def test_a_closed_path_loops_without_repeating_a_frame():
    plan = animation.plan(8, [{"target": "object", "name": "a",
                               "path": {"points": CIRCLE, "closed": True}}])
    got = values(plan, "set", "position")
    assert got[0] == pytest.approx([100, 20, 0])
    assert math.dist(got[-1], got[0]) == pytest.approx(math.dist(got[1], got[0]), rel=0.05)


@pytest.mark.parametrize("kind", ["camera", "object"])
def test_follow_faces_the_way_of_travel(kind):
    track = {"target": kind, "path": {"points": [[0, 0, 0], [0, 0, 50]], "follow": True}}
    if kind == "object":
        track["name"] = "a"
        turned = values(animation.plan(3, [track]), "set", "rotation")[1]
        assert turned == pytest.approx([0, 0, 0])
    else:
        assert values(animation.plan(3, [track]), "camera", "heading")[1] == 0


def test_the_camera_watches_a_moving_object_whatever_the_track_order():
    tracks = [{"target": "camera", "path": {"points": CIRCLE, "closed": True},
               "look_at_object": "a"},
              {"target": "object", "name": "a", "keys": [
                  {"frame": 0, "position": [0, 0, 0]},
                  {"frame": 3, "position": [30, 0, 0]}]}]
    plan = animation.plan(4, tracks, "linear")
    for frame in plan:
        assert [o["op"] for o in frame] == ["set", "camera"]
        assert frame[1]["look_at"] == frame[0]["position"]


@pytest.mark.parametrize("track, message", [
    ({"target": "moon", "keys": []}, "target"),
    ({"target": "object", "keys": [{"frame": 0}]}, "name"),
    ({"target": "camera"}, "keys or a path"),
    ({"target": "sun", "path": {"points": CIRCLE}}, "only the camera"),
    ({"target": "camera", "keys": [{"frame": 99, "fov": 5}]}, "frame from"),
    ({"target": "camera", "keys": [{"frame": 0, "zoom": 5}]}, "cannot set zoom"),
    ({"target": "camera", "keys": [{"frame": 0, "fov": "wide"}]}, "colour"),
    ({"target": "camera", "keys": [{"frame": 0, "fov": 5}, {"frame": 0, "fov": 6}]},
     "two keys")])
def test_bad_tracks_are_explained(track, message):
    with pytest.raises(BryceError, match=message):
        animation.plan(10, [track])


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("BRYCE3D_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("BRYCE3D_INSTALL", raising=False)
    monkeypatch.setattr(server, "_install_folder", lambda: None)
    backend = FakeBackend()
    monkeypatch.setattr(server, "_controller", BryceController(backend))
    source = tmp_path / "s.br3"
    source.write_bytes(basic_scene())
    return backend, str(source)


def make(source):
    result = server.br3_animate(source, "spin", 6, [
        {"target": "object", "name": "Cube 1", "keys": [
            {"frame": 0, "position": [0, 10, 0]}, {"frame": 5, "position": [50, 10, 0]}]},
        {"target": "camera", "path": {"points": CIRCLE, "closed": True},
         "look_at_object": "Cube 1"}], fps=10, easing="linear")
    return json.loads(result[0])


def test_every_frame_is_a_complete_scene(studio):
    made = make(studio[1])
    frames = sorted(n for n in os.listdir(made["folder"]) if n.endswith(".br3"))
    assert len(frames) == 6
    third = Br3.load(os.path.join(made["folder"], frames[3]))
    assert {o["name"]: o for o in third.objects()}["Cube 1"]["position"] == [30, 10, 0]
    assert third.camera()["position"] != Br3.load(studio[1]).camera()["position"]
    with Image.open(made["preview_gif"]) as gif:
        assert gif.n_frames == 6
    assert make(studio[1])["folder"] != made["folder"]      # nothing is overwritten


def test_frames_are_rendered_in_batches_and_joined(studio):
    backend, source = studio
    folder = make(source)["folder"]
    backend.menu_items = [{"path": "File/Clear and Render", "id": 7, "menu": 1,
                           "enabled": True}]

    def start_render(item):
        backend.render_frames, backend._frame = 3, 0

    backend.on_menu = start_render
    with pytest.raises(Exception, match="confirm"):
        server.br3_render_animation(folder)
    first = json.loads(server.br3_render_animation(folder, confirm=True, count=4,
                                                   timeout_seconds=200))
    assert first == {"rendered_now": [0, 1, 2, 3], "remaining": 2}
    second = json.loads(server.br3_render_animation(folder, confirm=True, count=4,
                                                    timeout_seconds=200))
    assert second["rendered_now"] == [4, 5] and second["remaining"] == 0
    assert os.path.isfile(second["gif"])
    opened = [e[1] for e in backend.log if e[0] == "open_file"]
    assert [os.path.basename(p) for p in opened] == [f"frame_{n:04d}.br3" for n in range(6)]


def test_rendering_stops_at_the_first_problem_and_keeps_unsaved_work(studio):
    backend, source = studio
    folder = make(source)["folder"]
    backend.unsaved = True
    report = json.loads(server.br3_render_animation(folder, confirm=True))
    assert report["rendered_now"] == [] and report["remaining"] == 6
    assert "unsaved" in report["stopped"]["reason"].lower()
    assert not [e for e in backend.log if e[0] in ("open_file", "forget")]


def test_a_folder_that_is_not_an_animation_is_refused(studio, tmp_path):
    with pytest.raises(Exception, match="not a folder made by"):
        server.br3_render_animation(str(tmp_path), confirm=True)


def test_a_light_can_pulse_and_change_colour():
    frames = animation.plan(3, [{"target": "object", "name": "Lamp", "keys": [
        {"frame": 0, "light": {"intensity": 0, "color": "#000000"}},
        {"frame": 2, "light": {"intensity": 100, "color": "#ffffff"}}]}], "linear")
    assert frames[1][0]["light"] == {"intensity": 50.0, "color": [0.5, 0.5, 0.5]}
    overshoot = animation.plan(6, [{"target": "object", "name": "Lamp", "keys": [
        {"frame": f, "light": {"intensity": v}} for f, v in ((0, 0), (1, 100), (4, 100),
                                                            (5, 0))]}], "smooth")
    assert all(0 <= step[0]["light"]["intensity"] <= 100 for step in overshoot)
