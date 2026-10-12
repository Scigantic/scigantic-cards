"""Draft a card from a folder or bucket.

`draft_card` measures what can be measured: file counts, sizes, layout,
extensions, tabular columns, and decoded file headers when the optional
`scigantic-headers` package is installed. It cannot know what the dataset is or
how it differs from the published version, so `identity` and the difference
lists are left for a person or an agent to fill in.

Reading is bounded: the listing stops at `max_files`, only regular files are
opened, hidden entries are skipped unless asked for, symlinks are not
followed, and raw bytes are embedded only for files a header decoder
recognizes and only when `embed_bytes` is set.
"""
from __future__ import annotations

import base64
import codecs
import csv
import io
import os
import stat
import time
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterator, Protocol

from .card import Access, Card, Field, Sample

_h: Any
try:  # optional
    import scigantic_headers as _h
except ImportError:  # pragma: no cover - exercised when the extra is absent
    _h = None

MAX_EMBED_BYTES = 4096
TEXT_PEEK = 65536
MAX_FIELDS_PER_FILE = 200
MAX_SAMPLE_FIELD_KEYS = 20
MAX_STRING = 200
TABULAR = {"csv": ",", "tsv": "\t"}


class DraftError(ValueError):
    """The location cannot be inspected."""


@dataclass
class Entry:
    path: str  # relative, "/" separated
    size: int


class Source(Protocol):
    root_label: str

    def walk(self, max_files: int, max_depth: int, include_hidden: bool,
             deadline: float) -> Iterator[Entry]: ...

    def read(self, entry: Entry, start: int, length: int) -> bytes: ...


# ---------------------------------------------------------------- local


class LocalSource:
    def __init__(self, root: str):
        self.root_label = root
        self.root = os.path.abspath(os.path.expanduser(root))
        if not os.path.exists(self.root):
            raise DraftError(f"{root}: no such file or directory")
        self.unreadable = 0
        self.too_deep = 0

    def walk(self, max_files, max_depth, include_hidden, deadline):
        if os.path.isfile(self.root):
            try:
                st = os.stat(self.root)
            except OSError as err:
                raise DraftError(f"{self.root_label}: {err}") from None
            yield Entry(os.path.basename(self.root), st.st_size)
            return
        stack = [(self.root, "", 0)]
        count = 0
        while stack:
            d, rel, depth = stack.pop()
            try:
                with os.scandir(d) as it:
                    items = sorted(it, key=lambda e: e.name)
            except OSError:
                self.unreadable += 1
                continue
            subdirs = []
            for e in items:
                if time.monotonic() > deadline:
                    return
                if not include_hidden and e.name.startswith("."):
                    continue
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if depth + 1 <= max_depth:
                            subdirs.append((e.path, f"{rel}{e.name}/", depth + 1))
                        else:
                            self.too_deep += 1
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    self.unreadable += 1
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                yield Entry(f"{rel}{e.name}", st.st_size)
                count += 1
                if count >= max_files:
                    return
            stack.extend(reversed(subdirs))

    def read(self, entry, start, length):
        path = self.root if os.path.isfile(self.root) else os.path.join(self.root, *entry.path.split("/"))
        with open(path, "rb") as fh:
            if start:
                fh.seek(start)
            return fh.read(length)


# ---------------------------------------------------------------- fsspec


