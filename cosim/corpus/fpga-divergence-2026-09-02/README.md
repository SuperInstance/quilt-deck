# fpga lane — first differential evidence (2026-09-02)

Regenerable by `python3 -m deck.cosim`-style run (deck/cosim.py, seed 7,
sets 3, Verilator 5.032 `--binary --timing`); committed so the
divergence claim is auditable without a re-run. Everything here is
input+output pairs: cold.ops/warm.ops/boot.hex are the provenance
inputs, .egr/.dump the outputs.

- cold.egr / warm.egr — RTL egress under default (zero-init) build: 93
  lines each; python predicts 127; first mismatch at frame #36.
- *.egr.xzero-init — same, from the `--x-initial unique` +
  `+verilator+rand+reset+2` rebuild: byte-identical to the zero-init
  streams (X_INERT). Rules out 2-state artifacts.
- cold.dump / warm.dump — contain 4 edge buckets (slot 6) over u8 range
  (e.g. 255255); quf rebuild rejects ("edge bucket out of u8 range").

Verdict: DIVERGENT (RTL vs python soft model). Cause owner:
eco-quiltverilog (TB/RTL codepath) per cosim/corpus/MANIFEST.md.
