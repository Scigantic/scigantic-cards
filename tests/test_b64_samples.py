import base64
import json

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from scigantic_cards import Access, Card, CardError, Field, Sample
from conftest import make_card


def test_b64_round_trip_plain_and_compressed():
    c = make_card(gotchas=["é 日本語 🙂"], samples=[Sample("a/b.bin", 3, "x", "s", {"k": [1, 2]}, "AAEC")])
    assert Card.from_b64(c.to_b64()) == c
    assert Card.from_b64(c.to_b64(compress=True)) == c


def test_b64_is_url_safe_and_whitespace_tolerant():
    c = make_card(gotchas=["?>?>?>" * 40])
    enc = c.to_b64()
    assert "+" not in enc and "/" not in enc
    wrapped = "\n".join(enc[i:i + 60] for i in range(0, len(enc), 60))
    assert Card.from_b64(wrapped) == c
    assert Card.from_b64(enc.rstrip("=")) == c


def test_compression_shrinks_repetitive_cards():
    c = make_card(gotchas=["repeat " * 500])
    assert len(c.to_b64(compress=True)) < len(c.to_b64()) / 4


@pytest.mark.parametrize("text", [
    "", "!!!!", "not base64 at all", "AAAA", "e30", "W10",  # "{}" and "[]" are not cards
    "z:", "z:AAAA", "z:" + base64.urlsafe_b64encode(b"not zlib").decode(),
    base64.urlsafe_b64encode(b"\xff\xfe\x00").decode(),
])
def test_b64_rejects_garbage_with_card_error(text):
    with pytest.raises(CardError):
        Card.from_b64(text)


def test_b64_size_limits():
    with pytest.raises(CardError):
        Card.from_b64("A" * 8_000_001)
    import zlib
    bomb = "z:" + base64.urlsafe_b64encode(zlib.compress(b" " * 50_000_000)).decode()
    with pytest.raises(CardError, match="larger"):
        Card.from_b64(bomb)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=200), st.booleans())
def test_b64_round_trip_any_text(s, compress):
    c = make_card(summary=s, gotchas=[s])
    assert Card.from_b64(c.to_b64(compress=compress)) == c


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=300))
def test_from_b64_only_ever_raises_card_error(s):
    try:
        Card.from_b64(s)
    except CardError:
        pass


def test_nan_rejected_in_json_text_and_sample_fields():
    with pytest.raises(CardError):
        Card.from_json('{"name":"x","samples":[{"path":"p","fields":{"a":NaN}}]}')
    with pytest.raises(CardError):
        Card.from_dict({"name": "x", "samples": [{"path": "p", "fields": {"a": [float("inf")]}}]})
    with pytest.raises(ValueError):
        Card(name="x", samples=[Sample("p", fields={"a": float("nan")})]).to_json()


@pytest.mark.parametrize("head,ok", [
    ("", True), ("AAAA", True), ("AA==", True), ("AAA=", True), ("QUJD", True),
    ("AAA", False), ("A===", False), ("AA=A", False), ("AAAA\n", False), ("AA AA", False),
    ("AAA-", False), ("AAAA" * 1501, False), ("é", False),
])
def test_head_b64_validation_matches_schema(schema, head, ok):
    doc = {"name": "x", "samples": [{"path": "p", "head_b64": head}]}
    try:
        Card.from_dict(doc)
        loader = True
    except CardError:
        loader = False
    try:
        jsonschema.validate(doc, schema)
        sch = True
    except jsonschema.ValidationError:
        sch = False
    assert loader == sch == ok


def test_sample_and_language_validation():
    for doc in (
        {"name": "x", "samples": ["a"]},
        {"name": "x", "samples": [{}]},
        {"name": "x", "samples": [{"path": 3}]},
        {"name": "x", "samples": [{"path": "p", "size": -1}]},
        {"name": "x", "samples": [{"path": "p", "fields": []}]},
        {"name": "x", "samples": [{"path": "p", "extra": 1}]},
        {"name": "x", "location": 5},
        {"name": "x", "access": [{"goal": "g", "snippet": "s", "language": 3}]},
    ):
        with pytest.raises(CardError):
            Card.from_dict(doc)


def test_new_fields_default_and_round_trip():
    c = Card.from_dict({"name": "x"})
    assert c.location == "" and c.samples == []
    full = make_card(location="s3://b/p", access=[Access("g", "ls", False, "bash")],
                     samples=[Sample("a", 1, "f", "s", {"n": None}, "QUJD")])
    assert Card.from_json(full.to_json()) == full


def test_decompression_memory_is_bounded():
    import tracemalloc
    import zlib

    comp = zlib.compressobj(9)
    parts = [comp.compress(b"\0" * 1_000_000) for _ in range(200)] + [comp.flush()]  # 200 MB of zeros
    bomb = "z:" + base64.urlsafe_b64encode(b"".join(parts)).decode()
    assert len(bomb) < 1_000_000
    tracemalloc.start()
    try:
        with pytest.raises(CardError, match="larger"):
            Card.from_b64(bomb)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 60_000_000, f"peak {peak} bytes"