class FsspecSource:
    """Any fsspec URL, such as s3://bucket/prefix, gs://bucket, memory://x."""

    def __init__(self, url: str, **storage_options: Any):
        try:
            import fsspec  # type: ignore[import-untyped]
        except ImportError:
            raise DraftError(
                "remote locations need fsspec: pip install 'scigantic-cards[remote]' "
                "plus s3fs or gcsfs for S3 or GCS"
            ) from None
        self.root_label = url
        scheme = url.split("://", 1)[0]
        if scheme in ("http", "https"):
            raise DraftError("http(s) locations cannot be listed; point at a bucket or folder")
        opts = dict(storage_options)
        if scheme == "s3":
            opts.setdefault("anon", True)
        elif scheme in ("gs", "gcs"):
            opts.setdefault("token", "anon")
        try:
            self.fs, self.root = fsspec.core.url_to_fs(url, **opts)
        except (ImportError, ValueError) as e:
            raise DraftError(f"{url}: {e}") from None
        self.root = self.root.rstrip("/")
        self.listing_error = ""
        self.too_deep = 0

    def _ls(self, path):
        return self.fs.ls(path, detail=True)

    def walk(self, max_files, max_depth, include_hidden, deadline):
        stack = [(self.root, 0)]
        count = 0
        prefix = self.root + "/"
        first = True
        while stack:
            d, depth = stack.pop()
            try:
                items = sorted(self._ls(d), key=lambda x: x["name"])
            except FileNotFoundError:
                if first:
                    raise DraftError(f"{self.root_label}: not found") from None
                continue
            except (OSError, PermissionError) as e:
                if first:
                    raise DraftError(f"{self.root_label}: {e}") from None
                self.listing_error = str(e)
                continue
            first = False
            subdirs = []
            for it in items:
                if time.monotonic() > deadline:
                    return
                name = it["name"].rstrip("/")
                base = name.rsplit("/", 1)[-1]
                if not include_hidden and base.startswith("."):
                    continue
                if it.get("type") == "directory":
                    if name != d.rstrip("/"):
                        if depth + 1 <= max_depth:
                            subdirs.append((name, depth + 1))
                        else:
                            self.too_deep += 1
                    continue
                if it.get("type") != "file":
                    continue
                rel = name[len(prefix):] if name.startswith(prefix) else base
                yield Entry(rel, int(it.get("size") or 0))
                count += 1
                if count >= max_files:
                    return
            stack.extend(reversed(subdirs))

    def read(self, entry, start, length):
        path = f"{self.root}/{entry.path}"
        with self.fs.open(path, "rb") as fh:
            if start:
                fh.seek(start)
            return fh.read(length)


def open_source(location: str, **storage_options: Any) -> Source:
    if "://" in location:
        return FsspecSource(location, **storage_options)
    return LocalSource(location)


# ---------------------------------------------------------------- decoding


def _ext(path: str) -> str:
    name = path.lower()
    if name.endswith(".gz"):
        name = name[:-3]
    return name.rsplit(".", 1)[-1] if "." in name.rsplit("/", 1)[-1] else ""


def _suffix(path: str) -> str:
    """Extension including a .gz wrapper, without the dot: 'tsv.gz', 'csv', ''."""
    base = path.rsplit("/", 1)[-1].lower()
    parts = base.split(".")
    if len(parts) < 2:
        return ""
    if parts[-1] == "gz" and len(parts) > 2:
        return ".".join(parts[-2:])
    return parts[-1]


def _gunzip_head(raw: bytes, limit: int) -> bytes:
    try:
        return zlib.decompressobj(31).decompress(raw, limit)
    except zlib.error:
        return b""


def _safe_value(v: Any, depth: int = 0) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, str):
        return v[:MAX_STRING]
    if depth >= 3:
        return None
    if isinstance(v, (list, tuple)):
        return [_safe_value(x, depth + 1) for x in list(v)[:20]]
    if isinstance(v, dict):
        return {str(k)[:MAX_STRING]: _safe_value(x, depth + 1) for k, x in list(v.items())[:MAX_SAMPLE_FIELD_KEYS]}
    return str(v)[:MAX_STRING]


def decode_header(source: Source, entry: Entry) -> Any:
    """Decoded header from scigantic-headers, or None."""
    if _h is None or not _h.is_decodable(entry.path):
        return None
    gz = entry.path.lower().endswith(".gz")
    name = entry.path[:-3] if gz else entry.path
    try:
        rd = _h.read_for(name)
        if rd.footer:
            start = max(entry.size - rd.footer, 0)
            data = source.read(entry, start, rd.footer)
        elif gz:
            data = _gunzip_head(source.read(entry, 0, max(rd.leading * 4, TEXT_PEEK)), rd.leading)
        else:
            data = source.read(entry, 0, rd.leading)
        return _h.decode_bytes(name, data)
    except Exception:
        return None


def _infer_dtype(values: list[str]) -> str:
    vals = [v for v in values if v != ""]
    if not vals:
        return "string"
    for caster, name in ((int, "int"), (float, "float")):
        try:
            for v in vals:
                caster(v)
            return name
        except ValueError:
            continue
    return "string"


