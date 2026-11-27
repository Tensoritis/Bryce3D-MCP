"""Client for bryce3d_bridge.dll, the helper that runs inside Bryce 3D.

The DLL gives Bryce a virtual mouse and keyboard, reports whether the scene has
unsaved changes and how busy Bryce is, and pictures Bryce's windows. It is
loaded only when asked, into the exact Bryce 3D build it was written for, and it
leaves with Bryce: closing Bryce removes it completely.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from .errors import BryceError

DLL = Path(__file__).with_name("bin") / "bryce3d_bridge.dll"
PIPE = r"\\.\pipe\bryce3d-bridge-{pid}"
LOADED_PREFIX = "bryce3d_bridge"
# SHA-256 of the program files the addresses in the DLL belong to.
SUPPORTED = {
    "bryce 3d.exe": "7cceb0d75130a70a9783e43aab089bdb27bf9e060f9ea7f0a2ba02b7e399ebe7",
    "axiom.dll": "a7b5cef57f55a0abe64dc0fd4f58ed63d892ed242ab5c44a44a42d2b8c5d1d38",
}
HOLD = 0.12          # a click shorter than this was seen not to select in Bryce


class BridgeClient:
    """The line protocol. `stream` needs write(bytes) and read(n) -> bytes."""

    def __init__(self, stream, sleep=time.sleep) -> None:
        self.stream = stream
        self.sleep = sleep
        self._buffer = b""

    # -- protocol ------------------------------------------------------------
    def _more(self) -> None:
        data = self.stream.read(65536)
        if not data:
            raise BryceError("the connection to the Bryce bridge was lost (did Bryce close?)")
        self._buffer += data

    def _line(self) -> bytes:
        while b"\n" not in self._buffer:
            self._more()
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line

    def _exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            self._more()
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def request(self, *words):
        line = " ".join(str(w) for w in words)
        if "\n" in line or len(line) > 200:
            raise BryceError("invalid bridge request")
        try:
            self.stream.write(line.encode("ascii") + b"\n")
            answer = self._line().decode("ascii", "replace")
        except OSError as exc:
            raise BryceError(f"the connection to the Bryce bridge failed: {exc}") from exc
        if answer.startswith("ERR "):
            raise BryceError(f"bridge: {answer[4:]}")
        if not answer.startswith("OK "):
            raise BryceError(f"unexpected answer from the bridge: {answer[:80]!r}")
        return json.loads(answer[3:])

    def close(self) -> None:
        close = getattr(self.stream, "close", None)
        if close:
            close()

    # -- information ---------------------------------------------------------
    def hello(self) -> dict:
        return self.request("hello")

    def state(self) -> dict:
        return self.request("state")

    def windows(self) -> list[dict]:
        """Bryce's windows, top-level ones front first, each followed by its children."""
        return self.request("windows")

    def forget_changes(self) -> bool:
        """Clear Bryce's unsaved-changes flag. True if there were unsaved changes."""
        return bool(self.request("forget-changes")["was_unsaved"])

    def capture(self, window: int) -> Image.Image:
        size = self.request("capture", int(window))
        data = self._exact(size["bytes"])
        return Image.frombuffer("RGB", (size["width"], size["height"]), data,
                                "raw", "BGRX", 0, 1).copy()

    # -- virtual input -------------------------------------------------------
    def virtual(self, on: bool) -> None:
        self.request("input", int(on))

    @contextmanager
    def input(self):
        """Hold the virtual mouse and keyboard for one action; always hand back."""
        self.virtual(True)
        try:
            yield self
        finally:
            self.virtual(False)

    def move(self, x: int, y: int) -> int:
        return self.request("move", int(x), int(y))["window"]

    def button(self, button: int, down: bool) -> int:
        return self.request("button", button, int(down))["window"]

    def key(self, code: int, down: bool) -> int:
        return self.request("key", code, int(down))["window"]

    def char(self, code: int) -> int:
        return self.request("char", code)["window"]


