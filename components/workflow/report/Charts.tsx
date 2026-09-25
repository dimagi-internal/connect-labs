/**
 * Charts for indicator reports, hand-drawn SVG (no chart library): activity by
 * week, and one indicator across saved reports.
 */
import React from 'react';
import {
  BAND_TEXT,
  BAND_WORD,
  bandColour,
  dateLbl,
  nCount,
  type Cell,
} from './format';

export interface Week {
  week: string;
  visits?: number;
  registered?: number;
}

/** Bars = babies registered, line = visits, on ONE scale. */
export function WeeklyActivity(props: { weeks: Week[] }) {
  const weeks = props.weeks || [];
  const W = 720,
    H = 200,
    L = 42,
    R = 12,
    T = 14,
    B = 28;
  if (!weeks.length)
    return (
      <div className="text-xs text-gray-400 py-10 text-center">
        No dated visits in this scope.
      </div>
    );
  let max = 1;
  weeks.forEach(function (w) {
    max = Math.max(max, w.visits || 0, w.registered || 0);
  });
  const step = Math.pow(10, Math.floor(Math.log(max) / Math.LN10));
  const top = Math.ceil(max / step) * step;
  const iw = W - L - R,
    ih = H - T - B;
  const bw = iw / weeks.length;
  function y(v: number) {
    return T + ih - (v / top) * ih;
  }
  const ticks = [0, top / 2, top];
  const path = weeks
    .map(function (w, i) {
      return (
        (i ? 'L' : 'M') +
        (L + i * bw + bw / 2).toFixed(1) +
        ' ' +
        y(w.visits || 0).toFixed(1)
      );
    })
    .join(' ');
  return (
    <svg
      viewBox={'0 0 ' + W + ' ' + H}
      className="w-full h-auto block"
      role="img"
      aria-label="Registrations and visits by week"
    >
      {ticks.map(function (t) {
        return (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#eeeef4" />
            <text
              x={L - 6}
              y={y(t) + 4}
              fontSize="10"
              fill="#9ca3af"
              textAnchor="end"
            >
              {nCount(t)}
            </text>
          </g>
        );
      })}
      {weeks.map(function (w, i) {
        const x = L + i * bw;
        const h = ih - (y(w.registered || 0) - T);
        return (
          <g key={w.week}>
            <rect
              x={(x + bw * 0.2).toFixed(1)}
              y={y(w.registered || 0).toFixed(1)}
              width={(bw * 0.6).toFixed(1)}
              height={h.toFixed(1)}
              rx="2"
              fill="#a5b4fc"
            >
              <title>
                {'Week of ' +
                  dateLbl(w.week) +
                  ': ' +
                  nCount(w.registered) +
                  ' registered, ' +
                  nCount(w.visits) +
                  ' visits'}
              </title>
            </rect>
            {i % 4 === 0 || i === weeks.length - 1 ? (
              <text
                x={x + bw / 2}
                y={H - 8}
                fontSize="10"
                fill="#9ca3af"
                textAnchor="middle"
              >
                {dateLbl(w.week)}
              </text>
            ) : null}
          </g>
        );
      })}
      <path
        d={path}
        fill="none"
        stroke="#4f46e5"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      {weeks.map(function (w, i) {
        return (
          <circle
            key={'v' + w.week}
            cx={L + i * bw + bw / 2}
            cy={y(w.visits || 0)}
            r={i === weeks.length - 1 ? 4 : 2.5}
            fill="#4f46e5"
            stroke="#fff"
            strokeWidth="1.5"
          >
            <title>
              {'Week of ' +
                dateLbl(w.week) +
                ': ' +
                nCount(w.visits) +
                ' visits, ' +
                nCount(w.registered) +
                ' registered'}
            </title>
          </circle>
        );
      })}
    </svg>
  );
}

/** The activity chart in its card, with its legend. */
export function WeeklyActivityCard(props: {
  weeks: Week[];
  title?: string;
  className?: string;
}) {
  return (
    <div
      className={
        'bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-2 ' +
        (props.className || '')
      }
    >
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <div className="text-sm font-semibold text-gray-900">
          {props.title || 'Registrations and visits by week'}
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span>
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm mr-1 align-middle"
              style={{ background: '#a5b4fc' }}
            />
            Babies registered
          </span>
          <span>
            <span
              className="inline-block w-3 h-0.5 mr-1 align-middle"
              style={{ background: '#4f46e5' }}
            />
            Visits
          </span>
        </div>
      </div>
      <WeeklyActivity weeks={props.weeks} />
    </div>
  );
}

export interface TrendPoint {
  date: string;
  /** Null draws a gap: a report with too few cases to score, not a zero. */
  entry: Cell | null;
}

/**
 * One indicator across saved reports, small-multiple sized. `points` is one per
 * report, oldest first. `loading` (or `points` of null) shows a placeholder:
 * "one report so far" while the history is still on its way would read as a
 * fact about the data. The header's band word still comes from the newest
 * point in hand, so it shows while the rest loads.
 */
