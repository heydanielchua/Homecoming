/**
 * Formatting, in one place so a figure reads the same everywhere.
 *
 * The one rule worth stating: a missing value is never formatted as a dash or
 * a zero. `Figure` carries its own reason for being missing, and the
 * components show that reason instead of a placeholder — so there is nowhere
 * in the page where a blank could be mistaken for a measurement.
 */

import type { Figure } from './types';

const SGD = new Intl.NumberFormat('en-SG', {
  style: 'currency',
  currency: 'SGD',
  maximumFractionDigits: 0,
});

const SGD_EXACT = new Intl.NumberFormat('en-SG', {
  style: 'currency',
  currency: 'SGD',
  maximumFractionDigits: 2,
});

const PLAIN = new Intl.NumberFormat('en-SG');

export function money(value: number | null | undefined): string | null {
  return value === null || value === undefined ? null : SGD.format(value);
}

export function moneyExact(value: number | null | undefined): string | null {
  return value === null || value === undefined ? null : SGD_EXACT.format(value);
}

/** A price range, as the fair value is always shown. */
export function range(
  low: number | null,
  high: number | null,
): string | null {
  if (low === null || high === null) return null;
  return `${SGD.format(low)} – ${SGD.format(high)}`;
}

export function count(value: number | null | undefined): string | null {
  return value === null || value === undefined ? null : PLAIN.format(value);
}

export function percent(value: number | null | undefined, digits = 1): string | null {
  if (value === null || value === undefined) return null;
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

/** Metres, rounded the way a person would say them. */
export function metres(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (value < 1000) return `${Math.round(value)} m`;
  return `${(value / 1000).toFixed(1)} km`;
}

/** A walk, at 80 m a minute — slow enough to be honest about a hill. */
export function walk(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const minutes = Math.round(value / 80);
  if (minutes < 1) return 'under a minute';
  return `${minutes} min walk`;
}

export function years(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  return `${value.toFixed(1)} years`;
}

export function sqm(value: number | null | undefined): string | null {
  return value === null || value === undefined ? null : `${Math.round(value)} m²`;
}

/** "2026-08" as "Aug 2026". */
export function month(value: string | null | undefined): string | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return value;
  const names = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  return `${names[Number(match[2]) - 1] ?? match[2]} ${match[1]}`;
}

/** "2026-Q3" as "Q3 2026". */
export function quarter(value: string | null | undefined): string | null {
  if (!value) return null;
  const match = /^(\d{4})-Q([1-4])$/.exec(value);
  return match ? `Q${match[2]} ${match[1]}` : value;
}

/**
 * The value of a figure, formatted by its unit, or null when it is unknown.
 *
 * Returning null rather than a dash is deliberate: the caller has to decide
 * what to show instead, and the only right answer is the figure's own reason.
 */
export function figureValue(
  figure: Figure<number | string> | undefined,
): string | null {
  if (!figure || figure.v === null) return null;
  const value = figure.v;
  // A unit only means anything on a number. `last_sale` carries a month, and
  // it falls through to being printed as it is.
  if (typeof value !== 'number') {
    return figure.u === 'month' ? (month(value) ?? value) : String(value);
  }
  switch (figure.u) {
    case 'SGD':
      return money(value);
    case 'SGD/sqm':
      // Not "/m²": every label that carries this unit already says per m²,
      // and "Price per m²: $12,069.90/m²" reads as a mistake.
      return moneyExact(value);
    case 'SGD/month':
      return `${money(value)}/month`;
    case '%':
      return percent(value);
    case 'years':
      return years(value);
    case 'sqm':
      return sqm(value);
    case 'year':
      // A year is a label, not a quantity: 1992, never "1,992".
      return String(Math.round(value));
    default:
      return count(value);
  }
}

/** Title case for the shouting HDB publishes: "ANG MO KIO" to "Ang Mo Kio". */
export function titleCase(text: string | null | undefined): string {
  if (!text) return '';
  return text
    .toLowerCase()
    .replace(/(^|[\s/('-])([a-z])/g, (_m, before: string, letter: string) =>
      `${before}${letter.toUpperCase()}`,
    );
}

export const CONFIDENCE_LABEL: Record<string, string> = {
  fact: 'Fact',
  worked: 'Worked out',
  estimated: 'Estimated',
  unknown: 'Unknown',
};

export const CONFIDENCE_EXPLAIN: Record<string, string> = {
  fact: 'Published by the government as it stands. It sold for this; the block is this tall.',
  worked: 'Computed from published figures, and only as good as the method shown with it.',
  estimated: 'Inferred where nothing is published. Always a range, with what it came from.',
  unknown: 'Not available, with the reason — never a blank and never a zero.',
};
