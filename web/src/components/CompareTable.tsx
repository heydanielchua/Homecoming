/**
 * Saved units side by side: units as columns, attributes as rows.
 *
 * The transposition is the whole design. Two to five flats against about
 * twenty-five attributes would be an unreadable twenty-five-column table the
 * other way round; this way it is tall and narrow, each row compares one
 * thing, and the row label stays put while the columns scroll.
 *
 * Rows group into collapsible sections, and each row marks the better value so
 * the table is scanned rather than read. "Better" is only claimed where it is
 * obvious — cheaper, more lease, nearer the MRT, more sales behind the
 * estimate. There is deliberately no overall winner and no score: the PRD
 * removed the score because it failed its own backtest, and a compare table
 * that quietly summed its rows would be the same mistake in a smaller place.
 *
 * Every report here was rebuilt from today's files. Only the inputs were
 * saved, so a unit saved in January is not showing January's figures.
 */

import * as React from 'react';
import { Collapsible } from '@base-ui/react/collapsible';

import { metres, money, percent, titleCase } from '../lib/format';
import { exposure } from '../lib/sun';
import { obstructionsFor, viewFrom } from '../lib/view';
import type { BlockReport, Meta, SavedUnit, TownReport } from '../lib/types';
import { bandOf, verdictFor } from './Report';

export interface LoadedUnit {
  unit: SavedUnit;
  report: BlockReport;
  town: TownReport | null;
}

/** One cell: what to show, and the number to compare on. */
interface Cell {
  text: string;
  /** Higher is better unless the row says otherwise. Null never wins. */
  value: number | null;
}

interface Row {
  label: string;
  /** Which direction counts as better, or none. */
  better: 'high' | 'low' | null;
  cells: Cell[];
}

interface Group {
  name: string;
  rows: Row[];
}

const nothing: Cell = { text: 'unknown', value: null };

function cell(text: string | null, value: number | null = null): Cell {
  return text === null ? nothing : { text, value };
}

