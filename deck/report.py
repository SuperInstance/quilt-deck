#!/usr/bin/env python3
"""The Captain's Report: render a quilt-deck day-export as markdown.

Reads the day-export JSON produced by ``deck/cli.py day --export`` (shape
per docs/DAY-EXPORT-SCHEMA.md v1 — the authoritative contract) and writes
a tight, numbers-forward wheelhouse report. Nobody keeps records; the
camera keeps them — this file is that ledger read back aloud.

Pure stdlib. Usage::

    python3 deck/report.py EXPORT.json [-o OUT.md]
    python3 deck/report.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys

SPECIES_ORDER = ("pink", "chum", "king", "coho")

#: One human sentence per refusal reason in the closed set (schema v1).
REFUSAL_SENTENCES = {
    "INSUFFICIENT_CREDIT": "moved more fish than the tote held",
    "TOTE_OVERFLOW": "the move would have overfilled the destination tote",
    "PHANTOM_HOLD_ENTRY": "the books gained a hold entry no move carried",
    "SPECIES_MISMATCH": "the fish broke the tote's ground-truth species rule",
    "UNKNOWN_CELL": "the move named a cell that is not in the graph",
    "NOT_A_MOVE": "the op was not a tote-to-tote or tote-to-hold move",
    "DOUBLE_MOVE": "the same move was booked twice",
}


def _d(value):
    """Return *value* when it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _l(value):
    """Return *value* when it is a list, else an empty list."""
    return value if isinstance(value, list) else []


