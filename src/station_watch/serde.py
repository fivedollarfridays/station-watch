"""Generic serialization for the frozen record dataclasses.

Records share three common fields (``ts``, ``run_id``, ``record_id``) and a
handful of typed fields (enums, nested dataclasses, tuples). Rather than write a
bespoke ``to_dict``/``from_dict`` per record, the :class:`Serde` mixin walks the
dataclass fields and their declared type hints so every record round-trips
through plain JSON-serializable structures.
"""

from __future__ import annotations

import json
import types
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _element_hint(hint: Any) -> Any:
    args = [a for a in get_args(hint) if a is not Ellipsis]
    return args[0] if args else Any


def _decode(hint: Any, value: Any) -> Any:
    origin = get_origin(hint)
    if origin in (Union, types.UnionType):
        if value is None:
            return None
        return _decode(_element_hint(hint), value)
    if origin in (list, tuple):
        decoded = [_decode(_element_hint(hint), item) for item in value]
        return tuple(decoded) if origin is tuple else decoded
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint(value)
    if isinstance(hint, type) and is_dataclass(hint):
        return hint.from_dict(value)
    return value


class Serde:
    """Mixin giving frozen dataclasses dict/JSON round-tripping."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _encode(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        hints = get_type_hints(cls)
        kwargs = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            kwargs[f.name] = _decode(hints[f.name], data[f.name])
        return cls(**kwargs)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, text: str):
        return cls.from_dict(json.loads(text))
