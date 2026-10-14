/**
 * The TypeScript side of the shared link fixture.
 *
 * `tests/golden/links.json` carries the index shards a real visit would fetch
 * and the answers Python's resolver gives for each URL. The resolver here
 * reads the shards and has to agree — which is what stops the browser quietly
 * resolving a link to a different block from the one the CLI would.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { parseLink, resolveAddress, resolveLink, tokenise } from './resolve';
import type { IndexShard } from './types';

const golden = JSON.parse(
  readFileSync(join(__dirname, '../../../tests/golden/links.json'), 'utf8'),
) as {
  blocks: { block: string; street: string; town: string; slug: string }[];
  index: Record<string, IndexShard>;
  cases: {
    url: string;
    expected: { status: string; slug?: string; candidates?: string[] };
  }[];
};

/** Stands in for `fetch('index/<block>.json')`, 404 and all. */
const fetchShard = async (block: string): Promise<IndexShard | null> =>
  golden.index[block] ?? null;

describe('the shared link fixture', () => {
  it('carries the shard that makes 266B ambiguous', () => {
    const shard = golden.index['266b']!;
    expect(shard.streets).toHaveLength(2);
    expect(shard.streets.map((s) => s.street).sort()).toEqual([
      'COMPASSVALE BOW',
      'PUNGGOL WAY',
    ]);
  });

  for (const testCase of golden.cases) {
    const label = testCase.url || '(an empty link)';
    it(`agrees with Python: ${label.slice(0, 70)}`, async () => {
      const got = await resolveLink(testCase.url, fetchShard);
      expect(got.status).toBe(testCase.expected.status);
      if (testCase.expected.slug) {
        expect(got.slug).toBe(testCase.expected.slug);
      }
      if (testCase.expected.candidates) {
        expect((got.candidates ?? []).map((c) => c.slug).sort()).toEqual(
          testCase.expected.candidates,
        );
      }
    });
  }
});

describe('reading a pasted link', () => {
  it('drops path noise but keeps street words', () => {
    const parse = parseLink(
      'https://www.99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-eSeF',
    );
    expect(parse.tokens[0]).toBe('669C');
    expect(parse.tokens).toContain('WEST');
    expect(parse.tokens).not.toContain('SINGAPORE');
    expect(parse.portal).toBe('99.co');
  });

  it('drops apostrophes rather than splitting on them', () => {
    // So HDB's `ST GEORGE'S RD` and a portal's `st-georges-road` tokenise
    // alike, and `C'WEALTH` survives as one token.
    expect(tokenise("ST GEORGE'S RD")).toEqual(['ST', 'GEORGES', 'RD']);
    expect(tokenise("C'WEALTH AVE WEST")).toEqual(['CWEALTH', 'AVE', 'WEST']);
  });

  it('ignores query strings and fragments', () => {
    const parse = parseLink(
      'https://www.99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-x?utm=1#photos',
    );
    expect(parse.tokens).not.toContain('UTM');
    expect(parse.tokens).not.toContain('PHOTOS');
  });

  it('strips a listing id but not a street number', () => {
    // JURONG WEST ST 64 ends in a number; only five digits or more is an id.
    const parse = parseLink(
      'https://www.propertyguru.com.sg/listing/hdb-for-sale-406-tampines-street-41-25099211',
    );
    expect(parse.tokens[parse.tokens.length - 1]).toBe('41');
  });

  it('recognises a link pasted without its scheme', () => {
    expect(parseLink('propertyguru.com.sg/listing/x').portal).toBe('PropertyGuru');
  });
});

describe('it never guesses', () => {
  it('asks which street when the link carries only a block number', async () => {
    const got = await resolveLink(
      'https://www.propertyguru.com.sg/listing/hdb-for-sale-266b-25099211',
      fetchShard,
    );
    expect(got.status).toBe('ambiguous');
    expect(got.reason).toContain('PUNGGOL WAY');
    expect(got.reason).toContain('COMPASSVALE BOW');
  });

  it('treats a block number nobody has as a 404 and says so', async () => {
    const got = await resolveLink(
      'https://www.99.co/singapore/sale/property/9999-nowhere-street-1-hdb-x',
      fetchShard,
    );
    expect(got.status).toBe('unresolved');
    expect(got.slug).toBeUndefined();
  });

  it('does not read a trailing number as the block', async () => {
    // Block 1 is a real block on LOR 7 TOA PAYOH, and must not be reported
    // just because a 1 appears at the end of the path.
    const got = await resolveLink(
      'https://www.propertyguru.com.sg/listing/hdb-for-sale-999z-nowhere-street-1',
      fetchShard,
    );
    expect(got.reason).not.toContain('TOA PAYOH');
  });

  it('says when a link is from somewhere else entirely', async () => {
    const got = await resolveLink('https://example.com/some/listing/page', fetchShard);
    expect(got.reason).toContain('not a PropertyGuru or 99.co link');
  });

  it('handles an empty link without throwing', async () => {
    const got = await resolveLink('', fetchShard);
    expect(got.status).toBe('unresolved');
    expect(got.reason).toBeTruthy();
  });
});

describe('the typed fallback', () => {
  it('takes the portal spelling', async () => {
    const got = await resolveAddress('669C', 'jurong west street 64', fetchShard);
    expect(got.slug).toBe('669c-jurong-west-st-64');
  });

  it("takes HDB's own spelling", async () => {
    const got = await resolveAddress('669c', 'JURONG WEST ST 64', fetchShard);
    expect(got.slug).toBe('669c-jurong-west-st-64');
  });

  it('lists the streets when the block exists but the street does not match', async () => {
    const got = await resolveAddress('266B', 'nowhere lane', fetchShard);
    expect(got.status).toBe('ambiguous');
    expect(got.reason).toContain('PUNGGOL WAY');
  });

  it('refuses an address HDB does not have', async () => {
    const got = await resolveAddress('999', 'nowhere rd', fetchShard);
    expect(got.status).toBe('unresolved');
    expect(got.reason).toContain('no block 999');
  });
});
