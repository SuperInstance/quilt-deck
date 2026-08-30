# Day-export JSON schema (v1) — the contract between backends, CLI, webui, report

One file, produced by `deck/cli.py day --export out.json` (any backend) and
consumed by the web console and the captain's report. Everything downstream
parses ONLY this shape.

```json
{
  "day": {"date": "2026-08-29", "sets": 5, "seed": 42, "backend": "python",
           "conformance": {"cold_quf_sha256": "…", "match": true}},
  "quf_sha256": "hex sha256 of the final QUF file",
  "quf_bytes": 123456,
  "cells": [
    {"id": 0, "name": "TOTE-PORT", "act": 1234, "refr": 0,
     "dials": [16 x int],
     "edges": [{"slot": 0, "peer": 15, "base": 0, "buckets": [8 x int],
                 "wh": 0, "age": 0}]}
  ],
  "books": {
    "landed":   {"pink": 3060, "chum": 900, "king": 70, "coho": 155},
    "totes":    {"TOTE-PORT": {"sp": "pink", "n": 312, "cap": 400}, "...": {}},
    "hold":     {"pink": 2748, "chum": 900, "king": 70, "coho": 155},
    "moves":    [{"t": 1, "from": "TOTE-PORT", "to": "HOLD", "n": 400, "sp": "pink"}],
    "refusals": [{"t": 2, "op": "move", "reason": "INSUFFICIENT_CREDIT",
                   "detail": "TOTE-PORT holds 12 pink; debit of 400 refused"}],
    "conservation": {"landed_total": 4185, "totes_total": 4185,
                      "hold_total": 4185, "balanced": true},
    "hook_sets":  [{"set": 1, "hooks_visible": 30, "depth_fm": 45.0}],
    "fires":      [{"cell": "TOTE-PORT", "dat": 24576, "tick": 37}]
  },
  "daylog": [ {"t": "set", "set": 1, "fish": [{"sp": "pink", "n": 612}]}, ... ]
}
```

Species keys: pink, chum, king, coho (canonical; aliases humpy/dog/keta/
chinook/silver resolve to these). Tote cells: TOTE-PORT (pink), TOTE-HOLD
(chum), TOTE-STBD-F (king), TOTE-STBD-A (coho). Moves are tote→tote or
tote→HOLD only. Refusal reasons (closed set): INSUFFICIENT_CREDIT,
TOTE_OVERFLOW, PHANTOM_HOLD_ENTRY, SPECIES_MISMATCH, UNKNOWN_CELL,
NOT_A_MOVE, DOUBLE_MOVE.

Conservation invariant (live-checkable): for every species,
landed == hold + sum(totes) + brail_loss where brail_loss is 0 in v1
(every fish is booked); the export must carry balanced=true only if it held.
