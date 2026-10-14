/**
 * What the view looks at from one floor, and what shades the sun from it.
 *
 * The mirror of `view_from` in `nhf/view.py`, here because the floor is typed
 * in rather than known when the site is built. `tests/golden/view.json` holds
 * the two implementations to the same answers.
 *
 * The distinction worth keeping is which height goes into which comparison. A
 * block's roof stands a storey above its own top floor, so:
 *
 *   * what blocks a **view** is somebody's floor above your eye line — by
 *     that measure a block of equal height is level with you, not above you;
 *   * what shades the **sun** is the roof above it, which is a storey higher.
 *
 * Comparing eyes with roofs instead would report a blocked view from half the
 * flats on the estate.
 */

import type {
  BlockedNeighbourData,
  NeighbourData,
  SunConstants,
  ViewData,
} from './types';
import type { Obstruction } from './sun';

const toDeg = (rad: number) => (rad * 180) / Math.PI;

const POINTS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;

export function compassPoint(bearing: number): string {
  const index = Math.floor((((bearing % 360) + 360) % 360 + 22.5) / 45) % 8;
  return POINTS[index]!;
}

/** Eye height above ground for a storey, in metres. */
export function viewerHeight(
  storey: number | null,
  constants: SunConstants,
): number | null {
  if (storey === null || storey < 1) return null;
  // Eye height is half the reason this function exists: see the note above.
  return (storey - 1) * constants.storey_height_m + eyeHeight(constants);
}

/**
 * Where your eyes are inside your own storey.
 *
 * Not published in `meta.json` as its own constant because it is only ever
 * used with the floor-to-floor height, and 1.5 m is half of nothing relative
 * to the heights involved. It is spelled out here so the two implementations
 * cannot disagree about it.
 */
export function eyeHeight(_constants: SunConstants): number {
  return 1.5;
}

function floorsOf(neighbour: NeighbourData): number | null {
  return neighbour.storeys ?? null;
}

/** The floor of a neighbour's highest flat. This is what blocks a view. */
export function topFloorHeight(
  neighbour: NeighbourData,
  constants: SunConstants,
): number | null {
  const floors = floorsOf(neighbour);
  if (!floors) return null;
  return (floors - 1) * constants.storey_height_m;
}

/** The top of the structure. This is what shades. */
export function roofHeight(
  neighbour: NeighbourData,
  constants: SunConstants,
): number | null {
  const floors = floorsOf(neighbour);
  if (!floors) return null;
  return floors * constants.storey_height_m;
}

/** How much horizon a neighbour covers, from one stated nominal length. */
export function halfWidthDeg(
  neighbour: NeighbourData,
  constants: SunConstants,
): number {
  if (neighbour.distance_m <= 0) return 90;
  return Math.min(
    90,
    toDeg(
      Math.atan2(constants.nominal_block_length_m / 2, neighbour.distance_m),
    ),
  );
}

/**
 * Total horizon covered, in degrees, with overlaps counted once.
 *
 * Two blocks in the same direction do not block twice as much view as one,
 * and summing their widths would say they did.
 */
export function mergeArcs(arcs: { centre: number; half: number }[]): number {
  if (arcs.length === 0) return 0;
  const spans: [number, number][] = [];
  for (const { centre, half } of arcs) {
    const start = ((centre - half) % 360 + 360) % 360;
    const end = ((centre + half) % 360 + 360) % 360;
    if (start <= end) spans.push([start, end]);
    else {
      spans.push([start, 360]);
      spans.push([0, end]);
    }
  }
  spans.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let total = 0;
  let [currentStart, currentEnd] = spans[0]!;
  for (const [start, end] of spans.slice(1)) {
    if (start <= currentEnd) currentEnd = Math.max(currentEnd, end);
    else {
      total += currentEnd - currentStart;
      currentStart = start;
      currentEnd = end;
    }
  }
  total += currentEnd - currentStart;
  return Math.min(360, total);
}

const round1 = (value: number) => Math.round(value * 10) / 10;

/** Which neighbours stand above a given floor, and which do not. */
export function viewFrom(
  neighbours: NeighbourData[],
  storey: number | null,
  constants: SunConstants,
): ViewData {
  if (storey === null || storey < 1) {
    return {
      storey: null,
      eye_height_m: null,
      unknown: 'the floor was not given, so what the view looks at is unknown',
      blocked: [],
      clear: [...neighbours],
      blocked_arc_deg: 0,
      open_directions: [],
      above_everything: false,
    };
  }

  const eye = viewerHeight(storey, constants)!;
  const blocked: BlockedNeighbourData[] = [];
  const clear: NeighbourData[] = [];

  for (const item of [...neighbours].sort((a, b) => a.distance_m - b.distance_m)) {
    const topFloor = topFloorHeight(item, constants);
    if (topFloor === null) {
      // A block with no published storey count is not evidence of a clear
      // view, so it is listed as neither.
      continue;
    }
    const rises = topFloor - eye;
    if (rises > constants.blocked_slack_m) {
      blocked.push({
        ...item,
        rises_m: round1(rises),
        elevation_deg: round1(toDeg(Math.atan2(rises, Math.max(1, item.distance_m)))),
        storeys_above: round1(rises / constants.storey_height_m),
      });
    } else {
      clear.push(item);
    }
  }

  const arc = mergeArcs(
    blocked.map((b) => ({ centre: b.bearing, half: halfWidthDeg(b, constants) })),
  );
  const blockedPoints = new Set(blocked.map((b) => compassPoint(b.bearing)));

  return {
    storey,
    eye_height_m: round1(eye),
    unknown: null,
    blocked,
    clear,
    blocked_arc_deg: round1(arc),
    open_directions: POINTS.filter((p) => !blockedPoints.has(p)),
    // Only when something is known to be below. A neighbour whose storey count
    // HDB does not publish leaves `clear` empty, and an empty `clear` is not a
    // view over the rooftops.
    above_everything: clear.length > 0 && blocked.length === 0,
  };
}

/**
 * Every neighbour, as the sun calculation wants them.
 *
 * Every one, not just the ones blocking the view, and measured to the roof:
 * a block level with yours does not take your view but its parapet still
 * stops the last few minutes of sun. A roof below your eye line comes through
 * as a negative height, which can never be in front of a sun above the
 * horizon.
 */
export function obstructionsFor(
  view: ViewData,
  constants: SunConstants,
): Obstruction[] {
  if (view.eye_height_m === null) return [];
  const eye = view.eye_height_m;
  const out: Obstruction[] = [];
  // Blocked and clear together: `view_from` leaves out only the neighbours
  // whose storey count HDB does not publish, and those have no roof height to
  // shade with either.
  for (const item of [...view.blocked, ...view.clear]) {
    const roof = roofHeight(item, constants);
    if (roof === null) continue;
    out.push({
      bearing: item.bearing,
      distance_m: item.distance_m,
      height_m: roof - eye,
      half_width: halfWidthDeg(item, constants),
      label: `block ${item.name}`,
    });
  }
  return out;
}
