/**
 * What the three-dimensional view draws, tested away from the renderer.
 *
 * The case that matters is the envelope: a volume drawn over a parcel that is
 * already built to its limit tells a buyer something could go up there when
 * nothing can, and it looks exactly as convincing as a true one. The rest is
 * the arithmetic that decides what is near enough to draw at all.
 */
import { describe, expect, it } from 'vitest';

import {
  bandsByRatio,
  builtOn,
  centresOf,
  envelopesFor,
  extentOf,
  viewerHeightM,
  withinRadius,
  type Geom,
} from './massing';
import type { Building, ZoningParcel } from './types';

// 8 Boon Keng Rd, so the numbers are the ones a reader could check.
const LAT = 1.316424;
const LON = 103.861501;
const STOREY_M = 3;

/** A square of `size` degrees with its south-west corner at (lon, lat). */
function square(lon: number, lat: number, size = 0.0004): Geom {
  return {
    type: 'Polygon',
    coordinates: [
      [
        [lon, lat],
        [lon + size, lat],
        [lon + size, lat + size],
        [lon, lat + size],
        [lon, lat],
      ],
    ],
  };
}

function building(b: string, lon: number, lat: number, storeys: number, size = 0.0004) {
  return {
    i: `p${b}`,
    b,
    s: storeys,
    slug: `${b}-somewhere`,
    m: square(lon, lat, size),
  } as unknown as Building;
}

function parcel(id: number, lon: number, lat: number, ratio: number | undefined, size = 0.0008) {
  return {
    i: id,
    g: 'residential',
    u: 'Residential',
    ...(ratio === undefined ? {} : { r: ratio }),
    m: square(lon, lat, size),
  } as unknown as ZoningParcel;
}

const BANDS = {
  '2.8': { low: 4, high: 17, n: 9402 },
  // Written by Python's str(3.0). JSON parses the ratio back as the number 3,
  // whose String() is '3' — so a text lookup misses this key entirely.
  '3.0': { low: 12, high: 22, n: 2140 },
  '4.2': { low: 16, high: 40, n: 83 },
};

describe('where a shape sits', () => {
  it('measures metres east and north of the flat', () => {
    const [x0, y0, x1, y1] = extentOf(square(LON, LAT), LON, LAT);
    expect(x0).toBeCloseTo(0, 6);
    expect(y0).toBeCloseTo(0, 6);
    // 0.0004 degrees is about 44 m either way at this latitude.
    expect(x1).toBeCloseTo(44.1, 1);
    expect(y1).toBeCloseTo(44.2, 1);
  });

  it('keeps a shape whose nearest corner is inside the radius', () => {
    expect(withinRadius(extentOf(square(LON + 0.003, LAT), LON, LAT), 450)).toBe(true);
  });

  it('drops one that is wholly beyond it', () => {
    expect(withinRadius(extentOf(square(LON + 0.01, LAT), LON, LAT), 450)).toBe(false);
  });

  it('keeps a parcel large enough to contain the flat', () => {
    // The nearest point of the box, not its centre: a parcel can be a
    // kilometre across and still be the one you are standing on.
    const huge = square(LON - 0.01, LAT - 0.01, 0.02);
    expect(withinRadius(extentOf(huge, LON, LAT), 450)).toBe(true);
  });
});

describe('what stands on a parcel', () => {
  const standing = centresOf(
    [building('7', LON, LAT, 40), building('22', LON + 0.004, LAT, 12)],
    LON,
    LAT,
  );

  it('finds the tallest building inside the parcel', () => {
    const box = extentOf(square(LON - 0.0002, LAT - 0.0002, 0.0012), LON, LAT);
    expect(builtOn(box, standing)).toBe(40);
  });

  it('is zero for a parcel with nothing on it', () => {
    const box = extentOf(square(LON + 0.002, LAT + 0.002), LON, LAT);
    expect(builtOn(box, standing)).toBe(0);
  });
});

