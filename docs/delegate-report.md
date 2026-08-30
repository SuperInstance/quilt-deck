TASK: Write deck/report.py — the "Captain's Report" generator for quilt-deck
(pure stdlib Python 3, no deps). It turns a day-export JSON into a markdown
report on stdout.

READ FIRST:
- /home/eileen/projects/quilt-deck/docs/DAY-EXPORT-SCHEMA.md (authoritative schema)
- /home/eileen/projects/quilt-verilog/docs/BACK-DECK-APP.md (domain tone — read the quotes)

DELIVERABLE: deck/report.py exposing:
- render_report(export: dict) -> str   (markdown)
- CLI: python3 deck/report.py EXPORT.json [-o OUT.md]

REPORT SHAPE (markdown, tight, numbers-forward):
1. `# Captain's Report — F/V EILEEN — {date}` + one-line summary:
   sets, total fish, backend, QUF sha (12 hex chars), balanced YES/NO
2. `## The day` — one bullet per set from daylog: set n, species mix
   (e.g. "612 pink · 180 chum · 14 king · 31 coho"), hook depth line
3. `## The books` — markdown table: species | landed | totes | hold | moves;
   totals row; the conservation line rendered as:
   "landed 4,185 = totes 4,185 + hold 0 + unbooked 0 — **balanced**" or the
   violation spelled out loudly
4. `## Refusals` — table (t, op, reason, detail) or "none — clean day";
   every refusal reason in the closed set gets one human sentence
   (INSUFFICIENT_CREDIT = moved more fish than the tote held, etc.)
5. `## Fabric` — cells with nonzero act or any trained buckets (name, act,
   edge readouts as bucket histograms in compact form like [412 3 0 0 0 0 0 0]),
   fire count; a closing line on tick count if derivable from export
   (sum nothing — only use what's in the export)
6. `## Night` — if daylog has {"t":"night"} entries: what the A/B verdict
   did (any dial writes listed in the entry), phrased plainly.

RULES: never invent numbers not present in the export; handle missing keys
defensively (render "—" not exceptions); format integers with thousands
separators; keep it under ~80 lines for a 5-set day. Include a
`if __name__ == "__main__"` CLI with argparse. PEP8, docstrings.

TEST: build a sample export dict in a `--selftest` mode (inline, following
the schema with 2 sets, 1 refusal) and assert render_report returns a str
containing the key section headers; exit 0/1. Do not touch files outside
deck/report.py.
