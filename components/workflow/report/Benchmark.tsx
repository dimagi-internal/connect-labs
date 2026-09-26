/**
 * The benchmark scorecard: one row per indicator, the reader's organisation
 * against the programme's other organisations, the others unnamed.
 *
 * Every organisation is on every row. One without a usable figure keeps its
 * place as a dashed outline and is counted in the coverage line with its
 * reason, so the peer set never changes from row to row -- the failure that
 * made the per-opportunity peer charts unreadable.
 */
import React from 'react';
import { fmtValue, type Measure } from './format';

export interface PeerFigure {
  value: number | null;
  band: string;
}

export const DRAWABLE = ['green', 'yellow', 'red', 'unbanded'];

const REASON: Record<string, string> = {
  insufficient: 'too few babies',
  notcredible: 'not credible',
  notinapp: 'not collected',
  unrecorded: 'not recorded',
  nodata: 'no data',
};

export function drawable(f: PeerFigure | null | undefined): boolean {
  return (
    !!f &&
    f.value !== null &&
    f.value !== undefined &&
    DRAWABLE.indexOf(f.band) !== -1
  );
}

/** The target a measure is graded against, in the value's own units. */
export function targetOf(m: Measure & { bands?: any; direction?: string }) {
  const b = m && m.bands;
  if (!b || !b.length || m.direction === 'mid2') return null;
  const t = Number(b[0]);
  if (isNaN(t)) return null;
  return m.unit === '%' ? t / 100 : t;
}

export interface Ranked {
  /** Best first; drawable figures, then withheld ones. */
  ordered: { figure: PeerFigure; mine: boolean }[];
  /** 1-based position of the reader among drawable figures, or null. */
  rank: number | null;
  /** Organisations with a drawable figure. */
  scored: number;
  total: number;
  /** "all 6 organisations" / "4 of 6 · 1 too few babies · 1 not credible". */
  coverage: string;
  /** Whether "best" has a direction (a two-sided measure has none). */
  ranked: boolean;
  /** Another organisation has exactly the reader's figure. */
  tied: boolean;
}

export function rankOrganisations(
  measure: Measure & { direction?: string },
  own: PeerFigure | null | undefined,
  others: PeerFigure[],
  /** VERSION 3: the entity's plural ("too few communities"); default babies. */
  opts?: { entityPlural?: string },
): Ranked {
  const dir = (measure && measure.direction) || 'higher';
  const ranked = dir === 'higher' || dir === 'lower';
  const all = (others || [])
    .map(function (f) {
      return { figure: f, mine: false };
    })
    .concat(own ? [{ figure: own, mine: true }] : []);
  const shown = all.filter(function (a) {
    return drawable(a.figure);
  });
  const held = all.filter(function (a) {
    return !drawable(a.figure);
  });
  shown.sort(function (a, b) {
    const d = Number(a.figure.value) - Number(b.figure.value);
    return dir === 'higher' ? -d : d;
  });
  // A tie shares the better place: rank is 1 + the organisations STRICTLY
  // better. Ordered by position, five organisations tied at 0.0 days ranked the
  // reader "5th of 6" on a lower-is-better indicator where it was joint best.
  const mine = shown.filter(function (a) {
    return a.mine;
  })[0];
  const pos = !mine
    ? -1
    : shown.filter(function (a) {
        const d = Number(a.figure.value) - Number(mine.figure.value);
        return dir === 'higher' ? d > 0 : d < 0;
      }).length;
  const reasons: Record<string, number> = {};
  held.forEach(function (a) {
    let r = REASON[a.figure.band] || 'no figure';
    if (a.figure.band === 'insufficient' && opts && opts.entityPlural)
      r = 'too few ' + opts.entityPlural;
    reasons[r] = (reasons[r] || 0) + 1;
  });
  const total = all.length;
  const coverage =
    shown.length === total
      ? 'all ' + total + ' organisations'
      : shown.length +
        ' of ' +
        total +
        Object.keys(reasons)
          .map(function (r) {
            return ' · ' + reasons[r] + ' ' + r;
          })
          .join('');
  const tied =
    !!mine &&
    shown.some(function (a) {
      return !a.mine && Number(a.figure.value) === Number(mine.figure.value);
    });
  return {
    ordered: shown.concat(held),
    tied,
    rank: pos === -1 ? null : pos + 1,
    scored: shown.length,
    total,
    coverage,
    ranked,
  };
}

export function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

