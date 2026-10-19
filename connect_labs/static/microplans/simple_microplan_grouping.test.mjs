/*
 * Tests for window.SimpleMicroplanGrouping (simple_microplan_grouping.js).
 * Run: node --test connect_labs/static/microplans/simple_microplan_grouping.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

global.window = {};
const here = dirname(fileURLToPath(import.meta.url));
await import('file://' + join(here, 'simple_microplan_grouping.js'));
const G = window.SimpleMicroplanGrouping;

// a 50 m square cell at grid position (ix, iy), weight = buildings
function cell(ix, iy, weight) {
  const x0 = ix * 50,
    y0 = iy * 50;
  return {
    id: ix + ',' + iy,
    ring: [
      [x0, y0],
      [x0 + 50, y0],
      [x0 + 50, y0 + 50],
      [x0, y0 + 50],
      [x0, y0],
    ],
    weight,
    x: x0 + 25,
    y: y0 + 25,
  };
}
function grid(nx, ny, weightAt) {
  const out = [];
  for (let ix = 0; ix < nx; ix++)
    for (let iy = 0; iy < ny; iy++) out.push(cell(ix, iy, weightAt(ix, iy)));
  return out;
}

test('grid cells share boundaries with edge neighbours, not corner neighbours', async () => {
  const adj = await G.buildAdjacency(grid(3, 3, () => 1));
  assert.deepEqual([...adj.get('1,1')].sort(), ['0,1', '1,0', '1,2', '2,1']);
  assert.ok(!adj.get('0,0').has('1,1'));
});

test('edges a float-hair apart still count as shared', async () => {
  const a = cell(0, 0, 1);
  const b = cell(1, 0, 1);
  b.ring = b.ring.map(([x, y]) => [x + 0.0003, y - 0.0002]);
  const adj = await G.buildAdjacency([a, b]);
  assert.ok(adj.get(a.id).has(b.id));
});

test('every group lands in target ± 10% and is one connected piece', async () => {
  // 10 x 10 cells with 5..20 buildings each, deterministic pseudo-random
  const items = grid(10, 10, (x, y) => 5 + ((x * 7 + y * 13) % 16));
  const adj = await G.buildAdjacency(items);
  const res = await G.groupByBuildings(items, adj, {
    target: 100,
    tolerance: 0.1,
  });
  const grouped = res.groups.flat();
  assert.equal(grouped.length, items.length, 'every work area is grouped once');
  assert.equal(new Set(grouped).size, items.length);
  res.groups.forEach((ids) =>
    assert.ok(G.isConnected(ids, adj), 'group is connected: ' + ids.join(' ')),
  );
  const ok = res.flags.filter((f) => f === 'ok').length;
  assert.ok(
    ok / res.groups.length >= 0.8,
    'most groups in range, got ' + ok + '/' + res.groups.length,
  );
  res.groups.forEach((ids, i) => {
    if (res.flags[i] === 'ok')
      assert.ok(res.sums[i] >= 90 && res.sums[i] <= 110, 'sum ' + res.sums[i]);
  });
});

test('an island that cannot reach the target is kept whole and flagged, never joined across a gap', async () => {
  const main = grid(4, 1, () => 50); // 4 cells in a row, 200 buildings
  const island = cell(10, 10, 30); // far away, alone
  const items = [...main, island];
  const adj = await G.buildAdjacency(items);
  const res = await G.groupByBuildings(items, adj, {
    target: 100,
    tolerance: 0.1,
  });
  const gi = res.groups.findIndex((ids) => ids.includes(island.id));
  assert.deepEqual(res.groups[gi], [island.id]);
  assert.equal(res.flags[gi], 'under');
  res.groups.forEach((ids) => assert.ok(G.isConnected(ids, adj)));
});

test('a work area above the range on its own is its own group, flagged over', async () => {
  const items = [cell(0, 0, 500), cell(1, 0, 50), cell(2, 0, 50)];
  const adj = await G.buildAdjacency(items);
  const res = await G.groupByBuildings(items, adj, {
    target: 100,
    tolerance: 0.1,
  });
  const gi = res.groups.findIndex((ids) => ids.includes('0,0'));
  assert.deepEqual(res.groups[gi], ['0,0']);
  assert.equal(res.flags[gi], 'over');
});

test('zero-building areas join a neighbouring group without changing its total', async () => {
  const items = [cell(0, 0, 50), cell(1, 0, 50), cell(2, 0, 0)];
  const adj = await G.buildAdjacency(items);
  const res = await G.groupByBuildings(items, adj, {
    target: 100,
    tolerance: 0.1,
  });
  assert.equal(res.groups.length, 1);
  assert.equal(res.sums[0], 100);
  assert.equal(res.flags[0], 'ok');
});

test('starting corner changes the seed order', () => {
  const items = grid(2, 2, () => 1);
  assert.equal(G.seedOrder(items, 'top-left')[0].id, '0,1');
  assert.equal(G.seedOrder(items, 'bottom-right')[0].id, '1,0');
});

test('best-of-corners is at least as good as any single corner', async () => {
  const items = [];
  for (let x = 0; x < 30; x++)
    for (let y = 0; y < 20; y++) items.push(cell(x, y, (x * 31 + y * 17) % 23));
  const adj = await G.buildAdjacency(items);
  const okCount = (r) => r.flags.filter((f) => f === 'ok').length;
  const best = await G.groupByBuildings(items, adj, {
    target: 100,
    tolerance: 0.1,
    corner: 'best',
  });
  for (const corner of Object.keys(G.CORNERS)) {
    const one = await G.groupByBuildings(items, adj, {
      target: 100,
      tolerance: 0.1,
      corner,
    });
    assert.ok(okCount(best) >= okCount(one), corner);
  }
  assert.ok(Object.keys(G.CORNERS).includes(best.corner));
  best.groups.forEach((ids) => assert.ok(G.isConnected(ids, adj)));
});

test('boundary-to-boundary distance', () => {
  assert.equal(G.ringDistance(cell(0, 0, 1), cell(1, 0, 1)), 0); // touching
  assert.equal(G.ringDistance(cell(0, 0, 1), cell(3, 0, 1)), 100); // two empty cells between
});

test('a lone work area joins the group of the nearest work area within the radius', () => {
  const items = [
    cell(0, 0, 10),
    cell(1, 0, 10),
    cell(4, 0, 5),
    cell(20, 20, 5),
  ];
  // (4,0) is 100 m from (1,0); (20,20) is far from everything
  const groups = [['0,0', '1,0'], ['4,0'], ['20,20']];
  const moves = G.absorbSingletons(items, groups, { radius: 200 });
  assert.deepEqual(
    moves.map((m) => m.id),
    ['4,0'],
  );
  assert.deepEqual(groups, [['0,0', '1,0', '4,0'], ['20,20']]);
});

test('radius 0 turns it off, and a lone area over the limit stays alone', () => {
  const items = [cell(0, 0, 10), cell(1, 0, 10), cell(3, 0, 500)];
  const g1 = [['0,0', '1,0'], ['3,0']];
  assert.deepEqual(G.absorbSingletons(items, g1, { radius: 0 }), []);
  const g2 = [['0,0', '1,0'], ['3,0']];
  assert.deepEqual(
    G.absorbSingletons(items, g2, { radius: 200, high: 110 }),
    [],
  );
  assert.equal(g2.length, 2);
});

test('prefers a nearby group that stays within the range over a nearer full one', () => {
  const items = [cell(0, 0, 100), cell(2, 0, 10), cell(5, 0, 50)];
  // lone (2,0) is 50 m from the full group (0,0) and 100 m from (5,0)
  const groups = [['0,0'], ['2,0'], ['5,0']];
  groups[0].push('x'); // make group 0 not a singleton
  items.push({ id: 'x', ring: cell(0, 1, 0).ring, weight: 0, x: 0, y: 0 });
  G.absorbSingletons(items, groups, { radius: 200, high: 110 });
  assert.ok(groups.find((g) => g.includes('5,0')).includes('2,0'));
});

test('by buildings, a lone work area already in range stays its own WAG', () => {
  const items = [cell(0, 0, 50), cell(1, 0, 50), cell(2, 0, 100)];
  const groups = [['0,0', '1,0'], ['2,0']];
  assert.deepEqual(
    G.absorbSingletons(items, groups, { radius: 200, low: 90, high: 110 }),
    [],
  );
  assert.equal(groups.length, 2);
});

// grid cells for mergeSmallCells: [ix, iy, buildings]
const gridCells = (list) =>
  list.map(([ix, iy, count]) => ({ id: ix + ',' + iy, ix, iy, count }));

test('minimum buildings: 0 leaves every cell its own work area', () => {
  const cells = gridCells([
    [0, 0, 1],
    [1, 0, 2],
  ]);
  const m = G.mergeSmallCells(cells, 0);
  assert.deepEqual(m.groups, [['0,0'], ['1,0']]);
  assert.equal(m.short, 0);
});

test('minimum buildings: short cells join a side neighbour, keeping every building', () => {
  // a row of 5 cells, min 5: 2+3 pair up on the right; 1+2 make only 3, so
  // they go on to join the 9
  const cells = gridCells([
    [0, 0, 1],
    [1, 0, 2],
    [2, 0, 9],
    [3, 0, 2],
    [4, 0, 3],
  ]);
  const m = G.mergeSmallCells(cells, 5);
  const counts = new Map(cells.map((c) => [c.id, c.count]));
  const sums = m.groups.map((g) => g.reduce((s, id) => s + counts.get(id), 0));
  assert.ok(
    sums.every((s) => s >= 5),
    JSON.stringify(m.groups),
  );
  assert.equal(
    sums.reduce((a, b) => a + b, 0),
    17,
  );
  assert.deepEqual(m.groups.flat().sort(), cells.map((c) => c.id).sort());
  assert.equal(m.short, 0);
  assert.deepEqual(m.groups, [
    ['0,0', '1,0', '2,0'],
    ['3,0', '4,0'],
  ]);
});

test('minimum buildings: never joins across a corner, and reports an isolated short cell', () => {
  const cells = gridCells([
    [0, 0, 1],
    [1, 1, 9],
  ]);
  const m = G.mergeSmallCells(cells, 5);
  assert.deepEqual(m.groups, [['0,0'], ['1,1']]);
  assert.equal(m.short, 1);
});

test('minimum buildings: a merged ring takes in the cell it encloses and fills empty spots', () => {
  // eight 1-building cells around a full cell at (1,1); a gap at (3,1) is enclosed
  // once the ring closes around it too
  const ring = [];
  for (let ix = 0; ix <= 4; ix++)
    for (let iy = 0; iy <= 2; iy++) {
      if (ix === 1 && iy === 1) continue;
      if (ix === 3 && iy === 1) continue;
      ring.push([ix, iy, 1]);
    }
  const cells = gridCells(ring.concat([[1, 1, 50]]));
  const m = G.mergeSmallCells(cells, 13);
  const withCentre = m.groups.find((g) => g.includes('1,1'));
  assert.ok(withCentre.includes('0,0'), 'the enclosed cell joined the ring');
  const g = m.groups.indexOf(withCentre);
  assert.deepEqual(m.fill[g], [[3, 1]]);
  // no cell is in two work areas
  const all = m.groups.flat();
  assert.equal(new Set(all).size, all.length);
  assert.equal(all.length, cells.length);
});
