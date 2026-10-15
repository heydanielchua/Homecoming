/**
 * Which pin a school gets.
 *
 * This mirrors `school_levels` in nhf/nearby.py, so the two can drift. The
 * eight spellings below are every value MOE publishes across 337 schools, and
 * a miss here draws a secondary school under the primary pill — which is
 * exactly what the map did before this existed.
 */
import { describe, expect, it } from 'vitest';

import { mapKindOf } from './places';

const school = (subtype: string | null) =>
  mapKindOf({ kind: 'school', subtype } as never);

describe('splitting schools by level', () => {
  it('keeps a primary school on the primary pin', () => {
    expect(school('PRIMARY')).toBe('school');
  });

  it('moves a secondary school to its own', () => {
    expect(school('SECONDARY (S1-S5)')).toBe('secondary_school');
    expect(school('SECONDARY (S1-S4)')).toBe('secondary_school');
  });

  it('counts a junior college as secondary rather than as nothing', () => {
    expect(school('JUNIOR COLLEGE')).toBe('secondary_school');
    expect(school('CENTRALISED INSTITUTE')).toBe('secondary_school');
    expect(school('MIXED LEVEL (S1-JC2)')).toBe('secondary_school');
  });

  it('draws a school that teaches both as primary', () => {
    // MIXED LEVEL (P1-S4) says PRIMARY nowhere. Matching on the word alone
    // would make it secondary, and the ballot is what a buyer is choosing a
    // flat for, so it belongs on the primary pin.
    expect(school('MIXED LEVEL (P1-S4)')).toBe('school');
  });

  it('falls back to the primary pin when the level is missing', () => {
    // Not a guess with consequences: it keeps the behaviour the map had
    // before levels were split at all.
    expect(school(null)).toBe('school');
    expect(school('')).toBe('school');
    expect(school('SOMETHING NEW MOE STARTS PUBLISHING')).toBe('school');
  });

  it('leaves every other kind of place alone', () => {
    for (const kind of ['mrt_exit', 'bus_stop', 'hawker_centre', 'preschool', 'park']) {
      expect(mapKindOf({ kind, subtype: 'PRIMARY' } as never)).toBe(kind);
    }
  });
});
