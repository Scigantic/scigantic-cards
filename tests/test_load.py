import json

import pytest

from scigantic_cards import Card, CardError
from conftest import make_card


def test_round_trip():
    c = make_card(gotchas=["a", "b"])
    assert Card.from_json(c.to_json()) == c
    assert Card.from_json(c.to_json(indent=None)) == c


def test_round_trip_non_ascii():
    c = make_card(identity="Données d'été, 日本語, 🙂")
    assert Card.from_json(c.to_json()) == c
    assert "日本語" in c.to_json()


def test_field_group_round_trips_and_defaults_empty():
    c = Card.from_dict({"name": "x", "fields": [
        {"name": "a", "dtype": "i", "group": "t1"}, {"name": "b", "dtype": "i"}]})
    assert [f.group for f in c.fields] == ["t1", ""]
    assert Card.from_json(c.to_json()) == c


def test_minimal_card():
    c = Card.from_dict({"name": "x"})
    assert c.fields == [] and c.approx_bytes is None


@pytest.mark.parametrize("doc,where", [
    ([], "card"),
    ("x", "card"),
    (None, "card"),
    ({}, "card.name"),
    ({"name": ""}, "card.name"),
    ({"name": 5}, "card.name"),
    ({"name": None}, "card.name"),
    ({"name": "x", "extra": 1}, "extra"),
    ({"name": "x", "approx_bytes": -1}, "card.approx_bytes"),
    ({"name": "x", "approx_bytes": True}, "card.approx_bytes"),
    ({"name": "x", "approx_bytes": 1.5}, "card.approx_bytes"),
    ({"name": "x", "approx_bytes": "10"}, "card.approx_bytes"),
    ({"name": "x", "approx_bytes": float("nan")}, "card.approx_bytes"),
    ({"name": "x", "approx_bytes": float("inf")}, "card.approx_bytes"),
    ({"name": "x", "fields": "a"}, "card.fields"),
    ({"name": "x", "fields": [1]}, "card.fields[0]"),
    ({"name": "x", "fields": [{"name": "a"}]}, "card.fields[0].dtype"),
    ({"name": "x", "fields": [{"name": "a", "dtype": "i", "z": 1}]}, "z"),
    ({"name": "x", "fields": [{"name": "a", "dtype": "i", "group": 3}]}, "card.fields[0].group"),
    ({"name": "x", "gotchas": "a"}, "card.gotchas"),
    ({"name": "x", "gotchas": ["a", 2]}, "card.gotchas[1]"),
    ({"name": "x", "access": [{"goal": "g"}]}, "card.access[0].snippet"),
    ({"name": "x", "access": [{"goal": "g", "snippet": "s", "verified": "yes"}]},
     "card.access[0].verified"),
])
def test_rejects_with_path(doc, where):
    with pytest.raises(CardError) as e:
        Card.from_dict(doc)
    assert where in str(e.value)


def test_integral_float_is_accepted_as_int():
    c = Card.from_dict({"name": "x", "approx_bytes": 1e9})
    assert c.approx_bytes == 10**9 and isinstance(c.approx_bytes, int)


def test_huge_int_is_accepted():
    assert Card.from_dict({"name": "x", "approx_bytes": 10**40}).approx_bytes == 10**40


def test_invalid_json_is_a_card_error():
    with pytest.raises(CardError):
        Card.from_json("{not json")


def test_deep_nesting_is_a_card_error():
    with pytest.raises(CardError):
        Card.from_json("[" * 100_000 + "]" * 100_000)


def test_card_error_is_a_value_error():
    assert issubclass(CardError, ValueError)
