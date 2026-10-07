// "Where it went": one week of the stock_flow payload laid out as boxes and ribbons.
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The file attaches to window rather than exporting, as the page loads it.
const here = path.dirname(fileURLToPath(import.meta.url));
new Function(fs.readFileSync(path.join(here, 'flow.js'), 'utf8'))();
const { layout, tiles, ordered, closeUp } = globalThis.SupplyFlow;

// Invented: 300 arrive; the store hands 150 to each of two workers in week 1;
// in week 2 one gives 40 out and the other 30.
const DATA = {
  weeks: ['2026-09-07', '2026-09-14', '2026-09-21'],
  as_of: '2026-09-27',
  unit: 'sachet',
  nodes: [
    {
      id: 'unlinked',
      name: 'Received, no order linked',
      kind: 'source',
      column: 0,
    },
    { id: 'p1', name: 'Partner store', kind: 'store', column: 1, parent: null },
    { id: 'p3', name: 'worker-zebu', kind: 'worker', column: 2, parent: 'p1' },
    {
      id: 'p2',
      name: 'worker-acacia',
      kind: 'worker',
      column: 2,
      parent: 'p1',
    },
    { id: 'given', name: 'Given out at visits', kind: 'sink', column: 3 },
  ],
  links: [
    { source: 'unlinked', target: 'p1', series: [300, 300, 300] },
    { source: 'p1', target: 'p2', series: [0, 150, 150] },
    { source: 'p1', target: 'p3', series: [0, 150, 150] },
    { source: 'p2', target: 'given', series: [0, 0, 40] },
    { source: 'p3', target: 'given', series: [0, 0, 30] },
  ],
};

describe('tiles', () => {
  it('says where every sachet is at the end of each week', () => {
    expect(tiles(DATA, 0)).toEqual({
      arrived: 300,
      stores: 300,
      workers: 0,
      given: 0,
      lost: 0,
    });
    expect(tiles(DATA, 2)).toEqual({
      arrived: 300,
      stores: 0,
      workers: 230,
      given: 70,
      lost: 0,
    });
  });
});

describe('ordered', () => {
  it('puts workers by name under their store, never by how they are doing', () => {
    const cols = ordered(DATA);
    expect(cols[2].map((n) => n.name)).toEqual([
      'worker-acacia',
      'worker-zebu',
    ]);
  });
});

describe('layout', () => {
  it('keeps one scale for every week, so the picture fills up as it plays', () => {
    const early = layout(DATA, 0, { width: 900, height: 600 });
    const late = layout(DATA, 2, { width: 900, height: 600 });
    expect(early.k).toBe(late.k);
    expect(early.boxes.p2.h).toBe(0);
    expect(late.boxes.p2.h).toBeCloseTo(150 * late.k);
  });

  it('hatches what a worker still holds and stacks ribbons without overlap', () => {
    const L = layout(DATA, 2, { width: 900, height: 600 });
    expect(L.boxes.p2.heldH).toBeCloseTo(110 * L.k);
    const out = L.ribbons
      .filter((r) => r.link.source === 'p1')
      .sort((a, b) => a.y0 - b.y0);
    expect(out[0].y0).toBe(L.boxes.p1.y);
    expect(out[1].y0).toBeCloseTo(out[0].y0 + out[0].h);
  });

  it('draws nothing along a route that has moved nothing yet', () => {
    const L = layout(DATA, 0, { width: 900, height: 600 });
    expect(L.ribbons.map((r) => r.link.target)).toEqual(['p1']);
  });
});

describe('closeUp', () => {
  it('keeps the store that restocks workers, the workers and the ends -- not the arrivals', () => {
    const near = closeUp(DATA);
    expect(near.nodes.map((n) => n.id).sort()).toEqual([
      'given',
      'p1',
      'p2',
      'p3',
    ]);
    expect(near.links.some((l) => l.source === 'unlinked')).toBe(false);
  });

  it('scales to those columns alone, so each worker is drawn bigger', () => {
    const whole = layout(DATA, 2, { width: 900, height: 600 });
    const near = layout(closeUp(DATA), 2, { width: 900, height: 600 });
    expect(near.boxes.p2.h).toBeGreaterThanOrEqual(whole.boxes.p2.h);
  });
});
