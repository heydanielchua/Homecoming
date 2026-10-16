/**
 * The block and its surroundings, as volumes.
 *
 * This draws the same three claims the text above it makes, in the one form
 * text is bad at. What stands is the footprint layer extruded to the storey
 * count HDB publishes. What could stand is every Master Plan parcel extruded
 * from whatever is already on it up to the top of its fitted band — the worst
 * case, deliberately, because the question a buyer is asking is how bad this
 * can get rather than how it is likely to turn out. The plane through the
 * scene is the viewer's eye line: everything above it can block the view,
 * everything below it cannot, and that is the whole reading.
 *
 * Nothing here is new data. The footprints and the parcels are the cell files
 * the site already publishes for the flat map, the storey bands and the sun's
 * track come out of `meta.json`, and the floor and facing come from the form.
 * Two representations of one model cannot disagree if they read one source.
 *
 * Three.js is a third of a megabyte and most readers never open this, so it
 * is imported dynamically and the whole thing loads when it scrolls into
 * view. If it fails, or the browser has no WebGL, the tables above are not a
 * fallback — they are the report, and this was only ever the picture of it.
 */

import * as React from 'react';
import { Slider } from '@base-ui/react/slider';
import { Switch } from '@base-ui/react/switch';
import { Toggle } from '@base-ui/react/toggle';
import { ToggleGroup } from '@base-ui/react/toggle-group';

import { streetOf } from '../lib/street';
import { fetchBuildings, fetchZoning, zoningKeysFor } from '../lib/data';
import {
  EYE_HEIGHT_M,
  M_PER_DEG_LAT,
  M_PER_DEG_LON,
  centresOf,
  envelopesFor,
  extentOf,
  viewerHeightM,
  withinRadius,
  type Geom,
  type Rings,
} from '../lib/massing';
import {
  FACINGS,
  ZONE_GROUPS,
  type Building,
  type Facing,
  type Meta,
  type ZoneGroup,
  type ZoningParcel,
} from '../lib/types';

/**
 * How far out to draw. Beyond this a block is skyline rather than something
 * in the way, and it matches the 300 m the neighbour table uses with enough
 * margin that the edge of the data is not the edge of the picture.
 */
const RADIUS_M = 450;

/** Compass bearings, for the facing wedge. Mirrors FACINGS in nhf/sun.py. */
const BEARINGS: Record<Facing, number> = {
  N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315,
};

/**
 * The compass points spelled out, for the buttons' accessible names.
 *
 * A screen reader says "N" as the letter en, which is not a direction. The
 * visible label stays short because eight of them share one row.
 */
const FACING_NAMES: Record<Facing, string> = {
  N: 'North',
  NE: 'North-east',
  E: 'East',
  SE: 'South-east',
  S: 'South',
  SW: 'South-west',
  W: 'West',
  NW: 'North-west',
};

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

type Layer = 'now' | 'worst';

/**
 * The zoning palette, and it is the map's own.
 *
 * The flat map above already draws these parcels in these colours, validated
 * all-pairs for colour-vision deficiency — six hues failed that and four hues
 * with two neutrals passed. A second palette down here would mean the same
 * parcel was pink in one view and purple in the other, so the tokens are the
 * map's, read from `.massing` where they are redeclared.
 */
const ZONES: Record<
  ZoneGroup,
  { label: string; css: string; ground?: boolean; surface?: boolean }
> = {
  residential: { label: 'Residential', css: '--zone-ground', ground: true },
  commercial: { label: 'Commercial', css: '--zone-1' },
  business: { label: 'Business', css: '--zone-2' },
  park: { label: 'Park', css: '--zone-3' },
  reserve: { label: 'Reserve site', css: '--zone-4' },
  civic: { label: 'Civic', css: '--zone-civic' },
  // `surface` means it is the ground itself rather than a use drawn over it:
  // laid down nearly solid, and never given an envelope, because nothing is
  // ever built to a plot ratio on a road.
  road: { label: 'Road', css: '--zone-road', surface: true },
  water: { label: 'Water', css: '--zone-water', surface: true },
};

/** The groups that carry a zoning decision, so can grow an envelope. */
const BUILDABLE_ZONES = ZONE_GROUPS.filter((g) => !ZONES[g].surface);

interface Props {
  meta: Meta;
  lat: number;
  lon: number;
  slug: string;
  storey: number | null;
  facing: Facing | null;
  /** This block's own top floor, which is as high as the slider goes. */
  maxFloor: number | null;
  onStorey: (next: number | null) => void;
  onFacing: (next: Facing | null) => void;
}

interface Loaded {
  buildings: Building[];
  parcels: ZoningParcel[];
}

/** What a pointer is resting on, in words. */
interface Readout {
  label: string;
  detail: string;
}

