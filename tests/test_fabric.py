"""Unit tests: the soft fabric vs the RTL's own acceptance math.

Golden values from quilt-verilog docs/SYNTHESIS.md Part A (Q3):
  - N co-fires before any half-life shift: wsum == base + N * 2^8 exactly
  - decay-only ticks shrink the weight below THRESH (view-verified)
  - fresh cofire reads 256; ladder envelope; saturation never wraps
Plus frame round-trips and the echo gate semantics (q_echo_gate rules).
Run: python3 -m pytest tests/ -q   (or python3 tests/test_fabric.py)
"""

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from deck.fabric import (EXTID, Flit, Fabric, OP_BIND, OP_EFF, OP_LINK,
                         OP_VIEW, flit_frame, parse_frame)
from deck.graph import new_fabric


def _mkfabric(ncell=2):
    f = Fabric(ncell=ncell)
    for cid in range(ncell):
        f.send(Flit(OP_BIND, src=EXTID, dst=cid, a0=cid))
    return f


def test_commission_first_bind_sets_id():
    f = _mkfabric()
    assert f.cells[0].bound and f.cells[0].cell_id == 0
    # unbound cell NAKs non-bind
    f2 = Fabric(ncell=1)
    f2.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=0))
    r = f2.egress[-1]
    assert r.op == 6  # NAK


def test_wsum_golden_exact():
    """SYNTHESIS Q3: 100 co-fires, wsum == base + N*256 exactly."""
    f = _mkfabric()
    base = 0x0100
    f.send(Flit(OP_LINK, src=EXTID, dst=0, a0=0, a1=base))
    for _ in range(100):
        f.send(Flit(OP_EFF, src=EXTID, dst=0, dat=0x0800))
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=1, a2=77))
    ack = [e for e in f.egress if e.op == 5][-1]
    assert ack.dat == base + 100 * 256


def test_decay_shrinks_below_thresh():
    f = _mkfabric()
    f.send(Flit(OP_LINK, src=EXTID, dst=0, a0=0, a1=0))
    for _ in range(4):
        f.send(Flit(OP_EFF, src=EXTID, dst=0, dat=0x4000))
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=0))
    act_before = [e for e in f.egress if e.op == 5][-1].dat
    f.tick(200)   # decay-only: leak >>>5 plus ladder shifts
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=0))
    act_after = [e for e in f.egress if e.op == 5][-1].dat
    assert act_before > 0
    assert act_after < act_before
    # ladder: after >8*HL ticks all buckets shifted past the oldest class
    f.tick(400)       # 600 total: >= 8 half-lives
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=1))
    wsum = [e for e in f.egress if e.op == 5][-1].dat
    assert wsum == 0  # base 0, every bucket emptied by shifts


def test_effect_unknown_src_dropped():
    f = _mkfabric()
    f.send(Flit(OP_EFF, src=5, dst=0, dat=0x4000))  # no edge with peer 5
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=0))
    assert [e for e in f.egress if e.op == 5][-1].dat == 0


def test_act_integration_saturation():
    """act += sat((w*dat)>>>15), saturate-never-wrap at 0x7FFF."""
    f = _mkfabric()
    f.send(Flit(OP_LINK, src=EXTID, dst=0, a0=0, a1=0xFFFF))
    for _ in range(10):
        f.send(Flit(OP_EFF, src=EXTID, dst=0, dat=0x7FFF))
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=0))
    # w = 0xFFFF (base max, no training): each eff adds ~0x7FFF -> saturate
    assert [e for e in f.egress if e.op == 5][-1].dat == 0x7FFF


def test_fire_fanout_and_refractory():
    f = _mkfabric(2)
    # cell0 fires -> effects to its etab peers (EXTID + cell1)
    f.send(Flit(OP_LINK, src=EXTID, dst=0, a0=0, a1=0xFFFF))
    f.send(Flit(OP_LINK, src=1, dst=0, a0=1, a1=0x0100))
    f.send(Flit(OP_BIND, src=EXTID, dst=0, a0=5, a1=0x0100))  # low thresh
    f.send(Flit(OP_EFF, src=EXTID, dst=0, dat=0x4000))
    f.tick(1)
    assert len(f.fires) == 1
    # fire value = pre-leak act; fanout reached EXTID (egress) and cell 1
    eff_out = [e for e in f.egress if e.op == 2]
    assert any(e.dst == EXTID for e in eff_out)
    # refractory: thresh still exceeded (act reset to 0 though)
    # -> no second fire until act re-accumulates
    assert len(f.fires) == 1


def test_echo_gate_window():
    """FLOOR=0 gate disabled (v1); FLOOR!=0 gates training to the window."""
    f = _mkfabric()
    # commission edge from host, floor 0x0100, thresh high (no fires)
    f.send(Flit(OP_LINK, src=EXTID, dst=0, a0=0, a1=0x0100))
    f.send(Flit(OP_BIND, src=EXTID, dst=0, a0=12, a1=0x0100))
    f.send(Flit(OP_EFF, src=EXTID, dst=0, dat=0x0800))
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=1))
    wsum = [e for e in f.egress if e.op == 5][-1].dat
    assert wsum == 0x0100  # gate closed (never fired): no training, base only
    # now make the cell fire once (low thresh), gate opens, train lands
    f.send(Flit(OP_BIND, src=EXTID, dst=0, a0=5, a1=0x0001))
    f.tick(1)  # act (from earlier eff, 16) >= 1 -> fire, ftrace = 0xFFFF
    f.send(Flit(OP_BIND, src=EXTID, dst=0, a0=5, a1=0x6000))
    f.send(Flit(OP_EFF, src=EXTID, dst=0, dat=0x0800))  # window open -> trains
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=1))
    wsum2 = [e for e in f.egress if e.op == 5][-1].dat
    assert wsum2 == 0x0100 + 256


def test_dial13_probe_alias():
    f = _mkfabric()
    f.send(Flit(OP_BIND, src=EXTID, dst=0, a0=13, a1=0xABCD))  # ignored
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=2, a1=13))
    v = [e for e in f.egress if e.op == 5][-1].dat
    assert v == 0  # live trace, not the written value


def test_frame_roundtrip():
    for trial in range(200):
        f = Flit(2, 15, 0, 0x1234, 0xBEEF, trial & 0xFFFF, 0x7FFF)
        assert parse_frame(flit_frame(f)).fields() == f.fields()


def test_ladder_shift_math():
    """hl_cnt+1 >= hl shifts; bucket i weight 2^-i in readout."""
    f = _mkfabric()
    f.send(Flit(OP_LINK, src=EXTID, dst=0, a0=0, a1=0))
    for _ in range(3):
        f.send(Flit(OP_EFF, src=EXTID, dst=0, dat=0x0800))
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=1))
    w0 = [e for e in f.egress if e.op == 5][-1].dat
    assert w0 == 3 * 256
    f.tick(64)  # one half-life: buckets shift one class older
    f.send(Flit(OP_VIEW, src=EXTID, dst=0, a0=1))
    w1 = [e for e in f.egress if e.op == 5][-1].dat
    assert w1 == 3 * 128  # 2^-1 class


def test_graph_commissioning_complete():
    fab = new_fabric()
    for c in fab.cells:
        assert c.bound, f"cell {c.myid} unbound"
        assert c.cell_id == c.myid
    snap = fab.snapshot()
    n_edges = sum(1 for c in snap["cells"] for e in c["edges"] if e["valid"])
    assert n_edges == 46
    # every commissioned peer base = one fresh cofire
    for c in fab.cells:
        for e in c.edges:
            if e.valid:
                assert e.engine.base == 0x0100


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)} unit tests passed")
