/**
 * Reading figures out of a pasted listing.
 *
 * The fixture is a real 99.co page — 665C Punggol Drive — transcribed from
 * what it actually shows, not from a guess at what a listing looks like. The
 * guess got two things wrong that matter: the space in `S$ 698,888`, and how
 * much money on a listing page is not the asking price.
 *
 * Most of these tests are therefore about what it must refuse. Putting a
 * specific, confident, wrong price in the header is the failure this feature
 * exists to avoid, and a blank beats a number the reader has no reason to
 * doubt.
 */
import { describe, expect, it } from 'vitest';

import {
  extractFlatType,
  extractListing,
  extractPrice,
  extractPsf,
  extractSqm,
  isBareLink,
} from './listing';

/** 665C Punggol Drive, as the page reads top to bottom. */
const PUNGGOL = `
99.co Buy Rent Sell Find Agent New Launches Explore Calculators Commercial Property News List Property
Back Save to Favourites Share
MUST SEE · Curated by 99 · Verified as real
4 Room (4A) HDB for Sale in 665C Punggol Drive
3 Beds · 2 Baths · 1,001 sqft · Built: 2016 · 99 yrs
PE 6 Oasis LRT · 2 mins (86m)
665C Punggol Drive 823665 View on map
Hougang / Punggol / Sengkang (D19)
S$ 698,888 S$ 698 psf NEGO
Est. mortgage S$ 2,222/mo
Track any Property Value [X-Value™]
Call Whatsapp
99 GROUP Get a Free Home Valuation. Find out your property value today!
Shaun Lim PROPNEX REALTY PTE. LTD. CEA: L3008022J | R064045B
+65 8889 xxxx Show
Photos Floorplan Map view Street view
Property details
Price/sqft S$ 698.19 psf
Floor Level High
No. of bedrooms 3
Furnishing Unfurnished
Built year 2016
Tenure 99-year leasehold
Property type HDB 4 Rooms
Last updated 3 hrs ago
https://www.99.co/singapore/sale/property/665c-punggol-drive-hdb-CpscKWcZgHmKU4LwpGvB7X
`;

describe('the Punggol listing, end to end', () => {
  it('reads every figure off it', () => {
    const got = extractListing(PUNGGOL);
    expect(got.price).toBe(698_888);
    expect(got.sqm).toBeCloseTo(93.0, 1); // 1,001 sqft
    expect(got.psf).toBe(698);
    expect(got.flatType).toBe('4 ROOM');
    expect(got.url).toContain('665c-punggol-drive');
  });

  it('picks the price by the listing\'s own arithmetic', () => {
    // 698 psf over 1,001 sqft is 698,698, which is 698,888 to within a
    // rounding — and nowhere near the 2,222 mortgage on the same page.
    expect(extractPrice(PUNGGOL)).toBe(698_888);
  });
});

describe('money that is not the asking price', () => {
  it('is not the per-square-foot beside it', () => {
    expect(extractPrice('S$ 698 psf')).toBeNull();
    expect(extractPrice('Price/sqft S$ 698.19 psf')).toBeNull();
    // Over a thousand it carries a comma and would otherwise qualify.
    expect(extractPrice('S$ 1,250 psf')).toBeNull();
  });

  it('is not the monthly mortgage', () => {
    expect(extractPrice('Est. mortgage S$ 2,222/mo')).toBeNull();
  });

  it('is not the postal code run onto the address', () => {
    // 823665 is six digits sitting in the same paste as the price.
    expect(extractPrice('665C Punggol Drive 823665')).toBeNull();
  });

  it('is not the agent\'s phone number or licence', () => {
    expect(extractPrice('+65 8889 xxxx CEA: L3008022J | R064045B')).toBeNull();
  });

  it('is not a year, a district or a walk', () => {
    expect(extractPrice('Built: 2016 · 99 yrs · (D19) · 2 mins (86m)')).toBeNull();
  });
});

