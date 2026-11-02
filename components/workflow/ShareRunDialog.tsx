/**
 * Share a saved run by public link: choose how long it lasts, copy it, end it.
 * Shown by the runner only when the server says this person may (public_share_views.py).
 */

import React, { useCallback, useEffect, useState } from 'react';

interface PublicShare {
  id: number;
  url: string;
  expires_at: string;
  view_count: number;
  created_by: string;
}

const DURATIONS: { value: string; label: string }[] = [
  { value: '1', label: '1 day' },
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: 'date', label: 'Until a date' },
];

function day(iso: string): string {
  return iso.slice(0, 10);
}

export function ShareRunDialog({
  url,
  csrfToken,
}: {
  url: string;
  csrfToken: string;
}) {
  const [open, setOpen] = useState(false);
  const [shares, setShares] = useState<PublicShare[]>([]);
  const [duration, setDuration] = useState('7');
  const [until, setUntil] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);

  const load = useCallback(async () => {
    const response = await fetch(url, { credentials: 'same-origin' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Could not load links.');
    setShares(body.shares);
  }, [url]);

  useEffect(() => {
    if (!open) return;
    load().catch((e) => setError(e.message));
  }, [open, load]);

  const post = async (address: string, payload: object) => {
    const response = await fetch(address, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'That did not work.');
    return body;
  };

  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not work.');
    } finally {
      setBusy(false);
    }
  };

  const create = () =>
    act(async () => {
      await post(url, {
        confirmed: true,
        ...(duration === 'date' ? { until } : { days: Number(duration) }),
      });
      setConfirmed(false);
    });

  const end = (id: number) =>
    act(async () => {
      await post(`/labs/workflow/api/public-shares/${id}/end/`, {});
    });

  const copy = async (share: PublicShare) => {
    await navigator.clipboard.writeText(share.url);
    setCopied(share.id);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="share-run"
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
      >
        <i className="fa-solid fa-share-nodes" /> Share
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div
            role="dialog"
            aria-label="Share this run"
            className="w-full max-w-xl rounded-lg bg-white p-5 shadow-xl max-h-[90vh] overflow-y-auto"
          >
            <div className="flex items-start justify-between mb-3">
              <h2 className="text-lg font-semibold text-gray-900">
                Share this run by public link
              </h2>
              <button
                type="button"
                aria-label="Close"
                onClick={() => setOpen(false)}
                className="text-gray-500 hover:text-gray-800"
              >
                <i className="fa-solid fa-xmark" />
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-2 mb-3">
              <label
                htmlFor="share-duration"
                className="text-sm font-medium text-gray-700"
              >
                Link lasts
              </label>
              <select
                id="share-duration"
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                className="rounded-md border border-gray-300 px-2 py-1 text-sm"
              >
                {DURATIONS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
              {duration === 'date' && (
                <input
                  type="date"
                  aria-label="Last day of the link"
                  value={until}
                  onChange={(e) => setUntil(e.target.value)}
                  className="rounded-md border border-gray-300 px-2 py-1 text-sm"
                />
              )}
            </div>

            <label className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 mb-3">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                Anyone with this link can see everything saved in this run
                without signing in, including worker names and locations, and
                including data the report does not display. It stays available
                until the date you chose or until you end it.
              </span>
            </label>

            <button
              type="button"
              onClick={create}
              disabled={busy || !confirmed || (duration === 'date' && !until)}
              className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              Create link
            </button>

            {error && (
              <p role="alert" className="mt-3 text-sm text-red-700">
                {error}
              </p>
            )}

            {shares.length > 0 && (
              <ul className="mt-4 divide-y divide-gray-200 border-t border-gray-200">
                {shares.map((share) => (
                  <li
                    key={share.id}
                    className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
                  >
                    <span className="text-gray-700">
                      Ends {day(share.expires_at)} · {share.view_count} view
                      {share.view_count === 1 ? '' : 's'}
                      {share.created_by ? ` · by ${share.created_by}` : ''}
                    </span>
                    <span className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => copy(share)}
                        className="text-blue-700 underline"
                      >
                        {copied === share.id ? 'Copied' : 'Copy link'}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => end(share.id)}
                        className="text-red-700 underline disabled:opacity-50"
                      >
                        End link
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </>
  );
}
