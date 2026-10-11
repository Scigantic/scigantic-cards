"""Card data model and strict loading.

`Card.from_dict` accepts exactly the documents that `card.schema.json`
accepts, and raises `CardError` with a path to the offending value
otherwise.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any


class CardError(ValueError):
    """The input is not a valid card."""


@dataclass
class Field:
    """A column or variable. `group` names the table or file it belongs to."""

    name: str
    dtype: str
    note: str = ""
    group: str = ""


@dataclass
class Access:
    """A way to open the data. `verified` means the snippet was executed."""

    goal: str
    snippet: str
    verified: bool = False
    language: str = "python"


@dataclass
class Sample:
    """One example file. `fields` holds decoded header values; `head_b64` is
    the first bytes of the file, base64 encoded, and is optional."""

    path: str
    size: int | None = None
    format: str = ""
    summary: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    head_b64: str = ""


@dataclass
class Card:
    name: str
    identity: str = ""
    summary: str = ""
    layout: str = ""
    location: str = ""
    approx_bytes: int | None = None
    approx_items: int | None = None
    fields: list[Field] = field(default_factory=list)
    differs_from_upstream: list[str] = field(default_factory=list)
    gotchas: list[str] = field(default_factory=list)
    access: list[Access] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, allow_nan=False)

    def to_b64(self, compress: bool = False) -> str:
        """URL-safe base64 of the compact JSON, for env vars, headers and URLs.

        With `compress=True` the JSON is deflated first and the result starts
        with "z:". `from_b64` accepts both forms.
        """
        raw = json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if compress:
            return "z:" + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @classmethod
    def from_b64(cls, text: str) -> "Card":
        text = "".join(text.split())
        compressed = text.startswith("z:")
        if compressed:
            text = text[2:]
        if len(text) > MAX_B64_CHARS:
            raise CardError(f"encoded card is larger than {MAX_B64_CHARS} characters")
        try:
            raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
            if compressed:
                d = zlib.decompressobj()
                raw = d.decompress(raw, MAX_DECODED_BYTES + 1)
                if d.unconsumed_tail or d.decompress(b"", 1):
                    raise CardError(f"decoded card is larger than {MAX_DECODED_BYTES} bytes")
            if len(raw) > MAX_DECODED_BYTES:
                raise CardError(f"decoded card is larger than {MAX_DECODED_BYTES} bytes")
            return cls.from_json(raw.decode("utf-8"))
        except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError) as e:
            if isinstance(e, CardError):
                raise
            raise CardError(f"not a valid encoded card: {e}") from None

    def compact_size(self) -> int:
        """UTF-8 bytes of the compact JSON form. This is what the budget measures."""
        text = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
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
            location=_str(obj.get("location", ""), "card.location"),
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
            samples=[
                _sample(x, f"card.samples[{i}]")
                for i, x in enumerate(_list(obj.get("samples", []), "card.samples"))
            ],
            checked_at=_str(obj.get("checked_at", ""), "card.checked_at"),
        )

    @classmethod
    def from_json(cls, text: str) -> "Card":
        try:
            data = json.loads(text, parse_constant=_reject_constant)
        except RecursionError:
            raise CardError("JSON is nested too deeply") from None
        except json.JSONDecodeError as e:
            raise CardError(f"invalid JSON: {e}") from None
        return cls.from_dict(data)


_TOP_KEYS = {
    "name", "identity", "summary", "layout", "location", "approx_bytes", "approx_items",
    "fields", "differs_from_upstream", "gotchas", "access", "samples", "checked_at",
}

MAX_B64_CHARS = 8_000_000
MAX_DECODED_BYTES = 5_000_000

# Standard base64 with padding, anchored without `$` so Python and JSON Schema
# (ECMAScript) regex engines treat a trailing newline the same way.
HEAD_B64_PATTERN = r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?(?![\s\S])"
HEAD_B64_MAX_CHARS = 6000
_HEAD_B64 = re.compile(HEAD_B64_PATTERN)


def _reject_constant(name: str) -> Any:
    raise CardError(f"{name} is not valid JSON")


def _check_finite(v: Any, where: str) -> None:
    stack = [v]
    while stack:
        x = stack.pop()
        if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
            raise CardError(f"{where}: NaN and Infinity are not allowed")
        elif isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)


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
    _no_extra(obj, {"name", "dtype", "note", "group"}, where)
    for k in ("name", "dtype"):
        if k not in obj:
            raise CardError(f"{where}.{k}: required")
    return Field(
        name=_str(obj["name"], f"{where}.name"),
        dtype=_str(obj["dtype"], f"{where}.dtype"),
        note=_str(obj.get("note", ""), f"{where}.note"),
        group=_str(obj.get("group", ""), f"{where}.group"),
    )


def _access(v: Any, where: str) -> Access:
    obj = _obj(v, where)
    _no_extra(obj, {"goal", "snippet", "verified", "language"}, where)
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
        language=_str(obj.get("language", "python"), f"{where}.language"),
    )


def _finite_obj(v: Any, where: str) -> dict[str, Any]:
    d = dict(_obj(v, where))
    _check_finite(d, where)
    return d


def _sample(v: Any, where: str) -> Sample:
    obj = _obj(v, where)
    _no_extra(obj, {"path", "size", "format", "summary", "fields", "head_b64"}, where)
    if "path" not in obj:
        raise CardError(f"{where}.path: required")
    head = _str(obj.get("head_b64", ""), f"{where}.head_b64")
    if len(head) > HEAD_B64_MAX_CHARS or not _HEAD_B64.search(head):
        raise CardError(f"{where}.head_b64: not base64 or longer than {HEAD_B64_MAX_CHARS} characters")
    return Sample(
        path=_str(obj["path"], f"{where}.path"),
        size=_count(obj.get("size"), f"{where}.size"),
        format=_str(obj.get("format", ""), f"{where}.format"),
        summary=_str(obj.get("summary", ""), f"{where}.summary"),
        fields=_finite_obj(obj.get("fields", {}), f"{where}.fields"),
        head_b64=head,
    )


def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__
