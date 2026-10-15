/**
 * Picking the flat type from a typed size.
 *
 * This replaced a dropdown, so a wrong answer here is silent: the Money
 * section would compare an asking price against the wrong type's sales and
 * every figure below it would be confidently wrong. The sizes below are the
 * real medians for 8 Boon Keng Road.
 */
import { describe, expect, it } from 'vitest';

import { flatTypeForSize, pricePerSqft, pricePerSqm, sqftOf } from './unit';

// This block's own medians, from its sales.
const SIZES = [
  { flatType: '3 ROOM', medianSqm: 68 },
  { flatType: '4 ROOM', medianSqm: 94 },
  { flatType: '5 ROOM', medianSqm: 109 },
];

describe('the flat type a size implies', () => {
  it('matches a size to the type that measures it here', () => {
    expect(flatTypeForSize(94, SIZES)).toBe('4 ROOM');
    expect(flatTypeForSize(68, SIZES)).toBe('3 ROOM');
    expect(flatTypeForSize(109, SIZES)).toBe('5 ROOM');
  });

  it('allows a few square metres either way', () => {
    // Flats of one type are not all one size; 95 and 92 are both 4-rooms.
    expect(flatTypeForSize(95, SIZES)).toBe('4 ROOM');
    expect(flatTypeForSize(92, SIZES)).toBe('4 ROOM');
  });

  it('judges against this block rather than a national rule', () => {
    // The same label covers different sizes in different estates, so 100 m²
    // is a 4-room where 4-rooms are 104 and a 5-room where they are 99.
    expect(flatTypeForSize(100, [
      { flatType: '4 ROOM', medianSqm: 104 },
      { flatType: '5 ROOM', medianSqm: 122 },
    ])).toBe('4 ROOM');
    expect(flatTypeForSize(100, [
      { flatType: '4 ROOM', medianSqm: 84 },
      { flatType: '5 ROOM', medianSqm: 99 },
    ])).toBe('5 ROOM');
  });

  it('says nothing rather than guessing at an impossible size', () => {
    // A size typed in square feet by mistake, or a stray zero. Picking the
    // nearest type would sound just as certain as a real match.
    expect(flatTypeForSize(1012, SIZES)).toBeNull();
    expect(flatTypeForSize(9, SIZES)).toBeNull();
  });

  it('says nothing when there is no size, rather than picking the first', () => {
    expect(flatTypeForSize(null, SIZES)).toBeNull();
    expect(flatTypeForSize(0, SIZES)).toBeNull();
    expect(flatTypeForSize(Number.NaN, SIZES)).toBeNull();
  });

  it('says nothing when the block has sold nothing to compare with', () => {
    expect(flatTypeForSize(94, [])).toBeNull();
    expect(flatTypeForSize(94, [{ flatType: '4 ROOM', medianSqm: 0 }])).toBeNull();
  });
});

describe('the figures off a size and a price', () => {
  it('converts to the square feet a listing quotes', () => {
    expect(sqftOf(94)).toBeCloseTo(1011.8, 1);
  });

  it('works out the price per square foot', () => {
    // $1,100,000 for 94 m² is 1,011.8 sqft, so a bit over $1,087.
    expect(pricePerSqft(1_100_000, 94)).toBeCloseTo(1087.2, 1);
  });

  it('works out the price per square metre the report compares in', () => {
    expect(pricePerSqm(1_100_000, 94)).toBeCloseTo(11_702.1, 1);
  });

  it('is unknown rather than zero or infinite when either is missing', () => {
    expect(pricePerSqft(null, 94)).toBeNull();
    expect(pricePerSqft(1_100_000, null)).toBeNull();
    expect(pricePerSqft(1_100_000, 0)).toBeNull();
    expect(pricePerSqm(0, 94)).toBeNull();
    expect(sqftOf(null)).toBeNull();
  });
});
