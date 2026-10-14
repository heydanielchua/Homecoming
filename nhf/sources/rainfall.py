"""Rainfall climatology, aggregated from NEA's historical readings.

NEA publishes a year of rainfall per dataset, 2016 to 2024, through the
ordinary datastore — so `DataGovClient` pages it like the resale files. The
catch is the volume: these are raw five-minute readings from 89 stations, and
one year is 7,044,646 rows. About 704 pages.

There is no way to make the server do the arithmetic. data.gov.sg has no
`datastore_search_sql` endpoint — asking for one returns the website's HTML —
so a `SUM(...) GROUP BY station` that would have come back as 89 rows is not
available. The rows are therefore paged and folded down here as they arrive,
never held: a year of readings becomes 89 station summaries and the readings
themselves are discarded.

One year is the recommended amount, not nine. Every station is sampled the
same way, so the *relative* picture — the east is drier than the central
catchment and the west — holds from a single year, and that comparison is
all the signal honestly supports. Rainfall over a 50 km island varies by
something like 50% between the wettest and driest stations, but the
year-to-year swing at one station is wide enough to swamp the difference
between two neighbouring towns, so a block-level claim would be false
precision however many years went into it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Iterator

log = logging.getLogger(__name__)

STATION_COLUMNS = (
    "station_id", "name", "lat", "lon", "year",
    "total_mm", "readings", "wet_readings", "rain_days", "days_observed",
    "source",
)

# A reading is "wet" above this, in mm per five minutes. The gauges report
# 0 constantly, and a tipping bucket's smallest increment is 0.2 mm, so
# anything at or below that is the gauge idling rather than rain.
WET_MM = 0.2


@dataclass
class Station:
    station_id: str
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    total_mm: float = 0.0
    readings: int = 0
    wet_readings: int = 0
    days: set = field(default_factory=set)
    wet_days: set = field(default_factory=set)


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fold(records: Iterable[dict]) -> dict[str, Station]:
    """Collapse a stream of readings into one summary per station.

    Takes an iterator and keeps only the running totals, because the input is
    seven million rows and the output is 89.
    """
    stations: dict[str, Station] = {}
    skipped = 0

    for rec in records:
        sid = (rec.get("station_id") or "").strip()
        if not sid:
            skipped += 1
            continue
        st = stations.get(sid)
        if st is None:
            st = stations[sid] = Station(sid)
        if st.name is None:
            st.name = (rec.get("station_name") or "").strip() or None
        if st.lat is None:
            st.lat = _f(rec.get("location_latitude"))
            st.lon = _f(rec.get("location_longitude"))

        value = _f(rec.get("reading_value"))
        # Gauges report negatives and absurd values when out of service; a
        # five-minute total above 100 mm is not weather, it is a fault.
        if value is None or value < 0 or value > 100:
            skipped += 1
            continue

        day = (rec.get("date") or (rec.get("timestamp") or "")[:10]) or None
        st.readings += 1
        st.total_mm += value
        if day:
            st.days.add(day)
        if value >= WET_MM:
            st.wet_readings += 1
            if day:
                st.wet_days.add(day)

    if skipped:
        log.info("rainfall: %s readings skipped as missing or out of range", skipped)
    return stations


def station_rows(stations: dict[str, Station], year: int, source: str) -> Iterator[tuple]:
    for st in sorted(stations.values(), key=lambda s: s.station_id):
        yield (
            st.station_id, st.name, st.lat, st.lon, year,
            round(st.total_mm, 1), st.readings, st.wet_readings,
            len(st.wet_days), len(st.days), source,
        )
