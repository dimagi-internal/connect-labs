// @vitest-environment happy-dom
// What render code keeps in localStorage / sessionStorage in the sandbox frame: the
// frame's copy answers at once, the host keeps it for that one page or workflow.
// Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Storage".
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  STORAGE_PREFIX,
  checkStorageBatch,
  createDispatcher,
  scopedStorage,
} from './host';
import {
  MemoryStorage,
  createStoragePersister,
  flushingStorageFirst,
  installMemoryStorage,
} from './frame';
import { LIMITS } from './protocol';

const LABS = 'https://labs.connect.dimagi.com';

function hostHandlers(overrides = {}) {
  return {
    querySupply: vi.fn(async () => ({})),
    queryWorkflow: vi.fn(async () => ({})),
    navigate: vi.fn(),
    shareSelection: vi.fn(),
    resize: vi.fn(),
    scroll: vi.fn(),
    reveal: vi.fn(),
    copyText: vi.fn(async () => undefined),
    confirm: vi.fn(async () => true),
    download: vi.fn(),
    reportError: vi.fn(),
    ...overrides,
  };
}

/** A frame's window: an opaque origin, where storage throws. */
function opaque() {
  const win = {};
  for (const name of ['localStorage', 'sessionStorage']) {
    Object.defineProperty(win, name, {
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
      configurable: true,
    });
  }
  return win;
}

function batch(over = {}) {
  return { area: 'local', clear: false, set: {}, remove: [], ...over };
}

// The host page's window: only its two Storage objects are read.
let window;
beforeEach(() => {
  window = {
    localStorage: new MemoryStorage(),
    sessionStorage: new MemoryStorage(),
  };
});

describe('the host keeps each page and workflow its own storage', () => {
  it('reads back only its own keys, without the prefix', () => {
    window.localStorage.setItem(`${STORAGE_PREFIX}7:filter`, 'open');
    window.localStorage.setItem(`${STORAGE_PREFIX}77:filter`, 'other');
    window.localStorage.setItem('labs-own-setting', 'secret');
    window.sessionStorage.setItem(`${STORAGE_PREFIX}7:tab`, 'map');
    expect(scopedStorage(window, '7').seed()).toEqual({
      local: { filter: 'open' },
      session: { tab: 'map' },
    });
  });

  it('writes, removes and clears under its own prefix only', () => {
    window.localStorage.setItem('labs-own-setting', 'keep');
    window.localStorage.setItem(`${STORAGE_PREFIX}8:x`, 'keep');
    const store = scopedStorage(window, '7');
    store.apply(batch({ set: { a: '1', b: '2' } }));
    expect(window.localStorage.getItem(`${STORAGE_PREFIX}7:a`)).toBe('1');
    store.apply(batch({ remove: ['a'] }));
    expect(window.localStorage.getItem(`${STORAGE_PREFIX}7:a`)).toBeNull();
    store.apply(batch({ clear: true, set: { c: '3' } }));
    expect(store.seed().local).toEqual({ c: '3' });
    // Nobody else's keys were touched.
    expect(window.localStorage.getItem('labs-own-setting')).toBe('keep');
    expect(window.localStorage.getItem(`${STORAGE_PREFIX}8:x`)).toBe('keep');
  });

  it('a key cannot climb out of its prefix', () => {
    scopedStorage(window, '7').apply(batch({ set: { '../labs-own': 'x' } }));
    expect(window.localStorage.getItem('../labs-own')).toBeNull();
    expect(window.localStorage.getItem('labs-own')).toBeNull();
    expect(window.localStorage.getItem(`${STORAGE_PREFIX}7:../labs-own`)).toBe(
      'x',
    );
  });

  it('keeps nothing without a scope', () => {
    const store = scopedStorage(window, null);
    store.apply(batch({ set: { a: '1' } }));
    expect(window.localStorage.length).toBe(0);
    expect(store.seed()).toEqual({ local: {}, session: {} });
  });

  it('keeps nothing, and does not throw, where the host storage throws', () => {
    const win = {};
    Object.defineProperty(win, 'localStorage', {
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
    });
    Object.defineProperty(win, 'sessionStorage', {
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
    });
    const store = scopedStorage(win, '7');
    expect(store.seed()).toEqual({ local: {}, session: {} });
    expect(() => store.apply(batch({ set: { a: '1' } }))).not.toThrow();
  });

  it('refuses more than one page may keep, and leaves what it had', () => {
    const store = scopedStorage(window, '7');
    store.apply(batch({ set: { a: 'small' } }));
    const big = 'x'.repeat(LIMITS.storageBytes);
    expect(() => store.apply(batch({ set: { b: big } }))).toThrow(
      /too much stored/,
    );
    expect(store.seed().local).toEqual({ a: 'small' });
  });

  it('refuses once every sandboxed page together has used its share', () => {
    const share = Math.floor(LIMITS.storageBytes * 0.9);
    const pages = Math.ceil(LIMITS.storageTotalBytes / share);
    let refused = 0;
    for (let i = 1; i <= pages + 1; i += 1) {
      try {
        scopedStorage(window, String(i)).apply(
          batch({ set: { v: 'x'.repeat(share) } }),
        );
      } catch (e) {
        expect(e.message).toMatch(/no room left/);
        refused += 1;
      }
    }
    expect(refused).toBeGreaterThan(0);
  });
});

