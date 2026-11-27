"""The bridge: the Python client and injector, and the real DLL.

The DLL tests run bryce3d_bridge.dll's machine code on 32-bit-capable x86 Linux,
against the code of your own Bryce (set BRYCE3D_INSTALL), with tests/bridge/
harness.c standing in for Windows. They are skipped anywhere that cannot do that.
"""

import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from bryce3d_mcp import bridge
from bryce3d_mcp.errors import BryceError

ROOT = Path(__file__).resolve().parent.parent


class Script:
    """A stream that answers each request line from a list."""

    def __init__(self, *answers):
        self.answers, self.sent, self.pending = list(answers), [], b""

    def write(self, data):
        self.sent.append(data.decode().strip())
        self.pending += self.answers.pop(0)

    def read(self, count):
        data, self.pending = self.pending[:7], self.pending[7:]     # arrives in pieces
        return data


def test_protocol_reads_split_answers_and_pictures():
    pixels = bytes((10, 20, 30, 0)) * 6
    stream = Script(b'OK {"ready":1}\n', b'OK {"width":3,"height":2,"bytes":24}\n' + pixels,
                    b"ERR switch the virtual input on first\n")
    client = bridge.BridgeClient(stream)
    assert client.hello() == {"ready": 1}
    picture = client.capture(7)
    assert picture.size == (3, 2) and picture.getpixel((2, 1)) == (30, 20, 10)
    with pytest.raises(BryceError, match="virtual input"):
        client.move(1, 2)
    assert stream.sent == ["hello", "capture 7", "move 1 2"]


def test_virtual_input_is_handed_back_after_an_error():
    stream = Script(b"OK {}\n", b"OK {}\n")
    client = bridge.BridgeClient(stream)
    with pytest.raises(RuntimeError), client.input():
        raise RuntimeError("stop")
    assert stream.sent == ["input 1", "input 0"]


def test_lost_connection_is_reported():
    with pytest.raises(BryceError, match="lost"):
        bridge.BridgeClient(Script(b"")).state()


def test_only_the_supported_program_is_touched(tmp_path):
    (tmp_path / "Bryce 3D.exe").write_bytes(b"something else")
    (tmp_path / "Axiom.dll").write_bytes(b"something else")
    calls = []
    with pytest.raises(BryceError, match="not the Bryce 3D build"):
        bridge.inject(1, str(tmp_path), process_factory=calls.append)
    assert not calls


class FakeProcess:
    def __init__(self, modules):
        self.loaded, self._modules, self.closed = [], modules, False

    def __call__(self, pid):
        return self

    def modules(self):
        return self._modules

    def load_library(self, path):
        self.loaded.append(path)

    def close(self):
        self.closed = True


def test_inject_loads_a_staged_copy_once(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "check_program_files", lambda folder: None)
    fresh = FakeProcess({"kernel32.dll": 1})
    assert bridge.inject(1, "x", fresh, staging=str(tmp_path)) and fresh.closed
    staged = Path(fresh.loaded[0])
    assert staged.parent == tmp_path and staged.read_bytes() == bridge.DLL.read_bytes()
    again = FakeProcess({staged.name.lower(): 2})
    assert bridge.inject(1, "x", again, staging=str(tmp_path)) is False and not again.loaded
    older = FakeProcess({"bryce3d_bridge-000000000000.dll": 2})
    with pytest.raises(BryceError, match="restart Bryce"):
        bridge.inject(1, "x", older, staging=str(tmp_path))


def test_find_export_in_a_synthetic_module():
    base, image = 0x70000000, bytearray(0x1000)
    names = [b"Alpha\0", b"LoadLibraryW\0", b"Zulu\0"]
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<II", image, 0x80 + 24 + 96, 0x200, 0x100)
    struct.pack_into("<7I", image, 0x200 + 12, 0, 1, 3, 3, 0x400, 0x420, 0x440)
    struct.pack_into("<3I", image, 0x400, 0x900, 0x910, 0x920)
    for i, name in enumerate(names):
        struct.pack_into("<I", image, 0x420 + 4 * i, 0x500 + 32 * i)
        image[0x500 + 32 * i:0x500 + 32 * i + len(name)] = name
    struct.pack_into("<3H", image, 0x440, 2, 0, 1)

    def read(address, size):
        return bytes(image[address - base:address - base + size])
    assert bridge.find_export(read, base, "LoadLibraryW") == base + 0x900
    assert bridge.find_export(read, base, "Zulu") == base + 0x910
    with pytest.raises(BryceError, match="not found"):
        bridge.find_export(read, base, "Missing")


# -- the real DLL ---------------------------------------------------------------
def install_folder():
    folder = os.environ.get("BRYCE3D_INSTALL")
    if not folder:
        pytest.skip("set BRYCE3D_INSTALL to your Bryce folder")
    return Path(folder)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "i686"):
        pytest.skip("needs x86 Linux")
    if not shutil.which("clang") or not shutil.which("lld"):
        pytest.skip("needs clang and lld")
    target = tmp_path_factory.mktemp("harness") / "harness"
    subprocess.run(["clang", "--target=i386-linux-gnu", "-ffreestanding", "-nostdlib", "-static",
                    "-fuse-ld=lld", "-O1", "-fno-builtin", "-fno-stack-protector", "-mno-sse",
                    "-Wl,--image-base=0x20000000",
                    str(ROOT / "tests" / "bridge" / "harness.c"), "-o", str(target)],
                   check=True)
    return target


