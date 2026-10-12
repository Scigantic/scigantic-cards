"""Regressions found by stress-testing 0.3.0."""
import base64
import json
import os
import signal
import sys
import time
import zlib

import pytest

from scigantic_cards import Card, CardError, draft_card, verify_card


def _card(snippet: str) -> Card:
    return Card.from_dict({"name": "x", "access": [{"goal": "g", "snippet": snippet}]})


def test_lone_surrogate_is_a_card_error_not_a_later_crash():
    with pytest.raises(CardError, match="surrogate"):
        Card.from_json('{"name": "\\ud800"}')
    with pytest.raises(CardError, match="surrogate"):
        Card.from_json('{"name": "a", "samples": [{"path": "p", "fields": {"k\\udfff": 1}}]}')


def test_paired_surrogates_still_load():
    assert Card.from_json('{"name": "\\ud83d\\ude00"}').name == "\U0001F600"


def test_huge_integer_is_a_card_error():
    with pytest.raises(CardError):
        Card.from_json('{"name": "a", "approx_bytes": ' + "9" * 5000 + "}")


def test_duplicate_keys_are_rejected():
    with pytest.raises(CardError, match="duplicate key"):
        Card.from_json('{"name": "a", "name": "b"}')


def test_truncated_and_padded_zlib_are_rejected():
    z = zlib.compress(b'{"name":"aaaa"}')
    enc = lambda b: "z:" + base64.urlsafe_b64encode(b).decode()
    assert Card.from_b64(enc(z)).name == "aaaa"
    with pytest.raises(CardError, match="truncated"):
        Card.from_b64(enc(z[:-4]))
    with pytest.raises(CardError, match="after the end"):
        Card.from_b64(enc(z + b"junk"))


def _deep(tmp_path, levels):
    p = tmp_path
    for _ in range(levels):
        p = p / "d"
    p.mkdir(parents=True)
    (p / "f.csv").write_text("a\n1\n")


def test_folders_past_max_depth_make_counts_lower_bounds(tmp_path):
    _deep(tmp_path, 12)
    card = draft_card(str(tmp_path), max_depth=5)
    assert "deeper than 5 levels" in card.layout
    assert "lower bounds" in card.layout
    assert card.approx_items is None and card.approx_bytes is None


def test_default_depth_reaches_twelve_levels(tmp_path):
    _deep(tmp_path, 12)
    card = draft_card(str(tmp_path))
    assert card.approx_items == 1
    assert "lower bounds" not in card.layout


def test_timeout_holds_when_a_child_leaves_the_process_group(tmp_path):
    pidfile = tmp_path / "pid"
    snippet = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'], start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(40)\n"
    )
    t = time.time()
    try:
        _, res = verify_card(_card(snippet), timeout=2)
        elapsed = time.time() - t
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
    assert res[0].status == "timeout"
    assert elapsed < 10


def test_nul_byte_in_snippet_fails_cleanly():
    _, res = verify_card(_card("print(1)\x00"), timeout=10)
    assert res[0].status == "failed"


def test_large_snippet_runs(tmp_path):
    big = "x = 1\n" * 1_000_000 + "print('done')\n"
    _, res = verify_card(_card(big), timeout=30)
    assert res[0].status == "passed"


def test_lone_surrogate_in_snippet_via_dict_is_rejected():
    with pytest.raises(CardError):
        Card.from_dict({"name": "x", "access": [{"goal": "g", "snippet": "\ud800"}]})


def test_chatty_snippet_keeps_only_a_tail():
    snippet = "import sys\nfor _ in range(50): sys.stderr.write('e' * 1_000_000)\nsys.exit(3)\n"
    _, res = verify_card(_card(snippet), timeout=30)
    assert res[0].status == "failed"
    assert len(res[0].detail) <= 300


def test_helper_left_running_by_a_finished_snippet_is_stopped(tmp_path):
    pidfile = tmp_path / "pid"
    snippet = (
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
    )
    t = time.time()
    _, res = verify_card(_card(snippet), timeout=10)
    assert res[0].status == "passed"
    assert time.time() - t < 5
    pid = int(pidfile.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
