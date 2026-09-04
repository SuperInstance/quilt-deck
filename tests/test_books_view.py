"""Golden-view test: the operator books view is snapshot-guarded.

Expert nudge 2026-08-30: the books view is the first consumer rendering
DERIVED numbers to a human — regressions should be caught the way QUF
byte regressions are. One fixture export, one committed golden rendering.

Reference discipline (also booked this nudge): cmd_books reads ONLY
export["books"] — the ledger snapshot written once by run_day
(deck/backends.py) and serialized into the QUF archive. No parallel
deck-graph walk exists, so the view and the archive share a single
source of truth by construction. This test pins the rendering so any
format or derivation change is a deliberate, reviewed diff.
"""
import json
import os
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deck.cli import _books_from_path, cmd_books  # noqa: E402

GOLDEN = Path(__file__).parent / "golden" / "books_seed7.txt"


def test_books_view_golden(capsys):
    fixture = Path(__file__).parent / "golden" / "fixture_day_seed7.json"
    export = json.loads(fixture.read_text())
    rc = cmd_books(Namespace(DAY=str(fixture)))
    assert rc == 0
    got = capsys.readouterr().out
    want = GOLDEN.read_text()
    assert got == want, (
        "books view drifted from golden rendering:\n--- golden ---\n%s\n"
        "--- got ---\n%s" % (want, got))


def test_books_view_derives_from_ledger_snapshot_only():
    """Probes (1): the view must read the ledger snapshot and nothing else.
    If cmd_books ever grows a second derivation path (parallel graph walk),
    this audit fails and forces single-sourcing."""
    import inspect
    src = inspect.getsource(cmd_books) + inspect.getsource(_books_from_path)
    assert "gen_day" not in src, "books view must not regenerate the day"
    assert "run_day" not in src, "books view must not re-run the day"
    assert "Fabric" not in src, "books view must not walk the fabric"
    assert "load_into_fabric" not in src, "books view must not walk the fabric"
    # it reads the export file or the QUF's app.deck section — both are the
    # same ledger snapshot — and only that
    assert "_books_from_path" in inspect.getsource(cmd_books)
    assert 'json.loads(data)["books"]' in src or "split_archive" in src


def test_books_view_from_quf_archive():
    """`deck books` reads the books straight out of a QUF archive (app.deck)
    and renders the identical view as from the day export -- the archive is
    the single source of truth, no day.json required."""
    import subprocess
    import sys
    import tempfile
    import os
    ROOT = str(Path(__file__).resolve().parents[1])
    quf = os.path.join(tempfile.mkdtemp(), "day.quf")
    subprocess.run([sys.executable, "-m", "deck", "day", "--seed", "7",
                    "--sets", "3", "--quf", quf, "--export", quf + ".json"],
                   cwd=ROOT, check=True, capture_output=True)
    r_quf = subprocess.run([sys.executable, "-m", "deck", "books", quf],
                           cwd=ROOT, capture_output=True, text=True)
    r_json = subprocess.run([sys.executable, "-m", "deck", "books", quf + ".json"],
                            cwd=ROOT, capture_output=True, text=True)
    assert r_quf.returncode == 0, r_quf.stderr
    assert r_quf.stdout == r_json.stdout, "QUF and json books views differ"
    assert "BALANCED" in r_quf.stdout and "refusals" in r_quf.stdout
