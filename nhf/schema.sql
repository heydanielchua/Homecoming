-- Next Home Finder store. One local SQLite file, filled by the monthly sync
-- job; the web app only ever reads from here, so no page view waits on a
-- government API.

PRAGMA journal_mode = WAL;

-- A block is identified by "BLOCK|STREET NAME", both upper-cased and with
-- whitespace collapsed, because neither HDB dataset carries a block id.
CREATE TABLE IF NOT EXISTS block (
    block_key            TEXT PRIMARY KEY,
    block                TEXT NOT NULL,
    street_name          TEXT NOT NULL,
    town                 TEXT,
    max_floor_lvl        INTEGER,
    year_completed       INTEGER,
    residential          TEXT,
    total_dwelling_units INTEGER,
    units_1room          INTEGER,
    units_2room          INTEGER,
    units_3room          INTEGER,
    units_4room          INTEGER,
    units_5room          INTEGER,
    units_exec           INTEGER,
    units_multigen       INTEGER,
    lat                  REAL,
    lon                  REAL,
    postal               TEXT,
    geocode_source       TEXT,
    geocoded_at          TEXT
);
CREATE INDEX IF NOT EXISTS block_town_idx ON block(town);

CREATE TABLE IF NOT EXISTS resale_txn (
    id                     INTEGER PRIMARY KEY,
    month                  TEXT NOT NULL,          -- YYYY-MM
    quarter                TEXT NOT NULL,          -- YYYY-Qn
    town                   TEXT,
    flat_type              TEXT,
    flat_model             TEXT,
    block                  TEXT,
    street_name            TEXT,
    block_key              TEXT,
    storey_range           TEXT,
    storey_low             INTEGER,
    storey_high            INTEGER,
    floor_area_sqm         REAL,
    lease_commence_year    INTEGER,
    remaining_lease_months INTEGER,
    resale_price           REAL,
    price_per_sqm          REAL,
    source                 TEXT,
    UNIQUE (month, block_key, flat_type, storey_range, floor_area_sqm,
            lease_commence_year, resale_price)
);
CREATE INDEX IF NOT EXISTS resale_block_idx ON resale_txn(block_key, flat_type, month);
CREATE INDEX IF NOT EXISTS resale_town_idx  ON resale_txn(town, flat_type, month);
CREATE INDEX IF NOT EXISTS resale_month_idx ON resale_txn(month);

