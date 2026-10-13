/**
 * The runner's HOST MODE for a page: draws the sandboxed frame where DynamicWorkflow
 * would have run the render code, passes it the page's data, and answers it
 * (./host.ts). Everything the person sees from the host -- a confirm, the page's
 * render error, "this page tried to leave its frame" -- is drawn here, as text.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  createSandboxHost,
  proxyFetch,
  type SandboxHost,
  type SandboxMode,
  type WorkflowHandlers,
} from './host';
import type { FramePayload } from './protocol';
import { checkFetch, type RouteContext } from './routes';

/** What the runner does for a workflow drawn in the frame (phase 2). */
export interface WorkflowHostProps {
  csrfToken: string;
  routeContext: RouteContext;
  queryPipelineRows(
    alias: string,
    query: Record<string, unknown>,
  ): Promise<unknown>;
  updateState(state: Record<string, unknown>): Promise<unknown>;
  runAction(
    key: string,
    args: Record<string, unknown>,
    options: { qaOnly: boolean },
  ): Promise<unknown>;
  actionKeys(): string[];
  /** Complete the run (no question: the frame host has asked already). */
  completeRun(): Promise<{ success: boolean; error?: string }>;
}

export interface SandboxFrameProps {
  labsOrigin: string;
  payload: FramePayload;
  querySupply(alias: string, args: Record<string, unknown>): Promise<unknown>;
  queryWorkflow(alias: string): Promise<unknown>;
  shareSelection(selection: {
    visible_ids: string[];
    drilled: Record<string, unknown>;
  }): void;
  /** What it draws: a page has no run. Both get the full method set when
   * `workflow` (the host's handlers) is given. */
  mode?: SandboxMode;
  workflow?: WorkflowHostProps;
}

interface Question {
  /** Whose words: the page's own (`confirm`) or labs' (`ask`, an effect). */
  from: 'page' | 'labs';
  text: string;
  /** Labs' words quoted under the page's (completing a run). */
  detail?: string;
  okLabel: string;
  answer: (yes: boolean) => void;
}

/**
 * Copy on THIS page. The click was in the frame, so the frame holds focus and the
 * async clipboard refuses a document that is not focused; the older selection copy
 * works with the click's activation, which reaches this page too.
 */
async function copyOnHost(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    // fall through
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand('copy');
  area.remove();
  if (!ok) throw new Error('the browser refused to copy');
}

