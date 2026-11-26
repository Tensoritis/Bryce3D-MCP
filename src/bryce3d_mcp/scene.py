"""Declarative scene building: one JSON description in, many UI steps out."""

from __future__ import annotations

import math
from typing import Any

from . import layout
from .controller import BryceController, validate_attributes
from .errors import BryceError

MAX_OBJECTS = 300
_KEYS = {"type", "name", "position", "size", "rotation", "boolean", "material", "on_ground", "repeat"}


def _vector(value: Any, label: str) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in value)):
        raise BryceError(f"{label} must be a list of 3 finite numbers")
    return [float(v) for v in value]


def _material(value: Any, label: str) -> dict | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value.lower() not in layout.SIMPLE_COLORS:
            raise BryceError(f"{label}: unknown color {value!r}; "
                             f"use one of {', '.join(layout.SIMPLE_COLORS)}")
        row, column = layout.SIMPLE_COLORS[value.lower()]
        return {"category": "simple&fast", "row": row, "column": column}
    if isinstance(value, dict) and set(value) <= {"category", "row", "column"}:
        spec = {"category": "simple&fast", "row": 0, "column": 0, **value}
        if str(spec["category"]).lower() not in layout.MATERIAL_CATEGORIES:
            raise BryceError(f"{label}: unknown material category {spec['category']!r}")
        try:
            layout.library_cell(spec["row"], spec["column"])
        except (ValueError, TypeError) as exc:
            raise BryceError(f"{label}: {exc}") from None
        return spec
    raise BryceError(f"{label}: material is a color name or {{category,row,column}}")


def expand(objects: list[dict]) -> list[dict]:
    """Validate everything and expand `repeat`, before the UI is touched.

    repeat: {"count": n, "offset": [x,y,z]} makes a straight line of copies;
            {"count": n, "radius": r} makes a ring around `position` in the XZ plane.
    """
    if not isinstance(objects, list) or not objects:
        raise BryceError("objects must be a non-empty list")
    steps: list[dict] = []
    for index, obj in enumerate(objects):
        label = f"objects[{index}]"
        if not isinstance(obj, dict) or set(obj) - _KEYS:
            raise BryceError(f"{label}: allowed keys are {', '.join(sorted(_KEYS))}")
        kind = obj.get("type")
        if kind not in layout.PRIMITIVES:
            raise BryceError(f"{label}: unknown type {kind!r}")
        size = _vector(obj["size"], f"{label}.size") if "size" in obj else None
        rotation = _vector(obj["rotation"], f"{label}.rotation") if "rotation" in obj else None
        position = _vector(obj["position"], f"{label}.position") if "position" in obj else None
        if obj.get("on_ground"):
            if size is None or position is None:
                raise BryceError(f"{label}: on_ground needs both size and position")
            if rotation and (rotation[0] or rotation[2]):
                raise BryceError(f"{label}: on_ground cannot be used with X or Z rotation")
            position = [position[0], position[1] + size[1] / 2, position[2]]

        positions = [position]
        repeat = obj.get("repeat")
        if repeat is not None:
            if position is None or not isinstance(repeat, dict):
                raise BryceError(f"{label}: repeat needs a position and an object")
            count = repeat.get("count")
            if not isinstance(count, int) or not 1 <= count <= MAX_OBJECTS:
                raise BryceError(f"{label}: repeat.count must be 1-{MAX_OBJECTS}")
            if set(repeat) == {"count", "offset"}:
                offset = _vector(repeat["offset"], f"{label}.repeat.offset")
                positions = [[p + o * i for p, o in zip(position, offset)] for i in range(count)]
            elif set(repeat) == {"count", "radius"}:
                radius = repeat["radius"]
                if not isinstance(radius, (int, float)) or not 0 < radius <= 100000:
                    raise BryceError(f"{label}: repeat.radius must be a positive number")
                positions = [[position[0] + radius * math.cos(math.tau * i / count), position[1],
                              position[2] + radius * math.sin(math.tau * i / count)]
                             for i in range(count)]
            else:
                raise BryceError(f"{label}: repeat is {{count, offset}} or {{count, radius}}")

        material = _material(obj.get("material"), label)
        for copy, pos in enumerate(positions):
            name = obj.get("name")
            if name is not None and len(positions) > 1:
                name = f"{name} {copy + 1}"
            step = {"type": kind, "name": name, "position": pos, "size": size,
                    "rotation": rotation, "boolean": obj.get("boolean"), "material": material}
            validate_attributes(name, pos, rotation, size, step["boolean"])
            steps.append(step)
        if len(steps) > MAX_OBJECTS:
            raise BryceError(f"scene is limited to {MAX_OBJECTS} objects per call")
    return steps


def build(controller: BryceController, steps: list[dict], start_at: int = 0) -> dict:
    """Run expanded steps in order, stopping at the first failure."""
    if not 0 <= start_at < len(steps):
        raise BryceError(f"start_at must be between 0 and {len(steps) - 1}")
    for index in range(start_at, len(steps)):
        step, stage = steps[index], "create"
        try:
            controller.create(step["type"])
            attributes = {k: step[k] for k in ("name", "position", "size", "rotation", "boolean")
                          if step[k] is not None}
            if attributes:
                stage = "attributes"
                controller.set_attributes(**attributes)
            if step["material"]:
                stage = "material"
                controller.apply_material(**step["material"])
        except BryceError as exc:
            return {"completed": False, "built": index, "total": len(steps), "failed_index": index,
                    "failed_stage": stage, "error": str(exc),
                    "next": "Inspect with a screenshot. Remove or finish the partial object, "
                            "then resume with start_at."}
    return {"completed": True, "built": len(steps), "total": len(steps)}
