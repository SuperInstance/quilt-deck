/* deckbridge.c -- the quilt-deck ESP32 backend (host loopback).
 *
 * The deck graph on the REAL quilt-esp32 backend: the vendored
 * quilt-vm-c (firmware/vm/quilt_vm.c, unmodified, read-only import)
 * hosts every deck cell as a thing; the five canon opcodes drive it:
 *   BIND   commissioning: one thing per cell, canon state JSON
 *   LINK   the fabric learning graph (etab edges, typed links)
 *   EFFECT every day-log flit is a queued reversible change (forward
 *          applies the flit to the numeric core; inverse restores the
 *          snapshot -- refusal rollback is the VM's own discipline)
 *   VIEW   cell state projection (books readback)
 *   TICK   drains pending effects in order; fabric decay ticks are
 *          effects too (qvm time advances without touching decay)
 *
 * The numeric core is a C port of deck/fabric.py (bit-exact same math:
 * saturating integers, ladder buckets, echo gate, hyperbola). The
 * cosim/referee proves all three engines agree; this port must match
 * the python model field-for-field, which test_day.py asserts via the
 * dumped state + byte-identical QUF (qufc.c writes the container).
 *
 * Wire protocol (stdin lines, stdout events; no JSON parsing needed):
 *   F <op> <src> <dst> <a0> <a1> <a2> <dat>   apply one flit
 *   T <n>                                    run n fabric ticks
 *   V <cid> <sel> <a1>                        view (response: R ...)
 *   D                                         dump state (DC/DA/DE lines)
 *   S <path>                                  save QUF via qufc
 *   B <path>                                  load QUF via qufc (warm)
 *   E                                         end
 * Events out:  E <op> <src> <dst> <a0> <a1> <a2> <dat>   (host egress)
 *              ! <cell> <dat>                             (fire)
 *              # <text>                                   (info)
 * Build: make -C esp32
 */

#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "quilt_vm.h"
#include "qm_opcodes.h"
#include "qufc.h"

#define NCELL 15
#define EXTID 0xF
#define K 8
#define EDGES_N 4
#define NDIALS 16

/* ---- dial addresses (q_dialfile.v) ---- */
#define D_KA 4
#define D_THRESH 5
#define D_REFR 6
#define D_P0E 8
#define D_MODE 9
#define D_HL 10
#define D_KLE 11
#define D_FLOOR 12
#define D_FTRACE 13
#define D_RQ 14

static const unsigned DIAL_DEFAULTS[NDIALS] = {
    0x0800, 0x0080, 6, 12, 5, 0x6000, 4, 0x2CCD,
    20, 0, 64, 2, 0x0000, 0x0000, 0x0008, 0x0008};

typedef struct {
    unsigned char peer;
    unsigned char valid;
    unsigned short base;
    unsigned char c[K];       /* ladder buckets */
    unsigned short hl_cnt;
    unsigned short wh;
    unsigned int age;
} Edge;

typedef struct {
    const char *name;
    int bound, cell_id;
    short act;                /* signed 16 stored as short */
    unsigned short refr;
    unsigned short ftrace;
    unsigned short dials[NDIALS];
    Edge edges[EDGES_N];
} Cell;

typedef struct {
    Cell cells[NCELL];
    qvm_t *vm;
    FILE *out;
} Deck;

static Deck deck;

static int s16v(unsigned short u) { return (int)(short)u; }

/* ---------------- the VM surface (canon state mirrors) --------------- */

static void cell_canon(const Cell *c, char *buf, size_t n) {
    /* canon JSON snapshot of the observable cell state */
    snprintf(buf, n,
        "{\"id\":%d,\"act\":%d,\"refr\":%u,\"ftrace\":%u,"
        "\"thresh\":%d,\"hl\":%u,\"edges\":[",
        c->cell_id, c->act, c->refr, c->ftrace,
        s16v(c->dials[D_THRESH]), c->dials[D_HL]);
    size_t used = strlen(buf);
    for (int e = 0; e < EDGES_N; e++) {
        used += snprintf(buf + used, n - used, "%s[%d,%u,%u]",
                         e ? "," : "", c->edges[e].valid ? c->edges[e].peer : -1,
                         c->edges[e].base,
                         c->edges[e].valid ? (unsigned)c->edges[e].c[0] : 0u);
    }
    snprintf(buf + used, n - used, "]}");
}