CREATE TABLE IF NOT EXISTS price_index (
    quarter     TEXT PRIMARY KEY,                  -- YYYY-Qn, 1Q2009 = 100
    index_value REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS town_median (
    quarter      TEXT NOT NULL,
    town         TEXT NOT NULL,
    flat_type    TEXT NOT NULL,
    median_price REAL,
    source       TEXT,                             -- 'hdb' or 'derived'
    PRIMARY KEY (quarter, town, flat_type)
);

CREATE TABLE IF NOT EXISTS rental_txn (
    id           INTEGER PRIMARY KEY,
    month        TEXT NOT NULL,                    -- rent approval month
    town         TEXT,
    block        TEXT,
    street_name  TEXT,
    block_key    TEXT,
    flat_type    TEXT,
    monthly_rent REAL,
    UNIQUE (month, block_key, flat_type, monthly_rent)
);
CREATE INDEX IF NOT EXISTS rental_block_idx ON rental_txn(block_key, flat_type);
CREATE INDEX IF NOT EXISTS rental_town_idx  ON rental_txn(town, flat_type);

CREATE TABLE IF NOT EXISTS office (
    code  TEXT PRIMARY KEY,
    label TEXT,
    postal TEXT,
    address TEXT,
    lat   REAL,
    lon   REAL
);

-- Drive time from a block to each office. status says why a row is empty, so
-- a missing OneMap token never looks like a fast commute.
CREATE TABLE IF NOT EXISTS commute (
    block_key     TEXT NOT NULL,
    office_code   TEXT NOT NULL,
    drive_minutes REAL,
    distance_m    REAL,
    status        TEXT NOT NULL,                   -- ok | no_token | no_geocode | error
    message       TEXT,
    computed_at   TEXT,
    PRIMARY KEY (block_key, office_code)
);

-- Existing and future MRT stations in one table. Existing come from an
-- imported LTA GeoJSON; future from config/future_mrt.yml.
CREATE TABLE IF NOT EXISTS mrt_station (
    name          TEXT NOT NULL,
    line          TEXT,
    status        TEXT NOT NULL,                   -- open | under_construction | announced
    expected_year INTEGER,
    lat           REAL,
    lon           REAL,
    source        TEXT,
    PRIMARY KEY (name, line, status)
);

-- Observed lease decay, fitted from the transactions themselves rather than
-- an assumed curve. factor is relative to a 95-year-remaining flat.
CREATE TABLE IF NOT EXISTS lease_factor (
    flat_type   TEXT NOT NULL,
    lease_band  INTEGER NOT NULL,                  -- lower bound, years left
    factor      REAL NOT NULL,
    n           INTEGER NOT NULL,
    fitted_at   TEXT,
    PRIMARY KEY (flat_type, lease_band)
);

-- Observed storey premium, relative to the block-and-type median.
CREATE TABLE IF NOT EXISTS storey_premium (
    flat_type   TEXT NOT NULL,
    storey_band INTEGER NOT NULL,                  -- lower bound of storey range
    factor      REAL NOT NULL,
    n           INTEGER NOT NULL,
    fitted_at   TEXT,
    PRIMARY KEY (flat_type, storey_band)
);

-- Cached per block-and-flat-type aggregates, so the phone never recomputes.
CREATE TABLE IF NOT EXISTS block_metric (
    block_key        TEXT NOT NULL,
    flat_type        TEXT NOT NULL,
    n_txn_24m        INTEGER,
    last_txn_month   TEXT,
    median_price_24m REAL,
    min_price_24m    REAL,
    max_price_24m    REAL,
    median_psm_24m   REAL,
    median_sqm       REAL,
    median_lease_months INTEGER,
    town_median_psm  REAL,
    cagr_5y          REAL,
    cagr_10y         REAL,
    town_cagr_10y    REAL,
    rpi_cagr_10y     REAL,
    computed_at      TEXT,
    PRIMARY KEY (block_key, flat_type)
);

-- Growth score (F4). signals_json keeps every input and its source so a
-- score is never a black box.
CREATE TABLE IF NOT EXISTS block_score (
    block_key     TEXT NOT NULL,
    flat_type     TEXT NOT NULL,
    growth_score  REAL,
    signals_json  TEXT,
    exit_low      REAL,
    exit_mid      REAL,
    exit_high     REAL,
    computed_at   TEXT,
    PRIMARY KEY (block_key, flat_type)
);

-- F7 shortlist. Listings are entered by hand; the app judges them.
CREATE TABLE IF NOT EXISTS listing (
    id             INTEGER PRIMARY KEY,
    url            TEXT,
    block_key      TEXT,
    block          TEXT,
    street_name    TEXT,
    flat_type      TEXT,
    asking_price   REAL,
    storey         INTEGER,
    floor_area_sqm REAL,
    corner_unit    INTEGER DEFAULT 0,
    facing         TEXT,
    status         TEXT DEFAULT 'Watching',        -- Watching|Viewed|Offered|Dropped
    viewing_date   TEXT,
    notes          TEXT,
    eip_checked    INTEGER DEFAULT 0,
    hardcourt_seen INTEGER DEFAULT 0,
    created_at     TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS listing_status_idx ON listing(status);

CREATE TABLE IF NOT EXISTS listing_photo (
    id         INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    caption    TEXT,
    created_at TEXT
);

-- Viewing checklist: what government data cannot tell us.
CREATE TABLE IF NOT EXISTS listing_check (
    listing_id INTEGER NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    item       TEXT NOT NULL,
    answer     TEXT,
    note       TEXT,
    PRIMARY KEY (listing_id, item)
);

-- F8 data freshness.
-- URA Master Plan land use, one row per parcel (113,212 in the 2019 layer).
-- Only a summary of each polygon is kept — centroid and bounding box — which
-- is enough to answer "what sits within a few hundred metres of this block"
-- without shipping a geometry library. The box matters as much as the centre:
-- a large parcel still counts when its centroid is far away.
--
-- gpr_raw and gpr_numeric are deliberately separate. URA writes LND (landed
-- housing), EVA (evaluated at planning stage), NA or SDP where there is no
-- ratio, and those are not the same as missing: landed housing tells you the
-- height, "to be evaluated" is an admission that nobody has decided.
CREATE TABLE IF NOT EXISTS land_parcel (
    id           INTEGER PRIMARY KEY,
    objectid     INTEGER,
    land_use     TEXT,
    gpr_raw      TEXT,
    gpr_numeric  REAL,
    gpr_note     TEXT,
    area_sqm     REAL,
    lat          REAL,
    lon          REAL,
    min_lat      REAL,
    max_lat      REAL,
    min_lon      REAL,
    max_lon      REAL,
    -- The parcel's outline, simplified, as a GeoJSON geometry. The Master
    -- Plan draws edges to fourteen decimal places; this keeps about five
    -- metres of it, which is what the zoning layer on the map is drawn from.
    -- Null where the shape was unusable, which is a parcel that is counted
    -- and measured but not drawn.
    geom         TEXT,
    source       TEXT,
    UNIQUE (objectid, source)
);
-- Proximity is asked as a bounding-box window first, so the box corners are
-- what need indexing, not the centroid.
CREATE INDEX IF NOT EXISTS parcel_box_idx ON land_parcel(min_lat, max_lat, min_lon, max_lon);
CREATE INDEX IF NOT EXISTS parcel_use_idx ON land_parcel(land_use);

-- Everything the report answers "what is near this block, and how far" out
-- of: hawker centres, parks, park facilities, MRT station exits, bus stops,
-- schools and pre-schools. One table because the question is always the same
-- shape, and the `kind` says which layer it came from.
--
-- Shapes are summarised the same way as land_parcel: centroid plus box. For a
-- park the box earns its place, because the distance that matters is to the
-- edge, not the middle. For a point layer the box is the point.
--
-- An MRT exit is a row of its own rather than a station, because what you
-- walk to is an exit: at a long station the two differ by a couple of hundred
-- metres, and the station centroid can sit under a road you cannot cross.
--
-- postal and geocoded_at exist for the one layer that arrives without
-- coordinates. MOE publishes 337 schools with a postal code and no geometry,
-- so they are loaded flat and then geocoded like the blocks were; until that
-- runs, lat and lon are null and the report says the school distance is
-- unknown rather than guessing one.
CREATE TABLE IF NOT EXISTS amenity (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,          -- hawker_centre | park | park_facility |
                                      -- mrt_exit | bus_stop | school | preschool
    subtype   TEXT,                   -- facility class, reserve type, school level
    name      TEXT,
    detail    TEXT,
    status    TEXT,                   -- hawker centres: six are not built yet
    size_hint REAL,                   -- stall count, or park area
    lat       REAL,
    lon       REAL,
    min_lat   REAL,
    max_lat   REAL,
    min_lon   REAL,
    max_lon   REAL,
    postal    TEXT,
    geocoded_at TEXT,
    source    TEXT,
    UNIQUE (kind, name, lat, lon)
);
CREATE INDEX IF NOT EXISTS amenity_kind_idx ON amenity(kind, subtype);
CREATE INDEX IF NOT EXISTS amenity_box_idx  ON amenity(min_lat, max_lat, min_lon, max_lon);

-- One row per rainfall station per year, folded down from roughly seven
-- million five-minute readings. The readings themselves are never stored:
-- the only question they answer is whether one part of the island is wetter
-- than another, and 89 summaries answer it.
CREATE TABLE IF NOT EXISTS rainfall_station (
    id            INTEGER PRIMARY KEY,
    station_id    TEXT NOT NULL,
    name          TEXT,
    lat           REAL,
    lon           REAL,
    year          INTEGER NOT NULL,
    total_mm      REAL,
    readings      INTEGER,
    wet_readings  INTEGER,
    rain_days     INTEGER,
    days_observed INTEGER,            -- days the gauge reported at all
    source        TEXT,
    UNIQUE (station_id, year)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY,
    dataset_key TEXT NOT NULL,
    resource_id TEXT,
    label       TEXT,
    status      TEXT NOT NULL,                     -- ok | skipped | error
    rows        INTEGER,
    started_at  TEXT,
    finished_at TEXT,
    message     TEXT
);
CREATE INDEX IF NOT EXISTS sync_log_key_idx ON sync_log(dataset_key, finished_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
