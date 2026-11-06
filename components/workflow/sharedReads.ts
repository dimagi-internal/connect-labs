/**
 * One request for identical reads that are in flight at the same time.
 *
 * Render code asks `actions.queryPipelineRows` from effects, and an effect that
 * runs twice (a re-render, a dependency that changes identity, React's dev
 * double-invoke) asks the same question twice within milliseconds. On prod one
 * workflow made 1,894 pipeline queries from 224 page loads, 461 of them under
 * 20 ms after the one before, in bursts of almost always an EVEN size. Each one
 * is a server-side SQL read with an uncached COUNT.
 *
 * Sharing is safe because only IDENTICAL calls share, and only while the first
 * is still running: every caller gets the answer to exactly what it asked, as
 * fresh as a request it would have sent itself. Nothing is kept once the
 * request settles, so this is not a cache.
 *
 * Each caller keeps its own progress callback: the shared request reports to
 * all of them.
 */

export type StatusListener<S extends unknown[]> = (...status: S) => void;

export interface SharedReads<T, S extends unknown[]> {
  /** Run `read` for `key`, or join the read already running for it. */
  run(
    key: string,
    read: (onStatus: StatusListener<S>) => Promise<T>,
    onStatus?: StatusListener<S>,
  ): Promise<T>;
  /** Reads currently in flight (for tests). */
  readonly size: number;
}

export function createSharedReads<
  T,
  S extends unknown[] = unknown[],
>(): SharedReads<T, S> {
  const inflight = new Map<
    string,
    { promise: Promise<T>; listeners: Set<StatusListener<S>> }
  >();
  return {
    run(key, read, onStatus) {
      const running = inflight.get(key);
      if (running) {
        if (onStatus) running.listeners.add(onStatus);
        // Its own copy: render code may sort or mutate the rows it was handed, and
        // that must not reach the caller it shares a request with.
        return running.promise.then((answer) => structuredClone(answer));
      }
      const listeners = new Set<StatusListener<S>>();
      if (onStatus) listeners.add(onStatus);
      const broadcast: StatusListener<S> = (...status) => {
        for (const listener of listeners) {
          try {
            listener(...status);
          } catch {
            // One caller's broken progress callback must not fail the others' read.
          }
        }
      };
      const promise = (async () => {
        try {
          return await read(broadcast);
        } finally {
          inflight.delete(key);
        }
      })();
      inflight.set(key, { promise, listeners });
      return promise;
    },
    get size() {
      return inflight.size;
    },
  };
}