def _num(value):
    """Return *value* as an int when it honestly is one, else 0."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _fmt_int(n):
    """Format an integer with thousands separators; None renders as em dash."""
    if n is None:
        return "—"
    if isinstance(n, int) and not isinstance(n, bool):
        return f"{n:,}"
    return str(n)


def _fmt_float(x):
    """Format a float compactly; None renders as em dash."""
    if x is None:
        return "—"
    if isinstance(x, (int, float)):
        return f"{x:g}"
    return str(x)


def _render_header(export):
    """Title line plus the one-line summary of the day."""
    day = _d(export.get("day"))
    books = _d(export.get("books"))
    cons = _d(books.get("conservation"))
    landed = _d(books.get("landed"))
    sets = day.get("sets")
    if sets is None:
        sets = sum(1 for e in _l(export.get("daylog"))
                   if _d(e).get("t") == "set")
    total = cons.get("landed_total")
    if total is None:
        total = sum(_num(v) for v in landed.values())
    sha = str(export.get("quf_sha256") or "")[:12] or "—"
    balanced = cons.get("balanced")
    verdict = "—" if balanced is None else ("YES" if balanced else "NO")
    return [
        f"# Captain's Report — F/V EILEEN — {day.get('date', '—')}",
        "",
        (f"{_fmt_int(sets)} sets · {_fmt_int(total)} fish · "
         f"backend {day.get('backend', '—')} · QUF {sha} · "
         f"balanced {verdict}"),
        "",
    ]


def _render_day(export):
    """One bullet per set from the daylog, each with its hook depth line."""
    hooks = {}
    for h in _l(_d(export.get("books")).get("hook_sets")):
        h = _d(h)
        if "set" in h:
            hooks[h["set"]] = h
    lines = ["## The day", ""]
    wrote = False
    for entry in _l(export.get("daylog")):
        entry = _d(entry)
        if entry.get("t") != "set":
            continue
        setno = entry.get("set", "—")
        mix = " · ".join(
            f"{_fmt_int(_d(f).get('n'))} {_d(f).get('sp', '—')}"
            for f in _l(entry.get("fish"))
        ) or "—"
        lines.append(f"- **Set {setno}** — {mix}")
        hook = hooks.get(setno)
        if hook is not None:
            lines.append(
                f"  hooks visible {_fmt_int(hook.get('hooks_visible'))}"
                f" · depth {_fmt_float(hook.get('depth_fm'))} fm"
            )
        wrote = True
    if not wrote:
        lines.append("- —")
    lines.append("")
    return lines


def _conservation_line(books):
    """The conservation invariant, balanced quietly or violated loudly."""
    cons = _d(books.get("conservation"))
    lt = cons.get("landed_total")
    tt = cons.get("totes_total")
    ht = cons.get("hold_total")
    have_all = all(isinstance(v, int) and not isinstance(v, bool)
                   for v in (lt, tt, ht))
    if have_all:
        unbooked = lt - tt - ht
        books_line = (f"landed {_fmt_int(lt)} = totes {_fmt_int(tt)} + "
                      f"hold {_fmt_int(ht)} + unbooked "
                      f"{_fmt_int(unbooked)}")
    else:
        unbooked = None
        books_line = (f"landed {_fmt_int(lt)} = totes {_fmt_int(tt)} + "
                      f"hold {_fmt_int(ht)} + unbooked —")
    balanced = cons.get("balanced")
    if balanced is True:
        return f"{books_line} — **balanced**"
    if balanced is False:
        gap = abs(unbooked) if unbooked is not None else "—"
        return (f"**CONSERVATION VIOLATION — THE BOOKS DO NOT BALANCE:** "
                f"landed {_fmt_int(lt)} ≠ totes {_fmt_int(tt)} + hold "
                f"{_fmt_int(ht)} + unbooked {_fmt_int(unbooked)}; "
                f"{_fmt_int(gap)} fish unaccounted for.")
    return f"{books_line} — balanced —"


def _render_books(export):
    """The ledger table (species | landed | totes | hold | moves) + verdict."""
    books = _d(export.get("books"))
    landed = _d(books.get("landed"))
    hold = _d(books.get("hold"))
    totes_by_sp = {}
    for tote in _d(books.get("totes")).values():
        tote = _d(tote)
        if tote.get("sp") is not None:
            totes_by_sp[tote["sp"]] = (totes_by_sp.get(tote["sp"], 0)
                                       + _num(tote.get("n")))
    moves_by_sp = {}
    for mv in _l(books.get("moves")):
        mv = _d(mv)
        if mv.get("sp") is not None:
            moves_by_sp[mv["sp"]] = (moves_by_sp.get(mv["sp"], 0)
                                     + _num(mv.get("n")))
    species = {sp for sp in landed if isinstance(sp, str)}
    species |= {sp for sp in hold if isinstance(sp, str)}
    species |= set(totes_by_sp) | set(moves_by_sp)
    ordered = [s for s in SPECIES_ORDER if s in species]
    ordered += sorted(species - set(SPECIES_ORDER))
    lines = ["## The books", ""]
    if ordered:
        lines.append("| species | landed | totes | hold | moves |")
        lines.append("|---|---:|---:|---:|---:|")
        totals = [0, 0, 0, 0]
        for sp in ordered:
            row = (_num(landed.get(sp)), totes_by_sp.get(sp, 0),
                   _num(hold.get(sp)), moves_by_sp.get(sp, 0))
            totals = [a + b for a, b in zip(totals, row)]
            lines.append(f"| {sp} | {_fmt_int(row[0])} | {_fmt_int(row[1])}"
                         f" | {_fmt_int(row[2])} | {_fmt_int(row[3])} |")
        lines.append(f"| **total** | **{_fmt_int(totals[0])}** | "
                     f"**{_fmt_int(totals[1])}** | **{_fmt_int(totals[2])}**"
                     f" | **{_fmt_int(totals[3])}** |")
    else:
        lines.append("—")
    lines.append("")
    lines.append(_conservation_line(books))
    lines.append("")
    return lines


def _render_refusals(export):
    """Refused ops as a table, each reason carrying its human sentence."""
    refusals = _l(_d(export.get("books")).get("refusals"))
    lines = ["## Refusals", ""]
    if not refusals:
        lines.append("none — clean day")
        lines.append("")
        return lines
    lines.append("| t | op | reason | detail |")
    lines.append("|---|---|---|---|")
    for r in refusals:
        r = _d(r)
        reason = r.get("reason", "—")
        why = REFUSAL_SENTENCES.get(reason, "reason outside the closed set")
        lines.append(f"| {_fmt_int(r.get('t'))} | {r.get('op', '—')} | "
                     f"{reason} — {why} | {r.get('detail', '—')} |")
    lines.append("")
    return lines


def _edge_histogram(edge):
    """An edge's ladder buckets in compact form, e.g. [412 3 0 0 0 0 0 0]."""
    return "[" + " ".join(str(b) for b in _l(edge.get("buckets"))) + "]"


