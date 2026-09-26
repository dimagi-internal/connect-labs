/**
 * The registry display contract, read (VERSION 3).
 *
 * A generic indicator report holds NO presentation of its own: its headline
 * tiles, targets, scorecard columns and groups, nouns and case columns come from
 * the payload's `display` block, which the semantic snapshot builder resolves
 * from the bound registry (connect_labs/semantic/display.py). These helpers turn
 * that block into what the other library components take, and fall back to the
 * measure catalog for a payload built before `display` existed.
 *
 * Plain functions and components over plain data; nothing here fetches.
 */
import React from 'react';
import { targetOf } from './Benchmark';
import { dateLbl, fmtValue, nCount, type Measure } from './format';
import type { TileSpec } from './HeadlineTiles';
import type { ScorecardColumn } from './Scorecard';

export interface Noun {
  name: string;
  plural: string;
}

export interface IndicatorDisplay {
  label?: string;
  plain?: string | null;
  headline?: number | null;
  target?: number | null;
  order?: number;
  scorecard?: boolean;
  credibility?: string | null;
}

export interface CaseField {
  field: string;
  label: string;
  format?: 'date' | 'count' | 'number' | 'text';
  unit?: string;
}

export interface Display {
  title: string | null;
  entity: Noun & { key?: string | null };
  worker: Noun;
  organisation: Noun;
  categories: string[];
  headline: string[];
  indicators: Record<string, IndicatorDisplay>;
  case_fields: CaseField[];
  reading: { column: string; label: string; unit?: string } | null;
  visits_pipeline: string | null;
  targets_note?: string | null;
}

type CatalogMeasure = Measure & {
  direction?: string;
  bands?: any;
  prominence?: string;
  label?: string;
  plain?: string;
  headline?: number | null;
  target?: number | null;
  order?: number;
  scorecard?: boolean;
};

function catalog(payload: any): CatalogMeasure[] {
  return ((payload && payload.cMeasures) || []).filter(function (m: any) {
    return m && m.indicator;
  });
}

/** The payload's display block, every key present. */
export function displayOf(payload: any): Display {
  const raw = (payload && payload.display) || {};
  const ms = catalog(payload);
  const indicators: Record<string, IndicatorDisplay> = {};
  const anyTop = ms.some(function (m) {
    return !!m.prominence;
  });
  ms.forEach(function (m, i) {
    const d = (raw.indicators || {})[m.indicator as string] || {};
    indicators[m.indicator as string] = {
      label: d.label || m.label || m.title || m.indicator,
      plain: d.plain !== undefined ? d.plain : m.plain || null,
      headline:
        d.headline !== undefined ? d.headline : (m.headline as any) || null,
      target: d.target !== undefined ? d.target : m.target,
      order:
        d.order !== undefined ? d.order : m.order !== undefined ? m.order : i,
      scorecard:
        d.scorecard !== undefined
          ? d.scorecard
          : m.scorecard !== undefined
            ? m.scorecard
            : !anyTop || m.prominence === 'Top',
      credibility: d.credibility || null,
    };
  });
  const cats: string[] = (raw.categories || []).slice();
  ms.forEach(function (m) {
    const c = m.category || 'Other';
    if (cats.indexOf(c) === -1) cats.push(c);
  });
  let headline: string[] = raw.headline;
  if (!headline) {
    const declared = ms
      .filter(function (m) {
        return indicators[m.indicator as string].headline;
      })
      .sort(function (a, b) {
        return (
          Number(indicators[a.indicator as string].headline) -
          Number(indicators[b.indicator as string].headline)
        );
      });
    headline = (
      declared.length
        ? declared
        : ms
            .filter(function (m) {
              return !anyTop || m.prominence === 'Top';
            })
            .slice(0, 5)
    ).map(function (m) {
      return m.indicator as string;
    });
  }
  function noun(v: any, name: string, plural: string): Noun {
    return {
      name: (v && v.name) || name,
      plural: (v && v.plural) || plural,
    };
  }
  return {
    title: raw.title || null,
    entity: Object.assign(noun(raw.entity, 'case', 'cases'), {
      key: (raw.entity && raw.entity.key) || null,
    }),
    worker: noun(raw.worker, 'worker', 'workers'),
    organisation: noun(raw.organisation, 'organisation', 'organisations'),
    categories: cats,
    headline: headline,
    indicators: indicators,
    case_fields: raw.case_fields || [
      { field: 'first_visit_date', label: 'First visit', format: 'date' },
      { field: 'last_visit_date', label: 'Last visit', format: 'date' },
      { field: 'total_visits', label: 'Visits', format: 'count' },
    ],
    reading: raw.reading || null,
    visits_pipeline: raw.visits_pipeline || null,
    targets_note: raw.targets_note || null,
  };
}

