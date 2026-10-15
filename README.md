# Listing Check

A private website for the two of us. Paste a link from PropertyGuru or 99.co,
add the asking price and which floor, and it reports everything the government
knows about that flat. One listing at a time.

The product brief this is built from is [`docs/PRD.md`](docs/PRD.md).

```
paste a link  →  resolve the block  →  show the report
                                    →  save it  →  compare saved units
```

The portals show what a seller is asking. They do not show what flats in that
block actually sold for, what they rent for, how much lease is left, what is
about to be built next door, or whether the flat bakes in the afternoon sun.
Working that out per listing takes an evening.

## How it works

```
monthly batch job  →  SQLite  →  one small JSON per block  →  static host
                                        ↓
                                  browser: look up the pasted block,
                                  apply the typed price/floor/facing,
                                  save to browser storage
```

A static website. No login, no sign-up, no server, no runtime database. The
site holds nothing about us — no budget, no offices, no stored shortlist — so
there is nothing to put a password in front of, and being static is what makes
it free to host.

Nothing is scraped. A listing page is never fetched; only the text of the link
is read.

## Getting started

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm install)

export DATAGOV_API_KEY=...          # optional, raises the rate limit

nhf init                            # create data/nhf.db
nhf sync                            # download everything (slow the first time)
nhf sync --step land_use            # 174 MB, only when URA revises the plan
nhf sync --step rainfall            # a sampled year of gauge readings
nhf build --curves-only             # fit the lease and storey curves
./scripts/build_site.sh dist        # the page, then the data beside it

python3 -m http.server --directory dist 8765
```

`nhf sync` downloads about 663,000 resale transactions back to 2000 and
geocodes roughly 11,000 block addresses through OneMap, so the first run takes
a couple of hours. Later runs only geocode blocks that are new.

### Monthly refresh

```cron
15 4 1 * *  cd /srv/listing-check && .venv/bin/nhf sync && .venv/bin/nhf build --curves-only \
            && ./scripts/build_site.sh dist