describe('the envelopes', () => {
  it('draws only the room above what already stands', () => {
    // A 2.8 parcel allows about 17 storeys; 12 stand, so 5 are left.
    const standing = centresOf([building('22', LON, LAT, 12)], LON, LAT);
    const { envelopes } = envelopesFor(
      [parcel(1, LON - 0.0002, LAT - 0.0002, 2.8)],
      standing,
      BANDS,
      STOREY_M,
      LON,
      LAT,
    );
    expect(envelopes).toHaveLength(1);
    expect(envelopes[0]!.stands).toBe(12);
    expect(envelopes[0]!.room).toBe(5);
    expect(envelopes[0]!.baseM).toBe(36);
    expect(envelopes[0]!.topM).toBe(51);
  });

  it('draws nothing for a parcel already built to its limit', () => {
    // The failure that would matter: a volume over a full parcel reads as a
    // warning, and it is exactly as convincing as a real one.
    const standing = centresOf([building('9', LON, LAT, 40)], LON, LAT);
    const result = envelopesFor(
      [parcel(1, LON - 0.0002, LAT - 0.0002, 4.2)],
      standing,
      BANDS,
      STOREY_M,
      LON,
      LAT,
    );
    expect(result.envelopes).toEqual([]);
    expect(result.spent).toBe(1);
    expect(result.summary).toContain('1 nearby parcel is already at the limit');
  });

  it('starts at the ground where nothing stands', () => {
    const { envelopes } = envelopesFor(
      [parcel(1, LON, LAT, 4.2)],
      [],
      BANDS,
      STOREY_M,
      LON,
      LAT,
    );
    expect(envelopes[0]!.stands).toBe(0);
    expect(envelopes[0]!.baseM).toBe(0);
    expect(envelopes[0]!.room).toBe(40);
    expect(envelopes[0]!.detail).toContain('nothing standing');
  });

  it('takes the top of the band, not its middle', () => {
    // Worst case is the question being asked. 4.2 fits 16-40; it must be 40.
    const { envelopes } = envelopesFor([parcel(1, LON, LAT, 4.2)], [], BANDS, STOREY_M, LON, LAT);
    expect(envelopes[0]!.topM).toBe(40 * STOREY_M);
  });

  it('skips a parcel URA fixed no ratio for', () => {
    const result = envelopesFor([parcel(1, LON, LAT, undefined)], [], BANDS, STOREY_M, LON, LAT);
    expect(result.envelopes).toEqual([]);
    expect(result.spent).toBe(0);
  });

  it('skips a ratio with no fitted band behind it', () => {
    const result = envelopesFor([parcel(1, LON, LAT, 9.9)], [], BANDS, STOREY_M, LON, LAT);
    expect(result.envelopes).toEqual([]);
  });

  it('reports the most room on any one parcel', () => {
    const result = envelopesFor(
      [parcel(1, LON, LAT, 2.8), parcel(2, LON + 0.002, LAT, 4.2)],
      [],
      BANDS,
      STOREY_M,
      LON,
      LAT,
    );
    expect(result.mostRoom).toBe(40);
    expect(result.summary).toContain('40 storeys');
  });

  it('says so plainly when there is no room anywhere', () => {
    const standing = centresOf([building('9', LON, LAT, 40)], LON, LAT);
    const result = envelopesFor(
      [parcel(1, LON - 0.0002, LAT - 0.0002, 4.2)],
      standing,
      BANDS,
      STOREY_M,
      LON,
      LAT,
    );
    expect(result.summary).toContain('Nothing in view has room left');
  });
});

describe('looking a band up by its plot ratio', () => {
  it('finds a whole-numbered ratio whose key Python wrote with a decimal', () => {
    // The regression that made this view lie: 206 of the 310 parcels around
    // 8 Boon Keng Rd are ratio 3.0, and a String(3) lookup found none of
    // them, so the view reported no room where there were 22 storeys of it.
    const { envelopes } = envelopesFor([parcel(1, LON, LAT, 3)], [], BANDS, STOREY_M, LON, LAT);
    expect(envelopes).toHaveLength(1);
    expect(envelopes[0]!.room).toBe(22);
  });

  it('reads the keys as numbers, however either side spelled them', () => {
    const map = bandsByRatio({ '3.0': { low: 12, high: 22, n: 2140 } });
    expect(map.get(3)).toEqual({ low: 12, high: 22, n: 2140 });
  });

  it('ignores a key that is not a number at all', () => {
    expect(bandsByRatio({ note: { low: 1, high: 2, n: 3 } }).size).toBe(0);
  });
});

describe('the eye line', () => {
  it('puts storey 1 at standing height on the ground floor', () => {
    expect(viewerHeightM(1, STOREY_M)).toBe(1.5);
  });

  it('counts floor-to-floor from there, as sun.py does', () => {
    expect(viewerHeightM(12, STOREY_M)).toBe(34.5);
  });

  it('is unknown rather than zero when no floor was typed', () => {
    expect(viewerHeightM(null, STOREY_M)).toBeNull();
  });
});
