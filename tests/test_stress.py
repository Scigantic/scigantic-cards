"""Property-based tests. The loader and the shipped JSON Schema must agree."""
import base64
import json
import time

import jsonschema
from hypothesis import HealthCheck, given, settings, strategies as st

from scigantic_cards import Card, CardError, lint
from scigantic_cards.cli import main

SETTINGS = settings(max_examples=1500, suppress_health_check=[HealthCheck.too_slow], deadline=None)

json_leaf = st.none() | st.booleans() | st.integers() | st.floats() | st.text()
json_any = st.recursive(
    json_leaf,
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=12), c, max_size=4),
    max_leaves=25,
)

text = st.text(max_size=30)
good_count = st.none() | st.integers(min_value=0, max_value=10**15) | st.sampled_from([0, 1.0, 1e20, -0.0])
edge = st.sampled_from([-1, -3, 1.5, True, False, None, "", "x", [], {}, float("nan"), float("inf")])
bad_or_any = edge | json_any

valid_field = st.fixed_dictionaries(
    {"name": text, "dtype": text}, optional={"note": text, "group": text}
)
valid_access = st.fixed_dictionaries(
    {"goal": text, "snippet": text}, optional={"verified": st.booleans(), "language": text}
)
b64_chunk = st.binary(max_size=60).map(lambda b: base64.b64encode(b).decode())
finite_leaf = (st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False)
               | st.text(max_size=20))
finite_json = st.recursive(
    finite_leaf,
    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=8), c, max_size=3),
    max_leaves=10,
)
valid_sample = st.fixed_dictionaries(
    {"path": text},
    optional={"size": good_count, "format": text, "summary": text,
              "fields": st.dictionaries(st.text(max_size=8), finite_json, max_size=4),
              "head_b64": b64_chunk},
)
valid_doc = st.fixed_dictionaries(
    {"name": st.text(min_size=1, max_size=30)},
    optional={
        "identity": text, "summary": text, "layout": text,
        "approx_bytes": good_count, "approx_items": good_count,
        "fields": st.lists(valid_field, max_size=4),
        "differs_from_upstream": st.lists(text, max_size=4),
        "gotchas": st.lists(text, max_size=4),
        "access": st.lists(valid_access, max_size=4),
        "samples": st.lists(valid_sample, max_size=3),
        "location": text,
        "checked_at": text,
    },
)

COUNT_NEAR = [-1, -3, 1.5, True, False, "1", float("nan"), float("inf"), [], {}]
STR_NEAR = [5, None, [], {}, True, 1.5]
NEAR = {
    "name": ["", 5, None, [], True],
    "approx_bytes": COUNT_NEAR, "approx_items": COUNT_NEAR,
    "fields": ["a", None, {}, [1], [None], [{}]],
    "access": ["a", None, {}, [1], [None], [{}]],
    "differs_from_upstream": ["a", None, {}, [1], [None]],
    "gotchas": ["a", None, {}, [1], [None]],
    "location": STR_NEAR,
    "samples": ["a", None, {}, [1], [None], [{}], [{"path": 1}], [{"path": "p", "size": -1}]],
    "bogus": ["x", 1, None],
}
SUB_NEAR = {
    "field": {"name": STR_NEAR, "dtype": STR_NEAR + [None], "note": STR_NEAR,
              "group": STR_NEAR, "bogus": ["x", 1]},
    "access": {"goal": STR_NEAR, "snippet": STR_NEAR, "verified": ["yes", 1, 0, None, "true"],
               "language": STR_NEAR, "bogus": ["x", 1]},
    "sample": {"path": STR_NEAR + [None], "size": COUNT_NEAR, "format": STR_NEAR, "summary": STR_NEAR,
               "fields": ["a", [], None, 3], "head_b64": ["AAA", "A===", "AAAA\n", "é", 5, None, "AA AA", "-_-_"],
               "bogus": ["x", 1]},
}

TOP = ["name", "identity", "summary", "layout", "location", "approx_bytes", "approx_items", "fields",
       "differs_from_upstream", "gotchas", "access", "samples", "checked_at", "bogus"]


def _dict_items(v):
    return isinstance(v, list) and v and all(isinstance(x, dict) for x in v)


@st.composite
def cardish(draw):
    """A valid card with up to two single-value mutations, at top level or one level down."""
    doc = draw(valid_doc)
    for _ in range(draw(st.integers(0, 2))):
        where = draw(st.sampled_from(["top", "field", "access", "list_item", "drop", "sample"]))
        value = draw(bad_or_any)
        if where == "top":
            key = draw(st.sampled_from(TOP))
            near = NEAR.get(key, STR_NEAR)
            doc[key] = draw(st.sampled_from(near)) if draw(st.booleans()) else value
        elif where == "field" and _dict_items(doc.get("fields")):
            i = draw(st.integers(0, len(doc["fields"]) - 1))
            key = draw(st.sampled_from(sorted(SUB_NEAR["field"])))
            doc["fields"][i][key] = draw(st.sampled_from(SUB_NEAR["field"][key])) if draw(st.booleans()) else value
        elif where == "access" and _dict_items(doc.get("access")):
            i = draw(st.integers(0, len(doc["access"]) - 1))
            key = draw(st.sampled_from(sorted(SUB_NEAR["access"])))
            doc["access"][i][key] = draw(st.sampled_from(SUB_NEAR["access"][key])) if draw(st.booleans()) else value
        elif where == "sample" and _dict_items(doc.get("samples")):
            i = draw(st.integers(0, len(doc["samples"]) - 1))
            key = draw(st.sampled_from(sorted(SUB_NEAR["sample"])))
            doc["samples"][i][key] = (draw(st.sampled_from(SUB_NEAR["sample"][key]))
                                      if draw(st.booleans()) else value)
        elif where == "drop":
            target = draw(st.sampled_from(["top", "fields", "access", "samples"]))
            if target == "top":
                doc.pop(draw(st.sampled_from(TOP)), None)
            elif _dict_items(doc.get(target)):
                item = doc[target][0]
                item.pop(draw(st.sampled_from(sorted(item))), None)
        elif where == "list_item":
            key = draw(st.sampled_from(["gotchas", "differs_from_upstream", "fields", "access"]))
            if isinstance(doc.get(key), list):
                doc[key].append(value)
    return doc


