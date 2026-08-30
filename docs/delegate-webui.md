TASK: Build the static operator web console for quilt-deck in
/home/eileen/projects/quilt-deck/deck/webui/ — small, offline, no build step.

READ FIRST:
- /home/eileen/projects/quilt-deck/docs/DAY-EXPORT-SCHEMA.md  (the exact JSON you render — authoritative)
- /home/eileen/projects/quilt-deck/docs/BACK-DECK-CONTEXT.md  (domain flavor, if present; else skim schema only)

DELIVERABLES (deck/webui/):
1. index.html — one page, dark wheelhouse theme (deep navy, warm amber accents,
   monospace numerals). Sections:
   - Header: vessel name F/V EILEEN, day date, backend badge (python/esp32/fpga), QUF sha256 (truncated, click-to-copy), conformance match indicator (green MATCH / red DIVERGE / grey n-a)
   - CONSERVATION banner: big live check — landed_total vs totes_total vs hold_total, balanced true/false; the words "NO UNBOOKED FISH" when true
   - BOOKS table: per species rows (landed / in totes / in hold / moves count), totals column; pure HTML table
   - DECK STATE: the four totes as "tank" cards (fill bar n/cap, species color: pink=#e75480 chum=#7ec8a3 king=#c9a227 coho=#c0c0d0), plus HOLD card with total
   - HOOK LINE: each hook_set as "30 hooks × 1.5 fathoms = N fm" line items
   - MOVES log: table (t, from, to, n, species chip)
   - REFUSALS log: table with reason badges (red); empty state "clean day — no refusals"
   - FIRES timeline: sparkline-ish list (cell, tick, dat) — tiny inline SVG bars ok
2. app.js — fetch('day.json') on load; render everything; no frameworks, no
   external CDNs (this runs on an offline deck box); vanilla ES2020 only.
   Also support file:// double-click open (if fetch fails, show a file picker
   that reads a local day JSON — FileReader).
3. style.css — clean, compact, print-friendly (@media print: hide refusals? no — keep all).
4. A sample day.json following the schema exactly (invent plausible numbers,
   5 sets, ~4200 fish total, 2-3 refusals incl. one PHANTOM_HOLD_ENTRY and one
   TOTE_OVERFLOW, balanced=true, conformance.match=true).

QUALITY BAR: a deckhand with wet gloves should get the answer in 3 seconds.
No emoji spam; icons via unicode glyphs only (⚓ ▤ ✓ ⚠). Test by opening
index.html — the sample day.json must render fully with zero console errors
(logic testable via `node -e` shims is a plus).
Do not touch anything outside deck/webui/.
