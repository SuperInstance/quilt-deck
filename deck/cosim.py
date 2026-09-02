"""deck.cosim -- the FPGA backend: run the day on the REAL RTL.

Compiles the same day log the python backend replays into framed op
scripts (cosim/tb_deck_cosim.v format), runs iverilog on quilt-verilog's
serialized fabric front-end (read-only import), then referees:

  1. egress streams: RTL vs the soft model's prediction, frame-exact
  2. cold end state: dump -> QUF, byte-identical to the python QUF
  3. warm replay: QUF-boot + training replay -> the SAME final QUF
     (the BACK-DECK-APP §6 doctrine made literal: dials warm, ladders
     re-earned from the day's own label stream)

quilt-verilog is never modified; its rtl/ is compiled with -I flags only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

from . import qufio
from .daylog import replay
from .fabric import Fabric, flit_frame
from .graph import CELL_NAMES, NCELL, load_into_fabric, quf_doc
from .ledger import DeckLedger

QV = os.path.expanduser("~/projects/quilt-verilog")
HERE = os.path.dirname(os.path.abspath(__file__))
COSIM = os.path.normpath(os.path.join(HERE, "..", "cosim"))
RUN = os.path.join(COSIM, "run")


def _egr_lines(run_dir: str, phase: str) -> int:
    """Count flushed egress lines in run/<phase>.egr (0 if absent/empty)."""
    p = os.path.join(run_dir, f"{phase}.egr")
    try:
        with open(p) as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _settle_for(nframes: int) -> int:
    return min(200 * nframes + 2000, 24000)


class ScriptBuilder:
    """Collects framed segments + tick markers in replay order."""

    def __init__(self):
        self.lines: List[str] = []
        self.ticks = 0

    def ops(self, flits) -> None:
        if not flits:
            return
        for f in flits:
            w = int.from_bytes(flit_frame(f), "big")
            self.lines.append("1 %020x" % w)
        self.lines.append("2 %d" % _settle_for(len(flits)))

    def tick(self, n: int) -> None:
        if n > 0:
            self.lines.append("3 %d" % n)
            self.ticks += n

    def text(self) -> str:
        return "\n".join(self.lines + ["0"]) + "\n"


def build_runs(log, seed_label=""):
    """(cold_doc, warm_doc, predicted cold/warm egress words, scripts)."""
    # ---- cold: commissioned replay on the soft engine ----
    fab = Fabric(ncell=NCELL)
    sb_cold = ScriptBuilder()
    mark = len(fab.egress)

    def on_cold(flits, segno, ticks=0):
        if flits:
            sb_cold.ops(flits)
        if ticks:
            sb_cold.tick(ticks)
    led = replay(fab, DeckLedger(), log, commission=True, on_flits=on_cold)
    # commissioning path emits (seg, tick) pairs via the callback; ensure
    # op segments with no trailing marker still align (all paths do now)
    cold_egress = [int.from_bytes(flit_frame(f), "big") for f in fab.egress]
    cold_doc = quf_doc(fab)

    # ---- warm: the RTL loader profile exactly (QUF-SPEC §9): dials image
    # + tpw restored by boot; cores UNBOUND, edges pruned (no load port in
    # v1) -- commissioning re-binds ids + topology (bases re-sent by LINK
    # flits), ladders re-earn from the day's own label stream. Everything
    # after the dials image is a pure function of the op stream, so the
    # warm end state must equal the cold end state byte-for-byte.
    fab2 = Fabric(ncell=NCELL)
    for i in range(NCELL):
        fab2.cells[i].dials = list(cold_doc["dials"][i])
    sb_warm = ScriptBuilder()

    def on_warm(flits, segno, ticks=0):
        if flits:
            sb_warm.ops(flits)
        if ticks:
            sb_warm.tick(ticks)
    led2 = replay(fab2, DeckLedger(), log, commission=True, on_flits=on_warm)
    warm_egress = [int.from_bytes(flit_frame(f), "big") for f in fab2.egress]
    warm_doc = quf_doc(fab2)
    return cold_doc, warm_doc, cold_egress, warm_egress, sb_cold, sb_warm, led


def parse_egress(path: str) -> List[int]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("E "):
                out.append(int(line[2:], 16))
    return out


def parse_dump(path: str) -> Dict:
    cells = {}
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            tag = parts[0]
            if tag == "DC":
                cid = int(parts[1])
                cells.setdefault(cid, {})["dials"] = [int(x) for x in parts[2:18]]
            elif tag == "DA":
                cid = int(parts[1])
                cells.setdefault(cid, {}).update(
                    act=int(parts[2]), refr=int(parts[3]), ftrace=int(parts[4]))
            elif tag == "DE":
                cid, slot = int(parts[1]), int(parts[2])
                c = cells.setdefault(cid, {})
                c.setdefault("edges", {})[slot] = {
                    "valid": int(parts[3]), "peer": int(parts[4]),
                    "base": int(parts[5]), "hl_cnt": int(parts[6]),
                    "wh": int(parts[7]), "age": int(parts[8]),
                    "buckets": [int(x) for x in parts[9:17]],
                }
    return cells


def dump_to_doc(dump: Dict) -> dict:
    """Rebuild a QUF doc from the RTL state dump (QUF edges = valid slots)."""
    edges = []
    for cid in sorted(dump):
        c = dump[cid]
        mode = c["dials"][9] & 1
        for slot in sorted(c.get("edges", {})):
            e = c["edges"][slot]
            if not e["valid"]:
                continue
            edges.append({"src": cid, "dst": e["peer"], "mode": mode,
                          "slot": slot, "base": e["base"], "wh": e["wh"],
                          "age": e["age"], "buckets": e["buckets"]})
    return {
        "header": {
            "quf.version": "quilt-deck 1.0",
            "cell_count": NCELL,
            "edge_count": len(edges),
            "route_count": NCELL,
            "edge.k": 8,
            "tick_period": 1 << 15,
            "quant.dials": "Q1.15", "quant.edges": "Q1.15",
            "quant.routing": "u8", "align": 32,
        },
        "dials": [dump[i]["dials"] for i in range(NCELL)],
        "edges": edges,
        "routing": [{"dst": i, "via": i} for i in range(NCELL)],
        "ticksched": {"tpw": 15, "phases": [0] * NCELL},
    }


def compare_egress(pred: List[int], got: List[int], label: str) -> List[str]:
    problems = []
    n = min(len(pred), len(got))
    first = None
    for i in range(n):
        if pred[i] != got[i]:
            first = i
            break
    if len(pred) != len(got):
        problems.append(f"{label}: egress length pred {len(pred)} vs RTL {len(got)}")
    if first is not None:
        problems.append(f"{label}: first egress mismatch at #{first}: "
                        f"pred {pred[first]:020x} vs RTL {got[first]:020x}")
        lo, hi = max(0, first - 3), min(n, first + 8)
        problems.append(f"  context pred: " + " ".join("%020x" % x for x in pred[lo:hi]))
        problems.append(f"  context RTL : " + " ".join("%020x" % x for x in got[lo:hi]))
    if not problems:
        problems.append(f"{label}: egress frame-exact ({len(got)} flits)")
    return problems


def diff_docs(a: dict, b: dict, la: str, lb: str) -> List[str]:
    out = []
    for i in range(NCELL):
        ca, cb = a["dials"][i], b["dials"][i]
        if ca != cb:
            bad = [j for j in range(16) if ca[j] != cb[j]]
            out.append(f"dials cell {i} ({CELL_NAMES[i]}): slots {bad} "
                       f"{la}={[ca[j] for j in bad]} {lb}={[cb[j] for j in bad]}")
    ea = {(e["src"], e["slot"]): e for e in a["edges"]}
    eb = {(e["src"], e["slot"]): e for e in b["edges"]}
    for key in sorted(set(ea) | set(eb)):
        x, y = ea.get(key), eb.get(key)
        if x is None or y is None:
            out.append(f"edge {key}: present {la}={x is not None} {lb}={y is not None}")
            continue
        for f in ("dst", "mode", "base", "wh", "age"):
            if x[f] != y[f]:
                out.append(f"edge {key}.{f}: {la}={x[f]} {lb}={y[f]}")
        if x["buckets"] != y["buckets"]:
            out.append(f"edge {key}.buckets: {la}={x['buckets']} {lb}={y['buckets']}")
    return out


def run(seed: int = 42, sets: int = 5, keep: bool = True,
        verbose: bool = True) -> Tuple[bool, List[str]]:
    from .daylog import gen_day
    os.makedirs(RUN, exist_ok=True)
    log = gen_day(seed=seed, sets=sets)

    cold_doc, warm_doc, ce, we, sb_cold, sb_warm, led = build_runs(log)

    with open(os.path.join(RUN, "cold.ops"), "w") as fh:
        fh.write(sb_cold.text())
    with open(os.path.join(RUN, "warm.ops"), "w") as fh:
        fh.write(sb_warm.text())
    boot = qufio.boot_image(qufio.build_archive(cold_doc, led.books.snapshot()))
    qufio.write_hex(boot, os.path.join(RUN, "boot.hex"))

    # compile + run the TB
    if verbose:
        print("fpga: compiling RTL + simulating (CPU-bound; capped at "
              "20 min — see cosim/corpus/MANIFEST.md for lane status)")
    # engine selection: Verilator (--binary, Stage 1 of the plan of
    # record in cosim/corpus/MANIFEST.md) when available, iverilog+vvp
    # as the documented fallback. Same TB, same scripts, same treaty.
    vvp = os.path.join(RUN, "tb.vvp")
    vbin = os.path.join(RUN, "obj_verilator", "tb")
    rtl = sorted(os.path.join(QV, "rtl", f) for f in os.listdir(os.path.join(QV, "rtl"))
                 if f.endswith(".v"))
    use_verilator = shutil.which("verilator") is not None and os.environ.get(
        "DECK_COSIM_ENGINE", "verilator") == "verilator"
    if use_verilator:
        cmd = ["verilator", "--binary", "--timing", "-Wno-fatal", "-j", "4",
               "--Mdir", os.path.join(RUN, "obj_verilator"), "-o", "tb",
               "--top-module", "tb_deck_cosim",
               os.path.join(COSIM, "tb_deck_cosim.v")] + rtl
        sim_argv = [vbin]
    else:
        cmd = ["iverilog", "-g2005", "-o", vvp] + rtl + [os.path.join(COSIM, "tb_deck_cosim.v")]
        sim_argv = ["vvp", vvp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return False, [cmd[0] + " failed:\n" + r.stdout + r.stderr]
    try:
        import threading
        stop = threading.Event()
        t0 = time.time()
        def progress():
            while not stop.wait(30):
                print(f"fpga: simulating... {time.time()-t0:.0f}s elapsed "
                      f"(egress so far: cold={_egr_lines(RUN, 'cold')}, "
                      f"warm={_egr_lines(RUN, 'warm')})")
        threading.Thread(target=progress, daemon=True).start()
        try:
            r = subprocess.run(sim_argv, cwd=COSIM, capture_output=True,
                               text=True, timeout=1200)
        finally:
            stop.set()
    except subprocess.TimeoutExpired:
        c = _egr_lines(RUN, "cold"); w = _egr_lines(RUN, "warm")
        if c or w:
            return False, [
                f"vvp TIMED OUT after 1200s; PARTIAL results recovered: "
                f"cold={c} egress lines, warm={w} egress lines (written "
                f"incrementally; see cosim/corpus/MANIFEST.md). The fpga "
                f"lane is EXPERIMENTAL and currently UNVERIFIED."]
        return False, [
            "vvp TIMED OUT after 1200s: ZERO egress lines (files written "
            "incrementally since the $fflush fix — zero here means the TB "
            "emitted nothing, not that output was lost on kill). The fpga "
            "lane is EXPERIMENTAL and currently UNVERIFIED — tracked in "
            "cosim/corpus/MANIFEST.md. The python and esp32 lanes carry "
            "the byte-identity treaty."]
    if verbose:
        print(r.stdout[-2000:])
    if "COSIM DONE" not in r.stdout:
        return False, [f"{os.path.basename(sim_argv[0])} failed:\n" + r.stdout[-3000:] + r.stderr[-1000:]]

    report: List[str] = []
    ok = True

    got_cold = parse_egress(os.path.join(RUN, "cold.egr"))
    got_warm = parse_egress(os.path.join(RUN, "warm.egr"))
    for line in compare_egress(ce, got_cold, "cold"):
        report.append(line)
        ok &= "frame-exact" in line
    for line in compare_egress(we, got_warm, "warm"):
        report.append(line)
        ok &= "frame-exact" in line

    dump_cold = parse_dump(os.path.join(RUN, "cold.dump"))
    dump_warm = parse_dump(os.path.join(RUN, "warm.dump"))
    rtl_cold_doc = dump_to_doc(dump_cold)
    rtl_warm_doc = dump_to_doc(dump_warm)

    d = diff_docs(cold_doc, rtl_cold_doc, "py", "rtl")
    if d:
        ok = False
        report += ["COLD state diverges (py vs rtl):"] + d[:40]
    else:
        report.append("COLD state: dials+edges byte-equal (py vs rtl)")
    d = diff_docs(rtl_cold_doc, rtl_warm_doc, "cold", "warm")
    if d:
        ok = False
        report += ["WARM re-earn diverges (rtl cold vs rtl warm):"] + d[:40]
    else:
        report.append("WARM re-earn: rtl warm == rtl cold (the doctrine holds)")
    d = diff_docs(warm_doc, rtl_warm_doc, "py-warm", "rtl-warm")
    if d:
        ok = False
        report += ["WARM state diverges (py vs rtl):"] + d[:40]
    else:
        report.append("WARM state: dials+edges byte-equal (py vs rtl)")

    # byte-identity of the containers
    a = qufio.quf.build(cold_doc)
    b = qufio.quf.build(rtl_cold_doc)
    report.append("COLD QUF bytes: %s (%d B)" % (
        "byte-identical" if a == b else "DIVERGE", len(a)))
    ok &= a == b
    c = qufio.quf.build(rtl_warm_doc)
    report.append("WARM QUF bytes: %s" % ("byte-identical to cold" if c == a else "DIVERGE"))
    ok &= c == a
    return ok, report


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42
    ok, rep = run(seed=seed)
    print("\n".join(rep))
    sys.exit(0 if ok else 1)
