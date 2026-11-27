import pytest
from bryce_files import composite
from PIL import Image, ImageDraw

from bryce3d_mcp import layout, scene
from bryce3d_mcp.commands import normalize_keys
from bryce3d_mcp.controller import BryceController, find_save_box, format_number
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


MENU = ("File/New Document...", "File/Open...", "File/Import Object...", "File/Save...",
        "File/Save As...", "File/Render", "File/Clear and Render", "Edit/Undo", "Edit/Clear")


def with_menu(backend):
    backend.menu_items = [{"path": path, "id": number, "menu": 900, "enabled": True}
                          for number, path in enumerate(MENU, 1)]


def a_file_dialog():
    return {"window": 55, "title": "Save As", "text": [], "takes_path": True,
            "buttons": [{"id": 1, "label": "Save"}, {"id": 2, "label": "Cancel"}]}


@pytest.mark.parametrize("name, number", [("File/Import Object", 3), ("import_object", 3),
                                          ("render", 6), ("Clear and Render", 7),
                                          ("clear_and_render", 7), ("undo", 8)])
def test_menu_names(bryce, name, number):
    controller, backend = bryce
    with_menu(backend)
    controller.command(name)
    assert backend.log == [("menu", number)]            # a message, no keys or clicks


def test_risky_commands_need_confirmation(bryce):
    controller, backend = bryce
    with_menu(backend)
    for name in ("new_document", "open", "save", "save_as", "Edit/Clear"):
        with pytest.raises(ConfirmationRequired):
            controller.command(name)
    assert backend.log == []


def test_commands_refuse_when_a_windows_dialog_is_open(bryce):
    controller, backend = bryce
    with_menu(backend)
    backend.open_dialogs = [a_file_dialog()]
    with pytest.raises(UnexpectedScreen):
        controller.command("render")
    assert backend.log == []


