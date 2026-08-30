#include "qufc.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

#define MAGIC_0 'Q'
#define MAGIC_1 'U'
#define MAGIC_2 'F'
#define MAGIC_3 0x00
#define VERSION 1
#define ENDIAN_LITTLE 1
#define DEFAULT_EDGE_K 8
#define DEFAULT_ALIGN 32
#define NDIALS 16

#define GGUF_T_STR 8
#define GGUF_T_U32 4

static inline void put_le32(uint8_t *p, uint32_t v) {
	p[0] = (v >> 0) & 0xff;
	p[1] = (v >> 8) & 0xff;
	p[2] = (v >> 16) & 0xff;
	p[3] = (v >> 24) & 0xff;
}

static inline void put_le16(uint8_t *p, uint16_t v) {
	p[0] = (v >> 0) & 0xff;
	p[1] = (v >> 8) & 0xff;
}

static inline void put_le64(uint8_t *p, uint64_t v) {
	p[0] = (v >> 0) & 0xff;
	p[1] = (v >> 8) & 0xff;
	p[2] = (v >> 16) & 0xff;
	p[3] = (v >> 24) & 0xff;
	p[4] = (v >> 32) & 0xff;
	p[5] = (v >> 40) & 0xff;
	p[6] = (v >> 48) & 0xff;
	p[7] = (v >> 56) & 0xff;
}

static inline uint32_t get_le32(const uint8_t *p) {
	return p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24);
}

static inline uint16_t get_le16(const uint8_t *p) {
	return p[0] | (p[1] << 8);
}

static inline uint64_t get_le64(const uint8_t *p) {
	return p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24) |
	       ((uint64_t)p[4] << 32) | ((uint64_t)p[5] << 40) |
	       ((uint64_t)p[6] << 48) | ((uint64_t)p[7] << 56);
}

static size_t align_up(size_t val, size_t align) {
	return ((val + align - 1) / align) * align;
}

static size_t pack_str(uint8_t *out, const char *s) {
	uint32_t len = (uint32_t)strlen(s);
	put_le32(out, len);
	if (len > 0) memcpy(out + 4, s, len);
	return 4 + len;
}

static void pack_u32(uint8_t *out, uint32_t v) {
	put_le32(out, v);
}

static size_t pack_dials(uint8_t *out, const QufcDoc *doc) {
	size_t sz = doc->cell_count * NDIALS * 2;
	if (doc->dials) {
		for (uint32_t i = 0; i < doc->cell_count * NDIALS; i++) {
			put_le16(out + i*2, doc->dials[i]);
		}
	}
	return sz;
}

static size_t pack_edges(uint8_t *out, const QufcDoc *doc) {
	size_t sz = doc->edge_count * (12 + doc->edge_k);
	for (uint32_t i = 0; i < doc->edge_count; i++) {
		const QufcEdge *e = &doc->edges[i];
		size_t off = i * (12 + doc->edge_k);
		out[off + 0] = e->src;
		out[off + 1] = e->dst;
		out[off + 2] = e->mode;
		out[off + 3] = e->slot;
		put_le16(out + off + 4, e->base);
		put_le16(out + off + 6, e->wh);
		put_le32(out + off + 8, e->age);
		for (uint32_t k = 0; k < doc->edge_k; k++)
			out[off + 12 + k] = e->buckets[k];
	}
	return sz;
}

static size_t pack_routing(uint8_t *out, const QufcDoc *doc) {
	size_t sz = doc->route_count * 2;
	if (doc->routing) memcpy(out, doc->routing, sz);
	return sz;
}

static size_t pack_ticks(uint8_t *out, const QufcDoc *doc) {
	put_le32(out, doc->tpw);
	for (uint32_t i = 0; i < doc->cell_count; i++)
		put_le32(out + 4 + i*4, doc->ticks_phases[i]);
	return 4 + doc->cell_count * 4;
}

