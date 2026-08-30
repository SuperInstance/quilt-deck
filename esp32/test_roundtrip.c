#include "qufc.h"
#include <stdio.h>
#include <string.h>

int main(void) {
	QufcDoc doc;
	memset(&doc, 0, sizeof(doc));
	doc.cell_count = 2;
	doc.edge_count = 3;
	doc.route_count = 3;
	doc.edge_k = 8;
	doc.tick_period = 64;
	doc.align = 32;

	uint16_t dials_data[32] = {
		0x0800, 0x0080, 0x0006, 0x000c, 0x0005, 0x5000, 0x0004, 0x2ccd,
		0x0014, 0x0000, 0x0030, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
		0x0800, 0x0080, 0x0006, 0x000c, 0x0005, 0x6000, 0x0004, 0x2ccd,
		0x0014, 0x0001, 0x0040, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
	};
	doc.dials = dials_data;

	QufcEdge edges_data[3] = {
		{.src=0, .dst=1, .mode=0, .slot=0, .base=0x1234, .wh=0, .age=0},
		{.src=0, .dst=2, .mode=1, .slot=1, .base=0x0040, .wh=7, .age=1000},
		{.src=1, .dst=0, .mode=0, .slot=0, .base=0x0200, .wh=3, .age=5},
	};
	for (int i = 0; i < 3; i++)
		for (int k = 0; k < 8; k++) edges_data[i].buckets[k] = 0;
	doc.edges = edges_data;

	uint8_t routing_data[6] = {0x01, 0x01, 0x02, 0x02, 0x0f, 0x0f};
	doc.routing = routing_data;

	uint32_t ticks_phases[2] = {0, 3};
	doc.ticks_phases = ticks_phases;
	doc.tpw = 6;

	fprintf(stderr, "Building...\n");
	unsigned char built[576];
	size_t built_len = qufc_build(&doc, built, sizeof(built));
	fprintf(stderr, "Built %zu bytes\n", built_len);

	fprintf(stderr, "Parsing...\n");
	QufcDoc parsed;
	qufc_parse(built, built_len, &parsed);
	fprintf(stderr, "Parsed successfully\n");

	fprintf(stderr, "Rebuilding...\n");
	unsigned char rebuilt[576];
	size_t rebuilt_len = qufc_build(&parsed, rebuilt, sizeof(rebuilt));
	fprintf(stderr, "Rebuilt %zu bytes\n", rebuilt_len);

	fprintf(stderr, "Comparing...\n");
	if (memcmp(built, rebuilt, 576) == 0) {
		printf("PASS: round-trip byte-exact\n");
	} else {
		printf("FAIL: mismatch\n");
	}
	return 0;
}
