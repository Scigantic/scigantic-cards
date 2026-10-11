"""Command line entry point: scigantic-cards lint card.json [--budget N] [--strict].

Exit codes: 0 no problems, 1 lint errors (or warnings with --strict),
2 a file could not be read or is not a valid card.
"""
from __future__ import annotations

import argparse
import sys

from .card import Card, CardError
from .lint import DEFAULT_BYTE_BUDGET, lint

MAX_BYTES = 5_000_000


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scigantic-cards")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("lint", help="lint one or more card files")
    lp.add_argument("files", nargs="+")
    lp.add_argument("--budget", type=int, default=DEFAULT_BYTE_BUDGET)
    lp.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = p.parse_args(argv)

    code = 0
    for path in args.files:
        try:
            card = _load(path)
        except (OSError, CardError, UnicodeDecodeError) as e:
            print(f"{path}: cannot load: {e}", file=sys.stderr)
            code = 2
            continue
        for issue in lint(card, byte_budget=args.budget):
            print(f"{path}: {issue.severity}: {issue.rule}: {issue.message}")
            if code == 0 and (issue.severity == "error" or args.strict):
                code = 1
    return code


def _load(path: str) -> Card:
    with open(path, "rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise CardError(f"file is larger than {MAX_BYTES} bytes")
    return Card.from_json(raw.decode("utf-8-sig"))


if __name__ == "__main__":
    raise SystemExit(main())
