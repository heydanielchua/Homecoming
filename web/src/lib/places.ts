/**
 * Splitting schools by level, for the map.
 *
 * Python ships every school under one kind with MOE's level in `subtype`,
 * because that is what the amenity table holds. The map wants them apart:
 * a primary school and a secondary school are different news — one is a
 * ballot you might win, the other is a journey your child makes — and a
 * single pin colour for both says neither.
 *
 * This mirrors `school_levels` in nhf/nearby.py and is held to the same
 * eight spellings. The year ranges carry the answer, not the words: MIXED
 * LEVEL (P1-S4) says PRIMARY nowhere and teaches to secondary four.
 */

import type { MapPoint, PointKind } from './types';

/** What the map keys a pin's colour and its filter pill off. */
export type MapKind = PointKind | 'secondary_school';

/**
 * Which pin a place belongs to.
 *
 * A school that teaches both levels is drawn as primary, because the ballot
 * is the thing a buyer is choosing a flat for; it still appears under its own
 * name in the list.
 */
export function mapKindOf(point: Pick<MapPoint, 'kind' | 'subtype'>): MapKind {
  if (point.kind !== 'school') return point.kind;
  const level = (point.subtype ?? '').toUpperCase();
  const primary = level.includes('PRIMARY') || /\bP\d/.test(level);
  if (primary) return 'school';
  const secondary =
    level.includes('SECONDARY') ||
    /\bS\d/.test(level) ||
    level.includes('JUNIOR COLLEGE') ||
    level.includes('CENTRALISED INSTITUTE');
  return secondary ? 'secondary_school' : 'school';
}
