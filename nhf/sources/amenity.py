"""Parks, hawker centres and park facilities, from data.gov.sg GeoJSON.

Four layers were on the list; three are loaded. `Parks` and `NParks Parks and
Nature Reserves` turned out to be the same 462 places — identical names, no
difference either way — published once as points and once as boundaries, so
only the boundaries are kept. They carry an area, and for a park the distance
that matters is to its edge: Bukit Timah's centroid sits well over a
kilometre from blocks whose nearest fence is a few hundred metres away. The
bounding box is what keeps that from reading as far.

Dog runs are not here, and should not be promised. `Park Facilities` does
have a `DOG-AREA` class, but there are three of them in the whole national
layer, against a real number in the dozens. A "nearest dog park" built on
that would be wrong almost everywhere, which is worse than absent.

What the layers do give:

    hawker_centre    129 centres with a stall count and a status, six of
                     them still under construction — a hawker centre opening
                     nearby is a real change to a neighbourhood
    park             462 park and nature reserve boundaries, with areas
    park_facility    14,754 points, classed: 9,285 car parks, 475 fitness
                     areas, 387 playgrounds, 225 BBQ pits, 16 multipurpose
                     courts — the hardcourt the PRD wanted
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from ..geo import geometry_summary

log = logging.getLogger(__name__)

AMENITY_COLUMNS = (
    "kind", "subtype", "name", "detail", "status", "size_hint",
    "lat", "lon", "min_lat", "max_lat", "min_lon", "max_lon", "source",
)

# Facility classes worth carrying to a block page. The layer is 63% car parks,
# which say nothing about living somewhere, and noticeboards and mapboards say
# less. Dropping them keeps the per-block join honest about what it found.
USEFUL_FACILITIES = {
    "PLAYGROUND", "FITNESS AREA", "BBQ PIT", "MULTIPURPOSE COURT",
    "THERAPEUTIC GARDEN", "ALLOTMENT GARDEN", "DOG-AREA", "CAMPSITE",
    "AMPHITHEATRE", "LOOKOUT POINT", "F&B", "TOILET", "DRINKING FOUNTAIN",
    "SHELTER", "JETTY", "FOOT RELAX", "BICYCLE PARKING", "BICYCLE RENTAL SHOP",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def hawker_rows(feature: dict, source: str) -> tuple | None:
    """One hawker centre.

    `STATUS` is kept because six of the 129 are under construction, and a
    centre that has not opened is a different fact from one that has.
    """
    props = feature.get("properties") or {}
    summary = geometry_summary(feature.get("geometry") or {})
    if not summary:
        return None
    lat, lon, min_lat, max_lat, min_lon, max_lon = summary
    address = " ".join(filter(None, (
        _text(props.get("ADDRESSBLOCKHOUSENUMBER")),
        _text(props.get("ADDRESSSTREETNAME")),
    ))) or None
    return (
        "hawker_centre", None,
        _text(props.get("NAME")) or _text(props.get("ADDRESSBUILDINGNAME")),
        address,
        _text(props.get("STATUS")),
        _number(props.get("NUMBER_OF_COOKED_FOOD_STALLS")),
        lat, lon, min_lat, max_lat, min_lon, max_lon, source,
    )


def park_rows(feature: dict, source: str) -> tuple | None:
    """One park or nature reserve, as a boundary."""
    props = feature.get("properties") or {}
    summary = geometry_summary(feature.get("geometry") or {})
    if not summary:
        return None
    lat, lon, min_lat, max_lat, min_lon, max_lon = summary
    return (
        "park", _text(props.get("N_RESERVE")), _text(props.get("NAME")),
        _text(props.get("L_CODE")), None,
        _number(props.get("SHAPE_1.AREA")),
        lat, lon, min_lat, max_lat, min_lon, max_lon, source,
    )


def facility_rows(feature: dict, source: str) -> tuple | None:
    """One park facility, if it is a kind worth reporting."""
    props = feature.get("properties") or {}
    klass = (_text(props.get("CLASS")) or "").upper()
    if klass not in USEFUL_FACILITIES:
        return None
    summary = geometry_summary(feature.get("geometry") or {})
    if not summary:
        return None
    lat, lon, min_lat, max_lat, min_lon, max_lon = summary
    return (
        "park_facility", klass, _text(props.get("NAME")),
        _text(props.get("ADDITIONAL_INFO")), None, None,
        lat, lon, min_lat, max_lat, min_lon, max_lon, source,
    )


LAYERS = {
    "hawker_centre": hawker_rows,
    "park": park_rows,
    "park_facility": facility_rows,
}


def load_layer(path: str, kind: str, source: str) -> Iterator[tuple]:
    """Read one GeoJSON layer into amenity rows.

    These layers are small — the largest is 6 MB — so unlike the 174 MB land
    use layer they are parsed whole rather than streamed.
    """
    mapper = LAYERS.get(kind)
    if not mapper:
        raise ValueError(f"no mapper for layer kind {kind!r}")

    with open(path, encoding="utf-8", errors="replace") as fh:
        features = (json.load(fh).get("features") or [])

    kept = 0
    for feature in features:
        row = mapper(feature, source)
        if row:
            kept += 1
            yield row
    log.info("%s: %s features read, %s kept", kind, len(features), kept)
