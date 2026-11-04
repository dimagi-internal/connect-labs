/**
 * What a viewer sees while report data loads: the one strip, for the runner and for the
 * sandbox frame. Progress comes in the one shape every kind of loading reports
 * (TaskProgress, from `build_task_progress` on the server).
 *
 * The bar appears only for a real count. A stage is not a fraction of the time: "step 2
 * of 6" can be most of the wait or a second of it, so it stays words.
 *
 * The clock ticks here between polls. It sits outside the live region: a screen reader
 * can reach it, and announcing the stage helps where announcing every second would not.
 */

import React, { useEffect, useState } from 'react';
import type { TaskProgress } from '../../connect_labs/static/js/task-progress';

export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

/** The count-based percentage, or null when there is no count to base one on. */
export function countedPercent(progress?: TaskProgress): number | null {
  if (!progress?.total || progress.total <= 0) return null;
  return Math.min(
    100,
    Math.round(((progress.processed || 0) / progress.total) * 100),
  );
}

interface Props {
  message: string;
  progress?: TaskProgress;
  /** How long the fill has been running. Without it, no clock and no "you can leave":
   * the data is on its way here, and leaving would lose it. */
  elapsedSeconds?: number;
}

export function LoadingStrip({ message, progress, elapsedSeconds }: Props) {
  const filling = elapsedSeconds !== undefined;
  const [seen, setSeen] = useState(() => ({
    at: Date.now(),
    from: elapsedSeconds || 0,
  }));
  const [, tick] = useState(0);
  useEffect(() => {
    setSeen({ at: Date.now(), from: elapsedSeconds || 0 });
  }, [elapsedSeconds]);
  useEffect(() => {
    if (!filling) return undefined;
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [filling]);
  const elapsed = seen.from + (Date.now() - seen.at) / 1000;
  const percent = countedPercent(progress);
  const steps = progress?.total_stages || 0;
  return (
    <div
      className="text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 mb-3"
      data-testid="loading-strip"
    >
      <div className="flex items-center gap-2">
        <i className="fa-solid fa-spinner fa-spin" aria-hidden="true" />
        <span role="status" className="flex-1">
          {message}
          {steps > 1 && ` (step ${progress?.current_stage} of ${steps})`}
        </span>
        {filling && (
          <span className="tabular-nums">{formatElapsed(elapsed)}</span>
        )}
      </div>
      {percent !== null && (
        <div
          className="mt-1.5 h-1 rounded bg-gray-200 overflow-hidden"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-label="Download progress"
        >
          <div
            className="h-full bg-blue-500 transition-all"
            style={{ width: `${percent}%` }}
          />
        </div>
      )}
      {filling && (
        <div className="mt-1 text-gray-400">
          You can leave this page. Loading carries on, and the report opens
          straight away once it has finished.
        </div>
      )}
    </div>
  );
}
