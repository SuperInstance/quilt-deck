# quilt-deck — the back-deck pipeline as a Quilt application

The F/V EILEEN back deck (docs/BACK-DECK-APP.md in quilt-verilog) as a real
application on the quilt backend: deck positions are cells, fish moves are
effects in balanced transactions, conservation is a runtime check of one
plain invariant, the whole
deck state travels in one QUF.

Three backends, one semantics:
- `python`  — soft fabric engine, bit-exact model of the quilt-verilog cell
- `esp32`   — the deck graph on quilt-esp32's vendored quilt-vm-c (host loopback)
- `fpga`    — iverilog cosim against rtl/q_serfabric_top.v (the serialized
              fabric front-end; golden vectors from the differential TB)

## Quickstart (every command verified from a clean clone)

Requirements: Python 3.10+ (stdlib only — the app imports nothing beyond
the standard library). `pytest` to run the test suite. The `fpga` backend
additionally needs [iverilog](http://iverilog.icarus.com/) (Icarus Verilog);
it is **optional** — python and esp32 lanes run without it, and the esp32
lane compiles its vendored C with your system cc, not a cross-toolchain.

```sh
python3 -m pytest tests/ -q                 # 31 tests, ~3s — everything green
python3 -m deck day --seed 7 --sets 3 \
    --export day.json --quf day.quf         # simulate a fishing day
python3 -m deck books day.json              # the balance ledger of the day
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

  The invariant the guard enforces, in one sentence: **every pound credited
  by an accepted move sits in exactly one custody cell (a tote or the
  hold) — `landed == totes + hold` at every commit; nothing is minted,
  nothing vanishes, and a refused move moves nothing.** This is the app
  face of the fabric's ledger identity A1/T1 (`emit = pipe + acc`;
  `emit + ext = book + pipe + (acc − book) + ext`, constant across
  commits). The canonical machine-checked wording is the BMC-55 statement
  in quilt-verilog's `docs/FORMAL-PROOFS.md` (commit b82cd19) — cite that
  document, not a paraphrase, until the L1/L2 strengthening lemmas close
  `mode prove`.

Also on the operator surface: `python3 -m deck day --summary` prints a
one-line summary; `python3 -m deck latest` symlinks `latest.json` to the
newest `day-*.json` export (the web console prefers it when present).

See docs/ARCHITECTURE.md. Not affiliated with a deployment; fleet-static-host
deployment is explicitly out of scope here.