describe('the storage request', () => {
  it('takes strings only, in a known area', () => {
    expect(() => checkStorageBatch({ area: 'cookies' })).toThrow(/area/);
    expect(() => checkStorageBatch({ area: 'local', set: { a: 1 } })).toThrow(
      /string/,
    );
    expect(() => checkStorageBatch({ area: 'local', remove: 'a' })).toThrow(
      /list/,
    );
    expect(() =>
      checkStorageBatch({
        area: 'local',
        set: { ['k'.repeat(LIMITS.storageKeyLength + 1)]: 'v' },
      }),
    ).toThrow(/key/);
    expect(
      checkStorageBatch({ area: 'session', set: { a: 'b' }, remove: ['c'] }),
    ).toEqual({
      area: 'session',
      clear: false,
      set: { a: 'b' },
      remove: ['c'],
    });
  });

  it('is answered on a page, and never crowds out its other requests', async () => {
    const storage = vi.fn();
    const dispatch = createDispatcher(hostHandlers({ storage }), {
      labsOrigin: LABS,
      hasActivation: () => true,
      mode: 'page',
    });
    for (let i = 0; i < 200; i += 1) {
      const out = await dispatch({
        id: i,
        method: 'storage',
        params: { area: 'local', set: { n: String(i) } },
      });
      expect(out).toEqual({ id: i, result: true });
    }
    expect(storage).toHaveBeenCalledTimes(200);
    const after = await dispatch({
      id: 'q',
      method: 'querySupply',
      params: { alias: 'stock' },
    });
    expect(after.error).toBeUndefined();
  });

  it('a value larger than the params cap of a page still fits', async () => {
    const storage = vi.fn();
    const dispatch = createDispatcher(hostHandlers({ storage }), {
      labsOrigin: LABS,
      hasActivation: () => true,
    });
    const value = 'x'.repeat(LIMITS.paramsBytes * 2);
    const out = await dispatch({
      id: 1,
      method: 'storage',
      params: { area: 'local', set: { big: value } },
    });
    expect(out.result).toBe(true);
    expect(storage.mock.calls[0][0].set.big).toBe(value);
  });
});

describe('the frame', () => {
  it('starts from what the host kept, and reports changes but not the seed', () => {
    const { local } = installMemoryStorage(opaque());
    const changes = [];
    local.seed({ filter: 'open' });
    local.onChange = (c) => changes.push(c);
    expect(local.getItem('filter')).toBe('open');
    local.setItem('filter', 2);
    local.removeItem('filter');
    local.clear();
    expect(changes).toEqual([
      { op: 'set', key: 'filter', value: '2' },
      { op: 'remove', key: 'filter' },
      { op: 'clear' },
    ]);
    expect(local).toBeInstanceOf(MemoryStorage);
  });

  it('batches changes per area, and a clear drops what came before it', () => {
    vi.useFakeTimers();
    const sent = [];
    const p = createStoragePersister((b) => sent.push(b));
    p.record('local', { op: 'set', key: 'a', value: '1' });
    p.record('local', { op: 'set', key: 'a', value: '2' });
    p.record('local', { op: 'remove', key: 'b' });
    p.record('session', { op: 'set', key: 'x', value: 'y' });
    expect(sent).toEqual([]);
    vi.advanceTimersByTime(60);
    expect(sent).toEqual([
      { area: 'local', clear: false, set: { a: '2' }, remove: ['b'] },
      { area: 'session', clear: false, set: { x: 'y' }, remove: [] },
    ]);
    sent.length = 0;
    p.record('local', { op: 'set', key: 'gone', value: '1' });
    p.record('local', { op: 'clear' });
    p.record('local', { op: 'set', key: 'kept', value: '1' });
    p.flush();
    expect(sent).toEqual([
      { area: 'local', clear: true, set: { kept: '1' }, remove: [] },
    ]);
    vi.useRealTimers();
  });

  it('sends what is waiting before any other request (set, then navigate)', () => {
    const calls = [];
    const raw = {
      request: vi.fn(async (method, params) => {
        calls.push([method, params]);
        return true;
      }),
    };
    const p = createStoragePersister((b) => raw.request('storage', b));
    const bridge = flushingStorageFirst(raw, p);
    p.record('session', { op: 'set', key: 'statusFilter', value: '["a"]' });
    bridge.request('navigate', { url: '/labs/commcare/initiate/' });
    expect(calls.map((c) => c[0])).toEqual(['storage', 'navigate']);
    expect(calls[0][1].set).toEqual({ statusFilter: '["a"]' });
  });
});

describe('end to end: what one load stores, the next load reads', () => {
  it('survives a reload, for this workflow only', async () => {
    const host = scopedStorage(window, '21440');
    const dispatch = createDispatcher(
      hostHandlers({ storage: (b) => host.apply(b) }),
      { labsOrigin: LABS, hasActivation: () => true },
    );
    // First load: render code stores a ward filter.
    const first = installMemoryStorage(opaque()).local;
    first.seed(host.seed().local);
    const p = createStoragePersister((b) =>
      dispatch({ id: 1, method: 'storage', params: b }),
    );
    first.onChange = (c) => p.record('local', c);
    first.setItem('wards', '["Gwandu"]');
    p.flush();
    await Promise.resolve();
    // Reload: a fresh frame, seeded by the host.
    const second = installMemoryStorage(opaque()).local;
    second.seed(scopedStorage(window, '21440').seed().local);
    expect(second.getItem('wards')).toBe('["Gwandu"]');
    // Another workflow sees none of it.
    expect(scopedStorage(window, '21458').seed().local).toEqual({});
  });
});