def run(harness, lines, *options):
    folder = install_folder()
    files = {name.lower(): folder / name for name in os.listdir(folder)}
    done = subprocess.run([str(harness), str(files["axiom.dll"]), str(files["bryce 3d.exe"]),
                           str(bridge.DLL), *options],
                          input=("\n".join(lines) + "\n").encode(), capture_output=True,
                          timeout=30)
    assert done.returncode == 0, done.stdout.decode("latin-1")[-600:]
    return done.stdout


def answers(output):
    """The harness's and the bridge's reply lines, without the start and end chatter."""
    text = [line for line in output.decode("latin-1").split("\n") if line]
    return text[2:-2]


def test_dll_virtual_mouse_is_seen_by_bryces_own_code(harness):
    out = answers(run(harness, [
        "hello", "!front", "!button", "!exe",
        "input 1", "!front", "move 400 300", "!button", "button 1 1", "!button", "!pointer",
        "!exe", "button 1 0", "!button", "!posted", "input 0", "!front", "!exe", "!suspends"]))
    assert json.loads(out[0][3:])["ready"] == 1
    assert json.loads(out[0][3:])["import_hooks"] == 15
    # Not driven: Bryce is in the background and everything comes from Windows.
    assert out[1:4] == ["H front 0", "H button 0",
                        "H exe cursor 7 8 front 8193 under 8193 ctrl 0"]
    # Driven: Axiom's real IsFrontProcess, ButtonDown and PointerLoc see our input.
    assert out[5] == "H front 1"
    assert out[7] == "H button 0"
    assert out[9:12] == ["H button 1", "H pointer 400 300",
                         "H exe cursor 400 300 front 4097 under 4097 ctrl 0"]
    assert out[13] == "H button 0"
    position = (300 - 50) << 16 | (400 - 100)      # client coordinates of the window
    assert out[14:18] == [f"H post 4097 512 0 {position}", f"H post 4097 513 1 {position}",
                          f"H post 4097 514 0 {position}", "H posted 3"]
    # Handed back: Windows answers again.
    assert out[19:21] == ["H front 0", "H exe cursor 7 8 front 8193 under 8193 ctrl 0"]
    assert out[21] == "H suspends 1 1"             # paused once to patch, then resumed


def test_dll_keys_pointer_warps_and_a_box_in_front(harness):
    out = answers(run(harness, [
        "input 1", "key 17 1", "!exe", "!warp 5 6", "!exe", "key 17 0", "!posted",
        "char 55", "!posted", "!prompt", "move 400 220", "!posted", "!shift", "key 16 1",
        "!shift", "input 0", "!warp 5 6", "!posted"]))
    assert out[2].endswith("ctrl 1")
    assert out[3] == "H real cursor 7 8"            # your pointer did not move
    assert "cursor 5 6" in out[4]
    assert out[6].startswith("H post 4097 256 17 1")
    assert out[7].startswith("H post 4097 257 17")
    chars = [line for line in out if line.startswith("H post") and " 258 55 " in line]
    assert len(chars) == 1
    assert 'OK {"window":4098}' in out               # the box in front gets the input
    assert "H shift 0" in out and "H shift 1" in out
    assert "H real cursor 5 6" in out
    assert any(line.startswith("H post 4097 257 16") or line.startswith("H post 4098 257 16")
               for line in out)                      # held keys are released


def test_dll_gives_the_input_back_when_the_client_goes_quiet(harness):
    out = answers(run(harness, ["input 1", "move 400 300", "button 1 1", "!idle 10000", "!front",
                                "!idle 40000", "!front", "!button", "!posted"]))
    assert out[4] == "H front 1"                     # 10 s of quiet: still ours
    assert out[6:8] == ["H front 0", "H button 0"]   # 50 s: handed back
    assert any(" 514 " in line for line in out)      # the button was released


def test_dll_releases_everything_on_disconnect(harness):
    output = run(harness, ["input 1", "button 2 1"]).decode("latin-1")
    assert "disconnected" in output


def test_dll_unsaved_flag_state_and_interrupt_counter(harness):
    out = answers(run(harness, ["!dirty 1537", "state", "forget-changes", "!flags", "state",
                                "!interrupt", "!interrupt", "state"]))
    first = json.loads(out[1][3:])
    assert first["unsaved"] == 1 and first["flags"] == 1537 and first["checks"] == 0
    assert out[2] == 'OK {"was_unsaved":1}'
    assert out[3] == "H flags 1536"                  # only bit 0 changed
    assert json.loads(out[4][3:])["unsaved"] == 0
    assert out[5:7] == ["H interrupt 0", "H interrupt 0"]   # real code, via our hook
    assert json.loads(out[7][3:])["checks"] == 2


