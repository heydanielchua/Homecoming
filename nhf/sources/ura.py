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
occasionally — the 2025 layer was approved 30 December 2025 — so this is
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
import time
from dataclasses import dataclass
from typing import Any, Iterator

from ..geo import (
    OUTLINE_TOLERANCE_M,
    SURFACE_TOLERANCE_M,
    geometry_summary,
    outline,
)

log = logging.getLogger(__name__)

POLL_DOWNLOAD = "https://api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download"

PARCEL_COLUMNS = (
    "objectid", "land_use", "gpr_raw", "gpr_numeric", "gpr_note",
    "area_sqm", "lat", "lon", "min_lat", "max_lat", "min_lon", "max_lon",
    "geom", "source",
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


# api-open.data.gov.sg rate-limits this endpoint far more tightly than the
# datastore does — two requests in a row is enough to be refused — and it says
# so in the body: "try again in 10 seconds". Six geospatial layers fetched back
# to back therefore loaded the first two and failed the rest, which looked like
# a network policy problem and was not. Waiting is the whole fix.
RATE_LIMIT_WAIT_SECONDS = 12.0
MAX_ATTEMPTS = 5


def download_url(dataset_id: str, session, sleep=time.sleep) -> str:
    """Ask data.gov.sg for a presigned link to the file.

    Retries a rate limit rather than failing the layer. `sleep` is injectable
    so a test can exercise the retry without waiting a minute for it.
    """
    last_status = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = session.get(POLL_DOWNLOAD.format(id=dataset_id), timeout=60)
        if resp.status_code == 429:
            last_status = 429
            if attempt < MAX_ATTEMPTS:
                wait = RATE_LIMIT_WAIT_SECONDS * attempt
                log.info(
                    "%s: rate limited by api-open.data.gov.sg, waiting %.0fs "
                    "(attempt %s of %s)",
                    dataset_id, wait, attempt, MAX_ATTEMPTS,
                )
                sleep(wait)
                continue
            break
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

    raise UraError(
        f"poll-download for {dataset_id} was rate limited {MAX_ATTEMPTS} times "
        f"(last status {last_status}). This endpoint allows roughly one request "
        "every ten seconds, so fetch the layers further apart."
    )


def parse_gpr(raw: Any) -> tuple[float | None, str | None]:
    """Split the GPR string into a ratio and a reason there isn't one."""
    text = "" if raw is None else str(raw).strip().upper()
    if not text or text == "NONE":
        return None, None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text), None
    return None, GPR_CODES.get(text, f"unrecognised GPR code {text!r}")


@dataclass
class ParcelStats:
    read: int = 0
    kept: int = 0
    no_geometry: int = 0
    # Counted but not drawable: the summary worked, the outline did not. Such
    # a parcel still answers "what could be built there"; it just has no shape
    # on the zoning layer.
    no_outline: int = 0
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
        "land use: %s features read, %s kept, %s without usable geometry, "
        "%s kept but not drawable, %s unparsed",
        stats.read, stats.kept, stats.no_geometry, stats.no_outline,
        stats.unparsed,
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
    # Roads and water are simplified finer than the rest: they are the two
    # layers a reader follows as a shape rather than reads as an area, and the
    # two that actually curve. See the tolerances in geo.py.
    use = (props.get("LU_DESC") or "").strip().upper()
    shape = outline(
        feature.get("geometry") or {},
        tolerance_m=(
            SURFACE_TOLERANCE_M
            if use in {"ROAD", "WATERBODY"}
            else OUTLINE_TOLERANCE_M
        ),
    )
    if shape is None:
        stats.no_outline += 1
    stats.kept += 1
    return (
        props.get("OBJECTID"),
        (props.get("LU_DESC") or "").strip() or None,
        (str(props.get("GPR")).strip() if props.get("GPR") is not None else None),
        gpr_numeric,
        gpr_note,
        props.get("SHAPE.AREA"),
        lat, lon, min_lat, max_lat, min_lon, max_lon,
        json.dumps(shape, separators=(",", ":")) if shape else None,
        source,
    )
