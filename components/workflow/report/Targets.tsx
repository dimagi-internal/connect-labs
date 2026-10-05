/**
 * Enrolment against target (VERSION 5): what a programme has enrolled, month by
 * month, beside the targets it committed to -- and the distance still to go.
 *
 * Targets are CONFIG, never code: a report reads them from its workflow's
 * `config.enrollment_targets` and hands them here with the snapshot's
 * `monthlyByScope`. Nothing programme-specific lives in this file.
 *
 *   {
 *     source: "KMC Goals | Case & Spend", as_of: "2026-10-05",
 *     unit: "registered babies", note: "...",
 *     indicator: "registered_cases",          // optional; the count summed
 *     llos: {
 *       NAMA: { before_window: 165, monthly: { "2026-07": 228, ... } },
 *       ...
 *     }
 *   }
 *
 * The actual for a month is the snapshot's raw count (`point.counts[ind]`,
 * which is never suppressed under a min-denominator floor), else the graded
 * cell's value for runs saved before counts were carried. `before_window` is
 * the goal sheet's carry-in: enrolments made before the window opened that the
 * sheet counts toward the total. It is credited to BOTH sides -- it is in the
 * goal and it has already happened -- so it never moves the gap.
 */
import React from 'react';
import { nCount } from './format';

export interface LloTarget {
  before_window?: number | null;
  /** 'YYYY-MM' -> target enrolments that month. */
  monthly?: Record<string, number>;
  /** First month this LLO's actuals count from; default its first target month. */
  start?: string;
}

export interface EnrolmentTargets {
  source?: string;
  as_of?: string;
  unit?: string;
  note?: string;
  indicator?: string;
  goal?: { programme?: number | null };
  llos?: Record<string, LloTarget>;
}

export interface MonthProgress {
  month: string;
  target: number;
  /** Null when the month is in the future, before this scope's start, or unknown. */
  actual: number | null;
  future: boolean;
  cumTarget: number;
  cumActual: number | null;
}

export interface EnrolmentProgress {
  scope: string;
  llos: string[];
  months: MonthProgress[];
  carryIn: number;
  goal: number;
  asOfMonth: string;
  cumActual: number;
  cumTargetToDate: number;
  gap: number;
  gapPct: number | null;
  /** Whole months after the as-of month. */
  remainingMonths: number;
  /** How much of the as-of month had elapsed (1 when it is complete). */
  asOfFraction: number;
  runRateNeeded: number | null;
  /** Past months whose actual could not be read (an old run's suppressed cell). */
  unknownMonths: string[];
}

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/** "2026-07" -> "Jul 2026" (or "Jul" when short). */
export function monthLbl(ym: string, short?: boolean): string {
  const p = String(ym || '').split('-');
  const m = MONTHS[Number(p[1]) - 1] || p[1] || '';
  return short ? m : m + ' ' + p[0];
}

function nextMonth(ym: string): string {
  const y = Number(ym.slice(0, 4));
  const m = Number(ym.slice(5, 7));
  return m === 12 ? y + 1 + '-01' : y + '-' + (m + 1 < 10 ? '0' : '') + (m + 1);
}

function monthsBetween(a: string, b: string): string[] {
  const out: string[] = [];
  let cur = a;
  while (cur <= b && out.length < 240) {
    out.push(cur);
    cur = nextMonth(cur);
  }
  return out;
}

/** Share of its month a date has reached: 2026-10-04 -> 4/31. 1 if unparseable. */
export function elapsedFraction(date: string): number {
  const y = Number(date.slice(0, 4)),
    m = Number(date.slice(5, 7)),
    d = Number(date.slice(8, 10));
  if (!y || !m || !d) return 1;
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return Math.min(1, d / days);
}

/** The LLOs that have at least one target, in config order. */
export function targetedLlos(targets: EnrolmentTargets | null | undefined) {
  const llos = (targets && targets.llos) || {};
  return Object.keys(llos).filter(function (k) {
    const t = llos[k] || {};
    return (
      Object.keys(t.monthly || {}).length > 0 || Number(t.before_window) > 0
    );
  });
}

