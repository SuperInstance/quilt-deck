TASK: Write a QUF container writer/reader in C99 — byte-identical to the
reference Python implementation — inside /home/eileen/projects/quilt-deck/esp32/.

READ FIRST (reference, do not modify):
- /home/eileen/projects/quilt-verilog/tools/quf.py   (the reference impl; your C must match build(), read(), rebuild() byte-for-byte for the canonical writer)
- /home/eileen/projects/quilt-verilog/docs/QUF-SPEC.md (format spec incl. the golden vector)

DELIVERABLES in /home/eileen/projects/quilt-deck/esp32/:
1. qufc.h — public API (no deps beyond C99 stdlib):
   - typedef struct { … } QufcEdge;  // src,dst,mode,slot u8; base,wh u16; age u32; buckets[16] u8; n_buckets
   - typedef struct { … } QufcDoc;   // header KVs you need: cell_count, edge_count, route_count, edge_k, tick_period, tpw, phases[]; dials[cell_count][16] u16; edges[]; routing[] (dst,via u8)
   - size_t qufc_build(const QufcDoc *doc, unsigned char *out, size_t cap); // returns 0 on error, size on success; canonical writer: KV order quf.version?, cell_count, edge_count, route_count, edge.k, tick_period, quant strings "Q1.15"/"Q1.15"/"u8", align=32 — EXACTLY quf.py CANON_KV emission for the same doc (quf.version string "qufc 1.0")
   - int qufc_parse(const unsigned char *buf, size_t len, QufcDoc *out);   // 0 ok; fills doc; tolerates unknown KV/sections (skip rule); rejects unknown value types
   - int qufc_selftest(void); // returns 0 iff the built golden vector matches byte-for-byte
2. qufc.c — implementation.
3. qufc_test.c — main() that runs the selftest, prints PASS/FAIL + sha256-free byte summary, exit 0/1.
4. Makefile target `qufc_test` (gcc -O2 -std=c99 -Wall -Wextra) — build artifacts into build/ (do NOT write into quilt-verilog).

GOLDEN VECTOR (from QUF-SPEC §11 / tools/quf.py selftest): 2 cells, 3 edges,
3 routes, tpw=6, edge.k=8, 576 bytes total. The exact JSON source is
/home/eileen/projects/quilt-verilog/tb/quf_tb.json — read it and make
qufc_selftest build the SAME document and compare against the byte string
embedded from `python3 /home/eileen/projects/quilt-verilog/tools/quf.py create
/home/eileen/projects/quilt-verilog/tb/quf_tb.json /tmp/gold.quf && cat /tmp/gold.quf`
(embed the 576 bytes as a const array in qufc.c, comment with the sha256
5b2a236ba5e38bca9ad96783c4252a12f36517f98a9164a249f0db115f221392).

MATCH RULES (get these exactly right — byte identity is the whole point):
- little-endian everywhere; header magic 'Q','U','F',0x00; version=1; endian=1; kv_count=u32
- KV encoding: u32 name_len, name, u32 value_type (GGUF numbering: string=8, u32=4), value bytes (string = u32 len + bytes)
- section table: u32 section_count; per section: u32 name_len, name, u32 kind(=0), u64 offset, u64 size; offsets absolute, ascending, 32-aligned; zero padding between table and first section, between sections, and at EOF (whole file padded to align)
- section order: dials, edges, routing, ticks
- dials payload: cell_count rows x 16 u16 LE
- edges: per record: u8 src,dst,mode,slot; u16 base,wh; u32 age; K u8 buckets (K=edge.k)
- routing: u8 dst, u8 via per record
- ticks: u32 tpw, then cell_count u32 phases
- build computes offsets the same way quf.py does (first section offset = align-up(16+len(kv)+4+table_entries); each next = align-up(prev_end))

VERIFY YOUR WORK: `python3 /home/eileen/projects/quilt-verilog/tools/quf.py verify build/gold_c.quf` must pass,
and `cmp build/gold_c.quf /tmp/gold.quf` must be identical. Also round-trip:
parse your own build output, rebuild, byte-compare. Do not modify anything
outside /home/eileen/projects/quilt-deck/esp32/ (build/ artifacts ok).