# ------------------------------------------------------------------ injection
def check_program_files(folder: str) -> None:
    """Refuse to touch any program other than the build the DLL was made for."""
    found = {name.lower(): name for name in os.listdir(folder)}
    for wanted, digest in SUPPORTED.items():
        if wanted not in found:
            raise BryceError(f"{wanted} was not found in {folder}")
        data = Path(folder, found[wanted]).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise BryceError(
                f"{found[wanted]} is not the Bryce 3D build the bridge supports, so nothing "
                "was loaded. The file tools and menu commands still work.")


def staged_dll(source: Path = DLL, folder: str | None = None) -> Path:
    """A copy named after its contents, so that a running Bryce never holds the
    repository's own file open."""
    if not source.is_file():
        raise BryceError(f"the bridge DLL is missing: {source} (build it with bridge/build.py)")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    folder = folder or os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                                    "bryce3d-mcp")
    os.makedirs(folder, exist_ok=True)
    target = Path(folder, f"{LOADED_PREFIX}-{digest}.dll")
    if not target.is_file() or target.read_bytes() != source.read_bytes():
        shutil.copyfile(source, target)
    return target


def find_export(read, base: int, wanted: str) -> int:
    """The address of an exported function in a 32-bit module of another process.
    `read(address, size)` returns bytes from that process."""
    def u32(address):
        return struct.unpack("<I", read(address, 4))[0]

    header = base + u32(base + 0x3C)
    if read(header, 4) != b"PE\0\0":
        raise BryceError("could not read a module inside Bryce")
    directory, size = struct.unpack("<II", read(header + 24 + 96, 8))
    table = base + directory
    _, _, _, count, functions, names, ordinals = struct.unpack("<7I", read(table + 12, 28))
    name_table = struct.unpack(f"<{count}I", read(base + names, 4 * count))
    target = wanted.encode("ascii") + b"\0"
    low, high = 0, count - 1
    while low <= high:                          # export names are sorted
        middle = (low + high) // 2
        name = read(base + name_table[middle], len(target))
        if name == target:
            ordinal = struct.unpack("<H", read(base + ordinals + 2 * middle, 2))[0]
            address = u32(base + functions + 4 * ordinal)
            if directory <= address < directory + size:
                raise BryceError(f"{wanted} is forwarded to another module")
            return base + address
        if name < target:
            low = middle + 1
        else:
            high = middle - 1
    raise BryceError(f"{wanted} was not found inside Bryce")


