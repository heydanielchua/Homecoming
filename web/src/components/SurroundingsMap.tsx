/**
 * Where the things near this block actually are.
 *
 * The text rows answer "how far and which way" — 353 m, NE. That is the right
 * answer to "is it walkable" and the wrong one to "what is around here",
 * because eight compass points is a 45-degree bucket and the mind quietly
 * rounds it to a straight line. The map answers the second question, and it
 * answers it with the geocode the source published rather than anything
 * derived from the bearing: a point this page cannot place is left off it.
 *
 * The basemap is OneMap, which is the Singapore Land Authority's own and the
 * only one here with the right street names, the right block numbers, and no
 * usage key to leak into a public repository. Its Night style stands in for
 * dark mode, so the map changes with the page rather than glowing white in
 * the middle of it.
 *
 * Identity is never colour alone: every marker carries a letter, the legend
 * names each kind, and the name and distance are on hover and on focus. That
 * is what lets the marker fills stay under 3:1 against an arbitrary map tile,
 * which they must, because the tile underneath is whatever OneMap drew there.
 */

import * as React from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import type { MapPoint, PointKind } from '../lib/types';
import { POINT_KINDS } from '../lib/types';

/**
 * The kinds, in the fixed order their colours are assigned in.
 *
 * Never cycled and never reordered by how many of each this block happens to
 * have: a school is aqua on every block or the colour means nothing.
 */
const KINDS: Record<
  PointKind,
  { label: string; plural: string; letter: string; slot: number }
> = {
  mrt_exit: { label: 'MRT exit', plural: 'MRT exits', letter: 'M', slot: 1 },
  bus_stop: { label: 'Bus stop', plural: 'Bus stops', letter: 'B', slot: 2 },
  school: { label: 'Primary school', plural: 'Primary schools', letter: 'S', slot: 3 },
  hawker_centre: { label: 'Hawker centre', plural: 'Hawker centres', letter: 'H', slot: 4 },
  preschool: { label: 'Pre-school', plural: 'Pre-schools', letter: 'P', slot: 5 },
  park: { label: 'Park', plural: 'Parks', letter: 'K', slot: 6 },
};

/** Singapore, with enough slack for Pulau Ubin and the western islands. */
const ISLAND_BOUNDS = L.latLngBounds([1.13, 103.57], [1.49, 104.13]);

const TILES: Record<'light' | 'dark', string> = {
  light: 'https://www.onemap.gov.sg/maps/tiles/Default/{z}/{x}/{y}.png',
  dark: 'https://www.onemap.gov.sg/maps/tiles/Night/{z}/{x}/{y}.png',
};

/* SLA asks for this wording on anything drawn on their basemap. */
const ATTRIBUTION =
  '<a href="https://www.onemap.gov.sg/" target="_blank" rel="noreferrer noopener">OneMap</a>' +
  ' &copy; contributors &#124; ' +
  '<a href="https://www.sla.gov.sg/" target="_blank" rel="noreferrer noopener">' +
  'Singapore Land Authority</a>';

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * A marker as an HTML pin rather than an image.
 *
 * The ring is the point of it. These fills sit on whatever OneMap drew
 * underneath — a park, a reservoir, a motorway — so contrast against the
 * surface cannot be reasoned about in advance. A ring in the page's own
 * surface colour separates the mark from the tile whatever the tile is.
 */
