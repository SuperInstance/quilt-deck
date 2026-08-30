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

## FPGA status: NOT VERIFIED LOCALLY (not "differs")

`--backend fpga` does not complete in this environment: vvp runs
CPU-bound and is killed at its in-code 1200s timeout with zero egress
produced (measured 2026-08-30, seed 7 sets 3). An earlier note
here claimed "python & fpga: differs" — that was WRONG: the comparison
ran against a nonexistent/stale file. There is no evidence of
divergence, only absence of evidence. The treaty treats fpga as an
UNVERIFIED lane, not a broken one.

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

### Plan of record for this lane

Scout filing
`ecosystem/scout/2026-08-30-renode-verilator-cosim-deterministic-fpga-lane.md`
(in the OpenClaw workspace): Renode + Verilator co-simulation — a
deterministic, headless, CI-runnable FPGA lane that escapes the
iverilog-hang class entirely. Until that lands, the byte-identity
treaty rests on the python and esp32 lanes only.

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
