/**
 * The arithmetic behind the three-dimensional view, kept out of the renderer.
 *
 * None of this draws anything. It decides what is near enough to matter, how
 * tall a thing is, and — the only part where a bug would be both silent and
 * serious — how much room is left above what already stands on a parcel. An
 * envelope drawn over a building that already fills it is not a warning, it
 * is a lie, and the only way to catch that is to test it away from WebGL.
 */

import type { Building, ZoningParcel } from './types';

/** Metres per degree at Singapore's latitude, matching nhf/geo.py. */
export const M_PER_DEG_LAT = 110574;
export const M_PER_DEG_LON = 110320;

export const EYE_HEIGHT_M = 1.5;

/** A GeoJSON ring is a closed list of positions; a polygon is outer + holes. */
export type Ring = [number, number][];
export type Rings = Ring[];
export interface Geom {
  type: string;
  coordinates: unknown;
}

/** Local metres east and north of an origin. */
export function project(
  lon: number,
  lat: number,
  originLon: number,
  originLat: number,
): [number, number] {
  return [(lon - originLon) * M_PER_DEG_LON, (lat - originLat) * M_PER_DEG_LAT];
}

export function polygons(geom: Geom): Rings[] {
  return geom.type === 'Polygon'
    ? [geom.coordinates as Rings]
    : (geom.coordinates as Rings[]);
}

export type Box = [number, number, number, number];

/** [minEast, minNorth, maxEast, maxNorth] in metres from the origin. */
export function extentOf(geom: Geom, originLon: number, originLat: number): Box {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const rings of polygons(geom)) {
    for (const [lon, lat] of rings[0] ?? []) {
      const [x, y] = project(lon, lat, originLon, originLat);
      if (x < x0) x0 = x;
      if (y < y0) y0 = y;
      if (x > x1) x1 = x;
      if (y > y1) y1 = y;
    }
  }
  return [x0, y0, x1, y1];
}

/**
 * True when the shape's box reaches within `radius` of the origin.
 *
 * The nearest point of the box, not its centre: a parcel can be a kilometre
 * across and still be the one you are standing on.
 */
export function withinRadius(box: Box, radius: number): boolean {
  const [x0, y0, x1, y1] = box;
  if (x0 > x1) return false;
  const x = Math.max(x0, Math.min(0, x1));
  const y = Math.max(y0, Math.min(0, y1));
  return x * x + y * y <= radius * radius;
}

export interface Standing {
  x: number;
  y: number;
  storeys: number;
}

/** Each building's centre and height, for the parcel test below. */
export function centresOf(
  buildings: Building[],
  originLon: number,
  originLat: number,
): Standing[] {
  return buildings.map((b) => {
    const [x0, y0, x1, y1] = extentOf(b.m as Geom, originLon, originLat);
    return { x: (x0 + x1) / 2, y: (y0 + y1) / 2, storeys: b.s ?? 1 };
  });
}

/**
 * The tallest building standing on a parcel, in storeys, or 0 for none.
 *
 * The test is the parcel's bounding box rather than a true point-in-polygon:
 * fifty fewer lines, and wrong only for a building sitting in the notch of an
 * L-shaped parcel. It is stated in the view's own caption rather than hidden.
 */
export function builtOn(box: Box, standing: Standing[]): number {
  let top = 0;
  for (const c of standing) {
    if (c.x >= box[0] && c.x <= box[2] && c.y >= box[1] && c.y <= box[3] && c.storeys > top) {
      top = c.storeys;
    }
  }
  return top;
}

export interface Band {
  low: number;
  high: number;
  n: number;
}

/**
 * The fitted bands, keyed by plot ratio as a number rather than as text.
 *
 * `meta.json` is written by Python, whose `str(3.0)` is `"3.0"`. JSON parses
 * that ratio back to the number 3, and JavaScript's `String(3)` is `"3"`,
 * which matches no key. Looking the band up by the text of the number
 * therefore found nothing for every whole-numbered ratio — 206 of the 310
 * parcels around one block in Boon Keng, all of them at ratio 3.0 — and the
 * view said the land around you had no room left when it had 22 storeys of
 * it. A lookup that under-reports is worse than one that fails loudly, so the
 * key is the parsed number and the formatting on either side stops mattering.
 */
export function bandsByRatio(bands: Record<string, Band>): Map<number, Band> {
  const out = new Map<number, Band>();
  for (const [key, band] of Object.entries(bands)) {
    const ratio = Number(key);
    if (Number.isFinite(ratio)) out.set(ratio, band);
  }
  return out;
}

export interface Envelope {
  parcel: ZoningParcel;
  /** Storeys already standing on it. */
  stands: number;
  /** Storeys of room above that, at the top of the fitted band. */
  room: number;
  /** Height in metres at which the volume starts. */
  baseM: number;
  /** Height in metres of its top. */
  topM: number;
  label: string;
  detail: string;
}

export interface EnvelopeResult {
  envelopes: Envelope[];
  /** Parcels already built to their limit, so nothing is drawn for them. */
  spent: number;
  /** The most storeys of room on any one parcel in view. */
  mostRoom: number;
  summary: string;
}

/**
 * What could still go up, parcel by parcel.
 *
 * Deliberately the top of the band rather than its middle: the question this
 * answers is how bad it can get, not how it is likely to turn out. A parcel
 * with a building already at or within a storey of the band's top gets no
 * volume at all — permission that is already spent is not news.
 */
export function envelopesFor(
  parcels: ZoningParcel[],
  standing: Standing[],
  bands: Record<string, Band>,
  storeyM: number,
  originLon: number,
  originLat: number,
): EnvelopeResult {
  const byRatio = bandsByRatio(bands);
  const envelopes: Envelope[] = [];
  let spent = 0;
  let mostRoom = 0;

  for (const parcel of parcels) {
    if (parcel.r === undefined || parcel.r === null) continue;
    const band = byRatio.get(parcel.r);
    if (!band) continue;

    const stands = builtOn(extentOf(parcel.m as Geom, originLon, originLat), standing);
    const room = band.high - stands;
    if (stands > 0 && room <= 1) {
      spent += 1;
      continue;
    }
    if (room <= 0) continue;

    envelopes.push({
      parcel,
      stands,
      room,
      baseM: stands * storeyM,
      topM: band.high * storeyM,
      label: parcel.u,
      detail:
        `plot ratio ${parcel.r} · ` +
        `${stands ? `${stands} storeys standing` : 'nothing standing'}, ` +
        `up to about ${band.high} at that ratio — room for ${room} more`,
    });
    if (room > mostRoom) mostRoom = room;
  }

  // The count of spent parcels is the reassuring half of the answer, so it
  // survives into the case where there is nothing left to draw at all.
  const atLimit = `${spent} nearby ${spent === 1 ? 'parcel is' : 'parcels are'} already at the limit`;
  const summary = envelopes.length
    ? `Drawn to the top of each fitted band: ${atLimit}, and the most room ` +
      `anywhere in view is ${mostRoom} storeys.`
    : spent > 0
      ? `Nothing in view has room left above what already stands: ${atLimit}.`
      : 'No parcel in view has a plot ratio fixed, so there is nothing to draw.';

  return { envelopes, spent, mostRoom, summary };
}

/** Eye height above ground for a storey. Mirrors `viewer_height_m` in sun.py. */
export function viewerHeightM(storey: number | null, storeyM: number): number | null {
  if (storey === null || storey < 1) return null;
  return (storey - 1) * storeyM + EYE_HEIGHT_M;
}
