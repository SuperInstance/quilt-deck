"""deck.fabric — the soft quilt fabric, bit-exact model of quilt-verilog v2.1.

One engine, three backends. This module IS the python backend and the
referee model for the other two: every rule below is transcribed from the
RTL (rtl/q_cell_core.v, q_hebb_edge.v, q_echo_gate.v, q_dialfile.v) with the
commit hash noted in tests/test_fabric.py's golden checks.

The five opcodes (Law 2): BIND, LINK, EFFECT, VIEW, TICK. A flit is a flat
record {op, src, dst, a0, a1, a2, dat}; the fabric is a ring of cells; the
host lives at EXTID. Routing is positional: dst == ring slot; the host is
the io node. This model is logical-order-exact (the ring preserves order;
the day-log protocol keeps ops drained between tick bursts) rather than
cycle-exact — the cosim referee (cosim/) proves the order model matches the
clocks.

Integer state throughout, saturate-never-wrap, no floats in fleet state.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------- consts --

OP_BIND, OP_LINK, OP_EFF, OP_VIEW, OP_TICK, OP_ACK, OP_NAK = 0, 1, 2, 3, 4, 5, 6
OP_NAMES = {OP_BIND: "BIND", OP_LINK: "LINK", OP_EFF: "EFF", OP_VIEW: "VIEW",
            OP_TICK: "TICK", OP_ACK: "ACK", OP_NAK: "NAK"}

AIDW = 4          # cell id width (v1: 4-bit ring)
PW = 16           # payload word width
K_BUCKETS = 8     # ladder buckets (q_hebb_edge K)
B_BITS = 8        # ladder counter bits per bucket (q_hebb_edge B)
AGEW = 24         # hyperbola age width
NDIALS = 16       # dials per cell (q_dialfile ND)
EXTID = 0xF       # the host / io node id

# dial addresses (q_dialfile.v)
D_ETA_F, D_ETA_S, D_KF, D_KS, D_KA, D_THRESH, D_REFR = 0, 1, 2, 3, 4, 5, 6
D_COSMIN, D_P0E, D_MODE, D_HL = 7, 8, 9, 10
D_KLE, D_FLOOR, D_FTRACE, D_RQ, D_RQL = 11, 12, 13, 14, 15

DIAL_DEFAULTS = [0x0800, 0x0080, 6, 12, 5, 0x6000, 4, 0x2CCD,
                 20, 0, 64, 2, 0x0000, 0x0000, 0x0008, 0x0008]


def s16(x: int) -> int:
    """Interpret a u16 bit pattern as signed (two's complement)."""
    return x - 0x10000 if x & 0x8000 else x


def u16(x: int) -> int:
    return x & 0xFFFF


def sclip16(x: int) -> int:
    """q_cell_core sclip16: saturate to signed 16-bit full scale."""
    if x > 32767:
        return 0x7FFF
    if x < -32768:
        return -32768
    return x


def sclip16_bits(x: int) -> int:
    v = sclip16(x)
    return v & 0xFFFF


@dataclass
class Flit:
    op: int
    src: int
    dst: int
    a0: int = 0
    a1: int = 0
    a2: int = 0
    dat: int = 0

    def fields(self) -> Tuple[int, int, int, int, int, int, int]:
        return (self.op, self.src, self.dst, self.a0, self.a1, self.a2, self.dat)


@dataclass
class HebbEdge:
    """q_hebb_edge state + the v2 echo-gate interplay, exact."""
    base: int = 0                    # bind-time base weight (u16)
    buckets: List[int] = field(default_factory=lambda: [0] * K_BUCKETS)
    hl_cnt: int = 0                  # ladder half-life counter (u16)
    wh: int = 0                      # hyperbolic integer weight (u16)
    age: int = 0                     # hyperbola age counter (u32)
    ovf: bool = False                # sticky train-time saturation

    # ---- readout (registered sequencer modeled closed-form) ----
    def readout(self, mode: int) -> int:
        if mode:
            # whs = wh > 255 ? 0xFFFF : wh << 8  (saturating)
            return 0xFFFF if self.wh > 255 else u16(self.wh << 8)
        # ladder: acc = sum_i c[i] << (K - i); lad = top PW bits, saturating.
        acc = 0
        for i in range(K_BUCKETS):
            acc += self.buckets[i] << (K_BUCKETS - i)
        if acc > 0xFFFF:
            return 0xFFFF
        return acc
        # (AW = K+B+1 = 17 > PW=16: sat bit = acc[16]; exact per g_lad_sat)

    def weight(self, mode: int) -> int:
        """o_w = saturate(base + engine_readout)."""
        w = self.base + self.readout(mode)
        return 0xFFFF if w > 0xFFFF else w

    # ---- commands ----
    def train(self, mode: int, gclass: int = 0) -> None:
        if not mode:  # ladder (graded class for v2 cmd 101; 0 == cmd 001)
            g = min(gclass, K_BUCKETS - 1)
            if self.buckets[g] >= (1 << B_BITS) - 1:
                self.ovf = True
            else:
                self.buckets[g] += 1
        else:         # hyperbola
            if self.wh == 0xFFFF:
                self.ovf = True
            else:
                self.wh += 1

    def tick(self, mode: int, hl: int, p0e: int) -> None:
        if not mode:
            # hlc = hl_cnt + 1 >= hl  -> shift; else count
            if (self.hl_cnt + 1) >= hl:
                self.buckets = [0] + self.buckets[:-1]
                self.hl_cnt = 0
            else:
                self.hl_cnt = u16(self.hl_cnt + 1)
        else:
            p0 = 1 << p0e
            msb = 0
            for j in range(PW):
                if self.wh >> j & 1:
                    msb = j
            ivr = p0 >> (2 * msb)
            ival = ivr if ivr else 1
            if self.wh != 0 and (self.age + 1) >= ival:
                self.wh = (self.wh - 1) & 0xFFFF
                self.age = 0
            else:
                self.age = (self.age + 1) & ((1 << AGEW) - 1)


@dataclass
class EdgeSlot:
    peer: int = 0
    valid: bool = False
    engine: HebbEdge = field(default_factory=HebbEdge)


@dataclass
class Cell:
    """q_cell: core + dialfile + echo gate + edge array. ids == ring slots."""
    myid: int
    dials: List[int] = field(default_factory=lambda: list(DIAL_DEFAULTS))
    # core state
    bound: bool = False
    cell_id: int = 0
    act: int = 0          # signed, stored as bits (u16)
    refr: int = 0         # u16
    edges: List[EdgeSlot] = field(default_factory=list)
    # v2 echo gate (q_echo_gate)
    ftrace: int = 0       # u16

    def __post_init__(self):
        if not self.edges:
            self.edges = [EdgeSlot() for _ in range(4)]  # EDGES_N=4

    # dial view fan-ins
    @property
    def d_ka(self) -> int:
        return self.dials[D_KA] & 0xF

    @property
    def d_thresh(self) -> int:
        return s16(self.dials[D_THRESH])

    @property
    def d_mode(self) -> int:
        return self.dials[D_MODE] & 1

    @property
    def d_hl(self) -> int:
        return self.dials[D_HL]

    @property
    def d_kle(self) -> int:
        return self.dials[D_KLE] & 0xF

    @property
    def d_floor(self) -> int:
        return self.dials[D_FLOOR]

    # ---- echo gate combinational view ----
    def gate_live(self) -> bool:
        return self.d_floor == 0 or self.ftrace >= self.d_floor

    def gate_gclass(self) -> int:
        if self.d_floor == 0 or self.ftrace == 0:
            return 0
        msb = 0
        for j in range(PW):
            if self.ftrace >> j & 1:
                msb = j
        return 15 - msb

    def leak_ftrace(self) -> None:
        f = self.ftrace
        if f == 0:
            return
        fleak = f - (f >> self.d_kle) if self.d_kle else f
        snap = (fleak <= self.d_floor) or (fleak <= 1) or (fleak >= f)
        self.ftrace = 0 if snap else u16(fleak)


class Fabric:
    """The soft ring: send flits from the host, observe egress to the host.

    Semantics transcribed from the RTL with per-op source comments. The
    egress log is the host-visible byte-stream twin of the serialized
    fabric's o_stx frames (cosim/tb_deck_cosim.v compares them flit for
    flit).
    """

    def __init__(self, ncell: int = 15, edges_n: int = 4):
        assert edges_n <= 4, "EIW=2 in v1: at most 4 edge slots per cell"
        self.ncell = ncell
        self.edges_n = edges_n
        self.cells: List[Cell] = [Cell(myid=i) for i in range(ncell)]
        self.egress: List[Flit] = []       # flits delivered to the host
        self.fires: List[dict] = []        # semantic fire events (books)

    # ------------------------------------------------------------ ops --

    def _respond(self, dst: int, src: int, nak: bool, a2: int, dat: int) -> None:
        self.egress.append(Flit(OP_NAK if nak else OP_ACK, src=src, dst=dst,
                                a2=a2, dat=dat))

    def _deliver(self, f: Flit) -> None:
        """One flit lands at f.dst (routing already done by send())."""
        if f.dst == EXTID:
            self.egress.append(f)
            return
        if f.dst >= self.ncell:
            # misaddressed to a nonexistent node: v1 traffic contract
            # (SYNTHESIS I1 honest limit) — dropped, booked
            return
        c = self.cells[f.dst]

        if not c.bound:
            # ST_UNB: any flit; only BIND commissions
            nak = f.op != OP_BIND
            if f.op == OP_BIND:
                c.cell_id = f.a0 & 0xF
                c.bound = True
            self._respond(f.src, c.cell_id, nak, f.a2, 0)
            return

        if f.op in (OP_ACK, OP_NAK, OP_TICK):
            return  # consumed, no action

        if f.op == OP_BIND:
            addr = f.a0 & 0xF
            if addr != D_FTRACE:          # dial 13 is a read-only probe
                c.dials[addr] = u16(f.a1)
            self._respond(f.src, c.cell_id, False, f.a2, 0)
            return

        if f.op == OP_LINK:
            slot = f.a0 & (self.edges_n - 1)
            c.edges[slot].peer = f.src & 0xF
            c.edges[slot].valid = True
            c.edges[slot].engine.base = u16(f.a1)
            self._respond(f.src, c.cell_id, False, f.a2, 0)
            return

        if f.op == OP_EFF:
            # edge scan: first matching valid slot (ST_EFFT order)
            hit = None
            for e in c.edges:
                if e.valid and e.peer == (f.src & 0xF):
                    hit = e
                    break
            if hit is None:
                return  # unknown source: dropped silently (link before effect)
            mode = c.d_mode
            if c.gate_live():
                # v2 graded cofire (cmd 101; class 0 == exact v1 cmd 001)
                hit.engine.train(mode, c.gate_gclass())
            w = hit.engine.weight(mode)   # read the weight back (post-train)
            prod = w * s16(u16(f.dat))    # unsigned w * signed dat (33-bit)
            act = s16(c.act)
            c.act = sclip16_bits(act + (prod >> 15))
            return  # effects are silent (no ACK unless a fire)

        if f.op == OP_VIEW:
            sel = f.a0 & 0x3
            if sel == 0:
                self._respond(f.src, c.cell_id, False, f.a2, u16(c.act))
            elif sel == 1:
                wacc = 0
                for e in c.edges:
                    if e.valid:
                        wacc += e.engine.weight(c.d_mode)
                self._respond(f.src, c.cell_id, False, f.a2,
                              0xFFFF if wacc > 0xFFFF else wacc)
            elif sel == 2:
                idx = f.a1 & 0xF
                val = u16(c.ftrace) if idx == D_FTRACE else c.dials[idx]
                self._respond(f.src, c.cell_id, False, f.a2, val)
            else:
                self._respond(f.src, c.cell_id, True, f.a2, 0)  # no cos in v1
            return

        # unknown opcode -> NAK (ST_IDLE default branch)
        self._respond(f.src, c.cell_id, True, f.a2, 0)

    def send(self, f: Flit) -> None:
        """Host injects one flit at the io node (src is whatever it claims)."""
        self._deliver(f)

    # ------------------------------------------------------------ tick --

    def tick(self, nticks: int = 1) -> None:
        """s_tick strobes: decay sweep, ftrace leak, act leak, fire test.

        Order per ST_TICK..ST_TLEAK..ST_FIRE; fire value is the PRE-leak act
        (afire latches the old value); fanout walks valid slots in order.
        """
        for _ in range(nticks):
            for c in self.cells:
                if not c.bound:
                    continue
                mode, hl, p0e = c.d_mode, c.d_hl, c.dials[D_P0E] & 0x1F
                for e in c.edges:
                    if e.valid:
                        e.engine.tick(mode, hl, p0e)
                # ST_TLEAK: leak trace, then act; fire test on pre-leak act
                fired = (s16(c.act) >= c.d_thresh) and c.refr == 0
                if fired:
                    # eg_fire pulses the cycle AFTER the leak strobe and
                    # wins by priority in q_echo_gate: net refill to 0xFFFF
                    c.ftrace = 0xFFFF
                else:
                    c.leak_ftrace()
                act = s16(c.act)
                c.act = sclip16_bits(act - (act >> c.d_ka))
                if fired:
                    afire = u16(act)  # pre-leak act (RTL latches old value)
                    self.fires.append({"cell": c.myid, "dat": afire})
                    for e in c.edges:
                        if e.valid:
                            self._deliver(Flit(OP_EFF, src=c.cell_id,
                                               dst=e.peer, dat=afire))
                    c.act = 0
                    c.refr = c.dials[D_REFR]
                else:
                    if c.refr:
                        c.refr -= 1

    # ------------------------------------------------------------ dump --

    def snapshot(self) -> dict:
        return {
            "ncell": self.ncell,
            "cells": [
                {
                    "id": c.myid,
                    "bound": c.bound,
                    "cell_id": c.cell_id,
                    "act": u16(c.act),
                    "refr": c.refr,
                    "ftrace": c.ftrace,
                    "dials": list(c.dials),
                    "edges": [
                        {
                            "slot": i,
                            "valid": e.valid,
                            "peer": e.peer,
                            "base": e.engine.base,
                            "buckets": list(e.engine.buckets),
                            "wh": e.engine.wh,
                            "age": e.engine.age,
                        }
                        for i, e in enumerate(c.edges)
                    ],
                }
                for c in self.cells
            ],
            "fires": list(self.fires),
            "egress": [
                {"op": f.op, "src": f.src, "dst": f.dst, "a0": f.a0,
                 "a1": f.a1, "a2": f.a2, "dat": f.dat}
                for f in self.egress
            ],
        }


# ---------------------------------------------------------------- framer --

def flit_frame(f: Flit) -> bytes:
    """10-byte q_serfabric frame: MSB-first bytes of the 80-bit word
    {op[2:0], src[3:0], dst[3:0], a0, a1, a2, dat, pad[4:0]=0}."""
    word = ((f.op & 0x7) << 77) | ((f.src & 0xF) << 73) | ((f.dst & 0xF) << 69) \
        | ((f.a0 & 0xFFFF) << 53) | ((f.a1 & 0xFFFF) << 37) \
        | ((f.a2 & 0xFFFF) << 21) | ((f.dat & 0xFFFF) << 5)
    return word.to_bytes(10, "big")


def parse_frame(frame: bytes) -> Flit:
    assert len(frame) == 10
    word = int.from_bytes(frame, "big")
    return Flit(
        op=(word >> 77) & 0x7,
        src=(word >> 73) & 0xF,
        dst=(word >> 69) & 0xF,
        a0=(word >> 53) & 0xFFFF,
        a1=(word >> 37) & 0xFFFF,
        a2=(word >> 21) & 0xFFFF,
        dat=(word >> 5) & 0xFFFF,
    )
