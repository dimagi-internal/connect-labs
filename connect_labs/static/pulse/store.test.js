/**
 * Store transport behaviour.
 *
 * Pausing a wall display exists so someone can stop and point at a frame. In
 * replay that is free — the clock stops. In live mode the server keeps
 * producing services regardless, so "pause" has to decide what happens to
 * arrivals, and dropping them would leave the totals disagreeing with the
 * ticker the moment you resumed.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

function loadStore() {
  const src = fs.readFileSync(path.join(here, 'store.js'), 'utf8');
  const win = {};
  new Function('window', src)(win);
  return win.PulseStore;
}

const FIELDS = [
  'visit_id',
  'field_ts',
  'sync_ts',
  'lat',
  'lon',
  'opportunity_id',
  'status',
  'flag_type',
  'country',
  'service_slug',
  'worker',
  'usd',
];
const row = (id) => [
  id,
  1700000000 + id,
  1700000000 + id,
  11.0,
  7.6,
  765,
  'approved',
  null,
  'NG',
  'mbw',
  'abc123',
  0.7,
];

describe('pause in live mode', () => {
  let PulseStore, store;

  beforeEach(() => {
    PulseStore = loadStore();
    store = new PulseStore({ mode: 'live' });
    store.fields = FIELDS;
  });

  it('holds arrivals while paused instead of dropping them', () => {
    const delivered = [];
    store._deliver = (ev) => delivered.push(ev.visit_id);

    store.playing = false;
    store._heldLive.push(
      store._decode(row(1), FIELDS),
      store._decode(row(2), FIELDS),
    );
    expect(delivered).toEqual([]);

    store.toggle(); // resume
    expect(delivered).toEqual([1, 2]);
    expect(store._heldLive).toHaveLength(0);
  });

  it('delivers in arrival order, so the ticker does not jumble on resume', () => {
    const delivered = [];
    store._deliver = (ev) => delivered.push(ev.visit_id);
    store.playing = false;
    for (const id of [5, 6, 7])
      store._heldLive.push(store._decode(row(id), FIELDS));
    store.toggle();
    expect(delivered).toEqual([5, 6, 7]);
  });

  it('pausing twice does not replay what was already delivered', () => {
    const delivered = [];
    store._deliver = (ev) => delivered.push(ev.visit_id);
    store.playing = false;
    store._heldLive.push(store._decode(row(9), FIELDS));
    store.toggle(); // resume -> flush 9
    store.toggle(); // pause again
    store.toggle(); // resume -> nothing held
    expect(delivered).toEqual([9]);
  });
});

describe('switching source', () => {
  it('discards anything held from the previous live session', async () => {
    const PulseStore = loadStore();
    const store = new PulseStore({ mode: 'live' });
    store.fields = FIELDS;
    store._heldLive.push(store._decode(row(3), FIELDS));
    store.loadReplayWindow = vi.fn().mockResolvedValue(undefined);

    await store.setMode('replay');

    expect(store._heldLive).toHaveLength(0);
    expect(store.loadReplayWindow).toHaveBeenCalled();
    expect(store.mode).toBe('replay');
  });

  it('is a no-op when already in that mode', async () => {
    const PulseStore = loadStore();
    const store = new PulseStore({ mode: 'replay' });
    store.loadReplayWindow = vi.fn();
    await store.setMode('replay');
    expect(store.loadReplayWindow).not.toHaveBeenCalled();
  });
});

describe('canClaimLive', () => {
  it('needs BOTH live mode and the server saying ingest is healthy', () => {
    const PulseStore = loadStore();
    const store = new PulseStore({ mode: 'live' });
    store.ingest = { live_ok: false };
    expect(store.canClaimLive).toBe(false);
    store.ingest = { live_ok: true };
    expect(store.canClaimLive).toBe(true);
    store.mode = 'replay';
    expect(store.canClaimLive).toBe(false);
  });
});