size_t qufc_build(const QufcDoc *doc, unsigned char *out, size_t cap) {
	if (!doc || !out || cap < 100) return 0;
	uint32_t edge_k = doc->edge_k ? doc->edge_k : DEFAULT_EDGE_K;
	uint32_t align = doc->align ? doc->align : DEFAULT_ALIGN;
	if (align < 8 || (align & (align - 1)) != 0) return 0;

	uint8_t kv_buf[2048];
	size_t kv_len = 0;
	const char *kvs[] = {"quf.version", "cell_count", "edge_count", "route_count",
		"edge.k", "tick_period", "quant.dials", "quant.edges", "quant.routing", "align"};
	const int kv_types[] = {GGUF_T_STR, GGUF_T_U32, GGUF_T_U32, GGUF_T_U32,
		GGUF_T_U32, GGUF_T_U32, GGUF_T_STR, GGUF_T_STR, GGUF_T_STR, GGUF_T_U32};
	const uint32_t kv_u32[] = {0, doc->cell_count, doc->edge_count,
		doc->route_count, edge_k, doc->tick_period, 0, 0, 0, align};
	const char *producer = doc->producer ? doc->producer : "quf.py 1.0";
	const char *kv_strs[] = {NULL, "", "", "", "", "", "Q1.15", "Q1.15", "u8", ""};
	kv_strs[0] = producer;

	for (int i = 0; i < 10; i++) {
		const char *name = kvs[i];
		uint32_t name_len = (uint32_t)strlen(name);
		put_le32(kv_buf + kv_len, name_len);
		kv_len += 4;
		memcpy(kv_buf + kv_len, name, name_len);
		kv_len += name_len;
		put_le32(kv_buf + kv_len, kv_types[i]);
		kv_len += 4;
		if (kv_types[i] == GGUF_T_STR)
			kv_len += pack_str(kv_buf + kv_len, kv_strs[i]);
		else if (kv_types[i] == GGUF_T_U32) {
			pack_u32(kv_buf + kv_len, kv_u32[i]);
			kv_len += 4;
		}
	}

	struct { const char *name; uint8_t *data; size_t size; } secs[4];
	int nsec = 0;
	uint8_t dials_buf[8192], edges_buf[8192], routing_buf[512], ticks_buf[1028];

	if (doc->dials) {
		secs[nsec].name = "dials";
		secs[nsec].data = dials_buf;
		secs[nsec].size = pack_dials(dials_buf, doc);
		nsec++;
	}
	if (doc->edge_count > 0) {
		secs[nsec].name = "edges";
		secs[nsec].data = edges_buf;
		secs[nsec].size = pack_edges(edges_buf, doc);
		nsec++;
	}
	if (doc->route_count > 0) {
		secs[nsec].name = "routing";
		secs[nsec].data = routing_buf;
		secs[nsec].size = pack_routing(routing_buf, doc);
		nsec++;
	}
	if (doc->ticks_phases) {
		secs[nsec].name = "ticks";
		secs[nsec].data = ticks_buf;
		secs[nsec].size = pack_ticks(ticks_buf, doc);
		nsec++;
	}

	uint32_t table_len = 4;
	for (int i = 0; i < nsec; i++) {
		uint32_t name_len = (uint32_t)strlen(secs[i].name);
		table_len += 4 + name_len + 4 + 8 + 8;
	}

	size_t base = 16 + kv_len + table_len;
	size_t first_off = align_up(base, align);
	size_t out_len = 0;

	out[out_len++] = MAGIC_0;
	out[out_len++] = MAGIC_1;
	out[out_len++] = MAGIC_2;
	out[out_len++] = MAGIC_3;
	put_le32(out + out_len, VERSION);
	out_len += 4;
	put_le32(out + out_len, ENDIAN_LITTLE);
	out_len += 4;
	put_le32(out + out_len, 10);
	out_len += 4;

	memcpy(out + out_len, kv_buf, kv_len);
	out_len += kv_len;
	put_le32(out + out_len, nsec);
	out_len += 4;

	uint8_t table_buf[512];
	size_t table_pos = 0;
	size_t sec_off = first_off;
	for (int i = 0; i < nsec; i++) {
		uint32_t name_len = (uint32_t)strlen(secs[i].name);
		put_le32(table_buf + table_pos, name_len);
		table_pos += 4;
		memcpy(table_buf + table_pos, secs[i].name, name_len);
		table_pos += name_len;
		put_le32(table_buf + table_pos, 0);
		table_pos += 4;
		put_le64(table_buf + table_pos, sec_off);
		table_pos += 8;
		put_le64(table_buf + table_pos, secs[i].size);
		table_pos += 8;
		sec_off = align_up(sec_off + secs[i].size, align);
	}
	memcpy(out + out_len, table_buf, table_pos);
	out_len += table_pos;

	while (out_len < first_off) out[out_len++] = 0;

	for (int i = 0; i < nsec; i++) {
		memcpy(out + out_len, secs[i].data, secs[i].size);
		out_len += secs[i].size;
		size_t next_off = align_up(out_len, align);
		while (out_len < next_off) out[out_len++] = 0;
	}

	size_t final_len = align_up(out_len, align);
	while (out_len < final_len) out[out_len++] = 0;

	if (out_len > cap) return 0;
	return out_len;
}

