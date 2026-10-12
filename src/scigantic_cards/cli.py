"""Command line entry point.

    scigantic-cards lint card.json [--budget N] [--strict]
    scigantic-cards init LOCATION [-o card.json] [--name N] [--max-files N]
                                  [--embed-bytes N] [--hidden]
    scigantic-cards encode card.json [--compress]
    scigantic-cards decode [STRING | -]
    scigantic-cards verify card.json --run [--write] [--timeout S]

Exit codes: 0 no problems, 1 lint errors (or warnings with --strict) or a
failed snippet, 2 a file could not be read or is not a valid card.
"""
from __future__ import annotations

import argparse
import json
import sys

from .card import Card, CardError
from .draft import MAX_EMBED_BYTES, DraftError, draft_card
from .lint import DEFAULT_BYTE_BUDGET, lint
from .verify import verify_card

MAX_BYTES = 5_000_000


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scigantic-cards")
    sub = p.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("lint", help="lint one or more card files")
    lp.add_argument("files", nargs="+")
    lp.add_argument("--budget", type=int, default=DEFAULT_BYTE_BUDGET)
    lp.add_argument("--strict", action="store_true", help="treat warnings as errors")

    ip = sub.add_parser("init", help="draft a card from a folder or bucket")
    ip.add_argument("location", help="a folder (~ works) or a URL such as s3://bucket/prefix")
    ip.add_argument("-o", "--output", help="write the card here instead of stdout")
    ip.add_argument("--name")
    ip.add_argument("--max-files", type=int, default=5000)
    ip.add_argument("--max-depth", type=int, default=8)
    ip.add_argument("--max-samples", type=int, default=6)
    ip.add_argument("--embed-bytes", type=int, default=0, metavar="N",
                    help=f"embed the first N bytes (max {MAX_EMBED_BYTES}) of sampled files "
                         "that a header decoder recognizes")
    ip.add_argument("--hidden", action="store_true", help="include hidden files and folders")

    ep = sub.add_parser("encode", help="print a card as URL-safe base64")
    ep.add_argument("file")
    ep.add_argument("--compress", action="store_true")

    dp = sub.add_parser("decode", help="print the JSON for a base64 card")
    dp.add_argument("text", nargs="?", default="-", help="the encoded card, or - for stdin")

    vp = sub.add_parser("verify", help="run a card's Python snippets (executes code from the card)")
    vp.add_argument("file")
    vp.add_argument("--run", action="store_true", help="required: confirms you trust the card")
    vp.add_argument("--write", action="store_true", help="save verified flags and checked_at to the file")
    vp.add_argument("--timeout", type=float, default=60.0)
    vp.add_argument("--cwd")

    args = p.parse_args(argv)
    if args.cmd == "lint":
        return _lint(args)
    if args.cmd == "init":
        return _init(args)
    if args.cmd == "encode":
        return _encode(args)
    if args.cmd == "decode":
        return _decode(args)
    return _verify(args)


def _lint(args: argparse.Namespace) -> int:
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


def _init(args: argparse.Namespace) -> int:
    try:
        card = draft_card(
            args.location, name=args.name, max_files=args.max_files, max_depth=args.max_depth,
            max_samples=args.max_samples, embed_bytes=args.embed_bytes, include_hidden=args.hidden,
        )
    except DraftError as e:
        print(f"cannot draft a card: {e}", file=sys.stderr)
        return 2
    text = card.to_json() + "\n"
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as e:
            print(f"{args.output}: {e}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)
    todo = [i.rule for i in lint(card) if i.rule in ("identity", "delta")]
    if todo:
        print("fill in: identity, and differs_from_upstream or gotchas "
              "(a script cannot know these)", file=sys.stderr)
    return 0


def _encode(args: argparse.Namespace) -> int:
    try:
        card = _load(args.file)
    except (OSError, CardError, UnicodeDecodeError) as e:
        print(f"{args.file}: cannot load: {e}", file=sys.stderr)
        return 2
    print(card.to_b64(compress=args.compress))
    return 0


def _decode(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.text == "-" else args.text
    try:
        card = Card.from_b64(text)
    except CardError as e:
        print(f"cannot decode: {e}", file=sys.stderr)
        return 2
    print(card.to_json())
    return 0


def _verify(args: argparse.Namespace) -> int:
    if not args.run:
        print("verify executes the Python snippets in the card. Re-run with --run if you trust it.",
              file=sys.stderr)
        return 2
    try:
        card = _load(args.file)
    except (OSError, CardError, UnicodeDecodeError) as e:
        print(f"{args.file}: cannot load: {e}", file=sys.stderr)
        return 2
    updated, results = verify_card(card, timeout=args.timeout, cwd=args.cwd)
    failed = False
    for r in results:
        line = f"{r.status}: {r.goal}"
        print(line + (f": {r.detail}" if r.detail else ""))
        failed = failed or r.status in ("failed", "timeout")
    if args.write:
        try:
            with open(args.file, "w", encoding="utf-8") as fh:
                fh.write(updated.to_json() + "\n")
        except OSError as e:
            print(f"{args.file}: {e}", file=sys.stderr)
            return 2
    return 1 if failed else 0


def _load(path: str) -> Card:
    with open(path, "rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise CardError(f"file is larger than {MAX_BYTES} bytes")
    return Card.from_json(raw.decode("utf-8-sig"))


if __name__ == "__main__":
    raise SystemExit(main())
