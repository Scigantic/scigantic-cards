"""Run a card's Python access snippets and record whether they worked.

This executes code taken from the card. Only run it on cards you trust.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import date

from .card import Access, Card


@dataclass
class RunResult:
    goal: str
    status: str  # "passed", "failed", "timeout", "skipped"
    detail: str = ""


def _run(snippet: str, timeout: float, cwd: str | None) -> tuple[str, str]:
    kw: dict = {"start_new_session": True} if os.name == "posix" else {}
    proc = subprocess.Popen(
        [sys.executable, "-c", snippet],
        cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:  # pragma: no cover
            proc.kill()
        proc.communicate()
        return "timeout", f"no result after {timeout:g}s"
    tail = (err or out).decode("utf-8", "replace").strip()[-300:]
    return ("passed" if proc.returncode == 0 else "failed"), tail


def verify_card(
    card: Card, *, timeout: float = 60.0, cwd: str | None = None, today: str | None = None,
) -> tuple[Card, list[RunResult]]:
    """Run each Python snippet. Returns an updated copy and one result per snippet.

    `verified` is set to whether the snippet exited 0. `checked_at` is set to
    today's date if at least one snippet ran.
    """
    results: list[RunResult] = []
    access: list[Access] = []
    ran = False
    for a in card.access:
        if a.language.lower() not in ("python", "py", "python3"):
            results.append(RunResult(a.goal, "skipped", f"language is {a.language!r}"))
            access.append(a)
            continue
        if not a.snippet.strip():
            results.append(RunResult(a.goal, "skipped", "empty snippet"))
            access.append(a)
            continue
        status, detail = _run(a.snippet, timeout, cwd)
        ran = True
        results.append(RunResult(a.goal, status, detail if status != "passed" else ""))
        access.append(replace(a, verified=(status == "passed")))
    updated = replace(
        card, access=access, checked_at=(today or date.today().isoformat()) if ran else card.checked_at,
    )
    return updated, results
