/**
 * The frame side of the page sandbox: runs inside the opaque-origin iframe
 * (workflow/sandbox_frame.html), compiles the page's render code exactly as the run
 * page does (../renderCode, via DynamicWorkflow), and hands it the same props --
 * with `actions` and `view` functions that are bridge requests to the labs page
 * around it. Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md.
 *
 * Nothing here is a security boundary: the frame's opaque origin, its CSP and the
 * host's checks are. This only makes render code that keeps to the page contract
 * work as it did: links navigate on a click, storage does not throw, the frame is
 * as tall as what it draws, and a crash is reported.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { DynamicWorkflow } from '../DynamicWorkflow';
import { createActionHandlers } from '../actionHandlers';
import { createLinkHelpers } from '../links';
import { buildRunView } from '../runView';
import type { WorkflowProps } from '../types';
import type { CompileOptions } from '../renderCode';
import {
  downloadBytes,
  installWorkflowShims,
  proxyLabsImages,
  sandboxGlobals,
  type AddressState,
} from './frameShims';
import {
  HELLO,
  INIT,
  STREAM,
  UPDATE,
  VIEWPORT,
  type BridgeResponse,
  type FramePayload,
  STORAGE_AREAS,
  type HostViewport,
  type StorageArea,
  type StorageBatch,
  type StorageSeed,
  type StreamMessage,
} from './protocol';
import { SANDBOX_GLOBAL, sandboxRewritePlugin } from './rewrite';
import { installViewport } from './viewport';

// ---------------------------------------------------------------------------
// Storage: an opaque origin has none, and reading localStorage throws there. The
// frame's copy answers render code at once (it is synchronous); the host keeps it
// for this page or workflow (host.ts scopedStorage) and hands it back in INIT.
// ---------------------------------------------------------------------------

export type StorageChange =
  | { op: 'set'; key: string; value: string }
  | { op: 'remove'; key: string }
  | { op: 'clear' };

export class MemoryStorage {
  private items = new Map<string, string>();
  /** Told of every change render code makes (not of `seed`). */
  onChange: ((change: StorageChange) => void) | null = null;
  get length() {
    return this.items.size;
  }
  key(i: number) {
    return Array.from(this.items.keys())[i] ?? null;
  }
  getItem(k: string) {
    return this.items.has(String(k)) ? this.items.get(String(k))! : null;
  }
  setItem(k: string, v: string) {
    const key = String(k);
    const value = String(v);
    this.items.set(key, value);
    this.onChange?.({ op: 'set', key, value });
  }
  removeItem(k: string) {
    const key = String(k);
    this.items.delete(key);
    this.onChange?.({ op: 'remove', key });
  }
  clear() {
    this.items.clear();
    this.onChange?.({ op: 'clear' });
  }
  /** What the host kept from an earlier load. */
  seed(items: Record<string, string> | undefined) {
    for (const [k, v] of Object.entries(items || {})) {
      if (typeof v === 'string') this.items.set(k, v);
    }
  }
}

export type InstalledStorage = Partial<Record<StorageArea, MemoryStorage>>;

/** Replace localStorage / sessionStorage with in-memory ones where they throw. */
export function installMemoryStorage(win: Window): InstalledStorage {
  const installed: InstalledStorage = {};
  for (const [area, name] of [
    ['local', 'localStorage'],
    ['session', 'sessionStorage'],
  ] as const) {
    let works = false;
    try {
      works = !!win[name];
    } catch {
      works = false;
    }
    if (works) continue;
    const storage = new MemoryStorage();
    try {
      Object.defineProperty(win, name, {
        value: storage,
        configurable: true,
      });
      installed[area] = storage;
    } catch {
      // Not replaceable here; render code that touches it will see the throw.
    }
  }
  return installed;
}

/**
 * Send storage changes to the host as batches (one `storage` request per area, at
 * most every `delayMs`), and `flush()` what is waiting at once: the frame calls it
 * before every other request, so `sessionStorage.setItem(...)` then a navigation
 * reaches the host in that order, as on the run page.
 */
