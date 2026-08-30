#include "qufc.h"
#include <stdio.h>
#include <string.h>

int main(void) {
	fprintf(stderr, "Creating doc...\n");
	QufcDoc doc;
	memset(&doc, 0, sizeof(doc));

	fprintf(stderr, "Setting doc fields...\n");
	doc.cell_count = 2;
	doc.edge_count = 3;
	doc.route_count = 3;
	doc.edge_k = 8;
	doc.tick_period = 64;
	doc.align = 32;

	fprintf(stderr, "Creating dials...\n");
	uint16_t dials_data[32] = {0};
	dials_data[0] = 0x0800;
	doc.dials = dials_data;

	fprintf(stderr, "Creating edges...\n");
	QufcEdge edges_data[3] = {0};
	edges_data[0].src = 0;
	edges_data[0].dst = 1;
	doc.edges = edges_data;

	fprintf(stderr, "Creating routing...\n");
	uint8_t routing_data[6] = {0};
	doc.routing = routing_data;

	fprintf(stderr, "Creating ticks...\n");
	uint32_t ticks_phases[2] = {0, 3};
	doc.ticks_phases = ticks_phases;
	doc.tpw = 6;

	fprintf(stderr, "Calling build...\n");
	unsigned char built[576];
	size_t built_len = qufc_build(&doc, built, sizeof(built));

	fprintf(stderr, "Built %zu bytes\n", built_len);
	return 0;
}
