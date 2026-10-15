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

import { fetchZoning, zoningKeysFor } from '../lib/data';
import { mapKindOf, type MapKind } from '../lib/places';
import type { MapPoint, ZoneGroup, ZoningParcel } from '../lib/types';
import { POINT_KINDS, ZONE_GROUPS } from '../lib/types';

/**
 * The kinds, in the fixed order their colours are assigned in.
 *
 * Never cycled and never reordered by how many of each this block happens to
 * have: a school is aqua on every block or the colour means nothing.
 */
const KINDS: Record<
  MapKind,
  { label: string; plural: string; letter: string; slot: number }
> = {
  mrt_exit: { label: 'MRT exit', plural: 'MRT exits', letter: 'M', slot: 1 },
  bus_stop: { label: 'Bus stop', plural: 'Bus stops', letter: 'B', slot: 2 },
  school: { label: 'Primary school', plural: 'Primary schools', letter: 'S', slot: 3 },
  hawker_centre: { label: 'Hawker centre', plural: 'Hawker centres', letter: 'H', slot: 4 },
  preschool: { label: 'Pre-school', plural: 'Pre-schools', letter: 'P', slot: 5 },
  park: { label: 'Park', plural: 'Parks', letter: 'K', slot: 6 },
  // The seventh is a slate rather than a seventh hue. Six was what survived
  // validating all pairs for colour-vision deficiency, and adding a hue to
  // that set is how a palette stops working; a dark neutral differs from
  // every one of them in lightness, which is the channel that survives.
  secondary_school: {
    label: 'Secondary school', plural: 'Secondary schools', letter: 'E', slot: 7,
  },
};

/** Every kind, in slot order, for the pills and the legend. */
const MAP_KINDS: MapKind[] = [...POINT_KINDS, 'secondary_school'];

/**
 * The zoning groups, in the order their colours are assigned.
 *
 * This is the Master Plan as a base layer — the same question the text rows
 * answer in words, in the form it is actually asked: not "a parcel 212 m
 * east", but "that empty patch I can see".
 *
 * Four hues and two neutrals, which is what survived validating all pairs for
 * colour-vision deficiency. Six hues did not: orange against amber, and
 * purple against blue, are a coin toss for a protanope, and a zoning map is
 * all-pairs by nature because any two zones can end up side by side.
 * Residential is the neutral ground rather than a hue because it is seven out
 * of ten parcels — colouring the majority tells you nothing, and the eye
 * needs it to recede so the other five read.
 *
 * Park against business is the one pair in the floor band, so identity never
 * rests on colour: every parcel names its own use on hover, and the legend
 * names all six.
 */
/**
 * The zoning colours are declared as CSS custom properties so the two themes
 * live beside the rest of the palette — but they are drawn on a canvas, and
 * `ctx.fillStyle = 'var(--zone-1)'` is not a colour. Canvas has no access to
 * the cascade: the assignment is simply ignored, the previous fill stays, and
 * parcels come out black or not at all. So the variables are read off the
 * element once, resolved to real values, and re-read when the theme changes.
 */
function resolveZones(
  el: HTMLElement,
): Partial<Record<ZoneGroup, { fill: string; line: string }>> {
  const style = getComputedStyle(el);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    residential: { fill: read('--zone-ground', '#b9b2a6'), line: read('--zone-ground-line', '#9d9589') },
    commercial: { fill: read('--zone-1', '#2a78d6'), line: read('--zone-1', '#2a78d6') },
    business: { fill: read('--zone-2', '#d4488f'), line: read('--zone-2', '#d4488f') },
    park: { fill: read('--zone-3', '#1baf7a'), line: read('--zone-3', '#1baf7a') },
    reserve: { fill: read('--zone-4', '#eb6834'), line: read('--zone-4', '#eb6834') },
    civic: { fill: read('--zone-civic', '#8d8d8d'), line: read('--zone-civic-line', '#6f6f6f') },
  };
}

