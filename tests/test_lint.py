"""One positive and one negative case per rule."""
import pytest

from scigantic_cards import Access, Field, lint
from conftest import make_card


def rules(card, **kw):
    return {i.rule for i in lint(card, **kw)}


def test_clean_card_has_no_issues():
    assert lint(make_card()) == []


@pytest.mark.parametrize("overrides,rule", [
    ({"name": "  "}, "name"),
    ({"identity": ""}, "identity"),
    ({"identity": "   \n"}, "identity"),
    ({"differs_from_upstream": []}, "delta"),
    ({"differs_from_upstream": ["", "  "]}, "delta"),
    ({"approx_bytes": None}, "size"),
    ({"access": []}, "access"),
    ({"access": [Access("g", "   ", True)]}, "empty-snippet"),
    ({"access": [Access("g", "x = 1", False)]}, "unverified-access"),
    ({"fields": [Field("a", "")]}, "field-dtype"),
    ({"fields": [Field("a", "int"), Field("a", "str")]}, "field-duplicate"),
    ({"checked_at": ""}, "checked-at"),
    ({"checked_at": "yesterday"}, "checked-at"),
    ({"checked_at": "2026-13-40"}, "checked-at"),
    ({"checked_at": "2026-10-11x"}, "checked-at"),
    ({"gotchas": ["x" * 5000]}, "budget"),
])
def test_rule_fires(overrides, rule):
    assert rule in rules(make_card(**overrides))


@pytest.mark.parametrize("snippet", [
    "glob.glob('~/data/*.nc')",
    'open("~/x.csv")',
    "Path('~/data').iterdir()",
    "os.listdir('~/d')",
])
def test_tilde_path_fires(snippet):
    assert "tilde-path" in rules(make_card(access=[Access("g", snippet, True)]))


@pytest.mark.parametrize("snippet", [
    "glob.glob(os.path.expanduser('~/data/*.nc'))",
    "Path('~/data').expanduser()",
    "url = 'https://example.org/~user/x'",
    "s3://bucket/~/x",
])
def test_tilde_path_quiet(snippet):
    assert "tilde-path" not in rules(make_card(access=[Access("g", snippet, True)]))


@pytest.mark.parametrize("value", ["2026-10-11", "2026-10-11T08:00:00Z", "2026-10-11 08:00"])
def test_checked_at_accepts(value):
    assert "checked-at" not in rules(make_card(checked_at=value))


def test_same_name_in_different_groups_is_not_a_duplicate():
    c = make_card(fields=[Field("samples", "int", group="clinical"),
                          Field("samples", "int", group="rna_seq")])
    assert "field-duplicate" not in rules(c)


def test_same_name_in_same_group_is_a_duplicate():
    c = make_card(fields=[Field("samples", "int", group="clinical"),
                          Field("samples", "str", group="clinical")])
    issues = [i for i in lint(c) if i.rule == "field-duplicate"]
    assert len(issues) == 1 and "clinical.samples" in issues[0].message


def test_ungrouped_and_grouped_same_name_are_distinct():
    c = make_card(fields=[Field("id", "int"), Field("id", "int", group="t")])
    assert "field-duplicate" not in rules(c)


def test_either_differs_or_gotchas_satisfies_delta():
    assert "delta" not in rules(make_card(differs_from_upstream=[], gotchas=["x"]))


def test_one_size_field_is_enough():
    assert "size" not in rules(make_card(approx_bytes=None, approx_items=5))


def test_zero_size_counts_as_given():
    assert "size" not in rules(make_card(approx_bytes=0))


def test_budget_is_configurable():
    c = make_card()
    assert "budget" in rules(c, byte_budget=10)
    assert "budget" not in rules(c, byte_budget=10_000)


def test_budget_counts_utf8_bytes_not_escapes():
    # 1000 CJK characters are 3000 UTF-8 bytes. With ensure_ascii escaping the
    # same text would be counted as 6000 bytes and trip a 4000 budget.
    c = make_card(gotchas=["字" * 1000])
    assert 3000 < c.compact_size() < 3400
    assert "budget" not in rules(c)


def test_one_issue_per_unverified_snippet():
    c = make_card(access=[Access("a", "x", False), Access("b", "y", False)])
    assert [i.rule for i in lint(c)].count("unverified-access") == 2


def test_lint_is_deterministic():
    c = make_card(identity="", access=[])
    assert lint(c) == lint(c)
