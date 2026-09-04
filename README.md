# quilt-deck — the back-deck pipeline as a Quilt application

The F/V EILEEN back deck (docs/BACK-DECK-APP.md in quilt-verilog) as a real
application on the quilt backend: deck positions are cells, fish moves are
effects in balanced transactions, conservation is a runtime check of one
plain invariant, the whole
deck state travels in one QUF.

Three backends, one semantics — two verified, one divergent:
- `python`  — soft fabric engine, bit-exact model of the quilt-verilog cell
- `esp32`   — the deck graph on quilt-esp32's vendored quilt-vm-c (host loopback)
- `fpga`    — Verilator cosim against rtl/q_serfabric_top.v (iverilog+vvp
  fallback: `DECK_COSIM_ENGINE=iverilog`). **EXPERIMENTAL / DIVERGENT**:
  the sim now completes (~82s under Verilator, measured 2026-09-02) and
  the first differential verdict is in — the RTL egress diverges from
  the python model at frame #36 and the dumped edge buckets overflow u8;
  X-sensitivity ruled out. Byte-identity claims rest on the python and
  esp32 lanes only. Evidence and cause ownership:
  `cosim/corpus/MANIFEST.md` — the honest negative lives there.

## Quickstart (python + esp32 commands verified from a clean clone; fpga unverified, see above)

Requirements: Python 3.10+ (stdlib only — the app imports nothing beyond
the standard library). `pytest` to run the test suite. The `fpga` backend
additionally needs [iverilog](http://iverilog.icarus.com/) (Icarus Verilog);
it is **optional** — python and esp32 lanes run without it, and the esp32
lane compiles its vendored C with your system cc, not a cross-toolchain.

**What is actually verified on the esp32 lane (honest scope):** the
`test_backend_conformance_python_esp32` test builds nothing — it needs
`esp32/build/deckbridge` to exist (`make -C esp32 deckbridge`); on a fresh
clone **the test SKIPS silently**, and the suite green means python-lane
coverage only. Byte-identity has been verified with the toolchains on the
author machines (gcc/clang, Linux + WSL); cc is not part of the treaty —
if your compiler produces a divergent archive, that is a bug report, not
a broken promise, and `tests/test_day.py` will name the diverging hash.
A `deck console` smoke test (server boots, serves index.html + app.js)
lands with this note.

```sh
python3 -m pytest tests/ -q                 # ~32 tests, ~6s — everything green (esp32 test skips if esp32/build/deckbridge not built: `make -C esp32 deckbridge`)
python3 -m deck day --seed 7 --sets 3 \
    --export day.json --quf day.quf         # simulate a fishing day
python3 -m deck books day.json              # the balance ledger of the day
python3 -m deck books day.quf               # same view, straight from the archive
python3 -m deck verify day.quf              # structural + conservation check
python3 -m deck console --port 8717         # web console at http://127.0.0.1:8717
```

What a day produces and where to look:

- `day.json` — the day export: books (conservation, totes, hold, moves,
  refusals, hook sets), fires, and the fabric summary. This is what the
  console and `deck books` render.
- `day.quf` — the whole deck state in one QUF archive (core fabric sections
  + `app.deck` books). `deck verify` reads it back; the esp32 backend
  produces a byte-identical archive for the same seed.
- stdout — a summary line (landed / balance), the QUF size + sha256 prefix,
  and every adversarial op REFUSED with its booked reason. Expect ~3
  refusals on seed 7 — those are the conservation guard working.

Trust boundary (DEVIL nudge 2026-09-04, stated so it can't be over-read):
`deck books day.quf` treats the archive as **operator-supplied trusted
input, not verified provenance**. It refuses to render a structurally
broken archive (`qufio.verify`: magic + section table + lengths parse —
truncation and header corruption fail it), but there is **no content
checksum in the QUF format**: a flipped byte that still parses renders
as-is. Nothing in the view proves the archive came from this pipeline. Proving that is a
separate act: `deck verify` for structure + conservation, and the corpus
hashes in `cosim/corpus/MANIFEST.md` for treaty artifacts. The day export
(`day.json`) is written by the same run that wrote the archive, so for
self-produced days the two views are pinned identical by test.

  The invariant the guard enforces, in one sentence: **every pound credited
  by an accepted move sits in exactly one custody cell (a tote or the
  hold) — `landed == totes + hold` at every commit; nothing is minted,
  nothing vanishes, and a refused move moves nothing.** This is the app
  face of the fabric's ledger identity A1/T1 (`emit = pipe + acc`;
  `emit + ext = book + pipe + (acc − book) + ext`, constant across
  commits). The canonical machine-checked wording is quilt-verilog's
  `docs/FORMAL-PROOFS.md`, which since 2026-08-30 carries an UNBOUNDED
  proof: `fabric.conservation.pdr.sby`, `mode prove`, engine `abc pdr`,
  25.9 s, frame 9 (commit 4b67c30; supersedes the BMC-55 statement cited
  at b82cd19). Re-verifiers: `abc pdr` is load-bearing — the same
  harness under smtbmc k-induction FAILS (b82cd19, re-confirmed on the
  current tree).

Also on the operator surface: `python3 -m deck day --summary` prints a
one-line summary; `python3 -m deck latest` symlinks `latest.json` to the
newest `day-*.json` export (the web console prefers it when present).

See docs/ARCHITECTURE.md. Not affiliated with a deployment; fleet-static-host
deployment is explicitly out of scope here.
