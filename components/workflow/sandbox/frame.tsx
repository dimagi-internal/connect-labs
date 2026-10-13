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
import {
  downloadBytes,
  installWorkflowShims,
  proxyLabsImages,
} from './frameShims';
import {
  HELLO,
  INIT,
  UPDATE,
  type BridgeResponse,
  type FramePayload,
} from './protocol';

// ---------------------------------------------------------------------------
// Storage: an opaque origin has none, and reading localStorage throws there.
// ---------------------------------------------------------------------------

export class MemoryStorage {
  private items = new Map<string, string>();
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
    this.items.set(String(k), String(v));
  }
  removeItem(k: string) {
    this.items.delete(String(k));
  }
  clear() {
    this.items.clear();
  }
}

/** Replace localStorage / sessionStorage with in-memory ones where they throw. */
export function installMemoryStorage(win: Window): void {
  for (const name of ['localStorage', 'sessionStorage'] as const) {
    let works = false;
    try {
      works = !!win[name];
    } catch {
      works = false;
    }
    if (works) continue;
    try {
      Object.defineProperty(win, name, {
        value: new MemoryStorage(),
        configurable: true,
      });
    } catch {
      // Not replaceable here; render code that touches it will see the throw.
    }
  }
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
}

/**
 * Say hello to the parent until it answers with INIT and a port; from then on every
 * request rides the port and window messages are ignored.
 */
export function connectBridge(
  win: Window,
  handlers: {
    onInit: (payload: FramePayload, bridge: Bridge) => void;
    onUpdate: (payload: FramePayload) => void;
  },
  helloEveryMs = 200,
): () => void {
  let port: MessagePort | null = null;
  let nextId = 1;
  const pending = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  const bridge: Bridge = {
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
    };
    if (data && data.type === UPDATE && data.payload) {
      handlers.onUpdate(data.payload);
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
    const data = event.data as { type?: string; payload?: FramePayload };
    if (!data || data.type !== INIT || !data.payload || !event.ports?.[0])
      return;
    port = event.ports[0];
    port.onmessage = onPortMessage;
    clearInterval(timer);
    win.removeEventListener('message', onWindowMessage);
    handlers.onInit(data.payload, bridge);
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

/** Report the content's height to the host whenever it changes. */
export function reportHeight(
  root: HTMLElement,
  send: (height: number) => void,
): () => void {
  let last = -1;
  const measure = () => {
    const h = Math.ceil(root.getBoundingClientRect().height) + 2;
    if (h !== last) {
      last = h;
      send(h);
    }
  };
  const observer = new ResizeObserver(measure);
  observer.observe(root);
  measure();
  return () => observer.disconnect();
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
}: {
  payload: FramePayload;
  bridge: Bridge;
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
    />
  );
}

export function startFrame(win: Window, rootEl: HTMLElement): void {
  installMemoryStorage(win);
  const reactRoot = createRoot(rootEl);
  let bridge: Bridge | null = null;
  let current: FramePayload | null = null;
  const draw = (payload: FramePayload) => {
    (win as unknown as { MAPBOX_TOKEN?: string }).MAPBOX_TOKEN =
      payload.mapboxToken || '';
    // One code path for pages and workflows: a page is a workflow with no run.
    (
      win as unknown as { WORKFLOW_API_ENDPOINTS?: unknown }
    ).WORKFLOW_API_ENDPOINTS = payload.apiEndpoints || {};
    reactRoot.render(<WorkflowFramePage payload={payload} bridge={bridge!} />);
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
  connectBridge(win, {
    onInit(payload, b) {
      bridge = b;
      current = payload;
      const pageUrl = () => (current && current.pageUrl) || '';
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
      reportHeight(rootEl, (height) => {
        b.request('resize', { height }).catch(() => undefined);
      });
      draw(payload);
    },
    // UPDATE carries only what changed.
    onUpdate(partial) {
      current = { ...(current as FramePayload), ...partial };
      draw(current);
    },
  });
}