int qufc_parse(const unsigned char *buf, size_t len, QufcDoc *out) {
	if (!buf || !out || len < 16) return -1;
	size_t pos = 0;
	if (buf[pos++] != MAGIC_0 || buf[pos++] != MAGIC_1 ||
	    buf[pos++] != MAGIC_2 || buf[pos++] != MAGIC_3) return -1;
	uint32_t version = get_le32(buf + pos);
	pos += 4;
	if (version != VERSION) return -1;
	uint32_t endian = get_le32(buf + pos);
	pos += 4;
	if (endian != ENDIAN_LITTLE) return -1;
	uint32_t kv_count = get_le32(buf + pos);
	pos += 4;

	memset(out, 0, sizeof(*out));
	out->align = DEFAULT_ALIGN;
	out->edge_k = DEFAULT_EDGE_K;

	for (uint32_t i = 0; i < kv_count; i++) {
		if (pos + 4 > len) return -1;
		uint32_t name_len = get_le32(buf + pos);
		pos += 4;
		if (pos + name_len + 4 > len) return -1;
		char name[256];
		if (name_len >= sizeof(name)) return -1;
		memcpy(name, buf + pos, name_len);
		name[name_len] = 0;
		pos += name_len;
		uint32_t vtype = get_le32(buf + pos);
		pos += 4;

		if (vtype == GGUF_T_U32) {
			if (pos + 4 > len) return -1;
			uint32_t v = get_le32(buf + pos);
			pos += 4;
			if (strcmp(name, "cell_count") == 0) out->cell_count = v;
			else if (strcmp(name, "edge_count") == 0) out->edge_count = v;
			else if (strcmp(name, "route_count") == 0) out->route_count = v;
			else if (strcmp(name, "edge.k") == 0) out->edge_k = v;
			else if (strcmp(name, "tick_period") == 0) out->tick_period = v;
			else if (strcmp(name, "align") == 0) out->align = v;
		} else if (vtype == GGUF_T_STR) {
			if (pos + 4 > len) return -1;
			uint32_t slen = get_le32(buf + pos);
			pos += 4;
			if (pos + slen > len) return -1;
			pos += slen;
		} else return -1;
	}

	if (pos + 4 > len) return -1;
	uint32_t nsec = get_le32(buf + pos);
	pos += 4;

	struct { uint64_t off, size; char name[256]; } table[4];
	int ntable = 0;

	for (uint32_t i = 0; i < nsec && ntable < 4; i++) {
		if (pos + 4 > len) return -1;
		uint32_t name_len = get_le32(buf + pos);
		pos += 4;
		if (pos + name_len + 20 > len) return -1;
		if (name_len > 255) return -1;
		uint32_t kind = get_le32(buf + pos + name_len);
		if (kind != 0) {
			pos += name_len + 20;
			continue;
		}
		memcpy(table[ntable].name, buf + pos, name_len);
		table[ntable].name[name_len] = 0;
		pos += name_len + 4;
		table[ntable].off = get_le64(buf + pos);
		pos += 8;
		table[ntable].size = get_le64(buf + pos);
		pos += 8;
		ntable++;
	}

	for (int i = 0; i < ntable; i++) {
		if (table[i].off + table[i].size > len) return -1;
		if (strcmp(table[i].name, "dials") == 0) {
			if (table[i].size != out->cell_count * NDIALS * 2) return -1;
			out->dials = (uint16_t *)malloc(table[i].size);
			if (!out->dials) return -1;
			for (uint32_t j = 0; j < out->cell_count * NDIALS; j++)
				out->dials[j] = get_le16(buf + table[i].off + j*2);
		} else if (strcmp(table[i].name, "edges") == 0) {
			uint32_t expected_sz = out->edge_count * (12 + out->edge_k);
			if (table[i].size != expected_sz) return -1;
			out->edges = (QufcEdge *)malloc(table[i].size);
			if (!out->edges) return -1;
			for (uint32_t j = 0; j < out->edge_count; j++) {
				size_t off = table[i].off + j * (12 + out->edge_k);
				QufcEdge *e = &out->edges[j];
				e->src = buf[off + 0];
				e->dst = buf[off + 1];
				e->mode = buf[off + 2];
				e->slot = buf[off + 3];
				e->base = get_le16(buf + off + 4);
				e->wh = get_le16(buf + off + 6);
				e->age = get_le32(buf + off + 8);
				for (uint32_t k = 0; k < out->edge_k; k++)
					e->buckets[k] = buf[off + 12 + k];
			}
		} else if (strcmp(table[i].name, "routing") == 0) {
			if (table[i].size != out->route_count * 2) return -1;
			out->routing = (uint8_t *)malloc(table[i].size);
			if (!out->routing) return -1;
			memcpy(out->routing, buf + table[i].off, table[i].size);
		} else if (strcmp(table[i].name, "ticks") == 0) {
			if (table[i].size != 4 + out->cell_count * 4) return -1;
			out->tpw = get_le32(buf + table[i].off);
			out->ticks_phases = (uint32_t *)malloc(out->cell_count * 4);
			if (!out->ticks_phases) return -1;
			for (uint32_t j = 0; j < out->cell_count; j++)
				out->ticks_phases[j] = get_le32(buf + table[i].off + 4 + j*4);
		}
	}
	return 0;
}

