"""deck.graph — the F/V EILEEN back deck as a commissioned cell graph.

Cell map (ring slots 0..14, host/io = EXTID 15) per docs/BACK-DECK-APP.md
adapted to the v1 fabric budget (AIDW=4: 15 cells max; EDGES_N=4 per cell).
Camera adapter cells (CAM-DECK-P/H/SF/SA) are host-side adapters — Law 4:
ingress is thin and dumb; their landing events enter as EXTID-sourced
effects. CAM-UW gets fabric residency because its label port is a back-flow
(retro-labels).

App dials (BACK-DECK-APP §4) after the v2 collision audit: slots 11-15 in
the current RTL are the echo-gate/RQH feature dials (q_dialfile v2) and 13
is a read-only probe — PROMOTE_MARGIN cannot live at 13. The deck app
therefore remaps: MATCH_WIN=11 (KLE), HOOK_PITCH=12 (FLOOR), 13 unavailable
(probe; container-only), PROMOTE_MARGIN=14 (QDW, RQEN bit kept clear),
QUARANTINE=15 (QLEAK), LEGAL_SET rides AUDIT's HL slot 10. The echo-gate
numerics are modeled bit-exact in deck.fabric, so the double duty is real
semantics, not a fudge: HOOK_PITCH/FLOOR is literally the causal window for
hook-frame training.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .fabric import (D_FLOOR, D_HL, D_KLE, D_MODE, D_RQ, D_RQL, D_THRESH,
                     EXTID, Flit, Fabric, OP_BIND, OP_LINK, OP_VIEW)

NCELL = 15

# species per deck position (constraint-cells: rules, not classifiers)
SPECIES = {"pink": 0, "chum": 1, "king": 2, "coho": 3}
ALIASES = {
    "humpy": "pink", "dog": "chum", "keta": "chum",
    "chinook": "king", "silver": "coho",
}

CELL_NAMES = {
    0: "TOTE-PORT",    # pink / humpy
    1: "TOTE-HOLD",    # center: chum / dog
    2: "TOTE-STBD-F",  # forward starboard half-tote: king / chinook
    3: "TOTE-STBD-A",  # aft starboard half-tote: coho / silver
    4: "HOLD",         # the fish hold (terminal custody)
    5: "ALIAS",
    6: "XID-MATCH",
    7: "HOOK-COUNT",
    8: "SOUNDER",
    9: "LEDGER-SCALE",
    10: "BESTSHOT",
    11: "AUDIT-CAPTAIN",
    12: "NIGHT-CRON",
    13: "AB-PROMOTE",
    14: "CAM-UW",
}
NAME_TO_ID = {v: k for k, v in CELL_NAMES.items()}
TOTE_FOR_SPECIES = {"pink": 0, "chum": 1, "king": 2, "coho": 3}
TOTE_CAPACITY = {0: 400, 1: 400, 2: 120, 3: 120}   # half-totes: king/coho
HOLD_CAPACITY = 20000
HOLD_ID = 4

# ------------------------------------------------------------------ links --
# Fabric learning graph (etab per cell, slot -> peer). Each entry becomes
# one LINK flit at commissioning and one QUF edge record. Fire fanout goes
# to the whole etab (v1 broadcast discipline); training-in matches src.
# The label bus spine: totes -> {ALIAS, XID, LEDGER} + EXTID (host sees
# tote-full fires). HOST = 15 everywhere.

LINKS: Dict[int, List[int]] = {
    0:  [EXTID, 5, 6, 9],   # TOTE-PORT:    host, ALIAS, XID, LEDGER
    1:  [EXTID, 5, 6, 9],   # TOTE-HOLD
    2:  [EXTID, 5, 6, 9],   # TOTE-STBD-F
    3:  [EXTID, 5, 6, 9],   # TOTE-STBD-A
    4:  [0, 1, 2, 3],       # HOLD: trained by moves from each tote
    5:  [0, 1, 2, 3],       # ALIAS: corroboration from the totes
    6:  [0, 1, 2, 3],       # XID-MATCH: label bus in, match-acks out
    7:  [EXTID, 8],         # HOOK-COUNT: frames in, depth events to SOUNDER
    8:  [7, EXTID],         # SOUNDER: labeled pairs from HOOK, echogram in
    9:  [EXTID, 0, 1, 2],   # LEDGER-SCALE: dial frames + three-tote label bus
    10: [EXTID],            # BESTSHOT: footage chains host-side
    11: [10, EXTID],        # AUDIT: pile from BESTSHOT, flips from host
    12: [11, EXTID],        # NIGHT-CRON: quarantine list in, night verdicts
    13: [12, 8, EXTID],     # AB-PROMOTE: verdicts to SOUNDER (binds), log
    14: [6, EXTID],         # CAM-UW: retro-labels from XID (host-mediated)
}

# ------------------------------------------------------------------ dials --
# App dial plan written at commissioning (on top of q_dialfile defaults).
# dat for one landing fish = 0x0800 (Q1.15 = 0.0625); fresh edge reads 256,
# so each landing integrates ~16 -> tote THRESH 0x1800 fires ~ every 384.

LAND_DAT = 0x0800

DIAL_PLAN: Dict[int, List[Tuple[int, int]]] = {
    0:  [(D_THRESH, 0x1800)],
    1:  [(D_THRESH, 0x1800)],
    2:  [(D_THRESH, 0x1000)],   # half-totes fill slower (smaller volume)
    3:  [(D_THRESH, 0x1000)],
    4:  [(D_THRESH, 0x6000)],   # hold fires = "hold brail complete"
    5:  [(D_THRESH, 0x0C00)],   # ALIAS fires ~ per 8-12 tote label events
    6:  [(D_THRESH, 0x0400), (D_KLE, 6), (D_FLOOR, 0x0100)],  # MATCH_WIN
    7:  [(D_THRESH, 0x0180), (D_KLE, 2), (D_FLOOR, 150)],     # HOOK_PITCH
    8:  [(D_THRESH, 0x1000)],   # SOUNDER fires on labeled-depth pairs
    9:  [(D_THRESH, 0x2000)],
    11: [(D_HL, 15)],                     # LEGAL_SET mask 0b1111 (all four)
    13: [(D_RQ, 0x0040), (D_RQL, 8)],     # PROMOTE_MARGIN 0x40, QUARANTINE 8
    14: [(D_THRESH, 0x0060), (D_KLE, 5), (D_FLOOR, 0x0080)],  # lost-label window
}

# Every commissioned edge carries base = one fresh cofire (256 in Q1.15
# readout units): a cold fabric bootstraps — gated cells still integrate
# activation through their priors, and base == fresh-ladder is the exact
# equivalence the ladder readout defines (SYNTHESIS weight-map paragraph).
LINK_BASE = 0x0100

TPW = 15          # tick period exponent: 2^15 cycles per deck-minute tick
HOOKS = 30        # gear: 30 hooks
FATHOMS_PER_HOOK = 1.5


def hook_depth(hooks_visible: int) -> float:
    """cannonball depth = hooks-visible x 1.5 fathoms (count-cell rule)"""
    return round(hooks_visible * FATHOMS_PER_HOOK, 1)


# ------------------------------------------------------------- commission --

def commissioning_flits() -> List[Flit]:
    """Cold-boot commissioning: first-bind ids, dial plan, link topology.

    Order is fixed (the same sequence replays on every backend): bind ids
    in slot order, dials in plan order, links in cell/slot order.
    """
    out: List[Flit] = []
    for cid in range(NCELL):
        out.append(Flit(OP_BIND, src=EXTID, dst=cid, a0=cid))
    for cid, plan in sorted(DIAL_PLAN.items()):
        for addr, val in plan:
            out.append(Flit(OP_BIND, src=EXTID, dst=cid, a0=addr, a1=val))
    for cid, peers in sorted(LINKS.items()):
        for slot, peer in enumerate(peers):
            out.append(Flit(OP_LINK, src=peer, dst=cid, a0=slot, a1=LINK_BASE))
    return out


def new_fabric() -> Fabric:
    """A commissioned deck fabric (python backend)."""
    f = Fabric(ncell=NCELL)
    for flit in commissioning_flits():
        f.send(flit)
    return f


# ------------------------------------------------------------- day state --

def quf_doc(fab: Fabric, app_section: bytes = b"") -> dict:
    """Final-state QUF document (JSON shape of tools/quf.py build())."""
    snap = fab.snapshot()
    edges = []
    for c in snap["cells"]:
        for e in c["edges"]:
            if not e["valid"]:
                continue
            edges.append({
                "src": c["id"], "dst": e["peer"],
                "mode": c["dials"][D_MODE] & 1, "slot": e["slot"],
                "base": e["base"], "wh": e["wh"], "age": e["age"],
                "buckets": e["buckets"],
            })
    routing = [{"dst": i, "via": i} for i in range(NCELL)]
    return {
        "header": {
            "quf.version": "quilt-deck 1.0",
            "cell_count": NCELL,
            "edge_count": len(edges),
            "route_count": len(routing),
            "edge.k": 8,
            "tick_period": 1 << TPW,
            "quant.dials": "Q1.15", "quant.edges": "Q1.15",
            "quant.routing": "u8", "align": 32,
        },
        "dials": [c["dials"] for c in snap["cells"]],
        "edges": edges,
        "routing": routing,
        "ticksched": {"tpw": TPW, "phases": [0] * NCELL},
    }


def load_into_fabric(fab: Fabric, doc: dict, restore_walk: bool = True) -> None:
    """Warm-load a QUF document into a live fabric (the python full-state
    path; restore_walk=False reproduces the RTL loader profile: topology +
    dials restored, ladders trained back)."""
    dials = doc["dials"]
    for cid in range(min(fab.ncell, len(dials))):
        c = fab.cells[cid]
        c.dials = list(dials[cid])
        c.bound = True
        c.cell_id = cid
    for e in doc.get("edges", []):
        c = fab.cells[e["src"]]
        slot = fab.cells[e["src"]].edges[e["slot"]]
        slot.peer = e["dst"]
        slot.valid = True
        slot.engine.base = e["base"]
        if restore_walk:
            slot.engine.buckets = list(e["buckets"])
            slot.engine.wh = e["wh"]
            slot.engine.age = e["age"]


def view_all_dials(fab: Fabric) -> List[int]:
    """End-of-day dial readback over the fabric itself (view(2) per dial):
    what the cosim asserts byte-exact against the container. Dial 13 reads
    the live echo trace (probe alias) on every backend — that is the v2
    contract, modeled exactly."""
    mark = len(fab.egress)
    for cid in range(fab.ncell):
        for addr in range(16):
            fab.send(Flit(OP_VIEW, src=EXTID, dst=cid, a0=2, a1=addr,
                          a2=0xA000 | cid * 16 + addr))
    resp = [r for r in fab.egress[mark:] if r.op in (5, 6)]
    assert len(resp) == fab.ncell * 16, "dial view readback incomplete"
    return [r.dat for r in resp]