static void publish_cell(int cid) {
    char key[32], canon[512];
    snprintf(key, sizeof key, "deck/%s", deck.cells[cid].name);
    cell_canon(&deck.cells[cid], canon, sizeof canon);
    qm_bind_str(deck.vm, key, canon);
}

/* ---------------- engine math (port of deck/fabric.py) --------------- */

static unsigned readout_ladder(const Edge *e) {
    unsigned acc = 0;
    for (int i = 0; i < K; i++)
        acc += (unsigned)e->c[i] << (K - i);
    return acc > 0xFFFF ? 0xFFFF : acc;
}

static unsigned readout_hyp(const Edge *e) {
    return e->wh > 255 ? 0xFFFFu : (unsigned)(e->wh << 8);
}

static unsigned edge_weight(const Cell *c, const Edge *e) {
    unsigned eng = (c->dials[D_MODE] & 1) ? readout_hyp(e) : readout_ladder(e);
    unsigned w = (unsigned)e->base + eng;
    return w > 0xFFFF ? 0xFFFF : w;
}

static int gate_live(const Cell *c) {
    return c->dials[D_FLOOR] == 0 || c->ftrace >= c->dials[D_FLOOR];
}

static int gate_gclass(const Cell *c) {
    if (c->dials[D_FLOOR] == 0 || c->ftrace == 0) return 0;
    int msb = 0;
    for (int j = 0; j < 16; j++)
        if ((c->ftrace >> j) & 1) msb = j;
    return 15 - msb;
}

static void leak_ftrace(Cell *c) {
    unsigned f = c->ftrace;
    if (!f) return;
    unsigned kle = c->dials[D_KLE] & 0xF;
    unsigned fleak = kle ? f - (f >> kle) : f;
    int snap = fleak <= c->dials[D_FLOOR] || fleak <= 1 || fleak >= f;
    c->ftrace = snap ? 0 : (unsigned short)fleak;
}

static void engine_train(Cell *c, Edge *e, int gclass) {
    if (!(c->dials[D_MODE] & 1)) {
        int g = gclass > K - 1 ? K - 1 : gclass;
        if (e->c[g] >= 255) { /* sticky ovf tracked nowhere observable */ }
        else e->c[g]++;
    } else {
        if (e->wh != 0xFFFF) e->wh++;
    }
}

static void engine_tick(Cell *c, Edge *e) {
    if (!(c->dials[D_MODE] & 1)) {
        if ((unsigned)c->hl_cnt + 1 >= c->dials[D_HL]) {
            for (int i = K - 1; i > 0; i--) e->c[i] = e->c[i - 1];
            e->c[0] = 0;
            c->hl_cnt = 0;
        } else c->hl_cnt++;
    } else {
        unsigned p0 = 1u << (c->dials[D_P0E] & 0x1F);
        int msb = 0;
        for (int j = 0; j < 16; j++)
            if ((e->wh >> j) & 1) msb = j;
        unsigned ivr = p0 >> (2 * msb);
        unsigned ival = ivr ? ivr : 1;
        if (e->wh != 0 && e->age + 1 >= ival) { e->wh--; e->age = 0; }
        else e->age++;
    }
}

/* ---------------- flit delivery --------------------------------------- */

typedef struct { int op, src, dst, a0, a1, a2, dat; } Flit;

static void host_egress(const Flit *f) {
    fprintf(deck.out, "E %d %d %d %d %d %d %d\n",
            f->op, f->src, f->dst, f->a0, f->a1, f->a2, f->dat);
}

static void respond(int dst, int src, int nak, int a2, int dat) {
    Flit r = { nak ? 6 : 5, src, dst, 0, 0, a2, dat };
    host_egress(&r);
}

