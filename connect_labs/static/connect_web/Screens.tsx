/** The screens before a form: menus, case lists, a case's details. Big rows, made for thumbs. */

import React, { useEffect, useState } from 'react';

import { visibleColumns } from './state';
import type { Command, EntityList, Notice } from './state';

export function Notification({ notice }: { notice?: Notice | null }) {
  if (!notice) return null;
  return (
    <div
      role={notice.error ? 'alert' : 'status'}
      className={`mb-3 rounded-xl border px-3 py-2 text-sm ${notice.error ? 'border-red-200 bg-red-50 text-red-900' : 'border-sky-200 bg-sky-50 text-sky-900'}`}
    >
      {notice.message}
    </div>
  );
}

export function MenuScreen({
  commands,
  onSelect,
  busy,
}: {
  commands: Command[];
  onSelect: (index: number) => void;
  busy: boolean;
}) {
  if (!commands.length)
    return (
      <p className="rounded-xl bg-white p-4 text-sm text-slate-500 shadow-sm">
        There's nothing in this menu.
      </p>
    );
  return (
    <ul className="space-y-2" data-testid="cw-menu">
      {commands.map((c) => (
        <li key={c.index}>
          <button
            type="button"
            disabled={busy}
            onClick={() => onSelect(c.index)}
            className="flex min-h-14 w-full items-center gap-3 rounded-xl bg-white px-4 py-3 text-left shadow-sm hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-600 disabled:opacity-60"
          >
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-indigo-800"
              aria-hidden="true"
            >
              <i
                className={`fa-solid ${c.navigationState === 'JUMP' ? 'fa-file-pen' : 'fa-folder'}`}
              />
            </span>
            <span className="min-w-0 flex-1 text-base font-medium leading-snug">
              {c.displayText}
            </span>
            {c.badgeText && (
              <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">
                {c.badgeText}
              </span>
            )}
            <i
              className="fa-solid fa-chevron-right shrink-0 text-slate-400"
              aria-hidden="true"
            />
          </button>
        </li>
      ))}
    </ul>
  );
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return '';
  return String(value);
}

