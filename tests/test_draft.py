import gzip
import json
import os
import struct
import sys

import pytest

from scigantic_cards import Card, DraftError, draft_card, lint
from scigantic_cards import draft as draft_mod
from scigantic_cards.draft import FsspecSource, LocalSource, _suffix

headers = pytest.importorskip("scigantic_headers")

FASTQ = b"@A00123:45:HXXXXDSXX:1:1101:1000:1000 1:N:0:ACGT\nACGTACGTAC\n+\nIIIIIIIIII\n"
VCF = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"


def npy(shape=(4, 3)):
    hdr = f"{{'descr': '<f4', 'fortran_order': False, 'shape': {shape}, }}"
    hdr += " " * (64 - ((10 + len(hdr) + 1) % 64)) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(hdr)) + hdr.encode() + b"\0" * 48


def write(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, "runs/r1/results.csv", "sample,count,score\nA,1,0.5\nB,2,0.7\n")
    write(tmp_path, "runs/r2/results.tsv.gz", gzip.compress(b"id\tname\n1\ta\n"))
    write(tmp_path, "runs/r1/reads.fastq", FASTQ)
    write(tmp_path, "runs/r2/calls.vcf", VCF)
    write(tmp_path, "arrays/a.npy", npy())
    return tmp_path


def test_counts_sizes_and_layout(tree):
    c = draft_card(str(tree))
    assert c.approx_items == 5
    assert c.approx_bytes == sum(p.stat().st_size for p in tree.rglob("*") if p.is_file())
    assert "5 files" in c.layout and "runs/ (4)" in c.layout and "arrays/ (1)" in c.layout
    assert c.location == str(tree)


def test_identity_and_delta_are_left_for_a_human(tree):
    rules = {i.rule for i in lint(draft_card(str(tree)))}
    assert {"identity", "delta"} <= rules


def test_tabular_columns_with_inferred_types_and_groups(tree):
    c = draft_card(str(tree))
    by = {(f.group, f.name): f.dtype for f in c.fields}
    assert by[("runs/r1/results.csv", "count")] == "int"
    assert by[("runs/r1/results.csv", "score")] == "float"
    assert by[("runs/r1/results.csv", "sample")] == "string"
    assert ("runs/r2/results.tsv.gz", "name") in by
    assert "field-duplicate" not in {i.rule for i in lint(c)}


def test_headers_are_decoded_into_samples(tree):
    c = draft_card(str(tree))
    by = {s.path: s for s in c.samples}
    assert by["runs/r1/reads.fastq"].format == "fastq"
    assert by["runs/r1/reads.fastq"].fields["instrument"] == "A00123"
    assert by["arrays/a.npy"].fields["shape"] == [4, 3]
    assert by["runs/r2/calls.vcf"].fields["version"] == "VCFv4.2"


def test_no_bytes_embedded_by_default(tree):
    assert all(s.head_b64 == "" for s in draft_card(str(tree)).samples)


def test_embedded_bytes_only_for_decodable_files(tree):
    write(tree, "secrets.env", "TOKEN=hunter2\n")
    write(tree, "notes.txt", "private notes\n")
    c = draft_card(str(tree), embed_bytes=16, max_samples=50)
    for s in c.samples:
        if s.head_b64:
            raw = __import__("base64").b64decode(s.head_b64)
            assert len(raw) <= 16 and s.format in ("fastq", "vcf", "npy")
    paths = {s.path for s in c.samples}
    assert {"secrets.env", "notes.txt"} <= paths  # listed, but never read for bytes
    blob = c.to_json()
    assert "hunter2" not in blob and "private notes" not in blob
    assert __import__("base64").b64encode(b"TOKEN").decode()[:6] not in blob


def test_embed_bytes_bounds():
    with pytest.raises(DraftError):
        draft_card(".", embed_bytes=-1)
    with pytest.raises(DraftError):
        draft_card(".", embed_bytes=10_000)


