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
                     courts
    mrt_exit         613 station exits, named by station and exit code
    bus_stop         5,205 stop locations, by stop number
    preschool        2,290 ECDA centres
    school           337 MOE schools, from the datastore rather than a
                     geospatial layer, carrying a postal code and no
                     coordinates

Two of those arrive in shapes nothing else here uses. ECDA's pre-school
layer is KML converted to GeoJSON, so every attribute sits inside one HTML
table in a `Description` string instead of in its own field, and has to be
pulled back out. MOE's school directory is not geospatial at all: it comes
down the ordinary datastore with a postal code, and the coordinates are
filled in afterwards by the same geocoder the blocks went through.
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any, Iterator

from ..geo import geometry_summary

log = logging.getLogger(__name__)

AMENITY_COLUMNS = (
    "kind", "subtype", "name", "detail", "status", "size_hint",
    "lat", "lon", "min_lat", "max_lat", "min_lon", "max_lon", "postal", "source",
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
        lat, lon, min_lat, max_lat, min_lon, max_lon, None, source,
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
        lat, lon, min_lat, max_lat, min_lon, max_lon, None, source,
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
        lat, lon, min_lat, max_lat, min_lon, max_lon, None, source,
    )


_ATTR_ROW = re.compile(
    r"<th>\s*([^<]+?)\s*</th>\s*<td>\s*(.*?)\s*</td>", re.I | re.S
)
_TAGS = re.compile(r"<[^>]+>")


def kml_description_props(description: str | None) -> dict[str, str]:
    """Pull the attributes back out of a KML-converted `Description`.

    data.gov.sg republishes some layers from KML, and the conversion puts
    every attribute into a single HTML table in one string field:

        <th>CENTRE_NAME</th> <td>CHILDREN'S COVE PRESCHOOL</td>

    so the properties dict carries two useless keys (`Name` is "kml_1") and
    the real fields have to be read out of the markup. An HTML parser would
    be the respectable answer to arbitrary HTML; this is one table shape
    emitted by one converter, so a regex over th/td pairs is enough and
    pulls in nothing.
    """
    if not description:
        return {}
    props: dict[str, str] = {}
    for key, value in _ATTR_ROW.findall(description):
        text = unescape(_TAGS.sub("", value)).strip()
        if text:
            props[unescape(key).strip().upper()] = text
    return props


def mrt_exit_rows(feature: dict, source: str) -> tuple | None:
    """One MRT station exit.

    The PRD asks for the nearest *exit* rather than the nearest station, and
    this layer is the reason it can: 613 exits against 170-odd stations.
    """
    props = feature.get("properties") or {}
    summary = geometry_summary(feature.get("geometry") or {})
    if not summary:
        return None
    lat, lon, min_lat, max_lat, min_lon, max_lon = summary
    station = _text(props.get("STATION_NA")) or _text(props.get("STATION_NAME"))
    exit_code = _text(props.get("EXIT_CODE"))
    if not station:
        return None
    # The station is the subtype as well as part of the name, so "how far to
    # the nearest exit of *this* station" stays answerable after the join.
    name = f"{station} {exit_code}" if exit_code else station
    return (
        "mrt_exit", station, name, exit_code, None, None,
        lat, lon, min_lat, max_lat, min_lon, max_lon, None, source,
    )


def bus_stop_rows(feature: dict, source: str) -> tuple | None:
    """One bus stop.

    Stop locations are free; which services call at each one needs LTA
    DataMall and an account key, so this layer answers "how far to a bus
    stop" and deliberately does not pretend to answer "which bus".
    """
    props = feature.get("properties") or {}
    summary = geometry_summary(feature.get("geometry") or {})
    if not summary:
        return None
    lat, lon, min_lat, max_lat, min_lon, max_lon = summary
    number = _text(props.get("BUS_STOP_N")) or _text(props.get("BUS_STOP_NUM"))
    if not number:
        return None
    return (
        "bus_stop", None, number, _text(props.get("BUS_ROOF_NUM")), None, None,
        lat, lon, min_lat, max_lat, min_lon, max_lon, None, source,
    )


def preschool_rows(feature: dict, source: str) -> tuple | None:
    """One ECDA pre-school, read out of the KML description table."""
    props = feature.get("properties") or {}
    attrs = kml_description_props(props.get("Description"))
    summary = geometry_summary(feature.get("geometry") or {})
    if not summary:
        return None
    lat, lon, min_lat, max_lat, min_lon, max_lon = summary
    name = attrs.get("CENTRE_NAME") or _text(props.get("Name"))
    if not name or name.lower().startswith("kml_"):
        return None
    return (
        "preschool", None, name, attrs.get("CENTRE_CODE"), None, None,
        lat, lon, min_lat, max_lat, min_lon, max_lon, None, source,
    )


# MOE's `mainlevel_code`. Only primary matters to the report — the PRD asks
# for the nearest primary school and how many are within 1 km, because that
# is what moves a resale price — but the rest are cheap to keep and a
# secondary school next door is a fact somebody will want.
SCHOOL_LEVELS = {
    "PRIMARY", "SECONDARY", "MIXED LEVELS", "JUNIOR COLLEGE",
    "CENTRALISED INSTITUTE",
}


def school_row(record: dict, source: str) -> tuple | None:
    """One MOE school, from the datastore, with no coordinates yet.

    The directory has a postal code and an address and no geometry, so the
    row is stored with a null lat/lon and `nhf geocode --schools` fills them
    in. A school with no postal code cannot be placed at all and is dropped,
    because a school at an unknown distance is worse than one fewer school in
    the count — the count is reported with the number that failed.
    """
    name = _text(record.get("school_name"))
    postal = _text(record.get("postal_code"))
    level = (_text(record.get("mainlevel_code")) or "").upper()
    if not name or not postal:
        return None
    address = " ".join((_text(record.get("address")) or "").split()) or None
    return (
        "school", level or None, name.strip().upper(), address, None, None,
        None, None, None, None, None, None, postal, source,
    )


LAYERS = {
    "hawker_centre": hawker_rows,
    "park": park_rows,
    "park_facility": facility_rows,
    "mrt_exit": mrt_exit_rows,
    "bus_stop": bus_stop_rows,
    "preschool": preschool_rows,
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
