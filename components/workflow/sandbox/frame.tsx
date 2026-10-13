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
import { createLinkHelpers } from '../links';
import type { WorkflowProps } from '../types';
import {
  HELLO,
  INIT,
  NOT_ON_A_PAGE,
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

function notOnAPage(name: string) {
  return () => Promise.reject(new Error(`${name} is ${NOT_ON_A_PAGE}`));
}

/**
 * `actions`: the phase-1 bridge methods, and for every other name a function that
 * rejects with "not available on a page yet" (rather than `undefined is not a function`).
 */
export function pageActions(bridge: Bridge): WorkflowProps['actions'] {
  const known: Record<string, unknown> = {
    querySupply: (alias: string, query: { args?: unknown } = {}) =>
      bridge.request('querySupply', { alias, args: query?.args ?? {} }),
    queryWorkflow: (alias: string) =>
      bridge.request('queryWorkflow', { alias }),
    download: (file: Record<string, unknown>) =>
      bridge.request('download', file),
    copyText: (text: string) => bridge.request('copyText', { text }),
  };
  return new Proxy(known, {
    get(target, prop) {
      if (typeof prop !== 'string') return undefined;
      if (prop in target) return target[prop];
      if (prop === 'then') return undefined; // not a thenable
      return notOnAPage(prop);
    },
  }) as unknown as WorkflowProps['actions'];
}

function FramePage({
  payload,
  bridge,
}: {
  payload: FramePayload;
  bridge: Bridge;
}) {
  const [state, setState] = useState(payload.view.state || {});
  useEffect(() => setState(payload.view.state || {}), [payload.view.state]);
  const onUpdateState = useCallback(async (next: Record<string, unknown>) => {
    // A page has no run: its state lives only in the frame, as in edit mode.
    setState((prev) => ({ ...prev, ...next }));
  }, []);
  const actions = useMemo(() => pageActions(bridge), [bridge]);
  const onError = useCallback(
    (message: string) => {
      bridge.request('reportError', { message }).catch(() => undefined);
    },
    [bridge],
  );
  const view = useMemo(
    () => ({
      workers: [],
      pipelines: {},
      supply: payload.supply,
      state,
      isCompleted: payload.view.isCompleted,
      asOf: payload.view.asOf,
      complete: () =>
        Promise.reject(new Error(`view.complete is ${NOT_ON_A_PAGE}`)),
      flags: [],
      flagsFor: () => [],
      ensureAutoFlags: async () => [],
      audits: [],
      auditsFor: () => [],
      tasks: [],
      tasksFor: () => [],
      workflowActions: [],
      shareSelection: (selection: unknown) => {
        bridge.request('shareSelection', selection).catch(() => undefined);
      },
      confirm: (text: string) =>
        bridge.request('confirm', { text }).then(Boolean),
    }),
    [bridge, payload, state],
  );
  return (
    <DynamicWorkflow
      renderCode={payload.renderCode}
      definition={payload.definition as unknown as WorkflowProps['definition']}
      instance={
        { ...payload.instance, state } as unknown as WorkflowProps['instance']
      }
      workers={[]}
      pipelines={{}}
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

export function startFrame(win: Window, rootEl: HTMLElement): void {
  installMemoryStorage(win);
  const reactRoot = createRoot(rootEl);
  let bridge: Bridge | null = null;
  const draw = (payload: FramePayload) => {
    (win as unknown as { MAPBOX_TOKEN?: string }).MAPBOX_TOKEN =
      payload.mapboxToken || '';
    reactRoot.render(<FramePage payload={payload} bridge={bridge!} />);
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
      interceptAnchorClicks(
        win.document,
        (url, newTab) => {
          b.request('navigate', { url, newTab }).catch((err) =>
            console.warn('[page] link refused:', err.message),
          );
        },
        typeof payload.pageUrl === 'string' ? payload.pageUrl : '',
      );
      reportHeight(rootEl, (height) => {
        b.request('resize', { height }).catch(() => undefined);
      });
      draw(payload);
    },
    onUpdate: draw,
  });
}
