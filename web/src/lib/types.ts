/**
 * The shapes `nhf site` writes.
 *
 * These mirror `nhf/report.py` and `nhf/site.py`. They are written by hand
 * rather than generated, because the generated version of a wire format
 * tends to be believed more than it deserves — what is here is what the
 * browser actually reads, and anything it gets wrong shows up as a type
 * error against the golden fixtures in `tests/golden`.
 */

/** How sure a figure is. The four levels the PRD insists on. */
export type Confidence = 'fact' | 'worked' | 'estimated' | 'unknown';

/**
 * One figure, with its confidence attached.
 *
 * `v` is null exactly when `c` is `unknown`, and `n` then holds the reason —
 * never a blank and never a zero.
 */
export interface Figure<T = number> {
  v: T | null;
  c: Confidence;
  /** Why, or what it was worked out from. */
  n?: string;
  /** The unit, where one would otherwise have to be guessed. */
  u?: string;
}

export interface Sale {
  month: string;
  storey: string;
  sqm: number | null;
  price: number;
  psm: number | null;
  lease_months: number | null;
}

export interface FairValue {
  low: number | null;
  mid: number | null;
  high: number | null;
  psm_mid: number | null;
  n_comps: number;
  scope: 'same_block' | 'nearby_blocks' | 'town' | 'none';
  notes: string[];
}

export interface Money {
  n_sales: Figure;
  last_sale?: Figure<string>;
  median_price: Figure;
  low_price?: Figure;
  high_price?: Figure;
  median_psm: Figure;
  median_sqm?: Figure;
  town_median_psm: Figure;
  vs_town_pct: Figure;
  /** Keyed by the lower bound of the five-floor storey band: "1", "6", "11". */
  fair_value: Record<string, FairValue>;
  sales: Sale[];
  thin: boolean;
}

export interface TrendPoint {
  quarter: string;
  psm: number;
  n: number;
}

export interface Trend {
  block: TrendPoint[];
  adjusted: boolean;
  note: string;
  window_quarters: number;
  min_sales: number;
}

export interface Rent {
  median: Figure;
  low?: Figure;
  high?: Figure;
  n?: Figure;
  scope: 'block' | 'nearby' | 'town' | null;
  latest_month?: string;
}

export interface FlatTypeSection {
  money: Money;
  trend: Trend;
  rent: Rent;
}

export interface Lease {
  commence_year: Figure;
  remaining_years: Figure;
  remaining_months?: number | null;
}

export interface NeighbourData {
  name: string;
  distance_m: number;
  /** Where the block itself is. This is what the table's direction shows. */
  bearing: number;
  direction: string;
  storeys: number | null;
  /**
   * How much horizon it really covers, measured from its footprint, and where
   * that coverage is centred. Absent for a block whose footprint did not
   * join, where the nominal slab in `view.ts` stands in.
   */
  covers_deg?: number;
  covers_centre?: number;
  covers_basis?: 'footprint' | 'nominal';
}

export interface HeightBand {
  low: number | null;
  high: number | null;
  basis: 'fitted' | 'coverage' | 'zoning' | 'none';
  note: string;
  gpr: number | null;
  n_blocks: number | null;
}

export interface ParcelData {
  land_use: string | null;
  distance_m: number;
  bearing: number;
  direction: string;
  area_sqm: number | null;
  gpr: number | null;
  gpr_note: string | null;
  band: HeightBand;
  buildable: boolean;
}

export interface Surroundings {
  /** Whether the block has coordinates. An empty neighbour list means
   *  "nothing within 300 m" when it does and "nothing measured" when it
   *  does not. */
  located: boolean;
  max_floor: Figure;
  neighbours: NeighbourData[];
  neighbours_taller: number;
  neighbour_radius_m: number;
  neighbour_note: string;
  parcels: ParcelData[];
  parcels_loaded: boolean;
  height_bands_fitted: number;
  view_without_floor: ViewData;
}

export interface BlockedNeighbourData extends NeighbourData {
  rises_m: number;
  elevation_deg: number;
  storeys_above: number;
}

export interface ViewData {
  storey: number | null;
  eye_height_m: number | null;
  unknown: string | null;
  blocked: BlockedNeighbourData[];
  clear: NeighbourData[];
  blocked_arc_deg: number;
  open_directions: string[];
  above_everything: boolean;
}

export interface Place {
  name: string | null;
  distance_m: number;
  direction: string;
  subtype?: string;
  detail?: string;
  status?: string;
  size?: number;
  to_edge?: boolean;
}

/** A Master Plan parcel, as the zoning layer draws it. */
export interface ZoningParcel {
  /** URA's parcel id. A parcel reaches several cells; it is drawn once. */
  i: number;
  /** The group whose colour it takes. */
  g: ZoneGroup;
  /** URA's own words for the use, for the tooltip. */
  u: string;
  /** Plot ratio, where URA fixed one. Absent means not evaluated. */
  r?: number;
  /** A GeoJSON Polygon or MultiPolygon, simplified to about five metres. */
  m: { type: 'Polygon' | 'MultiPolygon'; coordinates: number[][][] | number[][][][] };
}

export interface ZoningCell {
  cell: [number, number];
  parcels: ZoningParcel[];
}

/**
 * One HDB building, as the footprint layer draws it. Written per cell on the
 * same grid as the zoning, so the massing view fetches matching keys from
 * both directories.
 */
