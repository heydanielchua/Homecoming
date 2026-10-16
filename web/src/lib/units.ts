/**
 * One set of units, and they are the ones the listings use.
 *
 * HDB publishes floor areas in square metres and this page's whole data set is
 * metric underneath. But nobody shopping for a flat is reading HDB's CSV: they
 * are reading 99.co and PropertyGuru, where a flat is `1,302 sqft` and a price
 * is `S$ 520.74 psf`, and they are holding this page up against that tab. A
 * report that answers in dollars per square metre makes the reader do the
 * conversion before they can tell whether it agrees with the listing — and the
 * one job here is telling them whether it agrees with the listing.
 *
 * So feet are what gets shown and metres are what gets stored. The stored
 * figure stays metric because that is the unit it was published in and
 * converting on the way in would round HDB's own number; the conversion
 * happens at the last moment, on the way to the screen.
 *
 * Where a square metre still belongs, it is written `sqm` and not `m²` — HDB
 * writes sqm, the portals write sqm, and the superscript was ours alone.
 */

import { SQFT_PER_SQM } from './unit';

export { SQFT_PER_SQM };

const group = (value: number, places = 0) =>
  value.toLocaleString('en-SG', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });

const usable = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0;

/** Square feet from square metres. */
export function toSqft(sqm: number | null | undefined): number | null {
  return usable(sqm) ? sqm * SQFT_PER_SQM : null;
}

/** Dollars per square foot from dollars per square metre. */
export function toPsf(psm: number | null | undefined): number | null {
  return usable(psm) ? psm / SQFT_PER_SQM : null;
}

/** A floor area the way a listing writes it: `1,302 sqft`. */
export function areaSqft(sqm: number | null | undefined): string | null {
  const feet = toSqft(sqm);
  return feet === null ? null : `${group(Math.round(feet))} sqft`;
}

/** A floor area in HDB's own unit, for the line that cites HDB: `93 sqm`. */
export function areaSqm(sqm: number | null | undefined): string | null {
  return usable(sqm) ? `${group(Math.round(sqm))} sqm` : null;
}

/**
 * A price per square foot: `$719 psf`.
 *
 * To the dollar, not to the cent. A listing quotes `S$ 520.74 psf` because
 * that is its own asking price divided by its own floor area and the cents are
 * real. Every psf on this page is a median of other people's sales, where the
 * cents are arithmetic rather than information — `$7,741.94` said nothing true
 * that `$719` does not.
 */
export function psfOf(psm: number | null | undefined): string | null {
  const perFoot = toPsf(psm);
  return perFoot === null ? null : `$${group(Math.round(perFoot))} psf`;
}

/** The same, without the unit, for a column that carries it in the header. */
export function psfBare(psm: number | null | undefined): string | null {
  const perFoot = toPsf(psm);
  return perFoot === null ? null : `$${group(Math.round(perFoot))}`;
}
