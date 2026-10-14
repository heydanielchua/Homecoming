# Listing Check — PRD

Rewritten 14 Oct 2026. Supersedes the previous PRD entirely; the old one
described a ranked shortlist of every HDB block, which is not what we want.

## What this is

A private website for the two of us. Paste a link from PropertyGuru or
99.co, add the asking price and which floor, and it reports everything the
government knows about that flat. One listing at a time.

**The problem.** The portals show what a seller is asking. They do not show
what flats in that block actually sold for, what they rent for, how much
lease is left, what is about to be built next door, or whether the flat bakes
in the afternoon sun. Working that out per listing takes an evening.

**What it is not.** Not a ranked list of blocks to view — we browse the
portals already, and the gap is judging a specific flat. Not a price
forecast. Not a growth score (see *Why there is no growth score*).

## How it works

```
paste a link  →  resolve the block  →  show the report
                                    →  save it  →  compare saved units
```

1. The URL carries the block and street. Both portals put it in the path:
   `99.co/.../669c-jurong-west-street-64-hdb-<id>` and
   `propertyguru.com.sg/listing/hdb-for-sale-266b-punggol-way-<id>`.
2. That resolves against HDB's own spelling. If it cannot be resolved, the
   site says so and asks for the block and street — it never guesses.
3. Everything else is precomputed per block, so the report is a lookup.

Nothing is scraped. The page is never fetched; only the link text is read.

## What the report says

**Money.** Recent sales in that exact block, a fair-value range from
comparable sales adjusted for floor and lease, and where the asking price
sits against it. Price per sqm against the block's own median and its town's.
How many sales the judgement rests on, so a thin block reads as thin.

**Trend.** What the block has actually done — median price per sqm by
quarter, against its town and the national index. Mix-adjusted, because the
raw medians move with whichever floors happened to sell and reading that as
movement is the single easiest mistake to make here.

**Rent.** Median whole-flat rent for that flat type nearby, because the plan
is to rent it out eventually.

**View and what could block it.** What stands around the block and how tall,
against the floor being considered — HDB publishes the real storey count for
every block, so this is fact, not inference. Then what could *still* go up:
undeveloped or redevelopable land within a few hundred metres, what it is
zoned for, and a height band estimated from its plot ratio.

**Sun.** Which way the flat faces against where the afternoon sun actually
comes from. Singapore sits a degree north of the equator, so the sun passes
close to overhead at noon and the heat problem is the low western sun from
about 4pm — which is why "west sun" is the complaint here and "south sun" is
not. The useful part is the combination: a tall block to the west ruins the
view and saves the afternoons.

**Nearby.** Nearest MRT exit (an exit, not a station centroid — you walk to
an exit), nearest bus stop, nearest hawker centre and how many stalls,
nearest park and how big, nearest primary school and how many sit within
1 km, and the playgrounds, fitness corners and courts in walking distance.

**Rain.** Where the area ranks for wetness against the rest of the island.

## Comparing saved units

Saved units compare in a table with **units as columns and attributes as
rows**. Two to five flats against about twenty-five attributes would be an
unreadable twenty-five-column table the other way round; transposed it is
tall and narrow and each row compares one thing. Rows group into collapsible
sections — money, trend, lease, rent, view and sun, nearby, weather — and
each row marks the better value so the table is scanned rather than read.

**Only the inputs are saved**, never the report: the link, block, asking
price, floor, facing, and any note. The report is recomputed on open, so a
unit saved in January shows today's figures rather than January's.

## What we type

Four things, and the report says plainly which parts depend on each:

| Field | Why it cannot be looked up |
| --- | --- |
| The link | Carries the block and street |
| Asking price | Not in any dataset; only the seller knows it |
| Floor | The listing's storey is not published anywhere |
| Facing | HDB does not publish unit orientation |
| Destination postcode (optional) | For drive time, if wanted; blank hides those rows |

Leave the floor out and the view and sun rows read "unknown". They do not
guess.

## How sure each number is

Every figure carries its confidence, because mixing these is how a tool
starts lying:

- **Fact** — it sold for $473,000; the next block is 16 storeys; the hawker
  centre is 324 m away.
- **Worked out** — the fair-value range, the sun angle, distance to a park's
  edge.
- **Estimated** — a height band from a plot ratio. Shown as a range, with
  the ratio it came from.
- **Unknown, and why** — never a blank, never a zero.

## Architecture

A static website. No login, no sign-up, no server, no runtime database.

