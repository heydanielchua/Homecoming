/**
 * Turning a pasted portal link into one block, in the browser.
 *
 * The matching itself is a dictionary lookup against `index/<block>.json`,
 * which `nhf site` wrote. What is here is only the part that cannot be
 * precomputed: reducing a pasted URL to the same shape the index was built
 * in, and deciding what to do when it matches nothing.
 *
 * The lists below are the one thing that has to agree with `nhf/listing.py`,
 * and `tests/golden/links.json` is what holds them to it — Python writes the
 * answers, `resolve.test.ts` reads them, and a change to either side without
 * the other fails a test instead of quietly resolving to a different block.
 *
 * Nothing here fetches the listing page. Scraping the portals is against
 * their terms and is not needed: the address is in the path.
 */

import type { IndexShard, IndexStreet } from './types';

/**
 * Path and slug noise. None of these is a word in any HDB street name, which
 * is checked against the real 632 street names on the Python side.
 */
const JUNK = new Set([
  'www', 'com', 'sg', 'en', 'http', 'https',
  'singapore', 'property', 'properties', 'listing', 'listings', 'project',
  'sale', 'sales', 'rent', 'rental', 'buy', 'hdb', 'for', 'resale',
  'flat', 'flats', 'apartment', 'condo', 'blk', 'block', 'd',
]);

/** Up to four digits and an optional letter: 1, 101, 669C, 266B. */
const BLOCK_TOKEN = /^\d{1,4}[A-Z]?$/;

/**
 * A listing id. Street names carry numbers too — JURONG WEST ST 64,
 * TAMPINES ST 86 — but never five digits of one, so the length is what
 * separates a street number from an id.
 */
const LISTING_ID = /^\d{5,}$/;

const PORTALS: Record<string, string> = {
  '99.co': '99.co',
  'propertyguru.com.sg': 'PropertyGuru',
};

export type ResolutionStatus = 'resolved' | 'ambiguous' | 'unresolved';

export interface Resolution {
  status: ResolutionStatus;
  slug?: string;
  street?: IndexStreet;
  candidates?: IndexStreet[];
  portal?: string;
  matchedAlias?: string;
  reason?: string;
}

export interface LinkParse {
  tokens: string[];
  blockCandidates: number[];
  portal?: string;
  host?: string;
}

/**
 * Split anything into upper-case word tokens.
 *
 * Apostrophes are dropped rather than turned into separators, so HDB's
 * `ST GEORGE'S RD` and a portal's `st-georges-road` tokenise alike.
 */
