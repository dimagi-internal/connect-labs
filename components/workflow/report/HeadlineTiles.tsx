/**
 * The headline row: a handful of indicators, each with its value, a band word,
 * a one-line sub, an optional progress bar, and the change since the previous
 * saved report. The page decides what each tile says; this draws it.
 */
import React from 'react';
import { BAND_TEXT, BAND_WORD, dateLbl, nCount, type Cell } from './format';

export interface TileSpec {
  id: string;
  label: string;
  /** Formats the value as a count ("1,234"). */
  count?: boolean;
  /** Formats the value as a percentage of a fraction (0.7 -> "70.0%"). */
  pct?: boolean;
  unit?: string;
  sub?: string;
  target?: number;
}

export interface Tile {
  spec: TileSpec;
  entry: Cell | null | undefined;
  /** Overrides `spec.sub`. */
  sub?: string;
  /** 0..100: draws a progress bar under the value. */
  progress?: number | null;
  /** The cell from the previous saved report, for the change line. */
  previous?: Cell | null;
  /** What the change is measured TO, when that is not `entry` (e.g. the newest
   * point of a history the tile's scope does not match exactly). */
  current?: Cell | null;
  previousDate?: string | null;
  minDenominator?: number;
}

export function tileValue(
  t: TileSpec,
  e: Cell | null | undefined,
  minDen = 20,
) {
  if (!e || e.value === null || e.value === undefined) return '—';
  if (e.band === 'insufficient') return 'n<' + minDen;
  if (t.count) return nCount(e.value);
  if (t.pct) return (100 * Number(e.value)).toFixed(1) + '%';
  return Number(e.value).toFixed(1);
}

/** "+1.2 pt since 8 Sep", "Unchanged since 8 Sep", or '' with no comparison. */
export function tileDelta(
  t: TileSpec,
  prev: Cell | null | undefined,
  cur: Cell | null | undefined,
  prevDate: string | null | undefined,
): string {
  if (
    !prev ||
    !cur ||
    prev.value === null ||
    prev.value === undefined ||
    cur.value === null ||
    cur.value === undefined
  )
    return '';
  const d = Number(cur.value) - Number(prev.value);
  const since = ' since ' + dateLbl(prevDate);
  if (t.pct) {
    if (Math.abs(d) < 0.0005) return 'Unchanged' + since;
    return (d > 0 ? '+' : '−') + (100 * Math.abs(d)).toFixed(1) + ' pt' + since;
  }
  if (Math.abs(d) < 0.05) return 'Unchanged' + since;
  const s = t.count ? nCount(Math.abs(d)) : Math.abs(d).toFixed(1);
  return (d > 0 ? '+' : '−') + s + since;
}

export function HeadlineTiles(props: { tiles: Tile[] }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
      {props.tiles.map(function (tile) {
        const t = tile.spec;
        const e = tile.entry;
        const band = e && e.band && BAND_WORD[e.band] ? e.band : null;
        const sub = tile.sub !== undefined ? tile.sub : t.sub || '';
        const delta = tileDelta(
          t,
          tile.previous,
          tile.current !== undefined ? tile.current : e,
          tile.previousDate,
        );
        return (
          <div
            key={t.id}
            className="bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-3"
          >
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 truncate">
              {t.label}
            </div>
            <div className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">
              {tileValue(t, e, tile.minDenominator)}
              {t.unit ? (
                <span className="ml-2 text-xs font-medium text-gray-400">
                  {t.unit}
                </span>
              ) : null}
            </div>
            <div className="mt-1 flex items-center justify-between gap-2 text-xs text-gray-600 whitespace-nowrap">
              <span className="truncate" title={sub}>
                {sub}
              </span>
              {band ? (
                <span className={'font-semibold ' + BAND_TEXT[band]}>
                  {BAND_WORD[band]}
                </span>
              ) : null}
            </div>
            {tile.progress !== null && tile.progress !== undefined ? (
              <div className="mt-2 h-1.5 rounded bg-gray-100 overflow-hidden">
                <div
                  className="h-full rounded bg-indigo-600"
                  style={{ width: Number(tile.progress).toFixed(1) + '%' }}
                />
              </div>
            ) : null}
            <div className="mt-1 text-xs text-gray-400 whitespace-nowrap truncate">
              {delta || '\u00a0'}
            </div>
          </div>
        );
      })}
    </div>
  );
}