def schema_accepts(schema, doc) -> bool:
    try:
        jsonschema.validate(doc, schema)
        return True
    except jsonschema.ValidationError:
        return False


def loader_accepts(doc) -> bool:
    try:
        Card.from_dict(doc)
        return True
    except CardError:
        return False


@SETTINGS
@given(cardish())
def test_loader_matches_schema_on_card_shaped_input(schema, doc):
    assert loader_accepts(doc) == schema_accepts(schema, doc), doc


@SETTINGS
@given(json_any)
def test_loader_matches_schema_on_arbitrary_json(schema, doc):
    assert loader_accepts(doc) == schema_accepts(schema, doc), doc


@SETTINGS
@given(cardish())
def test_accepted_cards_lint_and_round_trip(doc):
    try:
        card = Card.from_dict(doc)
    except CardError:
        return
    lint(card)  # must not raise
    assert Card.from_json(card.to_json()) == card
    assert Card.from_json(card.to_json(indent=None)) == card


@SETTINGS
@given(json_any)
def test_loader_only_ever_raises_card_error(doc):
    try:
        Card.from_dict(doc)
    except CardError:
        pass


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.binary(max_size=2000))
def test_cli_survives_arbitrary_bytes(tmp_path_factory, data):
    p = tmp_path_factory.mktemp("f") / "card.json"
    p.write_bytes(data)
    assert main(["lint", str(p)]) in (0, 1, 2)


def test_cli_survives_non_files(tmp_path):
    assert main(["lint", str(tmp_path)]) == 2  # a directory
    assert main(["lint", str(tmp_path / "nope.json")]) == 2


def test_cli_handles_bom_and_crlf(tmp_path):
    p = tmp_path / "c.json"
    body = json.dumps({"name": "x"}, indent=2).replace("\n", "\r\n")
    p.write_bytes(b"\xef\xbb\xbf" + body.encode())
    assert main(["lint", str(p)]) == 1  # loads; lint errors on the empty card


def test_cli_rejects_oversized_file(tmp_path, capsys):
    p = tmp_path / "big.json"
    p.write_text('{"name": "x", "summary": "' + "a" * 6_000_000 + '"}')
    assert main(["lint", str(p)]) == 2
    assert "larger than" in capsys.readouterr().err


def test_cli_exit_codes_and_strict(tmp_path, capsys):
    from conftest import make_card
    ok = tmp_path / "ok.json"
    ok.write_text(make_card().to_json())
    warn = tmp_path / "warn.json"
    warn.write_text(make_card(checked_at="").to_json())
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert main(["lint", str(ok)]) == 0
    assert main(["lint", str(warn)]) == 0
    assert main(["lint", "--strict", str(warn)]) == 1
    assert main(["lint", str(bad)]) == 2
    # one bad file does not stop later files from being linted
    assert main(["lint", str(bad), str(warn), "--strict"]) == 2
    assert "warn.json" in capsys.readouterr().out


def test_large_card_lints_quickly():
    from conftest import make_card
    from scigantic_cards import Field
    card = make_card(fields=[Field(f"f{i}", "int") for i in range(100_000)])
    t = time.perf_counter()
    issues = lint(card)
    assert time.perf_counter() - t < 5
    assert any(i.rule == "budget" for i in issues)


# ---------------------------------------------------------------- draft_card on random trees

import os

from scigantic_cards import draft_card

name_chars = st.characters(blacklist_categories=("Cs",), blacklist_characters="/\x00")
file_name = st.text(name_chars, min_size=1, max_size=12).filter(lambda s: s not in (".", ".."))
tree_files = st.dictionaries(
    st.lists(file_name, min_size=1, max_size=4).map("/".join),
    st.binary(max_size=120),
    max_size=12,
)


def build(root, files) -> int:
    n = 0
    for rel, data in files.items():
        p = os.path.join(root, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.isdir(p) or os.path.isfile(os.path.dirname(p)):
                continue
            with open(p, "wb") as fh:
                fh.write(data)
        except OSError:
            continue
    return n


@settings(max_examples=150, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(tree_files, st.booleans(), st.integers(1, 8), st.integers(0, 4))
def test_draft_card_on_random_trees(tmp_path_factory, schema, files, hidden, max_files, max_depth):
    root = tmp_path_factory.mktemp("t")
    build(str(root), files)
    card = draft_card(str(root), include_hidden=hidden, max_files=max_files, max_depth=max_depth)
    assert Card.from_json(card.to_json()) == card
    assert schema_accepts(schema, json.loads(card.to_json()))
    lint(card)
    assert (card.approx_items or 0) <= max_files
    assert len(card.samples) <= 6
    if card.approx_items is not None:
        assert card.approx_bytes == sum(
            os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(root) for f in fs
            if (hidden or not any(part.startswith(".") for part in os.path.relpath(os.path.join(d, f), root).split(os.sep)))
            and len(os.path.relpath(os.path.join(d, f), root).split(os.sep)) - 1 <= max_depth
        )
