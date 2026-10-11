# Next Home Finder

A personal, phone-first web app that shortlists HDB resale flats and scores each
on value, livability and growth potential, using only free government data.
One ranked, explainable list instead of scattered portal browsing and
spreadsheet maths.

The product brief this is built from is [`docs/PRD.md`](docs/PRD.md).

```
data.gov.sg ─┐
OneMap      ─┼──►  nhf sync  ──►  SQLite  ──►  nhf build  ──►  phone app
LTA (by hand)┘      (monthly)      one file    (scores)        (reads only)
```

Nothing in the app calls a government API while you are looking at it, and
nothing is invented to fill a gap: a dataset that did not download, a drive
time that was never routed and a signal with no data source all read as
unknown, with the reason attached.

## Getting started

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

export DATAGOV_API_KEY=...          # optional, raises the rate limit
export ONEMAP_EMAIL=... ONEMAP_PASSWORD=...   # free account, for routing

nhf init                            # create data/nhf.db
nhf sync                            # download everything (slow the first time)
nhf build                           # fit the curves, compute metrics and scores
nhf serve                           # http://127.0.0.1:8765
```

`nhf sync` downloads roughly a million resale transactions back to 2000 and
geocodes about 13k block addresses, so the first run takes a while. Later runs
only geocode blocks that are new.

### Accounts

With `NHF_USERS` unset the app answers on localhost only. To use it from a
phone, make an entry for each of us and put it online behind HTTPS:

```bash
nhf hash-password daniel
export NHF_USERS='daniel:scrypt:...,connie:scrypt:...'
export NHF_SECRET_KEY="$(python3 -c 'import os;print(os.urandom(32).hex())')"
```

Then open it on the phone and add it to the home screen: it is installable, and
the shell stays usable in a lift lobby with no signal.

### Monthly refresh

```cron
15 4 1 * *  cd /srv/nhf && .venv/bin/nhf sync && .venv/bin/nhf build
```

The Data tab shows the age of every dataset and warns past 45 days.

## Commands

| Command | What it does |
| --- | --- |
| `nhf sync [--step resale] [--limit N]` | Download the datasets, geocode blocks, route drive times |
| `nhf build` | Fit the lease and storey curves, compute per-block metrics and growth scores |
| `nhf fit` | Print the fitted curves, to eyeball before trusting a valuation |
| `nhf rank [--top 20] [--town ...]` | The ranked list, in the terminal |
| `nhf value "101\|ANG MO KIO AVE 3" "4 ROOM" --storey 12 --asking 620000` | Fair value for one unit, and where an asking price sits |
| `nhf backtest [--as-of 2019-12] [--weight catch_up_gap=0]` | Grade the growth score against what actually happened |
| `nhf geocode`, `nhf commute` | The OneMap steps on their own |
| `nhf import-mrt stations.geojson`, `nhf geocode-mrt` | Existing and future MRT stations |
| `nhf status` | Data freshness summary |
| `nhf serve [--host 0.0.0.0]` | The app |

## How the numbers are made

**Fair value (F3).** Comparable sales in the same block over the last 24 months,
widened to blocks within 800 m when a block is too thin. Each comparable is
adjusted three ways before the range is taken — to today's market with the HDB
Resale Price Index, to the target floor with a fitted storey premium, and to the
target lease with a fitted lease curve — and the 20th / 50th / 80th percentiles
become low / mid / high. Every adjustment is shown per comparable on the block
page.

**The fitted curves.** Lease decay and the storey premium are read out of the
transactions rather than assumed: each sale's price per sqm is divided by the
median for its town, flat type and quarter, which removes location and the
market cycle, and the median of what is left inside each band becomes the
factor. Two guards matter. Cohorts with fewer than eight sales are dropped,
because a cohort of one makes every ratio exactly 1.0 and flattens the curve.
And the lease curve is forced to be monotone, since a shorter lease is never
worth more per sqm than a longer one.

The lease curve understates true decay, and the code says so where it is
fitted: HDB towns were built in waves, so lease length is partly collinear with
town, and dividing by a town cohort removes some of the lease signal along with
the location signal. It is a floor on observed decay. `nhf fit` prints it.

**Growth score (F4).** Five signals — price momentum against the index,
catch-up gap to the town median, lease runway at exit, walking distance to an
announced or under-construction station weighted by how soon it opens, and
flats in the town reaching MOP over the next three years. Each is turned into a
percentile across the blocks in budget, then weighted. A signal with no data
contributes nothing and the weights are re-based on what is available, so a
missing signal lowers confidence instead of quietly lowering the score. Every
signal is stored with its value, its percentile and its source.

Momentum is measured on storey- and lease-adjusted prices. Raw medians made it
predict the *opposite* of what happened next on test data: a block that
happened to sell two high floors in one window looked like a riser and then
regressed. Mix adjustment is what makes the signal mean anything.

**Value at exit.** `fair value today × (1 + g) ^ 20 × lease factor at exit /
lease factor today`, for three cases of `g`: a downside, a flat market, and the
town's own ten-year rate. A range, never a forecast price.

**Ranking (F5).** Price, lease, growth, commute and amenities, weighted by
sliders. Four of the five are scored on fixed scales from
`config/criteria.yml`, so a block's rank does not move when a filter changes
and a 3-room flat does not win the price column for being a 3-room flat. Only
growth is relative, as the PRD defines it. The price scale plateaus below 85%
of budget — under that, the difference stops mattering — and ties break on the
cheaper flat.

**Criteria (F1).** Nothing is hidden for missing a criterion. Each one grades
Best, Acceptable or Flagged with the reason in words: "Far north (Woodlands)",
"$35,000 over budget", "only 42 years left after our 20".

**The backtest.** `nhf backtest` scores every block on data up to a cutoff,
then grades the quintiles on what happened afterwards. Two things keep it
honest. The future-MRT and supply signals are switched off, because today's
station list and today's completion years are hindsight. And the outcome is
measured from windows strictly after the cutoff: when the outcome shared sales
with the window the score had seen, the same noise sat in the score's numerator
and the outcome's denominator, which manufactured a confident negative
correlation out of nothing.

Expect it to fail at first. That is the point — the PRD says the weights change
before the score is used for a real decision, and `--weight signal=N` is how to
try another set against history.

## Configuration

Everything tunable is in `config/`, with a `last_checked` date where a date
matters:

- `criteria.yml` — budget and tolerance, holding period, offices, every
  criterion threshold, the ranking weights and scales, fair-value parameters.
- `policy.yml` — MOP, Plus and Prime exclusion, whole-flat rental rules, the
  buyer CPF and loan thresholds. A policy change is a one-line edit.
- `datasets.yml` — pinned data.gov.sg resource ids and the OneMap endpoints.
- `future_mrt.yml` — the hand-kept LTA table; no station-level API was found.

Three things in there are deliberately marked unconfirmed rather than guessed:

- **Two resource ids read `TODO`** — the 2015–2016 resale file and HDB's own
  median-price dataset. The PRD links both pages but not their ids. `nhf sync`
  reports them as skipped and falls back to town medians computed from the
  transactions, which cover the same ground from 2000.
- **`buyer_finance` thresholds are placeholders** (`confirmed: false`). The real
  rule is that the remaining lease must cover the youngest buyer to age 95 for
  full CPF use. It shows as a flag and a manual check, never as advice.
- **`future_mrt.yml` is empty.** Until stations are added by hand the
  new-transport signal scores nothing and says so on the block page. Add them
  from the LTA rail expansion pages and run `nhf geocode-mrt`.

## Data sources

| Source | Used for |
| --- | --- |
| HDB resale flat prices, 2000 onwards (4 datasets) | Comparables, momentum, catch-up gap, the lease curve, the backtest |
| HDB Property Information | Top-floor detection, unit mix, blocks reaching MOP |
| HDB Resale Price Index (1Q2009 = 100) | Adjusting comparables to today, growth against the market |
| Median resale prices by town and flat type | `g_town` (derived from transactions until the id is filled in) |
| HDB renting out of flats, 2021 onwards | The retirement rental check |
| OneMap (SLA) | Geocoding every block, drive time to both offices, the base map |
| LTA upcoming rail projects | Future stations, kept by hand |
| URA Master Plan 2019 land use | Planned-development signal — later phase, weight 0 |
| OpenStreetMap | Hardcourt flag — later phase |

Resale prices are indicative and exclude transfers between relatives, so the
app works in medians per block and storey band, never single records.

## What is not built

Out of scope by choice: schools, scraping listing portals, forecasts presented
as fact, mortgage advice, multi-user accounts. Listings are entered by hand —
paste a link and an asking price, and the app judges it.

Later phases, in the PRD and not yet here: the hardcourt flag from
OpenStreetMap, the URA Master Plan signal, a retirement rent estimate rather
than today's median, price-trend alerts, and a sale-proceeds calculator for our
current flat.

## Tests

```bash
python3 -m pytest
```

The fixtures in `tests/conftest.py` are generated, not real: the live datasets
are far too large to check in. They are built with a known lease decay, a known
storey premium, a deliberately cheap block and a deliberately fast-growing one,
so the fit and the scoring can be checked against the right answer rather than
just for running without error. The app never loads them.
