/**
 * The street a block is addressed on, read back out of its slug.
 *
 * The Master Plan labels every road polygon "Road" and nothing else — it
 * zones land, it does not name streets — so the model had roads with no
 * names on them, which is most of what orients somebody looking at an estate
 * from above. The names do exist in what the page already ships: every
 * building carries a slug like `665c-punggol-dr`, built from HDB's own block
 * and street, so the street is in there behind HDB's abbreviations.
 *
 * Expanding them is the whole job, and it is finicky because HDB's
 * shortenings collide with ordinary words. `St` is Street in `yishun-st-11`
 * and Saint in `st-george-s-rd`; a lone `s` is a possessive and belongs on
 * the end of the word before it. Getting one of these wrong puts a wrong
 * name on a real road, which is worse than leaving it blank, so the rules
 * below are positional rather than a flat dictionary, and anything unknown
 * is left as it was written rather than guessed at.
 */

/**
 * HDB's abbreviations, as they appear in a slug.
 *
 * `st` is deliberately absent: it depends on where it sits and is handled
 * in the walk below.
 */
const WORDS: Record<string, string> = {
  rd: 'Road',
  dr: 'Drive',
  ave: 'Avenue',
  cres: 'Crescent',
  ctrl: 'Central',
  cl: 'Close',
  pl: 'Place',
  ter: 'Terrace',
  jln: 'Jalan',
  lor: 'Lorong',
  bt: 'Bukit',
  upp: 'Upper',
  nth: 'North',
  sth: 'South',
  pk: 'Park',
  gdns: 'Gardens',
  hts: 'Heights',
  mkt: 'Market',
  sq: 'Square',
  blvd: 'Boulevard',
  cmwlth: 'Commonwealth',
  tg: 'Tanjong',
  kg: 'Kampong',
};

/** Words that stay lower case inside a name, the way a street sign writes them. */
const SMALL = new Set(['of', 'the', 'and']);

const capitalise = (word: string) =>
  word ? word[0]!.toUpperCase() + word.slice(1) : word;

/**
 * A street slug written out in full: `yishun-st-11` becomes
 * `Yishun Street 11`.
 */
export function expandStreet(slug: string): string {
  const parts = (slug ?? '').split('-').filter(Boolean);
  const out: string[] = [];

  parts.forEach((part, index) => {
    // A lone `s` is the possessive in `st-george-s-rd`, and belongs on the
    // word before it rather than standing as a word of its own.
    if (part === 's' && out.length > 0) {
      out[out.length - 1] += "'s";
      return;
    }

    if (part === 'st') {
      // Saint when it opens the name, Street anywhere else. `St George's
      // Road` and `Yishun Street 11` are both real, and nothing but the
      // position tells them apart.
      out.push(index === 0 ? 'St' : 'Street');
      return;
    }

    const known = WORDS[part];
    if (known) {
      out.push(known);
      return;
    }

    // A number stays a number, and anything unrecognised is capitalised as
    // written: a Malay or Chinese street name is not an abbreviation and
    // guessing at it would do damage.
    out.push(/^\d+[a-z]?$/.test(part) ? part.toUpperCase() : capitalise(part));
  });

  return out
    .map((word, index) =>
      index > 0 && SMALL.has(word.toLowerCase()) ? word.toLowerCase() : word,
    )
    .join(' ');
}

/**
 * The street part of a building's slug, or null.
 *
 * A slug is the block and the street joined, and the block is known, so the
 * street is what is left once the block is taken off the front. When the
 * slug does not start with the block the two did not come from the same
 * place and nothing is returned — an address assembled out of a mismatch is
 * exactly the kind of thing that ends up printed on a map.
 */
export function streetSlugOf(slug: string, block: string): string | null {
  const whole = (slug ?? '').toLowerCase();
  const prefix = `${(block ?? '').toLowerCase().replace(/\s+/g, '-')}-`;
  if (!whole || !block || !whole.startsWith(prefix)) return null;
  const street = whole.slice(prefix.length);
  return street || null;
}

/** The full street name for a building, or null. */
export function streetOf(slug: string, block: string): string | null {
  const part = streetSlugOf(slug, block);
  return part ? expandStreet(part) : null;
}
