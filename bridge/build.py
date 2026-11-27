"""Build bryce3d_bridge.dll with clang, llvm-dlltool and lld-link.

No Windows SDK or C runtime is needed, so this works on Linux, macOS or Windows
with LLVM installed:  python bridge/build.py
"""
import pathlib
import re
import shutil
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
OUTPUT = HERE.parent / "src" / "bryce3d_mcp" / "bin" / "bryce3d_bridge.dll"
LIBRARIES = ("kernel32", "user32", "gdi32")


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} was not found; install LLVM (clang and lld)")
    return found


def declared() -> dict[str, dict[str, str]]:
    """{library: {function: decorated name}} from the header's declarations."""
    text = (HERE / "win32_min.h").read_text()
    found: dict[str, dict[str, str]] = {}
    for library in LIBRARIES:
        start = text.index(f"/* {library} */")
        later = [text.find(f"/* {other} */") for other in LIBRARIES]
        end = min([i for i in later if i > start] or [len(text)])
        functions = {}
        for line in re.findall(r"^(?:IMPORT|__declspec\(dllimport\))[^;]*;", text[start:end], re.M):
            name = re.search(r"(\w+)\(([^()]*)\);$", line)
            if "__cdecl" in line:
                functions[name.group(1)] = name.group(1)
                continue
            size = 0
            for argument in name.group(2).split(","):
                argument = argument.strip()
                if argument == "void":
                    continue
                size += 8 if argument == "POINT" else 4
            functions[name.group(1)] = f"{name.group(1)}@{size}"
        found[library] = functions
    return found


def build(output: pathlib.Path = OUTPUT) -> pathlib.Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        work = pathlib.Path(work)
        libraries = []
        for library, functions in declared().items():
            definition = work / f"{library}.def"
            definition.write_text(f"LIBRARY {library}.dll\nEXPORTS\n"
                                  + "".join(f"{decorated}\n" for decorated in functions.values()))
            target = work / f"{library}.lib"
            subprocess.run([tool("llvm-dlltool"), "-m", "i386", "-k", "-d", str(definition),
                            "-l", str(target)], check=True)
            libraries.append(str(target))
        objects = work / "bridge.obj"
        subprocess.run([tool("clang"), "--target=i686-pc-windows-msvc", "-c", "-O1",
                        "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                        "-mno-stack-arg-probe", "-mno-sse", "-Wall", "-Wextra", "-Werror",
                        str(HERE / "bridge.c"), "-o", str(objects)], check=True)
        subprocess.run([tool("lld-link"), "/dll", "/machine:x86", "/subsystem:windows",
                        "/entry:DllMain@12", "/nodefaultlib", "/safeseh:no", "/dynamicbase",
                        "/nxcompat", "/base:0x6b420000", "/Brepro", "/noimplib", f"/out:{output}", str(objects),
                        *libraries], check=True)
    return output


if __name__ == "__main__":
    print(build())
