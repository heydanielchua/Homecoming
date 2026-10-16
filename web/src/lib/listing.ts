/**
 * Reading a listing's own figures out of text pasted from the portal.
 *
 * The page cannot fetch a listing. It is static files on GitHub Pages with no
 * server behind them, and a browser is refused the portals' pages by their
 * CORS policy, so the only thing that ever reaches this code is what somebody
 * pasted. The URL carries an address and an opaque id and no price at all,
 * which is why the asking price had to be typed. It does not have to be:
 * whoever copies the link is looking at the price while they do it.
 *
 * Everything here is written against a real 99.co listing rather than a guess
 * at one, which matters because the guess was wrong in two ways. A price is
 * written `S$ 698,888`, with a space after the S$. And a listing is full of
 * money that is not the asking price: `S$ 698 psf` beside it, `Est. mortgage
 * S$ 2,222/mo` below it, `S$ 698.19 psf` again in the details table, a postal
 * code `823665` run onto the end of the address, and an agent's `+65 8889`.
 *
 * So the asking price is not simply the first or the largest figure. The page
 * states its own arithmetic — a price, a per-square-foot and a floor area —
 * and 698 × 1,001 is 698,698, which is 698,888 to within a rounding. That
 * cross-check is what picks the price out, and it is also what makes this
 * safe to trust: a figure that agrees with the other two was not a guess.
 */

export interface ListingFigures {
  /** Asking price in dollars, or null if nothing in the text was convincing. */
  price: number | null;
  /** Floor area in square metres, converted from feet when that is what it said. */
  sqm: number | null;
  /** The quoted per-square-foot, which is how the price gets checked. */
  psf: number | null;
  /** "4 ROOM" and the like, in HDB's spelling, ready to match a flat type. */
  flatType: string | null;
  /** A portal URL found in the text, so one paste can do both jobs. */
  url: string | null;
}

export const SQFT_PER_SQM = 10.7639;

/**
 * The narrowest and widest a resale flat can plausibly be asked for. Below
 * the floor sit the monthly mortgage and the per-square-foot; above the
 * ceiling sit the numbers that are not money at all.
 */
const MIN_PRICE = 100_000;
const MAX_PRICE = 5_000_000;

/** The range an HDB flat's floor area falls in, in square metres. */
const MIN_SQM = 25;
const MAX_SQM = 300;

/**
 * Money as a portal writes it. The thousands grouping is required: it is what
 * separates `S$ 698,888` from the postal code in `665C Punggol Drive 823665`
 * and from `+65 8889 xxxx`, neither of which is money and both of which are
 * six digits sitting in the same paste.
 */
const MONEY = /(?:S\$|\$)\s*(\d{1,3}(?:,\d{3})+)(?!\s*(?:psf|per\s*sq|\/\s*sq|\/mo|\s*\/\s*mo))/gi;

/**
 * The per-square-foot. Written without grouping under a thousand — `S$ 698
 * psf` — and with decimals in the details table — `S$ 698.19 psf`.
 */
const PSF = /(?:S\$|\$)\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*psf/i;

/** An area with its unit attached. 99.co quotes feet; HDB publishes metres. */
const AREA =
  /(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|square\s*feet|sq\.?\s*m\b|sqm|square\s*met(?:re|er)s?|m²)/gi;

/**
 * The flat type. 99.co writes it twice and differently: "4 Room (4A) HDB for
 * Sale" in the title and "HDB 4 Rooms" in the details, and the bracketed 4A
 * is the model, not another number of rooms.
 */
const FLAT_TYPE = /\b([1-5])\s*[- ]?\s*Rooms?\b/i;
const EXECUTIVE = /\bExecutive\b/i;

