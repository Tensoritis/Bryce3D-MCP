# How Bryce 3D's files work

Worked out by static analysis of an installed copy of MetaCreations Bryce 3D (executable
built November 1997): reading its files and disassembling `Axiom.dll` with `objdump`. No
Bryce code or data is included in this repository, and nothing in the program was changed.
Licensing and registration data were not examined.

## Container: "CCmF Composite File"

Used by object libraries (`.obp`), material libraries (`.mat`), sky presets (`.bsk`) and the
resource files in `Data/`. The structure comes from `CCmFile::Open`, `ValidateHeader`,
`ReadDirectoryData`, `NavigateE` and `ReadE` in `Axiom.dll`.

| Where | What |
| --- | --- |
| `0x000` | Signature starting `CCmF - Universal` |
| `0x200` | Format name, `Resource Composite File` |
| `0x24C`, `0x250` | u32 BE: directory size, offset of its first block |
| `0x260`, `0x264` | u32 BE: directory capacity, entries in use |
| block | u32 BE allocated, used, next block offset (0 = last), then the payload |
| directory | 12 bytes per entry: flags, size, offset (u32 BE) |

In the preset files, entry 16 lists class names (`C3dHeightField`, `C3dBaseMaterial`,
`Obj_ePosition`, `C3dCube`, `C3dTorusType`, ...) and entry 17 describes that table. Each
class row (32 bytes: name, then u32 BE data entry and descriptor entry at offset 16) points
to a table of objects. A descriptor starts with u32 BE record size, and holds the record
count at offset 12. In each object row, the u32 at offset 4 is the entry with its data.

## Terrain: `C3dHeightField`

| Offset | What |
| --- | --- |
| 8 | Name, NUL terminated |
| 272 | u32 LE: 8 when heights use only the top 11 bits |
| 648 | Six doubles, a bounding box that depends on grid size only in the files checked |
| 736 | u16 LE: grid size N (128, 256 and 512 seen) |
| 744 | `_elr` ("rle" tag) when compressed |
| 748 | u32 LE: compressed length |
| 900 | Heights |

Uncompressed heights are N x N little-endian u16. Compressed heights are pairs of
**big-endian** u16 (run length, height), then four zero bytes.

**Evidence:** all 32 terrains in the bundled `Mountains.obp` and `Tutorial.obp` decode to
recognizable pictures (a hand, a skull, spirals, river valleys), and re-encoding each one
reproduces the original record **byte for byte**. Run that check on your copy:

```bash
BRYCE3D_INSTALL="C:/path/to/Bryce 3D" pytest tests/test_terrain.py -k bundled
```

`objpresetdesc` (321 bytes) holds a library item's name (first 64 bytes) and description.
The library thumbnail is a separate `objprevcimage` record, which is not rewritten.

## Menus: `Data/menus.dat`

Menu items are 40-byte records:

| Bytes | What |
| --- | --- |
| 0-32 | Label (a leading style byte appears on a group's first item) |
| 33 | Shortcut character |
| 34 | 1 = Shift |
| 35 | `0x10` has a shortcut, `0x20` Ctrl, `0x40` Alt |
| 36-39 | Command code: `open`, `save`, `rndr`, `crnd`, `attr`, `edit`, `emat`, `time`, ... |

This reading gives Ctrl+Alt+E for Attributes and Ctrl+M for Edit Material, matching what
was pressed on a running copy, as do Ctrl+D, Ctrl+A and Ctrl+Z. Treating `0x40` alone as
Alt is an inference from the bit pattern and has not been pressed. The file holds two
variants of several menus; the first (Windows) one is used.

Notably, **Save As, Merge, Import Object, Export Image and Render to Disk have no
shortcut**, and the executable has no standard Windows menu or accelerator resources.

## The executable

PE32, relocations stripped, so it always loads at `0x400000`. Symbols are stripped, but it
and `Axiom.dll` export C++ names. Reading or changing the running program's memory is not
implemented: the offsets above describe saved records, and whether live objects have the
same layout has not been tested.

## Open questions

1. Does Bryce load a rewritten library or scene? The container is only changed where needed
   (same-size data in place; otherwise one block appended and one directory row updated),
   but this has **not been tried in Bryce**.
2. Are saved `.br3` scenes the same container? Likely, but no sample has been examined.
3. How do the grid axes map to scene X and Z?
4. Older, shorter terrain records (if any exist) are rejected rather than guessed at.
