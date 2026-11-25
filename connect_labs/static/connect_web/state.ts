/**
 * The player's state: which screen Formplayer's last answer puts us on, and — inside a form —
 * the question tree, errors and where we are. Pure reducers, unit-tested (state.test.js).
 */

import type { Answer, FormNode, Page } from './tree';
import {
  buildPages,
  findByIx,
  missingRequired,
  pageOf,
  relocate,
  submitErrors,
} from './tree';

export interface Command {
  index: number;
  displayText: string;
  badgeText?: string | null;
  navigationState?: string | null;
}

export interface EntityList {
  type: 'entities';
  title?: string;
  headers?: string[];
  widthHints?: number[];
  entities?: Array<{ id: string; data: unknown[] }>;
  actions?: Array<{ text?: string }>;
  pageCount?: number;
  currentPage?: number;
  noItemsText?: string;
  hasDetails?: boolean;
  isMultiSelect?: boolean;
}

export interface Notice {
  message: string;
  error: boolean;
}

export type Screen =
  | { kind: 'loading'; label?: string }
  | {
      kind: 'menu';
      selections: string[];
      title: string;
      breadcrumbs: string[];
      commands: Command[];
      notice?: Notice | null;
    }
  | {
      kind: 'cases';
      selections: string[];
      title: string;
      breadcrumbs: string[];
      list: EntityList;
      search: string;
      notice?: Notice | null;
    }
  | {
      kind: 'detail';
      selections: string[];
      caseId: string;
      title: string;
      details: Array<{
        title?: string;
        headers?: string[];
        details?: unknown[];
      }>;
    }
  | { kind: 'form'; form: FormState }
  | {
      kind: 'unsupported';
      selections: string[];
      title: string;
      message: string;
    }
  | { kind: 'submitted'; message: string }
  | { kind: 'error'; message: string; code: string; retryable: boolean };

export const CASES_PER_PAGE = 10;

function noticeOf(resp: any): Notice | null {
  const n = resp && resp.notification;
  if (n && typeof n.message === 'string' && n.message.trim())
    return { message: n.message, error: !!n.error };
  return null;
}

/**
 * The screen a navigate_menu(_start) response is (beans: CommandListResponseBean type "commands",
 * EntityListResponse type "entities", QueryResponseBean type "query", NewFormResponse with a
 * session_id and tree).
 */
export function screenFromNavigation(
  resp: any,
  requested: string[],
  search = '',
): Screen {
  const selections: string[] = Array.isArray(resp?.selections)
    ? resp.selections.map(String)
    : requested;
  const title = resp?.title || (resp?.breadcrumbs || []).slice(-1)[0] || '';
  const breadcrumbs: string[] = Array.isArray(resp?.breadcrumbs)
    ? resp.breadcrumbs
    : [];
  if (resp && typeof resp.session_id === 'string' && Array.isArray(resp.tree)) {
    return { kind: 'form', form: newForm(resp, selections) };
  }
  if (resp?.type === 'commands') {
    return {
      kind: 'menu',
      selections,
      title,
      breadcrumbs,
      commands: resp.commands || [],
      notice: noticeOf(resp),
    };
  }
  if (resp?.type === 'entities') {
    return {
      kind: 'cases',
      selections,
      title,
      breadcrumbs,
      list: resp,
      search,
      notice: noticeOf(resp),
    };
  }
  if (resp?.type === 'query') {
    return {
      kind: 'unsupported',
      selections,
      title: title || 'Case search',
      message:
        "This step searches for cases on the server, which the web player doesn't support yet. Use the phone app for it.",
    };
  }
  const notice = noticeOf(resp);
  return {
    kind: 'error',
    message:
      notice?.message ||
      "The form engine sent a screen the web player doesn't know how to show.",
    code: 'UNKNOWN_SCREEN',
    retryable: false,
  };
}

// ---------------------------------------------------------------------------
// Inside a form
// ---------------------------------------------------------------------------

export interface FormState {
  sessionId: string;
  title: string;
  langs: string[];
  lang: string | null;
  tree: FormNode[];
  selections: string[];
  /** {ix: message} from Formplayer (constraint, required) or our own checks. */
  errors: Record<string, string>;
  /** The current page's key (see tree.buildPages); null at the start. */
  pageKey: string | null;
  /** True on the "ready to submit" screen after the last page. */
  review: boolean;
  mode: 'paged' | 'all';
  submitting: boolean;
}

export function newForm(
  resp: any,
  selections: string[],
  mode: 'paged' | 'all' = 'paged',
): FormState {
  const tree: FormNode[] = resp.tree || [];
  const pages = buildPages(tree);
  const langs: string[] = Array.isArray(resp.langs)
    ? resp.langs.filter((l: unknown) => typeof l === 'string')
    : [];
  return {
    sessionId: resp.session_id,
    title: resp.title || '',
    langs,
    lang: langs[0] || null,
    tree,
    selections,
    errors: {},
    pageKey: pages[0]?.key ?? null,
    review: pages.length === 0,
    mode,
    submitting: false,
  };
}

