#ifndef QUFC_H
#define QUFC_H

#include <stdint.h>
#include <stddef.h>

typedef struct {
	uint8_t src, dst, mode, slot;
	uint16_t base, wh;
	uint32_t age;
	uint8_t buckets[16];
} QufcEdge;

typedef struct {
	uint32_t cell_count;
	uint32_t edge_count;
	uint32_t route_count;
	uint32_t edge_k;
	uint32_t tick_period;
	uint32_t align;
	uint16_t *dials;          /* cell_count × 16 */
	QufcEdge *edges;          /* edge_count records */
	uint8_t *routing;         /* route_count × 2 (dst, via pairs) */
	uint32_t *ticks_phases;   /* cell_count phases (after tpw) */
	uint32_t tpw;             /* ticks: tpw before phases */
	const char *producer;    /* quf.version string; NULL = "quf.py 1.0"
	                             (the golden vector's producer) */
} QufcDoc;

/* Build canonical QUF bytes. Returns size on success, 0 on error. */
size_t qufc_build(const QufcDoc *doc, unsigned char *out, size_t cap);

/* Parse QUF bytes into doc structure. Returns 0 on success, -1 on error. */
int qufc_parse(const unsigned char *buf, size_t len, QufcDoc *out);

/* Golden vector selftest. Returns 0 if PASS, -1 if FAIL. */
int qufc_selftest(void);

#endif
