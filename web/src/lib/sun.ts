/**
 * Afternoon sun on one face of one flat.
 *
 * This is the mirror of `exposure` in `nhf/sun.py`, and it is here for one
 * reason: the facing and the floor are typed in by the person looking at the
 * page, so the shading cannot be precomputed. What *is* precomputed is
 * everything expensive — the sun's track through the year, which is the same
 * for every block on a 50 km island — and that arrives in `meta.json`.
 *
 * Two implementations of one piece of arithmetic is a real risk, and
 * `tests/golden/sun.json` is the answer to it: Python writes the expected
 * numbers, `sun.test.ts` reads the same file, and a change to either side
 * without the other fails a test rather than quietly disagreeing.
 *
 * The physics, briefly, because it decides what is worth showing. Singapore
 * is 1.35 degrees north, so the midday sun is nearly overhead all year and a
 * north or south window takes very little of it. The problem is the low
 * western sun from about four o'clock, which arrives almost horizontally and
 * under any overhang. Sunset swings from 247 degrees in December to 293 in
 * June, so due west bakes all year while north-west bakes in our summer.
 */

import type { ExposureData, SunConstants, TrackSample } from './types';

export const COMPASS: Record<string, number> = {
  N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315,
};

/** Something standing between a window and the sun. */
export interface Obstruction {
  bearing: number;
  distance_m: number;
  /** How far its top rises above the viewer's eye. Negative if it is lower. */
  height_m: number;
  half_width: number;
  label?: string;
}

const toRad = (deg: number) => (deg * Math.PI) / 180;
const toDeg = (rad: number) => (rad * 180) / Math.PI;

/** Smallest angle between two bearings, 0..180. */
export function angleBetween(a: number, b: number): number {
  const diff = Math.abs(((a - b) % 360 + 360) % 360);
  return Math.min(diff, 360 - diff);
}

/** How high an obstruction's top sits above the horizontal, in degrees. */
export function elevationOf(item: Obstruction): number {
  if (item.distance_m <= 0) return item.height_m > 0 ? 90 : 0;
  return toDeg(Math.atan2(item.height_m, item.distance_m));
}

/** What, if anything, stands in front of the sun at that moment. */
export function blocksSun(
  obstructions: Obstruction[],
  azimuth: number,
  altitude: number,
): string | null {
  for (const item of obstructions) {
    if (angleBetween(item.bearing, azimuth) <= item.half_width) {
      if (elevationOf(item) >= altitude) {
        return item.label ?? 'a neighbouring block';
      }
    }
  }
  return null;
}

/**
 * Round the way Python's `round` does, to match the fixture.
 *
 * JavaScript rounds .5 away from zero and Python rounds it to even, which
 * would put the two implementations a cent apart on exactly the values most
 * likely to appear in a test.
 */
function roundHalfEven(value: number, digits = 0): number {
  const factor = 10 ** digits;
  const scaled = value * factor;
  const floor = Math.floor(scaled);
  const diff = scaled - floor;
  let rounded: number;
  if (Math.abs(diff - 0.5) < Number.EPSILON * Math.max(1, Math.abs(scaled))) {
    rounded = floor % 2 === 0 ? floor : floor + 1;
  } else {
    rounded = Math.round(scaled);
  }
  return rounded / factor;
}

export interface ExposureResult extends ExposureData {
  says: string;
}

/**
 * Afternoon sun hours per year on a face, net of what shades it.
 *
 * The cosine of the angle between the sun and the way the window faces is the
 * weighting, because that is the physics: sun at eighty degrees off the normal
 * delivers a sixth of what sun straight on does, and counting both as "an
 * hour of sun" is what makes a facing field useless.
 */
export function exposure(
  facing: string,
  track: TrackSample[],
  constants: SunConstants,
  obstructions: Obstruction[] = [],
): ExposureResult {
  const bearing = COMPASS[(facing ?? '').trim().toUpperCase()];
  if (bearing === undefined) {
    throw new Error(`not a compass point: ${facing}`);
  }

  let hours = 0;
  let lowHours = 0;
  let blockedHours = 0;
  let blockedLowHours = 0;
  const shaded: Record<string, number> = {};
  const byMonth: Record<number, number> = {};

  for (const sample of track) {
    const offset = angleBetween(bearing, sample.azimuth);
    if (offset >= constants.face_half_angle) continue; // behind this wall
    const share = sample.weight * Math.cos(toRad(offset));
    const low = sample.altitude <= constants.low_sun_altitude;

    hours += share;
    if (low) {
      lowHours += share;
      byMonth[sample.month] = (byMonth[sample.month] ?? 0) + share;
    }

    const blocker = blocksSun(obstructions, sample.azimuth, sample.altitude);
    if (blocker) {
      blockedHours += share;
      shaded[blocker] = (shaded[blocker] ?? 0) + share;
      if (low) blockedLowHours += share;
    }
  }

  const netLow = roundHalfEven(Math.max(0, lowHours - blockedLowHours), 1);
  const months = Object.keys(byMonth).map(Number);
  // Highest share wins; ties go to the earlier month, as Python's `max` does.
  let peakMonth: number | null = null;
  for (const month of months) {
    if (peakMonth === null || byMonth[month]! > byMonth[peakMonth]!) {
      peakMonth = month;
    }
  }

  const shadedRounded: Record<string, number> = {};
  for (const [label, value] of Object.entries(shaded)) {
    shadedRounded[label] = roundHalfEven(value, 1);
  }
  // Largest shade first, and at most four — the same trim Python applies, so
  // the fixtures agree.
  const topShade: Record<string, number> = {};
  for (const [label, value] of Object.entries(shadedRounded)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)) {
    topShade[label] = value;
  }

  const result: ExposureResult = {
    facing: (facing ?? '').trim().toUpperCase(),
    bearing,
    hours: roundHalfEven(hours, 1),
    low_hours: roundHalfEven(lowHours, 1),
    blocked_hours: roundHalfEven(blockedHours, 1),
    blocked_low_hours: roundHalfEven(blockedLowHours, 1),
    net_low_hours: netLow,
    shaded_by: topShade,
    peak_month: peakMonth,
    says: '',
  };
  result.says = describe(result);
  return result;
}

/**
 * One line a person can read, not a number to interpret.
 *
 * The thresholds are read off what the eight facings actually get — east,
 * south-east and north-east nothing, north 94 hours, south 97, south-west and
 * north-west 470, west 667 — so the words compare one direction with the
 * others rather than against a standard nobody has set.
 */
export function describe(exp: ExposureData): string {
  const net = exp.net_low_hours;
  if (net <= 0) {
    if (exp.low_hours <= 0) {
      return 'no low afternoon sun — it faces away from the setting sun';
    }
    const shade = Object.keys(exp.shaded_by).join(', ') || 'something to the west';
    return `no low afternoon sun — ${shade} stands in the way`;
  }
  if (net < 30) return 'almost no low afternoon sun';
  if (net < 200) return 'a little low afternoon sun, and only for part of the year';
  if (net < 550) return 'a lot of low afternoon sun';
  return 'the full western afternoon';
}

const MONTHS = [
  '', 'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export function monthName(month: number | null): string | null {
  return month === null ? null : (MONTHS[month] ?? null);
}