export type FormAction =
  | { type: 'answered'; ix?: string; resp: any }
  | { type: 'tree'; resp: any }
  | { type: 'localError'; ix: string; message: string | null }
  | { type: 'next' }
  | { type: 'back' }
  | { type: 'goto'; index: number }
  | { type: 'review' }
  | { type: 'mode'; mode: 'paged' | 'all' }
  | { type: 'lang'; lang: string; resp: any }
  | { type: 'submitting'; value: boolean }
  | {
      type: 'submitErrors';
      errors: Record<string, { type?: string; reason?: string }>;
    };

export function pagesOf(state: FormState): Page[] {
  return buildPages(state.tree);
}

export function currentIndex(state: FormState): number {
  return relocate(pagesOf(state), state.pageKey);
}

function withTree(state: FormState, resp: any): FormState {
  if (!resp || !Array.isArray(resp.tree)) return state;
  const tree = resp.tree as FormNode[];
  // Errors for questions that are gone (no longer relevant) go with them.
  const errors: Record<string, string> = {};
  for (const [ix, msg] of Object.entries(state.errors))
    if (findByIx(tree, ix)) errors[ix] = msg;
  const pages = buildPages(tree);
  const index = relocate(pages, state.pageKey);
  return { ...state, tree, errors, pageKey: pages[index]?.key ?? null };
}

export function formReducer(state: FormState, action: FormAction): FormState {
  switch (action.type) {
    case 'answered': {
      const resp = action.resp || {};
      if (resp.status === 'validation-error') {
        const message =
          resp.type === 'required'
            ? 'An answer is required.'
            : resp.reason || 'This answer is outside the allowed range.';
        return action.ix
          ? { ...state, errors: { ...state.errors, [action.ix]: message } }
          : state;
      }
      const next = withTree(state, resp);
      if (action.ix && next.errors[action.ix]) {
        const { [action.ix]: _gone, ...rest } = next.errors;
        return { ...next, errors: rest };
      }
      return next;
    }
    case 'tree':
      return withTree(state, action.resp);
    case 'localError': {
      const { [action.ix]: _old, ...rest } = state.errors;
      return {
        ...state,
        errors: action.message
          ? { ...rest, [action.ix]: action.message }
          : rest,
      };
    }
    case 'next': {
      const pages = pagesOf(state);
      const index = relocate(pages, state.pageKey);
      const missing = pages[index] ? missingRequired(pages[index].nodes) : {};
      if (Object.keys(missing).length)
        return { ...state, errors: { ...state.errors, ...missing } };
      if (index + 1 >= pages.length) return { ...state, review: true };
      return { ...state, pageKey: pages[index + 1].key, review: false };
    }
    case 'back': {
      if (state.review) return { ...state, review: false };
      const pages = pagesOf(state);
      const index = relocate(pages, state.pageKey);
      return index > 0 ? { ...state, pageKey: pages[index - 1].key } : state;
    }
    case 'goto': {
      const pages = pagesOf(state);
      const page = pages[Math.max(0, Math.min(action.index, pages.length - 1))];
      return page ? { ...state, pageKey: page.key, review: false } : state;
    }
    case 'review': {
      const missing = missingRequired(state.tree);
      if (Object.keys(missing).length) {
        const pages = pagesOf(state);
        const first = pageOf(pages, Object.keys(missing)[0]);
        return {
          ...state,
          errors: { ...state.errors, ...missing },
          review: false,
          pageKey: first >= 0 ? pages[first].key : state.pageKey,
        };
      }
      return { ...state, review: true };
    }
    case 'mode':
      return { ...state, mode: action.mode };
    case 'lang':
      return { ...withTree(state, action.resp), lang: action.lang };
    case 'submitting':
      return { ...state, submitting: action.value };
    case 'submitErrors': {
      const errors = { ...state.errors, ...submitErrors(action.errors) };
      const pages = pagesOf(state);
      const firstIx = Object.keys(submitErrors(action.errors))[0];
      const first = firstIx ? pageOf(pages, firstIx) : -1;
      return {
        ...state,
        errors,
        submitting: false,
        review: false,
        pageKey: first >= 0 ? pages[first].key : state.pageKey,
      };
    }
    default:
      return state;
  }
}

/** Progress through the form, 0..1, for the bar at the top. */
export function formProgress(state: FormState): number {
  const pages = pagesOf(state);
  if (!pages.length || state.review) return 1;
  return (currentIndex(state) + 1) / (pages.length + 1);
}

/** A tree's answer for an ix, with an optimistic value laid over it while a save is in flight. */
export function answerOf(tree: FormNode[], ix: string): Answer | undefined {
  return findByIx(tree, ix)?.answer;
}

/** The case list's columns worth showing: a header, and not hidden by a zero width hint. */
export function visibleColumns(list: EntityList): number[] {
  const headers = list.headers || [];
  const hints = list.widthHints || [];
  const out: number[] = [];
  headers.forEach((h, i) => {
    if (hints[i] === 0) return;
    if (!String(h ?? '').trim() && i > 0) return;
    out.push(i);
  });
  if (!out.length && (list.entities?.[0]?.data?.length ?? 0) > 0) out.push(0);
  return out;
}