function buildGroups(units: LoadedUnit[], meta: Meta): Group[] {
  const constants = meta.sun.constants;

  const per = units.map(({ unit, report, town }) => {
    const section = report.flat_types[unit.flatType];
    const band = bandOf(unit.storey, meta.curves.storey_band_floors) ?? '1';
    const fair = section?.money.fair_value[band] ?? null;
    const verdict = verdictFor(unit.asking, fair?.low ?? null, fair?.high ?? null);
    const view = viewFrom(report.surroundings.neighbours, unit.storey, constants);
    const sun = unit.facing
      ? exposure(
          unit.facing,
          meta.sun.track,
          constants,
          obstructionsFor(view, report.surroundings.neighbours, constants),
        )
      : null;
    const trend = section?.trend.block ?? [];
    const townTrend = town?.flat_types[unit.flatType]?.trend ?? [];

    // Growth over the series it has, annualised — shown as what the block has
    // done, never as a forecast.
    const growth = (points: { quarter: string; psm: number }[]) => {
      if (points.length < 8) return null;
      const first = points[0]!;
      const last = points[points.length - 1]!;
      const years =
        (Number(last.quarter.slice(0, 4)) - Number(first.quarter.slice(0, 4))) +
        (Number(last.quarter.slice(-1)) - Number(first.quarter.slice(-1))) / 4;
      if (years < 2 || first.psm <= 0) return null;
      return ((last.psm / first.psm) ** (1 / years) - 1) * 100;
    };

    return { unit, report, section, fair, verdict, view, sun, trend, townTrend, growth };
  });

  const groups: Group[] = [
    {
      name: 'Money',
      rows: [
        {
          label: 'Asking price',
          better: 'low',
          cells: per.map((u) => cell(money(u.unit.asking), u.unit.asking)),
        },
        {
          label: 'Fair value, this floor',
          better: null,
          cells: per.map((u) =>
            u.fair?.low && u.fair.high
              ? cell(`${money(u.fair.low)} – ${money(u.fair.high)}`)
              : nothing,
          ),
        },
        {
          label: 'Against that range',
          better: 'low',
          cells: per.map((u) =>
            u.verdict.status === 'unknown'
              ? nothing
              : cell(
                  u.verdict.status === 'within'
                    ? 'in the range'
                    : `${money(u.verdict.gap)} ${u.verdict.status}`,
                  u.verdict.status === 'above'
                    ? u.verdict.gap
                    : u.verdict.status === 'below'
                      ? -u.verdict.gap
                      : 0,
                ),
          ),
        },
        {
          label: 'Asking per m²',
          better: 'low',
          cells: per.map((u) => {
            const sqm = u.section?.money.median_sqm?.v ?? null;
            if (!u.unit.asking || !sqm) return nothing;
            const psm = u.unit.asking / sqm;
            return cell(`${money(psm)}/m²`, psm);
          }),
        },
        {
          label: 'Block median per m²',
          better: 'low',
          cells: per.map((u) =>
            cell(
              u.section?.money.median_psm.v
                ? `${money(u.section.money.median_psm.v)}/m²`
                : null,
              u.section?.money.median_psm.v ?? null,
            ),
          ),
        },
        {
          label: 'Against its town',
          better: 'low',
          cells: per.map((u) =>
            cell(
              percent(u.section?.money.vs_town_pct.v ?? null),
              u.section?.money.vs_town_pct.v ?? null,
            ),
          ),
        },
        {
          label: 'Sales behind it',
          better: 'high',
          cells: per.map((u) =>
            cell(
              u.section ? `${u.section.money.n_sales.v} in 24 months` : null,
              u.section?.money.n_sales.v ?? null,
            ),
          ),
        },
      ],
    },
    {
      name: 'Trend',
      rows: [
        {
          label: 'Block, a year on average',
          better: 'high',
          cells: per.map((u) => {
            const rate = u.growth(u.trend);
            return cell(rate === null ? null : percent(rate), rate);
          }),
        },
        {
          label: 'Its town, a year on average',
          better: null,
          cells: per.map((u) => {
            const rate = u.growth(u.townTrend);
            return cell(rate === null ? null : percent(rate));
          }),
        },
      ],
    },
    {
      name: 'Lease',
      rows: [
        {
          label: 'Years left',
          better: 'high',
          cells: per.map((u) =>
            cell(
              u.report.lease.remaining_years.v !== null
                ? `${u.report.lease.remaining_years.v} years`
                : null,
              u.report.lease.remaining_years.v,
            ),
          ),
        },
        {
          label: 'Lease started',
          better: 'high',
          cells: per.map((u) =>
            cell(
              u.report.lease.commence_year.v !== null
                ? String(u.report.lease.commence_year.v)
                : null,
              u.report.lease.commence_year.v,
            ),
          ),
        },
      ],
    },
    {
      name: 'Rent',
      rows: [
        {
          label: 'Median whole-flat rent',
          better: 'high',
          cells: per.map((u) =>
            cell(
              u.section?.rent.median.v
                ? `${money(u.section.rent.median.v)}/month`
                : null,
              u.section?.rent.median.v ?? null,
            ),
          ),
        },
        {
          label: 'Measured over',
          better: null,
          cells: per.map((u) =>
            cell(
              u.section?.rent.scope === 'block'
                ? 'this block'
                : u.section?.rent.scope === 'nearby'
                  ? 'blocks within 600 m'
                  : u.section?.rent.scope === 'town'
                    ? 'the town'
                    : null,
            ),
          ),
        },
        {
          label: 'Rent against asking price',
          better: 'high',
          cells: per.map((u) => {
            const rent = u.section?.rent.median.v;
            if (!rent || !u.unit.asking) return nothing;
            const yieldPct = ((rent * 12) / u.unit.asking) * 100;
            return cell(`${yieldPct.toFixed(1)}% a year`, yieldPct);
          }),
        },
      ],
    },
    {
      name: 'View and sun',
      rows: [
        {
          label: 'Floor',
          better: null,
          cells: per.map((u) =>
            cell(u.unit.storey ? `#${u.unit.storey}` : null, u.unit.storey),
          ),
        },
        {
          label: "This block's height",
          better: null,
          cells: per.map((u) =>
            cell(
              u.report.surroundings.max_floor.v
                ? `${u.report.surroundings.max_floor.v} storeys`
                : null,
            ),
          ),
        },
        {
          label: 'Blocks above you',
          better: 'low',
          cells: per.map((u) =>
            u.unit.storey === null
              ? nothing
              : cell(
                  u.view.above_everything
                    ? 'none within 300 m'
                    : `${u.view.blocked.length}`,
                  u.view.blocked.length,
                ),
          ),
        },
        {
          label: 'Horizon blocked',
          better: 'low',
          cells: per.map((u) =>
            u.unit.storey === null
              ? nothing
              : cell(`${Math.round(u.view.blocked_arc_deg)}°`, u.view.blocked_arc_deg),
          ),
        },
        {
          label: 'Facing',
          better: null,
          cells: per.map((u) => cell(u.unit.facing)),
        },
        {
          label: 'Low afternoon sun',
          better: 'low',
          cells: per.map((u) =>
            u.sun
              ? cell(
                  `${Math.round(u.sun.net_low_hours)} h a year`,
                  u.sun.net_low_hours,
                )
              : nothing,
          ),
        },
        {
          label: 'What the sun does',
          better: null,
          cells: per.map((u) => cell(u.sun?.says ?? null)),
        },
      ],
    },
    {
      name: 'Nearby',
      rows: [
        {
          label: 'Nearest MRT exit',
          better: 'low',
          cells: per.map((u) =>
            u.report.nearby.mrt_exit
              ? cell(
                  `${metres(u.report.nearby.mrt_exit.distance_m)} · ${titleCase(
                    u.report.nearby.mrt_station,
                  )}`,
                  u.report.nearby.mrt_exit.distance_m,
                )
              : nothing,
          ),
        },
        {
          label: 'Nearest bus stop',
          better: 'low',
          cells: per.map((u) =>
            cell(
              metres(u.report.nearby.bus_stop?.distance_m ?? null),
              u.report.nearby.bus_stop?.distance_m ?? null,
            ),
          ),
        },
        {
          label: 'Nearest hawker centre',
          better: 'low',
          cells: per.map((u) =>
            u.report.nearby.hawker
              ? cell(
                  `${metres(u.report.nearby.hawker.distance_m)} · ${
                    u.report.nearby.hawker.size ?? '?'
                  } stalls`,
                  u.report.nearby.hawker.distance_m,
                )
              : nothing,
          ),
        },
        {
          label: 'Nearest park',
          better: 'low',
          cells: per.map((u) =>
            cell(
              metres(u.report.nearby.park?.distance_m ?? null),
              u.report.nearby.park?.distance_m ?? null,
            ),
          ),
        },
        {
          label: 'Primary schools within 1 km',
          better: 'high',
          cells: per.map((u) =>
            cell(
              u.report.nearby.schools_within_1km !== null
                ? String(u.report.nearby.schools_within_1km)
                : null,
              u.report.nearby.schools_within_1km,
            ),
          ),
        },
        {
          label: 'Playgrounds and courts within 400 m',
          better: 'high',
          cells: per.map((u) => {
            const total = Object.values(u.report.nearby.facilities).reduce(
              (a, b) => a + b,
              0,
            );
            return cell(String(total), total);
          }),
        },
      ],
    },
    {
      name: 'Weather',
      rows: [
        {
          label: 'Wetness rank',
          better: 'high',
          cells: per.map((u) =>
            u.report.rain.rank !== null
              ? cell(
                  `${u.report.rain.rank} of ${u.report.rain.of}`,
                  // A higher rank number is a drier spot, which most people
                  // would call better.
                  u.report.rain.rank,
                )
              : nothing,
          ),
        },
        {
          label: 'Nearest gauge',
          better: null,
          cells: per.map((u) => cell(u.report.rain.station)),
        },
      ],
    },
  ];

  return groups;
}

