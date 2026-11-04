/**
 * The client half of connect_labs/workflow/cache_warm.py: a cold cache answers 202
 * `{status: "warming"}` while a background task fills it, and the reader asks again.
 *
 * One loop, used by the runner for a page's pipelines and `actions.queryPipelineRows`,
 * and by the sandbox host for every read a render makes. A render never sees a 202: its
 * `fetch` just takes as long as the fill takes, while the host shows the viewer what is
 * happening (LoadingStrip). That is what lets a report written before this existed, or
 * forked from one, work unchanged.
 */

import type { TaskProgress } from '../../connect_labs/static/js/task-progress';

export interface Warming {
  message: string;
  elapsedSeconds: number;
  retryAfterMs: number;
  /** The fill's progress, in the shape every kind of loading reports. */
  progress?: TaskProgress;
}

export const DEFAULT_RETRY_AFTER_MS = 5000;
/** Long enough for a programme's worth of opportunities. Past it the reader is told,
 * and its Retry joins the same fill. */
export const WARMING_TIMEOUT_MS = 30 * 60 * 1000;
/** A deploy replaces the web tasks under a long poll: the load balancer answers 502/503
 * for a while, and that says nothing about the fill. */
export const MAX_CONSECUTIVE_BLIPS = 12;

/** The fill's status, if this answer is one. */
export function warmingFrom(status: number, data: unknown): Warming | null {
  if (status !== 202 || !data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  if (d.status !== 'warming') return null;
  return {
    message: String(d.message || 'Loading…'),
    elapsedSeconds: Number(d.elapsed_seconds) || 0,
    retryAfterMs: Number(d.retry_after_ms) || DEFAULT_RETRY_AFTER_MS,
    progress: d.progress as TaskProgress | undefined,
  };
}

export class WarmingTimeout extends Error {
  constructor() {
    super('The data is still loading. Try again in a few minutes.');
    this.name = 'WarmingTimeout';
  }
}

export interface PollOptions<A> {
  /** The fill's status if this answer is one, or null when it is the final answer. */
  warmingOf(answer: A): Warming | null;
  /** An answer that says nothing about the fill (a gateway error mid-deploy). */
  isBlip?(answer: A): boolean;
  /** Each status while waiting. */
  onWarming?(warming: Warming): void;
  timeoutMs?: number;
  /** The reader has gone (its frame was torn down): stop asking. */
  stopped?(): boolean;
  now?(): number;
  sleep?(ms: number): Promise<void>;
}

/**
 * `read` until the answer stops being a warming one. Blips are only forgiven once a
 * fill is known to be under way: before that, a failure is the caller's own answer.
 */
export async function pollWhileWarming<A>(
  read: () => Promise<A>,
  options: PollOptions<A>,
): Promise<A> {
  const now = options.now || (() => Date.now());
  const sleep =
    options.sleep ||
    ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const deadline = now() + (options.timeoutMs ?? WARMING_TIMEOUT_MS);
  let last: Warming | null = null;
  let blips = 0;
  for (;;) {
    let answer: A | undefined;
    let failure: unknown;
    let failed = false;
    try {
      answer = await read();
    } catch (e) {
      failure = e;
      failed = true;
    }
    const blip = failed || options.isBlip?.(answer as A);
    if (last && blip && blips < MAX_CONSECUTIVE_BLIPS) {
      blips += 1;
    } else {
      if (failed) throw failure;
      const warming = options.warmingOf(answer as A);
      if (!warming) return answer as A;
      blips = 0;
      last = warming;
      options.onWarming?.(warming);
    }
    if (now() > deadline || options.stopped?.()) throw new WarmingTimeout();
    await sleep((last as Warming).retryAfterMs);
  }
}