function pin(kind: PointKind, label: string): L.DivIcon {
  const { letter, slot } = KINDS[kind];
  return L.divIcon({
    className: 'map-pin-wrap',
    html:
      `<span class="map-pin map-pin--${slot}" title="${escapeHtml(label)}"` +
      ` aria-hidden="true">${letter}</span>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function blockPin(): L.DivIcon {
  return L.divIcon({
    className: 'map-pin-wrap',
    html: '<span class="map-pin map-pin--here" aria-hidden="true"></span>',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

function prefersDark(): boolean {
  const stamped = document.documentElement.dataset.theme;
  if (stamped === 'dark') return true;
  if (stamped === 'light') return false;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
}

export function SurroundingsMap({
  lat,
  lon,
  label,
  points,
}: {
  lat: number | null;
  lon: number | null;
  label: string;
  points: MapPoint[];
}) {
  const host = React.useRef<HTMLDivElement | null>(null);
  const map = React.useRef<L.Map | null>(null);
  /** What the map should frame, kept for the resize handler to reapply. */
  const fitTo = React.useRef<L.LatLngBounds | null>(null);
  const tiles = React.useRef<L.TileLayer | null>(null);
  const [hidden, setHidden] = React.useState<Set<PointKind>>(new Set());
  const [failed, setFailed] = React.useState(false);

  const present = React.useMemo(() => {
    const counts = new Map<PointKind, number>();
    for (const p of points) {
      if (KINDS[p.kind]) counts.set(p.kind, (counts.get(p.kind) ?? 0) + 1);
    }
    // Fixed order, not frequency order, so a kind keeps its colour and its
    // place in the legend from one block to the next.
    return POINT_KINDS.filter((k) => counts.has(k)).map(
      (k) => [k, counts.get(k)!] as const,
    );
  }, [points]);

  const placed = lat !== null && lon !== null;

  React.useEffect(() => {
    if (!placed || !host.current || map.current) return;
    const instance = L.map(host.current, {
      center: [lat, lon],
      zoom: 16,
      minZoom: 11,
      maxBounds: ISLAND_BOUNDS,
      scrollWheelZoom: false, // The page scrolls past this; the map does not eat it.
      attributionControl: true,
    });
    const layer = L.tileLayer(TILES[prefersDark() ? 'dark' : 'light'], {
      minZoom: 11,
      maxZoom: 19,
      attribution: ATTRIBUTION,
    });
    let loaded = 0;
    let errored = 0;
    layer.on('tileload', () => {
      loaded += 1;
      setFailed(false);
    });
    layer.on('tileerror', () => {
      errored += 1;
      // One tile missing at the coast is ordinary. Nothing drawn at all is
      // the case worth saying out loud.
      if (loaded === 0 && errored >= 4) setFailed(true);
    });
    layer.addTo(instance);
    map.current = instance;
    tiles.current = layer;

    // This section lives in an accordion, so the container can be laid out
    // after Leaflet has measured it. A map that measured 0x0 computes its
    // zoom from nothing and lands on the whole island — which is how this
    // first shipped, asking for zoom 13 tiles of Johor. Every resize, the
    // panel opening included, re-measures and refits.
    const resized = new ResizeObserver(() => {
      instance.invalidateSize({ animate: false });
      const target = fitTo.current;
      if (target) instance.fitBounds(target, { maxZoom: 17, animate: false });
    });
    resized.observe(host.current);

    return () => {
      resized.disconnect();
      instance.remove();
      map.current = null;
      tiles.current = null;
    };
  }, [placed, lat, lon]);

  // Follow the page's theme, including a toggle made after the map was built.
  React.useEffect(() => {
    if (!tiles.current) return;
    const apply = () => tiles.current?.setUrl(TILES[prefersDark() ? 'dark' : 'light']);
    apply();
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    query?.addEventListener('change', apply);
    const watcher = new MutationObserver(apply);
    watcher.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    return () => {
      query?.removeEventListener('change', apply);
      watcher.disconnect();
    };
  }, [placed]);

  // Markers are redrawn when the data or the filter changes.
  React.useEffect(() => {
    const instance = map.current;
    if (!instance || !placed) return;
    const group = L.layerGroup().addTo(instance);

    L.marker([lat, lon], {
      icon: blockPin(),
      keyboard: false,
      interactive: true,
      zIndexOffset: 1000,
    })
      .bindTooltip(`${label} — this block`, { direction: 'top' })
      .addTo(group);

    const shown = points.filter((p) => KINDS[p.kind] && !hidden.has(p.kind));
    for (const point of shown) {
      const name = point.name ?? KINDS[point.kind].label;
      const text = `${name} · ${point.distance_m} m`;
      L.marker([point.lat, point.lon], {
        icon: pin(point.kind, text),
        alt: `${KINDS[point.kind].label}: ${text}`,
        keyboard: false,
      })
        .bindTooltip(text, { direction: 'top' })
        .addTo(group);
    }

    // Fit to what is drawn, so a block with everything close does not sit at
    // the same zoom as one with a school 900 m away.
    const bounds = L.latLngBounds([[lat, lon]]);
    for (const p of shown) bounds.extend([p.lat, p.lon]);
    const padded = bounds.pad(0.18);
    fitTo.current = padded;
    instance.invalidateSize({ animate: false });
    instance.fitBounds(padded, { maxZoom: 17, animate: false });

    return () => {
      group.remove();
    };
  }, [placed, lat, lon, label, points, hidden]);

  if (!placed) {
    return (
      <p className="muted">
        This block has no geocode, so there is nothing to put on a map. The
        rows above say the same thing in words.
      </p>
    );
  }

  if (points.length === 0) {
    return (
      <p className="muted">
        Nothing within the search radius has a published coordinate, so the map
        would be the block on its own.
      </p>
    );
  }

  function toggle(kind: PointKind) {
    setHidden((was) => {
      const next = new Set(was);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  return (
    <div
      className="map-block"
      role="group"
      aria-label={`Where ${label} is, and ${points.length} places near it`}
    >
      <div className="map-legend" role="group" aria-label="What to show">
        {present.map(([kind, count]) => {
          const off = hidden.has(kind);
          return (
            <button
              key={kind}
              type="button"
              className={`map-key${off ? ' map-key--off' : ''}`}
              aria-pressed={!off}
              onClick={() => toggle(kind)}
            >
              <span
                className={`map-pin map-pin--${KINDS[kind].slot} map-key-pin`}
                aria-hidden="true"
              >
                {KINDS[kind].letter}
              </span>
              {count === 1 ? KINDS[kind].label : `${KINDS[kind].plural} (${count})`}
            </button>
          );
        })}
      </div>

      <div ref={host} className="map-canvas" />

      {failed ? (
        <p className="hint">
          The basemap did not load, so the pins have nothing under them. The
          distances in the rows above do not depend on it.
        </p>
      ) : null}

      {/* The map is a picture; this is the same thing in words, for a screen
          reader and for anyone who would rather read it. */}
      <details className="map-list">
        <summary>List these places</summary>
        <ul>
          {points
            .filter((p) => KINDS[p.kind])
            .map((p, i) => (
              <li key={`${p.kind}-${p.name ?? i}`}>
                <span className="map-list-kind">{KINDS[p.kind].label}</span>{' '}
                {p.name ?? 'unnamed'} · {p.distance_m} m
              </li>
            ))}
        </ul>
      </details>
    </div>
  );
}