/** Six small vertical bars, best to worst, the reader's in blue. */
export function MiniRankBars(props: {
  ranked: Ranked;
  target?: number | null;
  width?: number;
  height?: number;
}) {
  const W = props.width || 260;
  const H = props.height || 44;
  const items = props.ranked.ordered;
  const vals = items
    .filter(function (a) {
      return drawable(a.figure);
    })
    .map(function (a) {
      return Number(a.figure.value);
    });
  const target = props.target;
  let max = Math.max.apply(
    null,
    vals
      .concat(target !== null && target !== undefined ? [target] : [])
      .concat([0]),
  );
  if (max <= 0) max = 1;
  const gap = 8;
  const n = Math.max(items.length, 1);
  const bw = Math.min(34, (W - gap * (n - 1)) / n);
  const plot = H - 4;
  return (
    <svg
      width={W}
      height={H}
      viewBox={'0 0 ' + W + ' ' + H}
      role="img"
      aria-label="Organisations, best to worst"
    >
      {items.map(function (a, i) {
        const x = i * (bw + gap);
        if (!drawable(a.figure)) {
          return (
            <rect
              key={i}
              x={x + 0.75}
              y={H - 12}
              width={bw - 1.5}
              height={10}
              rx="3"
              fill="none"
              stroke="#b9b7ae"
              strokeWidth="1.5"
              strokeDasharray="3 3"
            >
              <title>{REASON[a.figure.band] || 'no figure'}</title>
            </rect>
          );
        }
        const h = Math.max(3, (plot * Number(a.figure.value)) / max);
        return (
          <rect
            key={i}
            x={x}
            y={H - 2 - h}
            width={bw}
            height={h}
            rx="3"
            fill={a.mine ? '#4f46e5' : '#c7c5bc'}
          />
        );
      })}
      {target !== null && target !== undefined ? (
        <line
          x1="0"
          x2={W}
          y1={H - 2 - (plot * target) / max}
          y2={H - 2 - (plot * target) / max}
          stroke="#1d1d24"
          strokeWidth="1.5"
          strokeDasharray="5 4"
        />
      ) : null}
    </svg>
  );
}

/** The full chart: one labelled horizontal bar per organisation, best first. */
export function RankedBars(props: {
  measure: Measure;
  ranked: Ranked;
  ownLabel: string;
  target?: number | null;
}) {
  const m = props.measure;
  const items = props.ranked.ordered;
  const vals = items
    .filter(function (a) {
      return drawable(a.figure);
    })
    .map(function (a) {
      return Number(a.figure.value);
    });
  const target = props.target;
  let max = Math.max.apply(
    null,
    vals
      .concat(target !== null && target !== undefined ? [target] : [])
      .concat([0]),
  );
  if (max <= 0) max = 1;
  return (
    <div style={{ position: 'relative' }} className="flex flex-col gap-2">
      {target !== null && target !== undefined ? (
        <div
          style={{
            position: 'absolute',
            top: -4,
            bottom: -4,
            left: 'calc(172px + (100% - 172px - 132px) * ' + target / max + ')',
            borderLeft: '2px dashed #1d1d24',
            zIndex: 1,
          }}
        />
      ) : null}
      {items.map(function (a, i) {
        const ok = drawable(a.figure);
        const w = ok ? Math.max(1.5, (100 * Number(a.figure.value)) / max) : 0;
        return (
          <div
            key={i}
            className="grid items-center gap-3"
            style={{
              gridTemplateColumns: '160px minmax(0, 1fr) 120px',
              height: 24,
            }}
          >
            <div
              className={
                'text-sm truncate ' +
                (a.mine ? 'font-bold text-indigo-700' : 'text-gray-500')
              }
            >
              {a.mine ? props.ownLabel : 'Another organisation'}
            </div>
            <div className="h-4 rounded bg-gray-100 relative">
              <div
                className="absolute left-0 top-0 bottom-0 rounded"
                style={{
                  width: w + '%',
                  background: a.mine ? '#4f46e5' : '#c7c5bc',
                }}
              />
            </div>
            <div
              className={
                'text-sm tabular-nums ' +
                (!ok
                  ? 'italic text-gray-500'
                  : a.mine
                    ? 'font-bold text-indigo-700'
                    : 'text-gray-700')
              }
            >
              {ok
                ? fmtValue(m, a.figure.value)
                : (a.figure.value !== null && a.figure.value !== undefined
                    ? fmtValue(m, a.figure.value) + ' · '
                    : '') + (REASON[a.figure.band] || 'no figure')}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** A row of tabs. The page holds the active id. */
export function Tabs(props: {
  tabs: { id: string; label: string }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-gray-200" role="tablist">
      {props.tabs.map(function (t) {
        const on = t.id === props.active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={function () {
              props.onChange(t.id);
            }}
            className={
              'px-4 py-2 text-sm font-semibold -mb-px border-b-2 ' +
              (on
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-800')
            }
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