export function tokenise(text: string): string[] {
  return text
    .replace(/['’]/g, '')
    .toUpperCase()
    .split(/[^A-Z0-9]+/)
    .filter(Boolean);
}

/** Reduce a pasted link to tokens, and note where a block could be. */
export function parseLink(url: string): LinkParse {
  const text = (url ?? '').trim();
  let host = '';
  const withScheme = text.match(/^(?:[a-z][a-z0-9+.-]*:)?\/\/([^/?#]+)/i);
  if (withScheme?.[1]) {
    host = withScheme[1].toLowerCase();
  } else if (/^[\w.-]+\.(?:com|co|sg|net)/i.test(text)) {
    host = text.split(/[/?#]/)[0]!.toLowerCase();
  }

  let portal: string | undefined;
  for (const [domain, name] of Object.entries(PORTALS)) {
    if (host === domain || host.endsWith(`.${domain}`)) {
      portal = name;
      break;
    }
  }

  // Query strings and fragments carry tracking, not addresses.
  let path = text.split(/[?#]/)[0] ?? '';
  if (host && path.includes(host)) {
    path = path.slice(path.indexOf(host) + host.length);
  }

  const tokens = tokenise(path).filter((t) => !JUNK.has(t.toLowerCase()));
  // A trailing listing id is noise too, but only at the end: a five-digit
  // number in the middle of a path is some portal's internal id and the block
  // has not been seen yet.
  while (tokens.length > 0 && LISTING_ID.test(tokens[tokens.length - 1]!)) {
    tokens.pop();
  }

  const blockCandidates: number[] = [];
  tokens.forEach((token, i) => {
    if (BLOCK_TOKEN.test(token)) blockCandidates.push(i);
  });

  return { tokens, blockCandidates, portal, host: host || undefined };
}

/** Fetches `index/<block>.json`, or null when there is no such block. */
export type ShardFetcher = (blockNumber: string) => Promise<IndexShard | null>;

/**
 * Every block a token list could mean, longest street match first.
 *
 * Two things vary and both are tried. The block could be any token that looks
 * like one, because a slug is not guaranteed to put the address first. And
 * the tokens after it can run past the end of the street into a listing id or
 * a portal's trailing hash, so tails are dropped one at a time — longest
 * first, since a longer exact match is the more specific reading of the same
 * link.
 */
async function matchTokens(
  parse: LinkParse,
  fetchShard: ShardFetcher,
): Promise<{ found: IndexStreet[]; alias?: string; shards: Map<string, IndexShard> }> {
  const found: IndexStreet[] = [];
  const shards = new Map<string, IndexShard>();
  let alias: string | undefined;

  for (const position of parse.blockCandidates) {
    const number = parse.tokens[position]!.toLowerCase();
    let shard = shards.get(number);
    if (!shard) {
      const fetched = await fetchShard(number);
      if (!fetched) continue; // a 404: no block of that number anywhere
      shard = fetched;
      shards.set(number, shard);
    }

    const tail = parse.tokens.slice(position + 1).map((t) => t.toLowerCase());
    let settled = false;
    for (let end = tail.length; end > 0 && !settled; end -= 1) {
      const candidate = tail.slice(0, end).join('-');
      for (const street of shard.streets) {
        if (street.aliases.includes(candidate)) {
          if (!found.some((s) => s.slug === street.slug)) found.push(street);
          if (alias === undefined) alias = `${number}-${candidate}`;
          settled = true;
          break;
        }
      }
    }
  }
  return { found, alias, shards };
}

function describeStreets(streets: IndexStreet[]): string {
  const names = [...new Set(streets.map((s) => s.street))].sort();
  const listed = names.slice(0, 8).join(', ');
  return names.length > 8
    ? `${listed}, and ${names.length - 8} other streets`
    : listed;
}

/** A pasted link, resolved to one block or refused with a reason. */
export async function resolveLink(
  url: string,
  fetchShard: ShardFetcher,
): Promise<Resolution> {
  const parse = parseLink(url);
  const portal = parse.portal;

  if (parse.tokens.length === 0) {
    return {
      status: 'unresolved',
      portal,
      reason:
        'that link has no address in it — nothing in the path looks like a ' +
        'block and a street',
    };
  }

  const { found, alias, shards } = await matchTokens(parse, fetchShard);

  if (found.length === 1) {
    const street = found[0]!;
    return {
      status: 'resolved',
      slug: street.slug,
      street,
      portal,
      matchedAlias: alias,
    };
  }

  if (found.length > 1) {
    return {
      status: 'ambiguous',
      candidates: found,
      portal,
      reason:
        'that link reads as more than one block: ' +
        found.map((s) => `${parse.tokens[0]} ${s.street}`).join(', ') +
        '. Which one is it?',
    };
  }

  // Nothing matched. A bare block number is the common case and deserves a
  // better answer than "no", because 266B really is two different blocks.
  //
  // Only the first number that looks like a block is considered. A listing
  // slug puts the block before its street, so every later one is a street
  // number or part of an id — and without that rule a link ending
  // `...-nowhere-street-1-hdb-x` reports on block 1, which is a real block on
  // 28 different streets, instead of admitting the link did not resolve.
  for (const position of parse.blockCandidates.slice(0, 1)) {
    const number = parse.tokens[position]!.toLowerCase();
    const shard = shards.get(number) ?? (await fetchShard(number));
    if (!shard || shard.streets.length === 0) continue;
    const listed = describeStreets(shard.streets);
    return {
      status: 'ambiguous',
      candidates: shard.streets,
      portal,
      reason:
        shard.streets.length > 1
          ? `block ${number.toUpperCase()} exists, but the street in that link ` +
            `does not match any of its spellings. HDB has a block ` +
            `${number.toUpperCase()} on: ${listed}. Which street is it?`
          : `block ${number.toUpperCase()} exists — on ${listed} — but the ` +
            `street in that link does not match how HDB spells it. Is that ` +
            `the right block?`,
    };
  }

  const hint = portal
    ? ''
    : ' That is not a PropertyGuru or 99.co link, and other sites do not put ' +
      'the address in the path.';
  return {
    status: 'unresolved',
    portal,
    reason: `no HDB block matches that link.${hint} Type the block and street instead.`,
  };
}

/** The typed fallback: a block and a street, in any spelling. */
export async function resolveAddress(
  block: string,
  street: string,
  fetchShard: ShardFetcher,
): Promise<Resolution> {
  const tokens = tokenise(`${block} ${street}`).filter(
    (t) => !JUNK.has(t.toLowerCase()),
  );
  const blockCandidates: number[] = [];
  tokens.forEach((token, i) => {
    if (BLOCK_TOKEN.test(token)) blockCandidates.push(i);
  });

  const { found, alias, shards } = await matchTokens(
    { tokens, blockCandidates },
    fetchShard,
  );
  if (found.length === 1) {
    return { status: 'resolved', slug: found[0]!.slug, street: found[0]!, matchedAlias: alias };
  }
  if (found.length > 1) {
    return {
      status: 'ambiguous',
      candidates: found,
      reason:
        'that block and street read as more than one block: ' +
        found.map((s) => s.street).join(', '),
    };
  }

  const first = blockCandidates[0];
  const shard =
    first === undefined
      ? null
      : shards.get(tokens[first]!.toLowerCase()) ??
        (await fetchShard(tokens[first]!.toLowerCase()));
  if (shard && shard.streets.length > 0) {
    return {
      status: 'ambiguous',
      candidates: shard.streets,
      reason:
        `HDB has a block ${shard.block} on: ${describeStreets(shard.streets)}. ` +
        'None of them is spelled the way that was typed.',
    };
  }
  return {
    status: 'unresolved',
    reason:
      `HDB has no block ${block.toUpperCase()} on ${street.toUpperCase()}. ` +
      "Check the spelling against the block's own address.",
  };
}
