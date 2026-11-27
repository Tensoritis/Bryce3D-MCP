"""Bryce's links between objects: joining, tracking and riding a path.

Read from the animated scenes on the Bryce CD (saved on a Mac) and written here in
the byte order of Windows scenes.

* **Joining (parent and child).** The parent gets an object list with its own id, the
  way a group does, and the child's entry moves there from the scene's list. The
  parent's entry then has a non-zero last word. The child's flags at 272 gain bit 4.
  Its position, size and turn are measured in the parent: a point v of the child's
  frame is drawn at v M + t, where M and t are the parent's cached matrix and
  translation. Parents nest.
* **Tracking.** The record C3dObjTrackList 1 holds one 24-byte entry per link: the
  follower's tag and id, then the target's. Bryce keeps the follower's own turn and
  only the cached matrix points at the target: the +z side of objects and cameras,
  and the beam (-y) of spotlights. Ordinary objects that track also gain bit 0x1000
  of the flags at 268.
* **Riding a path.** The rider is joined to the path, gains bit 0x800 of the flags at
  268, and has a track (Obj_ePathT, at 488) in place of its position track. Its value
  runs from 0 to the number of path points less one: a share of the path's length.

In the CD's scene `statue`, a camera rides a path and carries two spotlights, all
three tracking a sphere; read this way, each of them points at it to four decimals.
"""

from __future__ import annotations

import math
import struct

from . import curve as curves
from . import keyframes, motion
from .errors import BryceError

LIST_CLASS, ROOT_CLASS, GROUP_CLASS = "C3dObjList", "C3dRootObjList", "C3dGroupType"
TRACK_LIST = "C3dObjTrackList"
PATH_TRACK, PATH_SLOT = "Obj_ePathT", 488
_ATTRIBUTES, _MORE = 268, 272
_TRACKS, _ON_PATH, _IS_CHILD = 0x1000, 0x800, 0x4
_MATRIX, _TRANSLATION, _OFFSET = 380, 452, 356
_ENTRY = 24                                  # a tracking entry
_AIM_DOWN = ("spot_light", "square_spot_light")
_NO_AIM = ("radial_light", "group", "path", "plane")
_IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def times(v, m) -> list[float]:
    """The row vector `v` times the 3x3 matrix `m`."""
    return [sum(v[i] * m[i][j] for i in range(3)) for j in range(3)]


def product(a, b) -> list[list[float]]:
    return [times(row, b) for row in a]


def inverse(m) -> list[list[float]]:
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise BryceError("that object is flat (a size of zero), so nothing can be "
                         "measured inside it")
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def split(matrix) -> tuple[list[float], list[list[float]]] | None:
    """(sizes, turn) of a matrix that is a resize followed by a turn, else None."""
    sizes = [math.sqrt(sum(v * v for v in row)) for row in matrix]
    if min(sizes) < 1e-9:
        return None
    turn = [[v / size for v in row] for row, size in zip(matrix, sizes)]
    for i, j in ((0, 1), (0, 2), (1, 2)):
        if abs(sum(a * b for a, b in zip(turn[i], turn[j]))) > 1e-5:
            return None
    det = sum(turn[0][i] * (turn[1][(i + 1) % 3] * turn[2][(i + 2) % 3]
                            - turn[1][(i + 2) % 3] * turn[2][(i + 1) % 3])
              for i in range(3))
    if det < 0:                              # mirrored: carried by one size
        sizes[0], turn[0] = -sizes[0], [-v for v in turn[0]]
    return sizes, turn


def aim_rows(direction, down: bool) -> list[list[float]]:
    """A turn matrix whose front (row 2), or whose underside when `down`, points
    along `direction`, kept as upright as it can be."""
    length = math.sqrt(sum(v * v for v in direction))
    ahead = [v / length for v in direction]
    flat = math.hypot(ahead[0], ahead[2])
    right = [ahead[2] / flat, 0.0, -ahead[0] / flat] if flat > 1e-9 else [1.0, 0.0, 0.0]
    up = [ahead[1] * right[2] - ahead[2] * right[1],
          ahead[2] * right[0] - ahead[0] * right[2],
          ahead[0] * right[1] - ahead[1] * right[0]]
    if down:                                 # underside forward, front upward
        return [right, [-v for v in ahead], up]
    return [right, up, ahead]


