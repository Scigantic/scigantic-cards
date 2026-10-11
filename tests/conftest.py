import json
from pathlib import Path

import pytest

from scigantic_cards import Access, Card

SCHEMA_PATH = Path(__file__).parent.parent / "src/scigantic_cards/card.schema.json"


@pytest.fixture(scope="session")
def schema():
    return json.loads(SCHEMA_PATH.read_text())


def make_card(**overrides) -> Card:
    base = dict(
        name="demo",
        identity="NOAA GHCN-Daily, 2024 snapshot",
        differs_from_upstream=["Station files are gzipped CSV, not .dly"],
        approx_bytes=10**9,
        access=[Access("read one station", "pd.read_csv(p)", verified=True)],
        checked_at="2026-10-11",
    )
    base.update(overrides)
    return Card(**base)