The site holds nothing about us — no budget, no offices, no stored
shortlist — so there is nothing to put a password in front of. Removing the
login is what makes it static, and being static is what makes it free to
host.

```
monthly batch job  →  SQLite  →  one small JSON per block  →  static host
                                        ↓
                                  browser: look up the pasted block,
                                  apply the typed price/floor/facing,
                                  save to browser storage
```

9,222 blocks have sold in the last 24 months. Precomputing one report per
block means the browser downloads only the block asked about.

**Saved units live in the browser that saved them.** That is the cost of
having no login: one list per device, and sharing a unit means sending a
link that carries its details. If that becomes annoying, the fix is a server
and a password, and that is a decision to make then, not to build for now.

## Data sources

Every id below has been fetched and parsed. Nothing here is expected to
work; it has worked.

| Source | Id | What it gives |
| --- | --- | --- |
| HDB resale, 2000–Feb 2012 | `d_43f493c6c50d54243cc1eab0df142d6a` | Sales, comparables, trend |
| HDB resale, Mar 2012–2014 | `d_2d5ff9ea31397b66239f245f57751537` | ″ |
| HDB resale, 2017 onwards | `d_8b84c4ee58e3cfc0ece0d773c8ca6abc` | ″ |
| HDB property information | `d_17f5382f26140b1fdae0ba2ef6239d2f` | Storey count per block — the view calculation |
| HDB resale price index | `d_14f63e595975691e7c24a27ae4c07c79` | Adjusting to today; trend benchmark |
| HDB renting out of flats | `d_c9f57187485a850908655db0e8cfe651` | Rent |
| URA Master Plan 2019 land use | `d_90d86daa5bfaa371668b84fa5f01424f` | Zoning, plot ratio, height band |
| NEA hawker centres | `d_4a086da0a5553be1d89383cd90d07ecd` | Hawker centres, stall counts |
| NParks parks and nature reserves | `d_77d7ec97be83d44f61b85454f844382f` | Park boundaries and areas |
| NParks park facilities | `d_14d807e20158338fd578c2913953516e` | Playgrounds, fitness, BBQ, courts |
| LTA MRT station exits | `d_b39d3a0871985372d7e1637193335da5` | 613 exits |
| LTA bus stops | `d_3f172c6feb3f4f92a2f47d93eed2908a` | 5,205 stop locations |
| MOE school directory | `d_688b934f82c1059ed0a6993d2a829089` | 337 schools; no coordinates, so geocoded |
| ECDA pre-school locations | `d_61eefab99958fd70e6aab17320a71f1c` | Pre-schools |
| NEA historical rainfall, per year | 9 ids in `datasets.yml` | Rainfall ranking, by sampling |
| OneMap (SLA) | — | Geocoding, routing, map tiles. **No token needed** |

The 2015–2016 resale file has no published resource id, so those two years
are absent. It does not affect anything here.

## What this does not do

Stated so it is never quietly promised:

- **Dog runs.** The park facilities layer has a `DOG-AREA` class with three
  entries for the whole country against a real number in the dozens. A
  "nearest dog park" would be wrong almost everywhere.
- **Basketball courts and hardcourts.** Not in government data. Park
  facilities has 16 multipurpose courts; SportsFields@SG is school halls and
  tennis courts last updated in 2014. OpenStreetMap has them properly and is
  the way in if this matters later.
- **Which buses stop where.** Stop locations are free; the services calling
  at each need LTA DataMall and an account key. On hold.
- **Whether a road floods.** No historical dataset exists. PUB's "Flood
  Prone Areas" is four rows of national hectares per year, and its flood
  alerts are live-only. The one route is to start recording the live alerts
  now and have a history in a year.
- **Exact permitted heights.** URA publishes plot ratio, not height. The
  band is inferred.
- **Scraping the portals.** Against their terms, and unnecessary — the link
  text carries what is needed.

## Why there is no growth score

The previous PRD put a 0–100 growth score at the centre and made passing a
backtest a success metric. It was built and it failed. Scored on data to
2019 and graded on 2019–2024:

```
quintile   median score   realised growth
       1           30.1           8.90%
       5           67.1           8.88%
```

Top quintile −0.02 points against the bottom: no separation at all. Taken
apart, price momentum — the heaviest weight — predicted **backwards**,
monotonically, by 1.55 points. Catch-up gap (+1.25) and lease runway (+0.87)
worked; momentum cancelled them.

A weighting of 0 / 60 / 40 across momentum, catch-up and lease runway did
separate, by +1.62 points and monotonically. But a score that needs the
evidence of a backtest to be believed does not belong at the top of a page
where a trend line and a fair-value range say something true. So: no score.
The trend is shown instead, and the reader draws the conclusion.