/**
 * Partial on purpose. Roads and water are in the cells for the massing view,
 * which has no basemap under it; this map does, and drawing them again would
 * bury everything else. A group with no entry here is skipped below rather
 * than drawn in some default colour.
 */
const ZONES: Partial<
  Record<ZoneGroup, { label: string; fill: string; line: string; alpha: number }>
> = {
  // Seven parcels in ten. At the weight the others carry it stops being a
  // ground and becomes a grey sheet over the streets, so it gets a sixth of
  // the ink and the basemap still reads through it.
  residential: { label: 'Residential', fill: 'var(--zone-ground)', line: 'var(--zone-ground-line)', alpha: 0.16 },
  commercial: { label: 'Commercial & mixed', fill: 'var(--zone-1)', line: 'var(--zone-1)', alpha: 0.4 },
  business: { label: 'Business & industry', fill: 'var(--zone-2)', line: 'var(--zone-2)', alpha: 0.4 },
  park: { label: 'Park & open space', fill: 'var(--zone-3)', line: 'var(--zone-3)', alpha: 0.4 },
  reserve: { label: 'Reserve site', fill: 'var(--zone-4)', line: 'var(--zone-4)', alpha: 0.45 },
  civic: { label: 'Civic & utility', fill: 'var(--zone-civic)', line: 'var(--zone-civic-line)', alpha: 0.22 },
};

/**
 * What the opening view frames, in metres.
 *
 * Everything is drawn; this only decides where the map starts. Fitting all of
 * it means one school 1.8 km away sets the zoom, and the map opens showing
 * Toa Payoh to Geylang with the block as a dot in the middle — technically
 * complete and useless for "what is around here". Framing the walkable ones
 * opens on streets you could name, and the far pins are a scroll away.
 */