export function createStoragePersister(
  send: (batch: StorageBatch) => void,
  delayMs = 50,
) {
  const empty = () => ({
    clear: false,
    set: new Map<string, string>(),
    remove: new Set<string>(),
  });
  const waiting: Record<StorageArea, ReturnType<typeof empty>> = {
    local: empty(),
    session: empty(),
  };
  let timer: ReturnType<typeof setTimeout> | null = null;
  function flush() {
    if (timer) clearTimeout(timer);
    timer = null;
    for (const area of STORAGE_AREAS) {
      const w = waiting[area];
      if (!w.clear && !w.set.size && !w.remove.size) continue;
      waiting[area] = empty();
      send({
        area,
        clear: w.clear,
        set: Object.fromEntries(w.set),
        remove: Array.from(w.remove),
      });
    }
  }
  function record(area: StorageArea, change: StorageChange) {
    const w = waiting[area];
    if (change.op === 'clear') {
      waiting[area] = { ...empty(), clear: true };
    } else if (change.op === 'set') {
      w.remove.delete(change.key);
      w.set.set(change.key, change.value);
    } else {
      w.set.delete(change.key);
      w.remove.add(change.key);
    }
    if (!timer) timer = setTimeout(flush, delayMs);
  }
  return { record, flush };
}

/** `bridge`, but storage changes still waiting are sent before each request. */
export function flushingStorageFirst(
  bridge: Bridge,
  persister: { flush: () => void },
): Bridge {
  return {
    ...bridge,
    request(method, params) {
      if (method !== 'storage') persister.flush();
      return bridge.request(method, params);
    },
  };
}

// ---------------------------------------------------------------------------
// Links: a click on <a href> becomes a navigate request the host checks.
// ---------------------------------------------------------------------------

/**
 * Turn clicks on links into `navigate(url, newTab)`. Capture phase, so the frame
 * never navigates itself (the host would take that as the page leaving its frame).
 * In-page `#fragment` links are left alone; `javascript:` links do nothing.
 */
export function interceptAnchorClicks(
  doc: Document,
  navigate: (url: string, newTab: boolean) => void,
  pageUrl = '',
  /** Workflow mode: a `<a download href="blob:...">` click; true when handled. */
  onDownload?: (anchor: HTMLAnchorElement) => boolean,
): () => void {
  function onClick(event: MouseEvent) {
    const target = event.target as Element | null;
    const anchor =
      target && typeof target.closest === 'function'
        ? (target.closest('a[href]') as HTMLAnchorElement | null)
        : null;
    if (!anchor) return;
    const raw = (anchor.getAttribute('href') || '').trim();
    if (!raw || raw.startsWith('#')) return;
    event.preventDefault();
    if (/^javascript:/i.test(raw)) return;
    if (onDownload && onDownload(anchor)) return;
    const newTab =
      anchor.getAttribute('target') === '_blank' ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.button === 1;
    // Relative links resolve against the HOST page's URL (passed in INIT), as they
    // would have before the page moved into a frame: against the frame's own URL they
    // would all point under the shell, where navigate refuses them.
    navigate(resolveHref(raw, pageUrl) || anchor.href || raw, newTab);
  }
  doc.addEventListener('click', onClick, true);
  doc.addEventListener('auxclick', onClick, true);
  return () => {
    doc.removeEventListener('click', onClick, true);
    doc.removeEventListener('auxclick', onClick, true);
  };
}