def test_save_as_fills_the_dialog_by_message(bryce, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    backend.on_menu = lambda item: backend.open_dialogs.append(a_file_dialog())
    backend.on_fill = write_scene
    result = controller.save_scene(str(tmp_path / "a"), confirm=True)
    assert result["saved"].endswith("a.br3") and result["verified"]
    assert backend.log == [("menu", 5), ("fill_dialog", 55, str(tmp_path / "a.br3"))]


def test_no_dialog_means_nothing_is_filled(bryce, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    with pytest.raises(BryceError, match="no Windows file dialog"):
        controller.save_scene(str(tmp_path / "a"), confirm=True)
    assert backend.log == [("menu", 5)]


def test_save_and_open_guards(bryce, tmp_path):
    controller, backend = bryce
    existing = tmp_path / "a.br3"
    existing.write_text("x")
    with pytest.raises(ConfirmationRequired):
        controller.save_scene(str(tmp_path / "b"))
    with pytest.raises(BryceError, match="already exists"):
        controller.save_scene(str(existing), confirm=True)
    with pytest.raises(ConfirmationRequired):
        controller.open_scene(str(existing))
    assert backend.log == []


def test_dialog_buttons(bryce):
    controller, backend = bryce
    backend.open_dialogs = [a_file_dialog()]
    assert controller.dialogs()[0]["title"] == "Save As"
    with pytest.raises(ConfirmationRequired):
        controller.press_dialog_button("cancel")
    controller.press_dialog_button("cancel", confirm=True)
    assert backend.log == [("button", 55, 2)]


@pytest.mark.parametrize("mode, event", [("resume", ("menu", 6)), ("clear", ("menu", 7)),
                                         ("button", ("click", layout.RENDER_BUTTON, 1))])
def test_render_modes(bryce, mode, event):
    controller, backend = bryce
    with_menu(backend)
    controller.start_render(mode)
    assert backend.log == [event]


@pytest.fixture(name="scene_file")
def scene_file_fixture(tmp_path):
    return scene_file(tmp_path)


def scene_file(tmp_path):
    scene = tmp_path / "s.br3"
    scene.write_text("x")
    return str(scene)


def test_open_and_render_uses_only_messages(bryce, tmp_path):
    controller, backend = bryce
    with_menu(backend)

    def rendering(item):
        backend.render_frames, backend._frame = 6, 0
    backend.on_menu = rendering
    scene = scene_file(tmp_path)
    result = controller.open_and_render(scene, confirm=True, timeout=120)
    assert result["finished"] and result["started_by"] == "menu"
    assert backend.log == [("open_file", scene), ("menu", 7)]


def test_a_render_that_never_starts_is_not_a_success(bryce, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    result = controller.open_and_render(scene_file(tmp_path), confirm=True, timeout=300)
    assert result["finished"] is False and "no sign of rendering" in result["reason"]


def test_a_blank_picture_never_counts_as_rendered(bryce):
    controller, backend = bryce
    backend.grab = lambda box: Image.new("RGB", (box[2] - box[0], box[3] - box[1]),
                                         (255, 255, 255))
    result = controller.wait_for_still_picture(timeout=30, settle=3, poll=1)
    assert result["finished"] is False and "blank" in result["reason"]


def test_a_box_from_bryce_stops_opening(bryce, tmp_path):
    """What happened on the first live run: 'Do you want to save your changes?'."""
    controller, backend = bryce
    with_menu(backend)
    backend.box_on_open = True
    with pytest.raises(UnexpectedScreen, match="save your changes"):
        controller.open_and_render(scene_file(tmp_path), confirm=True)
    assert not any(event[0] == "menu" for event in backend.log)


def test_unsaved_work_is_kept_unless_told_otherwise(bryce, tmp_path):
    controller, backend = bryce
    backend.unsaved = True
    scene = scene_file(tmp_path)
    with pytest.raises(ConfirmationRequired, match="unsaved changes"):
        controller.open_scene(scene, confirm=True)
    assert backend.log == []
    controller.open_scene(scene, confirm=True, discard_changes=True)
    assert backend.log == [("forget",), ("open_file", scene)]


def test_bridge_counters_start_and_finish_a_render(bryce):
    """The picture can stay still while Bryce is working: the counters show it."""
    controller, backend = bryce
    backend.counters = {"blits": 0, "checks": 0}
    work = {"left": 0}
    original = backend.sleep

    def sleep(seconds):
        if work["left"]:
            work["left"] -= 1
            backend.counters["checks"] += 500
        original(seconds)
    backend.sleep = sleep
    result = controller.watch_render(lambda: work.update(left=10), timeout=200,
                                     settle=6, poll=2)
    assert result["finished"] and result["seconds"] == 20
    assert result["timeline"][0]["checks"] == 500


def test_the_render_button_is_the_fallback_with_the_bridge(bryce, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    backend.counters = {"blits": 0, "checks": 0}

    def click(x, y, button="left", count=1):
        backend.log.append(("click", (x, y), count))
        backend.render_frames, backend._frame = 5, 0
    backend.click = click
    result = controller.open_and_render(scene_file(tmp_path), confirm=True, timeout=300)
    assert result["finished"] and result["started_by"].startswith("render button")
    assert ("click", layout.RENDER_BUTTON, 1) in backend.log and backend.log[-1] == ("end",)


def test_render_refuses_while_a_dialog_is_open(bryce):
    controller, backend = bryce
    backend.attributes = True
    with pytest.raises(UnexpectedScreen):
        controller.start_render("button")
    assert backend.log == []


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


def write_scene(path):
    with open(path, "wb") as handle:
        handle.write(composite({1: b"x" * 40}))


def box_picture():
    """The parts of Bryce's save question that the detector looks at."""
    picture = Image.new("RGB", (704, 660), (255, 255, 255))
    ImageDraw.Draw(picture).rectangle((50, 130, 337, 181), fill=(236, 236, 236))
    ImageDraw.Draw(picture).rectangle((52, 160, 243, 181), fill=(233, 233, 233))
    ImageDraw.Draw(picture).rectangle((244, 160, 305, 181), fill=(116, 116, 116))
    return picture


def test_save_box_is_found_in_a_picture():
    found = find_save_box(box_picture(), (0, -20))
    assert found == {"dont_save": (98, 150), "cancel": (203, 150), "save": (276, 150)}
    assert find_save_box(Image.new("RGB", (704, 660), (116, 116, 116))) is None
    assert find_save_box(Image.new("RGB", (704, 660), (236, 236, 236))) is None
    assert find_save_box(Image.new("RGB", (20, 20), (116, 116, 116))) is None


def test_a_save_that_writes_nothing_is_an_error(bryce, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    backend.on_menu = lambda item: backend.open_dialogs.append(a_file_dialog())
    with pytest.raises(UnexpectedScreen, match="did not write"):
        controller.save_scene(str(tmp_path / "a"), confirm=True, timeout=3)


def test_a_half_written_file_is_not_called_saved(bryce, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    backend.on_menu = lambda item: backend.open_dialogs.append(a_file_dialog())

    def partial(path):
        with open(path, "wb") as handle:
            handle.write(b"CCmF")

    backend.on_fill = partial
    with pytest.raises(UnexpectedScreen, match="did not write"):
        controller.save_scene(str(tmp_path / "a"), confirm=True, timeout=3)


def test_answering_the_save_box(bryce):
    controller, backend = bryce
    with pytest.raises(UnexpectedScreen, match="not on screen"):
        controller.answer_save_box("cancel")
    backend.capture = box_picture()
    backend.box_closes_on_click = True
    with pytest.raises(ConfirmationRequired):
        controller.answer_save_box("dont_save")
    assert backend.log == []
    assert controller.answer_save_box("cancel")["answered"] == "cancel"
    assert backend.log[0][:2] == ("click", (203, 150))
    assert backend.log[-1] == ("end",)


def test_nothing_is_sent_while_bryce_is_asking(bryce, scene_file):
    controller, backend = bryce
    backend.capture = box_picture()
    with pytest.raises(UnexpectedScreen, match="already asking"):
        controller.open_scene(scene_file, confirm=True)
    assert backend.log == []


def test_discarding_answers_a_box_that_appears_anyway(bryce, scene_file):
    controller, backend = bryce
    backend.counters, backend.box_closes_on_click = {}, True
    backend.on_open = lambda path: setattr(backend, "capture", box_picture())
    controller.open_scene(scene_file, confirm=True, discard_changes=True)
    assert ("click", (98, 150), 1) in backend.log


def test_a_box_is_left_alone_unless_discarding_was_asked(bryce, scene_file):
    controller, backend = bryce
    backend.counters = {}
    backend.on_open = lambda path: setattr(backend, "capture", box_picture())
    with pytest.raises(UnexpectedScreen, match="answer_save_box"):
        controller.open_scene(scene_file, confirm=True)
    assert not [e for e in backend.log if e[0] == "click"]


def test_load_saves_first_when_asked(bryce, scene_file, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    backend.unsaved = True
    backend.on_menu = lambda item: backend.open_dialogs.append(a_file_dialog())
    backend.on_fill = write_scene
    with pytest.raises(BryceError, match="save_current_to"):
        controller.load_file(scene_file, unsaved="save", confirm=True)
    with pytest.raises(ConfirmationRequired):
        controller.load_file(scene_file, confirm=True)
    assert backend.log == []
    result = controller.load_file(scene_file, unsaved="save", confirm=True,
                                  save_current_to=str(tmp_path / "keep"))
    assert result["saved_first"]["verified"]
    steps = [e[0] for e in backend.log]
    assert steps.index("fill_dialog") < steps.index("open_file")


def test_load_does_not_open_if_the_save_fails(bryce, scene_file, tmp_path):
    controller, backend = bryce
    with_menu(backend)
    backend.unsaved = True
    with pytest.raises(BryceError):
        controller.load_file(scene_file, unsaved="save", confirm=True,
                             save_current_to=str(tmp_path / "keep"))
    assert not [e for e in backend.log if e[0] in ("open_file", "forget")]
