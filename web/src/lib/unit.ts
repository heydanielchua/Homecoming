/**
 * The flat's own numbers: its size, what is being asked for it, and which of
 * the block's flat types that makes it.
 *
 * The flat type used to be a dropdown. It does not need to be: HDB's own
 * sales say what a 4-room in this block measures, so a size typed off the
 * listing picks the type better than a reader guessing from a menu — and it
 * picks it against *this block's* sales rather than a national rule of thumb,
 * which matters because the same label covers 90 m² in one estate and 105 in
 * another.
 *
 * Where it cannot tell, it says so by returning null rather than choosing the
 * nearest thing and sounding certain.
 */

/** Square feet in a square metre. Listings quote feet; HDB publishes metres. */
export const SQFT_PER_SQM = 10.7639;

/**
 * How far a size may sit from a type's median and still be that type, as a
 * share of the median. HDB's own ranges overlap a little at the edges — a
 * large 3-room and a small 4-room are a few square metres apart — so this is
 * deliberately loose enough to catch a real flat and tight enough that 150 m²
 * does not quietly become a 5-room.
 */
export const SIZE_TOLERANCE = 0.25;

export interface TypeSize {
  flatType: string;
  medianSqm: number;
}

/**
 * Which flat type a size most likely is, judged against this block's sales.
 *
 * Returns null when nothing is close enough, which is the honest answer for a
 * size typed wrong by a factor of ten or a flat type that has never sold here.
 */
export function flatTypeForSize(
  sizeSqm: number | null,
  sizes: TypeSize[],
): string | null {
  if (sizeSqm === null || !Number.isFinite(sizeSqm) || sizeSqm <= 0) return null;

  let best: TypeSize | null = null;
  let bestGap = Infinity;
  for (const candidate of sizes) {
    if (!candidate.medianSqm || candidate.medianSqm <= 0) continue;
    const gap = Math.abs(candidate.medianSqm - sizeSqm);
    if (gap < bestGap) {
      bestGap = gap;
      best = candidate;
    }
  }
  if (!best) return null;
  return bestGap / best.medianSqm <= SIZE_TOLERANCE ? best.flatType : null;
}

export function sqftOf(sizeSqm: number | null): number | null {
  if (sizeSqm === null || !Number.isFinite(sizeSqm) || sizeSqm <= 0) return null;
  return sizeSqm * SQFT_PER_SQM;
}

/** Asking price per square foot, which is how a listing quotes itself. */
export function pricePerSqft(
  price: number | null,
  sizeSqm: number | null,
): number | null {
  const sqft = sqftOf(sizeSqm);
  if (price === null || !Number.isFinite(price) || price <= 0) return null;
  if (sqft === null) return null;
  return price / sqft;
}

/** Asking price per square metre, the unit the rest of the report works in. */
export function pricePerSqm(
  price: number | null,
  sizeSqm: number | null,
): number | null {
  if (price === null || !Number.isFinite(price) || price <= 0) return null;
  if (sizeSqm === null || !Number.isFinite(sizeSqm) || sizeSqm <= 0) return null;
  return price / sizeSqm;
}
