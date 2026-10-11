"""Lint a card. Each rule yields zero or more Issue objects."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .card import Card

DEFAULT_BYTE_BUDGET = 4000

# A string literal that starts with ~/ . Python does not expand it in glob(),
# open(), Path() or os.listdir().
_TILDE_PATH = re.compile(r"""['"]~/""")


@dataclass
class Issue:
    rule: str
    severity: str  # "error" or "warn"
    message: str


def lint(card: Card, byte_budget: int = DEFAULT_BYTE_BUDGET) -> list[Issue]:
    out: list[Issue] = []

    if not card.name.strip():
        out.append(Issue("name", "error", "Name is blank."))

    if not card.identity.strip():
        out.append(Issue(
            "identity", "error",
            "No identity. Name the canonical dataset, release, or paper so the "
            "agent can recall what it already knows.",
        ))

    differences = [s for s in card.differs_from_upstream + card.gotchas if s.strip()]
    if not differences:
        out.append(Issue(
            "delta", "error",
            "No differences or gotchas. A card with neither only restates what "
            "the agent already knows.",
        ))

    if card.approx_bytes is None and card.approx_items is None:
        out.append(Issue(
            "size", "warn",
            "No size. Agents choose between streaming and loading based on it.",
        ))

    if not card.access:
        out.append(Issue("access", "error", "No access snippet."))
    for a in card.access:
        goal = a.goal.strip() or "(no goal)"
        if not a.snippet.strip():
            out.append(Issue("empty-snippet", "error", f"'{goal}': snippet is empty."))
        if not a.verified:
            out.append(Issue(
                "unverified-access", "warn", f"'{goal}': snippet was not executed.",
            ))
        if _TILDE_PATH.search(a.snippet) and "expanduser" not in a.snippet:
            out.append(Issue(
                "tilde-path", "error",
                f"'{goal}': a path starting with ~/ is not expanded by Python "
                "unless it goes through os.path.expanduser or Path.expanduser.",
            ))

    seen: set[tuple[str, str]] = set()
    for f in card.fields:
        label = f"{f.group}.{f.name}" if f.group else f.name
        if not f.dtype.strip():
            out.append(Issue("field-dtype", "warn", f"Field '{label}' has no dtype."))
        if (f.group, f.name) in seen:
            out.append(Issue("field-duplicate", "warn", f"Field '{label}' is listed twice."))
        seen.add((f.group, f.name))

    if not _is_iso_date(card.checked_at):
        out.append(Issue(
            "checked-at", "warn", "checked_at is missing or not an ISO date (YYYY-MM-DD).",
        ))

    size = card.compact_size()
    if size > byte_budget:
        out.append(Issue(
            "budget", "warn",
            f"Card is {size} bytes as compact JSON, over the {byte_budget} byte "
            "budget. Cut what the agent would already know.",
        ))
    return out


def _is_iso_date(s: str) -> bool:
    if len(s) < 10 or (len(s) > 10 and s[10] not in "T "):
        return False
    try:
        date.fromisoformat(s[:10])
    except ValueError:
        return False
    return True
