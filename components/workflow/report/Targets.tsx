/**
 * Enrollment against target (VERSION 5): what a program has enrolled, month by
 * month, beside the targets it committed to -- and the distance still to go.
 *
 * Targets are CONFIG, never code: a report reads them from its workflow's
 * `config.enrollment_targets` and hands them here with the snapshot's
 * `monthlyByScope`. Nothing program-specific lives in this file.
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
 * the goal sheet's carry-in: enrollments made before the window opened that the
 * sheet counts toward the total. It is credited to BOTH sides -- it is in the
 * goal and it has already happened -- so it never moves the gap.
 */
import React from 'react';
import { nCount } from './format';

export interface LloTarget {
  before_window?: number | null;
  /** 'YYYY-MM' -> target enrollments that month. */
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
  // What is left of the as-of month counts as time still to enroll in.
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
  const step = Math.pow(10, Math.floor(Math.log(max) / Math.LN10));
  const top = Math.ceil(max / step) * step;
  return top < max ? top + step : top;
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
  // No as-of divider: the paler, dashed future bars already say which months
  // are still to come, and a labelled line through the chart read as noise.
  const ticks = [0, 0.5, 1];
  return (
    <svg
      viewBox={'0 0 ' + W + ' ' + H}
      className="w-full h-auto block"
      role="img"
      aria-label="Enrollment against target by month"
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
              fill="#6b7280"
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
          ' still to enroll (' +
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

/** Legend for the chart. */
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
      {sw(C_CUM_ACTUAL, 'Cumulative enrolled (right axis)', true)}
    </div>
  );
}

// ── VERSION 6: this month, day by day ────────────────────────────────────────

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

export interface DailySnapshot {
  months?: string[];
  as_of?: string;
  byScope?: Record<string, Record<string, number[]>>;
}

export interface DailyProgress {
  month: string;
  previousMonth: string;
  daysInMonth: number;
  day: number;
  /** Running total by day, day 1 .. the as-of day. */
  cumulative: number[];
  /** Last month's running total by day, its full length. */
  previous: number[];
  target: number;
  onPace: number;
  enrolled: number;
  gap: number;
  /** Last month's running total by the same day (null if it had no such day). */
  previousByDay: number | null;
}

