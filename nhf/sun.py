"""Where the afternoon sun comes from, and whether it reaches a flat.

Singapore sits 1.35 degrees north of the equator, which changes the question
from the one a temperate-climate tool would ask. At noon the sun passes
within a couple of degrees of straight overhead all year, so a north or south
window gets very little direct sun through the middle of the day and the heat
problem is not "which way does it face" in general — it is the low western
sun from about four o'clock, which comes in almost horizontally, under any
overhang, for the two hours of the day when the flat is already hot.

That is why "west sun" is the complaint here and "south sun" is not, and it
is also why the useful answer combines two things the PRD keeps together: a
tall block to the west ruins the view and saves the afternoons.

Where the sun actually is comes out of the standard NOAA solar position
algorithm rather than a rule of thumb, because the interesting part is the
spread over the year. Sunset swings between about 247 degrees in December and
293 in June — south of west in our winter, north of west in our summer — so a
flat facing due west bakes all year while one facing north-west bakes from
March to September and gets off lightly in December.

The two functions the site depends on are `afternoon_track`, which is
computed once for the island and shipped as data, and `exposure`, which the
browser mirrors because it needs the floor and the facing that are typed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import asin, atan2, cos, degrees, radians, sin, tan

# Singapore Standard Time is UTC+8 all year, with no daylight saving. It is
# also a long way east of its meridian: solar noon is around 13:10, which is
# why the afternoon window below runs later than it would elsewhere.
UTC_OFFSET_HOURS = 8.0

# The island, for solar purposes. Blocks span 1.24 to 1.47 degrees north,
# which moves the sun's altitude by about a quarter of a degree — far less
# than the half degree the sun's own disc covers — so one track serves every
# block and the report says so rather than implying per-block precision.
ISLAND_LAT = 1.35
ISLAND_LON = 103.82

# The hours that matter. Direct sun before this is high enough to be stopped
# by the floor slab above the window; after 19:30 the sun is down every day of
# the year.
AFTERNOON_START = 15.5
AFTERNOON_END = 19.5

# Below this the sun is behind the horizon haze and the trees, and is not what
# anyone means by west sun. Above about 30 degrees it is coming in steeply
# enough that an overhang helps.
MIN_ALTITUDE = 2.0
LOW_SUN_ALTITUDE = 30.0

# A window takes direct sun while the sun is within this of the way it faces.
# Ninety degrees is the geometric truth for a flat pane — beyond that the sun
# is behind the wall — and the cosine weighting below is what stops a glancing
# 89-degree angle counting the same as a square-on one.
FACE_HALF_ANGLE = 90.0

COMPASS = {
    "N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0,
    "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0,
}

# HDB floor-to-floor height. Blocks vary a little and the published data does
# not carry it, so this is the standard figure and the report labels anything
# derived from it as worked out rather than fact.
STOREY_HEIGHT_M = 3.0

# Where your eyes are inside your own storey. It matters for exactly one
# comparison, and that comparison comes up constantly: a block the same
# height as yours. Its roof is a storey above its own top floor, so measuring
# you at floor level would make every equal-height neighbour "taller than
# you" and report a blocked view across half the estate.
EYE_HEIGHT_M = 1.5


def _julian_day(when: date, hour: float) -> float:
    """Julian day for a local date and decimal hour."""
    year, month = when.year, when.month
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    jd = (
        int(365.25 * (year + 4716))
        + int(30.6001 * (month + 1))
        + when.day + b - 1524.5
    )
    return jd + (hour - UTC_OFFSET_HOURS) / 24.0


@dataclass(frozen=True)
class SunPosition:
    azimuth: float      # degrees clockwise from true north
    altitude: float     # degrees above the horizon

    @property
    def up(self) -> bool:
        return self.altitude > MIN_ALTITUDE


def solar_position(lat: float, lon: float, when: date, hour: float) -> SunPosition:
    """Where the sun is, by the NOAA algorithm.

    `hour` is decimal local time, so 16.5 is half past four in the afternoon.
    Good to about a hundredth of a degree, which is two orders of magnitude
    finer than anything this is used for.
    """
    jd = _julian_day(when, hour)
    t = (jd - 2451545.0) / 36525.0

    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    centre = (
        sin(radians(mean_anom)) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + sin(radians(2 * mean_anom)) * (0.019993 - 0.000101 * t)
        + sin(radians(3 * mean_anom)) * 0.000289
    )
    true_long = mean_long + centre
    omega = 125.04 - 1934.136 * t
    apparent_long = true_long - 0.00569 - 0.00478 * sin(radians(omega))

    seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))
    mean_obliquity = 23.0 + (26.0 + seconds / 60.0) / 60.0
    obliquity = mean_obliquity + 0.00256 * cos(radians(omega))

    declination = degrees(
        asin(sin(radians(obliquity)) * sin(radians(apparent_long)))
    )

    # Equation of time, in minutes: the difference between clock noon and the
    # sun's own noon, which in Singapore is most of why solar noon is at 13:10.
    y = tan(radians(obliquity / 2.0)) ** 2
    eq_time = 4.0 * degrees(
        y * sin(2 * radians(mean_long))
        - 2 * eccentricity * sin(radians(mean_anom))
        + 4 * eccentricity * y * sin(radians(mean_anom)) * cos(2 * radians(mean_long))
        - 0.5 * y * y * sin(4 * radians(mean_long))
        - 1.25 * eccentricity * eccentricity * sin(2 * radians(mean_anom))
    )

    true_solar_minutes = (
        hour * 60.0 + eq_time + 4.0 * lon - 60.0 * UTC_OFFSET_HOURS
    )
    hour_angle = (true_solar_minutes / 4.0) - 180.0

    lat_r, dec_r, ha_r = radians(lat), radians(declination), radians(hour_angle)
    altitude = degrees(
        asin(sin(lat_r) * sin(dec_r) + cos(lat_r) * cos(dec_r) * cos(ha_r))
    )
    # atan2 form rather than the arccos one: near the zenith — which is where
    # the sun is at noon here — the arccos version loses all its precision and
    # can hand back a direction that is ninety degrees wrong.
    azimuth = (
        degrees(
            atan2(
                sin(ha_r),
                cos(ha_r) * sin(lat_r) - tan(dec_r) * cos(lat_r),
            )
        )
        + 180.0
    ) % 360.0
    return SunPosition(azimuth=round(azimuth, 3), altitude=round(altitude, 3))


@dataclass(frozen=True)
class TrackSample:
    """One moment of afternoon sun, and how much of the year it stands for."""

    month: int
    hour: float
    azimuth: float
    altitude: float
    weight: float        # days of the year this sample represents

    def as_dict(self) -> dict:
        return {
            "month": self.month, "hour": self.hour,
            "azimuth": self.azimuth, "altitude": self.altitude,
            "weight": self.weight,
        }


def afternoon_track(
    lat: float = ISLAND_LAT,
    lon: float = ISLAND_LON,
    year: int = 2026,
    step_minutes: int = 30,
) -> list[TrackSample]:
    """The afternoon sun through one year, sampled.

    The 15th of each month at half-hour steps: twelve samples of declination
    and nine of time of day. That is enough because neither varies quickly —
    declination moves under half a degree a day — and it keeps the table small
    enough to ship with the site, which is what lets the browser do the
    shading arithmetic for a floor and a facing that are only typed in.

    Samples below the horizon are dropped rather than carried as zeroes, so
    the length of the track is itself the answer to "how many afternoon hours
    of sun does this island get".
    """
    samples: list[TrackSample] = []
    step = step_minutes / 60.0
    for month in range(1, 13):
        when = date(year, month, 15)
        days = 365.25 / 12.0
        hour = AFTERNOON_START
        while hour <= AFTERNOON_END + 1e-9:
            pos = solar_position(lat, lon, when, hour)
            if pos.up:
                samples.append(TrackSample(
                    month=month, hour=round(hour, 2),
                    azimuth=pos.azimuth, altitude=pos.altitude,
                    weight=round(days * step, 4),
                ))
            hour += step
    return samples


def _angle_between(a: float, b: float) -> float:
    """Smallest angle between two bearings, 0..180."""
    diff = abs((a - b) % 360.0)
    return min(diff, 360.0 - diff)


@dataclass(frozen=True)
class Obstruction:
    """Something that stands between a window and the sun.

    `bearing` is from the flat to the obstruction, `height_m` is how far its
    top rises above the viewer's eye — negative for anything lower than the
    floor being considered — and `half_width` is how much of the horizon it
    covers, which is what decides whether the sun passes behind it or beside
    it.
    """

    bearing: float
    distance_m: float
    height_m: float
    half_width: float = 10.0
    label: str | None = None

    @property
    def elevation(self) -> float:
        """How high its top sits above the horizontal, in degrees."""
        if self.distance_m <= 0:
            return 90.0 if self.height_m > 0 else 0.0
        return degrees(atan2(self.height_m, self.distance_m))


def blocks_sun(obstructions: list[Obstruction], azimuth: float, altitude: float) -> str | None:
    """What, if anything, stands in front of the sun at that moment."""
    for item in obstructions:
        if _angle_between(item.bearing, azimuth) <= item.half_width:
            if item.elevation >= altitude:
                return item.label or "a neighbouring block"
    return None


@dataclass
class Exposure:
    """Afternoon sun on one face of one flat.

    `hours` and `low_hours` are per year. `low_hours` is the number that
    matters: sun below thirty degrees comes in under the overhang and across
    the floor, and is what makes a west-facing living room unusable at five
    o'clock.
    """

    facing: str
    bearing: float
    hours: float = 0.0
    low_hours: float = 0.0
    blocked_hours: float = 0.0
    blocked_low_hours: float = 0.0
    shaded_by: dict[str, float] | None = None
    peak_month: int | None = None

    @property
    def net_low_hours(self) -> float:
        """Low afternoon sun that actually arrives, shading taken off."""
        return round(max(0.0, self.low_hours - self.blocked_low_hours), 1)

    def as_dict(self) -> dict:
        return {
            "facing": self.facing,
            "bearing": self.bearing,
            "hours": round(self.hours, 1),
            "low_hours": round(self.low_hours, 1),
            "blocked_hours": round(self.blocked_hours, 1),
            "blocked_low_hours": round(self.blocked_low_hours, 1),
            "net_low_hours": self.net_low_hours,
            "shaded_by": dict(sorted(
                (self.shaded_by or {}).items(), key=lambda kv: -kv[1]
            )[:4]),
            "peak_month": self.peak_month,
        }


def exposure(
    facing: str,
    track: list[TrackSample] | None = None,
    obstructions: list[Obstruction] | None = None,
) -> Exposure:
    """Afternoon sun hours per year on a face, net of what shades it.

    The cosine of the angle between the sun and the way the window faces is
    the weighting, because that is the physics: sun at eighty degrees off the
    normal delivers a sixth of what sun straight on does, and counting both
    as "an hour of sun" is what makes a facing field useless.

    The mirror of this function runs in the browser, since the facing and the
    floor it needs are typed in rather than known here. `tests/test_sun.py`
    writes the fixture both implementations are checked against.
    """
    bearing = COMPASS.get((facing or "").strip().upper())
    if bearing is None:
        raise ValueError(f"not a compass point: {facing!r}")

    track = track if track is not None else afternoon_track()
    obstructions = obstructions or []
    result = Exposure(facing=facing.strip().upper(), bearing=bearing)
    shaded: dict[str, float] = {}
    by_month: dict[int, float] = {}

    for sample in track:
        offset = _angle_between(bearing, sample.azimuth)
        if offset >= FACE_HALF_ANGLE:
            continue                       # the sun is behind this wall
        share = sample.weight * cos(radians(offset))
        low = sample.altitude <= LOW_SUN_ALTITUDE

        result.hours += share
        if low:
            result.low_hours += share
            by_month[sample.month] = by_month.get(sample.month, 0.0) + share

        blocker = blocks_sun(obstructions, sample.azimuth, sample.altitude)
        if blocker:
            result.blocked_hours += share
            shaded[blocker] = shaded.get(blocker, 0.0) + share
            if low:
                result.blocked_low_hours += share

    result.shaded_by = {k: round(v, 1) for k, v in shaded.items()}
    result.peak_month = max(by_month, key=by_month.get) if by_month else None
    return result


def viewer_height_m(storey: int | None) -> float | None:
    """Eye height above ground for a storey, in metres.

    Storey 1 is the ground floor, so a tenth-floor flat's own floor is nine
    floor-to-floor heights up, and you are standing on it.
    """
    if storey is None or storey < 1:
        return None
    return (storey - 1) * STOREY_HEIGHT_M + EYE_HEIGHT_M


# What an unobstructed face of each orientation gets, in low afternoon sun
# hours per year, from the track above:
#
#     E, SE, NE     0      the sun is behind the wall all afternoon
#     N            94      only in June, when sunset swings to 293 degrees
#     S            97      only in December, when it swings to 247
#     SW, NW      470
#     W           667
#
# The thresholds are read off that spread, so the words compare one direction
# with the others rather than against a standard nobody has set.
def describe(exp: Exposure) -> str:
    """One line a person can read, not a number to interpret."""
    net = exp.net_low_hours
    if net <= 0:
        if exp.low_hours <= 0:
            return "no low afternoon sun — it faces away from the setting sun"
        shade = ", ".join((exp.shaded_by or {})) or "something to the west"
        return f"no low afternoon sun — {shade} stands in the way"
    if net < 30:
        return "almost no low afternoon sun"
    if net < 200:
        return "a little low afternoon sun, and only for part of the year"
    if net < 550:
        return "a lot of low afternoon sun"
    return "the full western afternoon"
