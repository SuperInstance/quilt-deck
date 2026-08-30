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

from deck.cli import cmd_books  # noqa: E402

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
    src = inspect.getsource(cmd_books)
    assert "gen_day" not in src, "books view must not regenerate the day"
    assert "run_day" not in src, "books view must not re-run the day"
    assert "Fabric" not in src, "books view must not walk the fabric"
    assert "load_into_fabric" not in src, "books view must not walk the fabric"
    # it reads the export file — the ledger snapshot — and only that
    assert "export[\"books\"]" in src or "export['books']" in src
