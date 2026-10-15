/**
 * Which zoning cells a map view has to fetch.
 *
 * This is arithmetic the browser does *before* it has read anything, so it
 * cannot discover it got the cell size wrong — a view would simply ask for
 * files that are not there and draw no zoning at all, silently. Python holds
 * the other half of the agreement in `nhf/site.py` and asserts this file
 * still says the same number; these are the cases on either side of a cell
 * boundary, which is where an off-by-one hides.
 */
import { describe, expect, it } from 'vitest';

import { ZONING_CELL, zoningCellKey, zoningKeysFor } from './data';

describe('the cell a point falls in', () => {
  it('is hundredths of a degree, floored', () => {
    expect(ZONING_CELL).toBe(100);
    // 8 Boon Keng Rd.
    expect(zoningCellKey(1.316424, 103.861501)).toBe('131_10386');
  });

  it('floors rather than rounds, so a point never lands in the next cell', () => {
    expect(zoningCellKey(1.3199, 103.8699)).toBe('131_10386');
    expect(zoningCellKey(1.32, 103.87)).toBe('132_10387');
  });

  it('handles the southern and western edges of the island', () => {
    expect(zoningCellKey(1.2, 103.6)).toBe('120_10360');
  });
});

describe('the cells a view covers', () => {
  it('is one cell when the view sits inside one', () => {
    expect(zoningKeysFor(1.3161, 103.8611, 1.3169, 103.8619)).toEqual([
      '131_10386',
    ]);
  });

  it('picks up every cell a view straddles', () => {
    const keys = zoningKeysFor(1.3195, 103.8695, 1.3205, 103.8705);
    expect(keys.sort()).toEqual(
      ['131_10386', '131_10387', '132_10386', '132_10387'].sort(),
    );
  });

  it('covers a wide view completely, with no gaps', () => {
    const keys = zoningKeysFor(1.31, 103.85, 1.34, 103.88);
    expect(keys).toHaveLength(4 * 4);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toContain('131_10385');
    expect(keys).toContain('133_10387');
  });

  it('asks for nothing rather than for the whole country', () => {
    // Zoomed out far enough, this would be hundreds of requests for files
    // that would be drawn two pixels wide. Better to show no zoning.
    expect(zoningKeysFor(1.2, 103.6, 1.5, 104.1)).toEqual([]);
  });
});