export function TrendCard(props: {
  label: string;
  title?: string;
  points: TrendPoint[] | null;
  pct?: boolean;
  target: number;
  format: (e: Cell) => string;
  loading?: boolean;
}) {
  const label = props.label;
  const pct = !!props.pct;
  const target = props.target;
  const history = props.points;
  const pts = (history || []).map(function (p) {
    const e = p.entry;
    if (!e || e.value === null || e.value === undefined) return null;
    if (e.band === 'insufficient' || e.band === 'notcredible') return null;
    return { v: Number(e.value), e: e, date: p.date, n: e.n };
  });
  const W = 260,
    H = 130,
    L = 36,
    R = 10,
    T = 12,
    B = 22;
  const n = pts.length;
  const real = pts.filter(Boolean) as NonNullable<(typeof pts)[number]>[];
  const cur = real.length ? real[real.length - 1].e : null;
  let body: React.ReactNode;
  if (history === null || props.loading) {
    body = (
      <div
        className="relative rounded bg-gray-50 animate-pulse"
        style={{ height: H }}
        aria-busy="true"
      >
        <div className="absolute inset-x-3 top-1/2 border-t border-dashed border-gray-200" />
        <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-400">
          Loading the trend across saved reports…
        </div>
      </div>
    );
  } else if (real.length < 2) {
    body = (
      <div className="text-xs text-gray-400 py-8 text-center">
        {real.length
          ? 'One report so far — the line builds as reports are saved weekly.'
          : 'No report has enough cases to score this yet.'}
      </div>
    );
  } else {
    let lo = target,
      hi = target;
    real.forEach(function (p) {
      lo = Math.min(lo, p.v);
      hi = Math.max(hi, p.v);
    });
    if (pct) {
      lo = Math.max(0, Math.floor((lo - 0.05) * 10) / 10);
      hi = Math.min(1, Math.ceil((hi + 0.05) * 10) / 10);
    } else {
      lo = Math.floor(lo - 1);
      hi = Math.ceil(hi + 1);
    }
    if (hi <= lo) hi = lo + 1;
    const iw = W - L - R,
      ih = H - T - B;
    const x = function (i: number) {
      return L + (n > 1 ? (i * iw) / (n - 1) : iw / 2);
    };
    const y = function (v: number) {
      return T + ih - ((v - lo) / (hi - lo)) * ih;
    };
    const f = function (v: number) {
      return pct ? Math.round(v * 100) + '%' : Number(v).toFixed(0);
    };
    let d = '';
    let pen = false;
    pts.forEach(function (p, i) {
      if (!p) {
        pen = false;
        return;
      }
      d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p.v).toFixed(1) + ' ';
      pen = true;
    });
    const last = real[real.length - 1];
    const lastIdx = pts.lastIndexOf(last);
    const yTicks = [lo, (lo + hi) / 2, hi];
    const xTicks = [0, Math.floor((n - 1) / 2), n - 1];
    const dotColor = bandColour(last.e.band);
    body = (
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        className="w-full h-auto block"
        role="img"
        aria-label={label}
      >
        {yTicks.map(function (t) {
          return (
            <g key={t}>
              <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#eeeef4" />
              <text
                x={L - 5}
                y={y(t) + 3.5}
                fontSize="9.5"
                fill="#9ca3af"
                textAnchor="end"
              >
                {f(t)}
              </text>
            </g>
          );
        })}
        <line
          x1={L}
          x2={W - R}
          y1={y(target)}
          y2={y(target)}
          stroke="#c3c6d3"
          strokeDasharray="3 3"
        />
        <text
          x={W - R}
          y={y(target) - 3}
          fontSize="9"
          fill="#9ca3af"
          textAnchor="end"
        >
          {'target ' + f(target)}
        </text>
        <path
          d={d}
          fill="none"
          stroke="#4f46e5"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        {pts.map(function (p, i) {
          if (!p) return null;
          const isLast = i === lastIdx;
          return (
            <circle
              key={p.date}
              cx={x(i)}
              cy={y(p.v)}
              r={isLast ? 4.5 : 3}
              fill={isLast ? dotColor : '#4f46e5'}
              stroke="#fff"
              strokeWidth="1.5"
            >
              <title>
                {'As of ' +
                  dateLbl(p.date) +
                  ': ' +
                  props.format(p.e) +
                  ' (n = ' +
                  nCount(p.n) +
                  ')'}
              </title>
            </circle>
          );
        })}
        {xTicks.map(function (i) {
          return (
            <text
              key={'x' + i}
              x={x(i)}
              y={H - 7}
              fontSize="9.5"
              fill="#9ca3af"
              textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}
            >
              {dateLbl((history || [])[i] && (history || [])[i].date)}
            </text>
          );
        })}
      </svg>
    );
  }
  return (
    <div className="bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-2">
      <div className="flex items-baseline justify-between gap-2">
        <div
          className="text-sm font-semibold text-gray-900 whitespace-nowrap"
          title={props.title}
        >
          {label}
        </div>
        {cur && cur.band && BAND_WORD[cur.band] ? (
          <span className={'text-xs font-semibold ' + BAND_TEXT[cur.band]}>
            {BAND_WORD[cur.band]}
          </span>
        ) : null}
      </div>
      {body}
    </div>
  );
}
