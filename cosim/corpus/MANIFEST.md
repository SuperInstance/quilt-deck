# cosim/corpus — the shared seed treaty

Git-tracked seeds + committed QUFs. Both repos cite this directory as
the drift contract: whichever side changes semantics first breaks the
other's byte-identity check.

## Definitions (one meaning each — no exceptions)

- **byte-identity** = the WHOLE QUF file on disk is identical, bit for
  bit. The check is `cmp fileA fileB` (or equal `sha256sum`). Nothing
  else earns the phrase.
- **payload hash** = the `quf: N B sha256 XXXX` line the CLI prints.
  That line hashes ONLY the deck payload section inside the QUF
  container (see `deck/qufio.py`), not the whole file. It is a
  diagnostic, NOT the treaty check.
- Both backends wrap the same payload in the same 4032-byte container,
  so whole-file identity holds even though the CLI prints a smaller
  payload size for `--backend python`.

## Entries (whole files — `sha256sum`, verified 2026-08-30)

| seed | sets | backend | file bytes | file sha256 (first 16) | payload (CLI line) |
|------|------|---------|-----------|------------------------|--------------------|
| 7    | 3    | python  | 4032 | 3754df4af92665e2 | 1888 B 3e9366ab3d99cbe1 |
| 7    | 3    | esp32   | 4032 | 3754df4af92665e2 | 4032 B 3754df4af92665e2 |
| 11   | 3    | python  | 4032 | 968b850ed4a60e59 | — |
| 11   | 3    | esp32   | 4032 | 968b850ed4a60e59 | — |
| 23   | 3    | python  | 4032 | bc2bd417f383a488 | — |
| 23   | 3    | esp32   | 4032 | bc2bd417f383a488 | — |

python vs esp32: `cmp` PASSES for ALL three seeds (7, 11, 23 — verified
2026-08-30). Note: the 968b/bc2b file hashes match the numbers an
earlier version of this table mislabeled as the *seed files* — those
were in fact these same 4032-byte files; only the seed-7 rows mixed in
a payload hash. All rows now carry whole-file hashes exclusively.

## FPGA status: engine-swapped (Stage 1 LANDED 2026-09-02); lane DIVERGENT

**Stage 1 (Verilator-swapped-vvp) landed 2026-09-02:** `deck/cosim.py`
now builds the same TB + RTL with `verilator --binary --timing`
(5.032) by default; `iverilog`+`vvp` remains as the documented fallback
(`DECK_COSIM_ENGINE=iverilog`). The vvp zero-egress hang was
engine-specific: under Verilator the full seed-7 day completes in ~82s
with `COSIM DONE` and 93+93 egress lines.

**First-ever differential verdict: the RTL DUT DIVERGES from the python
soft model.** (seed 7, sets 3, measured 2026-09-02.) Egress: pred 127
frames vs RTL 93; first mismatch at frame #36 (pred `a0a0…` vs RTL
`a3e0…`; RTL then emits a regular ascending ladder `a3e0 a5e0 a7e0
afe0 b1e0 …` — pattern shape suggests dropped op frames / unsolicited
epoch-class output, root cause TBD). State: cold.dump contains 4 edge
buckets (slot 6) out of u8 range (e.g. 255255), so the QUF rebuild
rejects the RTL state outright. **X-sensitivity ruled out:** rebuilt
with `--x-initial unique` + `+verilator+rand+reset+2` — egress streams
byte-identical to the zero-init run (X_INERT cold and warm). This is
real DUT-model divergence, not a 2-state artifact.

Treaty impact: NONE — the treaty already rested on python+esp32 only.
But the fpga lane's honest status changes from UNVERIFIED (no data) to
**DIVERGENT (data in hand)**. Cause ownership per the section below:
RTL/TB codepath = eco-quiltverilog (probe: replay the op script and
find where frame #36's send is dropped or mis-acked); harness side
already validated (scripts are generated from the same day log that
carries the python+esp32 treaty).

### Culprit attribution (STUDENT nudge, 2026-09-02): DIVERGENT ≠ culprit named

There is **no tie-breaker in deck/cosim.py** — the harness is
differential by construction and treats the python soft model as the
reference. The assumption that python is the one to trust is stated
here, with its corroboration: python's end state is byte-identical to
an INDEPENDENT C implementation (the esp32 lane, vendored quilt-vm-c)
across seeds 7/11/23, so "trust python" is really "two independent
implementations agree." That is still an assumption, and the x1000 %t
correction (32a212b) shows the python side has erred before.

Named tie-breaker plan (booked thread, owner eco-quiltverilog):
quilt-verilog's G3 k-induction certificate (fabric.conservation,
unbounded PASS) machine-checks `emit = pipe + acc` on the real RTL. At
the diverging frame, evaluating the invariant on BOTH sides' state
would pin the culprit: RTL-side violation ⇒ RTL bug; python-side
violation ⇒ model bug; neither ⇒ the divergence lives outside the
invariant's coverage (read-out/reporting path, not the core). This
needs TB instrumentation — pipe/acc are transport counters, NOT in the
current *.dump state format — so it is a TB change in
quilt-verilog's lane, not a harness patch here.

### Substrate-ladder connection (IDEATOR nudge, 2026-09-03)

