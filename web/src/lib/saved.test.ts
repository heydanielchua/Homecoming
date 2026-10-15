/**
 * Saved units and share links.
 *
 * The rule these exist to protect: **only the inputs are saved, never the
 * report**. A saved unit that carried a fair-value range would quietly go
 * stale, and a stale range is worse than no range — so the shape itself has
 * to stay free of anything computed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { describeUnit, loadSaved, remove, storeSaved, upsert, type UnitDraft } from './saved';
import { titleCase } from './format';
import { readShared, shareLink, shareParams } from './share';
import type { SavedUnit } from './types';

/** A localStorage that behaves, for the tests that need one. */
function fakeStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: () => null,
    length: 0,
  } as unknown as Storage;
}

const draft: UnitDraft = {
  url: 'https://www.99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-x',
  slug: '669c-jurong-west-st-64',
  blockKey: '669C|JURONG WEST ST 64',
  block: '669C',
  street: 'JURONG WEST ST 64',
  town: 'JURONG WEST',
  size: 94,
  flatType: '4 ROOM',
  asking: 620000,
  storey: 12,
  facing: 'W',
  note: 'viewing on Saturday',
};

beforeEach(() => {
  vi.stubGlobal('localStorage', fakeStorage());
});

describe('what gets saved', () => {
  it('keeps the typed inputs and nothing computed', () => {
    const [unit] = upsert([], draft);
    const keys = Object.keys(unit!).sort();
    expect(keys).toEqual([
      'asking', 'block', 'blockKey', 'facing', 'flatType', 'id', 'note',
      'savedAt', 'size', 'slug', 'storey', 'street', 'town', 'url',
    ]);
    // Nothing from the report may ride along: it is rebuilt on open.
    for (const banned of ['fairValue', 'median', 'trend', 'rent', 'sun', 'report']) {
      expect(keys).not.toContain(banned);
    }
  });

  it('replaces the same flat rather than adding a second row', () => {
    const once = upsert([], draft);
    const twice = upsert(once, { ...draft, asking: 600000 });
    expect(twice).toHaveLength(1);
    expect(twice[0]!.asking).toBe(600000);
    expect(twice[0]!.id).toBe(once[0]!.id); // the same unit, corrected
  });

  it('treats a different floor in the same block as a different flat', () => {
    // Two 4-room flats on different floors really are two things to compare.
    const units = upsert(upsert([], draft), { ...draft, storey: 3 });
    expect(units).toHaveLength(2);
  });

  it('treats a different flat type as a different flat', () => {
    const units = upsert(upsert([], draft), { ...draft, flatType: '5 ROOM' });
    expect(units).toHaveLength(2);
  });

  it('removes by id', () => {
    const units = upsert([], draft);
    expect(remove(units, units[0]!.id)).toEqual([]);
  });

  it('describes a unit without a floor honestly', () => {
    const [withFloor] = upsert([], draft);
    const [without] = upsert([], { ...draft, storey: null });
    expect(describeUnit(withFloor!)).toContain('#12');
    expect(describeUnit(without!)).toContain('floor unknown');
  });
});

describe('reading storage back', () => {
  it('round-trips', () => {
    const units = upsert([], draft);
    expect(storeSaved(units)).toBe(true);
    expect(loadSaved()).toEqual(units);
  });

  it('survives a browser that refuses to store anything', () => {
    // A private window, or site data blocked. The list is lost, not the page.
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
    } as unknown as Storage);
    expect(loadSaved()).toEqual([]);
    expect(storeSaved(upsert([], draft))).toBe(false);
  });

  it('survives something that is not JSON', () => {
    localStorage.setItem('listing-check.saved.v1', 'not json at all');
    expect(loadSaved()).toEqual([]);
  });

  it('drops one bad entry rather than the whole list', () => {
    // This data was written by an older version of the page and will be read
    // by a newer one.
    const good = upsert([], draft)[0]!;
    localStorage.setItem(
      'listing-check.saved.v1',
      JSON.stringify([good, { nonsense: true }, null, 'string']),
    );
    const loaded = loadSaved();
    expect(loaded).toHaveLength(1);
    expect(loaded[0]!.slug).toBe(good.slug);
  });

  it('repairs a half-written entry instead of trusting it', () => {
    localStorage.setItem(
      'listing-check.saved.v1',
      JSON.stringify([{ slug: 'x-y-rd', asking: 'lots', storey: -4, facing: 'UP' }]),
    );
    const [unit] = loadSaved();
    expect(unit!.asking).toBeNull();
    expect(unit!.storey).toBeNull();
    expect(unit!.facing).toBeNull();
    expect(unit!.id).toBeTruthy();
  });
});