/** `raw` resolved against the host page's URL, or "" when either does not parse. */
export function resolveHref(raw: string, pageUrl: string): string {
  if (!pageUrl) return '';
  try {
    return new URL(raw, pageUrl).href;
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// The bridge client
// ---------------------------------------------------------------------------

export interface Bridge {
  request(method: string, params?: unknown): Promise<unknown>;
  /** Messages the host pushes for one stream (an EventSource it reads for us). */
  listen?(stream: string, onMessage: (m: StreamMessage) => void): () => void;
}

/**
 * Say hello to the parent until it answers with INIT and a port; from then on every
 * request rides the port and window messages are ignored.
 */
export function connectBridge(
  win: Window,
  handlers: {
    onInit: (
      payload: FramePayload,
      bridge: Bridge,
      viewport: HostViewport | null,
      storage: StorageSeed | null,
    ) => void;
    onUpdate: (payload: FramePayload) => void;
    /** The host's window changed (./viewport.ts). */
    onViewport?: (viewport: HostViewport) => void;
  },
  helloEveryMs = 200,
): () => void {
  let port: MessagePort | null = null;
  let nextId = 1;
  const pending = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  const listeners = new Map<string, (m: StreamMessage) => void>();
  // A stream's first messages can beat the answer that names it: held until listened.
  const early = new Map<string, StreamMessage[]>();
  const bridge: Bridge = {
    listen(stream, onMessage) {
      listeners.set(stream, onMessage);
      const held = early.get(stream) || [];
      early.delete(stream);
      held.forEach(onMessage);
      return () => {
        listeners.delete(stream);
      };
    },
    request(method, params) {
      if (!port) return Promise.reject(new Error('the page is not connected'));
      const id = nextId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        port!.postMessage({ id, method, params: params ?? {} });
      });
    },
  };

  function onPortMessage(event: MessageEvent) {
    const data = event.data as BridgeResponse & {
      type?: string;
      payload?: FramePayload;
      viewport?: HostViewport;
    };
    if (data && data.type === UPDATE && data.payload) {
      handlers.onUpdate(data.payload);
      return;
    }
    if (data && data.type === VIEWPORT) {
      if (isViewport(data.viewport)) handlers.onViewport?.(data.viewport);
      return;
    }
    if (data && data.type === STREAM) {
      const m = data as unknown as StreamMessage;
      if (typeof m.stream !== 'string') return;
      const to = listeners.get(m.stream);
      if (to) to(m);
      else if (early.size < 32) {
        const held = early.get(m.stream) || [];
        if (held.length < 64) held.push(m);
        early.set(m.stream, held);
      }
      return;
    }
    const waiting =
      data && typeof data.id === 'number' ? pending.get(data.id) : undefined;
    if (!waiting) return;
    pending.delete(data.id as number);
    if (data.error) waiting.reject(new Error(data.error));
    else waiting.resolve(data.result);
  }

  function onWindowMessage(event: MessageEvent) {
    if (port) return;
    if (event.source !== win.parent) return;
    const data = event.data as {
      type?: string;
      payload?: FramePayload;
      viewport?: unknown;
      storage?: unknown;
    };
    if (!data || data.type !== INIT || !data.payload || !event.ports?.[0])
      return;
    port = event.ports[0];
    port.onmessage = onPortMessage;
    clearInterval(timer);
    win.removeEventListener('message', onWindowMessage);
    handlers.onInit(
      data.payload,
      bridge,
      isViewport(data.viewport) ? data.viewport : null,
      isStorageSeed(data.storage) ? data.storage : null,
    );
  }

  win.addEventListener('message', onWindowMessage);
  const hello = () => win.parent.postMessage({ type: HELLO }, '*');
  const timer = setInterval(hello, helloEveryMs);
  hello();
  return () => {
    clearInterval(timer);
    win.removeEventListener('message', onWindowMessage);
  };
}

// ---------------------------------------------------------------------------
// The props render code receives
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

function isStorageSeed(v: unknown): v is StorageSeed {
  if (!v || typeof v !== 'object') return false;
  const o = v as Record<string, unknown>;
  return STORAGE_AREAS.every((a) => !!o[a] && typeof o[a] === 'object');
}

function isViewport(v: unknown): v is HostViewport {
  if (!v || typeof v !== 'object') return false;
  const o = v as Record<string, unknown>;
  return [
    'width',
    'height',
    'scrollX',
    'scrollY',
    'frameTop',
    'frameHeight',
    'insetTop',
    'gutterLeft',
    'gutterRight',
    'clipLeft',
    'clipRight',
  ].every((k) => typeof o[k] === 'number' && Number.isFinite(o[k] as number));
}

/** Growing this many times running, each within GROWTH_WINDOW_MS of the last, is
 * content sized by the frame's own height (a feedback loop), not a page drawing. */
export const RUNAWAY_GROWTHS = 60;
export const GROWTH_WINDOW_MS = 250;

/**
 * Report the content's height to the host whenever it changes: the frame is as tall
 * as what it draws, for pages and workflows alike. `minHeight` is the least it may
 * be (an open dialog's window, ./viewport.ts). Content that keeps growing because it
 * is sized by the frame's own height stops being followed, said once.
 */
