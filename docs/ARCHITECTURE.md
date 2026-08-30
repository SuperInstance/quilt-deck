# quilt-deck architecture — the back deck as a Quilt application

2026-08-29. The application lane's answer to "take it to the application
completely": the F/V EILEEN back-deck pipeline (quilt-verilog
docs/BACK-DECK-APP.md) as a real program on the quilt backend — three
backends, one semantics, one QUF.

## 1. The layering

```
        day log (deck/daylog.py)          operator CLI + web console
              |                                  |
        the ledger (deck/ledger.py)  -- custody, conservation, refusals
              |                          (backend-independent: ONE booking
              |                           authority for all engines)
   +----------+----------------+------------------+
   | python   | esp32          | fpga             |
   | fabric.py| deckbridge.c   | cosim.py         |
   | soft     | on quilt-vm-c  | on q_serfabric   |
   | engine   | (vendored,     | (iverilog, the   |
   | (model)  |  read-only)    |  real RTL)       |
   +----------+----------------+------------------+
              |                 |
              QUF (tools/quf.py reference; qufc.c in C; byte-identical)
```

The ledger never touches a backend: it decides WHAT fabric traffic may
exist (conservation is the runtime check — a fish in the hold without a
booked debit is refused before any effect is emitted), and the replay
feeds identical flit streams to whichever engine is selected. The three
engines must then agree on everything observable, which the conformance
suite asserts: frame-exact egress, identical fire streams, byte-identical
final QUF.

## 2. Domain as cells (the graph)

15 cells on the v1 ring budget (AIDW=4; host/io = EXTID=15):

```
 0 TOTE-PORT (pink)   1 TOTE-HOLD (chum, center)   2 TOTE-STBD-F (king)
 3 TOTE-STBD-A (coho) 4 HOLD (terminal custody)    5 ALIAS
 6 XID-MATCH          7 HOOK-COUNT                 8 SOUNDER
 9 LEDGER-SCALE      10 BESTSHOT                  11 AUDIT-CAPTAIN
12 NIGHT-CRON        13 AB-PROMOTE                14 CAM-UW
```

CAM-DECK-* adapter cells are host-side (Law 4: ingress is thin; their
landing events enter as EXTID-sourced effects). Learning graph (etab,
≤4 slots/cell — v1 budget): the label-bus spine totes → {ALIAS, XID,
LEDGER} + EXTID; HOLD ← totes (moves); HOOK → SOUNDER (the depth-pair
link that frees the sounder from the underwater camera); AUDIT ←
BESTSHOT/host; CAM-UW ← XID (retro-labels, host-mediated).

Every commissioned edge carries base = one fresh cofire (0x0100): a cold
fabric bootstraps through its priors, and base == fresh-ladder is the
readout equivalence the weight map defines.

## 3. App dials after the v2 collision audit

BACK-DECK-APP §4 proposed slots 11-15 for app dials; the v2 RTL grabbed
them for the echo gate + RQH bank, and slot 13 is now a read-only probe
(writes ignored, reads the live echo trace). The deck app remaps and the
double duty is REAL semantics (the soft engines model the v2 numerics
bit-exact, so the numbers agree everywhere):

| dial | slot | deck meaning | v2 silicon meaning |
|---|---|---|---|
| MATCH_WIN | 11 (KLE) | match window width | trace leak shift |
| HOOK_PITCH | 12 (FLOOR) | 1.5 fm × 100 | echo gate floor = causal window |
| — | 13 | unavailable (probe) | live trace (read-only) |
| PROMOTE_MARGIN | 14 (QDW; bit15=0) | promotion margin | RQH quanta (off) |
| QUARANTINE | 15 (QLEAK) | quarantine weight | RQH leak (off) |
| LEGAL_SET | AUDIT's 10 (HL) | species mask | audit pile decay |

HOOK_PITCH as FLOOR is the nicest coincidence in the system: the hook
cell's gate literally opens only within a causal window of its own fires.

## 4. Conservation (the runtime check)

The ledger books every fish in exactly one place; moves are balanced
transactions (debit then credit, idempotency keys — a replayed booking
key is DOUBLE_MOVE regardless of balances). Refusals (closed set):
INSUFFICIENT_CREDIT, TOTE_OVERFLOW, PHANTOM_HOLD_ENTRY, SPECIES_MISMATCH,
UNKNOWN_CELL, NOT_A_MOVE, DOUBLE_MOVE. Refused ops emit ZERO fabric
traffic (state-hash-asserted in tests). The live books check:
landed == totes + hold + unbooked, per species, re-computed by the web
console from the books rather than trusting the export's flag.

## 5. The three backends

- **python** — deck/fabric.py: the soft cell/ring engine, transcribed
  from the RTL op-for-op (saturating integers, ladder buckets, echo
  gate, hyperbola; v2 feature dials modeled exactly, defaults = v1).
  Always works; the reference model.
- **esp32** — esp32/deckbridge.c: the same engine as C, hosted on the
  REAL quilt-esp32 backend (firmware/vm/quilt_vm.c, vendored upstream,
  compiled read-only) with the five canon opcodes: cells are things
  (canon state JSON), every day flit is a queued reversible EFFECT
  (forward applies, inverse restores the snapshot), ticks drain in
  order. Host loopback today; the firmware path (PlatformIO) is the
  same code. qufc.c writes the QUF container byte-identically to
  tools/quf.py (golden-vector-proved both ways).
- **fpga** — cosim/tb_deck_cosim.v + deck/cosim.py: the day as framed
  op scripts played into q_serfabric_top (15 cells, gate-mode release
  word for the cold day; QUF boot for the warm replay), differential
  against the python model: egress frame-exact, end state dumped from
  the RTL's own registers (hierarchical probes), final QUF rebuilt from
  RTL state and byte-compared. The warm DUT proves the §6 doctrine:
  QUF boot restores dials + topology; the ladders re-earn byte-identical
  state from the day's own training stream replay.

### Determinism protocol (cosim)

tpw=15 (32768 cycles per deck-minute tick). Every op segment is ≤100
flits followed by an explicit tick; no op segment can straddle a tick
edge (>2.5× headroom), so both engines see the identical op/tick
interleaving without cycle-exact modeling. Bytes drive on negedge
(the house race rule); the egress capture counts bytes (the tbusy edge
lags the first byte by the NBA).

## 6. QUF everywhere

- Archive QUF: four v1 sections + `app.deck` (books, config) — the
  blessed unknown-section extensibility; quf.py skips it, the app
  reattaches it.
- Boot QUF: app sections stripped for the RTL loader profile
  (QUF-SPEC §9; quf_boot consumes the four known sections).
- `new` commissions a cold graph; a day's end state saves as the
  archive; `warm` loads it back (python restores walk state — the full
  path; the RTL loader profile re-earns it, and both end states are
  byte-equal, which is the conformance suite's warm theorem).

## 7. What is delegated where

- qufc.c (C QUF container, golden-exact): claude (EXECUTE INLINE)
- web console (deck/webui, offline static): opencode (kimi hit its
  weekly quota; the lane rule — use the tool that works)
- Captain's Report (deck/report.py): opencode
- everything semantics-critical (fabric.py, ledger.py, daylog.py,
  cosim TB + driver, deckbridge.c core, graph/dials): the foreman
