/**
 * What a viewer sees while a cold cache fills behind their page (../warming.ts). Drawn
 * by the host, above the frame, so every render gets it without asking; same strip as
 * the runner's own pipeline-loading one.
 *
 * The clock ticks here between polls. It sits outside the live region: announcing the
 * stage to a screen reader helps, announcing every second does not.
 */

import React, { useEffect, useState } from 'react';
import type { Warming } from '../warming';

export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

export function WarmingStrip({ warming }: { warming: Warming }) {
  const [seen, setSeen] = useState(() => ({
    at: Date.now(),
    from: warming.elapsedSeconds,
  }));
  const [, tick] = useState(0);
  useEffect(() => {
    setSeen({ at: Date.now(), from: warming.elapsedSeconds });
  }, [warming.elapsedSeconds]);
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  const elapsed = seen.from + (Date.now() - seen.at) / 1000;
  return (
    <div
      className="text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 mb-3"
      data-testid="page-sandbox-warming"
    >
      <div className="flex items-center gap-2">
        <i className="fa-solid fa-spinner fa-spin" aria-hidden="true" />
        <span role="status" className="flex-1">
          {warming.message}
        </span>
        <span className="tabular-nums" aria-hidden="true">
          {formatElapsed(elapsed)}
        </span>
      </div>
      <div className="mt-1 text-gray-400">
        You can leave this page. Loading carries on, and the report opens
        straight away once it has finished.
      </div>
    </div>
  );
}