export function CaseListScreen({
  list,
  search,
  busy,
  onSearch,
  onPage,
  onOpen,
  onAction,
}: {
  list: EntityList;
  search: string;
  busy: boolean;
  onSearch: (text: string) => void;
  onPage: (page: number) => void;
  onOpen: (caseId: string) => void;
  onAction: (index: number) => void;
}) {
  const [query, setQuery] = useState(search);
  useEffect(() => setQuery(search), [search]);
  const columns = visibleColumns(list);
  const headers = list.headers || [];
  const entities = list.entities || [];
  const page = list.currentPage ?? 0;
  const pages = list.pageCount ?? 0;
  return (
    <div className="space-y-3" data-testid="cw-case-list">
      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          onSearch(query.trim());
        }}
        className="flex gap-2"
      >
        <label htmlFor="cw-case-search" className="sr-only">
          Search
        </label>
        <input
          id="cw-case-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search"
          className="block min-h-12 w-full rounded-xl border border-slate-300 bg-white px-3 text-base shadow-sm focus:border-indigo-600 focus:outline-none focus:ring-2 focus:ring-indigo-600/30"
        />
        <button
          type="submit"
          disabled={busy}
          className="min-h-12 shrink-0 rounded-xl bg-indigo-700 px-4 font-semibold text-white disabled:opacity-60"
          aria-label="Search"
        >
          <i className="fa-solid fa-magnifying-glass" aria-hidden="true" />
        </button>
      </form>
      {(list.actions || []).map((a, i) => (
        <button
          key={i}
          type="button"
          disabled={busy}
          onClick={() => onAction(i)}
          className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-indigo-300 bg-white px-4 font-semibold text-indigo-800 disabled:opacity-60"
        >
          <i className="fa-solid fa-plus" aria-hidden="true" />{' '}
          {a.text || 'New'}
        </button>
      ))}
      {list.isMultiSelect && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Choosing several cases at once isn't supported on the web yet; pick
          one.
        </p>
      )}
      {entities.length === 0 ? (
        <p className="rounded-xl bg-white p-4 text-sm text-slate-500 shadow-sm">
          {list.noItemsText ||
            (search ? 'Nothing matches that search.' : 'No cases yet.')}
        </p>
      ) : (
        <ul className="space-y-2">
          {entities.map((e) => {
            const [first, ...rest] = columns;
            return (
              <li key={e.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onOpen(e.id)}
                  className="block min-h-14 w-full rounded-xl bg-white px-4 py-3 text-left shadow-sm hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-600 disabled:opacity-60"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-base font-semibold">
                      {cell(e.data?.[first]) || '—'}
                    </span>
                    <i
                      className="fa-solid fa-chevron-right shrink-0 text-slate-400"
                      aria-hidden="true"
                    />
                  </span>
                  {rest.length > 0 && (
                    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
                      {rest.map((col) =>
                        cell(e.data?.[col]) ? (
                          <React.Fragment key={col}>
                            <dt className="text-slate-500">{headers[col]}</dt>
                            <dd className="min-w-0 truncate text-slate-800">
                              {cell(e.data?.[col])}
                            </dd>
                          </React.Fragment>
                        ) : null,
                      )}
                    </dl>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {pages > 1 && (
        <nav
          className="flex items-center justify-between gap-2"
          aria-label="Pages"
        >
          <button
            type="button"
            disabled={busy || page <= 0}
            onClick={() => onPage(page - 1)}
            className="min-h-11 rounded-xl border border-slate-300 bg-white px-4 font-medium disabled:opacity-40"
          >
            <i className="fa-solid fa-chevron-left" aria-hidden="true" />{' '}
            Previous
          </button>
          <span className="text-sm text-slate-600">
            Page {page + 1} of {pages}
          </span>
          <button
            type="button"
            disabled={busy || page + 1 >= pages}
            onClick={() => onPage(page + 1)}
            className="min-h-11 rounded-xl border border-slate-300 bg-white px-4 font-medium disabled:opacity-40"
          >
            Next <i className="fa-solid fa-chevron-right" aria-hidden="true" />
          </button>
        </nav>
      )}
      <p className="sr-only" aria-live="polite">
        {entities.length} cases shown
        {pages > 1 ? `, page ${page + 1} of ${pages}` : ''}.
      </p>
    </div>
  );
}

export function CaseDetailScreen({
  details,
  busy,
  onContinue,
}: {
  details: Array<{ title?: string; headers?: string[]; details?: unknown[] }>;
  busy: boolean;
  onContinue: () => void;
}) {
  const [tab, setTab] = useState(0);
  const current = details[tab] || details[0];
  return (
    <div className="space-y-3" data-testid="cw-case-detail">
      {details.length > 1 && (
        <div
          role="tablist"
          aria-label="Case details"
          className="-mx-3 flex gap-1 overflow-x-auto px-3"
        >
          {details.map((d, i) => (
            <button
              key={i}
              role="tab"
              type="button"
              aria-selected={i === tab}
              onClick={() => setTab(i)}
              className={`min-h-11 shrink-0 rounded-full px-4 text-sm font-medium ${i === tab ? 'bg-indigo-700 text-white' : 'bg-white text-slate-700 shadow-sm'}`}
            >
              {d.title || `Details ${i + 1}`}
            </button>
          ))}
        </div>
      )}
      {current ? (
        <dl
          className="divide-y divide-slate-100 rounded-xl bg-white px-4 shadow-sm"
          role="tabpanel"
        >
          {(current.headers || []).map((h, i) => (
            <div
              key={i}
              className="flex flex-col gap-0.5 py-3 sm:flex-row sm:gap-4"
            >
              <dt className="text-sm text-slate-500 sm:w-1/3">{h}</dt>
              <dd className="min-w-0 break-words text-base text-slate-900">
                {cell(current.details?.[i]) || '—'}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="rounded-xl bg-white p-4 text-sm text-slate-500 shadow-sm">
          No details for this case.
        </p>
      )}
      <StickyBar>
        <button
          type="button"
          disabled={busy}
          onClick={onContinue}
          className="min-h-12 w-full rounded-xl bg-indigo-700 px-4 font-semibold text-white disabled:opacity-60"
        >
          Continue{' '}
          <i className="fa-solid fa-arrow-right ml-1" aria-hidden="true" />
        </button>
      </StickyBar>
    </div>
  );
}

/** The bottom action bar: fixed to the screen on a phone, centred with the column on a desktop. */
export function StickyBar({ children }: { children: React.ReactNode }) {
  return (
    <>
      <div className="h-24" aria-hidden="true" />
      <div
        className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 backdrop-blur"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        <div className="mx-auto flex max-w-md gap-2 px-3 py-3 sm:max-w-2xl">
          {children}
        </div>
      </div>
    </>
  );
}

export function Skeleton({ label }: { label?: string }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-live="polite">
      <span className="sr-only">{label || 'Loading…'}</span>
      <div className="h-6 w-2/3 animate-pulse rounded bg-slate-200" />
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-16 animate-pulse rounded-xl bg-white shadow-sm"
        />
      ))}
      {label && <p className="text-center text-sm text-slate-500">{label}</p>}
    </div>
  );
}
