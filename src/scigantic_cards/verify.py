"""Run a card's Python access snippets and record whether they worked.

This executes code taken from the card. Only run it on cards you trust.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import date

from .card import Access, Card


@dataclass
class RunResult:
    goal: str
    status: str  # "passed", "failed", "timeout", "skipped"
    detail: str = ""


_TAIL = 4096


def _drain(stream, sink: list[bytes]) -> None:
    """Read a pipe to EOF, keeping only the last few KB so a chatty snippet cannot fill memory."""
    buf = b""
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            buf = (buf + chunk)[-_TAIL:]
    except (OSError, ValueError):
        pass
    sink.append(buf)


def _kill(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif proc.poll() is None:  # pragma: no cover
        proc.kill()
    proc.wait()


def _run(snippet: str, timeout: float, cwd: str | None) -> tuple[str, str]:
    kw: dict = {"start_new_session": True} if os.name == "posix" else {}
    # The snippet goes through a file, not argv, so its size and NUL bytes cannot break the launch.
    with tempfile.TemporaryDirectory(prefix="scigantic-cards-") as tmp:
        script = os.path.join(tmp, "snippet.py")
        with open(script, "w", encoding="utf-8", errors="replace", newline="") as fh:
            fh.write(snippet)
        proc = subprocess.Popen(
            [sys.executable, script], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw,
        )
        out_s: list[bytes] = []
        err_s: list[bytes] = []
        threads = [
            threading.Thread(target=_drain, args=(proc.stdout, out_s), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, err_s), daemon=True),
        ]
        for t in threads:
            t.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        # Also on a normal exit: stop any helper the snippet left running in its group.
        _kill(proc)
        # A child that left the process group can keep the pipes open; do not wait for it.
        for t in threads:
            t.join(timeout=2)
        if timed_out:
            return "timeout", f"no result after {timeout:g}s"
        out = out_s[0] if out_s else b""
        err = err_s[0] if err_s else b""
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
