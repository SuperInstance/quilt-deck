"""QUF round-trip tests: archive + boot profiles, warm load."""
import os, sys, hashlib
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
from deck.graph import new_fabric, quf_doc, load_into_fabric
from deck.daylog import gen_day, replay
from deck.ledger import DeckLedger
from deck import qufio

def test_archive_roundtrip():
    fab = new_fabric()
    led = replay(fab, DeckLedger(), gen_day(seed=1, sets=2))
    doc = quf_doc(fab)
    data = qufio.build_archive(doc, led.books.snapshot())
    assert qufio.verify(data)
    doc2, app2 = qufio.split_archive(data)
    assert doc2["dials"] == doc["dials"]
    assert len(doc2["edges"]) == len(doc["edges"])
    assert app2["conservation"]["balanced"]
    # quf.py reference accepts it (unknown section skipped)
    parsed = qufio.quf.read(data)
    assert "app.deck" in parsed["payload"]

def test_boot_profile_strips_app():
    fab = new_fabric()
    led = replay(fab, DeckLedger(), gen_day(seed=1, sets=2))
    data = qufio.build_archive(quf_doc(fab), led.books.snapshot())
    boot = qufio.boot_image(data)
    assert qufio.verify(boot)
    names = [n for (n, k, o, s) in qufio.quf.read(boot)["table"]]
    assert "app.deck" not in names and "ticks" in names

def test_warm_full_state_roundtrip():
    fab = new_fabric()
    replay(fab, DeckLedger(), gen_day(seed=1, sets=2))
    doc = quf_doc(fab)
    data = qufio.quf.build(doc)
    doc2, _ = qufio.split_archive(data)
    fab2 = new_fabric.__wrapped__() if hasattr(new_fabric, "__wrapped__") else None
    from deck.fabric import Fabric
    from deck.graph import NCELL
    f2 = Fabric(ncell=NCELL)
    load_into_fabric(f2, doc2, restore_walk=True)
    snap1, snap2 = fab.snapshot(), f2.snapshot()
    for c1, c2 in zip(snap1["cells"], snap2["cells"]):
        assert c1["dials"] == c2["dials"]
        for e1, e2 in zip(c1["edges"], c2["edges"]):
            assert e1["buckets"] == e2["buckets"]
            assert e1["base"] == e2["base"] and e1["wh"] == e2["wh"]

def test_day_quf_deterministic():
    hashes = set()
    for _ in range(2):
        fab = new_fabric()
        replay(fab, DeckLedger(), gen_day(seed=5, sets=2))
        hashes.add(hashlib.sha256(qufio.quf.build(quf_doc(fab))).hexdigest())
    assert len(hashes) == 1

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("PASS", fn.__name__)
    print(f"{len(fns)} quf tests passed")
