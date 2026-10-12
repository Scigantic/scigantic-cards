import json
import os
import sys
import time

import pytest

from scigantic_cards import Access, Card, verify_card
from scigantic_cards.cli import main
from conftest import make_card


def card_with(*snips, **kw):
    return make_card(access=[Access(f"s{i}", s, False, kw.get("lang", "python")) for i, s in enumerate(snips)],
                     checked_at="2000-01-01")


def test_pass_fail_and_flags():
    c, r = verify_card(card_with("print(1)", "raise SystemExit(3)", "import nope_missing"), today="2026-10-11")
    assert [x.status for x in r] == ["passed", "failed", "failed"]
    assert [a.verified for a in c.access] == [True, False, False]
    assert c.checked_at == "2026-10-11"
    assert "nope_missing" in r[2].detail


def test_timeout_kills_the_process_group(tmp_path):
    marker = tmp_path / "child_alive"
    snippet = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', \"import time,pathlib;time.sleep(2);pathlib.Path(r'{marker}').write_text('x')\"])\n"
        "time.sleep(60)"
    )
    t = time.time()
    _, r = verify_card(card_with(snippet), timeout=1)
    assert r[0].status == "timeout" and time.time() - t < 10
    time.sleep(3)
    assert not marker.exists()  # the grandchild was killed with the group


def test_skips_other_languages_and_empty_and_keeps_date():
    c, r = verify_card(card_with("ls -la", lang="bash"))
    assert r[0].status == "skipped" and c.checked_at == "2000-01-01" and not c.access[0].verified
    c, r = verify_card(card_with("   "))
    assert r[0].status == "skipped"


def test_does_not_mutate_input():
    c = card_with("print(1)")
    verify_card(c)
    assert not c.access[0].verified and c.checked_at == "2000-01-01"


def test_stdin_is_closed(tmp_path):
    _, r = verify_card(card_with("import sys; assert sys.stdin.read() == ''"), timeout=10)
    assert r[0].status == "passed"


def test_cwd_is_respected(tmp_path):
    (tmp_path / "here.txt").write_text("x")
    _, r = verify_card(card_with("assert open('here.txt').read() == 'x'"), cwd=str(tmp_path))
    assert r[0].status == "passed"


# ---------------------------------------------------------------- CLI


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_verify_refuses_without_run(tmp_path, capsys):
    p = tmp_path / "c.json"
    marker = tmp_path / "ran"
    p.write_text(card_with(f"open(r'{marker}', 'w').write('x')").to_json())
    code, _, err = run(["verify", str(p)], capsys)
    assert code == 2 and "--run" in err and not marker.exists()


def test_verify_run_write(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(card_with("print(1)", "1/0").to_json())
    code, out, _ = run(["verify", str(p), "--run"], capsys)
    assert code == 1 and "passed: s0" in out and "failed: s1" in out
    assert not json.loads(p.read_text())["access"][0]["verified"]  # not written without --write
    run(["verify", str(p), "--run", "--write"], capsys)
    saved = json.loads(p.read_text())
    assert [a["verified"] for a in saved["access"]] == [True, False] and saved["checked_at"] != "2000-01-01"


def test_verify_bad_file(tmp_path, capsys):
    assert run(["verify", str(tmp_path / "nope.json"), "--run"], capsys)[0] == 2


def test_encode_decode_round_trip(tmp_path, capsys, monkeypatch):
    p = tmp_path / "c.json"
    c = make_card(gotchas=["é"])
    p.write_text(c.to_json())
    for flags in ([], ["--compress"]):
        code, enc, _ = run(["encode", str(p), *flags], capsys)
        assert code == 0
        code, out, _ = run(["decode", enc.strip()], capsys)
        assert code == 0 and Card.from_json(out) == c
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(enc))
    assert Card.from_json(run(["decode"], capsys)[1]) == c
    assert run(["decode", "%%%"], capsys)[0] == 2


def test_init_writes_file_and_hints(tmp_path, capsys):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.csv").write_text("x,y\n1,2\n")
    out = tmp_path / "card.json"
    code, stdout, err = run(["init", str(tmp_path / "d"), "-o", str(out), "--name", "mine"], capsys)
    assert code == 0 and stdout == "" and "fill in" in err
    card = Card.from_json(out.read_text())
    assert card.name == "mine" and card.approx_items == 1
    assert run(["lint", str(out)], capsys)[0] == 1  # identity and delta still missing


def test_init_errors(tmp_path, capsys):
    assert run(["init", str(tmp_path / "missing")], capsys)[0] == 2
    assert run(["init", str(tmp_path), "--embed-bytes", "99999"], capsys)[0] == 2
    assert run(["init", str(tmp_path), "-o", str(tmp_path / "no" / "such" / "dir" / "c.json")], capsys)[0] == 2
