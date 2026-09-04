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


def test_books_view_refuses_structurally_dirty_quf():
    """DEVIL nudge 2026-09-04: a structurally broken archive is REFUSED
    loudly, never rendered as a partial/wrong books view.

    Honest scope: qufio.verify is a structural parse (magic, section
    table, lengths) -- the QUF format has NO content checksum. A flipped
    byte inside a payload that still parses renders as-is; that residual
    is the documented trust boundary in README, and fixing it means a
    checksum section in the QUF spec (treaty-level change), not a patch
    here. This test pins both sides of that line."""
    import subprocess
    import sys
    import tempfile
    import os
    ROOT = str(Path(__file__).resolve().parents[1])
    quf = os.path.join(tempfile.mkdtemp(), "day.quf")
    subprocess.run([sys.executable, "-m", "deck", "day", "--seed", "7",
                    "--sets", "3", "--quf", quf],
                   cwd=ROOT, check=True, capture_output=True)
    data = open(quf, "rb").read()
    # structural break: truncated tail (section table/lengths no longer add up)
    p = quf + ".truncated"
    with open(p, "wb") as fh:
        fh.write(data[:-100])
    r = subprocess.run([sys.executable, "-m", "deck", "books", p],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode != 0, "truncated archive rendered as books!"
    assert "not a clean QUF" in (r.stdout + r.stderr), r.stdout + r.stderr
    # structural break: magic corrupted
    p2 = quf + ".badmagic"
    with open(p2, "wb") as fh:
        fh.write(b"XXXX" + data[4:])
    r2 = subprocess.run([sys.executable, "-m", "deck", "books", p2],
                        cwd=ROOT, capture_output=True, text=True)
    assert r2.returncode != 0, "bad-magic archive rendered as books!"
    # documented residual: a payload byte-flip that still parses DOES render
    # (no content checksum). Pinned here so the boundary is visible, with a
    # harmless target: flip a padding byte only if one exists, else the last
    # body byte of the app.deck JSON section's tail whitespace is absent, so
    # flip a byte in the final zero padding region.
    pad = data.rstrip(b"\x00")
    assert len(pad) < len(data), "expected EOF zero padding in archive QUF"
    flip = len(pad) + (len(data) - len(pad)) // 2
    p3 = quf + ".padflip"
    with open(p3, "wb") as fh:
        fh.write(data[:flip] + bytes([data[flip] ^ 0x01]) + data[flip + 1:])
    r3 = subprocess.run([sys.executable, "-m", "deck", "books", p3],
                        cwd=ROOT, capture_output=True, text=True)
    assert r3.returncode == 0, "padding flip should still render (documented)"