/** Which columns tie for best in a row. Several can win. */
function winners(row: Row): Set<number> {
  if (!row.better) return new Set();
  const values = row.cells
    .map((c, i) => ({ i, v: c.value }))
    .filter((c): c is { i: number; v: number } => c.v !== null);
  if (values.length < 2) return new Set();
  const best =
    row.better === 'high'
      ? Math.max(...values.map((c) => c.v))
      : Math.min(...values.map((c) => c.v));
  return new Set(values.filter((c) => c.v === best).map((c) => c.i));
}

export function CompareTable({
  units,
  meta,
  onRemove,
}: {
  units: LoadedUnit[];
  meta: Meta;
  onRemove: (id: string) => void;
}) {
  const groups = React.useMemo(() => buildGroups(units, meta), [units, meta]);
  const [open, setOpen] = React.useState<Record<string, boolean>>(() =>
    Object.fromEntries(groups.map((g) => [g.name, true])),
  );

  if (units.length === 0) {
    return (
      <p className="empty">
        Nothing saved yet. Check a listing and save it, and it will appear here
        beside the others.
      </p>
    );
  }

  return (
    <div>
      <p className="secondary" style={{ marginBottom: 10 }}>
        Units are columns and attributes are rows, because twenty-five
        attributes the other way round would be unreadable. Each row marks the
        better value where there is an obvious better; there is no overall
        winner, on purpose.
      </p>

      {groups.map((group) => (
        <Collapsible.Root
          key={group.name}
          open={open[group.name] ?? true}
          onOpenChange={(value) =>
            setOpen((state) => ({ ...state, [group.name]: value }))
          }
        >
          <Collapsible.Trigger className="accordion-trigger">
            <span>{group.name}</span>
            <svg
              className="accordion-chevron"
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              aria-hidden="true"
            >
              <path d="M4 2l4 4-4 4" />
            </svg>
          </Collapsible.Trigger>
          <Collapsible.Panel className="accordion-panel">
            <div className="table-scroll">
              <table className="compare">
                <thead>
                  <tr>
                    <th scope="col">{group.name}</th>
                    {units.map(({ unit }) => (
                      <th scope="col" key={unit.id}>
                        {unit.block} {titleCase(unit.street)}
                        <div className="muted">
                          {unit.flatType}
                          {unit.storey ? ` · #${unit.storey}` : ''}
                          {unit.facing ? ` · ${unit.facing}` : ''}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {group.rows.map((row) => {
                    const best = winners(row);
                    return (
                      <tr key={row.label}>
                        <th scope="row">{row.label}</th>
                        {row.cells.map((c, i) => (
                          <td
                            key={units[i]?.unit.id ?? i}
                            className={best.has(i) ? 'better' : undefined}
                          >
                            {c.value === null && c.text === 'unknown' ? (
                              <span className="muted">unknown</span>
                            ) : (
                              c.text
                            )}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Collapsible.Panel>
        </Collapsible.Root>
      ))}

      <div className="row" style={{ marginTop: 16 }}>
        {units.map(({ unit }) => (
          <button
            key={unit.id}
            type="button"
            className="button button--quiet button--danger"
            onClick={() => onRemove(unit.id)}
          >
            Remove {unit.block} {titleCase(unit.street)}
          </button>
        ))}
      </div>
    </div>
  );
}
