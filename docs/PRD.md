# Next Home Finder — PRD

Oct 11, 2026 · @Daniel Chua

## Summary

A personal, phone-first web app that shortlists HDB resale flats at or under $600k and scores each on value, livability and growth potential, using only free government data. It replaces scattered portal browsing and spreadsheet maths with one ranked, explainable list for the two of us.

**Problem.** Listing portals show asking prices, not what blocks actually transact or rent for. Comparing lease left, past prices, rent and commute across towns is manual and slow.

**Goals**

1. Find every block where a target flat type has transacted under $600k in the last 12 months.
2. Show a fair-value range per block, so we know when an asking price is high.
3. Score each block's growth potential: price momentum, lease runway, upcoming MRT and planned development nearby.
4. Rank candidates on our own weighted criteria (price, lease, growth, commute, amenities).
5. Track a shortlist of specific listings and viewings in one place.

## Users and assumptions

Two users: Daniel, the main applicant and owner, and his wife Connie, the essential occupier. Both are Singapore Citizens. One household, no public sign-up.

**Context.** Our current HDB flat finishes its MOP in November 2027; we list it then and buy right after. The purchase is funded by Daniel's share of the sale proceeds, his CPF OA and a bank loan. We both work in the south, near postal codes 109679 and 098544, and drive about 30 minutes today.

**Working assumptions** (confirm or change):

- Target is an **HDB resale flat, 3-room or larger**. Any flat type works if the price fits.
- **$600k is a soft target**, not a hard cap. Units above it still show, flagged by how far over.
- This is our **home for about 20 years**. After that we retire overseas and rent the whole flat out, so the default holding period is 20 years.
- "Growth" means the flat holding or gaining value over those 20 years, net of lease decay, and still being rentable when we leave.
- No children planned, so schools carry no weight.
- Built with Claude Code; used mainly on our phones, day to day.

## Scope

The MVP answers one question: which blocks should we be viewing, and what should we pay there?

| Phase | In | Why |
| --- | --- | --- |
| MVP | HDB resale prices, block heights, price index, geocoding, drive time to both offices, top-floor detection, criteria flags, growth score, map + ranked list, shortlist | Covers price, floor, commute and growth with free government data |
| Later | Hardcourt proximity, Master Plan land-use signal, retirement rent estimate, price-trend alerts, sale-proceeds calculator for our current flat | Each adds a data layer or a rule set |
| Out | Schools, scraping listing portals, price forecasts presented as fact, mortgage advice, multi-user accounts | Not needed, terms-of-use risk, false precision, regulated advice |

Listings themselves are entered by hand (paste a PropertyGuru or 99.co link plus asking price). The app judges a listing; it does not crawl for them.

## Features

**F1. Criteria as soft filters.** No unit is hidden for missing a criterion. Each unit shows a fit badge, and any miss is listed in red with the reason, such as "Far north" or "$35k over budget".

| Criterion | Best | Acceptable | Flagged | How it's known |
| --- | --- | --- | --- | --- |
| Price | At or under $600k | Up to 10% over (adjustable) | More than that | Fair value or asking price |
| Flat type | Any 3-room or larger |  | 1- or 2-room | Resale data |
| Floor | Top floor | High floor: top third of the block (adjustable) | Lower floors | Storey band vs block's highest floor |
| Unit position | Corner unit | Non-corner |  | Ticked by hand per listing |
| Location | Outside the far north |  | Woodlands, Sembawang, Yishun | Town field |
| Commute | Shortest peak drive | Up to 35 min to the farther office (adjustable) | Longer | OneMap drive routing to 109679 and 098544 |
| Hardcourt | None nearby |  | Basketball or hard court within 100 m (adjustable) | OpenStreetMap, confirmed by hand at viewing |
| Lease | 60+ years left after our 20 years (adjustable) |  | Less | Lease start in resale data |

**F2. Block profile.** One page per block: last 24 months of resale transactions, median and range by flat type and storey band, price per sqm, lease left, price trend against its town and the national index, nearest existing and upcoming MRT, and amenities.

**F3. Fair-value estimate.** For a given unit (type, storey, sqm), show a low / mid / high range from comparable transactions in the same and nearby blocks, adjusted for lease left. An asking price is tagged below, within or above range.

**F4. Growth score.** Per block: a 0–100 score built from the signals in the Growth model, each shown with its value and source, plus a projected value range at the end of our holding period.

**F5. Weighted ranking.** We set weights (for example price 25, lease 20, growth 25, commute 15, amenities 15). Every score shows its inputs, so a rank is never a black box.

**F6. Map view.** Candidate blocks on a OneMap base map, coloured by score, with MRT stations shown.