```

The page shows how old the data is and says so plainly past 45 days.

## Trying it from the terminal

The whole report is available without the site, which is also how a change to
the maths gets checked before it is published:

```bash
nhf resolve 'https://www.99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-eSeF'
nhf report '669C|JURONG WEST ST 64' --storey 12 --facing W --asking 620000
nhf report 'https://www.propertyguru.com.sg/listing/hdb-for-sale-266b-punggol-way-1' --json
```

## Resolving a link

Everything hangs off this, so it is built to fail honestly rather than to
always answer. Both portals put the address in the URL path:

```
99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-eSeF
propertyguru.com.sg/listing/hdb-for-sale-266b-punggol-way-25099211
```

Two things make it harder than reading the slug. **Portal and HDB spell
streets differently** — the portals write `JURONG WEST STREET 64` and HDB
writes `JURONG WEST ST 64`, and the same goes for AVENUE/AVE, DRIVE/DR,
CRESCENT/CRES, CENTRAL/CTRL, BUKIT/BT, JALAN/JLN. And **block numbers are not
unique**: `266B` is both `266B|COMPASSVALE BOW` and `266B|PUNGGOL WAY`, so the
street is load-bearing and a block number on its own is a question.

The approach is an alias index rather than a parser clever enough to be
trusted. Every block is written out under every spelling a portal might
plausibly use, the pasted link is reduced to the same shape, and resolution is
an exact dictionary lookup. That inverts where the uncertainty sits: a guess at
expanding an abbreviation cannot produce a *wrong* block, only an alias no
block claims — which is a miss rather than a lie. Against the real data that is
26,501 aliases over 10,817 blocks with no collisions.

Two traps the real street list contains, both of which a tidy-looking
abbreviation table would have walked into. `ST` is STREET in 166 street names
and SAINT in two (`ST GEORGE'S RD`, `ST GEORGE'S LANE`), and the position does
not settle it, since `UPP CROSS ST` ends with the STREET sense — so both are
indexed and the one nobody links to is never looked up. And `RIS` appears in
15 street names, every one of them `PASIR RIS`; reading it as an abbreviation
of RISE would mint a street nobody lives on.

## How sure each number is

Every figure carries its confidence, because mixing these is how a tool starts
lying:

- **Fact** — it sold for $473,000; the next block is 16 storeys; the hawker
  centre is 324 m away.
- **Worked out** — the fair-value range, the sun angle, distance to a park's
  edge.
- **Estimated** — a height band from a plot ratio. Shown as a range, with the
  ratio it came from.
- **Unknown, and why** — never a blank, never a zero. A layer that has not been
  loaded says so; it does not report "no hawker centre nearby".

## How the numbers are made

**Fair value, per storey band.** Comparable sales in the same block over the
last 24 months, widened to blocks within 800 m when a block is too thin. Each
comparable is adjusted three ways before the range is taken — to today's market
with the HDB Resale Price Index, to the target floor with a fitted storey
premium, and to the target lease with a fitted lease curve — and the 20th /
50th / 80th percentiles become low / mid / high.

It is precomputed **per five-floor band** rather than per floor, and that is
not a rounding. The storey premium is *fitted* in five-floor bands, so a band
is exactly as fine as the evidence goes; one value per band is the whole
answer. That is what lets the browser apply a typed floor without redoing the
valuation, which keeps the arithmetic in one language.

**The fitted curves.** Lease decay and the storey premium are read out of the
transactions rather than assumed: each sale's price per sqm is divided by the
median for its town, flat type and quarter, which removes location and the
market cycle, and the median of what is left inside each band becomes the
factor. Cohorts with fewer than eight sales are dropped, because a cohort of
one makes every ratio exactly 1.0 and flattens the curve, and the lease curve
is forced monotone since a shorter lease is never worth more per sqm.

The lease curve understates true decay, and the code says so where it is
fitted: HDB towns were built in waves, so lease length is partly collinear with
town, and dividing by a town cohort removes some of the lease signal along with
the location signal. It is a floor on observed decay. `nhf fit` prints it.

**The trend is mix-adjusted, and indexed.** Each quarterly point is a trailing
year, drawn only where that year holds three sales, with every sale divided by
its own storey and lease factors. Without that, a block that happened to sell
two high floors in one window and two low ones in the next looks like it fell
ten per cent and recovered — and reading that as movement is the single easiest
mistake to make with this data.

The chart puts the block, its town and the national index on **one axis by
indexing all three to 100** at their first shared quarter. Dollars per square
metre and an index on a 2009 base are different scales, and a two-axis chart
would let whoever drew it decide which line looked higher.

**View and sun.** HDB publishes `max_floor_lvl` for every block, so what stands
around a block and how tall is fact, not inference. What blocks a *view* is
another flat above your eye line; what shades the *sun* is the roof above it,
and the two are a storey apart — comparing eyes with roofs reports a blocked
view from half the flats on an estate.

Where the sun is comes from the NOAA solar position algorithm, checked against
the sky rather than against itself: 65.2° at the December solstice, 67.9° at
the June one, sunset swinging from 247° in December to 293° in June. Singapore
sits 1.35° north, so the midday sun is nearly overhead all year and the heat
problem is the low western sun from about four o'clock. In hours of low
afternoon sun a year, an unobstructed face gets:

| E, SE, NE | N | S | SW, NW | W |
| --- | --- | --- | --- | --- |
| 0 | 94 | 97 | 470 | 667 |

which is "west sun" in numbers. The useful part is the combination: a tall
block to the west ruins the view and saves the afternoons.

**What could still be built.** URA publishes zoning and gross plot ratio, and
no height at all — `GPR_B_MN`, their own band field, is filled in on 52 parcels
out of 113,212. A ratio is turned into storeys by reading it off the blocks
already built at that ratio: the band is the 10th to 90th percentile of their
storey counts, with the number of blocks it rests on attached. Where that fit
is unavailable there is a documented fallback dividing the ratio by a plausible
range of site coverage, and it says which it used.

`LND` and `EVA` are kept apart, because they are not the same kind of blank:
landed housing settles the question (two or three storeys, and it cannot grow
into a view), while "to be evaluated" is an admission that nobody has decided.
Collapsing both into unknown would throw away the better half of a layer where
LND is 52% of parcels.

**Rain is a rank, not a total.** A year of five-minute readings is 6.4 million
rows and data.gov.sg has no server-side aggregation, so the gauges are sampled:
pages spread across the year, each carrying about 60 of the 89 stations, so
every gauge is sampled alike. That supports the comparison between them and
nothing finer.

## The map

The Nearby section opens on a map, because the rows under it answer a
different question from the one people actually ask. A row says *353 m, NE*,
which settles whether something is walkable. It does not settle *what is
around here* — eight compass points is a 45-degree bucket, and the mind
quietly straightens it into a line.

Two layers:

**The places** — the nearest few MRT exits, bus stops, primary schools, hawker
centres, pre-schools and parks, each at the coordinate its source published.
Nothing is derived from the bearing the rows round off, and a place this site
cannot locate is left off the map rather than put somewhere plausible. Each
kind is capped, because a block can have forty bus stops in range and forty
identical pins say nothing.

An area is pinned at the point its distance was measured to, not at its
middle. Kallang Riverside Park is 796 m away along the river and its centroid
is 1,298 m away; a map drawn from centroids puts the pin on the wrong side of
a town and then zooms out far enough to contain the mistake.

**The zoning** — URA's Master Plan 2019 underneath, which is the same question
the "what could still be built" rows answer, in the form it is really asked:
not "a reserve site 212 m east", but "that empty patch I can see". Point at a
parcel for its use and plot ratio. Roads and water are left to the basemap,
which already has them and draws them better.

The zoning is shared rather than per block. A parcel sits near dozens of
blocks, so writing the polygon into each of their reports would store it
dozens of times and make a block file five times its size. Instead the island
is cut into hundredth-of-a-degree cells — about 1.1 km, so a view touches
three or four — and a parcel is written into every cell its bounding box
reaches, carrying URA's id so the browser draws it once however many cells it
loads. 916 cells, 29.7 MB, a median cell of 11.8 KB.

Outlines are simplified to about five metres, which halves the points: the
Master Plan draws edges to fourteen decimal places and no boundary is surveyed
to a tenth of a millimetre. The tolerance adapts downward for small parcels,
because a flat five metres erases an eight-metre plot outright — that bug cost
4,331 residential parcels and showed up as holes in the ground wash rather
than as an error.

Four hues and two neutrals. Six did not survive validating every pair for
colour-vision deficiency, and a zoning map is all-pairs by nature because any
two zones can end up side by side. Residential is the neutral ground rather
than a colour: it is seven parcels in ten, colouring the majority says
nothing, and it has to recede for the other five to read.

The basemap is OneMap, the Singapore Land Authority's own — the only one with
the right block numbers and no usage key to leak into a public repository. Its
Night style stands in for dark mode. If the tiles are blocked the pins still
sit in the right places and the distances never depended on them, and the map
says so rather than showing a silent grey square.

## Comparing saved units

Saved units compare in a table with **units as columns and attributes as
rows**. Two to five flats against about twenty-five attributes would be an
unreadable twenty-five-column table the other way round; transposed it is tall
and narrow and each row compares one thing. Rows group into collapsible
sections and each row marks the better value, so the table is scanned rather
than read. There is no overall winner and no score — see below.

**Only the inputs are saved**, never the report: the link, block, asking price,
floor, facing and any note. The report is recomputed on open, so a unit saved
in January shows today's figures rather than January's.

Saved units live in the browser that saved them. That is the cost of having no
login: one list per device, and sharing a unit means sending a link that
carries its details, which the "Copy share link" button does. If that becomes
annoying, the fix is a server and a password, and that is a decision to make
then.

## Why there is no growth score

An earlier version of this put a 0–100 growth score at the centre and made
passing a backtest a success metric. It was built and it failed. Scored on data
to 2019 and graded on 2019–2024:

```
quintile   median score   realised growth
       1           30.1           8.90%
       5           67.1           8.88%