describe('choosing between several prices', () => {
  it('prefers the one the psf and the area agree with', () => {
    // A whole-page copy carries similar listings below, some of them dearer.
    // The largest would be wrong; the arithmetic picks the right one.
    const withNeighbours = `${PUNGGOL}
      Similar listings nearby
      668A Punggol Drive S$ 780,000
      665B Punggol Drive S$ 712,000
    `;
    expect(extractPrice(withNeighbours)).toBe(698_888);
  });

  it('takes the first when there is nothing to check against', () => {
    // No psf and no area, so the arithmetic cannot run. A listing states its
    // own price before it lists anyone else's.
    expect(extractPrice('Asking S$ 650,000. Nearby sold for S$ 780,000.'))
      .toBe(650_000);
  });

  it('refuses a figure too large to be a flat', () => {
    expect(extractPrice('S$ 12,000,000')).toBeNull();
  });

  it('is null when the text holds no money at all', () => {
    expect(extractPrice('665C Punggol Drive, 4 room, high floor')).toBeNull();
    expect(extractPrice('')).toBeNull();
  });
});

describe('the floor area', () => {
  it('converts the square feet 99.co quotes', () => {
    expect(extractSqm('1,001 sqft')).toBeCloseTo(93.0, 1);
    expect(extractSqm('721 sq ft')).toBeCloseTo(67.0, 1);
  });

  it('takes square metres as they are', () => {
    expect(extractSqm('93 sqm')).toBeCloseTo(93, 1);
    expect(extractSqm('94 m²')).toBeCloseTo(94, 1);
  });

  it('is not the walk to the station', () => {
    // "2 mins (86m)" is a distance, and 86 would pass for a floor area.
    expect(extractSqm('PE 6 Oasis LRT · 2 mins (86m)')).toBeNull();
  });

  it('is not the land area of the development', () => {
    expect(extractSqm('Flat 93 sqm on a 15,000 sqm site')).toBeCloseTo(93, 1);
  });

  it('refuses an area no flat has', () => {
    expect(extractSqm('5 sqm')).toBeNull();
    expect(extractSqm('4,000 sqm')).toBeNull();
  });

  it('ignores bare numbers with no unit', () => {
    expect(extractSqm('3 Beds · 2 Baths · Built: 2016')).toBeNull();
  });
});

describe('the flat type', () => {
  it('reads it from the title', () => {
    expect(extractFlatType('4 Room (4A) HDB for Sale in 665C Punggol Drive'))
      .toBe('4 ROOM');
  });

  it('reads the details table spelling too', () => {
    expect(extractFlatType('Property type HDB 4 Rooms')).toBe('4 ROOM');
  });

  it('knows an executive flat', () => {
    expect(extractFlatType('Executive HDB for Sale')).toBe('EXECUTIVE');
  });

  it('is null when nothing says', () => {
    expect(extractFlatType('HDB for Sale in Punggol')).toBeNull();
  });
});

describe('the per-square-foot', () => {
  it('reads it with and without decimals', () => {
    expect(extractPsf('S$ 698 psf')).toBe(698);
    expect(extractPsf('S$ 698.19 psf')).toBeCloseTo(698.19, 2);
    expect(extractPsf('S$ 1,250 psf')).toBe(1250);
  });

  it('is null when the listing does not quote one', () => {
    expect(extractPsf('S$ 698,888')).toBeNull();
  });
});

describe('telling a bare link from a listing', () => {
  it('knows a pasted link', () => {
    expect(isBareLink('https://www.99.co/singapore/sale/property/x')).toBe(true);
    expect(isBareLink('99.co/singapore/sale/property/x')).toBe(true);
    expect(isBareLink('  https://www.propertyguru.com.sg/listing/123  ')).toBe(true);
  });

  it('knows a whole listing is not one', () => {
    expect(isBareLink(PUNGGOL)).toBe(false);
    expect(isBareLink('665C Punggol Drive S$ 698,888')).toBe(false);
  });

  it('is not fooled by an empty paste', () => {
    expect(isBareLink('')).toBe(false);
    expect(isBareLink('   ')).toBe(false);
  });
});
