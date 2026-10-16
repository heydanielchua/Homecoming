/**
 * What the block has actually done, against its town and the whole market.
 *
 * Two decisions about this chart are worth stating, because both are places
 * it would have been easy to mislead.
 *
 * **Everything is indexed to 100 at the first quarter all three series
 * share.** The block and the town are medians in dollars per square metre;
 * the national series is the HDB Resale Price Index, which is already an
 * index on a different base. Plotting dollars against index points would need
 * two y-axes, and a two-axis chart lets the author decide which line looks
 * higher by choosing the scales. Indexing to a common base puts them on one
 * axis, which is both honest and the thing a reader actually wants: not "what
 * does it cost" — the money section answers that — but "has this block kept
 * up".
 *
 * **The medians are mix-adjusted**, and the note under the chart says so. A
 * block that happened to sell two high floors one quarter and two low floors
 * the next looks like it fell ten per cent and then recovered; reading that
 * as movement is the single easiest mistake to make with this data.
 *
 * The three colours are the first three slots of the validated categorical
 * palette. Aqua is below 3:1 on the light surface, so the end labels and the
 * table view below are not optional extras — they are the relief that makes
 * that slot usable.
 */

import * as React from 'react';

import { quarter as formatQuarter } from '../lib/format';
import { psfOf } from '../lib/units';
import type { TrendPoint } from '../lib/types';

/** One line's worth of data, after indexing. */
interface Series {
  key: string;
  name: string;
  colour: string;
  points: { quarter: string; value: number; raw: number | null; n?: number }[];
}

interface Props {
  block: TrendPoint[];
  town: TrendPoint[];
  national: { quarter: string; index: number }[];
  blockLabel: string;
  townLabel: string;
  note: string;
}

const WIDTH = 720;
const HEIGHT = 260;
const PAD = { top: 16, right: 56, bottom: 28, left: 40 };

function quarterKey(quarter: string): number {
  const [year, q] = quarter.split('-Q');
  return Number(year) * 4 + Number(q) - 1;
}

/** Index a series to 100 at `base`, dropping anything before it. */
function indexed(
  points: { quarter: string; value: number; n?: number }[],
  base: string,
): { quarter: string; value: number; raw: number; n?: number }[] {
  const baseKey = quarterKey(base);
  const basePoint = points.find((p) => p.quarter === base);
  if (!basePoint || basePoint.value === 0) return [];
  return points
    .filter((p) => quarterKey(p.quarter) >= baseKey)
    .map((p) => ({
      quarter: p.quarter,
      value: (100 * p.value) / basePoint.value,
      raw: p.value,
      n: p.n,
    }));
}

