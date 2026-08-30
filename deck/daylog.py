"""deck.daylog -- a day of fishing as a replayable log.

The SAME log drives all three backends (the conformance thesis):
- python: replay(fabric, ledger, log) on the soft engine
- fpga:   the log compiles to a framed op stream for cosim/tb_deck_cosim.v
- esp32:  the log compiles to canon opcode calls for esp32/deckbridge.c

Segmentation contract (cosim determinism, docs/ARCHITECTURE.md): the replay
emits SEGMENTS -- an op batch (landings chunked to <=100 fish) followed by a
tick burst. tpw=15 (32768 cycles/deck-minute) gives each segment >2.5x
headroom inside one tick period, so no tick ever lands mid-flight; the
cosim aligns to tick edges between segments and both engines see the
identical op/tick interleaving.

Entry types:
  {"t":"set","set":1,"fish":[{"sp":..,"n":..},..]}
  {"t":"hooks","set":1,"visible":30}
  {"t":"land","set":1,"tote":"TOTE-PORT","sp":"pink","n":612}
  {"t":"move","from":"TOTE-PORT","to":"HOLD","n":400,"sp":"pink"}
  {"t":"uw","set":1,"sightings":6}
  {"t":"ticks","n":32}
  {"t":"night","audit":[..],"ab":{"promote":false,"margin":64}}
  {"t":"refuse","op":"double-move"|"overflow"|"phantom"}   adversarial probes
"""

from __future__ import annotations

import random
from typing import Callable, List, Optional

from .fabric import EXTID, Flit, OP_BIND, OP_EFF, OP_VIEW, Fabric
from .graph import TOTE_CAPACITY, commissioning_flits
from .ledger import DeckLedger

LAND_CHUNK = 100
NIGHT_VERDICT_TAG = 0xBEEF


# ------------------------------------------------------------- generator --

def gen_day(seed: int = 42, sets: int = 5, adversarial: bool = True) -> List[dict]:
    """A plausible seine day: pinks dominant, chums center, kings/cohos in
    the starboard half-totes. Landing is capacity-paced: when a tote would
    overflow, the deck brails it to the hold mid-set. Deterministic per seed."""
    rng = random.Random(seed)
    log: List[dict] = []
    loads = {0: 0, 1: 0, 2: 0, 3: 0}          # per-tote running load
    for s in range(1, sets + 1):
        mix = {"pink": rng.randrange(480, 760), "chum": rng.randrange(120, 260),
               "king": rng.randrange(6, 22), "coho": rng.randrange(14, 44)}
        log.append({"t": "set", "set": s,
                    "fish": [{"sp": sp, "n": n} for sp, n in mix.items()]})
        log.append({"t": "hooks", "set": s, "visible": rng.randrange(18, 31)})
        for sp, tote, cid in (("pink", "TOTE-PORT", 0), ("chum", "TOTE-HOLD", 1),
                              ("king", "TOTE-STBD-F", 2), ("coho", "TOTE-STBD-A", 3)):
            n = mix[sp]
            while n > 0:
                room = TOTE_CAPACITY[cid] - loads[cid]
                chunk = min(n, rng.randrange(60, 101))
                if chunk > room:
                    brail = loads[cid] - 12 if loads[cid] > 24 else loads[cid]
                    if brail > 0:
                        log.append({"t": "move", "from": tote, "to": "HOLD",
                                    "sp": sp, "n": brail})
                        loads[cid] -= brail
                    if loads[cid] >= TOTE_CAPACITY[cid]:
                        n = 0  # physically cannot take more this brail cycle
                        break
                    continue
                log.append({"t": "land", "set": s, "tote": tote, "sp": sp,
                            "n": chunk})
                loads[cid] += chunk
                n -= chunk
        log.append({"t": "uw", "set": s, "sightings": rng.randrange(3, 9)})
        log.append({"t": "scale", "set": s, "frames": rng.randrange(2, 6)})
        log.append({"t": "ticks", "n": rng.randrange(24, 41)})
        for tote, sp, cid in (("TOTE-PORT", "pink", 0), ("TOTE-HOLD", "chum", 1),
                              ("TOTE-STBD-F", "king", 2), ("TOTE-STBD-A", "coho", 3)):
            brail = loads[cid] - 12 if loads[cid] > 24 else loads[cid]
            if brail > 0:
                log.append({"t": "move", "from": tote, "to": "HOLD",
                            "sp": sp, "n": brail})
                loads[cid] -= brail
    if adversarial:
        log.append({"t": "refuse", "op": "double-move"})
        log.append({"t": "refuse", "op": "overflow",
                    "tote": "TOTE-STBD-F", "sp": "king", "n": 200})
        log.append({"t": "refuse", "op": "phantom", "sp": "pink", "n": 50})
    log.append({"t": "night", "audit": [
        {"fish": i, "verdict": "confirm" if i % 7 else "quarantine"}
        for i in range(1, 25)],
        "ab": {"promote": False, "margin": 64}})
    log.append({"t": "ticks", "n": 16})
    return log