/** An indicator's target in the units its VALUES carry (a % target of 70 -> 0.7). */
export function targetValue(
  m: CatalogMeasure | null | undefined,
  d?: IndicatorDisplay | null,
): number | null {
  if (!m) return null;
  const t =
    d && d.target !== undefined && d.target !== null ? d.target : m.target;
  if (t === null || t === undefined || isNaN(Number(t)))
    return targetOf(m as any);
  return m.unit === '%' ? Number(t) / 100 : Number(t);
}

/** The headline row's tile specs, in the registry's tile order. */
export function headlineSpecs(payload: any, display?: Display): TileSpec[] {
  const dsp = display || displayOf(payload);
  const byId: Record<string, CatalogMeasure> = {};
  catalog(payload).forEach(function (m) {
    byId[m.indicator as string] = m;
  });
  return dsp.headline
    .filter(function (id) {
      return byId[id];
    })
    .map(function (id) {
      const m = byId[id];
      const d = dsp.indicators[id] || {};
      const t = targetValue(m, d);
      const pct = m.unit === '%';
      const count = !pct && m.kind === 'count';
      return {
        id: id,
        label: d.label || m.title || id,
        pct: pct,
        count: count,
        unit: pct || m.unit === 'n' ? undefined : m.unit,
        target: t === null ? undefined : t,
        sub:
          t !== null
            ? 'target ' + fmtValue(m, t)
            : m.direction === 'mid2'
              ? 'two-sided'
              : '',
      };
    });
}

export interface ScorecardLayout {
  columns: (ScorecardColumn & { category: string })[];
  groups: { label: string; span: number }[];
}

/** Scorecard columns in category order then `order`, with their group banner. */
export function scorecardLayout(
  payload: any,
  display?: Display,
): ScorecardLayout {
  const dsp = display || displayOf(payload);
  const cols = catalog(payload)
    .filter(function (m) {
      return (dsp.indicators[m.indicator as string] || {}).scorecard;
    })
    .map(function (m) {
      const d = dsp.indicators[m.indicator as string] || {};
      return {
        id: m.indicator as string,
        label: d.label || m.title || (m.indicator as string),
        title: (m.title || '') + (d.plain ? ' — ' + d.plain : ''),
        category: m.category || 'Other',
        order: Number(d.order) || 0,
      };
    })
    .sort(function (a, b) {
      const ca = dsp.categories.indexOf(a.category);
      const cb = dsp.categories.indexOf(b.category);
      return ca !== cb ? ca - cb : a.order - b.order;
    });
  const groups: { label: string; span: number }[] = [];
  cols.forEach(function (c) {
    const last = groups[groups.length - 1];
    if (last && last.label === c.category) last.span++;
    else groups.push({ label: c.category, span: 1 });
  });
  return {
    columns: cols.map(function (c) {
      return { id: c.id, label: c.label, title: c.title, category: c.category };
    }),
    groups: groups,
  };
}

/** One case-table cell, as its field's format says. */
export function fmtCaseField(f: CaseField, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (f.format === 'date') return dateLbl(value) || '—';
  if (f.format === 'count') return nCount(value) + (f.unit ? ' ' + f.unit : '');
  if (f.format === 'number') {
    const n = Number(value);
    return (
      (isNaN(n) ? String(value) : String(Math.round(n * 10) / 10)) +
      (f.unit ? ' ' + f.unit : '')
    );
  }
  return String(value) + (f.unit ? ' ' + f.unit : '');
}

/** "3 babies" / "1 baby". */
export function nounCount(n: unknown, noun: Noun): string {
  return nCount(n) + ' ' + (Number(n) === 1 ? noun.name : noun.plural);
}

/** "Baby" from "baby". */
export function cap(s: string | null | undefined): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