```

No separation at all. Taken apart, price momentum — the heaviest weight —
predicted *backwards*, monotonically. A weighting of 0 / 60 / 40 across
momentum, catch-up and lease runway did separate, by +1.62 points and
monotonically, but a score that needs the evidence of a backtest to be believed
does not belong at the top of a page where a trend line and a fair-value range
say something true. So: no score. The trend is shown instead.

The code that produced it — `nhf/growth.py`, `nhf/ranking.py`,
`nhf/backtest.py`, the Flask app in `nhf/web/` and `nhf export` — is still in
the tree and still passes its tests, but nothing in the site uses it. It is the
superseded product, kept for the evidence rather than for the feature.

## What this does not do

Stated so it is never quietly promised:

- **Dog runs.** The park facilities layer has a `DOG-AREA` class with three
  entries for the whole country against a real number in the dozens.
- **Which buses stop where.** Stop locations are free; the services calling at
  each need LTA DataMall and an account key.
- **Whether a road floods.** No historical dataset exists. PUB's flood alerts
  are live-only; the one route in is to start recording them now.
- **Exact permitted heights.** URA publishes plot ratio, not height.
- **Scraping the portals.** Against their terms, and unnecessary.

## Commands

| Command | What it does |
| --- | --- |
| `nhf sync [--step resale] [--limit N]` | Download the datasets, geocode blocks and schools |
| `nhf build [--curves-only]` | Fit the lease and storey curves; `--curves-only` skips the superseded metrics and score |
| `nhf fit` | Print the fitted curves, to eyeball before trusting a valuation |
| `nhf resolve <url>` | Turn a pasted link into a block, or fail with a reason |
| `nhf report <url or block key> [--storey 12] [--facing W] [--asking 620000]` | The whole report in the terminal |
| `nhf site [--out dist/data]` | Render the site's data: one JSON file per block |
| `nhf status` | Data freshness summary |
| `nhf geocode [--blocks] [--schools]` | The OneMap steps on their own |

And the two shell scripts, which wrap the above:

| Command | What it does |
| --- | --- |
| `./scripts/build_site.sh dist` | The page and the data into `dist/` |
| `./scripts/publish.sh [--data]` | Rebuild and publish to the public repository |

`nhf rank`, `nhf value`, `nhf backtest`, `nhf export` and `nhf serve` belong to
the superseded ranked-list product.

## Data sources

Every id has been fetched and parsed. Nothing here is expected to work; it has
worked.

| Source | What it gives |
| --- | --- |
| HDB resale prices, 2000 onwards (3 files) | Sales, comparables, the trend, the fitted curves |
| HDB property information | Storey count per block — the view calculation |
| HDB resale price index | Adjusting to today; the trend benchmark |
| HDB renting out of flats | Rent |
| URA Master Plan 2019 land use | Zoning, plot ratio, height band |
| NEA hawker centres | Hawker centres and stall counts |
| NParks parks and park facilities | Park boundaries, playgrounds, fitness corners, courts |
| LTA MRT station exits | 613 exits — an exit, not a station centroid |
| LTA bus stops | 5,205 stop locations |
| MOE school directory | 337 schools, geocoded from their postal codes |
| ECDA pre-school locations | 2,290 centres |
| NEA historical rainfall | The wetness ranking, by sampling |
| OneMap (SLA) | Geocoding and map tiles. No token needed |

The 2015–2016 resale file has no published resource id, so those two years are
absent. It does not affect anything here.

## Notes for whoever works on this next

Things that cost a day each to discover:

- **data.gov.sg has no working search.** `query=`, `q=`, `search=`, `keyword=`,
  `name=`, `title=` and `agencies=` are all accepted and all ignored.
  `data/datagov_catalogue.csv` mirrors all 4,655 datasets; finding one is a
  local grep. `python scripts/refresh_catalogue.py` re-crawls it.
- **Geospatial datasets are not in the datastore.** `datastore_search` returns
  an empty response for a GeoJSON dataset; they come from
  `api-open.data.gov.sg/.../poll-download`, which hands back a presigned link.
- **Deep pagination times out at large page sizes, not at depth.** `offset=0`
  with `limit=10000` answers in 0.4 s and `offset=140000` with the same limit
  returns 502 — but `offset=3000000` with `limit=1000` answers in six seconds,
  which is what makes the rainfall sampling possible.
- **Some layers are KML converted to GeoJSON.** ECDA's pre-schools put every
  attribute inside one HTML table in a `Description` string, and `Name` is
  "kml_1".
- **OneMap needs no token** for anything used here, and sends
  `Access-Control-Allow-Origin: *`, so the browser can call it directly.
- **The land use layer cannot be committed.** 174 MB, and GitHub blocks files
  over 100 MB. Pin the id, fetch at build time, derive, discard.

## Tests

```bash
python3 -m pytest
(cd web && npm test && npm run typecheck)
```

The fixtures in `tests/conftest.py` are generated, not real: the live datasets
are far too large to check in. They are built with a known lease decay, a known
storey premium, a deliberately cheap block and a deliberately fast-growing one,
so the fit and the scoring can be checked against the right answer rather than
just for running without error.

`tests/golden/` is the other half of that. Three pieces of arithmetic have to
live in both Python and TypeScript, because the floor and the facing are typed
into the browser and the block data is in the batch job: link resolution, the
view geometry, and the sun. Python writes the expected answers with
`python -m tests.make_golden`, and both suites are tested against the same
file — so a change to one side without the other fails a test instead of
producing two plausible numbers. Run it after changing `nhf/sun.py`,
`nhf/view.py` or `nhf/listing.py`, and commit the result.

## Two repositories, and why

A free GitHub Pages site is public and has no login. Everything the site
publishes is derived from public government data and none of it is ours, so
that part is survivable.

`config/criteria.yml` used to hold a real budget and both office postal codes.
It no longer does: it carries worked examples, and real figures go in
`config/criteria.local.yml`, which `.gitignore` keeps out of every commit and
`nhf/config.py` merges over the committed file key by key. Only name what
differs — and naming `offices` at all means naming all of them, because a list
replaces a list rather than appending to it:

```yaml
household:
  budget: 650000
  offices:
    - {code: office_a, label: Mine, postal: "123456"}
