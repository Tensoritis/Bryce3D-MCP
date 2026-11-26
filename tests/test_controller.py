import pytest

from bryce3d_mcp import layout, scene
from bryce3d_mcp.commands import normalize_keys
from bryce3d_mcp.controller import BryceController, format_number
from bryce3d_mcp.errors import BryceError, ConfirmationRequired, FocusLost, UnexpectedScreen
from fake import FakeBackend


@pytest.fixture
def bryce():
    backend = FakeBackend()
    return BryceController(backend), backend


def test_create_then_place(bryce):
    controller, backend = bryce
    controller.create("cube")
    controller.set_attributes(position=[25.5, 30, 0])
    assert ("click", layout.PRIMITIVES["cube"], 1) in backend.log
    assert backend.typed() == ["25.5", "30", "0"]
    assert ("click", layout.ATTR_FIELDS["position"][0], 2) in backend.log
    assert not backend.attributes


def test_size_is_entered_before_position(bryce):
    controller, backend = bryce
    controller.set_attributes(position=[1, 2, 3], size=[4, 5, 6], rotation=[7, 8, 9])
    assert backend.typed() == ["4", "5", "6", "7", "8", "9", "1", "2", "3"]


@pytest.mark.parametrize("backdrop", [(0, 0, 0), (255, 255, 255), (120, 120, 120), (40, 90, 200)])
def test_never_types_if_dialog_does_not_open(bryce, backdrop):
    """Holds whatever is behind Bryce: black, white, gray or a colored wallpaper."""
    controller, backend = bryce
    backend.attributes_never_opens = True
    backend.backdrop = backdrop
    with pytest.raises(UnexpectedScreen):
        controller.set_attributes(position=[1, 2, 3])
    assert backend.typed() == []


def test_failed_creation_does_not_edit_previous_object(bryce):
    controller, backend = bryce
    backend.create_does_nothing = True
    steps = scene.expand([{"type": "cube", "position": [1, 2, 3], "material": "red"}])
    result = scene.build(controller, steps)
    assert result["completed"] is False and result["failed_stage"] == "create"
    assert backend.typed() == []
    assert ("key", "ctrl+alt+e") not in backend.log


def test_focus_loss_sends_nothing(bryce):
    controller, backend = bryce
    backend.focused = False
    with pytest.raises(FocusLost):
        controller.create("sphere")
    assert backend.log == []


def test_material_flow(bryce):
    controller, backend = bryce
    controller.apply_material("simple&fast", *layout.SIMPLE_COLORS["red"])
    clicks = [e[1] for e in backend.log if e[0] == "click"]
    assert clicks == [layout.MAT_LIBRARY_ARROW, layout.MATERIAL_CATEGORIES["simple&fast"],
                      layout.library_cell(0, 3), layout.LIBRARY_OK, layout.MAT_OK]


def test_no_blind_clicks_if_library_does_not_open(bryce):
    controller, backend = bryce
    backend.library_never_opens = True
    with pytest.raises(UnexpectedScreen):
        controller.apply_material("metals", 1, 1)
    assert [e[1] for e in backend.log if e[0] == "click"] == [layout.MAT_LIBRARY_ARROW]


@pytest.mark.parametrize("keys", ["alt+F4", "Alt+Tab", "alt+shift+tab", "ctrl+esc",
                                  "alt+space", "ctrl+alt+del"])
def test_blocked_keys(bryce, keys):
    controller, backend = bryce
    with pytest.raises(BryceError):
        controller.press(keys, confirm=True)
    assert backend.log == []


@pytest.mark.parametrize("keys", ["delete", "shift+Delete", "Control+N", "ctrl+s", "ctrl+o"])
def test_destructive_keys_need_confirmation(bryce, keys):
    controller, backend = bryce
    with pytest.raises(ConfirmationRequired):
        controller.press(keys)
    assert backend.log == []
    controller.press(keys, confirm=True)
    assert backend.log


def test_destructive_command_needs_confirmation(bryce):
    controller, backend = bryce
    with pytest.raises(ConfirmationRequired):
        controller.run_command("new_document")
    controller.run_command("duplicate")
    assert backend.log == [("key", "ctrl+d")]


def test_key_normalization():
    assert normalize_keys(" E + Alt + Control ") == "ctrl+alt+e"
    for bad in ("", "ctrl", "a+b", "win+r"):
        with pytest.raises(ValueError):
            normalize_keys(bad)


def test_path_only_typed_into_a_real_file_dialog(bryce, tmp_path):
    controller, backend = bryce
    with pytest.raises(UnexpectedScreen):
        controller.save_scene_as(str(tmp_path / "a"))
    assert backend.typed() == []
    backend.window_class = "#32770"
    assert controller.save_scene_as(str(tmp_path / "a")).endswith("a.br3")


def test_save_and_open_guards(bryce, tmp_path):
    controller, backend = bryce
    existing = tmp_path / "a.br3"
    existing.write_text("x")
    with pytest.raises(ConfirmationRequired):
        controller.save_scene_as(str(existing))
    with pytest.raises(ConfirmationRequired):
        controller.open_scene(str(existing))
    assert backend.log == []


def test_menus(bryce):
    controller, backend = bryce
    with pytest.raises(BryceError, match="no native Windows menu"):
        controller.invoke_menu("File/Import Object")
    backend.menu_items = [{"path": "File/Import Object...", "id": 7, "enabled": True},
                          {"path": "File/New", "id": 8, "enabled": True}]
    controller.invoke_menu("file/import object")
    assert ("menu", 7) in backend.log
    with pytest.raises(ConfirmationRequired):
        controller.invoke_menu("File/New")


def test_render_waits_for_a_still_picture(bryce):
    controller, backend = bryce
    backend.render_frames = 10
    result = controller.render(timeout=100, settle=6, poll=2)
    assert result == {"finished": True, "seconds": 22}
    backend.render_frames, backend._frame = 10_000, 0
    assert controller.render(timeout=10, settle=6, poll=2)["finished"] is False


@pytest.mark.parametrize("value, text", [(25.5, "25.5"), (30, "30"), (-0.001, "0"),
                                         (10.239, "10.24"), (-90, "-90"), (100, "100")])
def test_number_format(value, text):
    assert format_number(value) == text
