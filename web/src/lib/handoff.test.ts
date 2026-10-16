/**
 * Reading what the bookmarklet handed over.
 *
 * The courier is three lines of javascript in somebody's bookmarks bar and
 * cannot be fixed once it is there, so this end has to cope with whatever
 * arrives — including nothing, and including a fragment that is a shared
 * link rather than a hand-off.
 */
import { describe, expect, it } from 'vitest';

import { HANDOFF_LIMIT, handoffLink, readHandoff } from './handoff';

describe('reading a hand-off', () => {
  it('reads the listing text back out of a link it made', () => {
    const text = 'S$ 698,888 S$ 698 psf\n1,001 sqft\nBuilt year 2016';
    const link = handoffLink(text, 'https://example.test/site/');
    expect(readHandoff(new URL(link).hash)).toBe(text);
  });

  it('survives the characters a listing is full of', () => {
    // & splits a query string, + decodes to a space, # ends a fragment, and
    // all three turn up in listing text.
    const text = 'S$ 698,888 & 93 m² + NEGO #1 · 99-year leasehold';
    const link = handoffLink(text, 'https://example.test/');
    expect(readHandoff(new URL(link).hash)).toBe(text);
  });

  it('is null for a fragment that is not a hand-off', () => {
    expect(readHandoff('#s=665c-punggol-dr&p=698888')).toBeNull();
    expect(readHandoff('#')).toBeNull();
    expect(readHandoff('')).toBeNull();
  });

  it('is null when the hand-off is empty or only spaces', () => {
    expect(readHandoff('#x=')).toBeNull();
    expect(readHandoff('#x=%20%20')).toBeNull();
  });

  it('caps what it will take', () => {
    const long = 'S$ 698,888 '.repeat(2000);
    expect(readHandoff(`#x=${encodeURIComponent(long)}`)!.length)
      .toBe(HANDOFF_LIMIT);
    expect(handoffLink(long, 'https://example.test/').length)
      .toBeLessThan(HANDOFF_LIMIT * 4);
  });

  it('does not throw on a fragment that is not a query string at all', () => {
    expect(() => readHandoff('#%%%not-a-query%%%')).not.toThrow();
  });
});
