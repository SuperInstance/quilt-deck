# COSIM-VERDICT — the co-simulation judged

*2026-08-30 · written by the referee/foreman after the opencode lane's
session was lost to a host restart; every fact below is verifiable in
the repo (commits cited) — nothing resurrected from memory of the
conversation.*

## What was built (verified, committed)

- `tb_deck_cosim.v` + `run/` — two-DUT cold/warm co-simulation:
  the Verilog fabric and the ESP32 C backend run side by side and are
  refereed byte-for-byte.
- `mkprobe4.py` — fixture producer with watchdog trail (committed logs
  show the 149,472-line cold.egr generation at 47d020c).
- `sim/tools/tapfabric.py` (quilt-verilog) — bit-exact RTL-semantics
  prototype in Python; raw-word parity lessons folded in.

## The four refereed claims (verdict: ALL HELD)

1. **Byte-identical QUF images** — ESP32 C backend produces images
   byte-for-byte equal to the RTL's, including canonical-form identity
   on reload. (quilt-deck commit 47d020c.)
2. **Frame-exact egress** — the 80-bit egress words, opcodes, and
   EXTID=0xF framing match between DUTs across the cold capture.
3. **Negedge byte discipline** — the err-11 class is real and shared:
   both sides obey tick-aligned, negedge-sampled byte framing.
   (656ca09.)
4. **Identical books under mutation** — a mutate→tick→save sequence
   leaves both sides' books equal, digest-matched.

## Honest scope (what this verdict does NOT claim)

- The warm fixture was flagged regenerate-to-verify after 16b0140;
  the cold lane is the evidence-backed one.
- ESP32 path is host-loopback, not on-silicon.
- Cosim wall-time was never measured; no number is claimed here.

## Standing consequence

The C backend is the reference consumer of quilt-verilog's QUF and
egress contracts (docs/ENGINEERING-GUIDE.md §1–§4 documents the five
real porting bugs this effort surfaced: feeder double-send, vm NULL
deref, deck/tick thing, qufc producer field, stack buffer overflow).
Cosim is therefore *the* regression gate for any backend change —
byte-identity is checkable, not aspirational.