export function TrendChart({
  block,
  town,
  national,
  blockLabel,
  townLabel,
  note,
}: Props) {
  const [hovered, setHovered] = React.useState<string | null>(null);
  const [showTable, setShowTable] = React.useState(false);

  const series = React.useMemo<Series[]>(() => {
    const raw = [
      {
        key: 'block',
        name: blockLabel,
        colour: 'var(--series-1)',
        points: block.map((p) => ({ quarter: p.quarter, value: p.psm, n: p.n })),
      },
      {
        key: 'town',
        name: townLabel,
        colour: 'var(--series-2)',
        points: town.map((p) => ({ quarter: p.quarter, value: p.psm, n: p.n })),
      },
      {
        key: 'national',
        name: 'Whole market',
        colour: 'var(--series-3)',
        points: national.map((p) => ({ quarter: p.quarter, value: p.index })),
      },
    ].filter((s) => s.points.length > 1);

    if (raw.length === 0) return [];

    // The base is the first quarter every series present has a value for, so
    // all three start at 100 and the comparison is of growth from one moment.
    const shared = raw
      .map((s) => new Set(s.points.map((p) => p.quarter)))
      .reduce((a, b) => new Set([...a].filter((q) => b.has(q))));
    const base = [...shared].sort((a, b) => quarterKey(a) - quarterKey(b))[0];
    if (!base) return [];

    return raw
      .map((s) => ({ ...s, points: indexed(s.points, base) }))
      .filter((s) => s.points.length > 1);
  }, [block, town, national, blockLabel, townLabel]);

  if (series.length === 0) {
    return (
      <p className="secondary">
        Not enough sales in this block to draw a trend. A point needs a year of
        sales behind it, and this block has not had one.
      </p>
    );
  }

  const quarters = [
    ...new Set(series.flatMap((s) => s.points.map((p) => p.quarter))),
  ].sort((a, b) => quarterKey(a) - quarterKey(b));

  const values = series.flatMap((s) => s.points.map((p) => p.value));
  const min = Math.min(100, ...values);
  const max = Math.max(100, ...values);
  const padding = Math.max(4, (max - min) * 0.08);
  const low = Math.floor((min - padding) / 10) * 10;
  const high = Math.ceil((max + padding) / 10) * 10;

  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (q: string) =>
    PAD.left + (quarters.indexOf(q) / Math.max(1, quarters.length - 1)) * plotW;
  const y = (v: number) => PAD.top + plotH - ((v - low) / (high - low)) * plotH;

  // Clean round ticks, which carry the values that are not directly labelled.
  const ticks: number[] = [];
  const step = high - low > 120 ? 40 : high - low > 60 ? 20 : 10;
  for (let value = low; value <= high; value += step) ticks.push(value);

  const yearLabels = quarters.filter((q) => q.endsWith('-Q1'));
  const hoveredQuarter = hovered ?? null;

  return (
    <div className="chart">
      <figure className="chart-figure">
        <svg
          className="chart-svg"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={
            `Price per square metre indexed to 100 at ${formatQuarter(quarters[0])}: ` +
            series
              .map(
                (s) =>
                  `${s.name} ends at ${Math.round(
                    s.points[s.points.length - 1]!.value,
                  )}`,
              )
              .join('; ')
          }
        >
          {ticks.map((value) => (
            <g key={value}>
              <line
                className="chart-grid"
                x1={PAD.left}
                x2={WIDTH - PAD.right}
                y1={y(value)}
                y2={y(value)}
              />
              <text
                className="chart-tick"
                x={PAD.left - 7}
                y={y(value)}
                textAnchor="end"
                dominantBaseline="middle"
              >
                {value}
              </text>
            </g>
          ))}

          {/* 100 is the base, so it is the one line worth more than a grid. */}
          <line
            className="chart-axis"
            x1={PAD.left}
            x2={WIDTH - PAD.right}
            y1={y(100)}
            y2={y(100)}
          />

          {yearLabels.map((q) => (
            <text
              key={q}
              className="chart-tick"
              x={x(q)}
              y={HEIGHT - PAD.bottom + 15}
              textAnchor="middle"
            >
              {q.slice(0, 4)}
            </text>
          ))}

          {hoveredQuarter ? (
            <line
              className="chart-crosshair"
              x1={x(hoveredQuarter)}
              x2={x(hoveredQuarter)}
              y1={PAD.top}
              y2={PAD.top + plotH}
            />
          ) : null}

          {series.map((s) => (
            <path
              key={s.key}
              className="chart-line"
              stroke={s.colour}
              d={s.points
                .map(
                  (p, i) =>
                    `${i === 0 ? 'M' : 'L'}${x(p.quarter).toFixed(1)} ${y(p.value).toFixed(1)}`,
                )
                .join(' ')}
            />
          ))}

          {/* End markers and labels. Three series, so each is directly
              labelled as well as in the legend — identity never rests on
              colour alone. */}
          {series.map((s, i) => {
            const last = s.points[s.points.length - 1]!;
            // Nudge labels apart only enough to stop an exact overlap; past
            // that the legend carries it.
            const others = series
              .filter((_, j) => j < i)
              .map((o) => y(o.points[o.points.length - 1]!.value));
            let labelY = y(last.value);
            while (others.some((o) => Math.abs(o - labelY) < 11)) labelY += 11;
            return (
              <g key={s.key}>
                <circle
                  className="chart-end-dot"
                  cx={x(last.quarter)}
                  cy={y(last.value)}
                  r={4}
                  fill={s.colour}
                />
                <text
                  className="chart-end-label"
                  x={x(last.quarter) + 9}
                  y={labelY}
                  dominantBaseline="middle"
                >
                  {Math.round(last.value)}
                </text>
              </g>
            );
          })}

          {/* The crosshair finds the X: a reader aims at a quarter, never at a
              2px line. Each band is a hit target wider than any mark. */}
          {quarters.map((q) => (
            <rect
              key={q}
              className="chart-hit"
              x={x(q) - plotW / Math.max(1, quarters.length - 1) / 2}
              width={Math.max(8, plotW / Math.max(1, quarters.length - 1))}
              y={PAD.top}
              height={plotH}
              onPointerEnter={() => setHovered(q)}
              onFocus={() => setHovered(q)}
              onBlur={() => setHovered(null)}
              tabIndex={0}
              role="button"
              aria-label={`${formatQuarter(q)}: ${series
                .map((s) => {
                  const point = s.points.find((p) => p.quarter === q);
                  return point
                    ? `${s.name} ${Math.round(point.value)}`
                    : `${s.name} no data`;
                })
                .join(', ')}`}
            />
          ))}
        </svg>
      </figure>

      <div className="legend">
        {series.map((s) => (
          <span className="legend-item" key={s.key}>
            <span
              className="legend-key"
              style={{ background: s.colour }}
              aria-hidden="true"
            />
            {s.name}
          </span>
        ))}
      </div>

      {/* One readout, every series: the pointer never has to land on a line. */}
      <div className="readout" aria-live="polite">
        {hoveredQuarter ? (
          <>
            <span className="readout-quarter">
              {formatQuarter(hoveredQuarter)}
            </span>
            <div className="readout-rows">
              {series.map((s) => {
                const point = s.points.find((p) => p.quarter === hoveredQuarter);
                return (
                  <span className="readout-row" key={s.key}>
                    <span
                      className="legend-key"
                      style={{ background: s.colour }}
                      aria-hidden="true"
                    />
                    <span className="readout-value">
                      {point ? Math.round(point.value) : '—'}
                    </span>
                    <span className="readout-name">
                      {s.name}
                      {point && s.key !== 'national'
                        ? ` · ${psfOf(point.raw)}`
                        : ''}
                    </span>
                  </span>
                );
              })}
            </div>
          </>
        ) : (
          <span className="secondary">
            Indexed to 100 at {formatQuarter(quarters[0])}, so the three lines
            compare growth rather than price. Hover or tab across for a quarter.
          </span>
        )}
      </div>

      <p className="muted" style={{ marginTop: 8 }}>
        {note}
      </p>

      <button
        type="button"
        className="button button--quiet"
        onClick={() => setShowTable((open) => !open)}
        aria-expanded={showTable}
      >
        {showTable ? 'Hide the numbers' : 'Show the numbers'}
      </button>

      {showTable ? (
        <div className="table-scroll">
          <table>
            <caption className="muted" style={{ captionSide: 'bottom' }}>
              Index, and the median price per square metre behind it.
            </caption>
            <thead>
              <tr>
                <th scope="col">Quarter</th>
                {series.map((s) => (
                  <th scope="col" className="num" key={s.key}>
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...quarters].reverse().map((q) => (
                <tr key={q}>
                  <th scope="row">{formatQuarter(q)}</th>
                  {series.map((s) => {
                    const point = s.points.find((p) => p.quarter === q);
                    return (
                      <td className="num" key={s.key}>
                        {point ? (
                          <>
                            {Math.round(point.value)}
                            {s.key !== 'national' ? (
                              <span className="muted">
                                {' '}
                                {psfOf(point.raw)}
                              </span>
                            ) : null}
                          </>
                        ) : (
                          <span className="muted">no sales</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
