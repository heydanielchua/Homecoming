/**
 * The TypeScript side of the shared sun fixture.
 *
 * `tests/golden/sun.json` is written by `python -m tests.make_golden` and
 * asserted against `nhf/sun.py` by `tests/test_sun.py`. This file reads the
 * same numbers. If one implementation changes and the other does not, one of
 * the two suites fails — which is the only way to notice, because both sides
 * would otherwise keep returning plausible hours.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { COMPASS, blocksSun, exposure, type Obstruction } from './sun';
import type { SunConstants, TrackSample } from './types';

interface Golden {
  lat: number;
  lon: number;
  year: number;
  step_minutes: number;
  constants: SunConstants;
  track: TrackSample[];
  cases: {
    name: string;
    facing: string;
    obstructions: Obstruction[];
    expected: Record<string, unknown>;
  }[];
}

const golden: Golden = JSON.parse(
  readFileSync(join(__dirname, '../../../tests/golden/sun.json'), 'utf8'),
);

describe('the shared sun fixture', () => {
  it('has a track to work from', () => {
    expect(golden.track.length).toBeGreaterThan(50);
    expect(golden.constants.low_sun_altitude).toBe(30);
  });

  for (const testCase of golden.cases) {
    it(`agrees with Python: ${testCase.name}`, () => {
      const got = exposure(
        testCase.facing,
        golden.track,
        golden.constants,
        testCase.obstructions,
      );
      const { says: _says, ...numbers } = got;
      expect(numbers).toEqual(testCase.expected);
    });
  }
});

describe('the physics the fixture encodes', () => {
  it('gives an east-facing flat no afternoon sun at all', () => {
    for (const facing of ['E', 'SE', 'NE']) {
      expect(exposure(facing, golden.track, golden.constants).hours).toBe(0);
    }
  });

  it('makes west the worst facing', () => {
    const hours = Object.fromEntries(
      Object.keys(COMPASS).map((f) => [
        f,
        exposure(f, golden.track, golden.constants).low_hours,
      ]),
    );
    expect(hours.W).toBeGreaterThan(hours.SW!);
    expect(hours.SW).toBeGreaterThan(hours.S!);
    expect(hours.W).toBeGreaterThan(600);
  });

  it('peaks a north-facing flat in June and a south-facing one in December', () => {
    // Sunset swings to 293 degrees in June and 247 in December.
    expect(exposure('N', golden.track, golden.constants).peak_month).toBe(6);
    expect(exposure('S', golden.track, golden.constants).peak_month).toBe(12);
  });

  it('refuses a facing that is not a compass point', () => {
    expect(() => exposure('WESTISH', golden.track, golden.constants)).toThrow();
  });
});

describe('shading', () => {
  const tall: Obstruction[] = [
    { bearing: 270, distance_m: 60, height_m: 30, half_width: 25, label: 'the next block' },
  ];

  it('takes the afternoon away from a west face', () => {
    const open = exposure('W', golden.track, golden.constants);
    const shaded = exposure('W', golden.track, golden.constants, tall);
    expect(shaded.low_hours).toBe(open.low_hours); // the sun is still there
    expect(shaded.blocked_low_hours).toBeGreaterThan(0); // it does not arrive
    expect(shaded.net_low_hours).toBeLessThan(open.net_low_hours / 2);
    expect(shaded.shaded_by['the next block']).toBeGreaterThan(0);
  });

  it('names the blocker only once there is no sun left to lose', () => {
    // With some sun still arriving, the sentence is about how much; with none,
    // the only useful thing to say is what is in the way.
    const wall: Obstruction[] = [
      { bearing: 270, distance_m: 20, height_m: 60, half_width: 90, label: 'Block 1' },
    ];
    expect(exposure('W', golden.track, golden.constants, tall).says).not.toContain(
      'the next block',
    );
    expect(exposure('W', golden.track, golden.constants, wall).says).toContain('Block 1');
  });

  it('ignores a block the sun passes beside', () => {
    const narrow: Obstruction[] = [
      { bearing: 180, distance_m: 40, height_m: 40, half_width: 10, label: 'south' },
    ];
    expect(
      exposure('W', golden.track, golden.constants, narrow).blocked_hours,
    ).toBe(0);
  });

  it('ignores a block below the floor being considered', () => {
    const low: Obstruction[] = [
      { bearing: 270, distance_m: 60, height_m: -20, half_width: 25, label: 'carpark' },
    ];
    expect(exposure('W', golden.track, golden.constants, low).blocked_hours).toBe(0);
  });

  it('names what is in the way, and only when it really is', () => {
    const items: Obstruction[] = [
      { bearing: 265, distance_m: 50, height_m: 25, half_width: 15, label: 'Block 123' },
    ];
    expect(blocksSun(items, 270, 10)).toBe('Block 123');
    expect(blocksSun(items, 270, 40)).toBeNull(); // sun above it
    expect(blocksSun(items, 180, 10)).toBeNull(); // wrong side
  });

  it('says a face with no sun is facing away rather than blaming a neighbour', () => {
    expect(exposure('E', golden.track, golden.constants).says).toContain('faces away');
  });
});