const FIT_RADIUS_M = 900;

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
function pin(kind: MapKind, label: string): L.DivIcon {
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

/**
 * What a parcel says when you point at it.
 *
 * The plot ratio is the part that answers "what could still be built": URA
 * publishes a ratio, not a height, and a parcel with no ratio is one nobody
 * has evaluated yet rather than one that is fixed low.
 */
function tooltipFor(parcel: ZoningParcel): string {
  const ratio =
    parcel.r === undefined
      ? 'plot ratio not fixed'
      : `plot ratio ${parcel.r}`;
  return `${escapeHtml(parcel.u)} \u00b7 ${ratio}`;
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
  const zoningLayer = React.useRef<L.LayerGroup | null>(null);
  const drawnCells = React.useRef<Set<string>>(new Set());
  const drawnParcels = React.useRef<Set<number>>(new Set());
  const shell = React.useRef<HTMLDivElement | null>(null);
  /** Bumped when the theme changes, to redraw with re-resolved colours. */
  const [palette, setPalette] = React.useState(0);
  const [zoningOn, setZoningOn] = React.useState(true);
  const [zoningSeen, setZoningSeen] = React.useState<Set<ZoneGroup>>(new Set());
  /** What the map should frame, kept for the resize handler to reapply. */
  const fitTo = React.useRef<L.LatLngBounds | null>(null);
  const tiles = React.useRef<L.TileLayer | null>(null);
  const [hidden, setHidden] = React.useState<Set<MapKind>>(new Set());
  const [failed, setFailed] = React.useState(false);

  const present = React.useMemo(() => {
    const counts = new Map<MapKind, number>();
    for (const p of points) {
      const kind = mapKindOf(p);
      if (KINDS[kind]) counts.set(kind, (counts.get(kind) ?? 0) + 1);
    }
    // Fixed order, not frequency order, so a kind keeps its colour and its
    // place in the legend from one block to the next.
    return MAP_KINDS.filter((k) => counts.has(k)).map(
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
    // Its own pane, under the markers and over the tiles. Canvas rather than
    // SVG: a dense cell is two thousand polygons, and the DOM does not want
    // two thousand paths it has to hit-test on every mousemove.
    instance.createPane('zoning');
    const pane = instance.getPane('zoning');
    if (pane) pane.style.zIndex = '350';
    zoningLayer.current = L.layerGroup([], { pane: 'zoning' }).addTo(instance);

    const resized = new ResizeObserver(() => {
      instance.invalidateSize({ animate: false });
      const target = fitTo.current;
      if (target) {
        instance.fitBounds(target, {
          maxZoom: 17,
          padding: [24, 24],
          animate: false,
        });
      }
    });
    resized.observe(host.current);

    return () => {
      resized.disconnect();
      instance.remove();
      map.current = null;
      tiles.current = null;
      zoningLayer.current = null;
      drawnCells.current = new Set();
      drawnParcels.current = new Set();
    };
  }, [placed, lat, lon]);

  // Follow the page's theme, including a toggle made after the map was built.
  React.useEffect(() => {
    if (!tiles.current) return;
    const apply = () => {
      tiles.current?.setUrl(TILES[prefersDark() ? 'dark' : 'light']);
      // The zoning colours came out of the cascade, so a theme change means
      // they have to be read again and the parcels redrawn in the new ones.
      zoningLayer.current?.clearLayers();
      drawnCells.current = new Set();
      drawnParcels.current = new Set();
      setPalette((n) => n + 1);
    };
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

  // The zoning layer. Cells are fetched for whatever the view covers and
  // drawn once; panning back over a cell costs nothing because both the fetch
  // and the drawing are remembered.
  React.useEffect(() => {
    const instance = map.current;
    const layer = zoningLayer.current;
    if (!instance || !layer || !placed) return;
    let live = true;

    const renderer = L.canvas({ pane: 'zoning', padding: 0.3 });
    const colours = shell.current ? resolveZones(shell.current) : null;

    async function draw() {
      if (!live || !instance || !layer) return;
      if (!zoningOn) return;
      const b = instance.getBounds();
      const keys = zoningKeysFor(
        b.getSouth(), b.getWest(), b.getNorth(), b.getEast(),
      );
      for (const key of keys) {
        if (drawnCells.current.has(key)) continue;
        drawnCells.current.add(key);
        const cell = await fetchZoning(key);
        if (!live || !cell) continue;
        const groups = new Set<ZoneGroup>();
        for (const parcel of cell.parcels) {
          const zone = ZONES[parcel.g];
          const colour = colours?.[parcel.g];
          if (!zone || !colour) continue;
          // Drawn once, however many cells carry it.
          if (drawnParcels.current.has(parcel.i)) continue;
          drawnParcels.current.add(parcel.i);
          groups.add(parcel.g);
          const shape = L.geoJSON(
            { type: 'Feature', properties: {}, geometry: parcel.m } as never,
            {
              pane: 'zoning',
              style: {
                renderer,
                color: colour.line,
                weight: 0.7,
                opacity: zone.alpha + 0.3,
                fillColor: colour.fill,
                fillOpacity: zone.alpha,
              },
            },
          );
          shape.bindTooltip(tooltipFor(parcel), { sticky: true });
          shape.addTo(layer);
        }
        if (groups.size) {
          setZoningSeen((was) => {
            const next = new Set(was);
            for (const g of groups) next.add(g);
            return next;
          });
        }
      }
    }

    void draw();
    instance.on('moveend', draw);
    return () => {
      live = false;
      instance.off('moveend', draw);
    };
    // lat/lon are dependencies even though the drawing does not read them:
    // a new block builds a new Leaflet map, and without them this effect
    // keeps its `moveend` handler bound to the old one and never draws on the
    // new. It happens to work today only because the component remounts when
    // the report changes, which is not something this effect should rely on.
  }, [placed, lat, lon, zoningOn, palette]);

  // Turning it off clears what was drawn, so turning it back on refetches
  // nothing — the cells are still in the page's cache — but redraws cleanly.
  React.useEffect(() => {
    const layer = zoningLayer.current;
    if (!layer || zoningOn) return;
    layer.clearLayers();
    drawnCells.current = new Set();
    drawnParcels.current = new Set();
    setZoningSeen(new Set());
  }, [zoningOn]);

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

    const shown = points.filter(
      (p) => KINDS[mapKindOf(p)] && !hidden.has(mapKindOf(p)),
    );
    for (const point of shown) {
      const kind = mapKindOf(point);
      const name = point.name ?? KINDS[kind].label;
      const text = `${name} · ${point.distance_m} m`;
      L.marker([point.lat, point.lon], {
        icon: pin(kind, text),
        alt: `${KINDS[kind].label}: ${text}`,
        keyboard: false,
      })
        .bindTooltip(text, { direction: 'top' })
        .addTo(group);
    }

    // Fit to what is drawn, so a block with everything close does not sit at
    // the same zoom as one with a school 900 m away.
    const bounds = L.latLngBounds([[lat, lon]]);
    const near = shown.filter((p) => p.distance_m <= FIT_RADIUS_M);
    // If nothing is close, frame what there is rather than the block alone.
    for (const p of near.length > 0 ? near : shown) bounds.extend([p.lat, p.lon]);
    fitTo.current = bounds;
    instance.invalidateSize({ animate: false });
    // Pixel padding, not `pad()`. A ratio pad grows with the span, so a block
    // with a school 1.8 km off got 18% of 2.4 km of margin and dropped a whole
    // zoom level for it — the map came out at z13, which is most of the
    // island. A fixed margin costs the same 24 px whatever is being framed.
    instance.fitBounds(bounds, {
      maxZoom: 17,
      padding: [24, 24],
      animate: false,
    });

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

  function toggle(kind: MapKind) {
    setHidden((was) => {
      const next = new Set(was);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  return (
    <div
      ref={shell}
      className="map-block"
      role="group"
      aria-label={`Where ${label} is, and ${points.length} places near it`}
    >
      <div className="map-zoning" role="group" aria-label="Zoning">
        <button
          type="button"
          className={`map-key map-key--wide${zoningOn ? '' : ' map-key--off'}`}
          aria-pressed={zoningOn}
          onClick={() => setZoningOn((on) => !on)}
        >
          Master Plan zoning
        </button>
        {zoningOn
          ? ZONE_GROUPS.filter((g) => zoningSeen.has(g) && ZONES[g]).map((g) => (
              <span key={g} className="map-zone-key">
                <span
                  className={`map-zone-swatch map-zone-swatch--${g}`}
                  aria-hidden="true"
                />
                {ZONES[g]?.label}
              </span>
            ))
          : null}
      </div>

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
          The basemap did not load. The tiles come from onemap.gov.sg, and
          something between this page and it refused them — a sandboxed
          preview, an extension, or a network that blocks the host. The pins
          are still in the right places and the distances above never depended
          on the basemap.
        </p>
      ) : null}

      {zoningOn ? (
        <p className="hint">
          Zoning is URA's Master Plan 2019: what each parcel is allowed to
          become, not what stands on it today. Point at one for its use and
          plot ratio. Roads and water are left to the basemap.
        </p>
      ) : null}

      {/* The map is a picture; this is the same thing in words, for a screen
          reader and for anyone who would rather read it. */}
      <details className="map-list">
        <summary>List these places</summary>
        <ul>
          {points
            .filter((p) => KINDS[mapKindOf(p)])
            .map((p, i) => (
              <li key={`${p.kind}-${p.name ?? i}`}>
                <span className="map-list-kind">{KINDS[mapKindOf(p)].label}</span>{' '}
                {p.name ?? 'unnamed'} · {p.distance_m} m
              </li>
            ))}
        </ul>
      </details>
    </div>
  );
}