static void deliver(const Flit *f) {
    if (f->dst == EXTID) { host_egress(f); return; }
    if (f->dst >= NCELL) return;
    Cell *c = &deck.cells[f->dst];

    if (!c->bound) {
        int nak = f->op != 0;
        if (f->op == 0) { c->cell_id = f->a0 & 0xF; c->bound = 1; }
        respond(f->src, c->cell_id, nak, f->a2, 0);
        publish_cell(f->dst);
        return;
    }
    if (f->op == 5 || f->op == 6 || f->op == 4) return;

    if (f->op == 0) {                      /* BIND: dial write */
        int addr = f->a0 & 0xF;
        if (addr != D_FTRACE) c->dials[addr] = (unsigned short)f->a1;
        respond(f->src, c->cell_id, 0, f->a2, 0);
        publish_cell(f->dst);
        return;
    }
    if (f->op == 1) {                      /* LINK: edge slot */
        int slot = f->a0 & (EDGES_N - 1);
        c->edges[slot].peer = (unsigned char)(f->src & 0xF);
        c->edges[slot].valid = 1;
        c->edges[slot].base = (unsigned short)f->a1;
        respond(f->src, c->cell_id, 0, f->a2, 0);
        publish_cell(f->dst);
        return;
    }
    if (f->op == 2) {                      /* EFFECT */
        Edge *hit = NULL;
        for (int i = 0; i < EDGES_N; i++)
            if (c->edges[i].valid && c->edges[i].peer == (f->src & 0xF)) {
                hit = &c->edges[i]; break;
            }
        if (!hit) return;                  /* unknown source: dropped */
        if (gate_live(c)) engine_train(c, hit, gate_gclass(c));
        unsigned w = edge_weight(c, hit);
        int prod = (int)w * (int)(short)(unsigned short)f->dat;
        int act = (int)c->act + (prod >> 15);
        if (act > 32767) act = 32767;
        if (act < -32768) act = -32768;
        c->act = (short)act;
        publish_cell(f->dst);
        return;
    }
    if (f->op == 3) {                      /* VIEW */
        int sel = f->a0 & 0x3;
        if (sel == 0) respond(f->src, c->cell_id, 0, f->a2,
                              (int)(unsigned short)c->act);
        else if (sel == 1) {
            unsigned wacc = 0;
            for (int i = 0; i < EDGES_N; i++)
                if (c->edges[i].valid)
                    wacc += edge_weight(c, &c->edges[i]);
            respond(f->src, c->cell_id, 0, f->a2,
                    wacc > 0xFFFF ? 0xFFFF : (int)wacc);
        } else if (sel == 2) {
            int idx = f->a1 & 0xF;
            int v = idx == D_FTRACE ? c->ftrace : c->dials[idx];
            respond(f->src, c->cell_id, 0, f->a2, v);
        } else respond(f->src, c->cell_id, 1, f->a2, 0);
        return;
    }
    respond(f->src, c->cell_id, 1, f->a2, 0);
}

static void fabric_tick(void) {
    for (int i = 0; i < NCELL; i++) {
        Cell *c = &deck.cells[i];
        if (!c->bound) continue;
        for (int e = 0; e < EDGES_N; e++)
            if (c->edges[e].valid) engine_tick(c, &c->edges[e]);
        int fired = (int)c->act >= s16v(c->dials[D_THRESH]) && c->refr == 0;
        if (fired) c->ftrace = 0xFFFF;
        else leak_ftrace(c);
        int act = (int)c->act;
        int ka = c->dials[D_KA] & 0xF;
        int leaked = act - (act >> ka);
        if (leaked > 32767) leaked = 32767;
        if (leaked < -32768) leaked = -32768;
        c->act = (short)leaked;
        if (fired) {
            int afire = (int)(unsigned short)act;  /* pre-leak value */
            fprintf(deck.out, "! %d %d\n", i, afire);
            for (int e = 0; e < EDGES_N; e++)
                if (c->edges[e].valid) {
                    Flit fx = { 2, c->cell_id, c->edges[e].peer, 0, 0, 0, afire };
                    deliver(&fx);
                }
            c->act = 0;
            c->refr = c->dials[D_REFR];
        } else if (c->refr) c->refr--;
        publish_cell(i);
    }
}

/* ---------------- VM effect plumbing ---------------------------------- */

typedef struct { Flit f; Cell snap[NCELL]; int is_tick; int n; } OpArg;

/* forward: apply; inverse: restore snapshot (refusal/rollback discipline) */
static void op_forward(qvm_thing_t *t, void *arg) {
    (void)t;
    OpArg *a = (OpArg *)arg;
    if (a->is_tick) fabric_tick();
    else deliver(&a->f);
}

static void op_inverse(qvm_thing_t *t, void *arg) {
    (void)t;
    OpArg *a = (OpArg *)arg;
    memcpy(deck.cells, a->snap, sizeof(a->snap));
}

