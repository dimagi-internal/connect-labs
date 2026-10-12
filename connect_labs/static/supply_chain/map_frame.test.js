// What the portfolio map frames when it opens, and when field workers show.
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The file attaches to window rather than exporting, as the page loads it.
const here = path.dirname(fileURLToPath(import.meta.url));
new Function(fs.readFileSync(path.join(here, 'map_frame.js'), 'utf8'))();
const { frameTargets, workerMinZoom } = globalThis.SupplyMapFrame;

const store = { _key: '1:1', kind: 'central_store' };
const worker = { _key: '1:2', kind: 'user_held' };
const kano = { _key: 's:9', name: 'A supplier in Kano' };

describe('frameTargets', () => {
  it('frames the places, not the suppliers an order comes from', () => {
    // A supplier 500 km away pulled the frame out to zoom 6, where the
    // workers' layer is hidden: the map showed a route and no workers.
    expect(frameTargets([store, worker], [kano])).toEqual([store, worker]);
  });
  it('falls back to the suppliers when no place is on the map', () => {
    expect(frameTargets([], [kano])).toEqual([kano]);
  });
  it('frames nothing when there is nothing', () => {
    expect(frameTargets([], [])).toEqual([]);
  });
});

describe('workerMinZoom', () => {
  it("shows one programme's workers at every zoom", () => {
    expect(workerMinZoom(1)).toBe(0);
  });
  it("keeps several programmes' workers for when the map is zoomed in", () => {
    expect(workerMinZoom(3)).toBe(9);
  });
});

describe('workersLine', () => {
  const at = (source) => ({
    kind: 'user_held',
    _placed: source !== null,
    location: { source: source || '' },
  });
  const { workersLine } = globalThis.SupplyMapFrame;
  it("says one programme's workers are drawn where their visits are", () => {
    expect(workersLine([at('visits'), at('visits')], 1)).toBe(
      '2 field workers, drawn where their visits are.',
    );
  });
  it('says how many have no GPS on their visits and where they are drawn instead', () => {
    expect(workersLine([at('visits'), at('parent'), at('org_hq')], 1)).toBe(
      '3 field workers, drawn where their visits are. 2 have no GPS on their visits and are drawn at their store or office.',
    );
  });
  it('counts the ones with no location at all, which the panel lists', () => {
    expect(workersLine([at('visits'), at(null)], 1)).toBe(
      '2 field workers, drawn where their visits are. 1 has no location and is listed below.',
    );
  });
  it("treats a recorded location as the worker's own, not a stand-in", () => {
    expect(workersLine([at('recorded')], 1)).toBe(
      '1 field worker, drawn where their visits are.',
    );
  });
  it('across programmes, asks for a closer zoom', () => {
    expect(workersLine([at('visits')], 2)).toBe(
      '1 field worker — zoom in to see them.',
    );
  });
  it('says nothing when there are no workers', () => {
    expect(workersLine([], 1)).toBe('');
  });
});