export function reportHeight(
  root: HTMLElement,
  send: (height: number) => void,
  options: { minHeight?: () => number; now?: () => number } = {},
): { measure: () => void; disconnect: () => void } {
  const now = options.now || (() => Date.now());
  let last = -1;
  let growths = 0;
  let lastGrowthAt = 0;
  let capped: number | null = null;
  // Measured without ./viewport.ts's shift: only the height matters, and it is the
  // element's own.
  const rectOf = Element.prototype.getBoundingClientRect;
  const measure = () => {
    // Exactly as tall, to the hundredth (rounded up, so nothing is cut off): the
    // page around it is as long as it was with the render drawn in it.
    let h = Math.ceil(rectOf.call(root).height * 100) / 100;
    h = Math.max(h, Math.ceil(options.minHeight?.() || 0));
    if (capped !== null && h > capped) h = capped;
    if (h === last) return;
    if (h > last && last > 0) {
      const t = now();
      growths = t - lastGrowthAt <= GROWTH_WINDOW_MS ? growths + 1 : 1;
      lastGrowthAt = t;
      if (growths >= RUNAWAY_GROWTHS && capped === null) {
        capped = last;
        console.warn(
          `[sandbox] the content kept growing as the frame grew (${RUNAWAY_GROWTHS} times running); held at ${last}px`,
        );
        return;
      }
    } else {
      growths = 0;
    }
    last = h;
    send(h);
  };
  const observer = new ResizeObserver(measure);
  observer.observe(root);
  measure();
  return { measure, disconnect: () => observer.disconnect() };
}

// ---------------------------------------------------------------------------
// A workflow in the frame (phase 2)
// ---------------------------------------------------------------------------

/**
 * `actions` for a workflow: the run page's own implementations (../actionHandlers),
 * whose `fetch` here is the bridge's, plus the bridge methods the host answers
 * itself -- declared actions through its dialog, this workflow's pipeline rows,
 * supply and workflow sources, downloads and the clipboard.
 */
export function workflowActions(
  bridge: Bridge,
  pageUrl: () => string,
): WorkflowProps['actions'] {
  const base = createActionHandlers('');
  return {
    ...base,
    queryPipelineRows: async (
      alias: string,
      query: Record<string, unknown> = {},
    ) => {
      // onStatus and timeoutMs are the caller's; the host polls a warming cache.
      const { onStatus, timeoutMs, ...rest } = query as {
        onStatus?: (m: string) => void;
        timeoutMs?: number;
      } & Record<string, unknown>;
      void timeoutMs;
      if (typeof onStatus === 'function') onStatus('Loading...');
      return bridge.request('queryPipelineRows', {
        alias,
        query: rest,
      }) as ReturnType<
        NonNullable<WorkflowProps['actions']['queryPipelineRows']>
      >;
    },
    querySupply: (alias: string, query: { args?: unknown } = {}) =>
      bridge.request('querySupply', {
        alias,
        args: query?.args ?? {},
      }) as ReturnType<NonNullable<WorkflowProps['actions']['querySupply']>>,
    queryWorkflow: (alias: string) =>
      bridge.request('queryWorkflow', { alias }) as ReturnType<
        NonNullable<WorkflowProps['actions']['queryWorkflow']>
      >,
    download: (file: Record<string, unknown>) =>
      bridge.request('download', file).then(() => undefined),
    copyText: (text: string) =>
      bridge.request('copyText', { text }).then(() => undefined),
    runAction: (
      key: string,
      args: Record<string, unknown>,
      options?: { qaOnly?: boolean },
    ) =>
      bridge.request('runAction', {
        key,
        args,
        options: { qaOnly: Boolean(options?.qaOnly) },
      }) as ReturnType<NonNullable<WorkflowProps['actions']['runAction']>>,
    openTaskCreator: (params: Record<string, unknown>) => {
      const q = new URLSearchParams();
      for (const k of [
        'username',
        'title',
        'description',
        'coaching_prompt',
        'workflow_instance_id',
        'priority',
      ]) {
        if (params[k]) q.set(k, String(params[k]));
      }
      bridge
        .request('navigate', {
          url: `/tasks/new/?${q.toString()}`,
          newTab: true,
        })
        .catch((e) =>
          console.warn('[workflow] task creator refused:', e.message),
        );
    },
    // Code that used to set window.location: a link the host checks.
    navigate: (url: string, opts?: { newTab?: boolean }) =>
      bridge.request('navigate', {
        url: resolveHref(String(url), pageUrl()) || String(url),
        newTab: Boolean(opts?.newTab),
      }),
    reload: () => bridge.request('navigate', { url: pageUrl(), newTab: false }),
  } as unknown as WorkflowProps['actions'];
}