static const unsigned char GOLDEN_BYTES[576] = {
	0x51, 0x55, 0x46, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x0a, 0x00, 0x00, 0x00,
	0x0b, 0x00, 0x00, 0x00, 0x71, 0x75, 0x66, 0x2e, 0x76, 0x65, 0x72, 0x73, 0x69, 0x6f, 0x6e, 0x08,
	0x00, 0x00, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x71, 0x75, 0x66, 0x2e, 0x70, 0x79, 0x20, 0x31, 0x2e,
	0x30, 0x0a, 0x00, 0x00, 0x00, 0x63, 0x65, 0x6c, 0x6c, 0x5f, 0x63, 0x6f, 0x75, 0x6e, 0x74, 0x04,
	0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x65, 0x64, 0x67, 0x65, 0x5f,
	0x63, 0x6f, 0x75, 0x6e, 0x74, 0x04, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x0b, 0x00, 0x00,
	0x00, 0x72, 0x6f, 0x75, 0x74, 0x65, 0x5f, 0x63, 0x6f, 0x75, 0x6e, 0x74, 0x04, 0x00, 0x00, 0x00,
	0x03, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x65, 0x64, 0x67, 0x65, 0x2e, 0x6b, 0x04, 0x00,
	0x00, 0x00, 0x08, 0x00, 0x00, 0x00, 0x0b, 0x00, 0x00, 0x00, 0x74, 0x69, 0x63, 0x6b, 0x5f, 0x70,
	0x65, 0x72, 0x69, 0x6f, 0x64, 0x04, 0x00, 0x00, 0x00, 0x40, 0x00, 0x00, 0x00, 0x0b, 0x00, 0x00,
	0x00, 0x71, 0x75, 0x61, 0x6e, 0x74, 0x2e, 0x64, 0x69, 0x61, 0x6c, 0x73, 0x08, 0x00, 0x00, 0x00,
	0x05, 0x00, 0x00, 0x00, 0x51, 0x31, 0x2e, 0x31, 0x35, 0x0b, 0x00, 0x00, 0x00, 0x71, 0x75, 0x61,
	0x6e, 0x74, 0x2e, 0x65, 0x64, 0x67, 0x65, 0x73, 0x08, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00,
	0x51, 0x31, 0x2e, 0x31, 0x35, 0x0d, 0x00, 0x00, 0x00, 0x71, 0x75, 0x61, 0x6e, 0x74, 0x2e, 0x72,
	0x6f, 0x75, 0x74, 0x69, 0x6e, 0x67, 0x08, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x75, 0x38,
	0x05, 0x00, 0x00, 0x00, 0x61, 0x6c, 0x69, 0x67, 0x6e, 0x04, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00,
	0x00, 0x04, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x64, 0x69, 0x61, 0x6c, 0x73, 0x00, 0x00,
	0x00, 0x00, 0x80, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x65, 0x64, 0x67, 0x65, 0x73, 0x00, 0x00, 0x00, 0x00, 0xc0,
	0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3c, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07,
	0x00, 0x00, 0x00, 0x72, 0x6f, 0x75, 0x74, 0x69, 0x6e, 0x67, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02,
	0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x05, 0x00,
	0x00, 0x00, 0x74, 0x69, 0x63, 0x6b, 0x73, 0x00, 0x00, 0x00, 0x00, 0x20, 0x02, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x0c, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x08, 0x80, 0x00, 0x06, 0x00, 0x0c, 0x00, 0x05, 0x00, 0x00, 0x50, 0x04, 0x00, 0xcd, 0x2c,
	0x14, 0x00, 0x00, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x08, 0x80, 0x00, 0x06, 0x00, 0x0c, 0x00, 0x05, 0x00, 0x00, 0x60, 0x04, 0x00, 0xcd, 0x2c,
	0x14, 0x00, 0x01, 0x00, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x01, 0x00, 0x00, 0x34, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x01, 0x01, 0x40, 0x00, 0x07, 0x00, 0xe8, 0x03, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x02, 0x03, 0x00,
	0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x01, 0x01, 0x02, 0x02, 0x0f, 0x0f, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
};

int qufc_selftest(void) {
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

	unsigned char built[576];
	size_t built_len = qufc_build(&doc, built, sizeof(built));

	if (built_len != 576) {
		fprintf(stderr, "FAIL: built size %zu != 576\n", built_len);
		return -1;
	}

	if (memcmp(built, GOLDEN_BYTES, 576) != 0) {
		fprintf(stderr, "FAIL: built bytes do not match golden vector\n");
		for (int i = 0; i < 576; i++) {
			if (built[i] != GOLDEN_BYTES[i]) {
				fprintf(stderr, "  byte %d: got 0x%02x, want 0x%02x\n",
					i, built[i], GOLDEN_BYTES[i]);
				break;
			}
		}
		return -1;
	}

	return 0;
}
