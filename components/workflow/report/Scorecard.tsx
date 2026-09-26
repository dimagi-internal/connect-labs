/**
 * Scorecard pieces: what one graded cell prints, the attention count, and the
 * legend under a banded table. A report keeps its own table layout (its
 * leading and trailing columns differ) and uses these for the cells, so every
 * report's scorecard reads the same.
 */
import React from 'react';
import { fmtValue, nCount, tintFor, type Cell, type Measure } from './format';

export interface ScorecardColumn {
  id: string;
  label: string;
  title?: string;
  /** Show the cell's denominator (`n`) instead of its value. */
  denOnly?: boolean;
}

/** What a scorecard cell prints for a column. */
export function ScoreCellText(props: {
  column: ScorecardColumn;
  entry: Cell | null | undefined;
  measure?: Measure | null;
  minDenominator?: number;
  /** VERSION 3: the tooltip on a not-credible figure. */
  notCredibleTitle?: string;
}) {
  const c = props.column;
  const e = props.entry;
  const m = props.measure || {};
  if (!e) return <>{'—'}</>;
  if (c.denOnly) return <>{e.n ? nCount(e.n) : '—'}</>;
  if (e.band === 'insufficient')
    return (
      <span className="text-gray-400">
        n&lt;{m.min_denominator || props.minDenominator || 20}
      </span>
    );
  if (e.value === null || e.value === undefined) return <>{'—'}</>;
  const text = fmtValue(m, e.value);
  if (e.band === 'notcredible')
    return (
      <span
        className="text-slate-400"
        title={
          props.notCredibleTitle ||
          'Death recording is not credible for this organisation'
        }
      >
        {text}
      </span>
    );
  return <>{text}</>;
}

/** A scorecard `<td>`: tinted by band, right-aligned, tabular figures. */
export function ScoreCell(props: {
  column: ScorecardColumn;
  entry: Cell | null | undefined;
  measure?: Measure | null;
  minDenominator?: number;
  highlight?: boolean;
  title?: string;
  notCredibleTitle?: string;
}) {
  const tint = props.column.denOnly ? '' : tintFor(props.entry);
  return (
    <td
      className={
        'px-1.5 py-2 text-right tabular-nums ' +
        (props.highlight ? 'bg-indigo-50 ' : '') +
        tint
      }
      title={props.title}
    >
      <ScoreCellText {...props} />
    </td>
  );
}

/** What a scorecard cell sorts on: what it shows. Withheld cells sort last. */
export function scoreSortValue(
  column: ScorecardColumn | null | undefined,
  entry: Cell | null | undefined,
): number | null {
  if (!column || !entry) return null;
  if (column.denOnly) return entry.n || null;
  if (
    entry.band === 'insufficient' ||
    entry.band === 'nodata' ||
    entry.band === 'notinapp' ||
    entry.band === 'unrecorded'
  )
    return null;
  return entry.value === null || entry.value === undefined
    ? null
    : Number(entry.value);
}

/** The attention `<td>`: off-target count in red, else watch count in amber. */
export function AttentionCell(props: { reds: number; yellows: number }) {
  const reds = props.reds;
  const yellows = props.yellows;
  return (
    <td className="px-1.5 py-2 text-right">
      {reds ? (
        <span
          className="inline-block px-1.5 py-0.5 rounded-md text-xs font-semibold bg-red-100 text-red-800 text-center"
          title={reds + ' off target · ' + (yellows || 0) + ' to watch'}
        >
          {reds}
        </span>
      ) : yellows ? (
        <span
          className="inline-block px-1.5 py-0.5 rounded-md text-xs font-semibold bg-amber-100 text-amber-800 text-center"
          title={yellows + ' to watch'}
        >
          {yellows}
        </span>
      ) : (
        <span className="text-gray-300">0</span>
      )}
    </td>
  );
}

export function ScorecardLegend(props: {
  right?: React.ReactNode;
  minDenominator?: number;
}) {
  return (
    <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100 flex items-center gap-4 flex-wrap">
      <span>
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-100 border border-red-400 mr-1 align-middle" />
        Off target
      </span>
      <span>
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-100 border border-amber-400 mr-1 align-middle" />
        Watch
      </span>
      <span>
        n&lt;{props.minDenominator || 20} = below the minimum denominator
      </span>
      <span className="ml-auto">{props.right}</span>
    </div>
  );
}