def _edge_readout(cell):
    """All of a cell's edges as slot→peer histogram readouts."""
    parts = []
    for e in _l(cell.get("edges")):
        e = _d(e)
        parts.append(f"{e.get('slot', '—')}→{e.get('peer', '—')} "
                     f"{_edge_histogram(e)}")
    return "; ".join(parts)


def _cell_trained(cell):
    """True when any edge bucket of the cell is nonzero."""
    return any(any(_l(_d(e).get("buckets")))
               for e in _l(cell.get("edges")))


def _render_fabric(export):
    """Cells that acted or trained; ladders, fires, and the tick count."""
    fire_counts = {}
    for f in _l(_d(export.get("books")).get("fires")):
        cell = _d(f).get("cell")
        if cell is not None:
            fire_counts[cell] = fire_counts.get(cell, 0) + 1
    lines = ["## Fabric", ""]
    rows = []
    for cell in _l(export.get("cells")):
        cell = _d(cell)
        name = cell.get("name", f"cell {cell.get('id', '—')}")
        if not (_num(cell.get("act")) or _cell_trained(cell)):
            continue
        rows.append(f"| {name} | {_fmt_int(cell.get('act'))} | "
                    f"{_edge_readout(cell) or '—'} | "
                    f"{_fmt_int(fire_counts.get(name))} |")
    if rows:
        lines.append("| cell | act | edges — bucket histograms | fires |")
        lines.append("|---|---:|---|---:|")
        lines.extend(rows)
    else:
        lines.append("no cell acted or trained this day")
    ticks = _d(export.get("day")).get("ticks")
    if ticks is None:
        ticks = export.get("ticks")
    if ticks is not None:
        lines.append("")
        lines.append(f"Fabric executed {_fmt_int(ticks)} ticks this day.")
    lines.append("")
    return lines


def _fmt_dial_write(w):
    """One dial write as plain text, e.g. ``ETA_F=3 on SOUNDER``."""
    name = w.get("dial") or w.get("name")
    if name is None:
        addr = w.get("addr")
        name = f"addr {addr}" if addr is not None else "dial"
    target = w.get("cell") or w.get("target")
    txt = f"{name}={_fmt_int(w.get('value'))}"
    return f"{txt} on {target}" if target else txt


def _render_night(export):
    """What the night cron's A/B verdict did, phrased plainly."""
    entries = [e for e in _l(export.get("daylog"))
               if _d(e).get("t") == "night"]
    if not entries:
        return []
    lines = ["## Night", "", "The computer had the boat to itself.", ""]
    for e in entries:
        e = _d(e)
        verdict = e.get("verdict")
        writes = (_l(e.get("writes")) or _l(e.get("dial_writes"))
                  or _l(e.get("dials")))
        wtxt = ", ".join(_fmt_dial_write(_d(w))
                         for w in writes if _d(w))
        v = str(verdict).lower() if verdict is not None else ""
        if v.startswith("promote"):
            line = ("A/B verdict: **promote** — the challenger's dials went "
                    f"to the live cell ({wtxt or 'no writes listed'}).")
        elif v.startswith("rollback"):
            line = ("A/B verdict: **rollback** — nothing written; rollback "
                    "is the null action.")
            if wtxt:
                line += f" Writes listed anyway: {wtxt}"
        else:
            line = f"A/B verdict: {verdict if verdict is not None else '—'}"
            if wtxt:
                line += f" — dial writes: {wtxt}"
        lines.append(line)
    lines.append("")
    return lines


def render_report(export: dict) -> str:
    """Render a day-export dict (DAY-EXPORT-SCHEMA.md v1) as markdown.

    Never raises on missing keys — absent data renders as an em dash.
    """
    sections = [
        _render_header(export),
        _render_day(export),
        _render_books(export),
        _render_refusals(export),
        _render_fabric(export),
        _render_night(export),
    ]
    joined = "\n".join(line for section in sections for line in section)
    return joined.rstrip() + "\n"


