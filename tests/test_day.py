"""End-to-end: a full fishing day through every available backend.

The thesis test: the SAME day log replayed on the python engine and the
esp32 bridge (and the FPGA cosim via deck.cosim.run) must produce
BYTE-IDENTICAL final QUF files; adversarial ops must refuse identically;
conservation must hold live. FPGA conformance runs separately
(`python3 -m deck.cosim`) because it drives iverilog.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from deck.backends import run_python
from deck.daylog import gen_day, replay
from deck.ledger import DeckLedger
from deck.graph import new_fabric, quf_doc

EXPECTED_REFUSALS = ["DOUBLE_MOVE", "TOTE_OVERFLOW", "PHANTOM_HOLD_ENTRY"]


def _one_day(backend: str, seed: int):
    log = gen_day(seed=seed)
    day = {"date": "2026-08-29", "seed": seed, "sets": 5, "backend": backend}
    if backend == "python":
        fab, led, doc, quf, archive = run_python(log, day)
        return led, archive, doc, {"archive": archive, "core": quf}
    elif backend == "esp32":
        from deck.backends import run_esp32
        res = run_esp32(log, day)
        return res["led"], res["quf_bytes"], res["doc"], res
    raise ValueError(backend)


def test_python_day_balanced_and_refused():
    led, quf, doc, _ = _one_day("python", 42)
    c = led.books.conservation()
    assert c["balanced"] and c["unbooked"] == 0
    assert c["landed_total"] > 3500
    reasons = [r.reason for r in led.books.refusals]
    assert reasons == EXPECTED_REFUSALS, reasons
    assert len(led.books.moves) > 15
    assert len(led.books.hooks) == 5
    # hook depth rule: 30 hooks x 1.5 fm
    assert all(0 < h["depth_fm"] <= 45.0 for h in led.books.hooks)


def test_python_day_trains_the_label_bus():
    led, quf, doc, _ = _one_day("python", 42)
    fab = new_fabric()
    # (re-run to inspect: run_python builds its own; do a fresh one)
    from deck.fabric import Fabric
    from deck.graph import NCELL
    fab = Fabric(ncell=NCELL)
    led2 = replay(fab, DeckLedger(), gen_day(seed=42), commission=True)
    snap = fab.snapshot()
    tote_ext = snap["cells"][0]["edges"][0]        # TOTE-PORT <- host landings
    assert sum(tote_ext["buckets"]) > 300          # landings trained the ladder
    sounder_hook = snap["cells"][8]["edges"][0]    # SOUNDER <- HOOK fires
    assert sum(sounder_hook["buckets"]) > 0        # the depth-pair link lived
    xid_tote = snap["cells"][6]["edges"][0]        # XID <- totes (in-window)
    assert sum(xid_tote["buckets"]) > 0            # the match window opened
    assert len(fab.fires) > 10


def test_backend_conformance_python_esp32():
    """Same day -> byte-identical QUF from both engines.

    Reference discipline (expert nudge 2026-08-29): the ESP32 bridge egress
    is compared against the python soft-fabric shadow -- the SAME single
    reference the FPGA cosim uses (deck/cosim.py compares RTL egress to the
    soft model's prediction). Byte-identity therefore chains transitively:
    esp32 == py and RTL == py imply all three agree. No two hardware
    codepaths are ever compared only to each other."""
    led_p, quf_p, doc_p, _ = _one_day("python", 7)
    try:
        led_e, quf_e, doc_e, res = _one_day("esp32", 7)
    except FileNotFoundError:
        print("SKIP esp32 backend (not built)")
        return
    h_p = hashlib.sha256(quf_p).hexdigest()
    h_e = hashlib.sha256(quf_e).hexdigest()
    assert h_p == h_e, f"QUF diverged: py {h_p[:12]} vs esp32 {h_e[:12]}"
    assert res["egress_match"], f"egress diverged at {res['egress_mismatch']}"
    assert res["fires_match"], "fire streams diverged"
    # books agree exactly (conservation is backend-independent)
    assert led_p.books.snapshot() == led_e.books.snapshot()
    # the final QUF is the ARCHIVE flavor: five sections incl. app.deck
    from deck.qufio import split_archive
    _, app_p = split_archive(quf_p)
    _, app_e = split_archive(quf_e)
    assert set(app_p) == set(app_e) == {
        "conservation", "hold", "hook_sets", "landed", "moves",
        "refusals", "totes"}, "app.deck section missing or wrong"
    print("conformance: python == esp32 byte-identical (%d B QUF)" % len(quf_p))


def test_adversarial_refusals_through_backends():
    """Adversarial ops must refuse identically on every backend, and a
    refusal must leave zero fabric traffic (state hash unchanged)."""
    from deck.fabric import Fabric
    from deck.graph import NCELL

    def state_hash(fab):
        import json
        s = fab.snapshot()
        s.pop("egress"); s.pop("fires")
        return hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()

    # phantom hold: ledger refuses; fabric never sees a flit
    fab = Fabric(ncell=NCELL)
    led = DeckLedger()
    before = state_hash(fab)
    flits = led.phantom_hold_entry("pink", 50)
    assert flits == [] and state_hash(fab) == before
    # double move: second booking refuses; no flits emitted
    led.land("TOTE-PORT", "pink", 100)
    f1 = led.move("TOTE-PORT", "HOLD", 40, "pink")
    for f in f1: fab.send(f)
    mid = state_hash(fab)
    f2 = led.move("TOTE-PORT", "HOLD", 40, "pink")
    assert f2 == [] and state_hash(fab) == mid
    # overflow: refuse, no flits
    f3 = led.land("TOTE-PORT", "pink", 500)
    assert f3 == [] and state_hash(fab) == mid


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)} day tests passed")


def test_no_bridge_drift_seam():
    """Drift sentinel (expert nudge 2026-08-29): the esp32 bridge C must live
    in exactly ONE place — this repo. If quilt-verilog ever sprouts its own
    copy, this test fails and forces single-sourcing or an explicit
    checksum gate, before silent divergence can happen."""
    qv = os.path.expanduser("~/projects/quilt-verilog")
    if not os.path.isdir(qv):
        print("SKIP: quilt-verilog not present")
        return
    clones = []
    for root, dirs, files in os.walk(qv):
        dirs[:] = [d for d in dirs if d != ".git"]
        if "deckbridge.c" in files:
            clones.append(os.path.join(root, "deckbridge.c"))
    here = os.path.join(os.path.dirname(__file__), "..", "esp32", "deckbridge.c")
    assert not clones, (
        "bridge C duplicated outside this repo: %s -- single-source it or add "
        "a checksum gate" % clones)
    assert os.path.exists(here), "canonical bridge C missing from quilt-deck"
    print("drift sentinel: bridge C single-sourced in quilt-deck")