class _Process:
    """The few process operations needed to load a DLL, through ctypes."""

    ACCESS = 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0400  # thread, VM, query

    def __init__(self, pid: int) -> None:
        if sys.platform != "win32":
            raise BryceError("the bridge only works on Windows, next to Bryce 3D")
        from ctypes import wintypes
        self.kernel32 = k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        H, D, B, P, S = (wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_void_p,
                         ctypes.c_size_t)
        for function, result, arguments in (
            (k.OpenProcess, H, [D, B, D]), (k.CloseHandle, B, [H]),
            (k.ReadProcessMemory, B, [H, P, P, S, ctypes.POINTER(S)]),
            (k.WriteProcessMemory, B, [H, P, P, S, ctypes.POINTER(S)]),
            (k.VirtualAllocEx, P, [H, P, S, D, D]), (k.VirtualFreeEx, B, [H, P, S, D]),
            (k.CreateRemoteThread, H, [H, P, S, P, P, D, P]),
            (k.WaitForSingleObject, D, [H, D]),
            (k.GetExitCodeThread, B, [H, ctypes.POINTER(D)]),
            (self.psapi.EnumProcessModulesEx, B,
             [H, ctypes.POINTER(H), D, ctypes.POINTER(D), D]),
            (self.psapi.GetModuleBaseNameW, D, [H, H, wintypes.LPWSTR, D]),
        ):
            function.restype, function.argtypes = result, arguments
        self.handle = k.OpenProcess(self.ACCESS, False, pid)
        if not self.handle:
            raise BryceError("Windows did not allow access to Bryce. If Bryce runs as "
                             "administrator, run this the same way.")

    def close(self) -> None:
        self.kernel32.CloseHandle(self.handle)

    def modules(self) -> dict[str, int]:
        """{lower-case name: base} of the 32-bit modules in the process."""
        from ctypes import wintypes
        modules = (wintypes.HANDLE * 512)()
        needed = wintypes.DWORD()
        if not self.psapi.EnumProcessModulesEx(self.handle, modules, ctypes.sizeof(modules),
                                               ctypes.byref(needed), 0x01):
            raise BryceError("could not list the modules inside Bryce")
        found = {}
        for module in modules[:min(512, needed.value // ctypes.sizeof(wintypes.HANDLE))]:
            name = ctypes.create_unicode_buffer(260)
            if module and self.psapi.GetModuleBaseNameW(self.handle, module, name, 260):
                found.setdefault(name.value.lower(), int(module))
        return found

    def read(self, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not self.kernel32.ReadProcessMemory(self.handle, address, buffer, size,
                                               ctypes.byref(done)) or done.value != size:
            raise BryceError("could not read from Bryce")
        return buffer.raw

    def load_library(self, path: str) -> None:
        kernel = self.modules().get("kernel32.dll")
        if kernel is None:
            raise BryceError("Bryce is still starting; try again in a moment")
        loader = find_export(self.read, kernel, "LoadLibraryW")
        data = (path + "\0").encode("utf-16-le")
        memory = self.kernel32.VirtualAllocEx(self.handle, None, len(data), 0x3000, 0x04)
        if not memory:
            raise BryceError("could not reserve memory in Bryce")
        thread = None
        try:
            done = ctypes.c_size_t()
            if not self.kernel32.WriteProcessMemory(self.handle, memory, data, len(data),
                                                    ctypes.byref(done)):
                raise BryceError("could not write to Bryce")
            thread = self.kernel32.CreateRemoteThread(self.handle, None, 0, loader, memory,
                                                      0, None)
            if not thread:
                raise BryceError("Windows did not allow the bridge to start inside Bryce "
                                 "(security software may block this)")
            if self.kernel32.WaitForSingleObject(thread, 10000) != 0:
                memory = None                   # still in use: leave it alone
                raise BryceError("Bryce did not finish loading the bridge")
            from ctypes import wintypes
            result = wintypes.DWORD()
            self.kernel32.GetExitCodeThread(thread, ctypes.byref(result))
            if not result.value:
                raise BryceError("Windows could not load the bridge DLL into Bryce")
        finally:
            if thread:
                self.kernel32.CloseHandle(thread)
            if memory:
                self.kernel32.VirtualFreeEx(self.handle, memory, 0, 0x8000)


def inject(pid: int, install_folder: str, process_factory=_Process, source: Path = DLL,
           staging: str | None = None) -> bool:
    """Load the bridge into the running Bryce. False if it was already there."""
    check_program_files(install_folder)
    target = staged_dll(source, staging)
    process = process_factory(pid)
    try:
        loaded = [n for n in process.modules() if n.startswith(LOADED_PREFIX)]
        if loaded:
            if target.name.lower() not in loaded:
                raise BryceError("an older bridge is already loaded; close and restart Bryce "
                                 "to use this version")
            return False
        process.load_library(str(target))
        return True
    finally:
        process.close()


def open_pipe(pid: int, wait: float = 5.0, opener=None):
    """Connect to the bridge's pipe, waiting briefly for it to appear."""
    opener = opener or (lambda name: open(name, "r+b", buffering=0))
    deadline = time.monotonic() + wait
    while True:
        try:
            return opener(PIPE.format(pid=pid))
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise BryceError(f"the bridge is not answering: {exc}. Another program may "
                                 "already be connected to it.") from exc
            time.sleep(0.1)


def attach(pid: int, install_folder: str) -> BridgeClient:
    """Load the bridge if needed and return a working client."""
    try:
        stream = open_pipe(pid, wait=0)             # already loaded
    except BryceError:
        inject(pid, install_folder)
        stream = open_pipe(pid)
    client = BridgeClient(stream)
    hello = client.hello()
    if not hello.get("ready"):
        client.close()
        raise BryceError(f"the bridge did not activate: {hello.get('problem')}")
    return client