export interface Building {
  /** Postal code: the footprint layer's key, and unique in it. */
  i: string;
  /** Block number, for the label. */
  b: string;
  /**
   * Storeys, not a height in metres. HDB publishes the count and nobody
   * publishes the height, so the three-metre assumption stays where a reader
   * can see it rather than being baked in upstream.
   */
  s: number | null;
  /** The block's own slug, so the view can find the flat's building. */
  slug: string;
  m: { type: 'Polygon' | 'MultiPolygon'; coordinates: number[][][] | number[][][][] };
}

export interface BuildingCell {
  cell: [number, number];
  buildings: Building[];
}

export const ZONE_GROUPS = [
  'residential',
  'commercial',
  'business',
  'park',
  'reserve',
  'civic',
  // Ground rather than a zoning decision, and drawn only by the view that
  // has no basemap under it. Mirrors ZONE_GROUPS in nhf/site.py.
  'road',
  'water',
] as const;

export type ZoneGroup = (typeof ZONE_GROUPS)[number];

/** One thing the map draws, at the coordinate the source geocoded it to. */
export interface MapPoint {
  kind: PointKind;
  name: string | null;
  lat: number;
  lon: number;
  distance_m: number;
  subtype?: string;
}

export const POINT_KINDS = [
  'mrt_exit',
  'bus_stop',
  'school',
  'hawker_centre',
  'preschool',
  'park',
] as const;

export type PointKind = (typeof POINT_KINDS)[number];

export interface Nearby {
  mrt_exit: Place | null;
  mrt_station: string | null;
  bus_stop: Place | null;
  hawker: Place | null;
  hawker_opening: Place | null;
  park: Place | null;
  primary_school: Place | null;
  schools_within_1km: number | null;
  secondary_school: Place | null;
  secondary_within_2km: number | null;
  preschools_within_walk: number | null;
  facilities: Record<string, number>;
  nearest_facility: Record<string, Place>;
  points: MapPoint[];
  missing: string[];
}

export interface Rain {
  station: string | null;
  station_id: string | null;
  distance_m: number | null;
  rank: number | null;
  of: number | null;
  wetter_than_pct: number | null;
  mm_per_1000_readings: number | null;
  year: number | null;
  unknown: string | null;
}

export interface BlockReport {
  block_key: string;
  block: string;
  street: string;
  town: string;
  lat: number | null;
  lon: number | null;
  postal: string | null;
  year_completed: number | null;
  dwelling_units: number | null;
  lease: Lease;
  flat_types: Record<string, FlatTypeSection>;
  surroundings: Surroundings;
  nearby: Nearby;
  rain: Rain;
  as_of: { month: string | null; quarter: string | null; built_at: string };
  slug: string;
  town_slug: string | null;
}

export interface TownReport {
  town: string;
  slug: string;
  flat_types: Record<string, { trend: TrendPoint[]; median_psm: number | null }>;
  as_of: { month: string | null; quarter: string | null };
}

export interface TrackSample {
  month: number;
  hour: number;
  azimuth: number;
  altitude: number;
  weight: number;
}

export interface ExposureData {
  facing: string;
  bearing: number;
  hours: number;
  low_hours: number;
  blocked_hours: number;
  blocked_low_hours: number;
  net_low_hours: number;
  shaded_by: Record<string, number>;
  peak_month: number | null;
}

export interface SunConstants {
  low_sun_altitude: number;
  min_altitude: number;
  face_half_angle: number;
  storey_height_m: number;
  nominal_block_length_m: number;
  blocked_slack_m: number;
}

export interface Meta {
  built_at: string;
  as_of: { month: string | null; quarter: string | null };
  national_index: { quarter: string; index: number }[];
  index_note: string;
  sun: {
    lat: number;
    lon: number;
    note: string;
    track: TrackSample[];
    open_face: Record<string, ExposureData & { says: string }>;
    constants: SunConstants;
  };
  curves: {
    lease: Record<string, Record<string, number>>;
    storey: Record<string, Record<string, number>>;
    storey_band_floors: number;
    note: string;
  };
  height_bands: Record<string, { low: number; high: number; n: number }>;
}

export interface IndexStreet {
  street: string;
  slug: string;
  town: string | null;
  /** Street spellings only: the block number is the file name. */
  aliases: string[];
  report: boolean;
}

export interface IndexShard {
  block: string;
  streets: IndexStreet[];
}

export interface Manifest {
  built_at: string;
  as_of: { month: string | null; quarter: string | null };
  counts: Record<string, number>;
  report_window_months: number;
  towns: string[];
  flat_types: string[];
  facings: string[];
  datasets: {
    dataset: string;
    label: string | null;
    status: string;
    rows: number | null;
    at: string | null;
    message: string | null;
  }[];
  collisions: Record<string, string[]>;
  sources: string;
}

/** The compass points, in the order a person reads them off a plan. */
export const FACINGS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;
export type Facing = (typeof FACINGS)[number];

/**
 * What we type. The only four things that cannot be looked up, plus a note.
 *
 * This is also exactly what is saved: the report is never stored, so a unit
 * saved in January shows today's figures when it is opened in June.
 */
export interface SavedUnit {
  id: string;
  /** The link as pasted, kept so the listing can be reopened. */
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
  savedAt: string;
}