**F7. Shortlist and viewings.** Save a listing from the phone (link, asking price, block, floor, notes, photos), log a viewing date, and mark a status: Watching, Viewed, Offered, Dropped. A viewing checklist covers what government data can't: corner unit, facing, view, and any hardcourt or noise source within sight.

**F8. Data freshness.** Each dataset shows its last-sync date; a scheduled job refreshes resale and rental data monthly.

## Growth model

An HDB flat's value moves with the whole market, gains from location upgrades, and loses ground as its lease runs down. The app scores each block on signals visible in government data and shows a value range, never a single forecast price.

```latex
\text{Growth score} = \sum_i w_i \cdot \text{percentile}(s_i) \quad \text{over all blocks in budget}
```

```latex
\text{Value at exit} \approx \text{fair value today} \times (1 + g_{\text{town}})^{N} \times \frac{\text{lease factor at exit}}{\text{lease factor today}}
```

Signals, each normalised to a percentile so they can be weighted:

| Signal | Measured as | Source |
| --- | --- | --- |
| Price momentum | 5- and 10-year annual growth of the block's or street's price per sqm, and the town's, against the HDB Resale Price Index | Resale transactions, town medians, price index |
| Catch-up gap | Block price per sqm below its town median for the same flat type and lease band | Resale transactions |
| Lease runway | Years left at purchase and at the end of the holding period; flagged when the exit buyer's CPF or loan use would be restricted (rule to confirm) | Resale transactions (lease start) |
| New transport | Walking distance to an MRT station announced or under construction, weighted by expected opening year | LTA upcoming rail projects (hand-kept table) + OneMap |
| Planned development | Master Plan land use within 1 km that is new commercial, business, park or rezoned residential | URA Master Plan layer |
| Nearby supply | Flats in the same town reaching MOP over the next 3 years, which adds resale supply | HDB block completion years (to verify) |

Defaults: N = 20 years; g\_town = the town's 10-year annual growth, shown alongside a 0% case and a downside case. The lease factor is a lookup table of value by years left, fitted from resale data, so it reflects observed lease decay rather than an assumed curve.

**Backtest before trusting it.** Score every block using only data up to 2019, then check whether high scorers outperformed by 2024. If they did not, the weights change before the score is used for a real decision.

**Retirement rental check.** At year 20 we plan to rent the whole flat out from overseas. For each block the app shows today's median whole-flat rent for that flat type, and the lease that would be left when renting starts. This is a sanity check that the flat stays rentable, not a yield target.

## Data sources

Free government sources cover the whole MVP; OneMap and URA need a free account. All data.gov.sg datasets are free for personal or commercial use under the Singapore Open Data Licence.

