"""URA Master Plan land use, from data.gov.sg.

A different shape of source from everything else here. The resale files come
down the CKAN datastore as paged rows; this is a 174 MB GeoJSON served as a
file, through a third host:

    api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download
        -> a presigned S3 URL, valid for an hour
        -> blobs.data.gov.sg/{id}.geojson

The datastore API the rest of the app uses returns nothing at all for a
geospatial dataset, so none of `DataGovClient` applies.

It is also not monthly data. The Master Plan is approved once and amended
occasionally — the 2019 layer was approved in November 2019 — so this is
fetched when a revision is published, not on the monthly cycle.

What the layer gives, from its own fields:

    LU_DESC     the zoning, one of 33 values — RESIDENTIAL, COMMERCIAL,
                RESERVE SITE, WHITE, UTILITY, ROAD and so on
    GPR         gross plot ratio, as a string, and mostly not a number:
                LND (landed housing) on 52% of parcels, EVA (evaluated
                case by case) on 11%, NA on 7%. Only about 30% carry a
                ratio.
    SHAPE.AREA  plot size in square metres

There is no height field, and `GPR_B_MN` — URA's own band minimum — is set
on 52 parcels out of 113,212, so it cannot be relied on. Height has to be
inferred from the ratio where there is one, which is why `gpr_numeric` is
stored separately from the raw string: a parcel reading LND is a confident
answer (landed housing is low-rise and will not block a view), while EVA is
an admission that nobody has decided yet. Those two must not collapse into
the same null.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterator

log = logging.getLogger(__name__)

POLL_DOWNLOAD = "https://api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download"

PARCEL_COLUMNS = (
    "objectid", "land_use", "gpr_raw", "gpr_numeric", "gpr_note",
    "area_sqm", "lat", "lon", "min_lat", "max_lat", "min_lon", "max_lon",
    "source",
)

# What URA writes where a ratio does not apply. Kept apart from a missing
# value: "landed housing" tells you the height, "to be evaluated" does not.
GPR_CODES = {
    "LND": "landed housing, so low-rise",
    "EVA": "evaluated at planning stage, not fixed",
    "NA": "not applicable to this use",
    "SDP": "subject to detailed planning",
}


class UraError(RuntimeError):
    """The layer could not be fetched or made sense of."""


def download_url(dataset_id: str, session) -> str:
    """Ask data.gov.sg for a presigned link to the file."""
    resp = session.get(POLL_DOWNLOAD.format(id=dataset_id), timeout=60)
    if not resp.ok:
        raise UraError(
            f"poll-download for {dataset_id} returned {resp.status_code}. "
            "api-open.data.gov.sg is a separate host from the datastore API; "
            "if this is a connection failure the network policy may not allow it."
        )
    url = (resp.json().get("data") or {}).get("url")
    if not url:
        raise UraError(f"no download url in the response for {dataset_id}")
    return url


def parse_gpr(raw: Any) -> tuple[float | None, str | None]:
    """Split the GPR string into a ratio and a reason there isn't one."""
    text = "" if raw is None else str(raw).strip().upper()
    if not text or text == "NONE":
        return None, None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text), None
    return None, GPR_CODES.get(text, f"unrecognised GPR code {text!r}")


def _ring_centroid(ring: list) -> tuple[float, float, float]:
    """Area-weighted centroid of one ring, and its signed area.

    The plain mean of the vertices is pulled towards whichever edge was
    drawn in most detail, and these rings are very unevenly sampled — some
    edges carry thirty points and the opposite side two. The shoelace
    centroid does not care how finely an edge was traced.
    """
    cx = cy = a2 = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        cross = x1 * y2 - x2 * y1
        a2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if a2 == 0:                       # degenerate: fall back to the mean
        n = len(ring) or 1
        return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n, 0.0)
    return (cx / (3 * a2), cy / (3 * a2), abs(a2) / 2)


def geometry_summary(geometry: dict) -> tuple[float, float, float, float, float, float] | None:
    """Centroid and bounding box for a Polygon or MultiPolygon.

    Only a summary is kept. Holding 113,212 polygons would mean shipping a
    geometry library and a spatial index for a question — "what sits within
    a few hundred metres of this block" — that a centroid and a box answer
    well enough, and the box is what makes a large parcel still count when
    its centre is far away.
    """
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates")
    if not coords:
        return None
    if kind == "Polygon":
        rings = [coords[0]]
    elif kind == "MultiPolygon":
        rings = [poly[0] for poly in coords if poly]
    else:
        return None

    total, wx, wy = 0.0, 0.0, 0.0
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    for ring in rings:
        if len(ring) < 3:
            continue
        x, y, area = _ring_centroid(ring)
        weight = area or 1e-12
        total += weight
        wx += x * weight
        wy += y * weight
        for lon, lat in ring:
            min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
    if not total or min_lon == float("inf"):
        return None
    return (wy / total, wx / total, min_lat, max_lat, min_lon, max_lon)


@dataclass
class ParcelStats:
    read: int = 0
    kept: int = 0
    no_geometry: int = 0
    unparsed: int = 0


def stream_parcels(path: str, source: str) -> Iterator[tuple]:
    """Yield one row per parcel, reading the file a feature at a time.

    The layer is 174 MB and `json.load` on it costs well over a gigabyte of
    objects, so features are cut out of the stream by brace depth and parsed
    one by one. The whole file is read; only a summary of each parcel is
    kept.
    """
    stats = ParcelStats()
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
                if depth <= 0:
                    depth = 0
                    row = _row(buf, source, stats)
                    if row:
                        yield row
                    buf = []
                continue
            if depth:
                buf.append(line)
                depth += opens - closes
                if depth <= 0:
                    depth = 0
                    row = _row(buf, source, stats)
                    if row:
                        yield row
                    buf = []

    log.info(
        "land use: %s features read, %s kept, %s without usable geometry, %s unparsed",
        stats.read, stats.kept, stats.no_geometry, stats.unparsed,
    )


def _row(buf: list[str], source: str, stats: ParcelStats) -> tuple | None:
    frag = "".join(buf)
    try:
        feature = json.loads(frag[frag.index("{"): frag.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        stats.unparsed += 1
        return None

    stats.read += 1
    props = feature.get("properties") or {}
    summary = geometry_summary(feature.get("geometry") or {})
    if not summary:
        stats.no_geometry += 1
        return None

    lat, lon, min_lat, max_lat, min_lon, max_lon = summary
    gpr_numeric, gpr_note = parse_gpr(props.get("GPR"))
    stats.kept += 1
    return (
        props.get("OBJECTID"),
        (props.get("LU_DESC") or "").strip() or None,
        (str(props.get("GPR")).strip() if props.get("GPR") is not None else None),
        gpr_numeric,
        gpr_note,
        props.get("SHAPE.AREA"),
        lat, lon, min_lat, max_lat, min_lon, max_lon,
        source,
    )
