"""Card data model and strict loading.

`Card.from_dict` accepts exactly the documents that `card.schema.json`
accepts, and raises `CardError` with a path to the offending value
otherwise.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


class CardError(ValueError):
    """The input is not a valid card."""


@dataclass
class Field:
    name: str
    dtype: str
    note: str = ""


@dataclass
class Access:
    """A way to open the data. `verified` means the snippet was executed."""

    goal: str
    snippet: str
    verified: bool = False


@dataclass
class Card:
    name: str
    identity: str = ""
    summary: str = ""
    layout: str = ""
    approx_bytes: int | None = None
    approx_items: int | None = None
    fields: list[Field] = field(default_factory=list)
    differs_from_upstream: list[str] = field(default_factory=list)
    gotchas: list[str] = field(default_factory=list)
    access: list[Access] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def compact_size(self) -> int:
        """UTF-8 bytes of the compact JSON form. This is what the budget measures."""
        text = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        return len(text.encode("utf-8"))

    @classmethod
    def from_dict(cls, d: Any) -> "Card":
        obj = _obj(d, "card")
        _no_extra(obj, _TOP_KEYS, "card")
        if "name" not in obj:
            raise CardError("card.name: required")
        name = _str(obj["name"], "card.name")
        if not name:
            raise CardError("card.name: must not be empty")
        return cls(
            name=name,
            identity=_str(obj.get("identity", ""), "card.identity"),
            summary=_str(obj.get("summary", ""), "card.summary"),
            layout=_str(obj.get("layout", ""), "card.layout"),
            approx_bytes=_count(obj.get("approx_bytes"), "card.approx_bytes"),
            approx_items=_count(obj.get("approx_items"), "card.approx_items"),
            fields=[
                _field(x, f"card.fields[{i}]")
                for i, x in enumerate(_list(obj.get("fields", []), "card.fields"))
            ],
            differs_from_upstream=_strs(
                obj.get("differs_from_upstream", []), "card.differs_from_upstream"
            ),
            gotchas=_strs(obj.get("gotchas", []), "card.gotchas"),
            access=[
                _access(x, f"card.access[{i}]")
                for i, x in enumerate(_list(obj.get("access", []), "card.access"))
            ],
            checked_at=_str(obj.get("checked_at", ""), "card.checked_at"),
        )

    @classmethod
    def from_json(cls, text: str) -> "Card":
        try:
            data = json.loads(text)
        except RecursionError:
            raise CardError("JSON is nested too deeply") from None
        except json.JSONDecodeError as e:
            raise CardError(f"invalid JSON: {e}") from None
        return cls.from_dict(data)


_TOP_KEYS = {
    "name", "identity", "summary", "layout", "approx_bytes", "approx_items",
    "fields", "differs_from_upstream", "gotchas", "access", "checked_at",
}


def _obj(v: Any, where: str) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise CardError(f"{where}: expected an object, got {_kind(v)}")
    return v


def _list(v: Any, where: str) -> list[Any]:
    if not isinstance(v, list):
        raise CardError(f"{where}: expected an array, got {_kind(v)}")
    return v


def _str(v: Any, where: str) -> str:
    if not isinstance(v, str):
        raise CardError(f"{where}: expected a string, got {_kind(v)}")
    return v


def _strs(v: Any, where: str) -> list[str]:
    return [_str(x, f"{where}[{i}]") for i, x in enumerate(_list(v, where))]


def _count(v: Any, where: str) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise CardError(f"{where}: expected an integer, got boolean")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if not isinstance(v, int):
        raise CardError(f"{where}: expected an integer, got {_kind(v)}")
    if v < 0:
        raise CardError(f"{where}: must not be negative")
    return v


def _no_extra(obj: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(obj) - allowed)
    if extra:
        raise CardError(f"{where}: unknown key {extra[0]!r}")


def _field(v: Any, where: str) -> Field:
    obj = _obj(v, where)
    _no_extra(obj, {"name", "dtype", "note"}, where)
    for k in ("name", "dtype"):
        if k not in obj:
            raise CardError(f"{where}.{k}: required")
    return Field(
        name=_str(obj["name"], f"{where}.name"),
        dtype=_str(obj["dtype"], f"{where}.dtype"),
        note=_str(obj.get("note", ""), f"{where}.note"),
    )


def _access(v: Any, where: str) -> Access:
    obj = _obj(v, where)
    _no_extra(obj, {"goal", "snippet", "verified"}, where)
    for k in ("goal", "snippet"):
        if k not in obj:
            raise CardError(f"{where}.{k}: required")
    verified = obj.get("verified", False)
    if not isinstance(verified, bool):
        raise CardError(f"{where}.verified: expected a boolean, got {_kind(verified)}")
    return Access(
        goal=_str(obj["goal"], f"{where}.goal"),
        snippet=_str(obj["snippet"], f"{where}.snippet"),
        verified=verified,
    )


def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__