/** One case's readings over time: a line, a dot per reading. */
export function ReadingChart(props: {
  points: { date: string; value: number }[];
  label?: string;
  unit?: string;
}) {
  const pts = (props.points || []).filter(function (p) {
    return p && p.date && p.value !== null && !isNaN(Number(p.value));
  });
  if (pts.length < 1)
    return (
      <div className="text-xs text-gray-400 py-6 text-center">
        No {props.label ? props.label.toLowerCase() : 'reading'} recorded.
      </div>
    );
  const W = 520,
    H = 160,
    L = 44,
    R = 12,
    T = 12,
    B = 24;
  const t0 = new Date(pts[0].date.slice(0, 10) + 'T00:00:00Z').getTime();
  const t1 = new Date(
    pts[pts.length - 1].date.slice(0, 10) + 'T00:00:00Z',
  ).getTime();
  let lo = Infinity,
    hi = -Infinity;
  pts.forEach(function (p) {
    lo = Math.min(lo, Number(p.value));
    hi = Math.max(hi, Number(p.value));
  });
  if (hi === lo) {
    hi = hi + 1;
    lo = lo - 1;
  }
  const pad = (hi - lo) * 0.1;
  lo -= pad;
  hi += pad;
  function x(d: string) {
    const t = new Date(d.slice(0, 10) + 'T00:00:00Z').getTime();
    return t1 === t0
      ? L + (W - L - R) / 2
      : L + ((t - t0) / (t1 - t0)) * (W - L - R);
  }
  function y(v: number) {
    return T + (H - T - B) - ((v - lo) / (hi - lo)) * (H - T - B);
  }
  const d = pts
    .map(function (p, i) {
      return (
        (i ? 'L' : 'M') +
        x(p.date).toFixed(1) +
        ' ' +
        y(Number(p.value)).toFixed(1)
      );
    })
    .join(' ');
  return (
    <svg
      viewBox={'0 0 ' + W + ' ' + H}
      className="w-full h-auto block"
      role="img"
      aria-label={props.label || 'Readings'}
    >
      {[lo, (lo + hi) / 2, hi].map(function (t) {
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
              {nCount(t)}
            </text>
          </g>
        );
      })}
      <path d={d} fill="none" stroke="#4f46e5" strokeWidth="2" />
      {pts.map(function (p, i) {
        return (
          <circle
            key={i}
            cx={x(p.date)}
            cy={y(Number(p.value))}
            r="3"
            fill="#4f46e5"
            stroke="#fff"
            strokeWidth="1.5"
          >
            <title>
              {dateLbl(p.date) +
                ': ' +
                nCount(p.value) +
                (props.unit ? ' ' + props.unit : '')}
            </title>
          </circle>
        );
      })}
      <text x={L} y={H - 6} fontSize="9.5" fill="#9ca3af">
        {dateLbl(pts[0].date)}
      </text>
      <text x={W - R} y={H - 6} fontSize="9.5" fill="#9ca3af" textAnchor="end">
        {dateLbl(pts[pts.length - 1].date)}
      </text>
    </svg>
  );
}

function defBlock(title: string, text: string) {
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">
          {title}
        </span>
        <button
          type="button"
          className="text-xs text-indigo-600 hover:underline"
          onClick={function () {
            if (navigator.clipboard && navigator.clipboard.writeText)
              navigator.clipboard.writeText(text);
          }}
        >
          Copy
        </button>
      </div>
      <pre className="mt-1 text-xs bg-gray-50 border border-gray-200 rounded-md p-2 overflow-x-auto whitespace-pre max-h-72">
        {text}
      </pre>
    </div>
  );
}

/**
 * One indicator's definition as the explain reader returns it
 * (semantic/explain.py): the authored sentence, how it is counted, its terms,
 * its thresholds, and the SQL. `entry` is one element of the endpoint's
 * `indicators`; `entityPlural` names what it counts ("all babies").
 */
