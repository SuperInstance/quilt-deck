# quilt-deck — the back-deck pipeline as a Quilt application

The F/V EILEEN back deck (docs/BACK-DECK-APP.md in quilt-verilog) as a real
application on the quilt backend: deck positions are cells, fish moves are
effects in balanced transactions, conservation is a runtime check, the whole
deck state travels in one QUF.

Three backends, one semantics:
- `python`  — soft fabric engine, bit-exact model of the quilt-verilog cell
- `esp32`   — the deck graph on quilt-esp32's vendored quilt-vm-c (host loopback)
- `fpga`    — iverilog cosim against rtl/q_serfabric_top.v (the serialized
              fabric front-end; golden vectors from the differential TB)

See docs/ARCHITECTURE.md. Not affiliated with a deployment; fleet-static-host
deployment is explicitly out of scope here.