| Source | What it gives | Access | Used for |
| --- | --- | --- | --- |
| [HDB resale flat prices, Jan 2017 onwards](https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view) (data.gov.sg) | Every resale transaction: month, town, flat type, block, street, storey range, floor area, flat model, lease start, remaining lease, price | `datastore_search` API; open, an API key raises rate limits | Comps, momentum, catch-up gap, lease factor |
| Older resale datasets ([2000–Feb 2012](https://data.gov.sg/datasets/d_43f493c6c50d54243cc1eab0df142d6a/view), [Mar 2012–2014](https://data.gov.sg/datasets/d_2d5ff9ea31397b66239f245f57751537/view), 2015–2016) | Same fields, earlier years | Same API | Long-run momentum, backtest |
| [HDB Property Information](https://data.gov.sg/dataset/hdb-property-information) (id `d_17f5382f26140b1fdae0ba2ef6239d2f`) | Per block: highest floor, year completed, unit mix by flat type (\~13.3k blocks) | Same API | Top-floor detection, nearby supply reaching MOP |
| [HDB Resale Price Index](https://data.gov.sg/datasets/d_14f63e595975691e7c24a27ae4c07c79/view) (1Q2009 = 100) | Quarterly market-wide index from 1990 | Same API | Growth relative to the market |
| [Median resale prices by town and flat type](https://data.gov.sg/dataset/median-resale-prices-for-registered-applications-by-town-and-flat-type) | Quarterly town medians from 2007 | Same API | Town growth rate g\_town |
| [HDB renting out of flats, Jan 2021 onwards](https://data.gov.sg/collections/166/view) (id `d_c9f57187485a850908655db0e8cfe651`) | Approved whole-flat rentals by block and flat type; owner-declared | Same API | Retirement rental check |
| [OneMap](https://www.onemap.gov.sg) (Singapore Land Authority) | Address search and geocoding, drive and transit routing, base map | Search is open; routing needs a token from a free account (3-day validity) | Geocode blocks, drive time to 109679 and 098544, map |
| [LTA upcoming rail projects](https://www.lta.gov.sg/content/ltagov/en/upcoming_projects/rail_expansion/cross_island_line.html) | Announced lines and stations, such as the Cross Island Line phases | Web pages; no station-level API found | Hand-kept table of future stations |
| [URA Master Plan 2019 land use layer](https://data.gov.sg/datasets/d_90d86daa5bfaa371668b84fa5f01424f/view) | Land-use polygons with plot ratio, as GeoJSON | Download | Planned development signal (later) |
| OpenStreetMap (not government) | Mapped sports pitches, including basketball courts | Free Overpass API | Hardcourt flag (later); no government dataset of hardcourts was found |

Notes for the build:

- Resale prices are indicative and exclude transfers between relatives; use medians per block and storey band, not single records.
- Neither HDB dataset has coordinates; geocode each unique block address once through OneMap search and cache it.
- The 2019 Master Plan layer is partly superseded by the 2025 Draft Master Plan; check data.gov.sg for a newer layer before building that signal.
- data.gov.sg rate limits now apply to keyless use; register for a key and bulk-download monthly, rather than query live per page view.

## Architecture

A small, single-user stack: a monthly batch job pulls everything into one local database, and the app reads only from that database, so no page view waits on a government API.

&#91;embedded content: data pipeline · 4 sources, 1 store, 1 app\]

Suggested stack, built with Claude Code: Python for the sync job and scoring, SQLite as the store, and a mobile-first web app (installable to the phone's home screen) with the OneMap base map. It runs on a small cloud instance behind a login for the two of us, so it works on our phones anywhere, including at viewings.

## Policy constraints

Growth only counts if we can sell when we want to, so the app encodes the rules that fix our earliest exit and limit who can buy from us.

| Rule | Effect in the app |
| --- | --- |
| A resale purchase starts a new [5-year MOP](https://www.hdb.gov.sg/residential/buying-a-flat/conditions-after-buying?anchor=mop) before the flat can be sold on the open market | Earliest exit date shown per candidate |
| Plus and Prime flats carry a 10-year MOP and [can never be rented out whole](https://www.hdb.gov.sg/business/estate-agents-and-salespersons/renting-out-a-flat-or-bedroom/eligibility) | Excluded by default, since renting out at retirement is the plan |
| Only Singapore Citizen owners can rent out a whole flat, after MOP and with HDB approval; a non-citizen tenant quota applies per block | Retirement rental check notes the quota as a manual check |
| Buyers' CPF and loan use depends on lease left versus their age | Lease flag in F1 and the Growth model (exact thresholds to confirm with HDB) |
| Ethnic Integration Policy and SPR quotas can limit eligible buyers in a block | Manual check field per shortlisted block; no open API for quota status |

Rules are kept in one config file with a last-checked date, so a policy change is a one-line edit.

## Success metrics

The app succeeds if it gets us to a confident offer faster and stops us overpaying.

- We view no more than 10 flats before making an offer, all of them from the app's top 20.
- Every offer we make sits inside the app's fair-value range.
- The growth score passes the backtest: the top quintile of 2019 scores beat the bottom quintile on 2019–2024 price growth.
- Data is never more than 45 days old.

## Milestones

Each phase ends with something we can use, so the project is worth it even if it stops early.

1. **Data spine.** Download all resale datasets, block property data, the price index and town medians; geocode every block; a notebook answers "which top-floor units fit $600k".
2. **Commute and criteria.** Drive time from every block to both offices; fit badges and red flags for every criterion in F1.
3. **Fair value.** Comparable-sales range per unit, with a top-floor adjustment; lease factor table; asking-price tag.
4. **Growth score + backtest.** Momentum, catch-up, lease runway and new-MRT signals; backtest passes or weights are revised.
5. **Phone app.** Mobile-first ranked list, block profile, map and weight sliders, hosted online behind a login, built with Claude Code.
6. **Shortlist.** Listings, viewing checklist, statuses and notes, in use for real viewings well before November 2027.
7. **Later.** Hardcourt flag, Master Plan signal, retirement rent estimate, alerts, sale-proceeds calculator.

## Risks and open questions

**Risks**

- Past growth may not repeat; the score ranks relative prospects, not future prices.
- Government dataset IDs and endpoints change; pin IDs in config and alert on a failed sync.
- Monthly data lags the live market by weeks, so fast-moving towns may look cheaper than they are.

**Open questions**

- [ ] How much bank loan will Daniel's income alone support, and does that change the $600k target?
- [ ] Is "far north" exactly Woodlands, Sembawang and Yishun, or wider?
- [ ] Is "top third of the block" the right definition of high floor?
- [ ] How far from a hardcourt is far enough: 100 m, or out of sight and earshot?
- [ ] Is there a newer Master Plan land-use layer than 2019 on data.gov.sg?
