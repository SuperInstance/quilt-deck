#include "qufc.h"
#include <stdio.h>
#include <string.h>

int main(void) {
	fprintf(stderr, "Creating doc...\n");
	QufcDoc doc;
	memset(&doc, 0, sizeof(doc));
	doc.cell_count = 2;
	doc.edge_count = 3;
	doc.route_count = 3;
	doc.edge_k = 8;
	doc.tick_period = 64;
	doc.align = 32;

	uint16_t dials_data[32] = {0};
	dials_data[0] = 0x0800;
	doc.dials = dials_data;

	QufcEdge edges_data[3] = {0};
	edges_data[0].src = 0;
	edges_data[0].dst = 1;
	doc.edges = edges_data;

	uint8_t routing_data[6] = {0};
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
	int res = qufc_parse(built, built_len, &parsed);
	fprintf(stderr, "Parse result: %d\n", res);
	return 0;
}
