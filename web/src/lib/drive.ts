/**
 * Drive time to a typed destination — the one thing on this page that leaves
 * the site.
 *
 * Everything else here is a lookup against files that were built a month ago,
 * which is what lets the site be static and free. A drive time cannot work
 * that way: the destination is typed in, so there is no route to precompute.
 *
 * The PRD leaves this as an open question — keep it as an optional typed
 * destination, or drop it — and this is the answer that keeps it without
 * spoiling the rest. OneMap's search and routing both answer unauthenticated
 * and send `Access-Control-Allow-Origin: *`, so the browser can ask directly:
 * no key to leak, no server to run, nothing in the way of the other rows. It
 * is on demand rather than automatic, so a report shows nothing to a network
 * until somebody asks for this one row, and a failure says what failed rather
 * than reading as a short drive.
 *
 * Only the postcode goes out, and only to a government mapping service. The
 * saved units never leave the browser.
 */

const SEARCH =
  'https://www.onemap.gov.sg/api/common/elastic/search';
const ROUTE = 'https://www.onemap.gov.sg/api/public/routingsvc/route';

export interface Drive {
  minutes: number;
  distanceM: number;
  destination: string;
}

export class DriveError extends Error {}

interface SearchResult {
  results?: { LATITUDE?: string; LONGITUDE?: string; ADDRESS?: string }[];
}

interface RouteResult {
  route_summary?: { total_time?: number; total_distance?: number };
}

/** Find a Singapore postcode, or say it is not one. */
async function geocode(postcode: string): Promise<{
  lat: number;
  lon: number;
  address: string;
}> {
  const url = new URL(SEARCH);
  url.searchParams.set('searchVal', postcode);
  url.searchParams.set('returnGeom', 'Y');
  url.searchParams.set('getAddrDetails', 'Y');
  url.searchParams.set('pageNum', '1');

  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    throw new DriveError(
      `OneMap answered ${response.status} when asked about ${postcode}.`,
    );
  }
  const payload = (await response.json()) as SearchResult;
  const hit = payload.results?.[0];
  if (!hit?.LATITUDE || !hit.LONGITUDE) {
    throw new DriveError(`OneMap has no address at postcode ${postcode}.`);
  }
  return {
    lat: Number(hit.LATITUDE),
    lon: Number(hit.LONGITUDE),
    address: hit.ADDRESS ?? postcode,
  };
}

/**
 * Drive time from a block to a postcode.
 *
 * Two requests, both unauthenticated: find the postcode, then route to it.
 */
export async function driveTo(
  from: { lat: number; lon: number },
  postcode: string,
): Promise<Drive> {
  const clean = postcode.replace(/\D/g, '');
  if (clean.length !== 6) {
    throw new DriveError('A Singapore postcode is six digits.');
  }

  const destination = await geocode(clean);

  const url = new URL(ROUTE);
  url.searchParams.set('start', `${from.lat},${from.lon}`);
  url.searchParams.set('end', `${destination.lat},${destination.lon}`);
  url.searchParams.set('routeType', 'drive');

  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    throw new DriveError(
      `OneMap answered ${response.status} when asked for the route. It does ` +
        'not normally need a credential for this.',
    );
  }
  const payload = (await response.json()) as RouteResult;
  const seconds = payload.route_summary?.total_time;
  const metres = payload.route_summary?.total_distance;
  if (seconds === undefined || metres === undefined) {
    throw new DriveError('OneMap found no road route between those two points.');
  }
  return {
    minutes: Math.round(seconds / 60),
    distanceM: metres,
    destination: destination.address,
  };
}