```

The same overlay works for any config file, so `datasets.local.yml` is where a
key that should not be committed belongs. That leaves `docs/PRD.md` as the
remaining reason this repository is private. So the split is:

| | holds | visibility |
|---|---|---|
| `Homecoming` (this one) | the code, the PRD, the criteria, the database | **private** |
| the site repository | `dist/` only — the page and the derived data | public, serves Pages |

Nothing crosses that line by hand. `scripts/publish.sh` publishes `dist/` and
only `dist/`, and `nhf.site.verify` runs over the output first and refuses to
publish if a field from the superseded product reappears, or a block report
grows a key nobody meant to publish. There is no path by which a change to
this repository's private files reaches the public one, because the publisher
never reads them — it copies a directory.

### The two manual steps

Neither can be done from here; both are one-time.

1. **Make this repository private** — Settings → General → Danger Zone →
   Change repository visibility. Do this first: until it is done, the budget
   and the postal codes are readable by anyone.
2. **Create the public repository** — new, public, no README, no licence
   (the first publish replaces the whole tree anyway). Then publish once with
   its URL, and the remote is remembered after that:

   ```sh
   ./scripts/publish.sh --remote git@github.com:<you>/<site-repo>.git
   ```

   Then turn Pages on in that repository: Settings → Pages → Deploy from
   branch → `main`, `/ (root)`.

## Iterating on the design

The loop is meant to be short, because the design is the part that will change
most often.

```sh
npm run dev --prefix web      # http://localhost:5173
```

The dev server reads the real `dist/data` off the disk — all 9,222 block
reports — so what is on the screen is a real flat with real figures, not a
fixture. Save a change and the page updates without a reload.

When it looks right:

```sh
./scripts/publish.sh          # about a second, then live in a minute
```

That rebuilds the page and publishes it. **It does not touch the data**, which
is the whole point: the page is three files and half a megabyte, the data is
123 MB across 12,000 of them. A design change uploads the three.

| command | rebuilds | takes |
|---|---|---|
| `./scripts/publish.sh` | the page | seconds |
| `./scripts/publish.sh --data` | the page and every block report | ~45 minutes |
| `./scripts/publish.sh --no-build` | nothing; publishes what is in `dist/` | seconds |
| `./scripts/publish.sh --dry-run` | builds and stages, never pushes | seconds |

The data only needs `--data` when the database has changed, which is once a
month — and `.github/workflows/publish.yml` does that on a schedule, so in
practice you never type it.

**The public repository keeps no history.** Each publish force-pushes a single
orphan commit. That is deliberate: with ordinary history a monthly refresh
would add 123 MB of objects permanently, git never forgets, and the repository
would pass GitHub's one-gigabyte guideline inside a year. The public
repository is a deployment artifact; the record is this repository, which has
the code that produced it. Re-publishing unchanged files is still cheap —
the new commit names the same blobs, the remote already has them, and only
what actually changed goes over the wire.

## The monthly refresh

`.github/workflows/publish.yml` runs `sync` → `build --curves-only` →
`build_site.sh` → `publish.sh --no-build` on the 1st, and on demand. Two
settings it needs, because a workflow cannot grant itself access to another
repository:

- secret `PUBLIC_SITE_TOKEN` — a fine-grained PAT with *Contents: read and
  write* on the public repository only.
- variable `PUBLIC_SITE_REPO` — `<owner>/<site-repo>`.

Without them the job stops at its first step and says which is missing, rather
than half-publishing. It caches `data/nhf.db` (283 MB) between runs, which
matters more than it looks: the database carries the geocodes, and finding
those takes about two hours through OneMap and never needs doing twice.

Two layers sit out of the monthly run, and `nhf sync` leaves them out unless
asked for by name — the land use plan, which is 174 MB of a document revised
every few years, and rainfall, which is 704 pages of five-minute readings
behind a figure that moves once a year. Both are already in the cached
database. To re-download them, dispatch the workflow with **Also re-download
land use and a year of rainfall** ticked, or locally:

```sh
nhf sync --step land_use --step rainfall
```
