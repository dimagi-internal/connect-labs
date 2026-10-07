/**
 * Running one of a workflow's actions from a button: preview, confirm, progress.
 *
 * A render's button calls `actions.runAction(key, {workers})`; the runner mounts
 * this dialog. It asks labs for a PREVIEW of exactly what would happen (which
 * workers, which bot, which text), lets the person settle anything the preview
 * needs (a bot to use, connecting Open Chat Studio), and runs the action only when
 * they confirm -- with the preview's single-use token, so what runs is what they
 * saw. The action then runs in the background; this shows it worker by worker.
 *
 * The same two steps an agent takes through the labs MCP (`workflow_run_action`):
 * see connect_labs/workflow/actions.py. Owned by the runner, not render code, so
 * no template can skip the confirmation.
 */
import React, { useCallback, useEffect, useState } from 'react';
import type {
  WorkflowActionExecution,
  WorkflowActionPreview,
  WorkflowActionWorker,
} from './types';

const POLL_MS = 1500;
const DONE = new Set(['completed', 'completed_with_errors', 'failed']);

export interface ActionRequest {
  key: string;
  label: string;
  args: { workers: WorkflowActionWorker[]; [key: string]: unknown };
}

interface Props {
  request: ActionRequest;
  actionBase: string;
  executionBase: string;
  csrfToken: string;
  onClose: (execution: WorkflowActionExecution | null) => void;
}

