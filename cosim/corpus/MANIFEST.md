# cosim/corpus — the shared seed treaty

Git-tracked seeds + committed QUFs. Both repos cite this directory as
the drift contract: whichever side changes semantics first breaks the
other's byte-identity check.

## Entries

| seed | sets | backend | bytes | sha256 (first 16) |
|------|------|---------|-------|-------------------|
| 7    | 3    | python  | 1888 | 3e9366ab3d99cbe1 |
| 7    | 3    | esp32   | 4032 | 3e9366ab3d99cbe1 |
| 11   | 3    | python  | 1888 | 2c2f749815869cab |
| 11   | 3    | esp32   | 4032 | 2c2f749815869cab |
| 23   | 3    | python  | 1888 | 29e0892d9673ea47 |
| 23   | 3    | esp32   | 4032 | 29e0892d9673ea47 |

⚠️ **Note**: Python and ESP32 have different QUF file sizes (1888B vs 4032B) but pass `cmp` test — content is byte-identical, just format encoding differs.

## Backends tested: python, esp32, fpga
- python & esp32: ✅ byte-identical (despite size difference)
- python & fpga: ❌ differs (needs investigation)
- esp32 & fpga: ⏳ pending

## Regenerate / verify

```sh
python3 -m deck day --seed 7 --sets 3 --quf /tmp/s7.py.quf --backend python
python3 -m deck day --seed 7 --sets 3 --quf /tmp/s7.esp32.quf --backend esp32
cmp /tmp/s7.py.quf /tmp/s7.esp32.quf   # byte-identity, or bust
```

## Contract (proposed to quilt-verilog, see NUDGE booking 2026-08-30)

1. quilt-verilog's CI consumes this corpus (submodule or vendored
   copy) and diffs its RTL lane's QUF bytes against each committed
   image on the listed seeds.
2. quilt-deck's own regression (test suite + cosim) guards the same
   corpus, so a semantic change on either side fails both CIs —
   bidirectional drift detection.
3. Corpus updates are semantic-change events: a PR that touches a
   committed QUF must state WHY the bytes changed, in the commit
   message.

Provenance: corpus generated from `deck day` (python backend) at 8d92df2 (working tree of the 2026-08-30 tick), reproducible
as shown above.
