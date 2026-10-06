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
  /** The progress bar's fill (default indigo), e.g. the value's band colour. */
  progressColour?: string;
  /** 0..100: a tick on the progress bar where the target sits. */
  progressTarget?: number | null;
  /** 'lower' shows a "lower is better" chip beside the label. */
  direction?: string | null;
  /** Makes the label a button (e.g. to open the indicator's definition). */
  onLabelClick?: () => void;
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
            <div
              className="flex items-center justify-between text-xs whitespace-nowrap"
              style={{ minWidth: 0, gap: 4 }}
            >
              {tile.onLabelClick ? (
                <button
                  type="button"
                  className="font-semibold uppercase tracking-wide text-gray-500 truncate text-left hover:text-indigo-700 underline decoration-dotted decoration-gray-300 underline-offset-2"
                  style={{ minWidth: 0 }}
                  onClick={tile.onLabelClick}
                >
                  {t.label}
                </button>
              ) : (
                <span className="font-semibold uppercase tracking-wide text-gray-500 truncate">
                  {t.label}
                </span>
              )}
              {tile.direction === 'lower' ? (
                <span
                  className="rounded bg-gray-100 text-gray-600"
                  style={{
                    padding: '0 4px',
                    fontSize: 11,
                    lineHeight: '16px',
                    flexShrink: 0,
                  }}
                >
                  lower is better
                </span>
              ) : null}
            </div>
            <div className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">
              {tileValue(t, e, tile.minDenominator)}
              {t.unit ? (
                <span className="ml-2 text-xs font-medium text-gray-500">
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
              <div
                className="mt-2 h-1.5 rounded bg-gray-100"
                style={{ position: 'relative' }}
                title={
                  tile.progressTarget !== null &&
                  tile.progressTarget !== undefined
                    ? 'target ' + Number(tile.progressTarget).toFixed(1) + '%'
                    : undefined
                }
              >
                <div
                  className={
                    'h-full rounded' +
                    (tile.progressColour ? '' : ' bg-indigo-600')
                  }
                  style={{
                    width:
                      Math.max(0, Math.min(100, Number(tile.progress))).toFixed(
                        1,
                      ) + '%',
                    background: tile.progressColour || undefined,
                  }}
                />
                {tile.progressTarget !== null &&
                tile.progressTarget !== undefined ? (
                  <div
                    data-target-tick="1"
                    style={{
                      position: 'absolute',
                      left:
                        'calc(' +
                        Math.max(
                          0,
                          Math.min(100, Number(tile.progressTarget)),
                        ) +
                        '% - 1px)',
                      top: -3,
                      width: 2,
                      height: 12,
                      background: '#111827',
                    }}
                  />
                ) : null}
              </div>
            ) : null}
            <div className="mt-1 text-xs text-gray-500 whitespace-nowrap truncate">
              {delta || '\u00a0'}
            </div>
          </div>
        );
      })}
    </div>
  );
}
