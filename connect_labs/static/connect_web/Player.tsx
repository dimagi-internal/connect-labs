/**
 * Connect Web's form player: a CommCare app (the opportunity's learn or deliver app) run in the
 * browser on Formplayer, through labs (api.ts). Menus → case list → case details → form →
 * submit, like the phone.
 *
 * Layout choice: **one screen per question, as on the phone**, with a field-list group as one
 * screen (that is how an app author asks for "these together"), a sticky Back / Next bar, and a
 * final "ready to submit" screen. "Show all questions" switches to one scrolling page for anyone
 * who prefers it (remembered on this device). See connect_web/README.md.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';

import { ApiError, EngineClient, preparePhoto } from './api';
import type { PlayerConfig } from './api';
import { storeBaseline } from './progress';
import { Caption, NodeView, RepeatView } from './Questions';
import type { QuestionHandlers } from './Questions';
import {
  CaseDetailScreen,
  CaseListScreen,
  MenuScreen,
  Notification,
  Skeleton,
  StickyBar,
} from './Screens';
import type { FormAction, FormState, Screen } from './state';
import {
  CASES_PER_PAGE,
  currentIndex,
  formProgress,
  formReducer,
  pagesOf,
  screenFromNavigation,
} from './state';
import {
  UNSUPPORTED_WIDGETS,
  collectAnswers,
  isRequired,
  metaLocation,
  missingRequired,
  questions,
  widgetFor,
} from './tree';
import type { Answer, Page } from './tree';

const MODE_KEY = 'cw-player-mode';

function useOnline() {
  const [online, setOnline] = useState(
    typeof navigator === 'undefined' ? true : navigator.onLine !== false,
  );
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);
  return online;
}

function useFocusOnChange<T extends HTMLElement>(key: unknown) {
  const ref = useRef<T>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    ref.current?.focus({ preventScroll: false });
    window.scrollTo({ top: 0 });
  }, [key]);
  return ref;
}

function errorScreen(e: unknown): Screen {
  if (e instanceof ApiError)
    return {
      kind: 'error',
      message: e.message,
      code: e.code,
      retryable: e.retryable || e.isNetwork,
    };
  return {
    kind: 'error',
    message: 'Something went wrong. Please try again.',
    code: 'CLIENT',
    retryable: true,
  };
}

export function Player({
  config,
  client: injected,
}: {
  config: PlayerConfig;
  client?: EngineClient;
}) {
  const client = useMemo(
    () => injected || new EngineClient(config),
    [injected, config],
  );
  const [screen, setScreen] = useState<Screen>({
    kind: 'loading',
    label: 'Opening the app…',
  });
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string>('');
  const history = useRef<
    Array<{ selections: string[]; extra?: Record<string, unknown> }>
  >([]);
  const lastRequest = useRef<() => Promise<void>>(async () => undefined);
  const online = useOnline();
  const headingRef = useFocusOnChange<HTMLHeadingElement>(screen);

  const go = useCallback(
    async (
      selections: string[],
      extra: Record<string, unknown> = {},
      { push = true, search = '' } = {},
    ) => {
      const run = async () => {
        setBusy(true);
        try {
          const resp = await client.navigate(selections, extra);
          const next = screenFromNavigation(resp, selections, search);
          if (push && next.kind !== 'error') {
            const sel =
              'selections' in next
                ? next.selections
                : next.kind === 'form'
                  ? next.form.selections
                  : selections;
            const top = history.current[history.current.length - 1];
            if (!top || top.selections.join('/') !== sel.join('/'))
              history.current.push({ selections: sel, extra });
            else
              history.current[history.current.length - 1] = {
                selections: sel,
                extra,
              };
          }
          setScreen(next);
        } catch (e) {
          setScreen(errorScreen(e));
        } finally {
          setBusy(false);
        }
      };
      lastRequest.current = run;
      await run();
    },
    [client],
  );

  useEffect(() => {
    void go([]);
  }, [go]);

  const back = useCallback(() => {
    history.current.pop();
    const prev = history.current[history.current.length - 1];
    if (!prev) {
      window.location.href = config.backUrl;
      return;
    }
    void go(prev.selections, {}, { push: false });
  }, [go, config.backUrl]);

  const sync = async () => {
    setBusy(true);
    try {
      await client.sync();
      setToast('Synced with CommCare HQ.');
      const top = history.current[history.current.length - 1];
      await go(top ? top.selections : [], {}, { push: false });
    } catch (e) {
      setToast(e instanceof ApiError ? e.message : 'Sync failed.');
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(''), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const title =
    screen.kind === 'form'
      ? screen.form.title
      : 'title' in screen && screen.title
        ? screen.title
        : screen.kind === 'submitted'
          ? 'Submitted'
          : config.appName;
  const crumbs = 'breadcrumbs' in screen ? screen.breadcrumbs.slice(0, -1) : [];

  return (
    <div className="pb-4" data-testid="cw-player" data-screen={screen.kind}>
      {!online && (
        <div
          role="status"
          className="mb-3 flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          data-testid="cw-offline"
        >
          <i className="fa-solid fa-wifi" aria-hidden="true" /> You're offline.
          Answers can't be saved until the connection is back.
        </div>
      )}
      {screen.kind !== 'form' && (
        <header className="mb-3 flex items-start gap-2">
          {screen.kind !== 'loading' &&
            screen.kind !== 'submitted' &&
            history.current.length > 1 && (
              <button
                type="button"
                onClick={back}
                disabled={busy}
                aria-label="Back"
                className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-700 hover:bg-slate-200"
              >
                <i className="fa-solid fa-arrow-left" aria-hidden="true" />
              </button>
            )}
          <div className="min-w-0 flex-1">
            {crumbs.length > 0 && (
              <nav
                aria-label="Where you are"
                className="truncate text-xs text-slate-500"
              >
                {crumbs.join(' › ')}
              </nav>
            )}
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="text-xl font-bold leading-snug focus:outline-none"
            >
              {title}
            </h1>
            <p className="text-sm text-slate-500">{config.opportunityName}</p>
          </div>
          {(screen.kind === 'menu' || screen.kind === 'cases') && (
            <button
              type="button"
              onClick={() => void sync()}
              disabled={busy}
              className="inline-flex min-h-11 shrink-0 items-center gap-1 rounded-lg px-3 text-sm font-medium text-indigo-800 hover:bg-indigo-50"
              aria-label="Sync with CommCare HQ"
            >
              <i
                className={`fa-solid fa-rotate ${busy ? 'fa-spin' : ''}`}
                aria-hidden="true"
              />{' '}
              <span className="hidden sm:inline">Sync</span>
            </button>
          )}
        </header>
      )}

      {toast && (
        <div
          role="status"
          className="mb-3 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900"
        >
          {toast}
        </div>
      )}

      {screen.kind === 'loading' && <Skeleton label={screen.label} />}

      {screen.kind === 'error' && (
        <div
          className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900"
          role="alert"
          data-testid="cw-player-error"
        >
          <p className="font-semibold">
            <i
              className="fa-solid fa-triangle-exclamation mr-1"
              aria-hidden="true"
            />
            {screen.code === 'ENGINE_NOT_CONFIGURED'
              ? "The form engine isn't configured"
              : "That didn't work"}
          </p>
          <p className="mt-1">{screen.message}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {screen.retryable && (
              <button
                type="button"
                onClick={() => void lastRequest.current()}
                className="min-h-11 rounded-xl bg-red-700 px-4 font-semibold text-white"
              >
                Try again
              </button>
            )}
            {history.current.length > 1 && (
              <button
                type="button"
                onClick={back}
                className="min-h-11 rounded-xl border border-red-300 bg-white px-4 font-semibold text-red-800"
              >
                Go back
              </button>
            )}
            <a
              href={config.backUrl}
              className="inline-flex min-h-11 items-center rounded-xl border border-red-300 bg-white px-4 font-semibold text-red-800"
            >
              Back to the opportunity
            </a>
          </div>
        </div>
      )}

      {screen.kind === 'menu' && (
        <>
          <Notification notice={screen.notice} />
          <MenuScreen
            commands={screen.commands}
            busy={busy}
            onSelect={(i) => void go([...screen.selections, String(i)])}
          />
        </>
      )}

      {screen.kind === 'cases' && (
        <>
          <Notification notice={screen.notice} />
          <CaseListScreen
            list={screen.list}
            search={screen.search}
            busy={busy}
            onSearch={(text) =>
              void go(
                screen.selections,
                {
                  search_text: text || undefined,
                  offset: 0,
                  cases_per_page: CASES_PER_PAGE,
                },
                { push: false, search: text },
              )
            }
            onPage={(page) =>
              void go(
                screen.selections,
                {
                  search_text: screen.search || undefined,
                  offset: page * CASES_PER_PAGE,
                  cases_per_page: CASES_PER_PAGE,
                },
                { push: false, search: screen.search },
              )
            }
            onAction={(i) => void go([...screen.selections, `action ${i}`])}
            onOpen={async (caseId) => {
              if (screen.list.hasDetails === false) {
                void go([...screen.selections, caseId]);
                return;
              }
              setBusy(true);
              try {
                const resp = await client.details([
                  ...screen.selections,
                  caseId,
                ]);
                const details = Array.isArray(resp?.details)
                  ? resp.details
                  : Array.isArray(resp?.entityDetailList)
                    ? resp.entityDetailList
                    : [];
                history.current.push({
                  selections: [...screen.selections, caseId],
                });
                setScreen({
                  kind: 'detail',
                  selections: screen.selections,
                  caseId,
                  title: screen.title,
                  details,
                });
              } catch (e) {
                setScreen(errorScreen(e));
              } finally {
                setBusy(false);
              }
            }}
          />
        </>
      )}

      {screen.kind === 'detail' && (
        <CaseDetailScreen
          details={screen.details}
          busy={busy}
          onContinue={() => {
            history.current.pop();
            void go([...screen.selections, screen.caseId]);
          }}
        />
      )}

      {screen.kind === 'unsupported' && (
        <div
          className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
          data-testid="cw-unsupported-screen"
        >
          {screen.message}
        </div>
      )}

      {screen.kind === 'form' && (
        <FormScreen
          key={screen.form.sessionId}
          initial={screen.form}
          client={client}
          config={config}
          onExit={back}
          onRestart={() => void go(screen.form.selections, {}, { push: false })}
          onSubmitted={(message) => {
            setScreen({ kind: 'submitted', message });
          }}
        />
      )}

      {screen.kind === 'submitted' && (
        <Submitted message={screen.message} config={config} />
      )}
    </div>
  );
}

function Submitted({
  message,
  config,
}: {
  message: string;
  config: PlayerConfig;
}) {
  const [seconds, setSeconds] = useState(4);
  useEffect(() => {
    if (seconds <= 0) {
      window.location.href = config.doneUrl;
      return;
    }
    const t = setTimeout(() => setSeconds((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [seconds, config.doneUrl]);
  return (
    <div
      className="rounded-2xl bg-white p-6 text-center shadow-sm"
      role="status"
      data-testid="cw-submitted-screen"
    >
      <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-green-100 text-green-700">
        <i className="fa-solid fa-check text-2xl" aria-hidden="true" />
      </div>
      <p className="text-lg font-semibold">Form submitted</p>
      {message && <p className="mt-2 text-sm text-slate-600">{message}</p>}
      <p className="mt-2 text-xs text-slate-500">
        CommCare HQ has it. Connect will pick it up shortly.
      </p>
      <a
        href={config.doneUrl}
        className="mt-4 inline-flex min-h-12 w-full items-center justify-center rounded-xl bg-indigo-700 px-4 font-semibold text-white"
      >
        Back to the opportunity{seconds > 0 ? ` (${seconds})` : ''}
      </a>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

type GpsPrompt = null | { reason: string };

function locate(): Promise<GeolocationPosition> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject({ code: 0, message: "This browser can't share its location." });
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 20000,
      maximumAge: 60000,
    });
  });
}

function FormScreen({
  initial,
  client,
  config,
  onExit,
  onRestart,
  onSubmitted,
}: {
  initial: FormState;
  client: EngineClient;
  config: PlayerConfig;
  onExit: () => void;
  onRestart: () => void;
  onSubmitted: (message: string) => void;
}) {
  const [state, rawDispatch] = useReducer(formReducer, initial, (s) => ({
    ...s,
    mode: (typeof localStorage !== 'undefined' &&
    localStorage.getItem(MODE_KEY) === 'all'
      ? 'all'
      : 'paged') as 'paged' | 'all',
  }));
  // The reducer is pure, so the latest state can be known synchronously (after awaiting saves,
  // before React re-renders): Next and Submit read it from here.
  const latest = useRef(state);
  latest.current = state;
  const dispatch = useCallback((action: FormAction) => {
    latest.current = formReducer(latest.current, action);
    rawDispatch(action);
  }, []);
  const [saving, setSaving] = useState(0);
  const [banner, setBanner] = useState<null | {
    message: string;
    retry?: () => void;
    sessionGone?: boolean;
  }>(null);
  const [gps, setGps] = useState<GpsPrompt>(null);
  const [submitError, setSubmitError] = useState<string>('');
  const flushers = useRef(new Map<string, () => Promise<void>>());
  const previews = useRef(new Map<string, string>());
  const [, forcePreview] = useState(0);
  const dirty = useRef(false);

  const pages = pagesOf(state);
  const index = currentIndex(state);
  const page: Page | undefined = pages[index];
  const headingRef = useFocusOnChange<HTMLHeadingElement>(
    `${state.pageKey}|${state.review}`,
  );

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (!dirty.current) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);

  useEffect(
    () => () => {
      previews.current.forEach((url) => URL.revokeObjectURL(url));
    },
    [],
  );

  const failed = useCallback((e: unknown, retry?: () => void) => {
    if (e instanceof ApiError && e.isSessionGone) {
      setBanner({ message: e.message, sessionGone: true });
    } else if (e instanceof ApiError) {
      setBanner({
        message: e.message,
        retry: e.retryable || e.isNetwork ? retry : undefined,
      });
    } else {
      setBanner({ message: "Your answer couldn't be saved.", retry });
    }
  }, []);

  const run = useCallback(
    async (op: () => Promise<any>, onOk: (resp: any) => void) => {
      setSaving((n) => n + 1);
      try {
        const resp = await op();
        setBanner(null);
        onOk(resp);
      } catch (e) {
        failed(e, () => void run(op, onOk));
      } finally {
        setSaving((n) => n - 1);
      }
    },
    [failed],
  );

  const handlers: QuestionHandlers = useMemo(
    () => ({
      answer: (ix: string, value: Answer) => {
        dirty.current = true;
        return run(
          () => client.answer(state.sessionId, ix, value),
          (resp) => dispatch({ type: 'answered', ix, resp }),
        );
      },
      photo: async (ix: string, file: File) => {
        dirty.current = true;
        let prepared;
        try {
          prepared = await preparePhoto(file, config.mediaMaxBytes);
        } catch (e) {
          dispatch({
            type: 'localError',
            ix,
            message:
              e instanceof ApiError
                ? e.message
                : "That photo couldn't be read.",
          });
          return;
        }
        await run(
          () =>
            client.answerMedia(
              state.sessionId,
              ix,
              prepared.blob,
              prepared.name,
            ),
          (resp) => {
            const old = previews.current.get(ix);
            if (old) URL.revokeObjectURL(old);
            previews.current.set(ix, URL.createObjectURL(prepared.blob));
            forcePreview((n) => n + 1);
            dispatch({ type: 'answered', ix, resp });
          },
        );
      },
      newRepeat: (ix: string) =>
        void run(
          () => client.newRepeat(state.sessionId, ix),
          (resp) => dispatch({ type: 'tree', resp }),
        ),
      deleteRepeat: (ix: string) =>
        void run(
          () => client.deleteRepeat(state.sessionId, ix),
          (resp) => dispatch({ type: 'tree', resp }),
        ),
      registerFlush: (ix, flush) => {
        if (flush) flushers.current.set(ix, flush);
        else flushers.current.delete(ix);
      },
      localError: (ix, message) =>
        dispatch({ type: 'localError', ix, message }),
      photoPreview: (ix) => previews.current.get(ix) || null,
      busy: saving > 0,
    }),
    [client, state.sessionId, run, config.mediaMaxBytes, saving],
  );

  const flushAll = async () => {
    await Promise.all([...flushers.current.values()].map((f) => f()));
  };

  const next = async () => {
    await flushAll();
    dispatch({ type: 'next' });
  };

  const changeLang = (lang: string) =>
    void run(
      () => client.changeLocale(state.sessionId, lang),
      (resp) => dispatch({ type: 'lang', lang, resp }),
    );

  const setMode = (mode: 'paged' | 'all') => {
    try {
      localStorage.setItem(MODE_KEY, mode);
    } catch {
      /* private mode */
    }
    dispatch({ type: 'mode', mode });
  };

  const blocking = questions(state.tree).filter(
    (q) => isRequired(q) && UNSUPPORTED_WIDGETS.includes(widgetFor(q)),
  );

  const submit = async (location: string | null | undefined) => {
    setSubmitError('');
    dispatch({ type: 'submitting', value: true });
    try {
      try {
        storeBaseline(config.opportunityId, await client.progress());
      } catch {
        /* the opportunity page falls back to its own numbers */
      }
      const resp = await client.submit(
        state.sessionId,
        collectAnswers(latest.current.tree),
        location || null,
      );
      if (resp.status === 'success') {
        dirty.current = false;
        onSubmitted(resp.submitResponseMessage || '');
        return;
      }
      if (resp.status === 'validation-error') {
        dispatch({ type: 'submitErrors', errors: resp.errors || {} });
        setSubmitError(
          'Some answers need attention before this form can be submitted.',
        );
        return;
      }
      dispatch({ type: 'submitting', value: false });
      if (resp.status === 'too-many-requests') {
        setSubmitError(
          'CommCare HQ is busy right now. Keep this page open and try again in a minute.',
        );
      } else if (resp.status === 'case-cycle-error') {
        setSubmitError(
          "This form would create a loop between cases, so CommCare HQ can't accept it. The app needs fixing.",
        );
      } else {
        const message =
          resp.notification?.message || 'CommCare HQ did not accept the form.';
        setSubmitError(`${message} Nothing was saved, so you can try again.`);
      }
    } catch (e) {
      dispatch({ type: 'submitting', value: false });
      if (e instanceof ApiError && e.isSessionGone)
        setBanner({ message: e.message, sessionGone: true });
      else
        setSubmitError(
          e instanceof ApiError
            ? e.message
            : 'The form could not be submitted. Try again.',
        );
    }
  };

  const startSubmit = async () => {
    await flushAll();
    // `review` refuses (and jumps to the first gap) if a required question is unanswered.
    dispatch({ type: 'review' });
    if (Object.keys(missingRequired(latest.current.tree)).length) return;
    try {
      const pos = await locate();
      await submit(metaLocation(pos.coords));
    } catch (err: any) {
      if (err instanceof ApiError) throw err;
      const reason =
        err && err.code === 1
          ? 'Location permission was refused.'
          : err && err.code === 3
            ? 'Finding the location took too long.'
            : "The location isn't available.";
      if (config.appKind === 'learn') {
        // A learn module doesn't need a location; don't make the worker deal with it.
        await submit(null);
      } else {
        setGps({ reason });
      }
    }
  };

  const nodes: React.ReactNode =
    state.mode === 'all' ? (
      <div className="space-y-6">
        {pages.map((p) => (
          <PageBody
            key={p.key}
            page={p}
            state={state}
            handlers={handlers}
            showContext
          />
        ))}
      </div>
    ) : page && !state.review ? (
      <PageBody page={page} state={state} handlers={handlers} />
    ) : null;

  const progress = Math.round(formProgress(state) * 100);
  const errorCount = Object.keys(state.errors).length;

  return (
    <div data-testid="cw-form" data-mode={state.mode}>
      <header className="mb-3">
        <div className="flex items-start gap-2">
          <button
            type="button"
            onClick={() => {
              if (
                !dirty.current ||
                window.confirm(
                  'Leave this form? Your answers so far will not be submitted.',
                )
              )
                onExit();
            }}
            aria-label="Leave the form"
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-700 hover:bg-slate-200"
          >
            <i className="fa-solid fa-xmark" aria-hidden="true" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-bold leading-snug">
              {state.title || config.appName}
            </h1>
            <p className="text-xs text-slate-500" aria-live="polite">
              {saving > 0 ? (
                <>
                  <i
                    className="fa-solid fa-circle-notch fa-spin mr-1"
                    aria-hidden="true"
                  />
                  Saving…
                </>
              ) : state.mode === 'paged' && !state.review && pages.length ? (
                `Question ${index + 1} of ${pages.length}`
              ) : (
                config.opportunityName
              )}
            </p>
          </div>
          {state.langs.length > 1 && (
            <label className="shrink-0">
              <span className="sr-only">Language</span>
              <select
                value={state.lang || ''}
                onChange={(e) => changeLang(e.target.value)}
                className="min-h-11 rounded-lg border border-slate-300 bg-white px-2 text-sm"
              >
                {state.langs.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {state.mode === 'paged' && (
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
            aria-label="Progress through the form"
          >
            <div
              className="h-full rounded-full bg-indigo-600 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
        <div className="mt-1 flex justify-end">
          <button
            type="button"
            onClick={() => setMode(state.mode === 'all' ? 'paged' : 'all')}
            className="min-h-11 px-2 text-sm font-medium text-indigo-800 underline"
          >
            {state.mode === 'all'
              ? 'One question at a time'
              : 'Show all questions'}
          </button>
        </div>
      </header>

      {banner && (
        <div
          role="alert"
          className="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-900"
          data-testid="cw-form-banner"
        >
          <p>{banner.message}</p>
          <div className="mt-2 flex gap-2">
            {banner.retry && (
              <button
                type="button"
                onClick={banner.retry}
                className="min-h-11 rounded-lg bg-red-700 px-3 font-semibold text-white"
              >
                Try again
              </button>
            )}
            {banner.sessionGone && (
              <button
                type="button"
                onClick={onRestart}
                className="min-h-11 rounded-lg bg-red-700 px-3 font-semibold text-white"
              >
                Start the form again
              </button>
            )}
          </div>
        </div>
      )}

      {blocking.length > 0 && (
        <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          This form has a required question the web can't capture yet (
          {blocking.map((q) => q.caption).join(', ')}), so it can't be submitted
          here.
        </div>
      )}

      <h2 ref={headingRef} tabIndex={-1} className="sr-only">
        {state.review
          ? 'Ready to submit'
          : page
            ? `Question ${index + 1} of ${pages.length}`
            : 'Form'}
      </h2>

      {state.mode === 'paged' && state.review ? (
        <Review
          state={state}
          pages={pages}
          onGoto={(i) => dispatch({ type: 'goto', index: i })}
        />
      ) : (
        nodes
      )}
      {state.mode === 'all' && (
        <Review state={state} pages={pages} onGoto={() => undefined} compact />
      )}

      {submitError && (
        <div
          role="alert"
          className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-900"
          data-testid="cw-submit-error"
        >
          {submitError}
        </div>
      )}

      {gps && (
        <div
          role="alertdialog"
          aria-labelledby="cw-gps-title"
          className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"
          data-testid="cw-gps-prompt"
        >
          <p id="cw-gps-title" className="font-semibold">
            <i className="fa-solid fa-location-dot mr-1" aria-hidden="true" />{' '}
            No location for this visit
          </p>
          <p className="mt-1">
            {gps.reason} Without it, the opportunity may flag this visit as{' '}
            <strong>“GPS missing”</strong>. Allow location for this site and try
            again, or submit without it.
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              className="min-h-12 rounded-xl bg-indigo-700 px-4 font-semibold text-white"
              onClick={() => {
                setGps(null);
                void startSubmit();
              }}
            >
              Try again
            </button>
            <button
              type="button"
              className="min-h-12 rounded-xl border border-amber-400 bg-white px-4 font-semibold text-amber-900"
              onClick={() => {
                setGps(null);
                void submit(null);
              }}
            >
              Submit without location
            </button>
          </div>
        </div>
      )}

      <StickyBar>
        {state.mode === 'paged' && (
          <button
            type="button"
            onClick={() => dispatch({ type: 'back' })}
            disabled={(index === 0 && !state.review) || state.submitting}
            className="min-h-12 flex-1 rounded-xl border border-slate-300 bg-white px-4 font-semibold text-slate-800 disabled:opacity-40"
          >
            <i className="fa-solid fa-arrow-left mr-1" aria-hidden="true" />{' '}
            Back
          </button>
        )}
        {state.mode === 'paged' && !state.review ? (
          <button
            type="button"
            onClick={() => void next()}
            disabled={state.submitting}
            className="min-h-12 flex-[2] rounded-xl bg-indigo-700 px-4 font-semibold text-white disabled:opacity-60"
            data-testid="cw-next"
          >
            {index + 1 >= pages.length ? 'Finish' : 'Next'}{' '}
            <i className="fa-solid fa-arrow-right ml-1" aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void startSubmit()}
            disabled={state.submitting || blocking.length > 0 || saving > 0}
            className="min-h-12 flex-[2] rounded-xl bg-green-700 px-4 font-semibold text-white disabled:opacity-60"
            data-testid="cw-submit"
          >
            {state.submitting ? (
              <>
                <i
                  className="fa-solid fa-circle-notch fa-spin mr-1"
                  aria-hidden="true"
                />{' '}
                Submitting…
              </>
            ) : (
              <>
                <i
                  className="fa-solid fa-paper-plane mr-1"
                  aria-hidden="true"
                />{' '}
                Submit{errorCount ? ` (${errorCount} to fix)` : ''}
              </>
            )}
          </button>
        )}
      </StickyBar>
    </div>
  );
}

function PageBody({
  page,
  state,
  handlers,
  showContext = false,
}: {
  page: Page;
  state: FormState;
  handlers: QuestionHandlers;
  showContext?: boolean;
}) {
  return (
    <div className="space-y-3" data-page={page.key}>
      {page.context.length > 0 && (showContext || state.mode === 'paged') && (
        <p className="px-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          {page.context.join(' › ')}
        </p>
      )}
      {page.kind === 'repeat' ? (
        <RepeatView
          nodes={page.nodes}
          errors={state.errors}
          handlers={handlers}
        />
      ) : page.kind === 'group' ? (
        page.nodes.map((g) => (
          <section key={g.ix} className="space-y-3">
            {g.caption && (
              <Caption
                node={g}
                as="h3"
                className="px-1 text-base font-semibold text-slate-800"
              />
            )}
            {(g.children || []).map((child) => (
              <NodeView
                key={child.ix}
                node={child}
                errors={state.errors}
                handlers={handlers}
              />
            ))}
          </section>
        ))
      ) : (
        page.nodes.map((n) => (
          <NodeView
            key={n.ix}
            node={n}
            errors={state.errors}
            handlers={handlers}
          />
        ))
      )}
    </div>
  );
}

function Review({
  state,
  pages,
  onGoto,
  compact = false,
}: {
  state: FormState;
  pages: Page[];
  onGoto: (i: number) => void;
  compact?: boolean;
}) {
  const errorIx = Object.keys(state.errors);
  const withErrors = pages
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => questions(p.nodes).some((q) => errorIx.includes(q.ix)));
  if (compact) {
    return errorIx.length ? (
      <p className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">
        {errorIx.length} answer{errorIx.length > 1 ? 's need' : ' needs'}{' '}
        attention above.
      </p>
    ) : null;
  }
  return (
    <div className="rounded-2xl bg-white p-5 shadow-sm" data-testid="cw-review">
      <p className="text-lg font-semibold">
        You've reached the end of the form
      </p>
      <p className="mt-1 text-sm text-slate-600">
        Submitting sends it to CommCare HQ with this device's location, as the
        phone app does. You can go back to check your answers first.
      </p>
      {withErrors.length > 0 && (
        <ul className="mt-3 space-y-2">
          {withErrors.map(({ p, i }) => (
            <li key={p.key}>
              <button
                type="button"
                onClick={() => onGoto(i)}
                className="flex min-h-11 w-full items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 text-left text-sm text-red-900"
              >
                <i
                  className="fa-solid fa-circle-exclamation"
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate">
                  {questions(p.nodes).find((q) => errorIx.includes(q.ix))
                    ?.caption || `Question ${i + 1}`}
                </span>
                <span className="font-semibold underline">Fix</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
