/**
 * Anonymous peer comparisons, as served by /labs/benchmarks/api/<opp>/:
 * where an opportunity sits among its peers today, and how it got there.
 *
 * Peers are anonymous and re-sorted per indicator, so a bar cannot be followed
 * from one chart to the next. The reader's own figure is the blue bar and the
 * blue line.
 */
import React from 'react';
import { fmtValue, type Measure } from './format';

export interface PeerPoint {
  value: number | string;
  peer_index?: number;
}

/**
 * Bars low to high. The reader's own bar is INSERTED by rank, never matched
 * against a peer: its figure may be newer than the published peers', and a
 * value-match would then colour some OTHER opportunity's bar and call it this
 * one. The store drops the reader's own published row, so it is drawn once.
 */
export function PeerBars(props: {
  peers: PeerPoint[];
  measure: Measure;
  own?: number | string | null;
}) {
  const measure = props.measure;
  const values: number[] = [];
  (props.peers || []).forEach(function (p) {
    const v = Number(p.value);
    if (v === v) values.push(v);
  });
  if (!values.length) return null;
  const own = props.own;
  const ownNum =
    own === null || own === undefined || Number(own) !== Number(own)
      ? null
      : Number(own);
  const all = values.concat(ownNum === null ? [] : [ownNum]);
  const top = Math.max.apply(null, all);
  let base = Math.min.apply(null, all);
  if (base > 0) base = 0;
  const span = top - base || 1;
  function pct(v: number) {
    return Math.max(2, Math.round(((v - base) / span) * 100));
  }
  const bars = values.map(function (v) {
    return { v, mine: false };
  });
  if (ownNum !== null) bars.push({ v: ownNum, mine: true });
  bars.sort(function (a, b) {
    return a.v - b.v;
  });
  return (
    <div>
      <div className="h-24 flex items-end gap-1 border-b border-gray-200">
        {bars.map(function (b, i) {
          return (
            <div
              key={i}
              className={
                'flex-1 rounded-t ' +
                (b.mine ? 'bg-indigo-600' : 'bg-slate-300')
              }
              style={{ height: pct(b.v) + '%' }}
              title={
                fmtValue(measure, b.v) +
                (b.mine ? ' — this opportunity' : ' — an anonymous peer')
              }
            ></div>
          );
        })}
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2 text-[11px] text-gray-500">
        <span className="min-w-0">
          {values.length} anonymous peer{values.length === 1 ? '' : 's'}
          {ownNum === null ? '' : ' + this opportunity'}, low to high
        </span>
        {ownNum === null ? (
          <span className="shrink-0 whitespace-nowrap text-gray-400">
            this opportunity: no value
          </span>
        ) : (
          <span className="shrink-0 whitespace-nowrap text-indigo-700 font-semibold">
            this opportunity: {fmtValue(measure, ownNum)}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * `W7` -> 7. `R` (a report index) and `M` (a cohort month) are older axes,
 * still parsed so an old publication keeps charting.
 */
export function periodNumber(p: unknown): number {
  const m = /^[WRM](\d+)$/.exec(String(p || ''));
  return m ? Number(m[1]) : -1;
}

/** "W7" -> "week 8": read by humans, and W7 invites an off-by-one. */
export function periodLabel(p: unknown): string {
  const n = periodNumber(p);
  if (n < 0) return String(p || '');
  return /^W/.test(String(p)) ? 'week ' + (n + 1) : String(p);
}

/**
 * One line per anonymous peer over TENURE -- each opportunity's own weeks of
 * delivering, so week 1 is week 1 for everybody -- with this opportunity's own
 * line on top.
 *
 * Points are placed by WEEK NUMBER from week 0. Spaced by position, W3, W8 and
 * W52 sat at equal intervals: gaps vanished and a line looked to start wherever
 * its first published week fell in the sorted list. A line ends where that
 * opportunity's figures settled (benchmarks/publish.py::opportunity_ends).
 */
export function PeerTrend(props: {
  series: Record<string, PeerPoint[]>;
  ownSeries?: Record<string, number | string | null>;
  measure: Measure;
}) {
  const measure = props.measure;
  const byPeriod = props.series || {};
  const ownByPeriod = props.ownSeries || {};
  const periods = Object.keys(byPeriod).sort(function (a, b) {
    return periodNumber(a) - periodNumber(b);
  });
  if (periods.length < 2) return null;

  // A peer_index denotes the SAME peer in every period of one indicator's
  // series -- that is what makes the points joinable at all.
  const lines: Record<string, (number | undefined)[]> = {};
  periods.forEach(function (p, i) {
    (byPeriod[p] || []).forEach(function (pt) {
      const k = String(pt.peer_index);
      (lines[k] = lines[k] || [])[i] = Number(pt.value);
    });
  });
  const ownPts = periods.map(function (p) {
    const v = ownByPeriod[p];
    return v === null || v === undefined ? null : Number(v);
  });

  const all: number[] = [];
  Object.keys(lines).forEach(function (k) {
    lines[k].forEach(function (v) {
      if (v !== undefined && v === v) all.push(v);
    });
  });
  ownPts.forEach(function (v) {
    if (v !== null) all.push(v);
  });
  if (!all.length) return null;
  const lo = Math.min.apply(null, all);
  let hi = Math.max.apply(null, all);
  if (hi === lo) hi = lo + 1;
  const W = 260,
    H = 120,
    L = 34,
    R = 8,
    T = 10,
    B = 20;
  const iw = W - L - R,
    ih = H - T - B;
  const nums = periods.map(periodNumber);
  const xLo = Math.min(0, nums[0]);
  let xHi = nums[nums.length - 1];
  if (xHi === xLo) xHi = xLo + 1;
  function xAt(n: number) {
    return L + ((n - xLo) * iw) / (xHi - xLo);
  }
  function x(i: number) {
    return xAt(nums[i]);
  }
  function y(v: number) {
    return T + ih - ((v - lo) / (hi - lo)) * ih;
  }
  function path(vals: (number | null | undefined)[]) {
    let d = '';
    let pen = false;
    vals.forEach(function (v, i) {
      if (v === null || v === undefined || v !== v) {
        pen = false;
        return;
      }
      d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      pen = true;
    });
    return d;
  }
  const hasOwn = ownPts.some(function (v) {
    return v !== null;
  });
  const peerCount = Object.keys(lines).length;
  const letter = (/^([WRM])/.exec(String(periods[0])) || [])[1] || 'W';
  return (
    <div>
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        className="w-full h-auto block"
        role="img"
        aria-label={(measure.title || measure.indicator) + ' over tenure'}
      >
        {[lo, (lo + hi) / 2, hi].map(function (t, i) {
          return (
            <g key={i}>
              <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#eeeef4" />
              <text
                x={L - 4}
                y={y(t) + 3}
                textAnchor="end"
                fontSize="7"
                fill="#9ca3af"
              >
                {fmtValue(measure, t)}
              </text>
            </g>
          );
        })}
        {[xLo, Math.round((xLo + xHi) / 2), xHi].map(function (n, i) {
          // End ticks anchored INWARD, or the card clips half a label.
          return (
            <text
              key={i}
              x={xAt(n)}
              y={H - 6}
              textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
              fontSize="7"
              fill="#9ca3af"
            >
              {periodLabel(letter + n)}
            </text>
          );
        })}
        {Object.keys(lines).map(function (k) {
          return (
            <path
              key={k}
              d={path(lines[k])}
              fill="none"
              stroke="#cbd5e1"
              strokeWidth="1.5"
            />
          );
        })}
        {hasOwn ? (
          <path d={path(ownPts)} fill="none" stroke="#4f46e5" strokeWidth="2" />
        ) : null}
        {hasOwn
          ? ownPts.map(function (v, i) {
              if (v === null) return null;
              return (
                <circle
                  key={i}
                  cx={x(i)}
                  cy={y(v)}
                  r="2.5"
                  fill="#4f46e5"
                  stroke="#fff"
                  strokeWidth="1"
                >
                  <title>
                    {periodLabel(periods[i]) + ': ' + fmtValue(measure, v)}
                  </title>
                </circle>
              );
            })
          : null}
      </svg>
      <div className="mt-1 text-[11px] text-gray-500 flex items-baseline justify-between gap-2">
        <span className="min-w-0">
          {peerCount} anonymous peer{peerCount === 1 ? '' : 's'} · each one's
          own weeks of delivering
        </span>
        {hasOwn ? (
          <span className="shrink-0 whitespace-nowrap text-indigo-700 font-semibold">
            this opportunity
          </span>
        ) : (
          <span className="shrink-0 whitespace-nowrap text-gray-400">
            this opportunity: not in the window
          </span>
        )}
      </div>
    </div>
  );
}

/** Both readings for one indicator, side by side, in one card. */
export function PeerCard(props: {
  measure: Measure;
  entry: {
    peers?: PeerPoint[];
    series?: Record<string, PeerPoint[]>;
    ownSeries?: Record<string, number | string | null>;
  };
  own?: number | string | null;
}) {
  const entry = props.entry || {};
  const m = props.measure;
  const hasTrend = Object.keys(entry.series || {}).length > 1;
  return (
    <div className="bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-3">
      <div className="text-sm font-semibold text-gray-900 mb-2">
        {m.title || m.indicator}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">
            Where it sits
          </div>
          {(entry.peers || []).length ? (
            <PeerBars peers={entry.peers || []} measure={m} own={props.own} />
          ) : (
            <div className="text-[11px] text-gray-400">
              No point value cleared the disclosure floors.
            </div>
          )}
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">
            Over time
          </div>
          {hasTrend ? (
            <PeerTrend
              series={entry.series || {}}
              ownSeries={entry.ownSeries}
              measure={m}
            />
          ) : (
            <div className="text-[11px] text-gray-400">
              Too few peers span enough reports to publish a trend for this
              indicator.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
