/**
 * Saved units, in this browser's own storage.
 *
 * **Only the inputs are saved, never the report.** The link, the block, the
 * asking price, the floor, the facing and a note — and the report is rebuilt
 * from today's data when the unit is opened, so a flat saved in January shows
 * June's figures in June rather than January's. Storing the report would make
 * the saved list quietly go stale, and a stale fair-value range is worse than
 * no range.
 *
 * This is the cost of having no login, and the PRD states it plainly: one
 * list per device. Sharing a unit means sending a link that carries its
 * details, which `share.ts` does. If that becomes annoying the fix is a
 * server and a password, and that is a decision to make then.
 *
 * Every read and write is wrapped, because storage throws rather than
 * returning empty in a private window or with site data blocked, and a
 * saved-units list that takes the page down with it would be a poor trade.
 */

import type { Facing, SavedUnit } from './types';
import { FACINGS } from './types';

const KEY = 'listing-check.saved.v1';

/** A stable id, so a unit survives being edited and re-saved. */
function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `u${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function isFacing(value: unknown): value is Facing {
  return typeof value === 'string' && (FACINGS as readonly string[]).includes(value);
}

/**
 * Read one unit out of whatever was in storage.
 *
 * Defensive on every field: this data was written by an older version of the
 * page and will be read by a newer one, and a single bad entry must not take
 * the whole list with it.
 */
function parseUnit(raw: unknown): SavedUnit | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const item = raw as Record<string, unknown>;
  if (typeof item.slug !== 'string' || !item.slug) return null;
  const asking = Number(item.asking);
  const storey = Number(item.storey);
  const size = Number(item.size);
  return {
    id: typeof item.id === 'string' && item.id ? item.id : newId(),
    url: typeof item.url === 'string' ? item.url : '',
    slug: item.slug,
    blockKey: typeof item.blockKey === 'string' ? item.blockKey : '',
    block: typeof item.block === 'string' ? item.block : '',
    street: typeof item.street === 'string' ? item.street : '',
    town: typeof item.town === 'string' ? item.town : '',
    // Saved before the size field existed reads as no size, which is what
    // it is — not a zero.
    size: Number.isFinite(size) && size > 0 ? size : null,
    flatType: typeof item.flatType === 'string' ? item.flatType : '',
    asking: Number.isFinite(asking) && asking > 0 ? asking : null,
    storey: Number.isFinite(storey) && storey >= 1 ? Math.round(storey) : null,
    facing: isFacing(item.facing) ? item.facing : null,
    destinationPostcode:
      typeof item.destinationPostcode === 'string' && item.destinationPostcode
        ? item.destinationPostcode
        : null,
    note: typeof item.note === 'string' ? item.note : '',
    savedAt:
      typeof item.savedAt === 'string' ? item.savedAt : new Date().toISOString(),
  };
}

export function loadSaved(): SavedUnit[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(parseUnit).filter((u): u is SavedUnit => u !== null);
  } catch {
    // A private window, blocked site data, or something that is not JSON.
    return [];
  }
}

/** Returns false when the write could not be made, so the page can say so. */
export function storeSaved(units: SavedUnit[]): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify(units));
    return true;
  } catch {
    return false;
  }
}

export interface UnitDraft {
  url: string;
  slug: string;
  blockKey: string;
  block: string;
  street: string;
  town: string;
  /** Floor area in square metres, as typed off the listing. */
  size: number | null;
  flatType: string;
  asking: number | null;
  storey: number | null;
  facing: Facing | null;
  destinationPostcode: string | null;
  note: string;
}

/**
 * Add a unit, or replace the one already saved for the same flat.
 *
 * "The same flat" is the block, the flat type and the floor together, because
 * two 4-room flats on different floors of one block are two different things
 * to compare — but re-pasting the same listing with a corrected asking price
 * should update it rather than add a second row.
 */
export function upsert(units: SavedUnit[], draft: UnitDraft): SavedUnit[] {
  const match = units.findIndex(
    (u) =>
      u.slug === draft.slug &&
      u.flatType === draft.flatType &&
      u.storey === draft.storey,
  );
  const unit: SavedUnit = {
    ...draft,
    id: match >= 0 ? units[match]!.id : newId(),
    savedAt: new Date().toISOString(),
  };
  if (match >= 0) {
    const copy = [...units];
    copy[match] = unit;
    return copy;
  }
  return [...units, unit];
}

export function remove(units: SavedUnit[], id: string): SavedUnit[] {
  return units.filter((u) => u.id !== id);
}

export function describeUnit(unit: SavedUnit): string {
  const floor = unit.storey ? `#${unit.storey}` : 'floor unknown';
  return `${unit.block} ${unit.street} · ${unit.flatType} · ${floor}`;
}