/** The window every chart shares: first to last target month of any LLO. */
export function targetWindow(
  targets: EnrolmentTargets | null | undefined,
): string[] {
  let lo = '',
    hi = '';
  targetedLlos(targets).forEach(function (k) {
    Object.keys((targets!.llos![k] || {}).monthly || {}).forEach(function (m) {
      if (!lo || m < lo) lo = m;
      if (!hi || m > hi) hi = m;
    });
  });
  return lo ? monthsBetween(lo, hi) : [];
}

function actualOf(point: any, indicator: string): number | null {
  if (!point) return 0;
  const c = point.counts && point.counts[indicator];
  if (typeof c === 'number') return c;
  const cell = point.ind && point.ind[indicator];
  if (cell && cell.value !== null && cell.value !== undefined)
    return Number(cell.value);
  // A point with no registrations at all has no cell value to suppress.
  if (point.n === 0) return 0;
  return null;
}

/**
 * Progress for one scope: 'all' (every LLO with a target, summed) or one LLO
 * by name. `asOf` is the run's as-of date; months after its month are future
 * and carry the target only. The as-of month itself counts as to-date.
 */
export function enrolmentProgress(opts: {
  targets: EnrolmentTargets;
  monthlyByScope: Record<string, any[]> | null | undefined;
  asOf: string;
  scope: string;
}): EnrolmentProgress | null {
  const targets = opts.targets;
  const all = targetedLlos(targets);
  const llos =
    opts.scope === 'all'
      ? all
      : all.indexOf(opts.scope) >= 0
        ? [opts.scope]
        : [];
  if (!llos.length) return null;
  const window = targetWindow(targets);
  const indicator = targets.indicator || 'registered_cases';
  const asOfMonth = String(opts.asOf || '').slice(0, 7);
  const byScope = opts.monthlyByScope || {};

  let carryIn = 0;
  const unknown: Record<string, boolean> = {};
  const perMonth = window.map(function (month) {
    let target = 0;
    let actual: number | null = 0;
    let counted = false;
    llos.forEach(function (llo) {
      const t = targets.llos![llo] || {};
      target += Number((t.monthly || {})[month]) || 0;
      const start =
        t.start ||
        Object.keys(t.monthly || {})
          .sort()
          .shift() ||
        window[0];
      if (month < start || month > asOfMonth) return;
      counted = true;
      const points = byScope['llo:' + llo] || [];
      const pt = points.filter(function (p) {
        return p && p.month === month;
      })[0];
      const a = actualOf(pt, indicator);
      if (a === null) {
        unknown[month] = true;
        return;
      }
      actual = (actual as number) + a;
    });
    return {
      month: month,
      target: target,
      actual: counted ? actual : null,
      future: month > asOfMonth,
    };
  });
  llos.forEach(function (llo) {
    carryIn += Number(targets.llos![llo].before_window) || 0;
  });

  // The as-of month is part-way through: a report as of 4 October has had four
  // days of October's target, not all of it. Holding it to the whole month read
  // PIPN as 1,000 behind on the first Sunday of a 1,200 month. So the month's
  // target is PRO-RATED by the days elapsed, in "target to date" and in the
  // months still to cover; the bars and the cumulative target line keep whole
  // months, which is what the goals sheet states.
  const fraction = elapsedFraction(String(opts.asOf || ''));
  let cumT = carryIn,
    cumA = carryIn,
    cumTargetToDate = carryIn;
  const months: MonthProgress[] = perMonth.map(function (m) {
    cumT += m.target;
    if (!m.future) {
      cumTargetToDate =
        m.month === asOfMonth ? cumT - m.target * (1 - fraction) : cumT;
      cumA += m.actual || 0;
    }
    return Object.assign({}, m, {
      cumTarget: cumT,
      // No line before this scope's actuals start counting (an LLO whose
      // targets begin later than the window), and none into the future.
      cumActual: m.future || m.actual === null ? null : cumA,
    });
  });
  const goal = cumT;
  const futureMonths = months.filter(function (m) {
    return m.future;
  }).length;
  const inWindow = months.some(function (m) {
    return m.month === asOfMonth;
  });
  // What is left of the as-of month counts as time still to enrol in.
  const remaining = futureMonths + (inWindow ? 1 - fraction : 0);
  const gap = cumA - cumTargetToDate;
  return {
    scope: opts.scope,
    llos: llos,
    months: months,
    carryIn: carryIn,
    goal: goal,
    asOfMonth: asOfMonth,
    cumActual: cumA,
    cumTargetToDate: cumTargetToDate,
    gap: gap,
    gapPct: cumTargetToDate ? gap / cumTargetToDate : null,
    remainingMonths: futureMonths,
    asOfFraction: inWindow ? fraction : 1,
    runRateNeeded: remaining > 0 ? Math.max(0, goal - cumA) / remaining : null,
    unknownMonths: Object.keys(unknown).sort(),
  };
}

