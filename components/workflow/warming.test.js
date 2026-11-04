// The client half of the cold-cache protocol (connect_labs/workflow/cache_warm.py).
import { describe, it, expect, vi } from 'vitest';
import {
  MAX_CONSECUTIVE_BLIPS,
  WarmingTimeout,
  pollWhileWarming,
  warmingFrom,
} from './warming';

const WARMING = {
  status: 'warming',
  message: 'Loading visit data',
  elapsed_seconds: 12,
  retry_after_ms: 5000,
};

function script(answers) {
  const queue = [...answers];
  return vi.fn(async () => {
    const next = queue.shift();
    if (next instanceof Error) throw next;
    return next;
  });
}

function poll(read, extra = {}) {
  let clock = 0;
  const sleep = vi.fn(async (ms) => {
    clock += ms;
  });
  const seen = [];
  const done = pollWhileWarming(read, {
    warmingOf: (a) => warmingFrom(a.status, a.data),
    isBlip: (a) => a.status >= 502,
    onWarming: (w) => seen.push(w),
    now: () => clock,
    sleep,
    ...extra,
  });
  return { done, sleep, seen };
}

describe('warmingFrom', () => {
  it('reads only a 202 that says it is warming', () => {
    expect(warmingFrom(202, WARMING)).toEqual({
      message: 'Loading visit data',
      elapsedSeconds: 12,
      retryAfterMs: 5000,
    });
    expect(warmingFrom(200, WARMING)).toBeNull();
    expect(warmingFrom(202, { status: 'queued' })).toBeNull();
    expect(warmingFrom(202, null)).toBeNull();
  });
});

describe('pollWhileWarming', () => {
  it('asks again at the pace the server names, then hands over the final answer', async () => {
    const final = { status: 200, data: { rows: [] } };
    const read = script([
      { status: 202, data: WARMING },
      { status: 202, data: { ...WARMING, message: 'Preparing' } },
      final,
    ]);
    const { done, sleep, seen } = poll(read);
    await expect(done).resolves.toBe(final);
    expect(read).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledWith(5000);
    expect(seen.map((w) => w.message)).toEqual([
      'Loading visit data',
      'Preparing',
    ]);
  });

  it('does not wait at all when the first answer is final', async () => {
    const read = script([{ status: 200, data: {} }]);
    const { done, sleep } = poll(read);
    await done;
    expect(sleep).not.toHaveBeenCalled();
  });

  it('hands a failure over when no fill is known to be under way', async () => {
    const gateway = { status: 502, data: {} };
    await expect(poll(script([gateway])).done).resolves.toBe(gateway);
    const lost = new Error('offline');
    await expect(poll(script([lost])).done).rejects.toBe(lost);
  });

  it('rides out a deploy under a fill already under way', async () => {
    const final = { status: 200, data: {} };
    const read = script([
      { status: 202, data: WARMING },
      { status: 503, data: {} },
      new Error('connection reset'),
      { status: 202, data: WARMING },
      final,
    ]);
    await expect(poll(read).done).resolves.toBe(final);
  });

  it('stops forgiving once the outage has plainly outlasted a deploy', async () => {
    const gateway = { status: 502, data: {} };
    const read = script([
      { status: 202, data: WARMING },
      ...Array(MAX_CONSECUTIVE_BLIPS + 1).fill(gateway),
    ]);
    await expect(poll(read).done).resolves.toBe(gateway);
    expect(read).toHaveBeenCalledTimes(MAX_CONSECUTIVE_BLIPS + 2);
  });

  it('gives up at the deadline rather than spinning forever', async () => {
    const read = vi.fn(async () => ({ status: 202, data: WARMING }));
    await expect(poll(read, { timeoutMs: 20000 }).done).rejects.toBeInstanceOf(
      WarmingTimeout,
    );
    expect(read.mock.calls.length).toBeLessThan(7);
  });
});

describe('warmingFrom: the fill progress', () => {
  it('carries the progress the server reports', () => {
    const progress = {
      status: 'running',
      message: 'm',
      processed: 3,
      total: 9,
    };
    expect(warmingFrom(202, { ...WARMING, progress }).progress).toEqual(
      progress,
    );
  });
});
