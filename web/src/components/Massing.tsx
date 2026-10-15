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
import type { Building, Facing, Meta, ZoningParcel } from '../lib/types';

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

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

type Layer = 'now' | 'worst';

interface Props {
  meta: Meta;
  lat: number;
  lon: number;
  slug: string;
  storey: number | null;
  facing: Facing | null;
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

export function Massing({ meta, lat, lon, slug, storey, facing }: Props) {
  const host = React.useRef<HTMLDivElement | null>(null);
  const canvas = React.useRef<HTMLCanvasElement | null>(null);
  const scene = React.useRef<SceneHandle | null>(null);

  const [wanted, setWanted] = React.useState(false);
  const [data, setData] = React.useState<Loaded | null>(null);
  const [failed, setFailed] = React.useState<string | null>(null);
  const [layer, setLayer] = React.useState<Layer>('now');
  const [sunOn, setSunOn] = React.useState(false);
  const [hour, setHour] = React.useState(17);
  const [month, setMonth] = React.useState(12);
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
    });
  }, [layer, storey, facing, sunOn, month, hour, built]);

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

        <label className="massing-check">
          <input
            type="checkbox"
            checked={sunOn}
            onChange={(e) => setSunOn(e.target.checked)}
          />
          Afternoon sun
        </label>

        {sunOn ? (
          <div className="massing-sliders">
            <label>
              <span>
                Month <b>{MONTHS[month - 1]}</b>
              </span>
              <input
                type="range"
                min={1}
                max={12}
                value={month}
                onChange={(e) => setMonth(Number(e.target.value))}
              />
            </label>
            <label>
              <span>
                Time <b>{clockOf(hour)}</b>
              </span>
              <input
                type="range"
                min={0}
                max={hours.length - 1}
                value={Math.max(0, hours.indexOf(hour))}
                onChange={(e) => setHour(hours[Number(e.target.value)] ?? 17)}
              />
            </label>
          </div>
        ) : null}

        <button
          type="button"
          className="massing-reset"
          onClick={() => scene.current?.home()}
        >
          Reset the view
        </button>
      </div>

      <p className="muted massing-legend">
        {eye === null
          ? 'Type a floor and the plane through the model becomes your eye line: anything above it can block the view.'
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

  const hemi = new THREE.HemisphereLight(0xffffff, 0x9a948c, 0.78);
  world.add(hemi);
  const sun = new THREE.DirectionalLight(0xfff6e0, 0.9);
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
    new THREE.MeshLambertMaterial({ color: colour('--massing-ground', '#c9c6bf') }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  world.add(ground);

  // --- what stands ---------------------------------------------------------
  const mineTone = colour('--massing-mine', '#d6ff4e');
  const plainTone = colour('--massing-built', '#eae7e1');
  const overTone = colour('--massing-over', '#c3bdb1');
  const matMine = new THREE.MeshLambertMaterial({ color: mineTone });
  const matPlain = new THREE.MeshLambertMaterial({ color: plainTone });
  const matOver = new THREE.MeshLambertMaterial({ color: overTone });
  const matEdge = new THREE.LineBasicMaterial({
    color: colour('--massing-line', '#8f8a80'),
    transparent: true,
    opacity: 0.8,
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
  const ghostTone = colour('--massing-ghost', '#eb6834');
  const matGhost = new THREE.MeshLambertMaterial({
    color: ghostTone, transparent: true, opacity: 0.26, depthWrite: false,
  });
  const matGhostOver = new THREE.MeshLambertMaterial({
    color: ghostTone, transparent: true, opacity: 0.44, depthWrite: false,
  });
  const matGhostEdge = new THREE.LineBasicMaterial({
    color: ghostTone, transparent: true, opacity: 0.6,
  });

  const allowed = new THREE.Group();
  allowed.visible = false;
  world.add(allowed);

  const volumes: { mesh: InstanceType<typeof THREE.Mesh>; top: number }[] = [];
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
    const geom = extrude(shapes, e.topM - e.baseM);
    geom.translate(0, e.baseM, 0); // start at the roofline, not the ground
    const mesh = new THREE.Mesh(geom, matGhost);
    mesh.userData = { label: e.label, detail: e.detail };
    allowed.add(mesh);
    allowed.add(new THREE.LineSegments(new THREE.EdgesGeometry(geom, 25), matGhostEdge));
    volumes.push({ mesh, top: e.topM });
  }
  const summary = found.summary;

  // --- the eye line --------------------------------------------------------
  // A level marker, not a floor. At the scene's full radius this became a
  // translucent sheet across the whole frame with the city hidden behind it;
  // what it has to say is "your eyes are at this height", and a disc around
  // your own block says that without walling the view off.
  const EYE_DISC_M = 170;
  const eyeMat = new THREE.MeshBasicMaterial({
    color: colour('--massing-eye', '#010101'),
    transparent: true,
    opacity: 0.1,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const eyePlane = new THREE.Mesh(new THREE.CircleGeometry(EYE_DISC_M, 64), eyeMat);
  eyePlane.rotation.x = -Math.PI / 2;
  eyePlane.visible = false;
  world.add(eyePlane);

  const eyeRim = new THREE.LineLoop(
    new THREE.BufferGeometry().setFromPoints(
      Array.from({ length: 65 }, (_, i) => {
        const a = (i / 64) * Math.PI * 2;
        return new THREE.Vector3(Math.cos(a) * EYE_DISC_M, 0, Math.sin(a) * EYE_DISC_M);
      }),
    ),
    // The rim takes the colour of the block it belongs to: this line is
    // where *your* eyes are, not a generic contour.
    new THREE.LineBasicMaterial({ color: mineTone }),
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

  const draw = () => renderer.render(world, camera);

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
      ? [...pickable, ...volumes.map((v) => v.mesh)]
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
    apply({ layer, storey, facing, sun: at }: SceneState) {
      allowed.visible = layer === 'worst';

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
        v.mesh.material = eye !== null && v.top > eye + 0.5 ? matGhostOver : matGhost;
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
        sun.intensity = 0.45 + 0.55 * Math.sin(alt);
        sun.castShadow = true;
        hemi.intensity = 0.72;
      } else {
        // No sun study: a raking fill from nowhere in particular, so the
        // volumes have faces to read without implying a time of day nobody
        // asked about. No shadows, for the same reason.
        sun.position.set(-520, 820, 420);
        sun.intensity = 0.85;
        sun.castShadow = false;
        hemi.intensity = 0.62;
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