function saveFile(file: { filename: string; mime: string; bytes: Uint8Array }) {
  const blob = new Blob([file.bytes as BlobPart], { type: file.mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function SandboxFrame(props: SandboxFrameProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<SandboxHost | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const [left, setLeft] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);
  const [notices, setNotices] = useState<{ id: number; text: string }[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    // One question at a time: a second while one is open is a no.
    const askQuestion = (q: Omit<Question, 'answer'>) =>
      new Promise<boolean>((resolve) => {
        setQuestion((current) => {
          if (current) {
            resolve(false);
            return current;
          }
          return {
            ...q,
            answer: (yes) => {
              setQuestion(null);
              resolve(yes);
            },
          };
        });
      });
    const wf = propsRef.current.workflow;
    const workflowHandlers: WorkflowHandlers | undefined = wf
      ? {
          fetch: (checked, init) =>
            proxyFetch(
              checked,
              init,
              propsRef.current.workflow!.csrfToken,
              fetch,
              (url) =>
                checkFetch(url, 'GET', propsRef.current.workflow!.routeContext),
            ),
          queryPipelineRows: (alias, query) =>
            propsRef.current.workflow!.queryPipelineRows(alias, query),
          updateState: (state) => propsRef.current.workflow!.updateState(state),
          runAction: (key, args, options) =>
            propsRef.current.workflow!.runAction(key, args, options),
          actionKeys: () => propsRef.current.workflow!.actionKeys(),
          ask: (text) => askQuestion({ from: 'labs', text, okLabel: 'Allow' }),
          completeRun: async (pageText) => {
            const yes = await askQuestion({
              from: 'labs',
              text: 'Complete this run? It is saved as it is now and cannot change after.',
              detail: pageText || undefined,
              okLabel: 'Complete run',
            });
            if (!yes) return false;
            const res = await propsRef.current.workflow!.completeRun();
            if (!res.success) {
              setNotices((n) => [
                ...n,
                {
                  id: Date.now(),
                  text: `The run could not be completed: ${res.error || 'unknown error'}`,
                },
              ]);
              return false;
            }
            // Re-drawn from the snapshot the server just wrote.
            window.location.reload();
            return true;
          },
          setLocation: (search, hash, push) => {
            const url = window.location.pathname + search + hash;
            if (push) window.history.pushState(null, '', url);
            else window.history.replaceState(null, '', url);
          },
          notify: (text) =>
            setNotices((n) => [
              ...n.slice(-2),
              { id: Date.now() + Math.random(), text },
            ]),
        }
      : undefined;
    const host = createSandboxHost({
      container: containerRef.current,
      labsOrigin: props.labsOrigin,
      payload: propsRef.current.payload,
      onLeave: () => setLeft(true),
      mode: props.mode,
      routeContext: wf?.routeContext,
      workflow: workflowHandlers,
      search: window.location.search,
      fill: true,
      handlers: {
        querySupply: (alias, args) => propsRef.current.querySupply(alias, args),
        queryWorkflow: (alias) => propsRef.current.queryWorkflow(alias),
        navigate: (href, newTab) => {
          if (newTab) window.open(href, '_blank', 'noopener,noreferrer');
          else window.location.assign(href);
        },
        shareSelection: (selection) =>
          propsRef.current.shareSelection(selection),
        copyText: copyOnHost,
        confirm: (text) => askQuestion({ from: 'page', text, okLabel: 'OK' }),
        download: saveFile,
        reportError: (message) => setRenderError(message),
      },
    });
    hostRef.current = host;
    return () => {
      host.destroy();
      hostRef.current = null;
    };
    // The frame is drawn once per shell; data changes go through update().
  }, [props.labsOrigin, props.mode]);

  useEffect(() => {
    hostRef.current?.update(props.payload);
  }, [props.payload]);

  if (left) {
    return (
      <div
        className="bg-red-50 border-l-4 border-red-400 p-4 text-sm text-red-700"
        data-testid="page-sandbox-left"
      >
        This {props.mode === 'workflow' ? 'workflow' : 'page'} tried to leave
        its frame, so it has been stopped. Reload to draw it again.
      </div>
    );
  }

  return (
    <div>
      {renderError && (
        <div
          className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-4 text-sm text-yellow-700"
          data-testid="page-sandbox-error"
        >
          The {props.mode === 'workflow' ? 'workflow' : 'page'} failed to draw:{' '}
          {renderError}
        </div>
      )}
      <div ref={containerRef} data-testid="page-sandbox" />
      {notices.length > 0 && (
        <div
          className="fixed bottom-4 left-4 z-50 space-y-2 max-w-md"
          data-testid="page-sandbox-notices"
        >
          {notices.map((n) => (
            <div
              key={n.id}
              className="rounded-md bg-gray-900 text-white text-sm px-4 py-3 shadow-lg flex items-start gap-3"
            >
              <span className="whitespace-pre-wrap break-words flex-1">
                {n.text}
              </span>
              <button
                type="button"
                className="text-gray-300 hover:text-white"
                aria-label="Dismiss"
                onClick={() =>
                  setNotices((all) => all.filter((x) => x.id !== n.id))
                }
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      {question && (
        <QuestionDialog
          question={question}
          asker={props.mode === 'workflow' ? 'workflow' : 'page'}
        />
      )}
    </div>
  );
}

/** How long labs' own question stays unanswerable after it appears: a double click
 * timed to its arrival must not land on Allow. */
export const ARM_MS = 700;

/**
 * The host's question. Labs' own (`from: 'labs'`, an effect or a run's completion)
 * is drawn unlike the page's (amber, a shield, labs' words first), focuses Cancel,
 * and its Allow only works after ARM_MS and a press that started on it.
 */
export function QuestionDialog({
  question,
  asker,
}: {
  question: Question;
  asker: 'workflow' | 'page';
}) {
  const labs = question.from === 'labs';
  const [armed, setArmed] = useState(!labs);
  const pressed = useRef(false);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    cancelRef.current?.focus();
    if (!labs) return;
    const t = setTimeout(() => setArmed(true), ARM_MS);
    return () => clearTimeout(t);
  }, [labs]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      data-testid="page-sandbox-confirm"
    >
      <div
        className={`max-w-md w-full rounded-lg bg-white shadow-xl p-5 ${
          labs ? 'border-4 border-amber-400' : ''
        }`}
      >
        <div
          className={`text-xs uppercase tracking-wide mb-2 ${
            labs ? 'text-amber-700 font-semibold' : 'text-gray-500'
          }`}
        >
          {labs ? (
            <>
              <i className="fa-solid fa-shield-halved mr-1" />
              Labs needs your OK
            </>
          ) : (
            `This ${asker} asks`
          )}
        </div>
        <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">
          {question.text}
        </p>
        {question.detail && (
          <blockquote className="mt-3 border-l-4 border-gray-200 pl-3 text-sm text-gray-600 whitespace-pre-wrap break-words">
            <span className="block text-xs text-gray-400 mb-1">
              The {asker} says:
            </span>
            {question.detail}
          </blockquote>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            ref={cancelRef}
            onClick={() => question.answer(false)}
            className="px-3 py-1.5 text-sm rounded-md bg-gray-100 hover:bg-gray-200 text-gray-800"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!armed}
            data-testid="page-sandbox-allow"
            onPointerDown={() => {
              pressed.current = armed;
            }}
            onClick={(e) => {
              // A labs question takes a press that began on Allow once it was armed
              // (a keyboard Enter on a focused Allow has detail 0 and counts).
              if (labs && !(pressed.current || e.detail === 0)) return;
              question.answer(true);
            }}
            className={`px-3 py-1.5 text-sm rounded-md text-white hover:opacity-90 disabled:opacity-40 ${
              labs ? 'bg-amber-600' : 'bg-brand-indigo'
            }`}
          >
            {question.okLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