async function send(url: string, csrfToken: string, body?: unknown) {
  const response = await fetch(url, {
    method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin',
    headers:
      body === undefined
        ? {}
        : { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

export function ActionDialog({
  request,
  actionBase,
  executionBase,
  csrfToken,
  onClose,
}: Props) {
  // The run page stamps the run's own scope on its API calls; do the same here.
  const scope = window.location.search.match(
    /[?&](opportunity_id|program_id)=\d+/,
  );
  const qs = scope ? `?${scope[0].slice(1)}` : '';
  const [args, setArgs] = useState(request.args);
  const [preview, setPreview] = useState<WorkflowActionPreview | null>(null);
  const [execution, setExecution] = useState<WorkflowActionExecution | null>(
    null,
  );
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [bot, setBot] = useState('');
  // The QA redirect (`deliver_to`): a Dimagi tester sends this one conversation to
  // their own Connect app. Applying it changes the arguments, so it re-previews --
  // the confirm token is bound to them.
  const [qaTo, setQaTo] = useState(
    typeof request.args.deliver_to === 'string' ? request.args.deliver_to : '',
  );
  const applyQa = (value: string) => {
    const next = { ...args };
    if (value.trim()) next.deliver_to = value.trim();
    else delete next.deliver_to;
    setArgs(next);
  };

  const load = useCallback(
    async (next: typeof args) => {
      setBusy(true);
      setError('');
      // A preview's token is bound to ITS arguments: never leave an older one
      // confirmable while these are being previewed (or after they are refused).
      setPreview(null);
      try {
        setPreview(
          await send(`${actionBase}${request.key}/preview/${qs}`, csrfToken, {
            arguments: next,
          }),
        );
      } catch (e) {
        setError(
          e instanceof Error ? e.message : 'Could not preview this action',
        );
      } finally {
        setBusy(false);
      }
    },
    [actionBase, request.key, qs, csrfToken],
  );

  useEffect(() => {
    load(args);
  }, [load, args]);

  useEffect(() => {
    if (!execution || DONE.has(execution.status)) return;
    const id = window.setTimeout(async () => {
      try {
        const data = await send(`${executionBase}${execution.id}/`, csrfToken);
        setExecution(data.execution);
      } catch {
        // Retried on the next tick.
      }
    }, POLL_MS);
    return () => window.clearTimeout(id);
  }, [execution, executionBase, csrfToken]);

  const confirm = async () => {
    if (!preview?.confirm) return;
    setBusy(true);
    setError('');
    try {
      const data = await send(
        `${actionBase}${request.key}/run/${qs}`,
        csrfToken,
        {
          arguments: preview.arguments,
          confirm: preview.confirm,
        },
      );
      setExecution(data.execution);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not run this action');
    } finally {
      setBusy(false);
    }
  };

  const done = execution && DONE.has(execution.status);
  const running = execution && !done;
  const results = execution?.results || {};

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="dialog"
      aria-modal="true"
    >
      <div className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-lg bg-white shadow-xl">
        <div className="border-b border-gray-200 px-5 py-3">
          <div className="text-base font-semibold text-gray-900">
            {preview?.summary || request.label}
          </div>
          {!execution && (
            <div className="text-xs text-gray-500">
              Nothing happens until you confirm.
            </div>
          )}
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4 text-sm">
          {!preview && !error && (
            <div className="text-gray-500">Preparing…</div>
          )}

          {preview?.synthetic && (
            <div className="rounded bg-gray-50 p-2 text-xs text-gray-600">
              {preview.synthetic_note ||
                'Synthetic data: no message is sent; the task gets a sample conversation.'}
            </div>
          )}

          {!execution && Boolean(preview?.qa_redirect || args.deliver_to) && (
            <label className="block text-xs text-gray-700">
              Send to me instead (QA) — your PersonalID username
              <div className="mt-1 flex gap-2">
                <input
                  type="text"
                  className="block w-full rounded border border-gray-300 px-2 py-1 text-sm"
                  value={qaTo}
                  placeholder="Leave empty to send to the worker"
                  onChange={(e) => setQaTo(e.target.value)}
                />
                <button
                  type="button"
                  className="rounded border border-gray-300 px-3 text-sm disabled:opacity-50"
                  disabled={
                    busy || qaTo.trim() === String(args.deliver_to || '')
                  }
                  onClick={() => applyQa(qaTo)}
                >
                  Apply
                </button>
              </div>
            </label>
          )}

          {preview?.deliver_to && (
            <div className="rounded bg-amber-50 p-2 text-xs font-medium text-amber-900">
              QA: this conversation goes to {preview.deliver_to}, not to the
              worker.
            </div>
          )}

          {preview?.needs.includes('connect_ocs') && (
            <div className="rounded bg-amber-50 p-2 text-xs text-amber-900">
              Connect to Open Chat Studio to start these conversations.{' '}
              <a
                className="font-semibold underline"
                href={`${preview.connect_url}?next=${encodeURIComponent(window.location.pathname + window.location.search)}`}
              >
                Connect
              </a>
            </div>
          )}

          {preview?.needs.includes('bot') && (
            <label className="block text-xs text-gray-700">
              Which bot?
              {preview.unknown_bot && (
                <span className="ml-1 text-amber-700">
                  (the configured bot is not available to you)
                </span>
              )}
              <div className="mt-1 flex gap-2">
                <select
                  className="block w-full rounded border border-gray-300 px-2 py-1 text-sm"
                  value={bot}
                  onChange={(e) => setBot(e.target.value)}
                >
                  <option value="">Choose a bot</option>
                  {(preview.bot_choices || []).map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="rounded border border-gray-300 px-3 text-sm disabled:opacity-50"
                  disabled={!bot || busy}
                  onClick={() => setArgs({ ...args, bot })}
                >
                  Use
                </button>
              </div>
            </label>
          )}

          {preview?.bot && (
            <div className="text-xs text-gray-600">
              Bot:{' '}
              <span className="font-medium text-gray-900">
                {preview.bot.name}
              </span>
            </div>
          )}

          {preview && (
            <ul className="divide-y divide-gray-100 rounded border border-gray-100">
              {preview.workers.length === 0 && (
                <li className="px-3 py-2 text-xs text-gray-500">
                  Nobody to coach: no indicator is off target or on watch.
                </li>
              )}
              {preview.workers.map((w) => {
                const r = results[w.key];
                return (
                  <li key={w.key} className="px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-gray-900">
                        {w.name}
                      </span>
                      <span
                        className={`text-xs ${
                          r?.status === 'ok'
                            ? 'text-green-700'
                            : r?.status === 'failed'
                              ? 'text-red-700'
                              : 'text-gray-500'
                        }`}
                      >
                        {r?.status === 'ok'
                          ? 'done'
                          : r?.status === 'failed'
                            ? r.error || 'failed'
                            : running
                              ? 'waiting…'
                              : ''}
                      </span>
                    </div>
                    {w.sending_to && (
                      <p className="mt-1 text-xs font-medium text-amber-700">
                        {w.sending_to}
                      </p>
                    )}
                    {w.prompt && (
                      <p className="mt-1 whitespace-pre-wrap text-xs text-gray-600">
                        {w.prompt}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {preview?.skipped && preview.skipped.length > 0 && (
            <div className="text-xs text-gray-500">
              Left out:{' '}
              {preview.skipped.map((w) => `${w.name} (${w.reason})`).join(', ')}
            </div>
          )}

          {execution && (
            <div className="text-xs text-gray-600">
              {execution.status === 'failed'
                ? execution.error || 'The action failed.'
                : `${execution.progress.ok} done${execution.progress.failed ? `, ${execution.progress.failed} failed` : ''} of ${execution.progress.total}`}
            </div>
          )}
          {error && <div className="text-xs text-red-700">{error}</div>}
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-3">
          {!execution && (
            <>
              <button
                type="button"
                className="rounded px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
                onClick={() => onClose(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                disabled={busy || !preview?.confirm}
                onClick={confirm}
              >
                {busy
                  ? 'Working…'
                  : `Confirm for ${preview?.workers.length ?? ''}`}
              </button>
            </>
          )}
          {execution && (
            <button
              type="button"
              className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              disabled={!done}
              onClick={() => onClose(execution)}
            >
              {done ? 'Close' : 'Running…'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
