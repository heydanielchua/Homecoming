/**
 * The TypeScript side of the shared view fixture.
 *
 * Same arrangement as the sun: Python writes `tests/golden/view.json`, both
 * implementations are asserted against it, and a drift fails a test instead
 * of producing two plausible answers.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { exposure } from './sun';
import { compassPoint, mergeArcs, obstructionsFor, viewFrom, viewerHeight } from './view';
import type { NeighbourData, SunConstants, ViewData } from './types';

const sunGolden = JSON.parse(
  readFileSync(join(__dirname, '../../../tests/golden/sun.json'), 'utf8'),
) as { constants: SunConstants; track: { month: number; hour: number; azimuth: number; altitude: number; weight: number }[] };

const golden = JSON.parse(
  readFileSync(join(__dirname, '../../../tests/golden/view.json'), 'utf8'),
) as {
  block_max_floor: number;
  /** Exactly what a block file ships, so nothing is reshaped on the way in. */
  neighbours: NeighbourData[];
  cases: {
    name: string;
    storey: number | null;
    located?: boolean;
    expected: ViewData;
  }[];
};

const constants = sunGolden.constants;

/** The fixture ships them in the block-file shape already. */
const neighbours: NeighbourData[] = golden.neighbours;

describe('the shared view fixture', () => {
  for (const testCase of golden.cases) {
    it(`agrees with Python: ${testCase.name}`, () => {
      const located = testCase.located ?? true;
      const got = viewFrom(
        located ? neighbours : [],
        testCase.storey,
        constants,
        located,
      );
      // `clear` carries the direction string, which Python derives the same
      // way; comparing the whole object checks that too.
      expect(got).toEqual(testCase.expected);
    });
  }
});

const one = (storeys: number, distance = 55, bearing = 270): NeighbourData[] => [
  { name: 'X', distance_m: distance, bearing, direction: compassPoint(bearing), storeys },
];

describe('what blocks a view', () => {
  it('counts a taller block as above a low floor', () => {
    const view = viewFrom(one(25), 4, constants);
    expect(view.blocked).toHaveLength(1);
    expect(view.blocked[0]!.storeys_above).toBeCloseTo(20.5, 1);
    expect(view.open_directions).not.toContain('W');
  });

  it('treats a block of equal height as level with you, not above you', () => {
    // Its roof is a storey above its own top floor. Comparing eyes with roofs
    // would report a blocked view from half the flats on the estate.
    const view = viewFrom(one(16, 40, 180), 16, constants);
    expect(view.blocked).toHaveLength(0);
    expect(view.clear.map((n) => n.name)).toEqual(['X']);
  });

  it('counts one storey taller as above you', () => {
    const view = viewFrom(one(17, 40, 180), 16, constants);
    expect(view.blocked).toHaveLength(1);
    expect(view.blocked[0]!.storeys_above).toBeCloseTo(0.5, 1);
  });

  it('does not treat an unknown storey count as a clear view', () => {
    const unknown: NeighbourData[] = [
      { name: 'X', distance_m: 50, bearing: 270, direction: 'W', storeys: null },
    ];
    const view = viewFrom(unknown, 4, constants);
    expect(view.blocked).toHaveLength(0);
    expect(view.clear).toHaveLength(0);
    expect(view.above_everything).toBe(false);
  });

  it('leaves the view unknown when no floor was given', () => {
    const view = viewFrom(one(25), null, constants);
    expect(view.unknown).toContain('floor was not given');
    expect(view.above_everything).toBe(false);
  });

  it('leaves the view unknown when the block was never geocoded', () => {
    // An empty neighbour list means two different things. From a placed
    // block it means nothing is within 300 m; from an unplaced one it means
    // nothing was measured, and reporting that as a clear view in every
    // direction is exactly the false confidence this report avoids.
    const unplaced = viewFrom([], 8, constants, false);
    expect(unplaced.unknown).toContain('no geocode');
    expect(unplaced.open_directions).toEqual([]);
    expect(unplaced.above_everything).toBe(false);

    const placed = viewFrom([], 8, constants, true);
    expect(placed.unknown).toBeNull();
    expect(placed.open_directions).toHaveLength(8);
  });

  it('has nothing to shade with when the block was never geocoded', () => {
    expect(obstructionsFor(viewFrom([], 8, constants, false), constants)).toEqual([]);
  });

  it('does not double-count two blocks in one direction', () => {
    const single = viewFrom(one(30, 60), 4, constants).blocked_arc_deg;
    const pair = viewFrom(
      [...one(30, 60), { name: 'Y', distance_m: 62, bearing: 272, direction: 'W', storeys: 30 }],
      4,
      constants,
    ).blocked_arc_deg;
    // Not identical — the second block is 2 degrees round and widens the arc
    // a little — but nothing like twice as wide, which is what summing the
    // widths would have given.
    expect(pair).toBeGreaterThanOrEqual(single);
    expect(pair).toBeLessThan(single + 3);
  });

  it('merges arcs that wrap through north', () => {
    expect(mergeArcs([{ centre: 0, half: 20 }])).toBeCloseTo(40, 5);
    expect(mergeArcs([{ centre: 350, half: 20 }, { centre: 10, half: 20 }])).toBeCloseTo(60, 5);
  });

  it('puts the ground floor at eye level rather than on the pavement', () => {
    expect(viewerHeight(1, constants)).toBe(1.5);
    expect(viewerHeight(10, constants)).toBe(9 * constants.storey_height_m + 1.5);
    expect(viewerHeight(null, constants)).toBeNull();
  });
});

describe('the view and the sun are the same geometry', () => {
  it('loses the western afternoon low down and keeps it high up', () => {
    // The PRD's own observation: the block that ruins the view saves the
    // afternoons.
    const west = one(16, 40, 270);
    const low = viewFrom(west, 4, constants);
    const high = viewFrom(west, 16, constants);
    const lowSun = exposure('W', sunGolden.track, constants, obstructionsFor(low, constants));
    const highSun = exposure('W', sunGolden.track, constants, obstructionsFor(high, constants));
    expect(lowSun.net_low_hours).toBe(0);
    expect(highSun.net_low_hours).toBeGreaterThan(600);
  });

  it('names the block that shades it', () => {
    const west = one(25, 55, 270);
    const view = viewFrom(west, 4, constants);
    const sun = exposure('W', sunGolden.track, constants, obstructionsFor(view, constants));
    expect(Object.keys(sun.shaded_by)).toContain('block X');
  });

  it('has nothing to shade with when the floor is unknown', () => {
    const view = viewFrom(one(25), null, constants);
    expect(obstructionsFor(view, constants)).toEqual([]);
  });
});