function WorkflowFramePage({
  payload,
  bridge,
  compileOptions,
}: {
  payload: FramePayload;
  bridge: Bridge;
  compileOptions?: CompileOptions;
}) {
  const pageUrl = useCallback(() => payload.pageUrl || '', [payload.pageUrl]);
  const actions = useMemo(
    () => workflowActions(bridge, pageUrl),
    [bridge, pageUrl],
  );
  // A page has no run: its state lives only in the frame, as in edit mode. A
  // workflow's state is the run's: the host writes it (or keeps it, in edit mode)
  // and sends it back as an UPDATE.
  const isPage = payload.mode !== 'workflow';
  const [pageState, setPageState] = useState<Record<string, unknown>>(
    (payload.view?.state || {}) as Record<string, unknown>,
  );
  const state = isPage
    ? pageState
    : ((payload.view?.state || {}) as Record<string, unknown>);
  const instance = payload.instance as unknown as WorkflowProps['instance'];
  const onUpdateState = useCallback(
    async (next: Record<string, unknown>) => {
      if (isPage) {
        setPageState((prev) => ({ ...prev, ...next }));
        return;
      }
      await bridge.request('updateState', { state: next });
    },
    [bridge, isPage],
  );
  const onError = useCallback(
    (message: string) => {
      bridge.request('reportError', { message }).catch(() => undefined);
    },
    [bridge],
  );
  const view = useMemo(
    () =>
      buildRunView({
        instance: { ...instance, state } as WorkflowProps['instance'],
        opportunityId: (payload.instance as { opportunity_id?: number })
          .opportunity_id,
        workers: payload.workers as WorkflowProps['workers'],
        pipelines: payload.pipelines as WorkflowProps['pipelines'],
        supply: payload.supply as Record<string, never>,
        state,
        flags: payload.flags as never,
        audits: payload.audits as never,
        tasks: payload.tasks as never,
        workflowActions: payload.workflowActions as never,
        csrfToken: '',
        complete: (opts?: { confirm?: string }) =>
          bridge
            .request('completeRun', { confirm: opts?.confirm || '' })
            .then(Boolean),
        confirm: (text: string) =>
          bridge.request('confirm', { text }).then(Boolean),
        shareSelection: (selection) => {
          bridge.request('shareSelection', selection).catch(() => undefined);
        },
      }),
    [bridge, payload, instance, state],
  );
  const pipelinesLoaded = Object.keys(payload.pipelines || {}).length > 0;
  return (
    <DynamicWorkflow
      // As on the run page: remount once pipelines land (see workflow-runner.tsx).
      key={pipelinesLoaded ? 'pipelines-loaded' : 'pipelines-empty'}
      renderCode={payload.renderCode}
      definition={payload.definition as unknown as WorkflowProps['definition']}
      instance={{ ...instance, state } as WorkflowProps['instance']}
      workers={payload.workers as WorkflowProps['workers']}
      pipelines={payload.pipelines as WorkflowProps['pipelines']}
      supply={payload.supply as WorkflowProps['supply']}
      workflows={payload.workflows as WorkflowProps['workflows']}
      scope={payload.scope as WorkflowProps['scope']}
      config={payload.config as WorkflowProps['config']}
      links={createLinkHelpers(payload.links)}
      actions={actions}
      onUpdateState={onUpdateState}
      view={view as unknown as WorkflowProps['view']}
      onError={onError}
      compileOptions={compileOptions}
    />
  );
}

