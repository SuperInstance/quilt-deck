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
import subprocess
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
               quf_bytes: bytes, extra: Optional[dict] = None) -> dict:
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
        "daylog": [],
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
    return fab, led, doc, quf_bytes


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
        self.banner = self.p.stdout.readline().strip()

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

    def drain(self, expect_events: int, timeout: float = 60.0) -> List[dict]:
        events = []
        deadline = time.time() + timeout
        while len(events) < expect_events and time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
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
        # 15 cells x (DC + DA + 4 DE) = 90 lines
        lines = []
        while len(lines) < 90:
            line = self.p.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def save(self, path: str) -> str:
        self._cmd("S %s" % path)
        return self.p.stdout.readline().strip()

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
    # replay against a SHADOW fabric (the soft model predicts every event;
    # the bridge must emit the same stream), feeding the bridge live:
    shadow = Fabric(ncell=NCELL)

    def feeder(flits, segno, ticks=0):
        if flits:
            for f in flits:
                shadow.send(f)
                br.flit(f)
        if ticks:
            shadow.tick(ticks)
            br.tick(ticks)

    led2 = replay(shadow, DeckLedger(), log, commission=True, on_flits=feeder)

    # read all events the bridge produced (count = python egress + fires)
    want = len(shadow.egress) + len(shadow.fires)
    events = br.drain(want + 8, timeout=120)
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
        fab, led, doc, quf_bytes = run_python(log, day)
        export = day_export(fab, led, backend, day, quf_bytes)
    elif backend == "esp32":
        res = run_esp32(log, day)
        export = day_export(res["fab"], res["led"], backend, day, res["quf_bytes"],
                            extra={"esp32": {k: v for k, v in res.items()
                                             if k not in ("fab", "led", "doc", "quf_bytes")}})
    else:
        raise ValueError("backend %r unknown (python|esp32)" % backend)
    export["_quf_bytes"] = quf_bytes if backend == "python" else res["quf_bytes"]
    if out_quf:
        with open(out_quf, "wb") as fh:
            fh.write(export["_quf_bytes"])
    return export