export function DefinitionBody(props: { entry: any; entityPlural?: string }) {
  const e = props.entry || {};
  const en = e.english || {};
  const all = 'all ' + (props.entityPlural || 'cases');
  const [showConsts, setShowConsts] = React.useState(false);
  function conditions(where: string[] | undefined) {
    if (!where || !where.length)
      return <span className="text-gray-500">{all}</span>;
    return (
      <ul className="space-y-0.5">
        {where.map(function (c) {
          return (
            <li key={c} className="flex gap-1.5">
              <span className="text-gray-400">•</span>
              <span>{c}</span>
            </li>
          );
        })}
      </ul>
    );
  }
  function row(label: string, body: React.ReactNode) {
    return (
      <div
        key={label}
        className="grid grid-cols-[6rem_1fr] gap-2 py-1.5 border-t border-gray-100 first:border-t-0"
      >
        <div className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold pt-0.5">
          {label}
        </div>
        <div className="text-sm text-gray-800">{body}</div>
      </div>
    );
  }
  const h = en.how;
  const rows: React.ReactNode[] = [];
  if (h) {
    if (h.kind === 'value') {
      rows.push(row('Value', cap(h.value)));
      rows.push(row('Over', conditions(h.base && h.base.where)));
    } else {
      rows.push(
        row(
          'Out of',
          <div>
            <div className="text-gray-600 mb-0.5">
              {cap(h.base && h.base.what)} where
            </div>
            {conditions(h.base && h.base.where)}
          </div>,
        ),
      );
      rows.push(
        row(
          h.kind === 'percent' ? 'Counts' : 'Divides',
          <div>
            <div className="text-gray-600 mb-0.5">
              {h.counts && h.counts.where && h.counts.where.length
                ? 'Those ' + h.counts.what + ' where'
                : cap(h.counts && h.counts.what)}
            </div>
            {h.counts && h.counts.where && h.counts.where.length
              ? conditions(h.counts.where)
              : null}
          </div>,
        ),
      );
    }
    if (h.shown_when)
      rows.push(row('Shown', 'Only when ' + h.shown_when + '.'));
  }
  const measureSql = [
    '-- ' + e.measure,
    (e.expression && e.expression.compiled) || '',
  ]
    .concat(
      (e.components || []).map(function (c: any) {
        return '-- ' + c.name + '\n' + (c.compiled || c.sql || '');
      }),
    )
    .join('\n');
  const propSql = (e.properties || [])
    .map(function (p: any) {
      return (
        (p.means ? '-- ' + p.name + ': ' + p.means + '\n' : '') +
        p.name +
        ' = ' +
        p.sql
      );
    })
    .join('\n');
  const consts = Object.keys(e.constants || {})
    .map(function (k) {
      return k + ' = ' + e.constants[k];
    })
    .join(', ');
  const reads = (en.reads || []).filter(function (r: any) {
    return r.means;
  });
  return (
    <div>
      {en.plain ? (
        <p className="text-base text-gray-900 leading-snug">{en.plain}</p>
      ) : null}
      {rows.length ? (
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1">
          {rows}
        </div>
      ) : en.definition ? (
        <p className="text-gray-600 text-sm mt-2">{en.definition}</p>
      ) : null}
      {reads.length ? (
        <dl className="mt-3 text-sm space-y-1.5">
          {reads.map(function (r: any) {
            return (
              <div key={r.name}>
                <dt className="font-medium text-gray-900 inline">
                  {r.label || r.name}
                </dt>
                <dd className="text-gray-600 inline">{' — ' + r.means}</dd>
              </div>
            );
          })}
        </dl>
      ) : null}
      {consts ? (
        <div className="mt-3 text-xs">
          <button
            type="button"
            className="text-indigo-600 hover:underline"
            onClick={function () {
              setShowConsts(!showConsts);
            }}
          >
            {(showConsts ? 'Hide' : 'Show') + ' the thresholds it uses'}
          </button>
          {showConsts ? (
            <div className="mt-1 font-mono text-gray-600 break-words">
              {consts}
            </div>
          ) : null}
        </div>
      ) : null}
      {defBlock('Measure', measureSql)}
      {propSql ? defBlock('Properties it reads', propSql) : null}
    </div>
  );
}

/**
 * The definition dialog: a header, the body for a loaded entry, and the links.
 * `state` is {status: 'loading'|'error'|'ready', entry?, error?}.
 */
export function DefinitionModal(props: {
  id: string;
  title?: string;
  state: { status: string; entry?: any; error?: string };
  onClose: () => void;
  entityPlural?: string;
  downloadUrl?: string;
  textUrl?: string;
  footer?: React.ReactNode;
}) {
  const st = props.state || { status: 'loading' };
  React.useEffect(
    function () {
      function onKey(ev: KeyboardEvent) {
        if (ev.key === 'Escape') props.onClose();
      }
      window.addEventListener('keydown', onKey);
      return function () {
        window.removeEventListener('keydown', onKey);
      };
    },
    [props.onClose],
  );
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-gray-900/40 p-4 overflow-y-auto"
      onClick={props.onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="bg-white rounded-xl shadow-xl w-full max-w-3xl mt-12 text-sm text-left"
        onClick={function (ev) {
          ev.stopPropagation();
        }}
      >
        <div className="px-5 py-3 border-b border-gray-100 flex items-start justify-between gap-3">
          <div>
            <div className="font-mono text-xs text-gray-400">{props.id}</div>
            <div className="text-base font-semibold text-gray-900">
              {props.title || (st.entry && st.entry.title) || props.id}
            </div>
          </div>
          <button
            type="button"
            aria-label="Close"
            className="text-gray-400 hover:text-gray-700 text-xl leading-none"
            onClick={props.onClose}
          >
            {'×'}
          </button>
        </div>
        <div className="px-5 py-4">
          {st.status === 'error' ? (
            <div className="text-xs text-red-700">
              Could not read the definition: {st.error}
            </div>
          ) : st.status !== 'ready' ? (
            <div className="text-xs text-gray-400">Reading the definition…</div>
          ) : (
            <DefinitionBody
              entry={st.entry}
              entityPlural={props.entityPlural}
            />
          )}
        </div>
        <div className="px-5 py-3 border-t border-gray-100 flex items-center gap-3 flex-wrap text-xs">
          {props.footer}
          {props.downloadUrl ? (
            <a
              className="ml-auto text-indigo-600 hover:underline"
              href={props.downloadUrl}
            >
              Download SQL
            </a>
          ) : null}
          {props.textUrl ? (
            <a
              className="text-indigo-600 hover:underline"
              href={props.textUrl}
              target="_blank"
              rel="noopener"
            >
              Open as text
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}