def test_hidden_skipped_unless_asked(tmp_path):
    write(tmp_path, "a.csv", "x\n1\n")
    write(tmp_path, ".git/config", "secret")
    write(tmp_path, ".hidden.csv", "x\n1\n")
    assert draft_card(str(tmp_path)).approx_items == 1
    assert draft_card(str(tmp_path), include_hidden=True).approx_items == 3


@pytest.mark.skipif(os.name != "posix", reason="symlinks and FIFOs")
def test_symlinks_and_fifos_are_not_followed_or_opened(tmp_path):
    write(tmp_path, "real.csv", "x\n1\n")
    (tmp_path / "link.csv").symlink_to(tmp_path / "real.csv")
    (tmp_path / "dirlink").symlink_to(tmp_path)  # would loop if followed
    (tmp_path / "broken").symlink_to(tmp_path / "missing")
    os.mkfifo(tmp_path / "pipe.csv")  # opening this would block forever
    c = draft_card(str(tmp_path), time_limit=20)
    assert c.approx_items == 1
    assert [s.path for s in c.samples] == ["real.csv"]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root posix user")
def test_unreadable_directory_is_counted_not_fatal(tmp_path):
    write(tmp_path, "ok.csv", "x\n1\n")
    locked = tmp_path / "locked"
    locked.mkdir()
    write(locked, "in.csv", "x\n1\n")
    locked.chmod(0)
    try:
        c = draft_card(str(tmp_path))
    finally:
        locked.chmod(0o755)
    assert c.approx_items == 1 and "could not be read" in c.layout


def test_truncation_is_reported_and_sizes_are_not_claimed(tmp_path):
    for i in range(30):
        write(tmp_path, f"f{i:02d}.csv", "x\n1\n")
    c = draft_card(str(tmp_path), max_files=10)
    assert "lower bounds" in c.layout and "10 files" in c.layout
    assert c.approx_bytes is None and c.approx_items is None
    exact = draft_card(str(tmp_path), max_files=30)  # exactly at the cap is not truncated
    assert exact.approx_items == 30 and "lower bounds" not in exact.layout


def test_max_depth(tmp_path):
    write(tmp_path, "a/b/c/d.csv", "x\n1\n")
    write(tmp_path, "top.csv", "x\n1\n")
    assert draft_card(str(tmp_path), max_depth=1).approx_items == 1
    assert draft_card(str(tmp_path), max_depth=3).approx_items == 2


def test_time_limit_marks_listing_incomplete(tmp_path):
    for i in range(50):
        write(tmp_path, f"f{i}.csv", "x\n1\n")
    c = draft_card(str(tmp_path), time_limit=-1)
    assert "lower bounds" in c.layout and c.approx_items is None


def test_empty_dir_and_single_file(tmp_path):
    c = draft_card(str(tmp_path))
    assert c.approx_items == 0 and c.samples == [] and c.fields == []
    f = write(tmp_path, "one.fastq", FASTQ)
    c = draft_card(str(f))
    assert c.approx_items == 1 and c.samples[0].format == "fastq"


def test_missing_location():
    with pytest.raises(DraftError):
        draft_card("/definitely/not/here")


