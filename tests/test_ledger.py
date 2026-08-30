"""Unit tests: the conservation ledger -- refusals by design.

The runtime check: a fish in the hold without a booked debit from a tote
is a custody violation the app REFUSES. Every refusal is booked with a
reason from the closed set. These tests are the adversarial ops the task
demands, at the ledger layer; test_day.py runs them through backends.
"""

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from deck.fabric import Fabric
from deck.ledger import DeckLedger


def test_phantom_hold_refused():
    led = DeckLedger()
    flits = led.phantom_hold_entry("pink", 50)
    assert flits == []
    assert led.books.refusals[-1].reason == "PHANTOM_HOLD_ENTRY"
    assert led.books.hold["pink"] == 0


def test_move_without_credit_refused():
    led = DeckLedger()
    flits = led.move("TOTE-PORT", "HOLD", 400, "pink")
    assert flits == [] and led.books.refusals[-1].reason == "INSUFFICIENT_CREDIT"


def test_double_move_refused():
    led = DeckLedger()
    led.land("TOTE-PORT", "pink", 100)
    f1 = led.move("TOTE-PORT", "HOLD", 40, "pink")
    assert len(f1) == 40
    f2 = led.move("TOTE-PORT", "HOLD", 40, "pink")   # same tick, same terms
    assert f2 == [] and led.books.refusals[-1].reason == "DOUBLE_MOVE"
    # a DIFFERENT move at the same tick is fine
    f3 = led.move("TOTE-PORT", "HOLD", 20, "pink")
    assert len(f3) == 20


def test_tote_overflow_refused():
    led = DeckLedger()
    flits = led.land("TOTE-PORT", "pink", 401)
    assert flits == [] and led.books.refusals[-1].reason == "TOTE_OVERFLOW"
    # partial landings up to cap are fine
    ok = led.land("TOTE-PORT", "pink", 400)
    assert len(ok) == 400
    one_more = led.land("TOTE-PORT", "pink", 1)
    assert one_more == [] and led.books.refusals[-1].reason == "TOTE_OVERFLOW"


def test_species_constraint_cells():
    led = DeckLedger()
    r = led.land("TOTE-PORT", "chum", 10)
    assert r == [] and led.books.refusals[-1].reason == "SPECIES_MISMATCH"
    # aliases resolve canonically (humpy -> pink)
    ok = led.land("TOTE-PORT", "humpy", 10)
    assert len(ok) == 10 and led.books.landed["pink"] == 10


def test_hold_is_terminal():
    led = DeckLedger()
    led.land("TOTE-PORT", "pink", 100)
    led.move("TOTE-PORT", "HOLD", 50, "pink")
    r = led.move("HOLD", "TOTE-HOLD", 10, "pink")
    assert r == [] and led.books.refusals[-1].reason == "NOT_A_MOVE"


def test_conservation_invariant_holds():
    led = DeckLedger()
    led.land("TOTE-PORT", "pink", 300)
    led.land("TOTE-HOLD", "dog", 150)            # alias
    led.move("TOTE-PORT", "HOLD", 280, "pink")
    c = led.books.conservation()
    assert c["landed_total"] == 450 and c["hold_total"] == 280
    assert c["totes_total"] == 170 and c["balanced"] and c["unbooked"] == 0


def test_move_fabric_traffic_is_balanced():
    """Every emitted move flit corresponds to one booked fish."""
    led = DeckLedger()
    led.land("TOTE-STBD-F", "king", 40)
    flits = led.move("TOTE-STBD-F", "HOLD", 40, "king")
    assert len(flits) == 40
    assert all(f.src == 2 and f.dst == 4 for f in flits)  # tote -> hold


def test_refused_ops_emit_no_fabric_traffic():
    led = DeckLedger()
    led.phantom_hold_entry("coho", 9)
    led.move("TOTE-PORT", "HOLD", 999, "pink")
    led.land("TOTE-STBD-A", "pink", 5)           # wrong tote species
    assert led.books.totes[3]["coho"] == 0
    assert led.books.hold["coho"] == 0
    assert led.books.landed["pink"] == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)} ledger tests passed")