export function Massing({
  meta, lat, lon, slug, storey, facing, maxFloor, onStorey, onFacing,
}: Props) {
  // Where the handle sits before a floor has been chosen. Halfway up reads
  // as "somewhere in this block" and is the shortest drag to anywhere; the
  // figures stay unknown until it is actually moved, because a floor nobody
  // picked is not a floor.
  const top = Math.max(1, maxFloor ?? 40);
  const restingFloor = Math.max(1, Math.round(top / 2));
  const host = React.useRef<HTMLDivElement | null>(null);
  const canvas = React.useRef<HTMLCanvasElement | null>(null);
  const scene = React.useRef<SceneHandle | null>(null);

  const [wanted, setWanted] = React.useState(false);
  const [data, setData] = React.useState<Loaded | null>(null);
  const [failed, setFailed] = React.useState<string | null>(null);
  const [layer, setLayer] = React.useState<Layer>('now');
  // The sun is on to begin with. Without it the scene is lit flat and reads
  // as grey chips: the shadows are what make one volume sit behind another.
  const [sunOn, setSunOn] = React.useState(true);
  const [hour, setHour] = React.useState(16);
  const [month, setMonth] = React.useState(12);
  const [hiddenZones, setHiddenZones] = React.useState<Partial<Record<ZoneGroup, boolean>>>({});
  const [readout, setReadout] = React.useState<Readout | null>(null);
  const [summary, setSummary] = React.useState<string | null>(null);
  /**
   * Bumped when a scene finishes building. The effect below pushes the
   * controls into the scene, and without this it would never run for the
   * scene it was waiting on: building is asynchronous, so by the time the
   * handle exists none of that effect's other dependencies have changed and
   * the scene keeps the defaults it was constructed with — every layer
   * hidden, which looks exactly like a renderer that does not work.
   */
  const [built, setBuilt] = React.useState(0);

  const hours = React.useMemo(
    () => Array.from(new Set(meta.sun.track.map((s) => s.hour))).sort((a, b) => a - b),
    [meta.sun.track],
  );

  // Load when it comes into view rather than when the report renders: most
  // readers never scroll this far, and nobody should pay for a renderer they
  // did not ask for.
  React.useEffect(() => {
    const node = host.current;
    if (!node || wanted) return;
    if (typeof IntersectionObserver === 'undefined') {
      setWanted(true);
      return;
    }
    const watch = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setWanted(true);
          watch.disconnect();
        }
      },
      { rootMargin: '200px' },
    );
    watch.observe(node);
    return () => watch.disconnect();
  }, [wanted]);

  React.useEffect(() => {
    if (!wanted) return;
    let live = true;
    const keys = zoningKeysFor(
      lat - RADIUS_M / M_PER_DEG_LAT,
      lon - RADIUS_M / M_PER_DEG_LON,
      lat + RADIUS_M / M_PER_DEG_LAT,
      lon + RADIUS_M / M_PER_DEG_LON,
    );
    Promise.all([
      Promise.all(keys.map((k) => fetchBuildings(k).catch(() => null))),
      Promise.all(keys.map((k) => fetchZoning(k).catch(() => null))),
    ])
      .then(([buildingCells, zoningCells]) => {
        if (!live) return;
        // A polygon is written into every cell its box reaches, so it is
        // deduplicated by its own id and drawn once.
        const buildings = new Map<string, Building>();
        const parcels = new Map<number, ZoningParcel>();
        buildingCells.forEach((c) => c?.buildings.forEach((b) => buildings.set(b.i, b)));
        zoningCells.forEach((c) => c?.parcels.forEach((p) => parcels.set(p.i, p)));
        setData({
          buildings: [...buildings.values()],
          parcels: [...parcels.values()],
        });
      })
      .catch((err: unknown) => {
        if (live) setFailed(err instanceof Error ? err.message : String(err));
      });
    return () => {
      live = false;
    };
  }, [wanted, lat, lon]);

  React.useEffect(() => {
    if (!data || !canvas.current) return;
    let live = true;
    let handle: SceneHandle | null = null;
    build(canvas.current, { meta, lat, lon, slug, data, setReadout })
      .then((made) => {
        if (!live) {
          made?.dispose();
          return;
        }
        handle = made;
        scene.current = made;
        setSummary(made.summary);
        setBuilt((n) => n + 1);
      })
      .catch((err: unknown) => {
        if (live) setFailed(err instanceof Error ? err.message : String(err));
      });
    return () => {
      live = false;
      handle?.dispose();
      scene.current = null;
    };
  }, [data, meta, lat, lon, slug]);

  React.useEffect(() => {
    scene.current?.apply({
      layer,
      storey,
      facing,
      sun: sunOn ? { month, hour } : null,
      hiddenZones,
    });
  }, [layer, storey, facing, sunOn, month, hour, hiddenZones, built]);

  const eye = viewerHeightM(storey, meta.sun.constants.storey_height_m);

  return (
    <div className="massing" ref={host}>
      <div className="massing-stage">
        <canvas ref={canvas} className="massing-canvas" />
        {failed ? (
          <p className="massing-note massing-note--fail">
            The 3D view could not be drawn ({failed}). Everything it would have
            shown is in the figures and tables above.
          </p>
        ) : !data ? (
          <p className="massing-note">Loading the block and what stands around it…</p>
        ) : readout ? (
          <p className="massing-note">
            <b>{readout.label}</b>
            <span>{readout.detail}</span>
          </p>
        ) : (
          <p className="massing-note">
            <b>Drag to turn it</b>
            <span>
              Scroll or pinch to zoom. Point at a block or a volume to read it.
            </span>
          </p>
        )}
      </div>

      <div className="massing-controls">
        <div className="massing-seg" role="group" aria-label="What to draw">
          <button
            type="button"
            onClick={() => setLayer('now')}
            aria-pressed={layer === 'now'}
          >
            What stands
          </button>
          <button
            type="button"
            onClick={() => setLayer('worst')}
            aria-pressed={layer === 'worst'}
          >
            Built to the limit
          </button>
        </div>

        {/* The switch and its words are one thing and must wrap as one. Left
            to the flex row they came apart at phone width — the switch
            stranded on the line above, its label sitting under the
            segmented control looking like a heading for it. */}
        <div className="massing-switch-row">
          <Switch.Root
            className="massing-switch"
            checked={sunOn}
            onCheckedChange={setSunOn}
            aria-labelledby="massing-sun-label"
          >
            <Switch.Thumb className="massing-switch-thumb" />
          </Switch.Root>
          <span
            className="massing-switch-label"
            id="massing-sun-label"
            onClick={() => setSunOn(!sunOn)}
          >
            Afternoon sun
          </span>
        </div>

        {/* One grid, four cells, every cell the same shape: a label and its
            value on one line, the control under it at one height. The floor
            used to run the full width with the eight facing buttons crushed
            into a corner beside it, and the month and time below at two more
            widths again — four controls, four different footprints, nothing
            lining up with anything. They are all the same size now, so the
            eye can move down a column instead of hunting. */}
        <div className="massing-grid">
          <div className="massing-cell">
            <span className="massing-cell-head" id="massing-floor-label">
              <span className="massing-cell-label">Your floor</span>
              <span className="massing-cell-value">
                {storey === null ? '—' : `#${storey}`}
              </span>
            </span>
            <Slider.Root
              className="slider"
              value={storey ?? restingFloor}
              min={1}
              max={top}
              onValueChange={(value) => onStorey(Number(value))}
            >
              <Slider.Control className="slider-control">
                <Slider.Track className="slider-track">
                  <Slider.Indicator className="slider-indicator" />
                  <Slider.Thumb
                    className="slider-thumb"
                    aria-labelledby="massing-floor-label"
                  />
                </Slider.Track>
              </Slider.Control>
            </Slider.Root>
          </div>

          <div className="massing-cell">
            <span className="massing-cell-head" id="massing-facing">
              <span className="massing-cell-label">Facing</span>
              <span className="massing-cell-value">{facing ?? '—'}</span>
            </span>
            {/* One row of eight, so the facing has a slider's footprint
                rather than a keypad's. A toggle group rather than eight
                buttons: the arrow keys walk the compass, which is what
                somebody comparing two aspects actually does. */}
            <ToggleGroup
              className="massing-compass"
              aria-labelledby="massing-facing"
              value={facing ? [facing] : []}
              onValueChange={(groupValue) => {
                const next = groupValue[groupValue.length - 1];
                onFacing((next as Facing | undefined) ?? null);
              }}
              multiple={false}
            >
              {FACINGS.map((point) => (
                <Toggle key={point} value={point} aria-label={FACING_NAMES[point]}>
                  {point}
                </Toggle>
              ))}
            </ToggleGroup>
          </div>

          {sunOn ? (
            <>
              <div className="massing-cell">
                <span className="massing-cell-head" id="massing-month">
                  <span className="massing-cell-label">Month</span>
                  <span className="massing-cell-value">{MONTHS[month - 1]}</span>
                </span>
                <Slider.Root
                  className="slider"
                  value={month}
                  min={1}
                  max={12}
                  onValueChange={(value) => setMonth(Number(value))}
                >
                  <Slider.Control className="slider-control">
                    <Slider.Track className="slider-track">
                      <Slider.Indicator className="slider-indicator" />
                      <Slider.Thumb
                        className="slider-thumb"
                        aria-labelledby="massing-month"
                      />
                    </Slider.Track>
                  </Slider.Control>
                </Slider.Root>
              </div>

              <div className="massing-cell">
                <span className="massing-cell-head" id="massing-time">
                  <span className="massing-cell-label">Time</span>
                  <span className="massing-cell-value">{clockOf(hour)}</span>
                </span>
                <Slider.Root
                  className="slider"
                  value={Math.max(0, hours.indexOf(hour))}
                  min={0}
                  max={hours.length - 1}
                  onValueChange={(value) =>
                    setHour(hours[Number(value)] ?? 17)
                  }
                >
                  <Slider.Control className="slider-control">
                    <Slider.Track className="slider-track">
                      <Slider.Indicator className="slider-indicator" />
                      <Slider.Thumb
                        className="slider-thumb"
                        aria-labelledby="massing-time"
                      />
                    </Slider.Track>
                  </Slider.Control>
                </Slider.Root>
              </div>
            </>
          ) : null}
        </div>

        <button
          type="button"
          className="massing-reset"
          onClick={() => scene.current?.home()}
        >
          Reset the view
        </button>
      </div>

      <div className="massing-legend-row">
        {BUILDABLE_ZONES.map((group) => (
          <button
            key={group}
            type="button"
            className="massing-chip"
            aria-pressed={!hiddenZones[group]}
            onClick={() =>
              setHiddenZones((current) => ({ ...current, [group]: !current[group] }))
            }
          >
            <span
              className="massing-swatch"
              style={{ background: `var(${ZONES[group].css})` }}
            />
            {ZONES[group].label}
          </button>
        ))}
        <span className="massing-chip massing-chip--static">
          <span className="massing-swatch" style={{ background: 'var(--massing-mine)' }} />
          This block
        </span>
      </div>

      <p className="muted massing-legend">
        {eye === null
          ? 'Drag the floor slider and the plane through the model moves with it: that is your eye line, and anything above it can block the view.'
          : `The plane is your eye line on floor ${storey}, ${Math.round(eye)} m up. Anything above it can block the view.`}
        {layer === 'worst' && summary ? ` ${summary}` : ''}
      </p>
      <p className="muted massing-legend">
        Heights are storeys × {meta.sun.constants.storey_height_m} m, because HDB
        publishes the storey count and nobody publishes the height. Only HDB
        buildings have footprints, so a gap here is a hole in the data rather
        than open sky.
      </p>
    </div>
  );
}

