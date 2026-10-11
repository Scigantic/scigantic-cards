"""Property-based tests. The loader and the shipped JSON Schema must agree."""
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

valid_field = st.fixed_dictionaries({"name": text, "dtype": text}, optional={"note": text})
valid_access = st.fixed_dictionaries(
    {"goal": text, "snippet": text}, optional={"verified": st.booleans()}
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
    "bogus": ["x", 1, None],
}
SUB_NEAR = {
    "field": {"name": STR_NEAR, "dtype": STR_NEAR + [None], "note": STR_NEAR, "bogus": ["x", 1]},
    "access": {"goal": STR_NEAR, "snippet": STR_NEAR, "verified": ["yes", 1, 0, None, "true"],
               "bogus": ["x", 1]},
}

TOP = ["name", "identity", "summary", "layout", "approx_bytes", "approx_items", "fields",
       "differs_from_upstream", "gotchas", "access", "checked_at", "bogus"]


def _dict_items(v):
    return isinstance(v, list) and v and all(isinstance(x, dict) for x in v)


@st.composite
def cardish(draw):
    """A valid card with up to two single-value mutations, at top level or one level down."""
    doc = draw(valid_doc)
    for _ in range(draw(st.integers(0, 2))):
        where = draw(st.sampled_from(["top", "field", "access", "list_item", "drop"]))
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
        elif where == "drop":
            target = draw(st.sampled_from(["top", "fields", "access"]))
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
