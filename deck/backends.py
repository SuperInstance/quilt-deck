"""deck.backends -- backend selection: one day log, three engines.

python : the soft fabric engine (deck.fabric) -- always works, the model
esp32 : esp32/build/deckbridge over quilt-vm-c (host loopback via the
         vendored firmware VM; the real ESP32 backend surface)
fpga   : iverilog cosim against quilt-verilog's q_serfabric_top (deck.cosim)

All three consume the SAME day log through the SAME ledger (conservation
is backend-independent by construction) and must produce byte-identical
final QUF files. `run_day` returns the export dict (schema:
docs/DAY-EXPORT-SCHEMA.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from typing import Dict, List, Optional

from .daylog import replay
from .fabric import Fabric, flit_frame
from .graph import CELL_NAMES, NCELL, quf_doc
from .ledger import DeckLedger

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ESP32_BIN = os.path.join(ROOT, "esp32", "build", "deckbridge")


def day_export(fab: Fabric, led: DeckLedger, backend: str, day: dict,
               quf_bytes: bytes, extra: Optional[dict] = None,
               daylog: Optional[List[dict]] = None) -> dict:
    snap = fab.snapshot()
    cells = []
    for c in snap["cells"]:
        cells.append({
            "id": c["id"], "name": CELL_NAMES[c["id"]], "act": c["act"],
            "refr": c["refr"], "dials": c["dials"],
            "edges": [
                {"slot": e["slot"], "peer": CELL_NAMES.get(e["peer"], "HOST"),
                 "base": e["base"], "buckets": e["buckets"],
                 "wh": e["wh"], "age": e["age"]}
                for e in c["edges"] if e["valid"]
            ],
        })
    books = led.books.snapshot()
    books["fires"] = [{"cell": CELL_NAMES.get(f["cell"], f["cell"]),
                       "dat": f["dat"], "tick": None} for f in fab.fires]
    export = {
        "day": day,
        "quf_sha256": hashlib.sha256(quf_bytes).hexdigest(),
        "quf_bytes": len(quf_bytes),
        "cells": cells,
        "books": books,
        "daylog": daylog or [],
    }
    if extra:
        export.update(extra)
    return export


def run_python(log: List[dict], day: dict, warm_dials=None):
    fab = Fabric(ncell=NCELL)
    if warm_dials:
        for i in range(NCELL):
            fab.cells[i].dials = list(warm_dials[i])
    led = replay(fab, DeckLedger(), log, commission=True)
    doc = quf_doc(fab)
    from . import qufio
    quf_bytes = qufio.quf.build(doc)
    archive = qufio.build_archive(doc, led.books.snapshot())
    return fab, led, doc, quf_bytes, archive


# ---------------------------------------------------------------- esp32 --

class Esp32Bridge:
    """Drive deckbridge over stdin/stdout (host loopback of the firmware
    VM path). The bridge emits egress events as they happen; the python
    side replays the same day through the same ledger and feeds the SAME
    flit stream (the ledger is the single booking authority)."""

    def __init__(self, binary: str = ESP32_BIN):
        if not os.path.exists(binary):
            raise FileNotFoundError(
                f"{binary} missing -- run: make -C esp32")
        self.p = subprocess.Popen([binary], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        # the bridge streams events as they happen; a full-duplex reader
        # thread prevents the 64K pipe deadlock (write-write mutual block)
        self.events_q: "queue.Queue[str]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.banner = self._next_line(timeout=10).strip()

    def _read_loop(self):
        for line in self.p.stdout:
            self.events_q.put(line.rstrip("\n"))
        self.events_q.put(None)

    def _next_line(self, timeout: float = 60.0):
        try:
            return self.events_q.get(timeout=timeout)
        except queue.Empty:
            return ""

    def _cmd(self, line: str):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def flit(self, f) -> Optional[str]:
        self._cmd("F %d %d %d %d %d %d %d" %
                  (f.op, f.src, f.dst, f.a0, f.a1, f.a2, f.dat))
        return None  # events read via drain()

    def tick(self, n: int):
        for _ in range(n):
            self._cmd("T 1")

    def events(self) -> List[dict]:
        """non-blocking: nothing buffered for pipes this small; the driver
        reads events after each phase via drain()."""
        return []

    def drain(self, quiet_cycles: int = 40, timeout: float = 300.0) -> List[dict]:
        """Read until the stream is quiet (event-driven days have no fixed
        count; quiescence = quiet_cycles consecutive 50ms empty polls)."""
        events: List[dict] = []
        quiet = 0
        deadline = time.time() + timeout
        while quiet < quiet_cycles and time.time() < deadline:
            try:
                line = self.events_q.get(timeout=0.05)
            except queue.Empty:
                quiet += 1
                continue
            quiet = 0
            if line is None:
                break
            line = line.strip()
            if line.startswith("E "):
                parts = [int(x) for x in line[2:].split()]
                events.append({"kind": "egress", "flit": parts})
            elif line.startswith("! "):
                _, c, d = line.split()
                events.append({"kind": "fire", "cell": int(c), "dat": int(d)})
            elif line.startswith("#"):
                events.append({"kind": "info", "text": line})
        return events

    def dump(self) -> str:
        self._cmd("D")
        lines = []
        while len(lines) < 90:            # 15 cells x (DC + DA + 4 DE)
            line = self._next_line()
            if not line:
                break
            lines.append(line)
        return "\n".join(lines)

    def save(self, path: str) -> str:
        self._cmd("S %s" % path)
        return self._next_line()

    def close(self):
        try:
            self._cmd("E")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def run_esp32(log: List[dict], day: dict):
    """Replay the day on the esp32 bridge; verify against the soft model."""
    fab_ref = Fabric(ncell=NCELL)
    led_ref = replay(fab_ref, DeckLedger(), log, commission=True)

    br = Esp32Bridge()
    # replay against a SHADOW fabric: replay() itself drives the fabric;
    # on_flits is an OBSERVER that mirrors the same stream to the bridge
    shadow = Fabric(ncell=NCELL)

    def feeder(flits, segno, ticks=0):
        for f in flits or ():
            br.flit(f)          # mirror only -- replay sends to the fabric
        for _ in range(ticks or 0):
            br.tick(1)

    led2 = replay(shadow, DeckLedger(), log, commission=True, on_flits=feeder)

    # collect everything the bridge produced (quiescence-based)
    events = br.drain(quiet_cycles=60, timeout=300)
    br_egress = [e for e in events if e["kind"] == "egress"]
    br_fires = [e for e in events if e["kind"] == "fire"]

    # compare egress flit-for-flit (op,src,dst,a0,a1,a2,dat)
    py_egress = [list(f.fields()) for f in shadow.egress]
    got_egress = [e["flit"] for e in br_egress]
    mismatch = None
    for i, (a, b) in enumerate(zip(py_egress, got_egress)):
        if a != b:
            mismatch = (i, a, b)
            break
    py_fires = [(f["cell"], f["dat"]) for f in shadow.fires]
    got_fires = [(e["cell"], e["dat"]) for e in br_fires]
    fires_ok = py_fires == got_fires

    quf_path = os.path.join(ROOT, "artifacts", "day_esp32.quf")
    os.makedirs(os.path.dirname(quf_path), exist_ok=True)
    info = br.save(quf_path)
    br.close()

    with open(quf_path, "rb") as fh:
        quf_bytes = fh.read()
    doc = quf_doc(shadow)
    return {
        "fab": shadow, "led": led2, "doc": doc, "quf_bytes": quf_bytes,
        "egress_match": mismatch is None and len(py_egress) == len(got_egress),
        "egress_mismatch": mismatch, "egress_count": len(got_egress),
        "fires_match": fires_ok, "fire_count": len(got_fires),
        "bridge_info": info,
    }


# ---------------------------------------------------------------- driver --

def run_day(log: List[dict], backend: str = "python", day: Optional[dict] = None,
            out_quf: Optional[str] = None) -> dict:
    """Run one day on one backend; returns the export dict."""
    day = day or {"date": "2026-08-29", "seed": 0, "sets": 5}
    if backend == "python":
        fab, led, doc, quf_bytes, archive = run_python(log, day)
        export = day_export(fab, led, backend, day, quf_bytes, daylog=log)
    elif backend == "esp32":
        res = run_esp32(log, day)
        export = day_export(res["fab"], res["led"], backend, day, res["quf_bytes"],
                            extra={"esp32": {k: v for k, v in res.items()
                                             if k not in ("fab", "led", "doc", "quf_bytes")}},
                            daylog=log)
        from . import qufio as _q
        archive = _q.build_archive(res["doc"], res["led"].books.snapshot())
    else:
        raise ValueError("backend %r unknown (python|esp32)" % backend)
    if out_quf:
        # the day's QUF is the ARCHIVE (v1 sections + app.deck books) --
        # state-is-a-file all the way up
        with open(out_quf, "wb") as fh:
            fh.write(archive)
    return export