def test_tilde_expansion(tmp_path, monkeypatch):
    write(tmp_path, "data/a.csv", "x,y\n1,2\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    c = draft_card("~/data")
    assert c.approx_items == 1 and c.location == "~/data" and c.name == "data"
    snippet = c.access[0].snippet
    assert "expanduser('~/data')" in snippet
    assert "tilde-path" not in {i.rule for i in lint(c)}


@pytest.mark.parametrize("name", ["café ü.csv", "日本語.csv", "a b.csv", "emoji🙂.csv", "new\nline.csv"])
def test_awkward_filenames(tmp_path, name):
    write(tmp_path, name, "x,y\n1,2\n")
    c = draft_card(str(tmp_path))
    assert c.approx_items == 1
    assert Card.from_json(c.to_json()) == c


@pytest.mark.parametrize("body", [
    b"", b"\n", b"\x00\x01\x02binary", b"\xff\xfe\xfd", b"a,b\n1", b",,,\n,,,\n",
    b'"unterminated,quote\n1,2\n', ("x," * 5000).encode() + b"\n1\n", b"\xef\xbb\xbfid,v\n1,2\n",
])
def test_garbage_in_tabular_files_never_crashes(tmp_path, body):
    write(tmp_path, "bad.csv", body)
    c = draft_card(str(tmp_path))
    assert Card.from_json(c.to_json()) == c
    assert len(c.fields) <= 200


def test_corrupt_gzip_and_corrupt_headers_never_crash(tmp_path):
    write(tmp_path, "bad.csv.gz", b"\x1f\x8b not really gzip")
    write(tmp_path, "bad.npy", b"\x93NUMPY\x01\x00\xff\xff")
    write(tmp_path, "bad.fastq", b"@only a header\n")
    write(tmp_path, "empty.vcf", b"")
    write(tmp_path, "trunc.fastq.gz", gzip.compress(FASTQ)[:10])
    c = draft_card(str(tmp_path), max_samples=20)
    assert Card.from_json(c.to_json()) == c
    assert c.approx_items == 5


def test_sample_fields_are_bounded(tmp_path, monkeypatch):
    class H:
        format, summary = "big", "s" * 5000
        fields = {f"k{i}": "v" * 5000 for i in range(100)} | {"nan": float("nan"), "n": {"a": {"b": {"c": {"d": 1}}}}}
    monkeypatch.setattr(draft_mod, "decode_header", lambda s, e: H())
    write(tmp_path, "x.dat", b"1")
    c = draft_card(str(tmp_path))
    s = c.samples[0]
    assert len(s.fields) <= 20 and all(len(str(v)) <= 200 for v in s.fields.values() if isinstance(v, str))
    assert len(s.summary) <= 400
    Card.from_json(c.to_json())  # no NaN, valid


def test_suffix_helper():
    assert [_suffix(p) for p in ("a.csv", "a.tsv.gz", "a.GZ", "a", "d/a.b.c", ".hid", "x.fastq.gz")] == \
        ["csv", "tsv.gz", "gz", "", "c", "hid", "fastq.gz"]


def test_generated_snippets_run_and_find_files(tree):
    from scigantic_cards import verify_card
    c = draft_card(str(tree))
    assert any("scigantic_headers" in a.snippet for a in c.access)
    updated, results = verify_card(c, timeout=60)
    assert [r.status for r in results] == ["passed"] * len(c.access), results
    assert all(a.verified for a in updated.access) and updated.checked_at


def test_generated_snippet_fails_when_the_data_moves(tree):
    from scigantic_cards import verify_card
    c = draft_card(str(tree))
    for p in sorted(tree.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    _, results = verify_card(c, timeout=60)
    assert results[0].status == "failed" and "no files matched" in results[0].detail


# ---------------------------------------------------------------- fsspec


@pytest.fixture
def memfs():
    import fsspec
    fs = fsspec.filesystem("memory")
    fs.store.clear() if hasattr(fs, "store") else None
    for path, data in {
        "/bkt/pre/a/one.csv": b"id,v\n1,2.5\n",
        "/bkt/pre/a/reads.fastq": FASTQ,
        "/bkt/pre/b/two.csv.gz": gzip.compress(b"id,w\n1,x\n"),
        "/bkt/pre/b/c/deep.vcf": VCF,
        "/bkt/pre/.hidden": b"h",
    }.items():
        fs.pipe_file(path, data)
    yield fs
    fs.store.clear() if hasattr(fs, "store") else None


def test_remote_listing_matches_local_semantics(memfs):
    c = draft_card("memory://bkt/pre")
    assert c.approx_items == 4 and "a/ (2)" in c.layout and "b/ (2)" in c.layout
    assert {s.path for s in c.samples} == {"a/one.csv", "a/reads.fastq", "b/two.csv.gz", "b/c/deep.vcf"}
    assert {s.format for s in c.samples} >= {"fastq", "vcf", "csv"}
    assert any(f.name == "w" for f in c.fields)  # gz csv columns read through the remote reader


def test_remote_snippets_and_truncation(memfs):
    c = draft_card("memory://bkt/pre", max_files=2)
    assert c.approx_items is None and "lower bounds" in c.layout
    full = draft_card("s3://not-reached/x", source=FsspecSource("memory://bkt/pre"))
    assert full.approx_items == 4
    from scigantic_cards.draft import _glob_snippet
    s3 = _glob_snippet("s3://bkt/pre/", "parquet")
    assert "fsspec.filesystem('s3', anon=True)" in s3 and "bkt/pre/**/*.parquet" in s3
    assert "token='anon'" in _glob_snippet("gs://bkt/p", "csv")


def test_remote_errors_are_draft_errors(memfs):
    with pytest.raises(DraftError):
        draft_card("memory://nope/nothing")
    with pytest.raises(DraftError, match="http"):
        draft_card("https://example.org/data")
    with pytest.raises(DraftError):
        draft_card("notaprotocol://x/y")


def test_remote_embed_bytes_same_rules(memfs):
    c = draft_card("memory://bkt/pre", embed_bytes=8)
    assert all((s.head_b64 == "") == (s.format not in ("fastq", "vcf")) for s in c.samples)


def test_works_without_scigantic_headers(tree, monkeypatch):
    monkeypatch.setattr(draft_mod, "_h", None)
    c = draft_card(str(tree))
    assert all(s.format in ("", "csv", "tsv") for s in c.samples)
    assert c.fields and not any("scigantic_headers" in a.snippet for a in c.access)


def test_walk_actually_stops_at_the_deadline(tmp_path, monkeypatch):
    for i in range(200):
        write(tmp_path, f"f{i:03d}.csv", "x\n1\n")
    ticks = iter(range(10**6))

    class Clock:
        @staticmethod
        def monotonic():
            return float(next(ticks))

    monkeypatch.setattr(draft_mod, "time", Clock)
    c = draft_card(str(tmp_path), time_limit=20)
    assert c.approx_items is None and "lower bounds" in c.layout
    listed = int(c.layout.split(" files")[0].replace(",", ""))
    assert 0 < listed < 40  # stopped long before the 200 files


def test_identical_schemas_are_listed_once(tmp_path):
    write(tmp_path, "a.csv", "id,v\n1,2\n")
    write(tmp_path, "b.csv", "id,v\n3,4\n")
    write(tmp_path, "c.csv", "id,w\n3,4\n")
    c = draft_card(str(tmp_path))
    assert sorted((f.group, f.name) for f in c.fields) == [("a.csv", "id"), ("a.csv", "v"), ("c.csv", "id"), ("c.csv", "w")]
    by = {s.path: s for s in c.samples}
    assert "same columns as a.csv" in by["b.csv"].summary and "same columns" not in by["c.csv"].summary


def test_parquet_schema_is_read_from_the_footer(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pq.write_table(pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"], "score": [0.1, 0.2, 0.3]}),
                   tmp_path / "t.parquet")
    pq.write_table(pa.table({"id": [4], "name": ["d"], "score": [0.4]}), tmp_path / "u.parquet")
    c = draft_card(str(tmp_path))
    by = {s.path: s for s in c.samples}
    assert by["t.parquet"].format == "parquet" and "3 rows" in by["t.parquet"].summary
    assert [(f.name, f.dtype) for f in c.fields] == [("id", "INT64"), ("name", "BYTE_ARRAY"), ("score", "DOUBLE")]
    assert "same columns as" in by["u.parquet"].summary  # identical schema listed once