const C_TARGET = '#d1d5db';
const C_TARGET_FUTURE = '#e5e7eb';
const C_ACTUAL = '#6366f1';
const C_CUM_ACTUAL = '#312e81';
const C_CUM_TARGET = '#9ca3af';

function niceTop(max: number): number {
  if (max <= 0) return 1;
  // The smallest "round" ceiling at or above max -- 1, 1.5, 2, 2.5, 3, 4, 5, 6,
  // 8 or 10 times a power of ten -- so a 12,771 goal sits under 15,000 rather
  // than leaving the top third of the chart empty under 20,000.
  const step = Math.pow(10, Math.floor(Math.log(max) / Math.LN10));
  const steps = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  for (let i = 0; i < steps.length; i++) {
    if (steps[i] * step >= max) return steps[i] * step;
  }
  return 10 * step;
}

/**
 * Paired monthly bars (target, actual) on the left axis and the two cumulative
 * lines on the right axis. Future months draw the target bar only, paler.
 */
export function EnrolmentTargetChart(props: { progress: EnrolmentProgress }) {
  const p = props.progress;
  const months = p.months;
  const W = 760,
    H = 252,
    L = 44,
    R = 52,
    T = 26,
    B = 30;
  if (!months.length) return null;
  let maxM = 1,
    maxC = 1;
  months.forEach(function (m) {
    maxM = Math.max(maxM, m.target, m.actual || 0);
    maxC = Math.max(maxC, m.cumTarget, m.cumActual || 0);
  });
  const topM = niceTop(maxM),
    topC = niceTop(maxC);
  const iw = W - L - R,
    ih = H - T - B;
  const bw = iw / months.length;
  const yM = function (v: number) {
    return T + ih - (v / topM) * ih;
  };
  const yC = function (v: number) {
    return T + ih - (v / topC) * ih;
  };
  const cx = function (i: number) {
    return L + i * bw + bw / 2;
  };
  const line = function (vals: (number | null)[]) {
    let d = '';
    let pen = false;
    vals.forEach(function (v, i) {
      if (v === null) {
        pen = false;
        return;
      }
      d += (pen ? 'L' : 'M') + cx(i).toFixed(1) + ' ' + yC(v).toFixed(1) + ' ';
      pen = true;
    });
    return d;
  };
  const asOfIdx = months.filter(function (m) {
    return !m.future;
  }).length;
  const ticks = [0, 0.5, 1];
  return (
    <svg
      viewBox={'0 0 ' + W + ' ' + H}
      className="w-full h-auto block"
      role="img"
      aria-label="Enrolment against target by month"
    >
      {ticks.map(function (f) {
        return (
          <g key={f}>
            <line
              x1={L}
              x2={W - R}
              y1={yM(topM * f)}
              y2={yM(topM * f)}
              stroke="#eeeef4"
            />
            <text
              x={L - 6}
              y={yM(topM * f) + 4}
              fontSize="10"
              fill="#9ca3af"
              textAnchor="end"
            >
              {nCount(topM * f)}
            </text>
            <text
              x={W - R + 6}
              y={yC(topC * f) + 4}
              fontSize="10"
              fill={C_CUM_ACTUAL}
              textAnchor="start"
            >
              {nCount(topC * f)}
            </text>
          </g>
        );
      })}
      {asOfIdx > 0 && asOfIdx < months.length ? (
        <g>
          <line
            x1={L + asOfIdx * bw}
            x2={L + asOfIdx * bw}
            y1={T - 22}
            y2={T + ih}
            stroke="#a5b4fc"
            strokeDasharray="4 3"
          />
          <text
            x={L + asOfIdx * bw + 4}
            y={T - 10}
            fontSize="10"
            fill="#6366f1"
          >
            future: target only
          </text>
        </g>
      ) : null}
      {months.map(function (m, i) {
        const x = L + i * bw;
        const w = bw * (m.future ? 0.5 : 0.36);
        const tx = m.future ? x + bw * 0.25 : x + bw * 0.12;
        const tip =
          monthLbl(m.month) +
          ': target ' +
          nCount(m.target) +
          (m.future
            ? ' (future)'
            : m.actual === null
              ? ''
              : ', enrolled ' + nCount(m.actual)) +
          ' · cumulative target ' +
          nCount(m.cumTarget) +
          (m.cumActual === null ? '' : ', enrolled ' + nCount(m.cumActual));
        return (
          <g key={m.month}>
            <title>{tip}</title>
            {m.target > 0 ? (
              <rect
                x={tx.toFixed(1)}
                y={yM(m.target).toFixed(1)}
                width={w.toFixed(1)}
                height={(T + ih - yM(m.target)).toFixed(1)}
                rx="2"
                fill={m.future ? C_TARGET_FUTURE : C_TARGET}
                stroke={m.future ? '#d1d5db' : 'none'}
                strokeDasharray={m.future ? '3 2' : undefined}
              />
            ) : null}
            {!m.future && m.actual !== null && m.actual > 0 ? (
              <rect
                x={(x + bw * 0.52).toFixed(1)}
                y={yM(m.actual).toFixed(1)}
                width={(bw * 0.36).toFixed(1)}
                height={(T + ih - yM(m.actual)).toFixed(1)}
                rx="2"
                fill={C_ACTUAL}
              />
            ) : null}
            <text
              x={cx(i)}
              y={H - 10}
              fontSize="10"
              fill="#6b7280"
              textAnchor="middle"
            >
              {monthLbl(m.month, true) +
                (i === 0 || m.month.slice(5) === '01'
                  ? ' ' + m.month.slice(2, 4)
                  : '')}
            </text>
          </g>
        );
      })}
      <path
        d={line(
          months.map(function (m) {
            return m.cumTarget;
          }),
        )}
        fill="none"
        stroke={C_CUM_TARGET}
        strokeWidth="2"
        strokeDasharray="5 4"
      />
      <path
        d={line(
          months.map(function (m) {
            return m.cumActual;
          }),
        )}
        fill="none"
        stroke={C_CUM_ACTUAL}
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
      {months.map(function (m, i) {
        return m.cumActual === null ? null : (
          <circle
            key={'c' + m.month}
            cx={cx(i)}
            cy={yC(m.cumActual)}
            r="3"
            fill={C_CUM_ACTUAL}
            stroke="#fff"
            strokeWidth="1.5"
          />
        );
      })}
    </svg>
  );
}

