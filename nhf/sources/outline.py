"""HDB building outlines, joined to the blocks we already know.

HDB publishes the footprint of every existing public housing building — 13,436
of them — as a polygon with a block number and a postal code. It does not
publish a height. The storey count lives in HDB Property Information, which
this project already loads into `block`, so the two together are the only way
to get a building's shape and its height from open data.

The join is on the postal code, which is unique in both: one footprint, one
block, no ambiguity. Block number plus street would be the obvious key and is
the worse one, because the footprint layer carries a street *code* rather than
a street name.

What this is for is the surroundings view: a flat's own building and the ones
around it, at their real outlines and real heights. What it is not is a
measurement — a storey is taken as three metres, which is right to a metre or
so and is not a survey. There is no open lidar for Singapore.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass

from ..geo import outline

log = logging.getLogger(__name__)

# The footprint layer's own field names.
POSTAL_FIELD = "POSTAL_COD"
BLOCK_FIELD = "BLK_NO"


@dataclass
class OutlineStats:
    read: int = 0
    kept: int = 0
    no_postal: int = 0
    no_outline: int = 0
    unparsed: int = 0


def stream_outlines(path: str) -> Iterator[tuple[str, str | None, str]]:
    """Yield (postal code, block number, simplified GeoJSON) per building.

    Read a feature at a time by brace depth, the same way the Master Plan is
    read: the file is 55 MB and `json.load` on it costs several times that in
    Python objects for something only passed through once.
    """
    stats = OutlineStats()
    buf: list[str] = []
    depth = 0
    started = False

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not started:
                if '"features"' in line:
                    started = True
                continue
            opens, closes = line.count("{"), line.count("}")
            if depth == 0 and opens:
                buf = [line[line.index("{"):]]
                depth = opens - closes
            elif depth:
                buf.append(line)
                depth += opens - closes
            if started and depth <= 0 and buf:
                frag = "".join(buf)
                buf = []
                depth = 0
                row = _row(frag, stats)
                if row:
                    yield row

    log.info(
        "building outlines: %s read, %s kept, %s without a postal code, "
        "%s without a usable outline, %s unparsed",
        stats.read, stats.kept, stats.no_postal, stats.no_outline,
        stats.unparsed,
    )


def _row(frag: str, stats: OutlineStats) -> tuple[str, str | None, str] | None:
    try:
        feature = json.loads(frag[frag.index("{"): frag.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        stats.unparsed += 1
        return None

    stats.read += 1
    props = feature.get("properties") or {}
    postal = str(props.get(POSTAL_FIELD) or "").strip()
    if not postal:
        stats.no_postal += 1
        return None

    shape = outline(feature.get("geometry") or {})
    if shape is None:
        stats.no_outline += 1
        return None

    stats.kept += 1
    block = str(props.get(BLOCK_FIELD) or "").strip() or None
    return postal, block, json.dumps(shape, separators=(",", ":"))
