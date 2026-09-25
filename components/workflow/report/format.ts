/**
 * Value formatting and band vocabulary shared by every indicator report.
 *
 * Lifted verbatim from the KMC programme report, whose look is the library's
 * look. A graded cell is `{id, n, value, band, thinDenominator?}` as the
 * semantic snapshot builder emits it (connect_labs/semantic/snapshot.py); a
 * measure is one entry of the payload's measure catalog (`unit`, `kind`,
 * `min_denominator`, `title`, ...).
 */

export interface Cell {
  id?: string;
  n?: number | null;
  value?: number | string | null;
  band?: string | null;
  thinDenominator?: boolean;
  coverage?: number;
}

export interface Measure {
  indicator?: string;
  title?: string;
  unit?: string;
  kind?: string;
  min_denominator?: number;
  category?: string;
  series?: string;
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

/** Counts carry thousands separators: 37853 reads as a typo next to 37,853. */
export function nCount(value: unknown): string {
  if (value === null || value === undefined) return 'n/a';
  const num = Number(value);
  if (isNaN(num)) return String(value);
  return Math.round(num).toLocaleString('en-US');
}

/** "2026-09-15" -> "15 Sep". */
export function dateLbl(s: unknown): string {
  if (!s) return '';
  const p = String(s).slice(0, 10).split('-');
  if (p.length < 3) return String(s);
  return Number(p[2]) + ' ' + (MONTHS[Number(p[1]) - 1] || p[1]);
}

export function daysBetween(a: unknown, b: unknown): number | null {
  const da = new Date(String(a).slice(0, 10) + 'T00:00:00Z');
  const db = new Date(String(b).slice(0, 10) + 'T00:00:00Z');
  if (isNaN(da.getTime()) || isNaN(db.getTime())) return null;
  return Math.round((db.getTime() - da.getTime()) / 86400000);
}

export function hasValue(e: Cell | null | undefined): boolean {
  return !!e && e.value !== null && e.value !== undefined;
}

/**
 * A graded value as its unit says to print it. A `%` value is a fraction
 * (0.705 -> "70.5%"); a count carries separators, a mean keeps one decimal.
 */
export function fmtValue(m: Measure | null | undefined, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  const num = Number(v);
  if (isNaN(num)) return '—';
  const unit = (m && m.unit) || '';
  if (unit === '%') return (100 * num).toFixed(1) + '%';
  if (unit === 'g') return nCount(num);
  if (unit === 'wks') return String(Math.round(num * 10) / 10);
  if ((m && m.kind) === 'count') return nCount(num);
  return num.toFixed(1);
}

/** The band colours of a graded cell, in every place a band is shown. */
export const BAND_CLS: Record<string, string> = {
  green: 'bg-green-100 text-green-800',
  yellow: 'bg-amber-100 text-amber-800',
  red: 'bg-red-100 text-red-800',
  unbanded: 'bg-gray-100 text-gray-500',
  insufficient: 'bg-gray-50 text-gray-400',
  nodata: 'bg-gray-50 text-gray-300',
  notcredible: 'bg-slate-100 text-slate-500',
  notinapp: 'bg-slate-100 text-slate-400 italic',
  unrecorded: 'bg-amber-100 text-amber-900',
};

export const BAND_WORD: Record<string, string> = {
  green: 'On target',
  yellow: 'Watch',
  red: 'Off target',
};

export const BAND_TEXT: Record<string, string> = {
  green: 'text-green-700',
  yellow: 'text-amber-700',
  red: 'text-red-700',
};

const CELL_TINT: Record<string, string> = {
  red: 'bg-red-50 text-red-700 font-semibold',
  yellow: 'bg-amber-50 text-amber-800 font-semibold',
};

/** A table cell's tint: only off-target and watch cells are coloured. */
export function tintFor(e: Cell | null | undefined): string {
  return (e && e.band && CELL_TINT[e.band]) || '';
}

/** The colour a band's dot or line takes in a chart. */
export function bandColour(band: string | null | undefined): string {
  if (band === 'red') return '#dc2626';
  if (band === 'yellow') return '#d97706';
  if (band === 'green') return '#15803d';
  return '#4f46e5';
}

/** Counts of off-target and watch cells in one row's indicators. */
export function attention(ind: Record<string, Cell> | null | undefined): {
  reds: number;
  yellows: number;
} {
  let reds = 0;
  let yellows = 0;
  Object.keys(ind || {}).forEach(function (k) {
    const e = (ind || {})[k];
    if (e && e.band === 'red') reds++;
    if (e && e.band === 'yellow') yellows++;
  });
  return { reds, yellows };
}