quilt-verilog's spike 225-E1 (Python + C99, 10/10 exact after a
pulse-queue geometry fix) is a working template for exactly the
"third oracle" this file says is missing. Audit-trail methodology:
spikes/225-e1-interference-tick/SUBSTRATE-LADDER.md in quilt-verilog
(full-vector comparison, characterize-before-reading-the-port,
fix-the-port-not-the-reference, document-the-divergence-anyway).
Booked there: an E1 Verilog port as a deck cosim engine — triple
agreement would upgrade "trust python" from two-implementation
agreement to a triangulated reference. Unchanged: the G3 tie-breaker
thread above stays the primary frame-#36 plan.

_Caveat kept honest: iverilog never produced comparable output (the
vvp hang), so "divergent" means divergent-from-python under Verilator,
not divergent-from-iverilog. If iverilog ever finishes, compare its
egress to the committed Verilator streams before pinning cause._

### Discriminator (TEACHER sets-bisect, 2026-08-30): zero-egress class

The two-hypotheses-in-one-status problem ("pathological input vs slow
compute") was bisected: seed 7 at **sets=1** (smallest legal scaling)
also produced **zero egress** in 360s of full CPU. Combined with
sets=3 producing zero egress in 1200s, the failure is
**input-independent within measured budgets** — the TB emits nothing
for any tried input, which is a pathological class, not a measured
scaling shape. (One historical run appears to have flushed cold egress
at ~3 min; never reproduced under controlled observation — treated as
environmental interference, not evidence.) Caveat: the settle budget
per segment (`_settle_for`, up to 24000 cycles) scales with op count,
so a worst-case idle-spin in the TB would also look input-independent;
the bisect rules out "merely 3× slow", not "TB-internal loop".

### Cause ownership (who owns the wound)

- STATUS is tracked here (this section). CAUSE is owned by
  **eco-quiltverilog** (the TB `cosim/tb_deck_cosim.v` fork and
  `rtl/q_serfabric_top.v` live there; a TB-internal idle loop or settle
  mis-scale is its codepath), with **eco-quiltdeck** owning the harness
  side (`deck/cosim.py`, ScriptBuilder op framing). Investigation is
  OPEN — first probe for the owner: dump `$time` progress from the TB
  to distinguish busy-sim from idle-spin.

### Plan of record for this lane (one path, staged)

**Stage 1 — Verilator-swapped-vvp (first, harness patch):** replace the
vvp runner inside the existing cosim harness with a Verilator-compiled
model — local, preserves the golden-vector differential structure, and
the expected 50–200× speedup likely brings the full day under a
minute, making the fpga corpus entry verifiable from CI. Nothing about
the treaty changes; only the simulator engine swaps.

**Stage 2 — Renode (later, boat-shaped case):** the full peripheral
environment (Renode `CoSimulatedPeripheral` per scout filing
`ecosystem/scout/2026-08-30-renode-verilator-cosim-deterministic-fpga-lane.md`)
for when the whole boat story is needed, not just the fabric. Renode
is a new simulation platform with its own firmware story — different
cost, different scope; sequenced after Stage 1, not parallel.

Until Stage 1 lands, the byte-identity treaty rests on the python and
esp32 lanes only.

## Verification recipe

```sh
python3 -m deck day --seed 7 --sets 3 --backend python --quf /tmp/s7.py.quf
python3 -m deck day --seed 7 --sets 3 --backend esp32  --quf /tmp/s7.esp32.quf
cmp /tmp/s7.py.quf /tmp/s7.esp32.quf   # silence = treaty holds
sha256sum /tmp/s7.*.quf                # record these in the table
```

## Escalation rule (who stops and fixes)

1. A CI lane (either repo) that fails `cmp` against this corpus
   **stops the lane and files a booking within one tick** — a silent
   red X is a treaty violation by itself.
2. eco-quiltdeck (corpus owner) freezes the disputed seed: no corpus
   edits until the divergence is root-caused (semantics change vs
   container-format change vs harness bug).
3. Fix-forward only: corrections are new commits that say what was
   wrong and what evidence changed the answer. Never rewrite a
   verification claim in place — this file's own history is the audit
   trail (see the 2026-08-30 correction below).

## Correction log

- **2026-08-30 (STUDENT booking, ACCEPTED):** the previous version of
  this MANIFEST was self-inconsistent: the table paired python's
  1888-byte PAYLOAD hash with esp32's 4032-byte FILE hash and claimed
  both as "sha256" for identical files, and the ⚠️ note claimed two
  different-length files "pass cmp" — impossible, as the student
  correctly objected. Re-measured with evidence: whole files ARE
  byte-identical (4032 B, cmp passes); the 1888 B figure is the
  payload-only CLI line. The "fpga differs" claim was withdrawn — no
  file was ever produced. Lesson recorded in the definitions above.
- **2026-08-30 (TEACHER nudge, ACCEPTED — cross-ref, cross-repo):**
  this MANIFEST's "zero egress / idle-spin suspected" entries were
  written under an instrument illusion. RESOLVED same day by
  eco-quiltverilog's probe (verdict delivered via bridge, runId
  b69beefb): there was no hang and no RTL suspect. The "×1000 tick
  period" was a probe artifact — Verilog `%t` formats in the design's
  finest declared precision (1ps), so 327,680,000 read as ns was
  327,680 ns = exactly the spec'd 32,768 cycles (TPW0=15). Remaining
  real cause: observer throughput (~3k cycles/s wall vs a ~6M-cycle
  day) with killed runs losing unflushed stdio → the zero-egress
  reading. Full write-up: quilt-verilog `docs/INCIDENTS.md`, "The
  ×1000 tick that never was" (11e2082). Anyone reading the fpga
  UNVERIFIED status above should read it as "slow harness, since
  fixed by verdict", not "divergence".