function daysIn(ym: string): number {
  const y = Number(ym.slice(0, 4)),
    m = Number(ym.slice(5, 7));
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

function running(xs: number[]): number[] {
  let t = 0;
  return (xs || []).map(function (v) {
    t += Number(v) || 0;
    return t;
  });
}

function addInto(acc: number[], xs: number[] | undefined) {
  (xs || []).forEach(function (v, i) {
    acc[i] = (acc[i] || 0) + (Number(v) || 0);
  });
}

/**
 * The as-of month against its target, day by day, for 'all' (every LLO with a
 * target, summed -- the same population as the monthly chart) or one LLO. The
 * on-pace line is the month's target spread evenly over its days.
 */
export function dailyProgress(opts: {
  targets: EnrolmentTargets;
  daily: DailySnapshot | null | undefined;
  scope: string;
}): DailyProgress | null {
  const d = opts.daily;
  if (!d || !d.months || d.months.length < 2 || !d.byScope) return null;
  const all = targetedLlos(opts.targets);
  const llos =
    opts.scope === 'all'
      ? all
      : all.indexOf(opts.scope) >= 0
        ? [opts.scope]
        : [];
  if (!llos.length) return null;
  const prevM = d.months[0],
    curM = d.months[1];
  const cur: number[] = [],
    prev: number[] = [];
  let target = 0;
  llos.forEach(function (llo) {
    const s = d.byScope!['llo:' + llo] || {};
    addInto(cur, s[curM]);
    addInto(prev, s[prevM]);
    target +=
      Number(((opts.targets.llos![llo] || {}).monthly || {})[curM]) || 0;
  });
  const days = daysIn(curM);
  const day = Math.max(
    cur.length,
    Number(String(d.as_of || '').slice(8, 10)) || cur.length,
  );
  while (cur.length < day) cur.push(0);
  const cumulative = running(cur);
  const previous = running(prev);
  const enrolled = cumulative.length ? cumulative[cumulative.length - 1] : 0;
  const onPace = (target * day) / days;
  return {
    month: curM,
    previousMonth: prevM,
    daysInMonth: days,
    day: day,
    cumulative: cumulative,
    previous: previous,
    target: target,
    onPace: onPace,
    enrolled: enrolled,
    gap: enrolled - onPace,
    previousByDay: previous.length >= day ? previous[day - 1] : null,
  };
}

/** "Day 4 of 31: 247 enrolled vs 194 on pace (+53) · last month by day 4: 180". */
export function dailyReadout(p: DailyProgress): string {
  const g = Math.round(p.gap);
  return (
    'Day ' +
    p.day +
    ' of ' +
    p.daysInMonth +
    ': ' +
    nCount(p.enrolled) +
    ' enrolled vs ' +
    nCount(p.onPace) +
    ' on pace (' +
    (g > 0 ? '+' : g < 0 ? '−' : '±') +
    nCount(Math.abs(g)) +
    ')' +
    (p.previousByDay === null
      ? ''
      : ' · last month by day ' + p.day + ': ' + nCount(p.previousByDay))
  );
}

/**
 * This month, day by day, on one axis: the running total of enrollments (solid),
 * the month's target spread evenly over its days (dashed, "on pace"), and last
 * month's running total (grey) for a day-for-day comparison.
 */
export function EnrolmentDailyChart(props: { progress: DailyProgress }) {
  const p = props.progress;
  const W = 760,
    H = 220,
    L = 52,
    R = 16,
    T = 14,
    B = 30;
  const xMax = Math.max(p.daysInMonth, p.previous.length);
  let max = Math.max(1, p.target, p.enrolled);
  p.previous.forEach(function (v) {
    max = Math.max(max, v);
  });
  const top = niceTop(max);
  const iw = W - L - R,
    ih = H - T - B;
  const x = function (day: number) {
    return L + (day / xMax) * iw;
  };
  const y = function (v: number) {
    return T + ih - (v / top) * ih;
  };
  const path = function (vals: number[]) {
    return (
      'M' +
      x(0).toFixed(1) +
      ' ' +
      y(0).toFixed(1) +
      vals
        .map(function (v, i) {
          return ' L' + x(i + 1).toFixed(1) + ' ' + y(v).toFixed(1);
        })
        .join('')
    );
  };
  const ticks = [1, 5, 10, 15, 20, 25, p.daysInMonth].filter(
    function (d, i, a) {
      return (
        d <= xMax &&
        a.indexOf(d) === i &&
        (d === p.daysInMonth || p.daysInMonth - d >= 3)
      );
    },
  );
  const ahead = p.gap >= 0;
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-1">
        <div className="text-sm font-semibold text-gray-900">
          {'This month, day by day · ' + monthLbl(p.month)}
        </div>
        <div className="text-xs text-gray-500 flex flex-wrap items-center">
          {swatch(C_CUM_ACTUAL, 'Enrolled this month', true)}
          {swatch(C_CUM_TARGET, 'On pace for the target', true, true)}
          {swatch(
            '#cbd5e1',
            'Last month (' + monthLbl(p.previousMonth, true) + ')',
            true,
          )}
        </div>
      </div>
      <div
        className={
          'text-xs font-medium mb-1 ' +
          (ahead ? 'text-green-700' : 'text-red-700')
        }
      >
        {dailyReadout(p)}
      </div>
      <svg
        viewBox={'0 0 ' + W + ' ' + H}
        className="w-full h-auto block"
        role="img"
        aria-label="Enrollment this month, day by day"
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
                fill="#6b7280"
                textAnchor="end"
              >
                {nCount(top * f)}
              </text>
            </g>
          );
        })}
        {ticks.map(function (d) {
          return (
            <text
              key={d}
              x={x(d)}
              y={H - 10}
              fontSize="10"
              fill="#6b7280"
              textAnchor="middle"
            >
              {String(d)}
            </text>
          );
        })}
        {p.previous.length ? (
          <path
            d={path(p.previous)}
            fill="none"
            stroke="#cbd5e1"
            strokeWidth="2"
          />
        ) : null}
        <line
          x1={x(0)}
          y1={y(0)}
          x2={x(p.daysInMonth)}
          y2={y(p.target)}
          stroke={C_CUM_TARGET}
          strokeWidth="2"
          strokeDasharray="5 4"
        />
        <path
          d={path(p.cumulative)}
          fill="none"
          stroke={C_CUM_ACTUAL}
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
        <circle
          cx={x(p.day)}
          cy={y(p.enrolled)}
          r="3.5"
          fill={C_CUM_ACTUAL}
          stroke="#fff"
          strokeWidth="1.5"
        />
      </svg>
      <p className="text-xs text-gray-500 mt-0.5">
        {'Day of the month along the bottom. The dashed line is ' +
          monthLbl(p.month, true) +
          "'s target of " +
          nCount(p.target) +
          ' spread evenly over its ' +
          p.daysInMonth +
          ' days.'}
      </p>
    </div>
  );
}
