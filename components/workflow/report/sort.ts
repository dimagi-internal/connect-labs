/**
 * Table sorting for indicator reports.
 *
 * Stable, and a row with no value sits at the bottom in BOTH directions: "no
 * data" is not a low score.
 */
import React from 'react';

export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

function blank(v: unknown): boolean {
  return (
    v === null ||
    v === undefined ||
    v === '' ||
    (typeof v === 'number' && isNaN(v))
  );
}

export function sortRows<T>(
  rows: T[],
  sort: SortState | null | undefined,
  valueOf: (row: T, key: string) => unknown,
): T[] {
  if (!sort) return rows;
  const dir = sort.dir === 'asc' ? 1 : -1;
  return rows
    .map(function (r, i) {
      return { r, i, v: valueOf(r, sort.key) as any };
    })
    .sort(function (a, b) {
      const an = blank(a.v);
      const bn = blank(b.v);
      if (an && bn) return a.i - b.i;
      if (an) return 1;
      if (bn) return -1;
      if (a.v === b.v) return a.i - b.i;
      return a.v < b.v ? -dir : dir;
    })
    .map(function (x) {
      return x.r;
    });
}

/** The next state when a column is clicked: descending first, then flip. */
export function nextSort(
  cur: SortState | null | undefined,
  key: string,
): SortState {
  return {
    key,
    dir: cur && cur.key === key && cur.dir === 'desc' ? 'asc' : 'desc',
  };
}

/**
 * Sort state for several tables at once, keyed by table name. Held by the
 * page, not by a table: a report's tables are usually defined inside its
 * render function, so each state change gives them a new identity and any
 * state they held themselves would be thrown away.
 */
export function useTableSort(initial?: Record<string, SortState>) {
  const [state, setState] = React.useState<Record<string, SortState>>(
    initial || {},
  );
  return {
    sortOf: function (table: string): SortState | null {
      return state[table] || null;
    },
    toggle: function (table: string, key: string) {
      setState(function (prev) {
        return Object.assign({}, prev, { [table]: nextSort(prev[table], key) });
      });
    },
    set: function (table: string, sort: SortState) {
      setState(function (prev) {
        return Object.assign({}, prev, { [table]: sort });
      });
    },
  };
}
