"""Corpus fidelity: the committed seed-treaty QUFs regenerate byte-identically.

The treaty files in cosim/corpus/ (seed 7/11/23, sets=3) are the drift
contract between this repo and quilt-verilog. Seed set is the FULL fixed
corpus — a regression pin, not adversarial selection and not exhaustive
(seed-selection rule + append-only rotation policy:
cosim/corpus/MANIFEST.md). Until now the files were only
re-verified by hand (sha256sum, 2026-08-30). This test runs the actual
treaty command -- `python3 -m deck day --seed N --sets 3 --quf ...` --
for each seed and cmps against the committed file, on the python backend
always and the esp32 backend when the bridge is built.

Reference discipline: this is the WHOLE-FILE check (`cmp`), i.e. the
treaty definition of byte-identity -- not the payload-only CLI line.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "cosim", "corpus")
SEEDS = [7, 11, 23]


def _run_day(seed: int, backend: str, out: str):
    cmd = [sys.executable, "-m", "deck", "day", "--seed", str(seed),
           "--sets", "3", "--backend", backend, "--quf", out]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, f"deck day failed ({backend}, seed {seed}):\n{r.stderr}"
    return r


@pytest.mark.parametrize("seed", SEEDS)
def test_corpus_regenerates_python(seed):
    corpus = os.path.join(CORPUS, f"seed-{seed}.quf")
    out = os.path.join(ROOT, "cosim", "run", f"corpus-check-{seed}.quf")
    _run_day(seed, "python", out)
    with open(corpus, "rb") as a, open(out, "rb") as b:
        assert a.read() == b.read(), (
            f"seed-{seed} corpus drift: regenerated QUF differs from the "
            "committed treaty file -- freeze the seed, root-cause before "
            "editing either side (cosim/corpus/MANIFEST.md escalation rule)")


@pytest.mark.parametrize("seed", SEEDS)
def test_corpus_regenerates_esp32(seed):
    if not os.path.exists(os.path.join(ROOT, "esp32", "build", "deckbridge")):
        pytest.skip("esp32 backend not built (make -C esp32 deckbridge)")
    corpus = os.path.join(CORPUS, f"seed-{seed}.quf")
    out = os.path.join(ROOT, "cosim", "run", f"corpus-check-{seed}-esp32.quf")
    _run_day(seed, "esp32", out)
    with open(corpus, "rb") as a, open(out, "rb") as b:
        assert a.read() == b.read(), (
            f"seed-{seed} corpus drift on esp32: regenerated QUF differs "
            "from the committed treaty file")
