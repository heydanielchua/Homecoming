/**
 * Putting a unit's details in a URL, and reading them back out.
 *
 * The site has no login and no server, so a saved list lives in the browser
 * that saved it. Sharing a unit therefore means sending a link that carries
 * its details — which is the honest version of the trade the PRD describes,
 * rather than a "share" button that silently does nothing useful.
 *
 * What travels is only what was typed: the block, the flat type, the floor,
 * the facing, the asking price, the note. The report is not in the link, so
 * whoever opens it sees today's figures and not the sender's.
 */

import type { UnitDraft } from './saved';
import type { Facing, SavedUnit } from './types';
import { FACINGS } from './types';

/** Query keys, kept short because these links get pasted into chat. */
const KEYS = {
  slug: 's',
  flatType: 't',
  asking: 'p',
  storey: 'f',
  facing: 'd',
  note: 'n',
  url: 'l',
  postcode: 'c',
} as const;

export interface SharedUnit {
  slug: string;
  flatType: string | null;
  asking: number | null;
  storey: number | null;
  facing: Facing | null;
  note: string;
  url: string;
  destinationPostcode: string | null;
}

export function shareParams(unit: SavedUnit | UnitDraft): URLSearchParams {
  const params = new URLSearchParams();
  params.set(KEYS.slug, unit.slug);
  if (unit.flatType) params.set(KEYS.flatType, unit.flatType);
  if (unit.asking) params.set(KEYS.asking, String(unit.asking));
  if (unit.storey) params.set(KEYS.storey, String(unit.storey));
  if (unit.facing) params.set(KEYS.facing, unit.facing);
  if (unit.note) params.set(KEYS.note, unit.note);
  if (unit.url) params.set(KEYS.url, unit.url);
  if (unit.destinationPostcode) {
    params.set(KEYS.postcode, unit.destinationPostcode);
  }
  return params;
}

/** The whole link, ready to paste. */
export function shareLink(unit: SavedUnit | UnitDraft, base?: string): string {
  const root =
    base ??
    (typeof window === 'undefined'
      ? ''
      : `${window.location.origin}${window.location.pathname}`);
  return `${root}#${shareParams(unit).toString()}`;
}

/** Read a shared unit out of a URL fragment, or null if there is none. */
export function readShared(hash: string): SharedUnit | null {
  const text = (hash ?? '').replace(/^#/, '');
  if (!text) return null;
  const params = new URLSearchParams(text);
  const slug = params.get(KEYS.slug);
  if (!slug) return null;

  const asking = Number(params.get(KEYS.asking));
  const storey = Number(params.get(KEYS.storey));
  const facing = params.get(KEYS.facing);
  return {
    slug,
    flatType: params.get(KEYS.flatType),
    asking: Number.isFinite(asking) && asking > 0 ? asking : null,
    storey: Number.isFinite(storey) && storey >= 1 ? Math.round(storey) : null,
    facing:
      facing && (FACINGS as readonly string[]).includes(facing)
        ? (facing as Facing)
        : null,
    note: params.get(KEYS.note) ?? '',
    url: params.get(KEYS.url) ?? '',
    destinationPostcode: params.get(KEYS.postcode),
  };
}

/** Put a unit in the address bar without adding a history entry per keystroke. */
export function replaceHash(unit: SavedUnit | UnitDraft | null): void {
  if (typeof window === 'undefined') return;
  const hash = unit ? `#${shareParams(unit).toString()}` : '';
  const next = `${window.location.pathname}${window.location.search}${hash}`;
  window.history.replaceState(null, '', next);
}
