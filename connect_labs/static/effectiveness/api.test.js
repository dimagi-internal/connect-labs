// What the page does to saved scenarios and groups, once, for the buttons and the canopy agent alike (./api.js).
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';

globalThis.window = globalThis.window || globalThis;
globalThis.document = {
  cookie: '',
  getElementById: (id) =>
    id === 'scenario-data'
      ? {
          textContent: JSON.stringify({
            scenarios_url: '/labs/cea/scenarios/',
            groups_url: '/labs/cea/groups/',
            csrf: 'tok',
          }),
        }
      : null,
};
// eslint-disable-next-line no-eval
eval(readFileSync(new URL('./api.js', import.meta.url), 'utf8'));
const A = window.EffApi;

let calls;
function reply(status, body) {
  globalThis.fetch = vi.fn((url, init) => {
    calls.push({ url, ...init });
    return Promise.resolve({
      ok: status < 400,
      status,
      json: () => Promise.resolve(body),
    });
  });
}
beforeEach(() => {
  calls = [];
  window.confirm = () => true;
});

describe('reordered', () => {
  const order = [10, 11, 12, 13];
  it('moves a scenario to a place, one place earlier, or one later', () => {
    expect(A.reordered(order, 13, 1)).toEqual([13, 10, 11, 12]);
    expect(A.reordered(order, 12, 'earlier')).toEqual([10, 12, 11, 13]);
    expect(A.reordered(order, 11, 'later')).toEqual([10, 12, 11, 13]);
  });
  it('does not move past either end, and refuses a scenario that is not there', () => {
    expect(A.reordered(order, 10, 'earlier')).toEqual(order);
    expect(A.reordered(order, 13, 'later')).toEqual(order);
    expect(A.reordered(order, 10, 99)).toEqual([11, 12, 13, 10]);
    expect(() => A.reordered(order, 99, 1)).toThrow(/not in this group/);
  });
});

describe('the calls', () => {
  it('send the page’s token and the server’s answer comes back as data', async () => {
    reply(200, { id: 7, name: 'x' });
    const got = await A.update(7, { note: 'n' });
    expect(got).toEqual({ id: 7, name: 'x' });
    expect(calls[0].url).toBe('/labs/cea/scenarios/7/');
    expect(calls[0].method).toBe('PATCH');
    expect(calls[0].headers['X-CSRFToken']).toBe('tok');
    expect(JSON.parse(calls[0].body)).toEqual({ note: 'n' });
  });

  it('move a scenario into a group, or out with null, by the same patch', async () => {
    reply(200, {});
    await A.moveToGroup(7, 3);
    await A.moveToGroup(7, null);
    expect(calls.map((c) => JSON.parse(c.body))).toEqual([
      { group: 3 },
      { group: null },
    ]);
  });

  it('reorder a group by sending its whole order', async () => {
    reply(200, {});
    await A.arrange(3, [12, 10, 11]);
    expect(calls[0].url).toBe('/labs/cea/groups/3/');
    expect(JSON.parse(calls[0].body)).toEqual({ scenario_ids: [12, 10, 11] });
  });

  it('reject with the server’s own words and its status', async () => {
    reply(409, { error: 'You already have a scenario called "x".' });
    await expect(A.save({ name: 'x' })).rejects.toMatchObject({
      message: 'You already have a scenario called "x".',
      status: 409,
    });
  });
});

describe('deleting', () => {
  it('asks first, and sends nothing when the person says no', async () => {
    window.confirm = vi.fn(() => false);
    reply(200, {});
    expect(await A.removeScenario(7, 'One')).toBeNull();
    expect(await A.removeGroup(3, 'G')).toBeNull();
    expect(window.confirm).toHaveBeenCalledTimes(2);
    expect(calls).toEqual([]);
  });

  it('deletes a scenario or a group when they say yes', async () => {
    reply(200, { deleted: 7 });
    await A.removeScenario(7, 'One');
    await A.removeGroup(3, 'G');
    expect(calls.map((c) => [c.method, c.url])).toEqual([
      ['DELETE', '/labs/cea/scenarios/7/'],
      ['DELETE', '/labs/cea/groups/3/'],
    ]);
  });
});
