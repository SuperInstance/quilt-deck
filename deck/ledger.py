"""deck.ledger — custody, conservation, refusal. The runtime check.

The fabric learns; the ledger books. Every fish is custody in exactly one
place (a tote or the hold); every move is a balanced transaction: debit
source, credit destination, one entry. A credit without a debit is a
PHANTOM, a debit past empty is INSUFFICIENT_CREDIT, a landing past capacity
is TOTE_OVERFLOW — all REFUSED with booked reasons before any fabric effect
is emitted. Conservation is not flavor; it is this file, run live on every
backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .fabric import EXTID, Flit, OP_EFF
from .graph import (ALIASES, CELL_NAMES, HOLD_CAPACITY, HOLD_ID,
                    NAME_TO_ID, SPECIES, TOTE_CAPACITY, TOTE_FOR_SPECIES)

REFUSALS = ("INSUFFICIENT_CREDIT", "TOTE_OVERFLOW", "PHANTOM_HOLD_ENTRY",
            "SPECIES_MISMATCH", "UNKNOWN_CELL", "NOT_A_MOVE", "DOUBLE_MOVE")


@dataclass
class Move:
    t: int          # day tick at booking
    src: str
    dst: str
    n: int
    sp: str


@dataclass
class Refusal:
    t: int
    op: str
    reason: str
    detail: str


@dataclass
class Books:
    landed: Dict[str, int] = field(default_factory=lambda: {s: 0 for s in SPECIES})
    totes: Dict[int, Dict[str, int]] = field(default_factory=dict)
    hold: Dict[str, int] = field(default_factory=lambda: {s: 0 for s in SPECIES})
    moves: List[Move] = field(default_factory=list)
    refusals: List[Refusal] = field(default_factory=list)
    tick: int = 0
    hooks: List[dict] = field(default_factory=list)
    ab_verdict: dict = field(default_factory=dict)

    def __post_init__(self):
        for cid in TOTE_CAPACITY:
            self.totes[cid] = {s: 0 for s in SPECIES}

    # ---------------------------------------------------------- queries --

    def tote_load(self, cid: int) -> int:
        return sum(self.totes[cid].values())

    def tote_species(self, cid: int) -> Optional[str]:
        """The tote's booked dominant species (constraint-cell check)."""
        for s, n in self.totes[cid].items():
            if n:
                return s
        return None

    def hold_total(self) -> int:
        return sum(self.hold.values())

    def conservation(self) -> dict:
        landed = sum(self.landed.values())
        totes = sum(self.tote_load(c) for c in self.totes)
        hold = self.hold_total()
        return {
            "landed_total": landed,
            "totes_total": totes,
            "hold_total": hold,
            "unbooked": landed - totes - hold,
            "balanced": landed == totes + hold,
        }

    def snapshot(self) -> dict:
        return {
            "landed": dict(self.landed),
            "totes": {
                CELL_NAMES[cid]: {
                    "sp": sp, "n": self.totes[cid][sp], "cap": cap,
                }
                for cid, cap in TOTE_CAPACITY.items()
                for sp in [self.tote_species(cid)] if sp
            },
            "hold": dict(self.hold),
            "moves": [m.__dict__ for m in self.moves],
            "refusals": [r.__dict__ for r in self.refusals],
            "conservation": self.conservation(),
            "hook_sets": list(self.hooks),
        }


