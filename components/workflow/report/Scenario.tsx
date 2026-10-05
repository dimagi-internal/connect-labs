/**
 * Scenario building blocks: the controls and read-outs a report needs when its
 * figures depend on ASSUMPTIONS the reader is meant to change -- a
 * cost-effectiveness model, a budget scenario, a what-if on a target.
 *
 * Same look as the rest of the library (white `rounded-xl` cards, indigo
 * accents, tabular numbers). Same contract: plain data and callbacks, nothing
 * fetches, nothing knows which program it is drawing. A number input hands
 * back the raw string the user typed, so a half-typed "0." survives a render;
 * the page parses it.
 */
import React from 'react';

export type Tone = 'good' | 'watch' | 'bad' | 'neutral';

const TONE_TEXT: Record<Tone, string> = {
  good: 'text-green-700',
  watch: 'text-amber-700',
  bad: 'text-red-700',
  neutral: 'text-gray-500',
};

const TONE_FILL: Record<Tone, string> = {
  good: '#16a34a',
  watch: '#f59e0b',
  bad: '#dc2626',
  neutral: '#6366f1',
};

/** "a – b", or just "a" when both ends format the same. */
export function rangeText(
  lo: unknown,
  hi: unknown,
  fmt: (v: unknown) => string,
): string {
  const a = fmt(lo);
  const b = fmt(hi);
  return a === b ? a : a + ' – ' + b;
}

export interface StatTile {
  id: string;
  label: string;
  /** Already formatted: "16.3x – 20.0x", "$1,886", "3.3%". */
  value: React.ReactNode;
  unit?: string;
  sub?: React.ReactNode;
  tone?: Tone | null;
  /** The word beside the sub line, coloured by tone ("Clears the bar"). */
  toneLabel?: string;
}

/**
 * Headline tiles for figures that are not a single banded indicator -- a
 * range, a currency, a modelled result. Looks like HeadlineTiles.
 */
