import asyncio
import json

import pytest

from bryce3d_mcp import server
from bryce3d_mcp.controller import BryceController
from fake import FakeBackend


@pytest.fixture
def backend(monkeypatch, tmp_path):
    fake = FakeBackend()
    monkeypatch.setattr(server, "_controller", BryceController(fake))
    monkeypatch.setenv("BRYCE3D_OUTPUT_DIR", str(tmp_path))
    return fake


def test_tool_list(backend):
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"screenshot", "build_scene", "create_object", "render", "hover", "click",
            "drag", "press_keys", "generate_mesh", "generate_heightmap",
            "terrain_list", "terrain_edit", "render_status", "save_render_image"} <= names
    assert {"br3_new", "br3_edit", "br3_preview", "br3_info", "br3_status", "br3_operations",
            "br3_render_result", "br3_open_in_bryce", "br3_export_obj",
            "br3_terrain_picture"} <= names
    assert len(names) == 42


def test_build_through_tool_function(backend):
    text, image = server.create_object("cube", position=[1, 2, 3], color="red")
    assert json.loads(text)["completed"] is True
    assert backend.typed() == ["1", "2", "3"]


def test_dry_run_sends_nothing(backend):
    plan = json.loads(server.build_scene([{"type": "torus"}], dry_run=True))
    assert plan["total"] == 1 and backend.log == []


def test_generated_files_are_never_overwritten(backend, tmp_path):
    args = dict(name="vase", shape="lathe", profile=[[0, 0], [3, 1], [0, 6]])
    first = json.loads(server.generate_mesh(**args))["path"]
    second = json.loads(server.generate_mesh(**args))["path"]
    assert first != second and (tmp_path / "vase.obj").exists()
    with pytest.raises(Exception):
        server.generate_mesh(name="../x", shape="lathe", profile=args["profile"])
    assert backend.log == []


def test_error_text_reaches_the_agent(backend):
    backend.focused = False
    with pytest.raises(server.ToolError, match="not focused"):
        server.run_command("undo")
    with pytest.raises(server.ToolError, match="one non-modifier key"):
        backend.focused = True
        server.press_keys("ctrl")