class DeckLedger:
    """The app layer above the fabric: books custody, refuses violations,
    emits only balanced fabric traffic."""

    def __init__(self, tick: int = 0):
        self.books = Books(tick=tick)

    # ---------------------------------------------------------- booking --

    def _refuse(self, op: str, reason: str, detail: str) -> None:
        self.books.refusals.append(
            Refusal(self.books.tick, op, reason, detail))

    def resolve(self, name: str) -> Optional[int]:
        if name in NAME_TO_ID:
            return NAME_TO_ID[name]
        return None

    def canonical_species(self, sp: str) -> Optional[str]:
        sp = sp.lower()
        if sp in SPECIES:
            return sp
        if sp in ALIASES:
            return ALIASES[sp]
        return None

    def land(self, tote: str, sp: str, n: int) -> List[Flit]:
        """Landing events: n fish of species sp into a tote. Emits n
        EXTID-sourced landing effects (one per fish — the cameras see
        fish, not batches) IF the constraint checks pass."""
        cid = self.resolve(tote)
        if cid is None or cid not in TOTE_CAPACITY:
            self._refuse("land", "UNKNOWN_CELL", f"no tote named {tote!r}")
            return []
        sp = self.canonical_species(sp)
        if sp is None:
            self._refuse("land", "SPECIES_MISMATCH",
                         f"unknown species {sp!r} — alias table has no row")
            return []
        want = TOTE_FOR_SPECIES[sp]
        if cid != want:
            self._refuse("land", "SPECIES_MISMATCH",
                         f"{CELL_NAMES[cid]} takes {CELL_NAMES[want]} fish "
                         f"({sp} refused — constraint-cell rule)")
            return []
        if self.books.tote_load(cid) + n > TOTE_CAPACITY[cid]:
            self._refuse("land", "TOTE_OVERFLOW",
                         f"{CELL_NAMES[cid]} holds "
                         f"{self.books.tote_load(cid)}/{TOTE_CAPACITY[cid]}; "
                         f"landing of {n} {sp} refused")
            return []
        # booked: custody moves from the sea (unbooked source) to the tote
        self.books.landed[sp] += n
        self.books.totes[cid][sp] += n
        from .graph import LAND_DAT
        return [Flit(OP_EFF, src=EXTID, dst=cid, dat=LAND_DAT) for _ in range(n)]

    def move(self, src: str, dst: str, n: int, sp: str) -> List[Flit]:
        """Move n fish src→dst (tote→tote or tote→HOLD). A move is a
        balanced transaction: debit then credit, one fabric effect per
        fish, refused whole if either side fails."""
        op = "move"
        s, d = self.resolve(src), self.resolve(dst)
        if s is None or d is None:
            self._refuse(op, "UNKNOWN_CELL", f"{src!r}->{dst!r}")
            return []
        sp = self.canonical_species(sp)
        if sp is None:
            self._refuse(op, "SPECIES_MISMATCH", f"unknown species {sp!r}")
            return []
        if d == s or (s not in TOTE_CAPACITY and s != HOLD_ID) or \
           (d not in TOTE_CAPACITY and d != HOLD_ID):
            self._refuse(op, "NOT_A_MOVE", f"{src!r}->{dst!r} is not a deck move")
            return []
        if s == HOLD_ID:
            self._refuse(op, "NOT_A_MOVE", "hold is terminal custody — no hold-out")
            return []
        # double-move guard first: an identical booking at the same tick is
        # a replay no matter what the balances say (the precise diagnosis)
        for m in self.books.moves:
            if (m.t == self.books.tick and m.src == src and m.dst == dst
                    and m.sp == sp and m.n == n):
                self._refuse(op, "DOUBLE_MOVE",
                             f"identical move booked this tick ({src}->{dst} "
                             f"{n} {sp})")
                return []
        # debit check
        if self.books.totes[s][sp] < n:
            self._refuse(op, "INSUFFICIENT_CREDIT",
                         f"{CELL_NAMES[s]} holds {self.books.totes[s][sp]} {sp}; "
                         f"debit of {n} refused")
            return []
        # credit check
        if d == HOLD_ID:
            if self.books.hold_total() + n > HOLD_CAPACITY:
                self._refuse(op, "TOTE_OVERFLOW",
                             f"HOLD at {self.books.hold_total()}/{HOLD_CAPACITY}")
                return []
        else:
            if self.books.totes[d][sp] + n > TOTE_CAPACITY[d]:
                self._refuse(op, "TOTE_OVERFLOW",
                             f"{CELL_NAMES[d]} would hold "
                             f"{self.books.totes[d][sp] + n}/{TOTE_CAPACITY[d]} {sp}")
                return []
            dom = self.tote_dominant(d)
            if dom and dom != sp:
                self._refuse(op, "SPECIES_MISMATCH",
                             f"{CELL_NAMES[d]} is a {dom} tote; {sp} refused")
                return []
        # balanced: debit source, credit destination
        self.books.totes[s][sp] -= n
        if d == HOLD_ID:
            self.books.hold[sp] += n
        else:
            self.books.totes[d][sp] += n
        self.books.moves.append(Move(self.books.tick, src, dst, n, sp))
        # fabric: host-mediated tote->dst effects, src spoofed as the tote
        # (the deck gesture enters through the app adapter, Law 4)
        return [Flit(OP_EFF, src=s, dst=d, dat=0x0800) for _ in range(n)]

    def tote_dominant(self, cid: int) -> Optional[str]:
        for s, v in self.books.totes[cid].items():
            if v:
                return s
        return None

    def phantom_hold_entry(self, sp: str, n: int) -> List[Flit]:
        """The adversarial op: hold credit with NO booked debit. Refused
        by construction — the only hold writes are inside move()."""
        self._refuse("hold-credit", "PHANTOM_HOLD_ENTRY",
                     f"{n} {sp} appeared in HOLD with no tote debit — refused")
        return []

    def hooks(self, set_no: int, visible: int) -> dict:
        from .graph import hook_depth
        entry = {"set": set_no, "hooks_visible": visible,
                 "depth_fm": hook_depth(visible)}
        self.books.hooks.append(entry)
        return entry