## Notes for whoever builds this

Things that cost a day each to discover:

- **data.gov.sg has no working search.** `query=`, `q=`, `search=`,
  `keyword=`, `name=`, `title=` and `agencies=` are all accepted and all
  ignored; the catalogue comes back alphabetical whatever you ask.
  `api-open` has no catalogue route at all. `data/datagov_catalogue.csv`
  mirrors all 4,655 datasets; finding one is a local grep.
- **Geospatial datasets are not in the datastore.** `datastore_search`
  returns an empty response for a GeoJSON dataset. They come from
  `api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download`, which
  hands back a presigned S3 link valid for an hour. That host needs to be
  allowed by the network policy.
- **Deep pagination times out.** On a seven-million-row table, `offset=0`
  with `limit=10000` answers in 0.4 s; `offset=140000` with the same limit
  returns 502 after 30 s. Smaller pages work at depth. There is no
  `datastore_search_sql`, so aggregation cannot be pushed to the server.
- **Rainfall is sampled, not exhausted.** A year is 7,044,646 rows and about
  704 pages, which is hours. Every page interleaves ~60 of the 89 stations,
  so a sample of pages gives a fair cross-station comparison. That yields a
  *relative* ranking, not an annual total in millimetres.
- **`GPR` is a string, and mostly not a number.** `LND` on 52% of parcels
  (landed housing — which is a confident answer about height), `EVA` on 11%
  (not yet evaluated — which is not), `NA` on 7%. About 30% carry a ratio.
  `GPR_B_MN`, URA's own band field, is set on 52 parcels out of 113,212.
- **Block numbers are not unique.** `266B` is both `266B|COMPASSVALE BOW`
  and `266B|PUNGGOL WAY`. The street is load-bearing.
- **Portal and HDB street names differ.** Portals write `JURONG WEST STREET
  64`; HDB writes `JURONG WEST ST 64`. Also `AVENUE/AVE`, `DRIVE/DR`,
  `CRESCENT/CRES`, `CENTRAL/CTRL`, `BUKIT/BT`, `JALAN/JLN`, `GARDENS/GDNS`.
- **OneMap needs no token** for anything used here. Geocoding, drive routing
  and map tiles all answer without one; the previous code refused to try.
- **The land use layer cannot be committed.** 174 MB, and GitHub blocks
  files over 100 MB. Pin the id, fetch at build time, derive, discard.

## What is already built

| | |
| --- | --- |
| HDB resale transactions | 663,226 rows, 2000-01 to 2026-10 |
| Rental records | 210,967 |
| Blocks geocoded | 10,863 of 10,969; 106 addresses did not match |
| Block storey counts | 10,817 |
| URA land use parcels | 113,212, with plot ratios parsed |
| Hawker centres | 129, six under construction |
| Parks | 462, with boundaries and areas |
| Park facilities | 1,716 useful of 14,754 |
| data.gov.sg catalogue | 4,655 datasets indexed |
| Fitted lease and storey curves | Yes — the lease curve understates decay; see the note where it is fitted |

Still to load: MRT exits, bus stops, schools (geocode from postcodes),
pre-schools, rainfall sample.

## Build order

Each step leaves something usable.

1. **Resolve a link.** Paste a URL, get the right block, or an honest
   failure. Everything depends on this being right.
2. **The money report.** Sales, fair value, asking-price verdict, rent.
3. **The trend.** Mix-adjusted quarterly series against town and index.
4. **Nearby.** MRT exits, bus stops, schools, hawker, parks, facilities.
5. **View and sun.** Neighbour heights against floor; zoning and height band
   for what could still be built; sun exposure net of shading.
6. **Save and compare.** Browser storage, the transposed table.
7. **Rain.** Sampled station ranking.
8. **Later.** Flood-alert collection; hardcourts from OpenStreetMap; bus
   services if the DataMall key is set up.

## Open questions

- [ ] Is a per-device saved list acceptable long term, or is a shared list
      worth a server and a password?
- [ ] Should drive time stay as an optional typed destination, or go?
- [ ] Is "within 1 km of a primary school" the right band, given it is
      resale value we care about rather than our own use?
- [ ] Is there a Master Plan land use layer newer than 2019? An
      "Amendment to Master Plan 2025 Building Layer"
      (`d_15dd877587e74558f4ca31e7b3fdcc09`) exists, so the 2019 zoning may
      be stale.
