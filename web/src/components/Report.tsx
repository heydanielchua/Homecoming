/**
 * The report for one flat: everything the government knows about it.
 *
 * Sections are collapsible and all open to begin with, because the first
 * question is usually the money one and the rest is read once. The order is
 * the PRD's: money, trend, lease, rent, view and sun, nearby, weather.
 *
 * Nothing in here fetches anything. The block's file, its town's file and
 * `meta.json` arrived before this rendered; the floor, facing and asking
 * price are applied to them here.
 */

import * as React from 'react';
import { Accordion } from '@base-ui/react/accordion';

import { metres, money, month, percent, range, titleCase, walk } from '../lib/format';
import { exposure, monthName, type Obstruction } from '../lib/sun';
import { areaSqft, psfBare, psfOf } from '../lib/units';
import { obstructionsFor, viewFrom } from '../lib/view';
import type {
  BlockReport,
  Facing,
  Meta,
  Place,
  TownReport,
} from '../lib/types';

import type { Inputs } from './UnitForm';
import { ConfidenceMark, Figure } from './Confidence';
import { Massing } from './Massing';
import { SurroundingsMap } from './SurroundingsMap';
import { TrendChart } from './TrendChart';

/** Which five-floor band a typed floor falls in. */
export function bandOf(storey: number | null, bandFloors: number): string | null {
  if (storey === null || storey < 1) return null;
  return String(Math.floor((storey - 1) / bandFloors) * bandFloors + 1);
}

export interface Verdict {
  status: 'below' | 'within' | 'above' | 'unknown';
  says: string;
  gap: number;
}

/** Where an asking price sits against a range. Mirrors `verdict` in report.py. */
export function verdictFor(
  asking: number | null,
  low: number | null,
  high: number | null,
): Verdict {
  if (asking === null) {
    return { status: 'unknown', says: 'no asking price typed in', gap: 0 };
  }
  if (low === null || high === null) {
    return {
      status: 'unknown',
      says: 'no fair-value range for this floor, so nothing to compare',
      gap: 0,
    };
  }
  if (asking < low) {
    return {
      status: 'below',
      says: `${money(low - asking)} below the bottom of the range`,
      gap: low - asking,
    };
  }
  if (asking > high) {
    return {
      status: 'above',
      says: `${money(asking - high)} above the top of the range`,
      gap: asking - high,
    };
  }
  return {
    status: 'within',
    says: 'inside the range the comparable sales support',
    gap: 0,
  };
}

/**
 * `size` means different things per layer — a stall count for a hawker centre,
 * square metres for a park — so the caller says which rather than this
 * guessing from the label it was given.
 */
type SizeKind = 'stalls' | 'area' | 'none';

function sizeText(size: number | undefined, kind: SizeKind): string | null {
  if (size === undefined || kind === 'none') return null;
  if (kind === 'stalls') {
    return `${Math.round(size)} cooked-food stall${size === 1 ? '' : 's'}`;
  }
  const hectares = size / 10_000;
  return hectares >= 1
    ? `${hectares.toFixed(hectares < 10 ? 1 : 0)} hectares`
    : `${Math.round(size).toLocaleString('en-SG')} sqm`;
}

function PlaceRow({
  label,
  place,
  size = 'none',
}: {
  label: string;
  place: Place | null;
  size?: SizeKind;
}) {
  if (!place) return null;
  const extra = sizeText(place.size, size);
  return (
    <li className="place">
      <div className="muted">{label}</div>
      <div className="place-name">{titleCase(place.name) || '—'}</div>
      <div className="place-distance">
        {metres(place.distance_m)} {place.direction}
        {place.to_edge ? ' to its edge' : ''} · {walk(place.distance_m)}
      </div>
      {extra ? <div className="muted">{extra}</div> : null}
    </li>
  );
}

function Section({
  value,
  title,
  summary,
  children,
}: {
  value: string;
  title: string;
  summary?: string;
  children: React.ReactNode;
}) {
  return (
    <Accordion.Item value={value} className="accordion-item">
      <Accordion.Header>
        <Accordion.Trigger className="accordion-trigger">
          <span>{title}</span>
          <span className="accordion-summary">{summary}</span>
        </Accordion.Trigger>
      </Accordion.Header>
      <Accordion.Panel className="accordion-panel">
        <div className="accordion-content">{children}</div>
      </Accordion.Panel>
    </Accordion.Item>
  );
}