static void vm_apply(const Flit *f, int is_tick, int n) {
    static OpArg arg;
    arg.f = *f; arg.is_tick = is_tick; arg.n = n;
    memcpy(arg.snap, deck.cells, sizeof(arg.snap));
    char target[32];
    snprintf(target, sizeof target, "deck/%s",
             is_tick ? "tick" : deck.cells[f->dst < NCELL ? f->dst : 0].name);
    qm_effect(deck.vm, target, op_forward, op_inverse, &arg);
    qm_tick(deck.vm, 1.0);   /* drain in order; vm time advances */
}

/* ---------------- state dump / QUF save ------------------------------- */

static const char *CELL_NAMES[NCELL] = {
    "TOTE-PORT", "TOTE-HOLD", "TOTE-STBD-F", "TOTE-STBD-A", "HOLD",
    "ALIAS", "XID-MATCH", "HOOK-COUNT", "SOUNDER", "LEDGER-SCALE",
    "BESTSHOT", "AUDIT-CAPTAIN", "NIGHT-CRON", "AB-PROMOTE", "CAM-UW"};

static void dump_state(void) {
    for (int i = 0; i < NCELL; i++) {
        Cell *c = &deck.cells[i];
        fprintf(deck.out, "DC %d", i);
        for (int d = 0; d < NDIALS; d++) fprintf(deck.out, " %u", c->dials[d]);
        fprintf(deck.out, "\nDA %d %d %u %u\n", i, (int)c->act, c->refr, c->ftrace);
        for (int e = 0; e < EDGES_N; e++) {
            Edge *g = &c->edges[e];
            fprintf(deck.out, "DE %d %d %u %u %u %u %u %u",
                    i, e, g->valid, g->peer, g->base, c->hl_cnt, g->wh, g->age);
            for (int b = 0; b < K; b++) fprintf(deck.out, " %u", g->c[b]);
            fprintf(deck.out, "\n");
        }
    }
}

static void quf_save(const char *path) {
    QufcDoc doc;
    memset(&doc, 0, sizeof doc);
    doc.cell_count = NCELL;
    doc.tpw = 15;
    static unsigned short dials[NCELL][16];
    for (int i = 0; i < NCELL; i++)
        for (int d = 0; d < NDIALS; d++)
            dials[i][d] = deck.cells[i].dials[d];
    doc.dials = dials[0];
    QufcEdge edges[64];
    int ne = 0;
    for (int i = 0; i < NCELL; i++)
        for (int e = 0; e < EDGES_N; e++) {
            Edge *g = &deck.cells[i].edges[e];
            if (!g->valid) continue;
            edges[ne].src = (unsigned char)i;
            edges[ne].dst = g->peer;
            edges[ne].mode = (unsigned char)(deck.cells[i].dials[D_MODE] & 1);
            edges[ne].slot = (unsigned char)e;
            edges[ne].base = g->base;
            edges[ne].wh = g->wh;
            edges[ne].age = g->age;
            for (int b = 0; b < K; b++) edges[ne].buckets[b] = g->c[b];
            edges[ne].n_buckets = K;
            ne++;
        }
    doc.edges = edges;
    doc.edge_count = ne;
    unsigned char routing[NCELL][2];
    for (int i = 0; i < NCELL; i++) { routing[i][0] = i; routing[i][1] = i; }
    doc.routing = routing[0];
    doc.route_count = NCELL;
    doc.phases = NULL;
    static unsigned char buf[65536];
    size_t n = qufc_build(&doc, buf, sizeof buf);
    if (n == 0) { fprintf(deck.out, "# quf_build FAILED\n"); return; }
    FILE *fh = fopen(path, "wb");
    if (!fh) { fprintf(deck.out, "# open %s FAILED\n", path); return; }
    fwrite(buf, 1, n, fh);
    fclose(fh);
    fprintf(deck.out, "# saved %zu bytes to %s\n", n, path);
}