function clockOf(hour: number): string {
  const h = Math.floor(hour);
  const m = Math.round((hour - h) * 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/* ------------------------------------------------------------------ scene */

interface SceneState {
  layer: Layer;
  storey: number | null;
  facing: Facing | null;
  sun: { month: number; hour: number } | null;
  hiddenZones: Partial<Record<ZoneGroup, boolean>>;
}

interface SceneHandle {
  apply(state: SceneState): void;
  home(): void;
  dispose(): void;
  summary: string;
}

interface BuildArgs {
  meta: Meta;
  lat: number;
  lon: number;
  slug: string;
  data: Loaded;
  setReadout: (r: Readout | null) => void;
}

/**
 * Everything imperative lives in here. React owns the controls; this owns the
 * scene graph, and the two meet at `apply`.
 */
async function build(
  canvasEl: HTMLCanvasElement,
  { meta, lat, lon, slug, data, setReadout }: BuildArgs,
): Promise<SceneHandle> {
  const THREE = await import('three');

  const storeyM = meta.sun.constants.storey_height_m;
  // Read the palette off the canvas, not the root: the tokens are scoped to
  // `.massing` the way the map scopes its own, and a renderer cannot resolve
  // `var(--x)` itself — handed one it silently draws nothing.
  const css = (name: string) =>
    getComputedStyle(canvasEl).getPropertyValue(name).trim();
  const colour = (name: string, fallback: string) =>
    new THREE.Color(css(name) || fallback);

  const project = (lo: number, la: number): [number, number] => [
    (lo - lon) * M_PER_DEG_LON,
    (la - lat) * M_PER_DEG_LAT,
  ];

  const box = (geom: Geom) => extentOf(geom, lon, lat);
  const near = (geom: Geom) => withinRadius(box(geom), RADIUS_M);

  function polygons(geom: Geom): Rings[] {
    return geom.type === 'Polygon'
      ? [geom.coordinates as Rings]
      : (geom.coordinates as Rings[]);
  }

  function shapesOf(geom: Geom) {
    const out: InstanceType<typeof THREE.Shape>[] = [];
    for (const rings of polygons(geom)) {
      if (!rings?.length) continue;
      const shape = new THREE.Shape();
      (rings[0] ?? []).forEach(([lo, la], i) => {
        const [x, y] = project(lo, la);
        if (i === 0) shape.moveTo(x, y);
        else shape.lineTo(x, y);
      });
      for (const hole of rings.slice(1)) {
        const path = new THREE.Path();
        hole.forEach(([lo, la], i) => {
          const [x, y] = project(lo, la);
          if (i === 0) path.moveTo(x, y);
          else path.lineTo(x, y);
        });
        shape.holes.push(path);
      }
      out.push(shape);
    }
    return out;
  }

  /** Extrude upward: the shape plane becomes the ground, depth becomes height. */
  function extrude(shapes: InstanceType<typeof THREE.Shape>[], height: number) {
    const geom = new THREE.ExtrudeGeometry(shapes, {
      depth: Math.max(height, 0.1),
      bevelEnabled: false,
    });
    geom.rotateX(-Math.PI / 2); // (x, y, z) -> (x, z, -y): north becomes -z
    return geom;
  }

  const buildings = data.buildings.filter((b) => near(b.m as Geom));
  const parcels = data.parcels.filter((p) => near(p.m as Geom));

  const renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const sky = colour('--massing-sky', '#dfe3e6');
  const world = new THREE.Scene();
  world.background = sky;
  world.fog = new THREE.Fog(sky, 900, 1700);

  const camera = new THREE.PerspectiveCamera(45, 1, 1, 4000);
  // Close enough that a forty-storey block fills a good part of a 440px
  // canvas: further out and the whole estate reads as grey chips.
  const HOME = { radius: 380, theta: -0.85, phi: 1.0 };
  const orbit = { ...HOME };
  const target = new THREE.Vector3(0, 42, 0);

  // Intensities are tuned for three.js r155 and later, which dropped the
  // legacy lighting mode: the same numbers that lit the prototype under r128
  // produce about half the irradiance here, and the model came out mid-grey
  // where it should be near-white. Measured against the agreed look rather
  // than ported — a lit face reads about 235 of 244 in both.
  const hemi = new THREE.HemisphereLight(0xffffff, 0x9a948c, 1.8);
  world.add(hemi);
  const sun = new THREE.DirectionalLight(0xfff6e0, 2.8);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {
    left: -RADIUS_M, right: RADIUS_M, top: RADIUS_M, bottom: -RADIUS_M,
    near: 100, far: 2400,
  });
  sun.shadow.camera.updateProjectionMatrix();
  world.add(sun);

  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(1800, 1800),
    new THREE.MeshLambertMaterial({ color: colour('--massing-ground', '#d8d6d2') }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  world.add(ground);

  // --- the zoning, flat on the ground --------------------------------------
  // The Master Plan under the massing, so a volume can be read against the
  // land it would stand on. Drawn a hand's width above the ground plane
  // because two coplanar surfaces fight for the same depth and flicker.
  /**
   * How high the flat layers sit over the ground plane.
   *
   * Two coplanar surfaces fight for the same depth and flicker, so these are
   * lifted clear of it and of each other — a road under the uses, because a
   * road drawn over a park hides the park. The street names are laid against
   * the road height rather than a number of their own, so that a name cannot
   * drift off its surface when one of these is tuned.
   */
  const ROAD_SURFACE_Y = 0.08;
  const USE_SURFACE_Y = 0.16;

  const groundOpacity = (group: ZoneGroup) =>
    ZONES[group].surface ? 0.9 : ZONES[group].ground ? 0.22 : 0.5;

  const zoning = new THREE.Group();
  world.add(zoning);
  const byZone = new Map<ZoneGroup, ZoningParcel[]>();
  for (const p of parcels) {
    if (!ZONES[p.g]) continue;
    const list = byZone.get(p.g);
    if (list) list.push(p);
    else byZone.set(p.g, [p]);
  }
  for (const [group, list] of byZone) {
    const material = new THREE.MeshBasicMaterial({
      color: colour(ZONES[group].css, '#b9b2a6'),
      transparent: true,
      // Seven parcels in ten are residential. At the weight the others carry
      // it stops being a ground and becomes a sheet over the streets.
      opacity: groundOpacity(group),
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    const groupNode = new THREE.Group();
    groupNode.name = group;
    for (const p of list) {
      const shapes = shapesOf(p.m as Geom);
      if (!shapes.length) continue;
      const geom = new THREE.ShapeGeometry(shapes);
      geom.rotateX(-Math.PI / 2);
      // A hand's width above the ground plane, and the surfaces a touch
      // below the uses: two coplanar faces fight for the same depth and
      // flicker, and a road drawn over a park hides the park.
      geom.translate(0, ZONES[group].surface ? ROAD_SURFACE_Y : USE_SURFACE_Y, 0);
      groupNode.add(new THREE.Mesh(geom, material));
    }
    zoning.add(groupNode);
  }

  // --- street names on the roads -------------------------------------------
  // What orients somebody looking down at an estate is knowing which road is
  // which, and the Master Plan does not name them: every road polygon is
  // labelled "Road" and nothing more. The names are in the building data
  // instead, in HDB's own slugs, so a street is named here by the blocks
  // addressed on it and the label is laid on the nearest road surface to
  // them.
  //
  // That is an inference, so it is kept a conservative one. A street needs
  // two blocks in view before it is named at all — one block is a corner, not
  // a frontage — the road surface has to be close enough to be that street's,
  // and only the few nearest the middle are drawn, because a dozen names over
  // an estate is a harder picture to read than none.
  let streetLabels: InstanceType<typeof THREE.Group> | null = null;
  const STREET_MIN_BLOCKS = 2;
  const STREET_MAX_ROAD_M = 150;
  const STREET_MAX_LABELS = 7;
  /** How far around the chosen spot to read the road's own direction. */
  const ROAD_AXIS_M = 70;
  /** How near asphalt each end of a name has to come down to count as on it. */
  const ROAD_FIT_M = 16;
  /**
   * Sizes to try, largest first, in metres per canvas pixel.
   *
   * A label lying on the ground loses about half its height to the camera's
   * tilt, so the largest is sized for that rather than for a plan view; the
   * smallest is the point at which it stops being readable at all, and a name
   * that will not fit by then is not drawn.
   */
  const LABEL_SCALES = [0.3, 0.24, 0.19];

  const ringCentroid = (geom: Geom): [number, number] | null => {
    const ring = polygons(geom)[0]?.[0];
    if (!ring?.length) return null;
    let sx = 0;
    let sy = 0;
    for (const [lo, la] of ring) {
      const [x, y] = project(lo, la);
      sx += x;
      sy += y;
    }
    return [sx / ring.length, sy / ring.length];
  };

  /**
   * The road surface, as points along its edges rather than its corners.
   *
   * A polygon only carries the corners, and a straight run of road has two of
   * them a couple of hundred metres apart. Measuring "is this on the road?"
   * against corners alone puts the middle of a long street further from the
   * road than a car park is, which first pushed every name onto the junctions
   * where the corners cluster, and then — once the names had to fit on the
   * road to be drawn at all — left almost none of them drawn. So each edge is
   * walked at a street's own scale.
   */
  const ROAD_STEP_M = 8;
  const roadPoints: [number, number][] = [];
  for (const p of parcels) {
    if (p.g !== 'road') continue;
    for (const rings of polygons(p.m as Geom)) {
      const ring = rings[0] ?? [];
      for (let i = 0; i < ring.length; i += 1) {
        const [ax, ay] = project(ring[i]![0], ring[i]![1]);
        const next = ring[(i + 1) % ring.length]!;
        const [bx, by] = project(next[0], next[1]);
        roadPoints.push([ax, ay]);
        const run = Math.hypot(bx - ax, by - ay);
        const steps = Math.floor(run / ROAD_STEP_M);
        for (let step = 1; step < steps; step += 1) {
          const t = step / steps;
          roadPoints.push([ax + (bx - ax) * t, ay + (by - ay) * t]);
        }
      }
    }
  }

  const blockCentres: [number, number][] = [];
  const streetBlocks = new Map<string, [number, number][]>();
  for (const b of buildings) {
    const centre = ringCentroid(b.m as Geom);
    if (!centre) continue;
    // Every block counts for clearance, including one whose street could not
    // be read: it still stands in the way of a name laid under it.
    blockCentres.push(centre);
    const name = streetOf(b.slug ?? '', b.b ?? '');
    if (!name) continue;
    const list = streetBlocks.get(name);
    if (list) list.push(centre);
    else streetBlocks.set(name, [centre]);
  }

  /**
   * How wide a name will be drawn, in canvas pixels, before anything is built.
   *
   * The placement below has to know the length to decide whether the name
   * fits on the road, and it decides that before there is a mesh.
   */
  const LABEL_FONT = '600 64px ui-sans-serif, system-ui, -apple-system, sans-serif';
  const LABEL_PAD = 56;

  function labelWidthPx(text: string): number {
    const probe = document.createElement('canvas').getContext('2d');
    if (!probe) return 0;
    probe.font = LABEL_FONT;
    return Math.ceil(probe.measureText(text).width) + LABEL_PAD;
  }

  /** A label drawn into a canvas, so no font has to be loaded to draw text. */
  function streetLabel(
    text: string,
    mPerPx: number,
  ): InstanceType<typeof THREE.Mesh> | null {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const FONT = LABEL_FONT;
    ctx.font = FONT;
    const width = Math.ceil(ctx.measureText(text).width);
    canvas.width = width + LABEL_PAD;
    canvas.height = 128;
    // Setting the size clears everything the context was told, the font
    // included, so it is set again here rather than above.
    ctx.font = FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.letterSpacing = '2px';
    // A dark halo under light type: the name has to hold up over asphalt,
    // over the pale ground where a junction opens out, and over a building
    // that the camera happens to put behind it.
    ctx.lineWidth = 6;
    ctx.lineJoin = 'round';
    ctx.strokeStyle = 'rgba(12, 12, 12, 0.42)';
    ctx.strokeText(text, canvas.width / 2, canvas.height / 2);
    ctx.fillStyle = css('--massing-road-ink') || '#f4f3f1';
    ctx.fillText(text, canvas.width / 2, canvas.height / 2);

    const texture = new THREE.CanvasTexture(canvas);
    texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
    texture.colorSpace = THREE.SRGBColorSpace;
    const geom = new THREE.PlaneGeometry(
      canvas.width * mPerPx,
      canvas.height * mPerPx,
    );
    geom.rotateX(-Math.PI / 2);
    return new THREE.Mesh(
      geom,
      new THREE.MeshBasicMaterial({
        map: texture,
        transparent: true,
        depthWrite: false,
      }),
    );
  }

  if (roadPoints.length) {
    const placed: {
      name: string;
      at: [number, number];
      angle: number;
      scale: number;
      from: number;
    }[] = [];

    for (const [name, blocks] of streetBlocks) {
      if (blocks.length < STREET_MIN_BLOCKS) continue;
      const cx = blocks.reduce((sum, b) => sum + b[0], 0) / blocks.length;
      const cy = blocks.reduce((sum, b) => sum + b[1], 0) / blocks.length;

      // Of the road near these blocks, the most open stretch of it. Taking
      // the nearest point put the name hard against the blocks that gave it,
      // where from any angle but straight down a tower stands over the middle
      // of the word. Clearance from the nearest building is what decides it,
      // out of everything close enough to still be this street.
      let best: [number, number] | null = null;
      let bestClearance = -1;
      let nearest = Infinity;
      for (const point of roadPoints) {
        const [x, y] = point;
        const reach = Math.hypot(x - cx, y - cy);
        nearest = Math.min(nearest, reach);
        if (reach > STREET_MAX_ROAD_M) continue;
        let clearance = Infinity;
        for (const [bx, by] of blockCentres) {
          clearance = Math.min(clearance, Math.hypot(x - bx, y - by));
        }
        if (clearance > bestClearance) {
          bestClearance = clearance;
          best = point;
        }
      }
      if (!best || nearest > STREET_MAX_ROAD_M) continue;

      // The angle comes from the asphalt around the chosen spot, not from the
      // blocks. The line through a street's blocks is roughly the line of the
      // road and roughly is not good enough: a few degrees out over a name a
      // hundred metres long walks its far end off the carriageway and into
      // somebody's car park. These are the road's own points, so the name
      // lies the way the road lies.
      const local: [number, number][] = [];
      for (const [x, y] of roadPoints) {
        if (Math.hypot(x - best[0], y - best[1]) <= ROAD_AXIS_M) local.push([x, y]);
      }
      if (local.length < 2) continue;
      const lx = local.reduce((sum, q) => sum + q[0], 0) / local.length;
      const ly = local.reduce((sum, q) => sum + q[1], 0) / local.length;
      let sxx = 0;
      let syy = 0;
      let sxy = 0;
      for (const [x, y] of local) {
        sxx += (x - lx) * (x - lx);
        syy += (y - ly) * (y - ly);
        sxy += (x - lx) * (y - ly);
      }
      // The halved atan2 keeps the axis within a quarter turn of east, which
      // is also what keeps the text the right way up.
      const angle = 0.5 * Math.atan2(2 * sxy, sxx - syy);

      // Then the name is sized to fit the road it is lying on. Both ends have
      // to come down on asphalt, and a name that will not fit at the smallest
      // size this stays readable at is left off rather than drawn across the
      // ground beside the street.
      const widthPx = labelWidthPx(name);
      const onRoad = (x: number, y: number) =>
        roadPoints.some(([rx, ry]) => Math.hypot(x - rx, y - ry) <= ROAD_FIT_M);
      let scale: number | null = null;
      for (const candidate of LABEL_SCALES) {
        const reach = (widthPx * candidate) / 2;
        const ex = Math.cos(angle) * reach;
        const ey = Math.sin(angle) * reach;
        if (
          onRoad(best[0] + ex, best[1] + ey) &&
          onRoad(best[0] - ex, best[1] - ey)
        ) {
          scale = candidate;
          break;
        }
      }
      if (scale === null) continue;

      placed.push({
        name,
        at: best,
        angle,
        scale,
        from: Math.hypot(cx, cy),
      });
    }

    const labels = new THREE.Group();
    labels.name = 'streets';
    streetLabels = labels;
    for (const label of placed
      .sort((a, b) => a.from - b.from)
      .slice(0, STREET_MAX_LABELS)) {
      const mesh = streetLabel(label.name, label.scale);
      if (!mesh) continue;
      // The shapes were rotated so that a parcel's northward axis became
      // -z; a label laid the same way turns about y by the road's own angle.
      //
      // Four centimetres over the road surface, not half a metre. Higher up
      // it read as a sign hovering above the street rather than paint on it:
      // at this camera angle the gap shows as the name sitting off to one
      // side of the road it belongs to, and it slides as the view turns. The
      // render order is what keeps it drawn over the asphalt from so close.
      mesh.position.set(label.at[0], ROAD_SURFACE_Y + 0.04, -label.at[1]);
      mesh.rotation.y = label.angle;
      mesh.userData.angle = label.angle;
      mesh.renderOrder = 2;
      labels.add(mesh);
    }
    world.add(labels);
  }

  // --- what stands ---------------------------------------------------------
  const mineTone = colour('--massing-mine', '#d6ff4e');
  const plainTone = colour('--massing-built', '#f4f3f1');
  const overTone = colour('--massing-over', '#e4ded1');
  const matMine = new THREE.MeshLambertMaterial({ color: mineTone });
  const matPlain = new THREE.MeshLambertMaterial({ color: plainTone });
  const matOver = new THREE.MeshLambertMaterial({ color: overTone });
  const matEdge = new THREE.LineBasicMaterial({
    color: colour('--massing-line', '#b4b0a8'),
    transparent: true,
    opacity: 0.55,
  });

  const standing = new THREE.Group();
  world.add(standing);
  const pickable: InstanceType<typeof THREE.Mesh>[] = [];
  const towers: { mesh: InstanceType<typeof THREE.Mesh>; top: number; mine: boolean }[] = [];
  let mine: Building | null = null;
  let tallest = 0;

  for (const b of buildings) {
    const shapes = shapesOf(b.m as Geom);
    if (!shapes.length) continue;
    const storeys = b.s ?? 1;
    const geom = extrude(shapes, storeys * storeyM);
    const isMine = b.slug === slug;
    const mesh = new THREE.Mesh(geom, isMine ? matMine : matPlain);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.userData = {
      label: isMine ? `Block ${b.b} — this block` : `Block ${b.b}`,
      detail: `${storeys} storeys · about ${Math.round(storeys * storeyM)} m`,
    };
    standing.add(mesh);
    standing.add(new THREE.LineSegments(new THREE.EdgesGeometry(geom, 25), matEdge));
    pickable.push(mesh);
    // A block's top *floor* is a storey below its roof, and what blocks a
    // view is somebody's floor above your eye line, not their roof.
    towers.push({ mesh, top: (storeys - 1) * storeyM, mine: isMine });
    if (isMine) mine = b;
    if (storeys > tallest) tallest = storeys;
  }

  // --- what could stand ----------------------------------------------------
  const bands = meta.height_bands;
  // The envelope is drawn in one ink, not in the colour of the land it
  // stands on. Zone-colouring it put a pink volume on pink ground and a
  // taupe one on taupe ground: the two compounded into a single mass on the
  // east and vanished entirely on the west, which is the side you live on.
  // What the land is, the ground and the readout already say; what the
  // volume has to say is how high it goes.
  // Edges, with no solid face. A translucent mass over translucent ground
  // was the whole problem: filled, these read as haze and hid the buildings
  // standing inside them. As a cage the volume says "not built yet" the way
  // a survey drawing does, and what stands is still visible through it.
  const limitTone = colour('--massing-limit', '#2b2b2b');
  const matLimitEdge = new THREE.LineBasicMaterial({
    color: limitTone, transparent: true, opacity: 0.3,
  });
  // Above your eye line it earns more ink: that is the part that can block.
  const matLimitEdgeOver = new THREE.LineBasicMaterial({
    color: limitTone, transparent: true, opacity: 0.75,
  });
  const matHidden = new THREE.MeshBasicMaterial({
    // Invisible, and only there to be what the pointer lands on: a cage has
    // no face to hover, and hit-testing its edges would be unusable.
    transparent: true, opacity: 0, depthWrite: false,
  });

  const allowed = new THREE.Group();
  allowed.visible = false;
  world.add(allowed);

  // Two lists with two jobs: the edges are what gets restyled at the eye
  // line, and the invisible meshes are what the pointer can land on.
  const volumes: {
    edges: InstanceType<typeof THREE.LineSegments>;
    top: number;
  }[] = [];
  const limitTargets: InstanceType<typeof THREE.Mesh>[] = [];
  const found = envelopesFor(
    parcels,
    centresOf(buildings, lon, lat),
    bands,
    storeyM,
    lon,
    lat,
  );
  for (const e of found.envelopes) {
    const shapes = shapesOf(e.parcel.m as Geom);
    if (!shapes.length) continue;
    const group = e.parcel.g;
    if (ZONES[group].surface) continue;   // nothing is built on a road

    const geom = extrude(shapes, e.topM - e.baseM);
    geom.translate(0, e.baseM, 0); // start at the roofline, not the ground
    const mesh = new THREE.Mesh(geom, matHidden);
    mesh.userData = { label: e.label, detail: e.detail, zone: group };
    allowed.add(mesh);
    limitTargets.push(mesh);

    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geom, 25), matLimitEdge,
    );
    allowed.add(edges);
    volumes.push({ edges, top: e.topM });
  }
  const summary = found.summary;

  // --- the eye line --------------------------------------------------------
  // A level marker, not a floor. At the scene's full radius this became a
  // translucent sheet across the whole frame with the city hidden behind it;
  // what it has to say is "your eyes are at this height", and a disc around
  // your own block says that without walling the view off.
  const EYE_DISC_M = 170;
  const eyeTone = colour('--massing-eye', '#6c4cf0');
  const eyeMat = new THREE.MeshBasicMaterial({
    color: eyeTone,
    transparent: true,
    opacity: 0.09,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const eyePlane = new THREE.Mesh(new THREE.CircleGeometry(EYE_DISC_M, 64), eyeMat);
  eyePlane.rotation.x = -Math.PI / 2;
  eyePlane.visible = false;
  world.add(eyePlane);

  // A band rather than a line. A one-pixel ring is a pixel wide however far
  // away it is, so it read as a stray lasso dropped over the model; three
  // metres of open cylinder has area, catches the light the rest of the
  // scene is lit by, and says "this is a height" rather than "this is a
  // circle". The colour is its own — a violet that appears nowhere in the
  // zoning palette or on the buildings, so it cannot be mistaken for either.
  const eyeRim = new THREE.Mesh(
    new THREE.CylinderGeometry(EYE_DISC_M, EYE_DISC_M, 3, 72, 1, true),
    new THREE.MeshBasicMaterial({
      color: eyeTone,
      transparent: true,
      opacity: 0.5,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  eyeRim.visible = false;
  world.add(eyeRim);

  // --- which way you look --------------------------------------------------
  const wedge = new THREE.Mesh(
    new THREE.CircleGeometry(RADIUS_M * 0.75, 32, -Math.PI / 4, Math.PI / 2),
    new THREE.MeshBasicMaterial({
      color: mineTone, transparent: true, opacity: 0.16,
      side: THREE.DoubleSide, depthWrite: false,
    }),
  );
  wedge.rotation.x = -Math.PI / 2;
  wedge.visible = false;
  world.add(wedge);

  if (mine) {
    const [x0, y0, x1, y1] = box(mine.m as Geom);
    const cx = (x0 + x1) / 2;
    const cz = -(y0 + y1) / 2;
    target.set(cx, 42, cz);
    eyePlane.position.set(cx, 0, cz);
    eyeRim.position.set(cx, 0, cz);
    wedge.position.set(cx, 0.4, cz);
  }

  /* ------------------------------------------------------------ interaction */

  function place() {
    camera.position.set(
      target.x + orbit.radius * Math.sin(orbit.phi) * Math.sin(orbit.theta),
      target.y + orbit.radius * Math.cos(orbit.phi),
      target.z + orbit.radius * Math.sin(orbit.phi) * Math.cos(orbit.theta),
    );
    camera.lookAt(target);
  }

  function size() {
    const w = canvasEl.clientWidth;
    const h = canvasEl.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  /**
   * Keep the street names readable whichever way the view is turned.
   *
   * A name painted along a road runs one way in the world, and once the
   * camera orbits past it you are reading it from the far end: it comes out
   * upside down, which looks like a rendering fault rather than a label. Map
   * renderers flip the text instead, and so does this — whichever of the two
   * directions along the road reads left to right on screen is the one it
   * takes. A half turn about y, decided per frame, costs nothing.
   */
  function faceLabels() {
    if (!streetLabels) return;
    const ahead = new THREE.Vector3();
    camera.getWorldDirection(ahead);
    // The screen's rightward axis, flat on the ground.
    const right = new THREE.Vector3(-ahead.z, 0, ahead.x).normalize();
    for (const mesh of streetLabels.children) {
      const base = mesh.userData.angle as number;
      const along = new THREE.Vector3(Math.cos(base), 0, -Math.sin(base));
      mesh.rotation.y = along.dot(right) < 0 ? base + Math.PI : base;
    }
  }

  const draw = () => {
    faceLabels();
    renderer.render(world, camera);
  };

  const zoom = (factor: number) => {
    orbit.radius = Math.min(980, Math.max(130, orbit.radius * factor));
  };
  const turn = (dx: number, dy: number) => {
    orbit.theta -= dx * 0.006;
    // Stop short of the horizon: below about 1.3 the eye drops under the
    // skyline and the whole frame is ground.
    orbit.phi = Math.min(1.3, Math.max(0.16, orbit.phi - dy * 0.005));
  };

  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  let touch = false;

  const onDown = (e: PointerEvent) => {
    dragging = true;
    touch = e.pointerType === 'touch';
    lastX = e.clientX;
    lastY = e.clientY;
    if (!touch) canvasEl.setPointerCapture(e.pointerId);
  };
  const onMove = (e: PointerEvent) => {
    if (!dragging) {
      hover(e);
      return;
    }
    // On a touch screen the canvas keeps `touch-action: pan-y`, so a vertical
    // swipe scrolls the page as it would anywhere else and only the sideways
    // part of the gesture turns the model. Tilting is the two-finger gesture.
    turn(e.clientX - lastX, touch ? 0 : e.clientY - lastY);
    lastX = e.clientX;
    lastY = e.clientY;
    place();
    draw();
  };
  const onUp = (e: PointerEvent) => {
    dragging = false;
    if (!touch && canvasEl.hasPointerCapture(e.pointerId)) {
      canvasEl.releasePointerCapture(e.pointerId);
    }
  };
  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    zoom(1 + e.deltaY * 0.0012);
    place();
    draw();
  };

  let pinch = 0;
  let pinchY = 0;
  const onTouchMove = (e: TouchEvent) => {
    if (e.touches.length !== 2) return;
    e.preventDefault();
    const a = e.touches[0];
    const b = e.touches[1];
    if (!a || !b) return;
    const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const midY = (a.clientY + b.clientY) / 2;
    if (pinch) {
      zoom(pinch / dist);
      turn(0, midY - pinchY);
      place();
      draw();
    }
    pinch = dist;
    pinchY = midY;
  };
  const onTouchEnd = () => {
    pinch = 0;
  };

  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let lastHit: string | null = null;
  function hover(e: PointerEvent) {
    const r = canvasEl.getBoundingClientRect();
    pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    ray.setFromCamera(pointer, camera);
    const targets = allowed.visible
      ? [...pickable, ...limitTargets]
      : pickable;
    const hit = ray.intersectObjects(targets, false)[0];
    const key = hit ? String(hit.object.id) : null;
    if (key === lastHit) return;
    lastHit = key;
    setReadout(
      hit
        ? {
            label: String(hit.object.userData.label ?? ''),
            detail: String(hit.object.userData.detail ?? ''),
          }
        : null,
    );
  }
  const onLeave = () => {
    lastHit = null;
    setReadout(null);
  };

  canvasEl.addEventListener('pointerdown', onDown);
  canvasEl.addEventListener('pointermove', onMove);
  canvasEl.addEventListener('pointerup', onUp);
  canvasEl.addEventListener('pointercancel', onUp);
  canvasEl.addEventListener('pointerleave', onLeave);
  canvasEl.addEventListener('wheel', onWheel, { passive: false });
  canvasEl.addEventListener('touchmove', onTouchMove, { passive: false });
  canvasEl.addEventListener('touchend', onTouchEnd);

  const onResize = () => {
    size();
    place();
    draw();
  };
  window.addEventListener('resize', onResize);

  size();
  place();
  draw();

  return {
    summary,
    home() {
      Object.assign(orbit, HOME);
      place();
      draw();
    },
    apply({ layer, storey, facing, sun: at, hiddenZones }: SceneState) {
      allowed.visible = layer === 'worst';
      for (const node of zoning.children) {
        node.visible = !hiddenZones[node.name as ZoneGroup];
      }

      const eye =
        storey !== null && storey >= 1 ? (storey - 1) * storeyM + EYE_HEIGHT_M : null;
      eyePlane.visible = eye !== null;
      eyeRim.visible = eye !== null;
      if (eye !== null) {
        eyePlane.position.y = eye;
        eyeRim.position.y = eye;
      }

      // Anything that reaches above your eye line is what the table above
      // calls a block above you; everything else is skyline.
      for (const t of towers) {
        if (t.mine) continue;
        t.mesh.material = eye !== null && t.top > eye + 0.5 ? matOver : matPlain;
      }
      for (const v of volumes) {
        // Drawing only what breaks the eye line was the obvious way to thin
        // this out and it does nothing: on floor 12 the eye line is 34.5 m
        // and the commonest envelope here is 17 storeys, so all 275 of them
        // clear it. Height does not separate these; distance would.
        v.edges.material =
          eye !== null && v.top > eye + 0.5 ? matLimitEdgeOver : matLimitEdge;
      }
      // The ground steps back while the volumes are up, so the two layers
      // are figure and ground rather than two things at full strength.
      for (const node of zoning.children) {
        const group = node.name as ZoneGroup;
        const material = (node.children[0] as { material?: { opacity: number } })
          ?.material;
        if (material) {
          material.opacity = groundOpacity(group) * (layer === 'worst' ? 0.45 : 1);
        }
      }

      wedge.visible = facing !== null;
      if (facing !== null) {
        // Bearings run clockwise from north; the scene has north at -z, so a
        // bearing of zero is a rotation of zero about the vertical axis.
        wedge.rotation.z = -(BEARINGS[facing] * Math.PI) / 180;
      }

      if (at) {
        const sample =
          meta.sun.track.find((s) => s.month === at.month && s.hour === at.hour) ??
          meta.sun.track.find((s) => s.hour === at.hour) ??
          null;
        const alt = Math.max(sample?.altitude ?? 45, 1) * (Math.PI / 180);
        const az = (sample?.azimuth ?? 250) * (Math.PI / 180);
        sun.position.set(
          900 * Math.cos(alt) * Math.sin(az),
          900 * Math.sin(alt),
          -900 * Math.cos(alt) * Math.cos(az),
        );
        sun.intensity = 1.75 + 2.35 * Math.sin(alt);
        sun.castShadow = true;
        hemi.intensity = 1.8;
      } else {
        // No sun study: a raking fill from nowhere in particular, so the
        // volumes have faces to read without implying a time of day nobody
        // asked about. No shadows, for the same reason.
        sun.position.set(-520, 820, 420);
        sun.intensity = 2.6;
        sun.castShadow = false;
        hemi.intensity = 1.75;
      }

      place();
      draw();
    },
    dispose() {
      canvasEl.removeEventListener('pointerdown', onDown);
      canvasEl.removeEventListener('pointermove', onMove);
      canvasEl.removeEventListener('pointerup', onUp);
      canvasEl.removeEventListener('pointercancel', onUp);
      canvasEl.removeEventListener('pointerleave', onLeave);
      canvasEl.removeEventListener('wheel', onWheel);
      canvasEl.removeEventListener('touchmove', onTouchMove);
      canvasEl.removeEventListener('touchend', onTouchEnd);
      window.removeEventListener('resize', onResize);
      world.traverse((o: unknown) => {
        const node = o as { geometry?: { dispose(): void } };
        node.geometry?.dispose();
      });
      renderer.dispose();
    },
  };
}