interface Props {
  report: BlockReport;
  town: TownReport | null;
  meta: Meta;
  flatType: string;
  asking: number | null;
  storey: number | null;
  facing: Facing | null;
  /**
   * The typed details, and how to change them. They live here rather than in
   * a form at the top because each one belongs beside the answer it changes.
   */
  inputs: Inputs;
  onInputs: (next: Inputs) => void;
}

export function Report({
  report,
  town,
  meta,
  flatType,
  asking,
  storey,
  facing,
  inputs,
  onInputs,
}: Props) {
  const set = <K extends keyof Inputs>(key: K, value: Inputs[K]) =>
    onInputs({ ...inputs, [key]: value });
  const section = report.flat_types[flatType];
  const constants = meta.sun.constants;

  const view = React.useMemo(
    () =>
      viewFrom(
        report.surroundings.neighbours,
        storey,
        constants,
        report.surroundings.located,
      ),
    [report.surroundings.neighbours, storey, constants, report.surroundings.located],
  );

  const obstructions = React.useMemo<Obstruction[]>(
    () => obstructionsFor(view, constants),
    [view, report.surroundings.neighbours, constants],
  );

  const sun = React.useMemo(() => {
    if (!facing) return null;
    return exposure(facing, meta.sun.track, constants, obstructions);
  }, [facing, meta.sun.track, constants, obstructions]);

  const band = bandOf(storey, meta.curves.storey_band_floors);
  const fair =
    section && band
      ? section.money.fair_value[band] ?? null
      : section
        ? section.money.fair_value['1'] ?? null
        : null;
  const verdict = verdictFor(asking, fair?.low ?? null, fair?.high ?? null);

  if (!section) {
    return (
      <div className="card">
        <p>
          No {flatType.toLowerCase()} flat in {report.block} {titleCase(report.street)}{' '}
          has sold recently, so there is nothing to compare an asking price
          with. The types that have sold here are{' '}
          {Object.keys(report.flat_types).join(', ') || 'none at all'}.
        </p>
      </div>
    );
  }

  const { money: m, trend, rent } = section;
  const nearby = report.nearby;

  return (
    <Accordion.Root
      className="accordion"
      multiple
      defaultValue={['money', 'trend', 'lease', 'rent', 'view', 'nearby', 'weather']}
    >
      <Section
        value="money"
        title="Money"
        summary={
          fair?.mid
            ? `fair value around ${money(fair.mid)}`
            : 'no comparable sales'
        }
      >
        {asking !== null ? (
          <div className={`verdict verdict--${verdict.status}`}>
            <span className="verdict-flag">
              {verdict.status === 'below'
                ? 'Under the range'
                : verdict.status === 'above'
                  ? 'Over the range'
                  : verdict.status === 'within'
                    ? 'In the range'
                    : 'No verdict'}
            </span>
            <span>
              {money(asking)} asked · {verdict.says}
            </span>
            {fair?.low && fair.high ? (
              <span className="secondary">
                Range for floors {band}–{Number(band) + meta.curves.storey_band_floors - 1}:{' '}
                {range(fair.low, fair.high)}, from {fair.n_comps} comparable
                {fair.n_comps === 1 ? '' : 's'}
                {fair.scope === 'nearby_blocks' ? ' nearby' : ' in this block'}
              </span>
            ) : null}
          </div>
        ) : null}

        <div className="figures" style={{ marginTop: 12 }}>
          <Figure label="Sales in 24 months" figure={m.n_sales} />
          <Figure label="Median sale" figure={m.median_price} />
          <Figure
            label="Price/sqft"
            figure={m.median_psm}
            format={(psm) => psfOf(psm) ?? '—'}
          />
          <Figure
            label={`${titleCase(report.town)} median`}
            figure={m.town_median_psm}
            format={(psm) => psfOf(psm) ?? '—'}
          />
          <Figure label="Against its town" figure={m.vs_town_pct} />
          {m.last_sale ? <Figure label="Last sale" figure={m.last_sale} /> : null}
        </div>

        {fair?.notes.length ? (
          <ul className="notes">
            {fair.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}

        {m.sales.length > 0 ? (
          <div className="table-scroll">
            <table>
              <caption className="muted" style={{ captionSide: 'bottom' }}>
                Every {flatType.toLowerCase()} sale in this block over 24
                months. These are the sales the range above rests on.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Month</th>
                  <th scope="col">Floor</th>
                  <th scope="col" className="num">Size</th>
                  <th scope="col" className="num">Price</th>
                  <th scope="col" className="num">Per sqft</th>
                  <th scope="col" className="num">Lease left</th>
                </tr>
              </thead>
              <tbody>
                {m.sales.map((sale, i) => (
                  <tr key={`${sale.month}-${sale.price}-${i}`}>
                    <th scope="row">{month(sale.month)}</th>
                    <td>{sale.storey}</td>
                    <td className="num">{areaSqft(sale.sqm) ?? '—'}</td>
                    <td className="num">{money(sale.price)}</td>
                    <td className="num">{psfBare(sale.psm) ?? '—'}</td>
                    <td className="num">
                      {sale.lease_months
                        ? `${Math.floor(sale.lease_months / 12)} yr`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Section>

      <Section
        value="trend"
        title="Trend"
        summary={trend.block.length > 1 ? undefined : 'too few sales to draw'}
      >
        <TrendChart
          block={trend.block}
          town={town?.flat_types[flatType]?.trend ?? []}
          national={meta.national_index}
          blockLabel={`Block ${report.block}`}
          townLabel={titleCase(report.town)}
          note={trend.note}
        />
      </Section>

      <Section
        value="lease"
        title="Lease"
        summary={
          report.lease.remaining_years.v !== null
            ? `${report.lease.remaining_years.v} years left`
            : 'unknown'
        }
      >
        <div className="figures">
          <Figure label="Lease started" figure={report.lease.commence_year} />
          <Figure label="Years left" figure={report.lease.remaining_years} />
        </div>
        <p className="muted" style={{ marginTop: 10 }}>
          An HDB lease runs 99 years and cannot be topped up. What is left is
          what a buyer after you would be buying.
        </p>
      </Section>

      <Section
        value="rent"
        title="Rent"
        summary={
          rent.median.v !== null ? `${money(rent.median.v)} a month` : 'unknown'
        }
      >
        <div className="figures">
          <Figure label="Median whole-flat rent" figure={rent.median} />
          {rent.low ? <Figure label="Lowest approved" figure={rent.low} /> : null}
          {rent.high ? <Figure label="Highest approved" figure={rent.high} /> : null}
          {rent.n ? <Figure label="Approvals" figure={rent.n} /> : null}
        </div>
        {rent.scope ? (
          <p className="muted" style={{ marginTop: 10 }}>
            {rent.scope === 'block'
              ? "This block's own rental approvals."
              : rent.scope === 'nearby'
                ? 'Too few in this block, so this is the blocks within 600 m.'
                : `Too few nearby, so this is the ${titleCase(report.town)} median.`}{' '}
            Whole flats only, not rooms.
          </p>
        ) : null}
      </Section>

      <Section
        value="view"
        title="View and sun"
        summary={
          !report.surroundings.located
            ? 'this block has no geocode'
            : // No floor typed yet is not worth a line in the header. The
              // section already says what it needs where the figures are, and
              // a prompt up here only tells a reader off for not having
              // filled in a field they can see.
              storey === null
              ? undefined
              : view.above_everything
                ? 'above everything within 300 m'
                : `${view.blocked.length} block${view.blocked.length === 1 ? '' : 's'} above you`
        }
      >
        {view.unknown ? (
          <p className="secondary">{view.unknown}</p>
        ) : (
          <>
            <div className="figures">
              <Figure label="This block's height" figure={report.surroundings.max_floor} />
              <div className="figure">
                <div className="figure-label">
                  <span>From floor {storey}</span>
                </div>
                <div className="figure-value">
                  {view.above_everything
                    ? 'Nothing in the way'
                    : `${view.blocked.length} above you`}
                </div>
                <div className="figure-note">
                  {Math.round(view.blocked_arc_deg)}° of the horizon · open to{' '}
                  {view.open_directions.join(', ') || 'nothing'}
                </div>
              </div>
            </div>

            {view.blocked.length > 0 ? (
              <div className="table-scroll">
                <table>
                  <caption className="muted" style={{ captionSide: 'bottom' }}>
                    HDB publishes the storey count for every block, so this is
                    fact rather than inference. {report.surroundings.neighbour_note}.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Block</th>
                      <th scope="col">Direction</th>
                      <th scope="col" className="num">Away</th>
                      <th scope="col" className="num">Storeys</th>
                      <th scope="col" className="num">Above you</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.blocked.map((b) => (
                      <tr key={`${b.name}-${b.distance_m}`}>
                        <th scope="row">{b.name}</th>
                        <td>{b.direction}</td>
                        <td className="num">{metres(b.distance_m)}</td>
                        <td className="num">{b.storeys}</td>
                        <td className="num">
                          {Math.round(b.storeys_above)}{' '}
                          {Math.round(b.storeys_above) === 1 ? 'floor' : 'floors'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </>
        )}

        <h3 style={{ marginTop: 18 }}>Afternoon sun</h3>
        {!facing ? (
          <p className="secondary">
            Pick a facing to see it. Singapore sits a degree north of the
            equator, so the midday sun is nearly overhead and the heat problem
            is the low western sun from about four o'clock — which is why
            “west sun” is the complaint here and “south sun” is not.
          </p>
        ) : sun ? (
          <>
            <div className="figures">
              <div className="figure">
                <div className="figure-label">
                  <span>Facing {facing}</span>
                </div>
                <div className="figure-value" style={{ fontSize: '1rem' }}>
                  {sun.says}
                </div>
                <div className="figure-note">
                  {Math.round(sun.net_low_hours)} hours a year of low afternoon
                  sun arrive
                  {sun.blocked_low_hours > 0
                    ? `, with ${Math.round(sun.blocked_low_hours)} shaded out`
                    : ''}
                  {sun.peak_month
                    ? `. Worst in ${monthName(sun.peak_month)}`
                    : ''}
                </div>
              </div>
              {Object.keys(sun.shaded_by).length > 0 ? (
                <div className="figure">
                  <div className="figure-label">
                    <span>Shaded by</span>
                  </div>
                  <div className="figure-value" style={{ fontSize: '1rem' }}>
                    {Object.keys(sun.shaded_by).join(', ')}
                  </div>
                  <div className="figure-note">
                    A tall block to the west ruins the view and saves the
                    afternoons.
                  </div>
                </div>
              ) : null}
            </div>
            <p className="muted" style={{ marginTop: 10 }}>
              {meta.sun.note}.
            </p>
          </>
        ) : null}

        <h3 style={{ marginTop: 18 }}>The block in three dimensions</h3>
        {report.lat !== null && report.lon !== null ? (
          <Massing
            meta={meta}
            lat={report.lat}
            lon={report.lon}
            slug={report.slug}
            storey={storey}
            facing={facing}
            maxFloor={report.surroundings.max_floor?.v ?? null}
            onStorey={(next) => set('storey', next === null ? '' : String(next))}
            onFacing={(next) => set('facing', next)}
          />
        ) : (
          <p className="secondary">
            This block has no geocode, so there is nothing to place in a model.
          </p>
        )}

        <h3 style={{ marginTop: 18 }}>What could still be built</h3>
        {!report.surroundings.parcels_loaded ? (
          <p className="secondary">
            The Master Plan land use layer has not been loaded, so this is
            unknown rather than nothing — run <code>nhf sync --step land_use</code>.
          </p>
        ) : report.surroundings.parcels.length === 0 ? (
          <p className="secondary">
            No undeveloped or redevelopable parcel over 1,000 sqm within 300 m.
          </p>
        ) : (
          <div className="table-scroll">
            <table>
              <caption className="muted" style={{ captionSide: 'bottom' }}>
                URA publishes plot ratio, not height, so every band here is an
                estimate shown as a range with the ratio it came from.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Zoned</th>
                  <th scope="col">Direction</th>
                  <th scope="col" className="num">Away</th>
                  <th scope="col" className="num">Size</th>
                  <th scope="col">Could reach</th>
                </tr>
              </thead>
              <tbody>
                {report.surroundings.parcels.map((parcel, i) => (
                  <tr key={`${parcel.land_use}-${i}`}>
                    <th scope="row">{titleCase(parcel.land_use)}</th>
                    <td>{parcel.direction}</td>
                    <td className="num">{metres(parcel.distance_m)}</td>
                    <td className="num">
                      {parcel.area_sqm
                        ? `${Math.round(parcel.area_sqm / 100) / 100} ha`
                        : '—'}
                    </td>
                    <td>
                      {parcel.band.low !== null ? (
                        <>
                          {parcel.band.low}–{parcel.band.high} storeys{' '}
                          <ConfidenceMark
                            level={parcel.band.basis === 'zoning' ? 'fact' : 'estimated'}
                            note={parcel.band.note}
                          />
                          <div className="muted">{parcel.band.note}</div>
                        </>
                      ) : (
                        <>
                          <ConfidenceMark level="unknown" note={parcel.band.note} />
                          <div className="muted">{parcel.band.note}</div>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        value="nearby"
        title="Nearby"
        summary={
          nearby.mrt_exit
            ? `${metres(nearby.mrt_exit.distance_m)} to an MRT exit`
            : undefined
        }
      >
        <SurroundingsMap
          lat={report.lat}
          lon={report.lon}
          label={`${report.block} ${titleCase(report.street)}`}
          points={nearby.points ?? []}
        />

        <ul className="places">
          <PlaceRow label="Nearest MRT exit" place={nearby.mrt_exit} />
          <PlaceRow label="Nearest bus stop" place={nearby.bus_stop} />
          <PlaceRow
            label="Nearest hawker centre"
            place={nearby.hawker}
            size="stalls"
          />
          <PlaceRow
            label="Hawker centre being built"
            place={nearby.hawker_opening}
            size="stalls"
          />
          <PlaceRow label="Nearest park" place={nearby.park} size="area" />
          <PlaceRow label="Nearest primary school" place={nearby.primary_school} />
          {nearby.schools_within_1km !== null ? (
            <li className="place">
              <div className="muted">Primary schools within 1 km</div>
              <div className="place-name">{nearby.schools_within_1km}</div>
              <div className="place-distance">
                The band that moves a resale price, not our own walk.
              </div>
            </li>
          ) : null}
          <PlaceRow
            label="Nearest secondary school"
            place={nearby.secondary_school}
          />
          {nearby.secondary_within_2km !== null ? (
            <li className="place">
              <div className="muted">Secondary schools within 2 km</div>
              <div className="place-name">{nearby.secondary_within_2km}</div>
              <div className="place-distance">
                Secondary places go by PSLE score, not by distance, so this is
                how far a teenager travels rather than a claim on admission.
              </div>
            </li>
          ) : null}
          {Object.entries(nearby.nearest_facility).map(([kind, place]) => (
            <PlaceRow
              key={kind}
              label={titleCase(kind.replace(/_/g, ' '))}
              place={place}
            />
          ))}
          {nearby.preschools_within_walk !== null ? (
            <li className="place">
              <div className="muted">Pre-schools within 400 m</div>
              <div className="place-name">{nearby.preschools_within_walk}</div>
            </li>
          ) : null}
        </ul>

        {nearby.missing.length > 0 ? (
          <ul className="notes">
            {nearby.missing.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        ) : null}

        <p className="muted" style={{ marginTop: 10 }}>
          Straight-line distances, so each is a lower bound on the walk. Which
          buses call at a stop needs an LTA DataMall key and is not here. The
          map shows the nearest few of each kind rather than every one, so a
          street of bus stops does not bury the school.
        </p>

      </Section>

      <Section
        value="weather"
        title="Weather"
        summary={
          report.rain.rank !== null
            ? `${report.rain.rank} of ${report.rain.of} gauges for wetness`
            : 'unknown'
        }
      >
        {report.rain.rank !== null ? (
          <>
            <div className="figures">
              <div className="figure">
                <div className="figure-label">
                  <span>Wetness rank</span>
                </div>
                <div className="figure-value">
                  {report.rain.rank} of {report.rain.of}
                </div>
                <div className="figure-note">
                  Wetter than {percent(report.rain.wetter_than_pct, 0)} of the
                  island's gauges
                </div>
              </div>
              <div className="figure">
                <div className="figure-label">
                  <span>Nearest gauge</span>
                </div>
                <div className="figure-value" style={{ fontSize: '1rem' }}>
                  {report.rain.station}
                </div>
                <div className="figure-note">
                  {metres(report.rain.distance_m)} away · {report.rain.year}
                </div>
              </div>
            </div>
            <p className="muted" style={{ marginTop: 10 }}>
              A rank, not a rainfall total. A year of readings is over six
              million rows with no way to make the server add them up, so the
              gauges are sampled alike — which supports the comparison between
              them and nothing finer.
            </p>
          </>
        ) : (
          <p className="secondary">{report.rain.unknown}</p>
        )}
      </Section>
    </Accordion.Root>
  );
}
