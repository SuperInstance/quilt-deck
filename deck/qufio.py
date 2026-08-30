"""deck.qufio — QUF save/load for the deck graph, against the reference
implementation (quilt-verilog tools/quf.py, imported read-only).

Two container flavors, one state:
- ARCHIVE QUF: the four v1 sections + an `app.deck` section (books, config,
  daylog tail). Unknown sections are the blessed extensibility path
  (QUF-SPEC §8 rule 3): quf.py skips them, the app reattaches them.
- BOOT QUF: unknown sections stripped — the rtl/quf_boot loader profile
  consumes dials/edges/routing/ticks; app sections are host-side
  (SYNTHESIS/QUF-SPEC §9 honesty).
"""

from __future__ import annotations

import importlib.util
import json
import os
import struct
import sys
from typing import Dict, Optional, Tuple

_QV = os.path.expanduser("~/projects/quilt-verilog")
_spec = importlib.util.spec_from_file_location("quf", os.path.join(_QV, "tools", "quf.py"))
quf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quf)

APP_SECTION = b"app.deck"


def build_archive(doc: dict, app: dict) -> bytes:
    """Build the four-section container with quf.build, then splice the
    app.deck payload as a fifth section using the same canonical layout
    algorithm (ascending 32-aligned offsets, zero padding, EOF padded)."""
    base = quf.build(doc)
    parsed = quf.read(base)
    align = parsed["header"].get("align", 32)
    payload = json.dumps(app, separators=(",", ":"), sort_keys=True).encode()

    secs = [(name, data) for name, _, data in parsed["sections"]]
    secs.append((APP_SECTION.decode(), payload))
    # re-emit exactly like quf.rebuild but with the extra section appended
    kv_bytes = bytearray()
    for key, vt, v in parsed["kv"]:
        kb = key.encode()
        kv_bytes += quf.u32(len(kb)) + kb + quf.u32(vt) + quf.pack_value(vt, v)
    table_len = 4 + sum(4 + len(n.encode()) + 20 for n, _ in secs)
    base_len = 16 + len(kv_bytes) + table_len
    entries = bytearray()
    body = bytearray()
    off = (base_len + align - 1) // align * align
    for name, data in secs:
        entries += quf.table_chunk(name, off, len(data))
        body += b"\x00" * (off - (base_len + len(body)))
        body += data
        off = (base_len + len(body) + align - 1) // align * align
    out = bytearray()
    out += quf.MAGIC
    out += struct.pack("<III", 1, 1, len(parsed["kv"]))
    out += kv_bytes
    out += quf.u32(len(secs))
    out += entries
    out += body
    if len(out) % align:
        out += b"\x00" * (align - len(out) % align)
    return bytes(out)


def split_archive(data: bytes) -> Tuple[dict, dict]:
    """Parse an archive QUF: (quf-doc-dict, app-dict)."""
    parsed = quf.read(data)
    app: Dict = {}
    doc: Dict = {"header": dict(parsed["header"])}
    for name, kind, blob in parsed["sections"]:
        if name.encode() == APP_SECTION:
            app = json.loads(blob.decode())
    if "dials" in parsed["payload"]:
        doc["dials"] = _unpack_dials(parsed["payload"]["dials"],
                                     parsed["header"]["cell_count"])
    if "edges" in parsed["payload"]:
        doc["edges"] = _unpack_edges(parsed["payload"]["edges"],
                                     parsed["header"].get("edge.k", 8))
    if "routing" in parsed["payload"]:
        doc["routing"] = [{"dst": b[0], "via": b[1]}
                          for b in (parsed["payload"]["routing"][i:i + 2]
                                    for i in range(0, len(parsed["payload"]["routing"]), 2))]
    if "ticks" in parsed["payload"]:
        blob = parsed["payload"]["ticks"]
        tpw = struct.unpack_from("<I", blob, 0)[0]
        phases = list(struct.unpack_from("<%dI" % parsed["header"]["cell_count"], blob, 4))
        doc["ticksched"] = {"tpw": tpw, "phases": phases}
    return doc, app


def _unpack_dials(blob: bytes, cell_count: int):
    rows = []
    for r in range(cell_count):
        row = list(struct.unpack_from("<16H", blob, r * 32))
        rows.append(row)
    return rows


def _unpack_edges(blob: bytes, k: int):
    edges, off = [], 0
    rec = 12 + k
    while off + rec <= len(blob):
        src, dst, mode, slot = blob[off], blob[off + 1], blob[off + 2], blob[off + 3]
        base, wh = struct.unpack_from("<HH", blob, off + 4)
        age = struct.unpack_from("<I", blob, off + 8)[0]
        buckets = list(blob[off + 12:off + 12 + k])
        edges.append({"src": src, "dst": dst, "mode": mode, "slot": slot,
                      "base": base, "wh": wh, "age": age, "buckets": buckets})
        off += rec
    return edges


def boot_image(data: bytes) -> bytes:
    """Strip unknown sections for the RTL loader profile (boot QUF)."""
    parsed = quf.read(data)
    doc = {"header": {k: v for k, v in parsed["header"].items()}}
    for name in ("dials", "edges", "routing", "ticks"):
        if name in parsed["payload"]:
            if name == "dials":
                doc["dials"] = _unpack_dials(parsed["payload"][name],
                                             parsed["header"]["cell_count"])
            elif name == "edges":
                doc["edges"] = _unpack_edges(parsed["payload"][name],
                                             parsed["header"].get("edge.k", 8))
            elif name == "routing":
                doc["routing"] = [{"dst": b[0], "via": b[1]}
                                  for b in (parsed["payload"][name][i:i + 2]
                                            for i in range(0, len(parsed["payload"][name]), 2))]
            else:
                blob = parsed["payload"][name]
                tpw = struct.unpack_from("<I", blob, 0)[0]
                phases = list(struct.unpack_from("<%dI" % parsed["header"]["cell_count"], blob, 4))
                doc["ticksched"] = {"tpw": tpw, "phases": phases}
    # header counts must match the stripped payload
    doc["header"].pop("cell_count", None)
    doc["header"].pop("edge_count", None)
    doc["header"].pop("route_count", None)
    doc["header"].pop("tick_period", None)
    return quf.build(doc)


def write_hex(data: bytes, path: str) -> None:
    """Byte-per-line hex image ($fscanf style, tb convention)."""
    with open(path, "w") as fh:
        for b in data:
            fh.write("%02x\n" % b)


def verify(data: bytes) -> bool:
    try:
        quf.read(data)
        return True
    except Exception:
        return False


def load_path(path: str) -> Tuple[bytes, dict, dict]:
    with open(path, "rb") as fh:
        data = fh.read()
    doc, app = split_archive(data)
    return data, doc, app


def save_path(path: str, doc: dict, app: dict) -> bytes:
    data = build_archive(doc, app)
    with open(path, "wb") as fh:
        fh.write(data)
    return data
