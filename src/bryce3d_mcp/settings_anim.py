"""Keys for the camera, the sun, the sky, materials and lamps.

Each setting already has its own track (see keyframes.py); a still scene simply
has one key in it. Bryce draws from copies kept in the owner's record, so posing
the scene at a time means working out every animated track and writing the copy.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Callable

from . import curve as curves
from . import keyframes
from .errors import BryceError

TARGETS = ("object", "camera", "sun", "sky", "material", "light")
# Where a track's number sits in its owner's record.
_MATERIAL_ORDER = (
    "Mat_eDiffuseC", "Mat_eAmbientC", "Mat_eSpecularC", "Mat_eSpecExp", "Mat_eTransC",
    "Mat_eDiffuse", "Mat_eAmbient", "Mat_eSpecular", "Mat_eTrans", "Mat_eReflect",
    "Mat_eBump", "Mat_eMetallic", "Mat_eHTDensity", "Mat_eMatFlags", "Mat_eVolC",
    "Mat_eRefract", "Mat_eHTEdge", "Mat_eHTSharp", "Mat_eHTQuality")
MATERIAL_SLOTS = {name: 900 + 4 * i for i, name in enumerate(_MATERIAL_ORDER)}
LIGHT_SLOTS = {"Light_C": 688, "Light_Int": 692}

# The fields a key may name, and the track ("channel") each belongs to.
CAMERA_FIELDS = {"position": "position", "heading": "orientation", "pitch": "orientation",
                 "bank": "orientation", "look_at": "orientation", "fov": "fov"}
SUN_FIELDS = {"azimuth": "direction", "altitude": "direction",
              "disc_color": "disc_color", "halo_color": "halo_color"}


@dataclass
class Channel:
    """One animatable setting."""

    name: str
    class_name: str
    owners: list                     # records that each hold a copy of the track
    offset: int                      # of the value, inside a one-key record
    form: str                        # "d" doubles or "f" floats
    number: int
    cache: Callable                  # (owner, numbers) -> write the copy Bryce draws
    show: Callable                   # numbers -> what the tools report
    slot: int | None = None          # where to link a track the owner lacks
    mirrors: tuple = ()              # records with no track that show the same value

    @property
    def key_offset(self) -> int:
        return self.offset - keyframes.HEAD


def hex_colour(values) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, v)) * 255):02x}" for v in values)


def _plain(values):
    return round(values[0], 4) + 0.0


class SettingAnimation:
    """The part of a scene that animates settings. Mixed into Br3."""

    # -- reading tracks ------------------------------------------------------------
    def _channel_record(self, channel: Channel, owner=None) -> bytes | None:
        track = self._linked(owner or channel.owners[0], channel.class_name)
        return None if track is None else self._payload(track)

    def _channel_entries(self, channel: Channel):
        record = self._channel_record(channel)
        if record is None:
            return None
        return keyframes.values(record, channel.number, channel.form, channel.key_offset)

    def _channel_value(self, channel: Channel, time: float):
        record = self._channel_record(channel)
        return keyframes.at(self._channel_entries(channel), time,
                            points=keyframes.time_curve(record))

    def _is_animated(self, channel: Channel) -> bool:
        record = self._channel_record(channel)
        return record is not None and keyframes.count(record) > 1

    def _refuse_animated_settings(self, channels, what: str) -> None:
        moving = [c.name for c in channels if self._is_animated(c)]
        if moving:
            raise BryceError(
                f"{what} is animated ({', '.join(moving)}), so a plain change would wipe "
                "out its motion. Use the keyframes operation; a single key makes a "
                "setting still again.")

    # -- posing ---------------------------------------------------------------------
    def _pose_channels(self, channels, time: float) -> None:
        for channel in channels:
            if not self._is_animated(channel):
                continue
            value = self._channel_value(channel, time)
            self._show_channel(channel, value)

    def _show_channel(self, channel: Channel, value) -> None:
        for owner in (*channel.owners, *channel.mirrors):
            channel.cache(owner, value)

    def _pose_settings(self, time: float) -> None:
        for _, _, channels in self._setting_groups():
            self._pose_channels(channels, time)

    # -- reporting ------------------------------------------------------------------
    def _channel_report(self, channel: Channel, fps: float) -> dict:
        record = self._channel_record(channel)
        found = {"keys": [{"frame": round(t * fps), "time": round(t, 4),
                           "value": channel.show(v)}
                          for t, v in self._channel_entries(channel)]}
        points = keyframes.time_curve(record)
        if points:
            found["curve"] = curves.describe(points)
        return found

    def animated_settings(self) -> dict:
        """Everything but object poses that has two keys or more."""
        found: dict = {}
        fps = None
        for target, name, channels in self._setting_groups():
            moving = [c for c in channels if self._is_animated(c)]
            if not moving:
                continue
            fps = fps or self.timeline()["fps"]
            report = {c.name: self._channel_report(c, fps) for c in moving}
            if name is None:
                found[target] = report
            else:
                found.setdefault(target + "s", {})[name] = report
        return found

    # -- writing --------------------------------------------------------------------
    def _write_channel(self, channel: Channel, entries, spec) -> None:
        """Give a channel the keys in `entries` (None keeps its keys) and the
        curve `spec` (None keeps its curve)."""
        for owner in channel.owners:
            track = self._linked(owner, channel.class_name)
            record = self._payload(track)
            if entries is not None:
                keys = [(0.0, entries[0][1])] if len(entries) == 1 else entries
                record = keyframes.build(record, keys, channel.form, channel.key_offset)
            if spec is not None and keyframes.count(record) > 1:
                record = keyframes.with_curve(record, curves.points(spec))
            self.file.replace(self._entry(track), record)

    def _seed_track(self, channel: Channel, library, value) -> None:
        """Make sure a channel has a track, holding the present value."""
        for owner in channel.owners:
            if self._linked(owner, channel.class_name) is not None:
                continue
            record = keyframes.fresh(channel.number, channel.form, channel.key_offset, value)
            if channel.slot is None:
                raise BryceError(f"there is no {channel.class_name} record to animate; "
                                 "use a template scene saved by Bryce")
            key = self._new_key(set())
            self._add_record(channel.class_name, key, record, bytes(48),
                             after=self._earlier_classes(channel.class_name, owner[0]))
            self._reindex()
            self._write(owner, channel.slot, struct.pack("<I", key))

    def _earlier_classes(self, class_name: str, owner_class: str) -> list[str]:
        """The record types a new track type must follow: Bryce lists a track's
        type before the type of whatever owns it."""
        names = list(self._classes)
        first_owner = next((i for i, n in enumerate(names) if n.endswith(("Light", "LightType"))),
                           names.index(owner_class))
        return names[:first_owner]


def unit_vector(azimuth: float, altitude: float):
    a, h = math.radians(azimuth), math.radians(altitude)
    return math.cos(h) * math.sin(a), math.sin(h), math.cos(h) * math.cos(a)


def sun_angles(vector):
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z) or 1.0
    return {"azimuth": round(math.degrees(math.atan2(x, z)) % 360, 2),
            "altitude": round(math.degrees(math.asin(max(-1.0, min(1.0, y / length)))), 2)}
