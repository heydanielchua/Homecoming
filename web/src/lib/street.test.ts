/**
 * Writing HDB's street abbreviations out in full.
 *
 * The fixtures are real slugs taken from the shipped building data, not
 * invented ones — including the two that collide: `yishun-st-11` where `st`
 * is Street and `st-george-s-rd` where it is Saint and the lone `s` is a
 * possessive.
 */
import { describe, expect, it } from 'vitest';

import { expandStreet, streetOf, streetSlugOf } from './street';

describe('writing a street slug out in full', () => {
  it('expands the abbreviations HDB actually uses', () => {
    expect(expandStreet('punggol-dr')).toBe('Punggol Drive');
    expect(expandStreet('bedok-sth-rd')).toBe('Bedok South Road');
    expect(expandStreet('yishun-ave-5')).toBe('Yishun Avenue 5');
    expect(expandStreet('northshore-cres')).toBe('Northshore Crescent');
    expect(expandStreet('yishun-ctrl')).toBe('Yishun Central');
    expect(expandStreet('jln-bt-merah')).toBe('Jalan Bukit Merah');
    expect(expandStreet('upp-serangoon-rd')).toBe('Upper Serangoon Road');
    expect(expandStreet('lor-1-toa-payoh')).toBe('Lorong 1 Toa Payoh');
  });

  it('knows Street from Saint by where it sits', () => {
    // Both are real. Only the position tells them apart.
    expect(expandStreet('yishun-st-11')).toBe('Yishun Street 11');
    expect(expandStreet('bedok-nth-st-1')).toBe('Bedok North Street 1');
    expect(expandStreet('st-george-s-rd')).toBe("St George's Road");
    expect(expandStreet('st-george-s-lane')).toBe("St George's Lane");
  });

  it('leaves a name it does not recognise as it was written', () => {
    // Guessing at a Malay or Chinese street name would do real damage.
    expect(expandStreet('chai-chee-rd')).toBe('Chai Chee Road');
    expect(expandStreet('choa-chu-kang-ave-1')).toBe('Choa Chu Kang Avenue 1');
    expect(expandStreet('senja-link')).toBe('Senja Link');
    expect(expandStreet('tah-ching-rd')).toBe('Tah Ching Road');
  });

  it('keeps a block number in the name as it is', () => {
    expect(expandStreet('tampines-st-83')).toBe('Tampines Street 83');
    expect(expandStreet('yishun-ring-rd')).toBe('Yishun Ring Road');
  });
});

describe('taking the street off a building slug', () => {
  it('strips the block from the front', () => {
    expect(streetSlugOf('665c-punggol-dr', '665C')).toBe('punggol-dr');
    expect(streetOf('665c-punggol-dr', '665C')).toBe('Punggol Drive');
    expect(streetOf('1-jln-bt-merah', '1')).toBe('Jalan Bukit Merah');
    expect(streetOf('109a-depot-rd', '109A')).toBe('Depot Road');
  });

  it('is null when the slug and the block disagree', () => {
    // An address assembled out of a mismatch is what ends up printed on a map.
    expect(streetSlugOf('665c-punggol-dr', '123A')).toBeNull();
    expect(streetOf('665c-punggol-dr', '')).toBeNull();
    expect(streetOf('', '665C')).toBeNull();
  });

  it('is null when the slug is nothing but the block', () => {
    expect(streetSlugOf('665c', '665C')).toBeNull();
  });
});
