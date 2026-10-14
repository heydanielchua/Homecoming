/**
 * The marker that sits beside every figure saying how sure it is.
 *
 * This is the PRD's central rule made visible: fact, worked out, estimated,
 * or unknown with the reason. The four levels carry a shape as well as a
 * colour — a filled dot, a hollow ring, a rotated square — because a reader
 * who cannot tell the green from the amber still has to be able to tell a
 * sale price from a height band.
 *
 * `Figure` renders the value and the marker together, and when the value is
 * missing it shows the reason in its place. There is deliberately no code
 * path here that renders a dash or a zero for a missing number.
 */

import { Tooltip } from '@base-ui/react/tooltip';

import { CONFIDENCE_EXPLAIN, CONFIDENCE_LABEL, figureValue } from '../lib/format';
import type { Confidence as Level, Figure as FigureData } from '../lib/types';

export function ConfidenceMark({
  level,
  note,
}: {
  level: Level;
  note?: string;
}) {
  const label = CONFIDENCE_LABEL[level] ?? level;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger
        render={
          <span className={`conf conf--${level}`}>
            <span className="conf-dot" aria-hidden="true" />
            {label}
          </span>
        }
      />
      <Tooltip.Portal>
        <Tooltip.Positioner sideOffset={6}>
          <Tooltip.Popup className="popup">
            <Tooltip.Arrow className="popup-arrow" />
            <strong>{label}.</strong> {CONFIDENCE_EXPLAIN[level]}
            {note ? (
              <>
                {' '}
                <span className="secondary">{note}</span>
              </>
            ) : null}
          </Tooltip.Popup>
        </Tooltip.Positioner>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

/**
 * One labelled figure.
 *
 * `fallback` is for the handful of places where the figure's own reason is
 * already shown elsewhere on the page; everywhere else the reason is the
 * value, which is the point.
 */
export function Figure({
  label,
  figure,
  fallback,
  format,
}: {
  label: string;
  // A figure can hold a month or a quarter as readily as a number, so this is
  // deliberately not `Figure<number>`.
  figure: FigureData<number | string> | undefined;
  fallback?: string;
  format?: (value: number) => string;
}) {
  const known = figure && figure.v !== null;
  const shown =
    known && format && typeof figure.v === 'number'
      ? format(figure.v)
      : figureValue(figure);

  return (
    <div className="figure">
      <div className="figure-label">
        <span>{label}</span>
        {figure ? <ConfidenceMark level={figure.c} /> : null}
      </div>
      {known ? (
        <div className="figure-value">{shown}</div>
      ) : (
        <div className="figure-value figure-value--unknown">
          {figure?.n ?? fallback ?? 'not available'}
        </div>
      )}
      {known && figure?.n ? <div className="figure-note">{figure.n}</div> : null}
    </div>
  );
}

/** A plain value with a marker, for use inside prose and table cells. */
export function Marked({
  children,
  level,
  note,
}: {
  children: React.ReactNode;
  level: Level;
  note?: string;
}) {
  return (
    <span>
      {children} <ConfidenceMark level={level} note={note} />
    </span>
  );
}