function pctLbl(v: number | null): string {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v > 0 ? '+' : '') + Math.round(v * 100) + '%';
}

/**
 * The distance-to-target readout: cumulative actual, cumulative target to date,
 * the gap (number and %), the full goal, and the monthly run-rate the remaining
 * months need.
 */
export function EnrolmentTargetSummary(props: {
  progress: EnrolmentProgress;
  unit?: string;
}) {
  const p = props.progress;
  const last = p.months.length ? p.months[p.months.length - 1].month : '';
  const behind = p.gap < 0;
  const items: { label: string; value: string; sub?: string; tone?: string }[] =
    [
      {
        label: 'Enrolled to date',
        value: nCount(p.cumActual),
        sub: p.carryIn
          ? 'incl. ' + nCount(p.carryIn) + ' carried in from before the window'
          : 'through ' + monthLbl(p.asOfMonth),
      },
      {
        label: 'Target to date',
        value: nCount(p.cumTargetToDate),
        sub:
          p.asOfFraction < 1
            ? 'through ' +
              monthLbl(p.asOfMonth) +
              ', its target pro-rated to the ' +
              Math.round(p.asOfFraction * 100) +
              '% of the month elapsed'
            : 'cumulative through ' + monthLbl(p.asOfMonth),
      },
      {
        label: behind ? 'Behind target' : 'Ahead of target',
        value:
          (p.gap > 0 ? '+' : p.gap < 0 ? '−' : '') + nCount(Math.abs(p.gap)),
        sub: pctLbl(p.gapPct) + ' against target to date',
        tone: behind ? 'bad' : 'good',
      },
      {
        label: 'Goal by ' + monthLbl(last),
        value: nCount(p.goal),
        sub:
          nCount(Math.max(0, p.goal - p.cumActual)) +
          ' still to enrol (' +
          (p.goal ? Math.round((p.cumActual / p.goal) * 100) : 0) +
          '% reached)',
      },
      {
        label: 'Needed per month',
        value: p.runRateNeeded === null ? '—' : nCount(p.runRateNeeded),
        sub:
          p.runRateNeeded === null
            ? 'the window has closed'
            : 'over the ' +
              (p.asOfFraction < 1
                ? 'rest of ' + monthLbl(p.asOfMonth, true) + ' and the '
                : '') +
              p.remainingMonths +
              ' remaining month' +
              (p.remainingMonths === 1 ? '' : 's') +
              ' to reach the goal',
      },
    ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
      {items.map(function (it) {
        return (
          <div
            key={it.label}
            className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
          >
            <div className="text-[11px] uppercase tracking-wide text-gray-500">
              {it.label}
            </div>
            <div
              className={
                'text-lg font-semibold ' +
                (it.tone === 'bad'
                  ? 'text-red-700'
                  : it.tone === 'good'
                    ? 'text-green-700'
                    : 'text-gray-900')
              }
            >
              {it.value}
            </div>
            {it.sub ? (
              <div className="text-[11px] text-gray-500 leading-snug">
                {it.sub}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function swatch(bg: string, label: string, line?: boolean, dashed?: boolean) {
  return (
    <span key={label} className="inline-flex items-center mr-3">
      <span
        className={
          'inline-block mr-1 align-middle ' +
          (line ? 'w-4 h-0 border-t-2' : 'w-2.5 h-2.5 rounded-sm')
        }
        style={
          line
            ? { borderColor: bg, borderTopStyle: dashed ? 'dashed' : 'solid' }
            : { background: bg }
        }
      />
      {label}
    </span>
  );
}

/**
 * Legend for the (VERSION 5) combined chart. Kept for renders written against
 * it; the KMC report now draws the two single-axis charts below instead.
 */
export function EnrolmentTargetLegend() {
  function sw(bg: string, label: string, line?: boolean, dashed?: boolean) {
    return (
      <span key={label} className="inline-flex items-center mr-3">
        <span
          className={
            'inline-block mr-1 align-middle ' +
            (line ? 'w-4 h-0 border-t-2' : 'w-2.5 h-2.5 rounded-sm')
          }
          style={
            line
              ? {
                  borderColor: bg,
                  borderTopStyle: dashed ? 'dashed' : 'solid',
                }
              : { background: bg }
          }
        />
        {label}
      </span>
    );
  }
  return (
    <div className="text-xs text-gray-500 flex flex-wrap items-center">
      {sw(C_TARGET, 'Monthly target')}
      {sw(C_ACTUAL, 'Enrolled that month')}
      {sw(C_CUM_TARGET, 'Cumulative target', true, true)}
      {sw(C_CUM_ACTUAL, 'Cumulative enrolled', true)}
    </div>
  );
}

// ── VERSION 6: two charts, one axis each ─────────────────────────────────────
// The combined chart drew a running total over monthly bars on a second axis,
// and a reader asked what the blue line was conveying. Each question now gets
// its own chart: how did each month go, and where does the running total stand
// against the goal.

const C_AHEAD = '#16a34a';
const C_BEHIND = '#dc2626';

function ChartHead(props: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 mb-1">
      <div className="text-sm font-semibold text-gray-900">{props.title}</div>
      <div className="text-xs text-gray-500 flex flex-wrap items-center">
        {props.children}
      </div>
    </div>
  );
}

function monthTick(m: string, i: number): string {
  return (
    monthLbl(m, true) +
    (i === 0 || m.slice(5) === '01' ? ' ' + m.slice(2, 4) : '')
  );
}

/** Monthly target vs enrolled, bars only, one axis. Future months: target only. */
export function EnrolmentMonthlyChart(props: { progress: EnrolmentProgress }) {
  const p = props.progress;
  const months = p.months;
  if (!months.length) return null;
  const W = 760,
    H = 230,
    L = 44,
    R = 16,
    T = 26,
    B = 30;
  let max = 1;
  months.forEach(function (m) {
    max = Math.max(max, m.target, m.actual || 0);
  });
  const top = niceTop(max);
  const iw = W - L - R,
    ih = H - T - B;
  const bw = iw / months.length;
  const y = function (v: number) {
    return T + ih - (v / top) * ih;
  };
  const asOfIdx = months.filter(function (m) {
    return !m.future;
  }).length;
  return (
    <div>
      <ChartHead title="Monthly enrolment">
        {swatch(C_TARGET, 'Target')}
        {swatch(C_ACTUAL, 'Enrolled')}
      </ChartHead>
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        className="w-full h-auto block"
        role="img"
        aria-label="Monthly enrolment against target"
      >
        {[0, 0.5, 1].map(function (f) {
          return (
            <g key={f}>
              <line
                x1={L}
                x2={W - R}
                y1={y(top * f)}
                y2={y(top * f)}
                stroke="#eeeef4"
              />
              <text
                x={L - 6}
                y={y(top * f) + 4}
                fontSize="10"
                fill="#9ca3af"
                textAnchor="end"
              >
                {nCount(top * f)}
              </text>
            </g>
          );
        })}
        {asOfIdx > 0 && asOfIdx < months.length ? (
          <g>
            <line
              x1={L + asOfIdx * bw}
              x2={L + asOfIdx * bw}
              y1={T - 22}
              y2={T + ih}
              stroke="#a5b4fc"
              strokeDasharray="4 3"
            />
            <text
              x={L + asOfIdx * bw + 4}
              y={T - 10}
              fontSize="10"
              fill="#6366f1"
            >
              future: target only
            </text>
          </g>
        ) : null}
        {months.map(function (m, i) {
          const x = L + i * bw;
          const tip =
            monthLbl(m.month) +
            ': target ' +
            nCount(m.target) +
            (m.future
              ? ' (future)'
              : m.actual === null
                ? ''
                : ', enrolled ' + nCount(m.actual));
          return (
            <g key={m.month}>
              <title>{tip}</title>
              {m.target > 0 ? (
                <rect
                  x={(m.future ? x + bw * 0.25 : x + bw * 0.12).toFixed(1)}
                  y={y(m.target).toFixed(1)}
                  width={(bw * (m.future ? 0.5 : 0.36)).toFixed(1)}
                  height={(T + ih - y(m.target)).toFixed(1)}
                  rx="2"
                  fill={m.future ? C_TARGET_FUTURE : C_TARGET}
                  stroke={m.future ? '#d1d5db' : 'none'}
                  strokeDasharray={m.future ? '3 2' : undefined}
                />
              ) : null}
              {!m.future && m.actual !== null && m.actual > 0 ? (
                <rect
                  x={(x + bw * 0.52).toFixed(1)}
                  y={y(m.actual).toFixed(1)}
                  width={(bw * 0.36).toFixed(1)}
                  height={(T + ih - y(m.actual)).toFixed(1)}
                  rx="2"
                  fill={C_ACTUAL}
                />
              ) : null}
              <text
                x={x + bw / 2}
                y={H - 10}
                fontSize="10"
                fill="#6b7280"
                textAnchor="middle"
              >
                {monthTick(m.month, i)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export interface CumulativeKnot {
  /** Position in months from the window's start (0 = start of the first month). */
  at: number;
  target: number;
  /** Null before this scope's actuals start, and after the as-of date. */
  enrolled: number | null;
}

/**
 * The running totals as knots on a continuous month axis: the window's start,
 * every month END, and the as-of date part-way through its month. Pro-rating the
 * as-of month's target is linear within the month, so the target line passes
 * through exactly the "target to date" figure at the as-of knot.
 */
export function cumulativeKnots(p: EnrolmentProgress): CumulativeKnot[] {
  const knots: CumulativeKnot[] = [];
  let started = false;
  let prevT = p.carryIn;
  let prevA = p.carryIn;
  p.months.forEach(function (m, i) {
    if (i === 0)
      knots.push({
        at: 0,
        target: p.carryIn,
        enrolled: m.actual === null ? null : p.carryIn,
      });
    if (m.actual !== null && !started) {
      started = true;
      // The running total starts where this scope's actuals start.
      if (i > 0) knots.push({ at: i, target: prevT, enrolled: prevA });
    }
    if (m.month === p.asOfMonth && p.asOfFraction < 1) {
      knots.push({
        at: i + p.asOfFraction,
        target: p.cumTargetToDate,
        enrolled: m.cumActual,
      });
      knots.push({ at: i + 1, target: m.cumTarget, enrolled: null });
    } else {
      knots.push({
        at: i + 1,
        target: m.cumTarget,
        enrolled: m.future ? null : m.cumActual,
      });
    }
    prevT = m.cumTarget;
    if (m.cumActual !== null) prevA = m.cumActual;
  });
  return knots;
}

/**
 * Running total since the window opened against the cumulative target, on ONE
 * axis: enrolled solid to the as-of date, target dashed to the goal, the gap
 * shaded (green ahead, red behind), each line labelled at its end.
 */
export function EnrolmentCumulativeChart(props: {
  progress: EnrolmentProgress;
}) {
  const p = props.progress;
  const months = p.months;
  if (!months.length) return null;
  const knots = cumulativeKnots(p);
  const W = 760,
    H = 250,
    L = 52,
    R = 96,
    T = 18,
    B = 30;
  let max = 1;
  knots.forEach(function (k) {
    max = Math.max(max, k.target, k.enrolled || 0);
  });
  const top = niceTop(max);
  const iw = W - L - R,
    ih = H - T - B;
  const bw = iw / months.length;
  const x = function (at: number) {
    return L + at * bw;
  };
  const y = function (v: number) {
    return T + ih - (v / top) * ih;
  };
  const xy = function (at: number, v: number) {
    return x(at).toFixed(1) + ' ' + y(v).toFixed(1);
  };
  const tLine = knots
    .map(function (k, i) {
      return (i ? 'L' : 'M') + xy(k.at, k.target);
    })
    .join(' ');
  const enrolled = knots.filter(function (k) {
    return k.enrolled !== null;
  });
  const aLine = enrolled
    .map(function (k, i) {
      return (i ? 'L' : 'M') + xy(k.at, k.enrolled as number);
    })
    .join(' ');
  const ahead = p.gap >= 0;
  const gapFill = enrolled.length
    ? 'M' +
      enrolled
        .map(function (k) {
          return xy(k.at, k.enrolled as number);
        })
        .join(' L') +
      ' L' +
      enrolled
        .slice()
        .reverse()
        .map(function (k) {
          return xy(k.at, k.target);
        })
        .join(' L') +
      ' Z'
    : '';
  const lastA = enrolled.length ? enrolled[enrolled.length - 1] : null;
  const last = knots[knots.length - 1];
  // Two labels share the as-of x: the higher figure sits above its point, the
  // lower one below, so they never collide.
  const aboveA = lastA ? (lastA.enrolled as number) >= lastA.target : true;
  const goalLbl = 'Goal ' + nCount(p.goal);
  return (
    <div>
      <ChartHead title="Cumulative enrolment vs goal">
        {swatch(C_CUM_ACTUAL, 'Enrolled', true)}
        {swatch(C_CUM_TARGET, 'Target', true, true)}
      </ChartHead>
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        className="w-full h-auto block"
        role="img"
        aria-label="Cumulative enrolment against the goal"
      >
        {[0, 0.5, 1].map(function (f) {
          return (
            <g key={f}>
              <line
                x1={L}
                x2={W - R}
                y1={y(top * f)}
                y2={y(top * f)}
                stroke="#eeeef4"
              />
              <text
                x={L - 6}
                y={y(top * f) + 4}
                fontSize="10"
                fill="#9ca3af"
                textAnchor="end"
              >
                {nCount(top * f)}
              </text>
            </g>
          );
        })}
        {months.map(function (m, i) {
          return (
            <text
              key={m.month}
              x={x(i + 0.5)}
              y={H - 10}
              fontSize="10"
              fill="#6b7280"
              textAnchor="middle"
            >
              {monthTick(m.month, i)}
            </text>
          );
        })}
        {gapFill ? (
          <path
            d={gapFill}
            fill={ahead ? C_AHEAD : C_BEHIND}
            fillOpacity="0.12"
            stroke="none"
          />
        ) : null}
        <path
          d={tLine}
          fill="none"
          stroke={C_CUM_TARGET}
          strokeWidth="2"
          strokeDasharray="5 4"
        />
        {aLine ? (
          <path
            d={aLine}
            fill="none"
            stroke={C_CUM_ACTUAL}
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
        ) : null}
        {lastA ? (
          <g>
            <circle
              cx={x(lastA.at)}
              cy={y(lastA.enrolled as number)}
              r="3.5"
              fill={C_CUM_ACTUAL}
              stroke="#fff"
              strokeWidth="1.5"
            />
            <circle
              cx={x(lastA.at)}
              cy={y(lastA.target)}
              r="3"
              fill="#fff"
              stroke={C_CUM_TARGET}
              strokeWidth="1.5"
            />
            <text
              x={x(lastA.at) + 6}
              y={y(lastA.enrolled as number) + (aboveA ? -8 : 14)}
              fontSize="11"
              fontWeight="600"
              fill={C_CUM_ACTUAL}
              stroke="#fff"
              strokeWidth="3"
              paintOrder="stroke"
            >
              {'Enrolled ' + nCount(lastA.enrolled)}
            </text>
            <text
              x={x(lastA.at) + 6}
              y={y(lastA.target) + (aboveA ? 14 : -8)}
              fontSize="11"
              fill="#6b7280"
              stroke="#fff"
              strokeWidth="3"
              paintOrder="stroke"
            >
              {'Target ' + nCount(lastA.target)}
            </text>
          </g>
        ) : null}
        <circle
          cx={x(last.at)}
          cy={y(last.target)}
          r="3"
          fill="#fff"
          stroke={C_CUM_TARGET}
          strokeWidth="1.5"
        />
        <text
          x={x(last.at) + 6}
          y={y(last.target) + 4}
          fontSize="11"
          fontWeight="600"
          fill="#4b5563"
        >
          {goalLbl}
        </text>
      </svg>
      <p className="text-xs text-gray-500 mt-0.5">
        {'Running total since ' +
          monthLbl(months[0].month, true) +
          (p.carryIn
            ? ', including the ' +
              nCount(p.carryIn) +
              ' enrolled before ' +
              monthLbl(months[0].month, true)
            : '') +
          '. Above the dashed line = ahead of target.'}
      </p>
    </div>
  );
}