class Links:
    """Mixed into Br3."""

    # -- reading ------------------------------------------------------------------------
    def _joined_list(self, ref):
        """The list of what is joined to an object, if it has one."""
        classes = self._ids.get(ref[1], {})
        if LIST_CLASS in classes and GROUP_CLASS not in classes:
            return (LIST_CLASS, ref[1])
        return None

    def _joined(self, ref) -> list:
        listing = self._joined_list(ref)
        return self._list_items(listing) if listing else []

    def _family(self) -> list:
        """(tag, record, raw entry, parent) of every object in the scene's list and
        of everything joined to one, parents first. Group members are not listed."""
        found, waiting = [], [(tag, ref, raw, None)
                              for tag, ref, raw in self._root()[2]]
        seen = set()
        while waiting:
            tag, ref, raw, parent = waiting.pop(0)
            if ref in seen:
                continue
            seen.add(ref)
            found.append((tag, ref, raw, parent))
            waiting[:0] = [(t, r, w, ref) for t, r, w in self._joined(ref)]
        return found

    def parent_of(self, ref):
        for _, other, _, parent in self._family():
            if other == ref:
                return parent
        return None

    def children_of(self, ref) -> list:
        return [other for _, other, _ in self._joined(ref)]

    def _cache(self, ref):
        payload = self._payload(ref)
        flat = struct.unpack_from("<9d", payload, _MATRIX)
        return ([list(flat[i * 3:i * 3 + 3]) for i in range(3)],
                list(struct.unpack_from("<3d", payload, _TRANSLATION)))

    def frame_around(self, ref) -> tuple[list, list]:
        """(M, t) taking a point measured in `ref`'s parent to the scene: v M + t,
        in file units. The unit frame for an object that is not joined to one."""
        parent = self.parent_of(ref)
        if parent is None:
            return _IDENTITY, [0.0, 0.0, 0.0]
        return self.frame_of(parent)

    def frame_of(self, ref) -> tuple[list, list]:
        """The frame that what is joined to `ref` is measured in."""
        matrix, move = self._cache(ref)
        outer, shift = self.frame_around(ref)
        return (product(matrix, outer),
                [a + b for a, b in zip(times(move, outer), shift)])

    def scene_centre(self, ref) -> list[float]:
        """An object's centre in the scene, in file units, however it is joined."""
        matrix, move = self.frame_around(ref)
        return [a + b for a, b in zip(times(self.centre_of(ref), matrix), move)]

    def tracking(self) -> list[tuple]:
        """(follower tag, follower id, target tag, target id) of every tracking link."""
        if TRACK_LIST not in self._ids.get(1, {}):
            return []
        payload = self._payload((TRACK_LIST, 1))
        number = struct.unpack_from("<I", payload, 8)[0]
        if 12 + _ENTRY * number > len(payload):
            raise BryceError("corrupt scene: the tracking list is too short")
        found = []
        for i in range(number):
            at = 12 + _ENTRY * i
            found.append((payload[at:at + 8][::-1].decode("latin-1"),
                          struct.unpack_from("<I", payload, at + 8)[0],
                          payload[at + 12:at + 20][::-1].decode("latin-1"),
                          struct.unpack_from("<I", payload, at + 20)[0]))
        return found

    def _entry_for(self, ref) -> str | None:
        """The list tag of a record, the camera's included."""
        return next((tag for tag, other, _, _ in self._family() if other == ref), None)

    def _by_id(self, tag: str, key: int):
        return next((other for found, other, _, _ in self._family()
                     if found == tag and other[1] == key), None)

    def target_of(self, ref):
        for _, follower, tag, key in self.tracking():
            if follower == ref[1]:
                return self._by_id(tag, key)
        return None

    def rides_path(self, ref) -> bool:
        return struct.unpack_from("<I", self._payload(ref), PATH_SLOT)[0] != 0 \
            and PATH_TRACK in self._ids.get(
                struct.unpack_from("<I", self._payload(ref), PATH_SLOT)[0], {})

    def describe_links(self, ref) -> dict:
        found = {}
        parent = self.parent_of(ref)
        if parent is not None:
            found["joined_to"] = self._name(parent)
        target = self.target_of(ref)
        if target is not None:
            found["tracks"] = self._name(target)
        if parent is not None and self.rides_path(ref):
            found["rides_path"] = self._name(parent)
        return found

    # -- flags and lists ----------------------------------------------------------------
    def _flag(self, ref, offset: int, bit: int, on: bool) -> None:
        value = struct.unpack_from("<I", self._payload(ref), offset)[0]
        value = value | bit if on else value & ~bit
        self._write(ref, offset, struct.pack("<I", value))

    def _mark_parent(self, ref, has: bool) -> None:
        """Set or clear the last word of an object's own list entry. Where Bryce
        saved it set, it holds the object's id: that of the list it owns."""
        owner = self.parent_of(ref)
        listing = (LIST_CLASS, owner[1]) if owner else self._first(ROOT_CLASS)
        items = []
        for _, other, raw in self._list_items(listing):
            if other == ref:
                raw = raw[:12] + struct.pack("<I", ref[1] if has else 0)
            items.append(raw)
        self._write_list(listing, items)

    def _take_out(self, ref) -> bytes:
        """Remove an object's entry from whichever list holds it, and return it."""
        parent = self.parent_of(ref)
        listing = (LIST_CLASS, parent[1]) if parent else self._first(ROOT_CLASS)
        items = self._list_items(listing)
        entry = next(raw for _, other, raw in items if other == ref)
        rest = [raw for _, other, raw in items if other != ref]
        self._write_list(listing, rest)
        if parent is not None and not rest:
            self._mark_parent(parent, False)
            self._drop_list(listing)
        return entry

    def _drop_list(self, listing) -> None:
        """Remove an emptied list. `_discard` would keep it: the object that owned
        it has the same id, which is how the two are tied."""
        rows = [row for row in self._rows[LIST_CLASS]
                if struct.unpack_from(">I", row)[0] != listing[1]]
        self.file.set_table(*self._classes[LIST_CLASS], rows)
        self._reindex()

    def _put_under(self, parent, entry: bytes) -> None:
        listing = self._joined_list(parent)
        if listing is None:
            classes = list(self._classes)
            before = classes[:classes.index(ROOT_CLASS)]
            self._add_record(LIST_CLASS, parent[1], struct.pack("<III", 1, 1, 1) + entry,
                             bytes(48), after=before)
            self._reindex()
        else:
            self._write_list(listing, [raw for _, _, raw in self._list_items(listing)]
                             + [entry])
            self._reindex()
        self._mark_parent(parent, True)

    # -- measuring in another frame -----------------------------------------------------
    def _scene_shape(self, ref) -> tuple[list, list]:
        """An object's own matrix and centre as drawn in the scene."""
        _, _, scale, turn = self.transform(ref)
        rows = self._turn_matrix(turn)
        matrix = [[rows[i][j] * scale[i] for j in range(3)] for i in range(3)]
        outer, _ = self.frame_around(ref)
        return product(matrix, outer), self.scene_centre(ref)

    def _measured_in(self, matrix, centre, frame) -> tuple[list, list, tuple]:
        """(centre, sizes, turn) that draw `matrix` and `centre` inside `frame`."""
        outer, shift = frame
        back = inverse(outer)
        parts = split(product(matrix, back))
        if parts is None:
            raise BryceError(
                "this cannot be done exactly: the parent is stretched unevenly and "
                "the object is turned inside it, which would need a slant. Make the "
                "parent's three sizes equal, or turn the object to match the parent")
        local = times([c - s for c, s in zip(centre, shift)], back)
        return local, parts[0], self._turn_from(parts[1])

    # -- joining ------------------------------------------------------------------------
    def _joinable(self, ref, role: str) -> None:
        if ref[0] == GROUP_CLASS and role == "parent":
            raise BryceError("a group already holds its own members; join to one "
                             "object, not to a group")

    def join(self, name: str, parent_name: str, library=None) -> dict:
        """Make `name` a child of `parent_name`. Neither moves."""
        _, child = self.find(name)
        _, parent = self.find(parent_name)
        if child == parent:
            raise BryceError("an object cannot be joined to itself")
        above = parent
        while above is not None:
            if above == child:
                raise BryceError(f"{parent_name!r} is already joined under {name!r}")
            above = self.parent_of(above)
        self._joinable(parent, "parent")
        if self.animated_channels(child) or self.rides_path(child):
            raise BryceError(f"{name!r} is animated. Join it first, or join only "
                             "objects that stand still")
        matrix, centre = self._scene_shape(child)
        local, sizes, turn = self._measured_in(matrix, centre, self.frame_of(parent))
        if self.parent_of(child) == parent:
            return self.describe(*self.find(name))
        entry = self._take_out(child)
        self._put_under(parent, entry)
        self._flag(child, _MORE, _IS_CHILD, True)
        self.place(child, local, sizes, turn, library)
        self.refresh_tracking()
        return self.describe(*self.find(name))

    def unjoin(self, name: str, library=None) -> dict:
        """Set a child free, where it stands."""
        _, child = self.find(name)
        if self.parent_of(child) is None:
            raise BryceError(f"{name!r} is not joined to anything")
        self._unjoin(child, library)
        return self.describe(self._kind_of_record(child), child)

    def _unjoin(self, child, library=None) -> None:
        name = self._name(child)
        if self.rides_path(child):
            self._leave_path(child, library)
        elif self.animated_channels(child):
            raise BryceError(f"{name!r} is animated inside its parent; its keys are "
                             "measured there, so it cannot be set free exactly")
        matrix, centre = self._scene_shape(child)
        local, sizes, turn = self._measured_in(matrix, centre,
                                               (_IDENTITY, [0.0, 0.0, 0.0]))
        entry = self._take_out(child)
        self._write_root([raw for _, _, raw in self._root()[2]] + [entry])
        self._reindex()
        self._flag(child, _MORE, _IS_CHILD, False)
        self.place(child, local, sizes, turn, library)
        self.refresh_tracking()

    # -- tracking -----------------------------------------------------------------------
    def _write_tracking(self, entries: list[tuple]) -> None:
        body = struct.pack("<III", 1, 1, len(entries)) + b"".join(
            tag.encode("latin-1")[::-1] + struct.pack("<I", key)
            + other.encode("latin-1")[::-1] + struct.pack("<I", wanted)
            for tag, key, other, wanted in entries)
        if TRACK_LIST in self._ids.get(1, {}):
            self.file.replace(self._entry((TRACK_LIST, 1)), body)
        elif entries:
            classes = list(self._classes)
            before = classes[:classes.index(ROOT_CLASS) + 1]
            self._add_record(TRACK_LIST, 1, body, bytes(48), after=before)
        self._reindex()

    def _follower(self, name: str):
        """(kind, record) of an object, or of the camera."""
        if name == "camera":
            return "camera", self._viewing_camera()
        return self.find(name)

    def track(self, name: str, target_name: str) -> dict:
        """Keep `name` (an object, or "camera") facing `target_name`."""
        kind, follower = self._follower(name)
        _, target = self.find(target_name)
        if follower == target:
            raise BryceError("an object cannot track itself")
        if kind in _NO_AIM:
            raise BryceError(f"a {kind.replace('_', ' ')} has no front to point")
        if self.target_of(target) == follower:
            raise BryceError("two things cannot track each other")
        tag, aimed = self._entry_for(follower), self._entry_for(target)
        entries = [e for e in self.tracking() if e[1] != follower[1]]
        entries.append((tag, follower[1], aimed, target[1]))
        self._write_tracking(entries)
        if kind != "camera" and not kind.endswith("light"):
            self._flag(follower, _ATTRIBUTES, _TRACKS, True)
        self.refresh_tracking()
        return {"tracks": target_name, "name": name}

    def untrack(self, name: str, library=None) -> dict:
        kind, follower = self._follower(name)
        if self.target_of(follower) is None:
            raise BryceError(f"{name!r} is not tracking anything")
        self._write_tracking([e for e in self.tracking() if e[1] != follower[1]])
        if kind != "camera":
            if not kind.endswith("light"):
                self._flag(follower, _ATTRIBUTES, _TRACKS, False)
            self._restore_turn(follower)
        return {"untracked": name}

    def _restore_turn(self, ref) -> None:
        """Put the cached matrix back to the object's own turn."""
        scale = self._vector(ref, "Obj_eScale") or self._cached_transform(ref)[2]
        turn = self._vector(ref, "Obj_eOrient", 4) or (0.0, 0.0, 0.0, 1.0)
        rows = self._turn_matrix(turn)
        self._write(ref, _MATRIX, struct.pack(
            "<9d", *(rows[i][j] * scale[i] for i in range(3) for j in range(3))))

    def refresh_tracking(self) -> None:
        """Point every follower at its target as things stand now. Bryce does this
        itself; the saved copy is kept right so that previews agree."""
        for tag, key, other, wanted in self.tracking():
            follower, target = self._by_id(tag, key), self._by_id(other, wanted)
            if follower is None or target is None:
                continue
            aimed_at = self.scene_centre(target)
            if follower[0] == "C3dBaseCamera":
                self._aim_camera(aimed_at)
                continue
            kind = self._kind_of_record(follower)
            origin = self.scene_centre(follower)
            direction = [a - b for a, b in zip(aimed_at, origin)]
            if sum(v * v for v in direction) < 1e-12:
                continue
            matrix, move = self._cache(follower)
            sizes = [math.sqrt(sum(v * v for v in row)) for row in matrix]
            outer, _ = self.frame_around(follower)
            parts = split(outer)
            if parts is None:
                continue
            down = kind in _AIM_DOWN
            local = product(aim_rows(direction, down), inverse(parts[1]))
            axis = 1 if down else 2
            sign = -1 if down else 1
            current = [sign * v / (sizes[axis] or 1) for v in matrix[axis]]
            if sum(a * b for a, b in zip(current, local[axis] if not down else
                                         [-v for v in local[axis]])) > 1 - 1e-9:
                continue                      # it already faces the target
            # Folding any offset into the translation keeps the centre where it is.
            centre = self._cached_transform(follower)[0]
            self._write(follower, _OFFSET, struct.pack("<3d", 0.0, 0.0, 0.0))
            self._write(follower, _TRANSLATION, struct.pack("<3d", *centre))
            self._write(follower, _MATRIX, struct.pack(
                "<9d", *(local[i][j] * sizes[i] for i in range(3) for j in range(3))))

    # -- riding a path ------------------------------------------------------------------
    def _path_spot(self, path, share: float) -> list[float]:
        """Where a rider `share` (0 to 1) of the way along a path is, measured from
        the path's centre, in file units."""
        payload = self._payload(path)
        points = [list(p) for p in motion.read_points(payload)]
        closed = motion.split_loop(points)[1]
        curve = motion.curve_through(points, closed, "bryce",
                                     motion.read_shaping(payload))
        return list(curve.at(min(1.0, max(0.0, share)))[0])

    def _ride_value(self, ref, time: float) -> float:
        record = self._payload((PATH_TRACK, struct.unpack_from(
            "<I", self._payload(ref), PATH_SLOT)[0]))
        entries = keyframes.values(record, 1, "f")
        return keyframes.at(entries, time, keyframes.STRAIGHT,
                            keyframes.time_curve(record))[0]

    def pose_riders(self, time: float) -> None:
        for _, ref, _, parent in self._family():
            if parent is None or parent[0] != motion.PATH_CLASS \
                    or not self.rides_path(ref):
                continue
            count = len(motion.read_points(self._payload(parent)))
            spot = self._path_spot(parent, self._ride_value(ref, time) / (count - 1))
            matrix, _ = self._cache(ref)
            self._write(ref, _OFFSET, struct.pack("<3d", 0.0, 0.0, 0.0))
            self._write(ref, _TRANSLATION, struct.pack("<3d", *spot))
            self._write(ref, _MATRIX, struct.pack("<9d", *(v for r in matrix for v in r)))

    def ride_path(self, name: str, path_name: str, keys: list[tuple[float, float]],
                  library=None, points=None) -> None:
        """Make Bryce's own path link. `keys` are (seconds, share of the way)."""
        _, rider = self.find(name)
        kind, path = self.find(path_name)
        if kind != "path":
            raise BryceError(f"{path_name!r} is not a path")
        if self.rides_path(rider):
            self._leave_path(rider, library)
        if self.animated_channels(rider):
            raise BryceError(f"{name!r} already has keys of its own")
        if self.parent_of(rider) != path:
            self.join(name, path_name, library)
        count = len(motion.read_points(self._payload(path)))
        record = keyframes.fresh(1, "f", keyframes.VALUE, (0.0,))
        record = keyframes.build(record, [(t, (share * (count - 1),))
                                          for t, share in keys], "f")
        if points:
            record = keyframes.with_curve(record, points)
        key = self._new_key()
        self._add_record(PATH_TRACK, key, record, bytes(48),
                         after=list(self._classes)[:list(self._classes).index(
                             "C3dRootObjList")])
        self._reindex()
        self._write(rider, PATH_SLOT, struct.pack("<I", key))
        old = self._linked(rider, "Obj_ePosition")
        if old is not None:                   # Bryce keeps no position track here
            self._write(rider, self._slot_of("Obj_ePosition"), struct.pack("<I", 0))
            self._reindex()
            self._discard({old})
        self._flag(rider, _ATTRIBUTES, _ON_PATH, True)

    def follow_native(self, name: str, path: str, *, start: int = 0,
                      end: int | None = None, reverse: bool = False, curve=None,
                      duration: float | None = None, library=None) -> dict:
        """Put an object on a path with Bryce's own link, from one end to the other
        between two frames."""
        timeline = self.timeline()
        fps = timeline["fps"]
        if end is None:
            end = max(timeline["duration_frames"], start + 90)
        for label, value in (("start", start), ("end", end)):
            if not isinstance(value, int) or isinstance(value, bool) \
                    or not 0 <= value <= 100_000:
                raise BryceError(f"{label} must be a frame number")
        if end <= start:
            raise BryceError("end must be after start")
        if duration is not None and not (isinstance(duration, (int, float))
                                         and 0 <= duration <= 100_000 / fps):
            raise BryceError("duration (seconds) is out of range")
        shares = (1.0, 0.0) if reverse else (0.0, 1.0)
        self.ride_path(name, path, [(start / fps, shares[0]), (end / fps, shares[1])],
                       library, None if curve is None else curves.points(curve))
        self._extend_timeline(end / fps, duration)
        self.set_time({"time": self.timeline()["time"]})
        return {"followed": {"target": name, "native": True, "frames": [start, end]},
                "timeline": self.timeline()}

    def _leave_path(self, rider, library) -> None:
        centre = list(self._cached_transform(rider)[0])
        slot = struct.unpack_from("<I", self._payload(rider), PATH_SLOT)[0]
        track = (PATH_TRACK, slot)
        self._write(rider, PATH_SLOT, struct.pack("<I", 0))
        self._flag(rider, _ATTRIBUTES, _ON_PATH, False)
        self._reindex()
        self._discard({track})
        self._ensure_track(rider, "Obj_ePosition", library)
        self._set_vector(rider, "Obj_ePosition", centre)
