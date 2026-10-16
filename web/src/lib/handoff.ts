/**
 * Taking a listing straight off the portal, without copying anything.
 *
 * The page cannot fetch a listing — static files, no server, and the
 * portals' CORS policy refuses a browser their HTML — and fetching one from
 * a server of ours would be scraping, which their terms forbid and their bot
 * protection would break anyway. That left copy the page, paste it here,
 * which works and which nobody wants to do.
 *
 * The way round it is that the listing is already open in a browser that is
 * allowed to read it: yours. A bookmarklet runs in that tab, keeps the lines
 * that look like a listing's figures, and opens this page with them in the
 * fragment. Nothing is crawled and no server of ours ever touches the
 * portal — it is the same act as reading the page yourself, which is what
 * the copying was, with the keystrokes taken out.
 *
 * What travels is text, not parsed figures. The bookmarklet is a courier and
 * nothing more, so the one parser that reads a listing stays here, where it
 * is tested, instead of being copied into a line of javascript in somebody's
 * bookmarks bar where it would quietly rot.
 */

/** The fragment key the bookmarklet writes. Short: this gets long enough. */
export const HANDOFF_KEY = 'x';

/**
 * How much listing text a hand-off may carry.
 *
 * Long enough for the figures and the details table several times over,
 * short enough to stay inside what every browser will hold in a URL. The
 * bookmarklet filters the page down before it gets here, so hitting this at
 * all means something unexpected came through, and the tail is the part
 * worth losing.
 */
export const HANDOFF_LIMIT = 4000;

/**
 * The listing text a bookmarklet handed over, or null.
 *
 * Returns the raw text. The caller runs the same extraction a paste goes
 * through, so a hand-off and a paste cannot disagree about what a listing
 * says.
 */
export function readHandoff(hash: string): string | null {
  const text = (hash ?? '').replace(/^#/, '');
  if (!text) return null;
  let value: string | null = null;
  try {
    value = new URLSearchParams(text).get(HANDOFF_KEY);
  } catch {
    return null;
  }
  if (!value) return null;
  const trimmed = value.trim();
  return trimmed ? trimmed.slice(0, HANDOFF_LIMIT) : null;
}

/** The link a bookmarklet builds. Here so the bookmarklet and the reader agree. */
export function handoffLink(text: string, base: string): string {
  const params = new URLSearchParams();
  params.set(HANDOFF_KEY, text.slice(0, HANDOFF_LIMIT));
  return `${base}#${params.toString()}`;
}
