# cosim/corpus — the shared seed treaty

Git-tracked seeds + committed QUFs. Both repos cite this directory as
the drift contract: whichever side changes semantics first breaks the
other's byte-identity check.

## Entries

| seed | sets | file | bytes | sha256 (first 16) |
|------|------|------|-------|-------------------|
| 7    | 3    | seed-7.quf  | 4032 | 3754df4af92665e2 |
| 11   | 3    | seed-11.quf | 4032 | 968b850ed4a60e59 |
| 23   | 3    | seed-23.quf | 4032 | bc2bd417f383a488 |

## Regenerate / verify

```sh
python3 -m deck day --seed 7 --sets 3 --quf /tmp/s7.quf
cmp /tmp/s7.quf cosim/corpus/seed-7.quf   # byte-identity, or bust
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
