/**
 * Fetching the static data, and caching it for the session.
 *
 * Every one of these is a plain file on the host — `nhf site` wrote them —
 * so there is no API, no key and nothing to go down. The caching is per page
 * load and in memory only: the point of recomputing a report on open is that
 * it is today's report, and a cache that outlived the page would quietly
 * undo that.
 *
 * A 404 is a real answer here rather than an error. `index/9999.json` not
 * existing *is* the fact that HDB has no block 9999, which is why
 * `fetchShard` returns null for it instead of throwing.
 */

import type {
  BlockReport,
  IndexShard,
  Manifest,
  Meta,
  TownReport,
} from './types';

/**
 * Where the data sits relative to the page.
 *
 * Relative, so the same build serves from a domain root and from a project
 * path on GitHub Pages without being rebuilt for either.
 */
const DATA_ROOT = 'data';

class Cache<T> {
  private readonly entries = new Map<string, Promise<T | null>>();

  constructor(private readonly load: (key: string) => Promise<T | null>) {}

  get(key: string): Promise<T | null> {
    let entry = this.entries.get(key);
    if (!entry) {
      entry = this.load(key);
      this.entries.set(key, entry);
    }
    return entry;
  }
}

async function getJson<T>(path: string): Promise<T | null> {
  const response = await fetch(`${DATA_ROOT}/${path}`, {
    headers: { Accept: 'application/json' },
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(
      `${path} came back ${response.status}. The data directory may not have ` +
        'been built — run `nhf site` and deploy its output next to the page.',
    );
  }
  return (await response.json()) as T;
}

const shards = new Cache<IndexShard>((block) =>
  getJson<IndexShard>(`index/${block}.json`),
);
const blocks = new Cache<BlockReport>((slug) =>
  getJson<BlockReport>(`blocks/${slug}.json`),
);
const towns = new Cache<TownReport>((slug) =>
  getJson<TownReport>(`towns/${slug}.json`),
);

let metaOnce: Promise<Meta | null> | null = null;
let manifestOnce: Promise<Manifest | null> | null = null;

/** The index shard for one block number, or null if there is no such block. */
export function fetchShard(block: string): Promise<IndexShard | null> {
  return shards.get(block.toLowerCase());
}

export function fetchBlock(slug: string): Promise<BlockReport | null> {
  return blocks.get(slug);
}

export function fetchTown(slug: string | null): Promise<TownReport | null> {
  return slug ? towns.get(slug) : Promise.resolve(null);
}

/** The national index, the fitted curves and the sun. Fetched once. */
export function fetchMeta(): Promise<Meta | null> {
  metaOnce ??= getJson<Meta>('meta.json');
  return metaOnce;
}

/** What was built, and how old each dataset behind it is. */
export function fetchManifest(): Promise<Manifest | null> {
  manifestOnce ??= getJson<Manifest>('manifest.json');
  return manifestOnce;
}

/**
 * How stale the data is, in days.
 *
 * Shown because a report built on a six-month-old download is a different
 * claim from one built this morning, and the only way to know which is to
 * say.
 */
export function ageInDays(builtAt: string | null | undefined): number | null {
  if (!builtAt) return null;
  const then = Date.parse(builtAt);
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 86_400_000);
}