# --------------------------------------------------------------- replayer --

def replay(fab: Fabric, ledger: Optional[DeckLedger] = None,
           log: List[dict] = None, commission: bool = False,
           on_flits: Optional[Callable[[List[Flit], int], None]] = None
           ) -> DeckLedger:
    """Replay a day log against a fabric + ledger. on_flits(flits, segno)
    lets the cosim/esp32 compilers capture the exact op stream; tick bursts
    are signaled with an empty flit list."""
    if ledger is None:
        ledger = DeckLedger()
    if commission:
        seg = commissioning_flits()
        for f in seg:
            fab.send(f)
        if on_flits:
            on_flits(seg, 0)

    state = {"segno": 1, "fires_seen": 0}

    def emit(flits: List[Flit]):
        for f in flits:
            fab.send(f)
        if on_flits:
            on_flits(flits, state["segno"])
        state["segno"] += 1

    def tick_burst(n: int):
        fab.tick(n)
        ledger.books.tick += n
        if on_flits:
            on_flits([], state["segno"])   # tick-burst boundary marker
        state["segno"] += 1

    for entry in log:
        t = entry["t"]
        if t == "set":
            continue                        # bookkeeping header
        if t == "hooks":
            ledger.hooks(entry["set"], entry["visible"])
            emit([Flit(OP_EFF, src=EXTID, dst=7, dat=0x0800)
                  for _ in range(entry["visible"])])
        elif t == "land":
            flits = ledger.land(entry["tote"], entry["sp"], entry["n"])
            for i in range(0, len(flits), LAND_CHUNK):
                emit(flits[i:i + LAND_CHUNK])
        elif t == "move":
            n = entry["n"]
            flits = ledger.move(entry["from"], entry["to"], n, entry["sp"])
            for i in range(0, len(flits), LAND_CHUNK):
                emit(flits[i:i + LAND_CHUNK])
        elif t == "uw":
            # underwater leave-frames; each XID-MATCH fire since the last
            # sighting retro-labels one sighting (host-mediated Law-4
            # adapter: the label bus event enters as a src=XID effect,
            # which is exactly what CAM-UW's etab accepts)
            new_fires = len(fab.fires) - state["fires_seen"]
            state["fires_seen"] = len(fab.fires)
            n = entry["sightings"]
            flits = [Flit(OP_EFF, src=EXTID, dst=14, dat=0x0800)
                     for _ in range(n)]
            retro = min(n, max(0, new_fires))
            flits += [Flit(OP_EFF, src=6, dst=14, dat=0x0800)
                      for _ in range(retro)]
            emit(flits)
        elif t == "scale":
            # CAM-SCALE dial frames: the scale never stops talking; the
            # LEDGER joins species via the tote label bus (its trained
            # tote edges are exactly that join)
            emit([Flit(OP_EFF, src=EXTID, dst=9, dat=0x0800)
                  for _ in range(entry["frames"])])
        elif t == "refuse":
            op = entry["op"]
            if op == "double-move":
                if ledger.books.moves:
                    m = ledger.books.moves[-1]
                    flits = ledger.move(m.src, m.dst, m.n, m.sp)
                    assert not flits, "double-move must refuse"
                else:
                    ledger.move("TOTE-PORT", "HOLD", 40, "pink")
            elif op == "overflow":
                ledger.land(entry["tote"], entry["sp"], entry["n"])
            elif op == "phantom":
                ledger.phantom_hold_entry(entry["sp"], entry["n"])
        elif t == "ticks":
            tick_burst(entry["n"])
        elif t == "night":
            emit([Flit(OP_VIEW, src=EXTID, dst=11, a0=0, a2=NIGHT_VERDICT_TAG)])
            quarantined = sum(1 for a in entry["audit"]
                              if a["verdict"] == "quarantine")
            if quarantined:
                emit([Flit(OP_EFF, src=EXTID, dst=11, dat=0x0800)
                      for _ in range(quarantined)])
            tick_burst(1)
            # A/B verdict: rollback is the NULL ACTION -- nothing written
            # unless the challenger clears PROMOTE_MARGIN (SYNTHESIS steal-1)
            if entry["ab"].get("promote"):
                emit([Flit(OP_BIND, src=13, dst=8, a0=0, a1=0x0C00),
                      Flit(OP_BIND, src=13, dst=8, a0=9, a1=0)])
            ledger.books.ab_verdict = dict(entry["ab"], tick=ledger.books.tick)
    return ledger