def test_dll_lists_windows_and_sends_pictures(harness):
    output = run(harness, ["!prompt", "windows", "capture 4097", "capture 8193", "capture x",
                           "move 1 2", "button 1 1", "key 65 1", "bogus", "state extra"])
    lines = output.split(b"\n")
    listing = next(line for line in lines if line.startswith(b"OK ["))
    windows = json.loads(listing[3:])
    assert [w["window"] for w in windows] == [4098, 4097]       # not the other program
    assert windows[0]["class"] == "Bryce?Alert"                # quotes made harmless
    assert windows[1]["title"] == "Bryce 3D ? ?"
    assert windows[1]["rect"] == [100, 50, 740, 530]
    start = output.index(b'OK {"width":4,"height":3,"bytes":48}\n')
    picture = output[output.index(b"\n", start) + 1:][:48]
    assert picture == b"".join(struct.pack("<I", 0x00102030 + i) for i in range(12))
    assert b"ERR not one of Bryce's windows" in output
    assert b"ERR usage: capture" in output
    assert output.count(b"ERR switch the virtual input on first") == 3
    assert b"ERR unknown request" in output


@pytest.mark.parametrize("option, reason", [("no-axiom", "Axiom.dll"),
                                            ("other-build", "not the Bryce 3D build")])
def test_dll_refuses_other_programs(harness, option, reason):
    out = answers(run(harness, ["hello", "input 1", "!front", "!exe"], option))
    hello = json.loads(out[0][3:])
    assert hello["ready"] == 0 and reason in hello["problem"] and hello["import_hooks"] == 0
    assert out[1].startswith("ERR bridge not active")
    assert out[2:4] == ["H front 0", "H exe cursor 7 8 front 8193 under 8193 ctrl 0"]


def test_committed_dll_matches_its_source(tmp_path):
    if not all(shutil.which(t) for t in ("clang", "lld-link", "llvm-dlltool")):
        pytest.skip("needs LLVM")
    sys.path.insert(0, str(ROOT / "bridge"))
    try:
        import build
        rebuilt = build.build(tmp_path / "bryce3d_bridge.dll")
    finally:
        sys.path.pop(0)
    if rebuilt.read_bytes() != bridge.DLL.read_bytes():
        pytest.skip("a different LLVM version builds different bytes")


# -- driving Bryce through the bridge ----------------------------------------
class FakeClient:
    def __init__(self):
        self.log, self.flags = [], 1

    def windows(self):
        return [{"window": 5, "parent": 0, "class": "CWinAppl", "title": "Bryce", "visible": 1,
                 "enabled": 1, "rect": [0, 0, 1920, 1080], "client": [0, 20, 1920, 1060]}]

    def capture(self, window):
        raise BryceError("no picture")

    def virtual(self, on):
        self.log.append(("input", on))

    def move(self, x, y):
        self.log.append(("move", x, y))

    def button(self, button, down):
        self.log.append(("button", button, down))

    def key(self, code, down):
        self.log.append(("key", code, down))

    def char(self, code):
        self.log.append(("char", chr(code)))


class Base:
    def sleep(self, seconds):
        pass

    def grab(self, box):
        return "picture"


def test_bridge_backend_sends_everything_through_the_bridge():
    from bryce3d_mcp.virtual import BridgeBackend
    client = FakeClient()
    backend = BridgeBackend(Base(), client)
    assert backend.grab((0, 0, 1, 1)) == "picture"       # pictures: the ordinary backend
    backend.begin()
    backend.click(10, 20, count=2)
    backend.key("ctrl+d")
    backend.type_text("4.5")
    backend.drag((0, 0), (4, 0), "right", duration=0.04)
    backend.end()
    origin = (640, 20 + (1040 - 480) // 2)
    assert client.log[0] == ("input", True) and client.log[-1] == ("input", False)
    assert client.log[1:6] == [("move", origin[0] + 10, origin[1] + 20), ("button", 1, True),
                               ("button", 1, False), ("button", 1, True), ("button", 1, False)]
    assert client.log[6:10] == [("key", 0x11, True), ("key", 0x44, True),
                                ("key", 0x44, False), ("key", 0x11, False)]
    assert [e[1] for e in client.log if e[0] == "char"] == ["4", ".", "5"]
    assert ("button", 2, True) in client.log and ("button", 2, False) in client.log
    with pytest.raises(BryceError, match="ASCII"):
        backend.type_text("café")


def test_bridge_backend_releases_a_button_when_a_drag_fails():
    from bryce3d_mcp.virtual import BridgeBackend
    client = FakeClient()
    backend = BridgeBackend(Base(), client)
    backend.begin()

    def broken(x, y, moves=iter(range(100))):
        if next(moves) > 0:
            raise BryceError("gone")
    client.move = broken
    with pytest.raises(BryceError):
        backend.drag((0, 0), (50, 0))
    assert client.log[-1] == ("button", 1, False)
