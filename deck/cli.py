"""deck.cli -- the operator surface.

  python3 -m deck new [--out deck.quf]        commission a cold graph QUF
  python3 -m deck day [--seed 42] [--sets 5] [--backend python|esp32|fpga]
                      [--export day.json] [--quf day.quf] [--summary]
  python3 -m deck books DAY.json|DAY.quf     the balance ledger of the day
                                          (a QUF archive is read back from
                                          its app.deck section)
  python3 -m deck verify DAY.quf              structural + conservation check
  python3 -m deck warm DAY.quf --ops more.json [--out day2.quf]
  python3 -m deck latest               create symlink to latest day export
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from . import qufio
from .daylog import gen_day, replay
from .fabric import Fabric
from .graph import NCELL, load_into_fabric, quf_doc
from .ledger import DeckLedger

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))


def cmd_new(args):
    fab = Fabric(ncell=NCELL)
    led = replay(fab, DeckLedger(), [], commission=True)
    doc = quf_doc(fab)
    path = args.out or "deck.quf"
    data = qufio.save_path(path, doc, led.books.snapshot())
    print(f"commissioned graph -> {path} ({len(data)} B, "
          f"sha256 {hashlib.sha256(data).hexdigest()[:16]})")
    return 0


def cmd_day(args):
    day = {"date": args.date, "seed": args.seed, "sets": args.sets,
           "backend": args.backend}
    log = gen_day(seed=args.seed, sets=args.sets)
    if args.backend == "fpga":
        from .cosim import run
        ok, report = run(seed=args.seed, sets=args.sets)
        print("\n".join(report))
        return 0 if ok else 1
    from .backends import run_day
    export = run_day(log, backend=args.backend, day=day,
                     out_quf=args.quf)
    if args.export:
        with open(args.export, "w") as fh:
            json.dump(export, fh, indent=1, sort_keys=True)
        print(f"export -> {args.export}")
    b = export["books"]["conservation"]
    if getattr(args, "summary", False):
        bal = "ok" if b["balanced"] else "VIOLATION"
        print(f"day {args.date} seed={args.seed} sets={args.sets} "
              f"backend={args.backend}: landed {b['landed_total']} "
              f"({b['balanced'] and 'balanced' or 'VIOLATION'}), "
              f"{len(export['books']['refusals'])} refusals, "
              f"quf {export['quf_bytes']}B {export['quf_sha256'][:16]}")
    else:
        print(f"day: {b['landed_total']} fish landed | totes {b['totes_total']} "
              f"| hold {b['hold_total']} | unbooked {b['unbooked']} "
              f"| balanced {b['balanced']}")
        print(f"quf: {export['quf_bytes']} B sha256 {export['quf_sha256'][:16]}")
        for r in export["books"]["refusals"]:
            print(f"REFUSED [{r['reason']}] {r['detail']}")
    return 0


def _books_from_path(path: str) -> dict:
    """Books from a day export (json) or a QUF archive (app.deck section).

    The QUF is the single source of truth -- the whole deck state travels in
    one QUF -- so the operator can read the books straight from the archive
    without keeping the day export around."""
    with open(path, "rb") as fh:
        data = fh.read()
    if path.endswith(".json"):
        return json.loads(data)["books"]
    doc, app = qufio.split_archive(data)
    if "conservation" not in app:
        raise SystemExit(f"{path}: no app.deck books in container")
    return app


def cmd_books(args):
    b = _books_from_path(args.DAY)
    print("== F/V EILEEN — the books ==")
    c = b["conservation"]
    print(f"landed {c['landed_total']} = totes {c['totes_total']} + hold "
          f"{c['hold_total']} + unbooked {c['unbooked']} — "
          f"{'BALANCED' if c['balanced'] else 'VIOLATION'}")
    print(f"{'species':8s} {'landed':>8s} {'totes':>8s} {'hold':>8s} {'efficiency':>10s}")
    for sp in ("pink", "chum", "king", "coho"):
        landed = b['landed'][sp]
        totes = sum(t["n"] for t in b["totes"].values() if t["sp"] == sp)
        hold = b['hold'][sp]
        efficiency = (totes / landed * 100) if landed > 0 else 0
        print(f"{sp:8s} {landed:8d} {totes:8d} {hold:8d} {efficiency:9.1f}%")
    print(f"moves: {len(b['moves'])} booked")
    if b["refusals"]:
        print("refusals:")
        for r in b["refusals"]:
            print(f"  [{r['reason']}] {r['detail']}")
    else:
        print("refusals: none — clean day")
    for h in b["hook_sets"]:
        print(f"set {h['set']}: {h['hooks_visible']} hooks × 1.5 fm = "
              f"{h['depth_fm']} fm")
    return 0


def cmd_verify(args):
    with open(args.DAY, "rb") as fh:
        data = fh.read()
    if not qufio.verify(data):
        print("STRUCTURAL: FAIL (not a clean QUF)")
        return 1
    doc, app = qufio.split_archive(data)
    print(f"STRUCTURAL: clean QUF ({len(data)} B, {doc['header']['cell_count']} "
          f"cells, {len(doc['edges'])} edges)")
    if "conservation" in app:
        c = app["conservation"]
        ok = c.get("balanced")
        print(f"CONSERVATION: landed {c['landed_total']} vs totes "
              f"{c['totes_total']} + hold {c['hold_total']} — "
              f"{'BALANCED' if ok else 'VIOLATION'}")
        return 0 if ok else 1
    print("CONSERVATION: no app.deck books in container")
    return 0


def cmd_warm(args):
    with open(args.quf, "rb") as fh:
        data = fh.read()
    doc, app = qufio.split_archive(data)
    fab = Fabric(ncell=NCELL)
    load_into_fabric(fab, doc, restore_walk=True)
    with open(args.ops) as fh:
        log = json.load(fh)
    led = replay(fab, DeckLedger(), log, commission=False)
    out = qufio.save_path(args.out or "day2.quf", quf_doc(fab),
                          led.books.snapshot())
    print(f"warm start + ops -> {args.out or 'day2.quf'} ({len(out)} B)")
    return 0


def cmd_console(args):
    import http.server
    import socketserver
    webui = os.path.join(HERE, "webui")
    os.chdir(webui)
    class H(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()
    with socketserver.TCPServer(("127.0.0.1", args.port), H) as httpd:
        print(f"console: http://127.0.0.1:{args.port}/index.html "
              f"(serving {webui}; Ctrl-C to stop)")
        httpd.serve_forever()
    return 0

def cmd_latest(args):
    """Create/update latest.json symlink to most recent day export."""
    from pathlib import Path
    import time
    
    if args.day:
        target = Path(args.day)
        if not target.exists():
            print(f"ERROR: {args.day} does not exist")
            return 1
        if not (target.name.startswith("day-") and target.name.endswith(".json")):
            print(f"ERROR: {args.day} does not look like a day export")
            return 1
    else:
        # Look for day-*.json files
        day_files = list(Path.cwd().glob("day-*.json"))
        if not day_files:
            print("ERROR: no day-*.json files found in current directory")
            return 1
        # Sort by modification time, newest first
        day_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        target = day_files[0]
    
    # Create/update the symlink
    latest_json = Path("latest.json")
    try:
        if latest_json.exists():
            if latest_json.is_symlink():
                latest_json.unlink()
            else:
                print("ERROR: latest.json exists and is not a symlink")
                return 1
        latest_json.symlink_to(target)
        mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(target.stat().st_mtime))
        print(f"latest.json -> {target.name} (modified {mtime})")
        return 0
    except Exception as e:
        print(f"ERROR: failed to create symlink: {e}")
        return 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="deck")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new")
    s.add_argument("--out", default=None)

    s = sub.add_parser("day")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--sets", type=int, default=5)
    s.add_argument("--date", default="2026-08-29")
    s.add_argument("--backend", choices=["python", "esp32", "fpga"],
                   default="python")
    s.add_argument("--export", default=None)
    s.add_argument("--quf", default=None)
    s.add_argument("--summary", action="store_true",
                   help="compact one-line summary output")

    s = sub.add_parser("books")
    s.add_argument("DAY")

    s = sub.add_parser("verify")
    s.add_argument("DAY")

    s = sub.add_parser("warm")
    s.add_argument("quf")
    s.add_argument("--ops", required=True)
    s.add_argument("--out", default=None)

    s = sub.add_parser("console")
    s.add_argument("--port", type=int, default=8717)

    s = sub.add_parser("latest")
    s.add_argument("--day", default=None)

    args = p.parse_args(argv)
    return {"new": cmd_new, "day": cmd_day, "books": cmd_books,
            "verify": cmd_verify, "warm": cmd_warm,
            "console": cmd_console, "latest": cmd_latest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
