import { describe, expect, it, vi } from 'vitest';

import { createSharedReads } from './sharedReads';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('createSharedReads', () => {
  it('sends ONE request for identical reads in flight together', async () => {
    const shared = createSharedReads();
    const d = deferred();
    const read = vi.fn(() => d.promise);
    const a = shared.run('k', read);
    const b = shared.run('k', read);
    expect(read).toHaveBeenCalledTimes(1);
    d.resolve({ rows: [1, 2], total: 2 });
    expect(await a).toEqual({ rows: [1, 2], total: 2 });
    expect(await b).toEqual({ rows: [1, 2], total: 2 });
  });

  it('gives each caller its own copy, so one mutating its rows cannot reach the other', async () => {
    const shared = createSharedReads();
    const d = deferred();
    const a = shared.run('k', () => d.promise);
    const b = shared.run('k', () => d.promise);
    d.resolve({ rows: [3, 1, 2] });
    const first = await a;
    first.rows.sort();
    expect((await b).rows).toEqual([3, 1, 2]);
  });

  it('sends different reads separately', async () => {
    const shared = createSharedReads();
    const read = vi.fn(async () => 'x');
    await Promise.all([shared.run('a', read), shared.run('b', read)]);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('keeps nothing once a read settles: the next identical call asks again', async () => {
    const shared = createSharedReads();
    const read = vi.fn(async () => 'x');
    await shared.run('k', read);
    expect(shared.size).toBe(0);
    await shared.run('k', read);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('a failure reaches every caller and is not remembered', async () => {
    const shared = createSharedReads();
    const d = deferred();
    const a = shared.run('k', () => d.promise);
    const b = shared.run('k', () => d.promise);
    d.reject(new Error('boom'));
    await expect(a).rejects.toThrow('boom');
    await expect(b).rejects.toThrow('boom');
    expect(shared.size).toBe(0);
  });

  it('reports progress to every caller, and one broken callback does not fail the read', async () => {
    const shared = createSharedReads();
    const d = deferred();
    const seenA = [];
    const seenB = [];
    let report;
    const a = shared.run(
      'k',
      (onStatus) => {
        report = onStatus;
        return d.promise;
      },
      (m) => {
        seenA.push(m);
        throw new Error('bad callback');
      },
    );
    const b = shared.run(
      'k',
      () => d.promise,
      (m) => seenB.push(m),
    );
    report('Warming…');
    d.resolve('done');
    expect(await a).toBe('done');
    expect(await b).toBe('done');
    expect(seenA).toEqual(['Warming…']);
    expect(seenB).toEqual(['Warming…']);
  });
});