def tabular_fields(source: Source, entry: Entry) -> list[Field]:
    """Column names and inferred types from the first rows of a CSV or TSV."""
    ext = _ext(entry.path)
    gz = entry.path.lower().endswith(".gz")
    try:
        raw = source.read(entry, 0, TEXT_PEEK)
        if gz:
            raw = _gunzip_head(raw, TEXT_PEEK * 4)
        text = codecs.decode(raw, "utf-8-sig", "ignore")
    except Exception:
        return []
    if "\x00" in text:
        return []
    lines = text.splitlines()[:51]
    if len(lines) < 1:
        return []
    try:
        rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=TABULAR[ext]))
    except csv.Error:
        return []
    if not rows or not any(c.strip() for c in rows[0]):
        return []
    header, body = rows[0], rows[1:]
    out = []
    for i, name in enumerate(header[:MAX_FIELDS_PER_FILE]):
        col = [r[i] for r in body if i < len(r)]
        out.append(Field(name=name.strip()[:MAX_STRING], dtype=_infer_dtype(col),
                         note="inferred from the first rows", group=entry.path))
    return out


# ---------------------------------------------------------------- the draft


def _human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if x < 1024 or unit == "PiB":
            return f"{int(x)} B" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{n} B"


def draft_card(
    location: str,
    *,
    name: str | None = None,
    max_files: int = 5000,
    max_depth: int = 16,
    max_samples: int = 6,
    embed_bytes: int = 0,
    include_hidden: bool = False,
    time_limit: float = 60.0,
    source: Source | None = None,
    **storage_options: Any,
) -> Card:
    if max_files < 1 or max_depth < 0 or max_samples < 0:
        raise DraftError("max_files must be >= 1; max_depth and max_samples must be >= 0")
    if not 0 <= embed_bytes <= MAX_EMBED_BYTES:
        raise DraftError(f"embed_bytes must be between 0 and {MAX_EMBED_BYTES}")
    src = source or open_source(location, **storage_options)
    deadline = time.monotonic() + time_limit

    entries = list(src.walk(max_files + 1, max_depth, include_hidden, deadline))
    too_deep = getattr(src, "too_deep", 0)
    stopped_early = len(entries) > max_files or time.monotonic() > deadline or too_deep > 0
    entries = entries[:max_files]

    total = sum(e.size for e in entries)
    exts = Counter(_ext(e.path) or "(none)" for e in entries)
    tops = Counter(e.path.split("/", 1)[0] + "/" for e in entries if "/" in e.path)

    parts = [f"{len(entries):,} files, {_human(total)}"]
    if too_deep:
        parts[0] += (f" (listing stopped early; {too_deep:,} {'folder' if too_deep == 1 else 'folders'} deeper than {max_depth} levels "
                     "{'was' if too_deep == 1 else 'were'} not listed, so counts and sizes are lower bounds).")
    else:
        parts[0] += " (listing stopped early; counts and sizes are lower bounds)." if stopped_early else "."
    if tops:
        shown = ", ".join(f"{d} ({n:,})" for d, n in sorted(tops.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
        more = f" and {len(tops) - 8} more" if len(tops) > 8 else ""
        parts.append(f"Top-level folders: {shown}{more}.")
    if exts:
        parts.append("Extensions: " + ", ".join(
            f".{x} ({n:,})" for x, n in sorted(exts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]) + ".")
    unreadable = getattr(src, "unreadable", 0)
    if unreadable:
        parts.append(f"{unreadable:,} entries could not be read.")

    samples: list[Sample] = []
    fields: list[Field] = []
    first_decoded: Entry | None = None
    seen_schemas: dict[tuple[tuple[str, str], ...], str] = {}
    by_kind: dict[str, list[Entry]] = {}
    for e in entries:
        by_kind.setdefault(_ext(e.path) or "(none)", []).append(e)
    # one sample per extension first, then fill in, ordered by how common the extension is
    order = [k for k, _ in sorted(exts.items(), key=lambda kv: (-kv[1], kv[0]))]
    picks: list[Entry] = []
    for rank in range(max_samples):
        for k in order:
            if len(picks) >= max_samples:
                break
            if rank < len(by_kind[k]):
                picks.append(by_kind[k][rank])
        if len(picks) >= max_samples:
            break
    for e in picks:
        hdr = decode_header(src, e)
        s = Sample(path=e.path, size=e.size)
        if hdr is not None:
            if first_decoded is None:
                first_decoded = e
            s.format = str(hdr.format)
            s.summary = str(hdr.summary)[:MAX_STRING * 2]
            s.fields = _safe_value(dict(hdr.fields))
            if embed_bytes:
                try:
                    s.head_b64 = base64.b64encode(src.read(e, 0, embed_bytes)).decode("ascii")
                except Exception:
                    pass
        elif _ext(e.path) in TABULAR:
            cols = tabular_fields(src, e)
            if cols:
                s.format = _ext(e.path)
                s.summary = f"{len(cols)} columns"
                _add_columns(fields, seen_schemas, s, cols)
        samples.append(s)
    for s, e in zip(samples, picks):
        if s.format and s.format not in ("csv", "tsv"):
            _add_columns(fields, seen_schemas, s, _fields_from_header_fields(s, e))

    suffixes = Counter(_suffix(e.path) for e in entries)
    dominant = min(suffixes, key=lambda k: (-suffixes[k], k)) if suffixes else ""
    access = _access_snippets(
        location, dominant, _suffix(first_decoded.path) if first_decoded else "",
    )
    card = Card(
        name=name or _default_name(location),
        layout=" ".join(parts),
        location=location,
        approx_bytes=None if stopped_early else total,
        approx_items=None if stopped_early else len(entries),
        fields=fields,
        access=access,
        samples=samples,
    )
    return card


def _add_columns(fields: list[Field], seen: dict[tuple[tuple[str, str], ...], str],
                 sample: Sample, cols: list[Field]) -> None:
    """Add a file's columns unless an earlier sample already had exactly these."""
    if not cols:
        return
    key = tuple((c.name, c.dtype) for c in cols)
    if key in seen:
        sample.summary += f"; same columns as {seen[key]}"
        return
    seen[key] = sample.path
    fields.extend(cols)


def _fields_from_header_fields(s: Sample, e: Entry) -> list[Field]:
    """Columns that a decoder reported, such as the Parquet schema."""
    f = s.fields
    cols = f.get("columns") if isinstance(f, dict) else None
    out: list[Field] = []
    if isinstance(cols, list):
        for c in cols[:MAX_FIELDS_PER_FILE]:
            if isinstance(c, dict) and "name" in c:
                out.append(Field(str(c["name"])[:MAX_STRING], str(c.get("type", c.get("dtype", "")))[:MAX_STRING],
                                 "from the file header", e.path))
            elif isinstance(c, str):
                out.append(Field(c[:MAX_STRING], "unknown", "from the file header", e.path))
    return out


def _default_name(location: str) -> str:
    tail = location.rstrip("/").rsplit("/", 1)[-1] or location.rstrip("/") or "dataset"
    return tail.lstrip("~") or "dataset"


def _glob_snippet(location: str, suffix: str) -> str:
    pattern = f"*.{suffix}" if suffix else "*"
    if "://" in location:
        scheme, rest = location.split("://", 1)
        proto = "gcs" if scheme in ("gs", "gcs") else scheme
        opts = ", anon=True" if proto == "s3" else ", token='anon'" if proto == "gcs" else ""
        return (
            "import fsspec\n"
            f"fs = fsspec.filesystem({proto!r}{opts})\n"
            f"files = fs.glob({rest.rstrip('/') + '/**/' + pattern!r})\n"
            "assert files, 'no files matched'\n"
            "print(len(files), files[:3])"
        )
    return (
        "import glob, os\n"
        f"root = os.path.expanduser({location!r})\n"
        f"files = sorted(glob.glob(os.path.join(root, '**', {pattern!r}), recursive=True))\n"
        "assert files, 'no files matched'\n"
        "print(len(files), files[:3])"
    )


def _access_snippets(location: str, dominant: str, decoded_suffix: str) -> list[Access]:
    out = [Access("List files", _glob_snippet(location, dominant), False)]
    if decoded_suffix and _h is not None and "://" not in location:
        out.append(Access(
            "Read one file header",
            _glob_snippet(location, decoded_suffix)
            + "\nfrom scigantic_headers import decode_file\n"
            "hdr = decode_file(files[0])\nprint(hdr.summary if hdr else 'no decoder for this file')",
            False,
        ))
    return out