def _sample_export():
    """A balanced 2-set, 1-refusal day following DAY-EXPORT-SCHEMA.md v1."""
    return {
        "day": {"date": "2026-08-29", "sets": 2, "seed": 42,
                "backend": "python",
                "conformance": {"cold_quf_sha256": "ab" * 32,
                                "match": True}},
        "quf_sha256": "feedf00d" + "0" * 56,
        "quf_bytes": 123456,
        "cells": [
            {"id": 0, "name": "TOTE-PORT", "act": 1234, "refr": 0,
             "dials": [0] * 16,
             "edges": [
                 {"slot": 0, "peer": 15, "base": 0,
                  "buckets": [412, 3, 0, 0, 0, 0, 0, 0], "wh": 1,
                  "age": 37},
                 {"slot": 1, "peer": 1, "base": 0, "buckets": [0] * 8,
                  "wh": 0, "age": 0}]},
            {"id": 1, "name": "SOUNDER", "act": 0, "refr": 0,
             "dials": [0] * 16, "edges": []},
        ],
        "books": {
            "landed": {"pink": 837, "chum": 270, "king": 14, "coho": 31},
            "totes": {"TOTE-PORT": {"sp": "pink", "n": 12, "cap": 400}},
            "hold": {"pink": 825, "chum": 270, "king": 14, "coho": 31},
            "moves": [
                {"t": 1, "from": "TOTE-PORT", "to": "HOLD", "n": 825,
                 "sp": "pink"},
                {"t": 1, "from": "TOTE-HOLD", "to": "HOLD", "n": 270,
                 "sp": "chum"},
                {"t": 1, "from": "TOTE-STBD-F", "to": "HOLD", "n": 14,
                 "sp": "king"},
                {"t": 1, "from": "TOTE-STBD-A", "to": "HOLD", "n": 31,
                 "sp": "coho"},
            ],
            "refusals": [
                {"t": 2, "op": "move", "reason": "INSUFFICIENT_CREDIT",
                 "detail": "TOTE-PORT holds 12 pink; debit of 400 refused"},
            ],
            "conservation": {"landed_total": 1152, "totes_total": 12,
                             "hold_total": 1140, "balanced": True},
            "hook_sets": [
                {"set": 1, "hooks_visible": 30, "depth_fm": 45.0},
                {"set": 2, "hooks_visible": 24, "depth_fm": 36.0},
            ],
            "fires": [{"cell": "TOTE-PORT", "dat": 24576, "tick": 37}],
        },
        "daylog": [
            {"t": "set", "set": 1,
             "fish": [{"sp": "pink", "n": 612}, {"sp": "chum", "n": 180},
                      {"sp": "king", "n": 14}, {"sp": "coho", "n": 31}]},
            {"t": "set", "set": 2,
             "fish": [{"sp": "pink", "n": 225}, {"sp": "chum", "n": 90}]},
            {"t": "night", "verdict": "rollback", "writes": []},
        ],
    }


def _selftest():
    """Render the sample export and assert the report's shape."""
    report = render_report(_sample_export())
    assert isinstance(report, str), "render_report must return str"
    needles = [
        "# Captain's Report — F/V EILEEN — 2026-08-29",
        "## The day",
        "## The books",
        "## Refusals",
        "## Fabric",
        "## Night",
        "612 pink · 180 chum · 14 king · 31 coho",
        "landed 1,152 = totes 12 + hold 1,140 + unbooked 0 — **balanced**",
        "INSUFFICIENT_CREDIT",
        "moved more fish than the tote held",
        "[412 3 0 0 0 0 0 0]",
        "rollback",
        "1,152 fish",
    ]
    for needle in needles:
        assert needle in report, f"missing from report: {needle!r}"
    bare = render_report({})
    assert "## The books" in bare and "—" in bare, \
        "bare export must render dashes, not raise"
    return 0


def main(argv=None) -> int:
    """CLI entry point: render EXPORT.json to stdout or -o OUT.md."""
    parser = argparse.ArgumentParser(
        description="Render a quilt-deck day-export as the Captain's Report.")
    parser.add_argument("export", nargs="?", metavar="EXPORT.json",
                        help="day-export JSON file to render")
    parser.add_argument("-o", "--out", metavar="OUT.md",
                        help="write the report here instead of stdout")
    parser.add_argument("--selftest", action="store_true",
                        help="run the inline selftest and exit 0/1")
    args = parser.parse_args(argv)
    if args.selftest:
        try:
            _selftest()
        except AssertionError as exc:
            print(f"selftest FAILED: {exc}", file=sys.stderr)
            return 1
        print("selftest OK")
        return 0
    if args.export is None:
        parser.error("EXPORT.json is required (or pass --selftest)")
    with open(args.export, encoding="utf-8") as fh:
        export = json.load(fh)
    report = render_report(export)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