export function startFrame(win: Window, rootEl: HTMLElement): void {
  const storages = installMemoryStorage(win);
  const reactRoot = createRoot(rootEl);
  let bridge: Bridge | null = null;
  let current: FramePayload | null = null;
  let compileOptions: CompileOptions | undefined;
  const draw = (payload: FramePayload) => {
    (win as unknown as { MAPBOX_TOKEN?: string }).MAPBOX_TOKEN =
      payload.mapboxToken || '';
    // One code path for pages and workflows: a page is a workflow with no run.
    (
      win as unknown as { WORKFLOW_API_ENDPOINTS?: unknown }
    ).WORKFLOW_API_ENDPOINTS = payload.apiEndpoints || {};
    reactRoot.render(
      <WorkflowFramePage
        payload={payload}
        bridge={bridge!}
        compileOptions={compileOptions}
      />,
    );
  };
  const report = (message: string) =>
    bridge?.request('reportError', { message }).catch(() => undefined);
  win.addEventListener('error', (e) => report(String(e.message || e)));
  win.addEventListener('unhandledrejection', (e) =>
    report(
      String(
        (e as PromiseRejectionEvent).reason?.message || 'A promise failed',
      ),
    ),
  );
  let viewport: ReturnType<typeof installViewport> | null = null;
  connectBridge(win, {
    onInit(payload, connected, hostViewport, storageSeed) {
      // What render code stored on an earlier load, before it runs; its changes go
      // to the host, ahead of any request made after them.
      const persister = createStoragePersister((batch) => {
        connected
          .request('storage', batch)
          .catch((e) => console.warn('[sandbox] storage not kept:', e.message));
      });
      for (const area of STORAGE_AREAS) {
        const storage = storages[area];
        if (!storage) continue;
        storage.seed(storageSeed?.[area]);
        storage.onChange = (change) => persister.record(area, change);
      }
      const b = flushingStorageFirst(connected, persister);
      bridge = b;
      current = payload;
      // The host's window, before anything is drawn: 100vh is its height from the
      // first frame (./viewport.ts).
      viewport = installViewport(win, rootEl, b);
      if (hostViewport) viewport.apply(hostViewport);
      const pageUrl = () => (current && current.pageUrl) || '';
      // The host page's query and hash, as the frame moves them (history, a hash).
      const address: AddressState = { search: '', hash: '' };
      try {
        const u = new URL(pageUrl());
        address.search = u.search;
        address.hash = u.hash;
      } catch {
        // no page URL: an empty address
      }
      // Render code is compiled with the sandbox's rewrites (./rewrite.ts): its
      // `window.location` and awaitable `confirm()` reach these instead.
      compileOptions = {
        plugins: [sandboxRewritePlugin],
        params: { [SANDBOX_GLOBAL]: sandboxGlobals(b, pageUrl, address) },
      };
      let onDownload: ((a: HTMLAnchorElement) => boolean) | undefined;
      {
        // The shared map module asks this for admin boundaries (a labs path, so a
        // bridge fetch the host checks).
        (
          win as unknown as { BOUNDARY_VIEWPORT_URL?: string }
        ).BOUNDARY_VIEWPORT_URL = '/microplans/boundaries/viewport/';
        // Render code reads opportunity names off the run page's #user-opportunities.
        const names = win.document.createElement('script');
        names.type = 'application/json';
        names.id = 'user-opportunities';
        names.textContent = JSON.stringify(payload.userOpportunities || []);
        win.document.body.appendChild(names);
        const { sandboxFetch, blobs } = installWorkflowShims(
          win as Window & typeof globalThis,
          b,
          pageUrl,
          address,
        );
        proxyLabsImages(win.document, sandboxFetch, () => win.location.href);
        onDownload = (anchor) => {
          const href = anchor.getAttribute('href') || '';
          if (!anchor.hasAttribute('download')) return false;
          if (!href.startsWith('blob:') && !href.startsWith('data:'))
            return false;
          const filename = anchor.getAttribute('download') || 'download.csv';
          downloadBytes(href, blobs).then((bytes) => {
            if (!bytes) return;
            b.request('download', { filename, bytes }).catch((e) =>
              console.warn('[workflow] download refused:', e.message),
            );
          });
          return true;
        };
        // `a.click()` on a link never added to the document reaches no listener:
        // route it the same way.
        const proto = (win as Window & typeof globalThis).HTMLAnchorElement
          .prototype;
        const nativeClick = proto.click;
        proto.click = function (this: HTMLAnchorElement) {
          if (this.isConnected) return nativeClick.call(this);
          const raw = (this.getAttribute('href') || '').trim();
          if (!raw || raw.startsWith('#') || /^javascript:/i.test(raw)) return;
          if (onDownload && onDownload(this)) return;
          b.request('navigate', {
            url: resolveHref(raw, pageUrl()) || this.href || raw,
            newTab: this.getAttribute('target') === '_blank',
          }).catch((e) => console.warn('[workflow] link refused:', e.message));
        };
      }
      interceptAnchorClicks(
        win.document,
        (url, newTab) => {
          b.request('navigate', { url, newTab }).catch((err) =>
            console.warn('[page] link refused:', err.message),
          );
        },
        typeof payload.pageUrl === 'string' ? payload.pageUrl : '',
        onDownload,
      );
      const heights = reportHeight(
        rootEl,
        (height) => {
          b.request('resize', { height }).catch(() => undefined);
        },
        { minHeight: () => viewport?.minHeight() || 0 },
      );
      viewport.onMinHeightChange(heights.measure);
      draw(payload);
    },
    onViewport(vp) {
      viewport?.apply(vp);
    },
    // UPDATE carries only what changed.
    onUpdate(partial) {
      current = { ...(current as FramePayload), ...partial };
      draw(current);
    },
  });
}