const URL_IN_TEXT = /https?:\/\/[^\s"'<>]+/i;

function numbersOf(text: string, pattern: RegExp): number[] {
  const out: number[] = [];
  for (const match of text.matchAll(pattern)) {
    const value = Number(match[1]!.replace(/,/g, ''));
    if (Number.isFinite(value)) out.push(value);
  }
  return out;
}

/** The quoted per-square-foot, or null. */
export function extractPsf(text: string): number | null {
  const match = (text ?? '').match(PSF);
  if (!match) return null;
  const value = Number(match[1]!.replace(/,/g, ''));
  return Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * The floor area in square metres, or null.
 *
 * Takes the smallest convincing figure. Where a page quotes more than one
 * area the flat is the small one; the larger is the site or the development.
 */
export function extractSqm(text: string): number | null {
  const found: number[] = [];
  for (const match of (text ?? '').matchAll(AREA)) {
    const value = Number(match[1]!.replace(/,/g, ''));
    if (!Number.isFinite(value) || value <= 0) continue;
    const feet = /f/i.test(match[2]!);
    const sqm = feet ? value / SQFT_PER_SQM : value;
    if (sqm >= MIN_SQM && sqm <= MAX_SQM) found.push(sqm);
  }
  if (found.length === 0) return null;
  return Math.round(Math.min(...found) * 10) / 10;
}

/**
 * The asking price, or null.
 *
 * Where the listing quotes a per-square-foot and an area it states its own
 * arithmetic, and the price is whichever candidate agrees with it: 698 psf
 * over 1,001 sqft is 698,698, which picks 698,888 out of a page that also
 * contains 2,222. Within a tenth is as close as that product gets, because
 * the quoted psf is itself rounded.
 *
 * With nothing to check against it takes the first candidate rather than the
 * largest. A listing states its price at the top and a copy of the whole page
 * carries similar listings below it, some of them dearer.
 */
export function extractPrice(text: string): number | null {
  const source = text ?? '';
  const candidates = numbersOf(source, MONEY).filter(
    (value) => value >= MIN_PRICE && value <= MAX_PRICE,
  );
  if (candidates.length === 0) return null;

  const psf = extractPsf(source);
  const sqm = extractSqm(source);
  if (psf !== null && sqm !== null) {
    const expected = psf * sqm * SQFT_PER_SQM;
    let best: number | null = null;
    let bestGap = Infinity;
    for (const value of candidates) {
      const gap = Math.abs(value - expected) / expected;
      if (gap < bestGap) {
        bestGap = gap;
        best = value;
      }
    }
    if (best !== null && bestGap <= 0.1) return best;
  }
  return candidates[0]!;
}

/** The flat type in HDB's spelling, or null. */
export function extractFlatType(text: string): string | null {
  const source = text ?? '';
  const rooms = source.match(FLAT_TYPE);
  if (rooms) return `${rooms[1]} ROOM`;
  return EXECUTIVE.test(source) ? 'EXECUTIVE' : null;
}

export function extractUrl(text: string): string | null {
  return (text ?? '').match(URL_IN_TEXT)?.[0] ?? null;
}

/** Everything worth having from a pasted listing. */
export function extractListing(text: string): ListingFigures {
  const source = text ?? '';
  return {
    price: extractPrice(source),
    sqm: extractSqm(source),
    psf: extractPsf(source),
    flatType: extractFlatType(source),
    url: extractUrl(source),
  };
}

/**
 * Whether a paste is a bare link rather than a listing worth reading.
 *
 * A link pasted on its own is the common case and must stay fast and silent;
 * only something with more in it than a URL is searched for figures.
 */
export function isBareLink(text: string): boolean {
  const trimmed = (text ?? '').trim();
  if (!trimmed || /\s/.test(trimmed)) return false;
  return /^(?:https?:\/\/)?[\w.-]+\.[a-z]{2,}/i.test(trimmed);
}

/**
 * The listing's own "Property details" table.
 *
 * The page works off HDB's figures — what sold in this block, at what size,
 * on what floor — and those are facts about the building, not about the unit
 * on sale. They disagree with the listing more often than you would think.
 * HDB's completion year is the block's; the listing quotes the project's.
 * HDB's floor area is the last comparable sale's; the listing quotes this
 * flat's, which may be strata and may include a void. Neither is wrong, and
 * silently showing one where somebody is reading the other is what makes the
 * page look broken against the tab it was copied from.
 *
 * So the listing's own table is kept as the listing wrote it, down to the
 * wording, and shown beside ours rather than folded into it.
 */
export interface ListingDetails {
  /** The quoted per-square-foot, as written: `S$ 698.19 psf`. */
  priceSqft: string | null;
  /** `Low`, `Mid` or `High` — both portals use the same three. */
  floorLevel: string | null;
  bedrooms: number | null;
  bathrooms: number | null;
  /** `Unfurnished`, `Partially Furnished`, `Fully Furnished`. */
  furnishing: string | null;
  /** 99.co's `Built year`; PropertyGuru writes the same thing as `TOP`. */
  builtYear: number | null;
  /** `99-year leasehold`, `Freehold`, and the rest, as written. */
  tenure: string | null;
  /** 99.co's `HDB 4 Rooms`; PropertyGuru's `HDB Flat`. */
  propertyType: string | null;
  /** The HDB flat model — `Model A`, `Improved`, `Premium Apartment`. */
  model: string | null;
  /** Floor area in square feet, which is the unit both portals quote in. */
  floorAreaSqft: number | null;
  /** `3 hrs ago`, `2 days ago` — how stale the listing says it is. */
  lastUpdated: string | null;
}

export const NO_DETAILS: ListingDetails = {
  priceSqft: null,
  floorLevel: null,
  bedrooms: null,
  bathrooms: null,
  furnishing: null,
  builtYear: null,
  tenure: null,
  propertyType: null,
  model: null,
  floorAreaSqft: null,
  lastUpdated: null,
};

/**
 * The value a labelled row carries.
 *
 * A details table copies either as `Built year 2016` on one line or as the
 * label and the value on two, depending on the browser and on how much of
 * the page was selected, so both are read. The value stops at the next
 * label, because a whole table copied onto a single line would otherwise
 * swallow the rest of itself.
 */
function rowValue(text: string, labels: string[], stops: string[]): string | null {
  for (const label of labels) {
    const at = new RegExp(
      `${label}\\s*:?\\s*(.*)`,
      'i',
    );
    for (const line of text.split('\n')) {
      const hit = line.match(at);
      if (!hit) continue;
      let value = hit[1]!.trim();
      if (value) {
        // Trim anything that is plainly the next label of the table.
        for (const stop of stops) {
          const cut = value.search(new RegExp(`\\s*\\b${stop}\\b`, 'i'));
          if (cut > 0) value = value.slice(0, cut).trim();
        }
        if (value) return value;
      }
      // The label sat alone on its line, so the value is the next one.
      const lines = text.split('\n');
      const index = lines.indexOf(line);
      const next = lines.slice(index + 1).find((l) => l.trim());
      if (next) return next.trim();
    }
  }
  return null;
}

/**
 * The labels that end the row before them, so one row cannot eat the next.
 *
 * `PSF` and `TOP` are searched for as labels but never used as stops: they
 * are also an ordinary unit and an ordinary word, and `S$ 698.19 psf` would
 * lose its unit to the first and a `TOP` floor to the second.
 */
const DETAIL_STOPS = [
  'Price/sqft',
  'Price per sqft',
  'Floor Level',
  'Floor level',
  'No\\. of bedrooms',
  'Bedrooms',
  'No\\. of bathrooms',
  'Bathrooms',
  'Furnishing',
  'Built year',
  'Completion year',
  'Tenure',
  'Property type',
  'Property Type',
  'Flat model',
  'Floor area',
  'Floor Area',
  'Last updated',
  'Listing ID',
];

const toInt = (value: string | null): number | null => {
  if (value === null) return null;
  const n = Number(value.replace(/[^\d]/g, ''));
  return Number.isFinite(n) && n > 0 ? n : null;
};

/** Reads the listing's details table out of pasted text. */
export function extractDetails(text: string): ListingDetails {
  const source = text ?? '';
  const row = (...labels: string[]) => rowValue(source, labels, DETAIL_STOPS);

  const builtYear = toInt(row('Built year', 'Completion year', 'TOP'));
  const area = extractSqm(source);

  return {
    priceSqft: row('Price/sqft', 'Price per sqft', 'PSF'),
    floorLevel: row('Floor Level', 'Floor level'),
    bedrooms: toInt(row('No\\. of bedrooms', 'Bedrooms')),
    bathrooms: toInt(row('No\\. of bathrooms', 'Bathrooms')),
    furnishing: row('Furnishing'),
    // A year, not a lease length or a storey count.
    builtYear: builtYear !== null && builtYear >= 1960 && builtYear <= 2040 ? builtYear : null,
    tenure: row('Tenure'),
    propertyType: row('Property type', 'Property Type'),
    model: row('Model', 'Flat model'),
    floorAreaSqft: area === null ? null : Math.round(area * SQFT_PER_SQM),
    lastUpdated: row('Last updated'),
  };
}