describe('sharing a unit', () => {
  it('carries what was typed and not the report', () => {
    const params = shareParams(draft);
    expect(params.get('s')).toBe(draft.slug);
    expect(params.get('p')).toBe('620000');
    expect(params.get('f')).toBe('12');
    expect(params.get('d')).toBe('W');
    expect(params.get('a')).toBe('94');
    expect([...params.keys()].sort()).toEqual(
      ['a', 'd', 'f', 'l', 'n', 'p', 's', 't'],
    );
  });

  it('round-trips through a link', () => {
    const link = shareLink(draft, 'https://example.test/');
    const shared = readShared(link.slice(link.indexOf('#')));
    expect(shared).toMatchObject({
      slug: draft.slug,
      flatType: '4 ROOM',
      asking: 620000,
      storey: 12,
      facing: 'W',
      note: 'viewing on Saturday',
    });
  });

  it('leaves out what was not typed', () => {
    const bare: UnitDraft = {
      ...draft, asking: null, storey: null, facing: null, size: null,
      note: '', url: '',
    };
    expect([...shareParams(bare).keys()]).toEqual(['s', 't']);
    const shared = readShared(`#${shareParams(bare).toString()}`);
    expect(shared?.asking).toBeNull();
    expect(shared?.storey).toBeNull();
    expect(shared?.facing).toBeNull();
  });

  it('is nothing when there is no unit in the link', () => {
    expect(readShared('')).toBeNull();
    expect(readShared('#')).toBeNull();
    expect(readShared('#p=620000')).toBeNull(); // a price with no block
  });

  it('refuses a facing that is not a compass point', () => {
    expect(readShared('#s=x-y-rd&d=SIDEWAYS')?.facing).toBeNull();
  });

  it('refuses a negative or nonsense price', () => {
    expect(readShared('#s=x-y-rd&p=-5')?.asking).toBeNull();
    expect(readShared('#s=x-y-rd&p=lots')?.asking).toBeNull();
    expect(readShared('#s=x-y-rd&f=0')?.storey).toBeNull();
  });
});

describe('a saved unit is not a report', () => {
  it('has no field whose value could go stale', () => {
    // The whole reason the report is recomputed on open.
    const [unit] = upsert([], draft);
    const serialised = JSON.stringify(unit);
    for (const stale of ['"c":"fact"', 'fair_value', 'median_psm', 'n_comps']) {
      expect(serialised).not.toContain(stale);
    }
  });

  it('keeps the link, so the listing can be opened again', () => {
    const [unit] = upsert([], draft) as SavedUnit[];
    expect(unit!.url).toContain('99.co');
  });
});


describe('reading HDB\'s shouting back as words', () => {
  it('title-cases a town and a street', () => {
    expect(titleCase('ANG MO KIO')).toBe('Ang Mo Kio');
    expect(titleCase('LOR 1 TOA PAYOH')).toBe('Lor 1 Toa Payoh');
  });

  it('leaves the letter after an apostrophe alone', () => {
    // Every HDB street name with an apostrophe wants this, and the obvious
    // word-boundary class produced "St George'S Lane".
    expect(titleCase("ST GEORGE'S LANE")).toBe("St George's Lane");
    expect(titleCase("QUEEN'S RD")).toBe("Queen's Rd");
    expect(titleCase("C'WEALTH AVE WEST")).toBe("C'wealth Ave West");
  });

  it('capitalises after a bracket, a slash and a hyphen', () => {
    expect(titleCase('KALLANG/WHAMPOA')).toBe('Kallang/Whampoa');
    expect(titleCase('TOA PAYOH (WEST)')).toBe('Toa Payoh (West)');
    expect(titleCase('BUKIT-TIMAH')).toBe('Bukit-Timah');
  });

  it('is nothing for nothing', () => {
    expect(titleCase(null)).toBe('');
    expect(titleCase('')).toBe('');
  });
});
