/**
 * Page chrome for indicator reports: cards, section titles, notices, the
 * report header. White `rounded-xl` cards on a grey rule, indigo accents --
 * the KMC programme report's look.
 */
import React from 'react';

export function Card(props: {
  children?: React.ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <div
      className={
        'bg-white border border-gray-200 rounded-xl ' +
        (props.padded === false ? '' : 'px-4 pt-3 pb-3 ') +
        (props.className || '')
      }
    >
      {props.children}
    </div>
  );
}

export function SectionTitle(props: {
  children?: React.ReactNode;
  right?: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="mb-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-gray-900">
          {props.children}
        </h2>
        {props.right ? (
          <div className="text-xs text-gray-500">{props.right}</div>
        ) : null}
      </div>
      {props.sub ? (
        <div className="text-xs text-gray-500 mt-0.5">{props.sub}</div>
      ) : null}
    </div>
  );
}

const NOTICE_CLS: Record<string, string> = {
  info: 'border-indigo-200 bg-indigo-50 text-indigo-900',
  warn: 'border-amber-200 bg-amber-50 text-amber-900',
  error: 'border-red-200 bg-red-50 text-red-800',
  muted: 'border-gray-200 bg-gray-50 text-gray-600',
};

export function Notice(props: {
  tone?: 'info' | 'warn' | 'error' | 'muted';
  children?: React.ReactNode;
  onRetry?: () => void;
}) {
  return (
    <div
      className={
        'rounded-xl border px-4 py-3 text-sm ' +
        NOTICE_CLS[props.tone || 'info']
      }
    >
      {props.children}
      {props.onRetry ? (
        <button
          type="button"
          className="ml-2 underline"
          onClick={props.onRetry}
        >
          try again
        </button>
      ) : null}
    </div>
  );
}

export function Loading(props: {
  children?: React.ReactNode;
  height?: number;
}) {
  return (
    <div
      className="relative rounded-xl bg-gray-50 animate-pulse"
      style={{ height: props.height || 96 }}
      aria-busy="true"
    >
      <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-400">
        {props.children || 'Loading…'}
      </div>
    </div>
  );
}

export function Pill(props: {
  tone?: 'final' | 'current' | 'source' | 'muted';
  children?: React.ReactNode;
  title?: string;
}) {
  const cls: Record<string, string> = {
    final: 'bg-green-100 text-green-800',
    current: 'bg-amber-100 text-amber-800',
    source: 'bg-indigo-50 text-indigo-700',
    muted: 'bg-gray-100 text-gray-600',
  };
  return (
    <span
      title={props.title}
      className={
        'inline-block px-2 py-0.5 rounded-full text-xs font-semibold ' +
        cls[props.tone || 'muted']
      }
    >
      {props.children}
    </span>
  );
}

/**
 * The top of a report: breadcrumb, title, the date its figures are as of,
 * whether they are final, where they came from, and the page's actions.
 */
export function ReportHeader(props: {
  crumbs?: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  badges?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div>
      {props.crumbs ? (
        <div className="text-xs text-gray-500 mb-1">{props.crumbs}</div>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-gray-900">{props.title}</h1>
          {props.subtitle ? (
            <div className="mt-1 text-sm text-gray-500">{props.subtitle}</div>
          ) : null}
          {props.badges ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {props.badges}
            </div>
          ) : null}
        </div>
        {props.actions ? (
          <div className="flex flex-wrap items-center gap-2">
            {props.actions}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function Button(props: {
  children?: React.ReactNode;
  onClick?: () => void;
  primary?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      title={props.title}
      disabled={props.disabled}
      onClick={props.onClick}
      className={
        'px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50 ' +
        (props.primary
          ? 'bg-indigo-600 text-white hover:bg-indigo-700'
          : 'bg-white border border-gray-300 text-gray-700 hover:bg-gray-50')
      }
    >
      {props.children}
    </button>
  );
}