export function StatTiles(props: { tiles: StatTile[]; columns?: number }) {
  const cols = props.columns || Math.min(5, Math.max(1, props.tiles.length));
  const lg: Record<number, string> = {
    1: 'lg:grid-cols-1',
    2: 'lg:grid-cols-2',
    3: 'lg:grid-cols-3',
    4: 'lg:grid-cols-4',
    5: 'lg:grid-cols-5',
  };
  return (
    <div className={'grid grid-cols-2 gap-3 ' + (lg[cols] || 'lg:grid-cols-5')}>
      {props.tiles.map(function (t) {
        return (
          <div
            key={t.id}
            className="bg-white border border-gray-200 rounded-xl px-4 pt-3 pb-3"
          >
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 truncate">
              {t.label}
            </div>
            <div className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">
              {t.value}
              {t.unit ? (
                <span className="ml-2 text-xs font-medium text-gray-400">
                  {t.unit}
                </span>
              ) : null}
            </div>
            {t.sub || t.toneLabel ? (
              <div className="mt-1 flex items-center justify-between gap-2 text-xs text-gray-600">
                <span className="truncate">{t.sub}</span>
                {t.toneLabel ? (
                  <span
                    className={
                      'font-semibold whitespace-nowrap ' +
                      TONE_TEXT[t.tone || 'neutral']
                    }
                  >
                    {t.toneLabel}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

const WIDTH: Record<string, string> = {
  xs: 'w-16',
  sm: 'w-24',
  md: 'w-32',
};

/** A compact number input with optional prefix/suffix ("$", "%", "x"). */
export function NumberField(props: {
  value: string | number;
  onChange: (raw: string) => void;
  prefix?: string;
  suffix?: string;
  step?: number | string;
  min?: number;
  max?: number;
  width?: 'xs' | 'sm' | 'md';
  title?: string;
  /** Marks the value as differing from its sourced default. */
  edited?: boolean;
  ariaLabel?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1" title={props.title}>
      {props.prefix ? (
        <span className="text-xs text-gray-500">{props.prefix}</span>
      ) : null}
      <input
        type="number"
        aria-label={props.ariaLabel}
        step={props.step === undefined ? 'any' : props.step}
        min={props.min}
        max={props.max}
        value={
          props.value === null || props.value === undefined ? '' : props.value
        }
        onChange={function (e) {
          props.onChange(e.target.value);
        }}
        className={
          'rounded-lg border px-2 py-1 text-right text-sm tabular-nums ' +
          'focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 ' +
          (props.edited
            ? 'border-indigo-400 bg-indigo-50 text-indigo-900 '
            : 'border-gray-300 bg-white text-gray-900 ') +
          (WIDTH[props.width || 'sm'] || WIDTH.sm)
        }
      />
      {props.suffix ? (
        <span className="text-xs text-gray-500">{props.suffix}</span>
      ) : null}
    </span>
  );
}

/** A low–high pair of NumberFields. */
export function RangeField(props: {
  low: string | number;
  high: string | number;
  onChange: (low: string, high: string) => void;
  prefix?: string;
  suffix?: string;
  step?: number | string;
  width?: 'xs' | 'sm' | 'md';
  edited?: boolean;
  ariaLabel?: string;
}) {
  const low = props.low;
  const high = props.high;
  return (
    <span className="inline-flex items-center gap-1">
      <NumberField
        value={low}
        ariaLabel={(props.ariaLabel || 'range') + ' low'}
        prefix={props.prefix}
        step={props.step}
        width={props.width || 'xs'}
        edited={props.edited}
        onChange={function (v) {
          props.onChange(v, String(high));
        }}
      />
      <span className="text-xs text-gray-400">to</span>
      <NumberField
        value={high}
        ariaLabel={(props.ariaLabel || 'range') + ' high'}
        prefix={props.prefix}
        suffix={props.suffix}
        step={props.step}
        width={props.width || 'xs'}
        edited={props.edited}
        onChange={function (v) {
          props.onChange(String(low), v);
        }}
      />
    </span>
  );
}

/**
 * One assumption: label, control, and where its default came from. Stack them
 * inside a Card to make an assumptions panel.
 */
export function Field(props: {
  label: React.ReactNode;
  children?: React.ReactNode;
  hint?: React.ReactNode;
}) {
  return (
    <div className="py-2">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-1">
        {props.label}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm text-gray-800">
        {props.children}
      </div>
      {props.hint ? (
        <div className="mt-1 text-xs text-gray-500">{props.hint}</div>
      ) : null}
    </div>
  );
}

/** A small pill-style switch between a few modes. Tabs are for page sections. */
export function Segmented(props: {
  options: { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div
      className="inline-flex rounded-lg border border-gray-300 bg-gray-50 p-0.5"
      role="radiogroup"
    >
      {props.options.map(function (o) {
        const on = o.id === props.value;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={function () {
              props.onChange(o.id);
            }}
            className={
              'px-3 py-1 rounded-md text-xs font-semibold ' +
              (on
                ? 'bg-white text-indigo-700 shadow-sm'
                : 'text-gray-500 hover:text-gray-800')
            }
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export interface StripMarker {
  value: number;
  label: string;
  dashed?: boolean;
}

/** Position of `v` on [min, max] as a 0..100 percentage, log or linear. */
export function stripPos(
  v: number,
  min: number,
  max: number,
  log?: boolean,
): number {
  const c = Math.max(min, Math.min(max, v));
  if (log) {
    if (min <= 0) return 0;
    return (
      ((Math.log(c) - Math.log(min)) / (Math.log(max) - Math.log(min))) * 100
    );
  }
  return ((c - min) / (max - min)) * 100;
}

/**
 * A low–high range drawn on a fixed axis, against labelled threshold markers:
 * "where does this estimate sit relative to the bar". The page decides the
 * tone; the strip only draws it.
 */
export function RangeStrip(props: {
  lo: number;
  hi: number;
  min: number;
  max: number;
  log?: boolean;
  markers?: StripMarker[];
  tone?: Tone;
  width?: number;
  title?: string;
}) {
  const lo = Math.min(props.lo, props.hi);
  const hi = Math.max(props.lo, props.hi);
  const a = stripPos(lo, props.min, props.max, props.log);
  const b = stripPos(hi, props.min, props.max, props.log);
  return (
    <div
      className="relative h-3 rounded-full bg-gray-100"
      style={{ width: props.width || 200 }}
      title={props.title}
      role="img"
      aria-label={props.title}
    >
      <div
        className="absolute top-0 h-3 rounded-full"
        style={{
          left: a + '%',
          width: Math.max(4, b - a) + '%',
          background: TONE_FILL[props.tone || 'neutral'],
        }}
      />
      {(props.markers || []).map(function (m) {
        return (
          <div
            key={m.label}
            title={m.label}
            className="absolute"
            style={{
              top: -3,
              bottom: -3,
              left: stripPos(m.value, props.min, props.max, props.log) + '%',
              borderLeft: '2px ' + (m.dashed ? 'dashed' : 'solid') + ' #1d1d24',
            }}
          />
        );
      })}
    </div>
  );
}