static void quf_load(const char *path) {
    static unsigned char buf[65536];
    FILE *fh = fopen(path, "rb");
    if (!fh) { fprintf(deck.out, "# open %s FAILED\n", path); return; }
    size_t n = fread(buf, 1, sizeof buf, fh);
    fclose(fh);
    QufcDoc doc;
    memset(&doc, 0, sizeof doc);
    if (qufc_parse(buf, n, &doc) != 0) {
        fprintf(deck.out, "# quf_parse FAILED\n"); return;
    }
    for (int i = 0; i < NCELL && i < (int)doc.cell_count; i++) {
        Cell *c = &deck.cells[i];
        for (int d = 0; d < NDIALS; d++)
            c->dials[d] = doc.dials[i * 16 + d];
        c->bound = 1;
        c->cell_id = i;
        publish_cell(i);
    }
    for (unsigned e = 0; e < doc.edge_count && e < 64; e++) {
        const QufcEdge *g = &doc.edges[e];
        if (g->src >= NCELL || g->slot >= EDGES_N) continue;
        Cell *c = &deck.cells[g->src];
        c->edges[g->slot].peer = g->dst;
        c->edges[g->slot].valid = 1;
        c->edges[g->slot].base = g->base;
        /* walk state restored (the python full-state path; the RTL
           loader profile re-earns it -- both end states byte-equal,
           which the conformance suite proves) */
        c->edges[g->slot].wh = g->wh;
        c->edges[g->slot].age = g->age;
        for (int b = 0; b < K && b < g->n_buckets; b++)
            c->edges[g->slot].c[b] = g->buckets[b];
    }
    fprintf(deck.out, "# loaded %zu bytes from %s\n", n, path);
}

/* ---------------- main loop ------------------------------------------- */

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    deck.vm = qvm_new();
    deck.out = stdout;
    if (!deck.vm) { fprintf(stderr, "qvm_new failed\n"); return 1; }

    static const char *names_init[NCELL] = {
        "TOTE-PORT", "TOTE-HOLD", "TOTE-STBD-F", "TOTE-STBD-A", "HOLD",
        "ALIAS", "XID-MATCH", "HOOK-COUNT", "SOUNDER", "LEDGER-SCALE",
        "BESTSHOT", "AUDIT-CAPTAIN", "NIGHT-CRON", "AB-PROMOTE", "CAM-UW"};
    for (int i = 0; i < NCELL; i++) {
        Cell *c = &deck.cells[i];
        memset(c, 0, sizeof *c);
        c->name = names_init[i];
        for (int d = 0; d < NDIALS; d++) c->dials[d] = DIAL_DEFAULTS[d];
        publish_cell(i);                    /* BIND: things exist day-one */
    }
    /* LINK: the learning graph as typed VM links (documentation surface;
       the numeric etab is commissioned by the day-log LINK flits) */
    qm_link(deck.vm, "deck/TOTE-PORT", "deck/ALIAS", "label-bus");
    qm_link(deck.vm, "deck/TOTE-PORT", "deck/XID-MATCH", "label-bus");
    qm_link(deck.vm, "deck/TOTE-HOLD", "deck/ALIAS", "label-bus");
    qm_link(deck.vm, "deck/TOTE-HOLD", "deck/XID-MATCH", "label-bus");
    qm_link(deck.vm, "deck/HOOK-COUNT", "deck/SOUNDER", "depth-pairs");
    qm_link(deck.vm, "deck/AUDIT-CAPTAIN", "deck/NIGHT-CRON", "quarantine");

    fprintf(deck.out, "# deckbridge up: %d cells on quilt-vm-c\n", NCELL);
    fflush(deck.out);

    char line[256];
    while (fgets(line, sizeof line, stdin)) {
        if (line[0] == 'F') {
            Flit f;
            if (sscanf(line + 1, "%d %d %d %d %d %d %d",
                       &f.op, &f.src, &f.dst, &f.a0, &f.a1, &f.a2, &f.dat) == 7)
                vm_apply(&f, 0, 1);
        } else if (line[0] == 'T') {
            int n = 1;
            if (sscanf(line + 1, "%d", &n) == 1 && n > 0)
                for (int i = 0; i < n; i++) vm_apply(NULL, 1, 1);
        } else if (line[0] == 'V') {
            int cid, sel, a1;
            if (sscanf(line + 1, "%d %d %d", &cid, &sel, &a1) == 3) {
                Flit v = { 3, EXTID, cid, sel, a1, 0xBEEF, 0 };
                vm_apply(&v, 0, 1);
            }
        } else if (line[0] == 'D') {
            dump_state();
        } else if (line[0] == 'S') {
            char path[192];
            if (sscanf(line + 1, "%191s", path) == 1) quf_save(path);
        } else if (line[0] == 'B') {
            char path[192];
            if (sscanf(line + 1, "%191s", path) == 1) quf_load(path);
        } else if (line[0] == 'E') {
            break;
        }
        fflush(deck.out);
    }
    qvm_free(deck.vm);
    return 0;
}
