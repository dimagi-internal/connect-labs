/* Simple Microplan: auto-grouping work areas into WAGs.
   Pure logic (no map, no DOM) on window.SimpleMicroplanGrouping, so it is
   unit-tested under node (simple_microplan_grouping.test.mjs).

   An item is { id, ring: [[x, y], ...] in metres, weight: buildings, x, y }
   where (x, y) is its centroid. Every function is async and yields to the
   page now and then (opts.tick) so a progress bar can repaint. */
(function () {
  'use strict';

  const noTick = () => Promise.resolve();

  // Edges closer than EPS metres count as the same line; they must run together
  // for at least MIN_SHARED metres to be a shared boundary (a shared corner is not).
  const EPS = 0.5;
  const MIN_SHARED = 1;

  function bboxOf(ring) {
    let a = Infinity,
      b = Infinity,
      c = -Infinity,
      d = -Infinity;
    for (const [x, y] of ring) {
      if (x < a) a = x;
      if (y < b) b = y;
      if (x > c) c = x;
      if (y > d) d = y;
    }
    return [a, b, c, d];
  }

  // Length along which segment q lies on segment p (0 if they are not collinear).
  function sharedLength(p1, p2, q1, q2) {
    const dx = p2[0] - p1[0],
      dy = p2[1] - p1[1];
    const L = Math.hypot(dx, dy);
    if (L < 1e-9) return 0;
    const ux = dx / L,
      uy = dy / L;
    const off = (q) => Math.abs((q[0] - p1[0]) * uy - (q[1] - p1[1]) * ux);
    if (off(q1) > EPS || off(q2) > EPS) return 0;
    const t1 = (q1[0] - p1[0]) * ux + (q1[1] - p1[1]) * uy;
    const t2 = (q2[0] - p1[0]) * ux + (q2[1] - p1[1]) * uy;
    return Math.max(
      0,
      Math.min(L, Math.max(t1, t2)) - Math.max(0, Math.min(t1, t2)),
    );
  }

  function shareBoundary(a, b) {
    const ra = a.ring,
      rb = b.ring;
    for (let i = 0; i + 1 < ra.length; i++) {
      const p1 = ra[i],
        p2 = ra[i + 1];
      const minX = Math.min(p1[0], p2[0]) - EPS,
        maxX = Math.max(p1[0], p2[0]) + EPS;
      const minY = Math.min(p1[1], p2[1]) - EPS,
        maxY = Math.max(p1[1], p2[1]) + EPS;
      for (let j = 0; j + 1 < rb.length; j++) {
        const q1 = rb[j],
          q2 = rb[j + 1];
        if (Math.max(q1[0], q2[0]) < minX || Math.min(q1[0], q2[0]) > maxX)
          continue;
        if (Math.max(q1[1], q2[1]) < minY || Math.min(q1[1], q2[1]) > maxY)
          continue;
        if (sharedLength(p1, p2, q1, q2) >= MIN_SHARED) return true;
      }
    }
    return false;
  }

  // id -> Set of ids it shares a boundary with. Spatial buckets keep it near-linear.
  async function buildAdjacency(items, opts = {}) {
    const tick = opts.tick || noTick;
    const onProgress = opts.onProgress || (() => {});
    const boxes = items.map((it) => bboxOf(it.ring));
    const sizes = boxes
      .map((b) => Math.max(b[2] - b[0], b[3] - b[1]))
      .sort((a, b) => a - b);
    const cell = Math.max(sizes[Math.floor(sizes.length / 2)] || 50, 5);
    const buckets = new Map();
    const keysFor = (b) => {
      const out = [];
      for (
        let gx = Math.floor((b[0] - EPS) / cell);
        gx <= Math.floor((b[2] + EPS) / cell);
        gx++
      ) {
        for (
          let gy = Math.floor((b[1] - EPS) / cell);
          gy <= Math.floor((b[3] + EPS) / cell);
          gy++
        ) {
          out.push(gx + ',' + gy);
        }
      }
      return out;
    };
    boxes.forEach((b, i) => {
      for (const k of keysFor(b)) {
        if (!buckets.has(k)) buckets.set(k, []);
        buckets.get(k).push(i);
      }
    });
    const adj = new Map(items.map((it) => [it.id, new Set()]));
    for (let i = 0; i < items.length; i++) {
      const seen = new Set();
      for (const k of keysFor(boxes[i])) {
        for (const j of buckets.get(k) || []) {
          if (j <= i || seen.has(j)) continue;
          seen.add(j);
          const a = boxes[i],
            b = boxes[j];
          if (
            b[0] > a[2] + EPS ||
            b[2] < a[0] - EPS ||
            b[1] > a[3] + EPS ||
            b[3] < a[1] - EPS
          )
            continue;
          if (shareBoundary(items[i], items[j])) {
            adj.get(items[i].id).add(items[j].id);
            adj.get(items[j].id).add(items[i].id);
          }
        }
      }
      if (i % 200 === 199) {
        onProgress((i + 1) / items.length);
        await tick();
      }
    }
    onProgress(1);
    return adj;
  }

  // Seed order: sweep from a corner, a row at a time (how field teams read a map).
  const CORNERS = {
    'top-left': (a, b) => b.y - a.y || a.x - b.x,
    'top-right': (a, b) => b.y - a.y || b.x - a.x,
    'bottom-left': (a, b) => a.y - b.y || a.x - b.x,
    'bottom-right': (a, b) => a.y - b.y || b.x - a.x,
  };
  function seedOrder(items, corner) {
    return items.slice().sort(CORNERS[corner] || CORNERS['top-left']);
  }

  /* Group so every WAG's building total is within target ± tolerance and every
     WAG is one connected piece (each work area shares a boundary with another
     in its group). Returns { groups: [[id, ...]], sums, flags } where flags[i] is
     'ok', 'under' (could not reach the range: an island, or hemmed in), 'over'
     (one work area alone is above the range) or 'empty' (only 0-building areas). */
  async function groupByBuildings(items, adj, opts) {
    // 'best': try every corner, keep the layout with the most WAGs in range
    if (opts.corner === 'best') {
      const corners = Object.keys(CORNERS);
      const onProgress = opts.onProgress || (() => {});
      let best = null;
      for (let k = 0; k < corners.length; k++) {
        const res = await groupByBuildingsFrom(
          items,
          adj,
          Object.assign({}, opts, {
            corner: corners[k],
            onProgress: (f) => onProgress((k + f) / corners.length),
          }),
        );
        const score = (r) => [
          r.flags.filter((f) => f === 'ok').length,
          -r.flags.filter((f) => f !== 'ok').length,
        ];
        if (
          !best ||
          score(res)[0] > score(best)[0] ||
          (score(res)[0] === score(best)[0] && score(res)[1] > score(best)[1])
        ) {
          best = Object.assign(res, { corner: corners[k] });
        }
      }
      return best;
    }
    return Object.assign(await groupByBuildingsFrom(items, adj, opts), {
      corner: opts.corner || 'top-left',
    });
  }

  async function groupByBuildingsFrom(items, adj, opts) {
    const tick = opts.tick || noTick;
    const onProgress = opts.onProgress || (() => {});
    const target = opts.target;
    const tol = opts.tolerance == null ? 0.1 : opts.tolerance;
    const low = target * (1 - tol),
      high = target * (1 + tol);
    const byId = new Map(items.map((it) => [it.id, it]));
    const groupOf = new Map(); // id -> group index
    const groups = [],
      sums = [];

    const join = (g, id) => {
      groups[g].push(id);
      groupOf.set(id, g);
      sums[g] += byId.get(id).weight;
    };

    // 1. grow groups from seeds, only through neighbours
    const order = seedOrder(
      items.filter((it) => it.weight > 0),
      opts.corner,
    );
    let done = 0;
    for (const seed of order) {
      done++;
      if (groupOf.has(seed.id)) continue;
      const g = groups.length;
      groups.push([]);
      sums.push(0);
      join(g, seed.id);
      while (sums[g] < low) {
        let best = null,
          bestScore = null;
        const tried = new Set();
        for (const member of groups[g]) {
          for (const nid of adj.get(member)) {
            if (groupOf.has(nid) || tried.has(nid)) continue;
            tried.add(nid);
            const n = byId.get(nid);
            if (n.weight <= 0 || sums[g] + n.weight > high) continue;
            // compact first (touches most of the group), then closest to the seed
            let touching = 0;
            for (const x of adj.get(nid)) if (groupOf.get(x) === g) touching++;
            const dist = Math.hypot(n.x - seed.x, n.y - seed.y);
            const score = [touching, -dist];
            if (
              !best ||
              score[0] > bestScore[0] ||
              (score[0] === bestScore[0] && score[1] > bestScore[1])
            ) {
              best = n;
              bestScore = score;
            }
          }
        }
        if (!best) break;
        join(g, best.id);
      }
      if (done % 100 === 0) {
        onProgress(0.7 * (done / order.length));
        await tick();
      }
    }

    // 2. fold under-filled groups into a neighbouring group when the total still fits
    async function mergeUnder() {
      let merged = true;
      while (merged) {
        merged = false;
        for (let g = 0; g < groups.length; g++) {
          if (!groups[g].length || sums[g] >= low) continue;
          let bestG = -1,
            bestGap = Infinity;
          for (const id of groups[g]) {
            for (const nid of adj.get(id)) {
              const h = groupOf.get(nid);
              if (h == null || h === g || !groups[h].length) continue;
              const total = sums[g] + sums[h];
              if (total > high) continue;
              const gap = Math.abs(target - total);
              if (gap < bestGap) {
                bestGap = gap;
                bestG = h;
              }
            }
          }
          if (bestG >= 0) {
            for (const id of groups[g]) join(bestG, id);
            groups[g] = [];
            sums[g] = 0;
            merged = true;
          }
        }
        await tick();
      }
    }
    await mergeUnder();
    onProgress(0.8);

    // Two connected parts of `ids`, both within the range, or null.
    function splitInTwo(ids) {
      const inU = new Set(ids);
      const seeds = seedOrder(
        ids.map((id) => byId.get(id)),
        opts.corner,
      );
      for (const seed of seeds) {
        const part = new Set([seed.id]);
        let sum = seed.weight;
        while (sum < low) {
          let best = null;
          for (const id of part)
            for (const nid of adj.get(id)) {
              if (!inU.has(nid) || part.has(nid)) continue;
              const w = byId.get(nid).weight;
              if (sum + w > high) continue;
              if (
                !best ||
                Math.abs(target - (sum + w)) <
                  Math.abs(target - (sum + best.weight))
              )
                best = byId.get(nid);
            }
          if (!best) break;
          part.add(best.id);
          sum += best.weight;
        }
        if (sum < low || sum > high) continue;
        const rest = ids.filter((id) => !part.has(id));
        const restSum = rest.reduce((a, id) => a + byId.get(id).weight, 0);
        if (restSum >= low && restSum <= high && isConnected(rest, adj))
          return [[...part], rest];
      }
      return null;
    }

    // 2b. repair groups still short of the range
    const leave = (g, id) => {
      groups[g] = groups[g].filter((x) => x !== id);
      groupOf.delete(id);
      sums[g] -= byId.get(id).weight;
    };
    const fixed = new Set();
    for (let pass = 0; pass < 3; pass++) {
      let changed = false;
      for (let g = 0; g < groups.length; g++) {
        if (!groups[g].length || sums[g] >= low || fixed.has(g)) continue;
        // (a) take edge work areas from neighbours that can spare them and stay whole
        let took = true;
        while (sums[g] < low && took) {
          took = false;
          let best = null;
          for (const id of groups[g]) {
            for (const nid of adj.get(id)) {
              const h = groupOf.get(nid);
              if (h == null || h === g) continue;
              const w = byId.get(nid).weight;
              if (w <= 0 || sums[h] - w < low || sums[g] + w > high) continue;
              if (
                !isConnected(
                  groups[h].filter((x) => x !== nid),
                  adj,
                )
              )
                continue;
              const gap = Math.abs(target - (sums[g] + w));
              if (!best || gap < best.gap) best = { nid, h, gap };
            }
          }
          if (best) {
            leave(best.h, best.nid);
            join(g, best.nid);
            took = changed = true;
          }
        }
        if (sums[g] >= low) continue;
        // (c) re-split this group plus one neighbour into two connected in-range groups
        let resplit = false;
        const neighbourGroups = new Set();
        for (const id of groups[g])
          for (const nid of adj.get(id)) {
            const h = groupOf.get(nid);
            if (h != null && h !== g) neighbourGroups.add(h);
          }
        for (const h of neighbourGroups) {
          const total = sums[g] + sums[h];
          if (total < 2 * low || total > 2 * high) continue;
          const split = splitInTwo(groups[g].concat(groups[h]));
          if (!split) continue;
          for (const id of groups[g].concat(groups[h])) groupOf.delete(id);
          groups[g] = [];
          sums[g] = 0;
          groups[h] = [];
          sums[h] = 0;
          split[0].forEach((id) => join(g, id));
          split[1].forEach((id) => join(h, id));
          resplit = changed = true;
          break;
        }
        if (resplit) continue;
        // (b) still short: hand every member to neighbours with room, or change nothing
        const moves = [];
        const left = groups[g].slice();
        const room = new Map();
        const roomOf = (h) => (room.has(h) ? room.get(h) : high - sums[h]);
        let progress = true;
        while (left.length && progress) {
          progress = false;
          for (let i = 0; i < left.length; i++) {
            const id = left[i];
            const w = byId.get(id).weight;
            let bestH = -1;
            for (const nid of adj.get(id)) {
              const h = groupOf.get(nid);
              const target2 = h === g ? moves.find((m) => m.id === nid) : null;
              const hh = target2 ? target2.h : h;
              if (hh == null || hh === g || roomOf(hh) < w) continue;
              if (bestH < 0 || roomOf(hh) > roomOf(bestH)) bestH = hh;
            }
            if (bestH >= 0) {
              moves.push({ id, h: bestH });
              room.set(bestH, roomOf(bestH) - w);
              left.splice(i, 1);
              i--;
              progress = true;
            }
          }
        }
        if (!left.length) {
          for (const m of moves) {
            leave(g, m.id);
            join(m.h, m.id);
          }
          changed = true;
        } else {
          fixed.add(g); // nothing more to do for this one
        }
      }
      if (!changed) break;
      await tick();
    }
    await mergeUnder();
    onProgress(0.85);

    // 3. work areas with no buildings join a neighbouring group (it changes no total)
    let attached = true;
    while (attached) {
      attached = false;
      for (const it of items) {
        if (groupOf.has(it.id)) continue;
        let bestG = -1;
        for (const nid of adj.get(it.id)) {
          const h = groupOf.get(nid);
          if (
            h != null &&
            (bestG < 0 || groups[h].length < groups[bestG].length)
          )
            bestG = h;
        }
        if (bestG >= 0) {
          join(bestG, it.id);
          attached = true;
        }
      }
    }
    // isolated 0-building areas: one group per connected patch
    for (const it of items) {
      if (groupOf.has(it.id)) continue;
      const g = groups.length;
      groups.push([]);
      sums.push(0);
      const stack = [it.id];
      join(g, it.id);
      while (stack.length) {
        for (const nid of adj.get(stack.pop())) {
          if (!groupOf.has(nid)) {
            join(g, nid);
            stack.push(nid);
          }
        }
      }
    }
    onProgress(1);

    const out = [],
      outSums = [],
      flags = [];
    groups.forEach((members, g) => {
      if (!members.length) return;
      out.push(members);
      outSums.push(sums[g]);
      flags.push(
        sums[g] === 0
          ? 'empty'
          : sums[g] < low
            ? 'under'
            : sums[g] > high
              ? 'over'
              : 'ok',
      );
    });
    return { groups: out, sums: outSums, flags, low, high };
  }

  // Shortest distance between two segments (0 if they cross).
  function segDist(p1, p2, q1, q2) {
    const cross = (a, b, c) =>
      (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    const d1 = cross(q1, q2, p1),
      d2 = cross(q1, q2, p2),
      d3 = cross(p1, p2, q1),
      d4 = cross(p1, p2, q2);
    if (
      ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
      ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
    )
      return 0;
    const ptSeg = (p, a, b) => {
      const dx = b[0] - a[0],
        dy = b[1] - a[1];
      const L2 = dx * dx + dy * dy;
      const t = L2
        ? Math.max(
            0,
            Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2),
          )
        : 0;
      return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
    };
    return Math.min(
      ptSeg(p1, q1, q2),
      ptSeg(p2, q1, q2),
      ptSeg(q1, p1, p2),
      ptSeg(q2, p1, p2),
    );
  }

  function inRing(pt, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i],
        [xj, yj] = ring[j];
      if (
        yi > pt[1] !== yj > pt[1] &&
        pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi
      )
        inside = !inside;
    }
    return inside;
  }

  // Gap in metres between two work areas' boundaries (0 when they touch or overlap).
  function ringDistance(a, b) {
    if (inRing(a.ring[0], b.ring) || inRing(b.ring[0], a.ring)) return 0;
    let best = Infinity;
    for (let i = 0; i + 1 < a.ring.length; i++) {
      for (let j = 0; j + 1 < b.ring.length; j++) {
        const d = segDist(a.ring[i], a.ring[i + 1], b.ring[j], b.ring[j + 1]);
        if (d < best) best = d;
        if (best === 0) return 0;
      }
    }
    return best;
  }

  /* A group of one work area joins the group of the nearest work area within
     `radius` metres (boundary to boundary) instead of standing alone.
     groups: [[id, ...]] -- changed in place. With opts.high, a group that stays
     within it is preferred over a nearer one that would go over; a lone area
     heavier than opts.high on its own is left alone, and so (with opts.low) is
     one that is already in range by itself. Returns the moves made. */
  function absorbSingletons(items, groups, opts) {
    const radius = opts.radius;
    if (!(radius > 0)) return [];
    const byId = new Map(items.map((it) => [it.id, it]));
    const boxes = new Map(items.map((it) => [it.id, bboxOf(it.ring)]));
    const groupOf = new Map();
    const sums = groups.map((ids, g) => {
      ids.forEach((id) => groupOf.set(id, g));
      return ids.reduce(
        (s, id) => s + (byId.get(id) ? byId.get(id).weight : 0),
        0,
      );
    });
    const high = opts.high == null ? Infinity : opts.high;
    const moves = [];
    for (let g = 0; g < groups.length; g++) {
      if (groups[g].length !== 1) continue;
      const lone = byId.get(groups[g][0]);
      if (!lone || lone.weight > high) continue;
      // already a full WAG on its own (by buildings): it stays
      if (opts.low != null && lone.weight >= opts.low) continue;
      const lb = boxes.get(lone.id);
      let nearest = null,
        nearestFits = null;
      for (const other of items) {
        const h = groupOf.get(other.id);
        if (h == null || h === g || !groups[h].length) continue;
        const ob = boxes.get(other.id);
        if (
          ob[0] > lb[2] + radius ||
          ob[2] < lb[0] - radius ||
          ob[1] > lb[3] + radius ||
          ob[3] < lb[1] - radius
        )
          continue;
        const d = ringDistance(lone, other);
        if (d > radius) continue;
        if (!nearest || d < nearest.d) nearest = { h, d };
        if (
          sums[h] + lone.weight <= high &&
          (!nearestFits || d < nearestFits.d)
        )
          nearestFits = { h, d };
      }
      const to = nearestFits || nearest;
      if (!to) continue;
      groups[to.h].push(lone.id);
      groupOf.set(lone.id, to.h);
      sums[to.h] += lone.weight;
      groups[g] = [];
      sums[g] = 0;
      moves.push({ id: lone.id, to: to.h, distance: to.d });
    }
    for (let g = groups.length - 1; g >= 0; g--)
      if (!groups[g].length) groups.splice(g, 1);
    return moves;
  }

  const SIDES = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ];

  /* Minimum buildings per work area: a grid cell with fewer than `min` buildings
     joins a cell it shares a side with, until every work area holds at least
     `min`, so no building is left out. The smallest area always joins its
     smallest neighbour, which keeps merged areas small. A merged area that closes
     a ring takes in whatever the ring encloses: other cells are absorbed and
     empty spots are filled, so its outline stays one polygon without holes and
     no two work areas overlap.
     cells: [{ id, ix, iy, count }] in output order. Returns
     { groups: [[id, ...]], fill: [[[ix, iy], ...]] (empty spots each group
     covers, per group), short: groups still under `min` (no neighbour left) }. */
  function mergeSmallCells(cells, min) {
    const n = cells.length;
    if (!(min > 0) || !n)
      return {
        groups: cells.map((c) => [c.id]),
        fill: cells.map(() => []),
        short: 0,
      };
    const key = (ix, iy) => ix + ',' + iy;
    const at = new Map(cells.map((c, i) => [key(c.ix, c.iy), i]));
    const owner = cells.map((_, i) => i); // cell -> the area holding it
    const members = cells.map((_, i) => [i]);
    const count = cells.map((c) => c.count || 0);
    const alive = cells.map(() => true);
    const nbrs = cells.map((c) => {
      const s = new Set();
      for (const [dx, dy] of SIDES) {
        const j = at.get(key(c.ix + dx, c.iy + dy));
        if (j != null) s.add(j);
      }
      return s;
    });
    function merge(from, to) {
      for (const i of members[from]) owner[i] = to;
      for (const i of members[from]) members[to].push(i);
      members[from] = [];
      count[to] += count[from];
      alive[from] = false;
      for (const m of nbrs[from]) {
        if (m === to) continue;
        nbrs[m].delete(from);
        nbrs[m].add(to);
        nbrs[to].add(m);
      }
      nbrs[to].delete(from);
      nbrs[from].clear();
    }

    // bucket queue on building count: merging never makes an area smaller, so
    // one pass over the buckets in order sees every area while it is short
    const buckets = [];
    const push = (k) => {
      if (count[k] >= min) return;
      if (!buckets[count[k]]) buckets[count[k]] = [];
      buckets[count[k]].push(k);
    };
    for (let k = 0; k < n; k++) push(k);
    for (let b = 0; b < min; b++) {
      const q = buckets[b];
      if (!q) continue;
      for (let qi = 0; qi < q.length; qi++) {
        const k = q[qi];
        if (!alive[k] || count[k] !== b) continue; // merged since, or stale
        let to = -1;
        for (const m of nbrs[k])
          if (
            to < 0 ||
            count[m] < count[to] ||
            (count[m] === count[to] && m < to)
          )
            to = m;
        if (to < 0) continue; // nothing next to it
        merge(k, to);
        push(to);
      }
    }

    // a ring of cells takes in what it encloses (4-connected flood from outside)
    const fills = new Map();
    for (let k = 0; k < n; k++) {
      if (!alive[k] || members[k].length < 3) continue;
      let x0 = Infinity,
        y0 = Infinity,
        x1 = -Infinity,
        y1 = -Infinity;
      for (const i of members[k]) {
        const c = cells[i];
        if (c.ix < x0) x0 = c.ix;
        if (c.ix > x1) x1 = c.ix;
        if (c.iy < y0) y0 = c.iy;
        if (c.iy > y1) y1 = c.iy;
      }
      x0--;
      y0--;
      x1++;
      y1++;
      const w = x1 - x0 + 1,
        h = y1 - y0 + 1;
      const own = new Uint8Array(w * h);
      for (const i of members[k])
        own[(cells[i].iy - y0) * w + (cells[i].ix - x0)] = 1;
      const seen = new Uint8Array(w * h);
      const stack = [];
      for (let x = 0; x < w; x++) stack.push(x, (h - 1) * w + x);
      for (let y = 0; y < h; y++) stack.push(y * w, y * w + w - 1);
      while (stack.length) {
        const p = stack.pop();
        if (seen[p] || own[p]) continue;
        seen[p] = 1;
        const x = p % w,
          y = (p - x) / w;
        if (x > 0) stack.push(p - 1);
        if (x < w - 1) stack.push(p + 1);
        if (y > 0) stack.push(p - w);
        if (y < h - 1) stack.push(p + w);
      }
      const holes = [];
      for (let p = 0; p < w * h; p++) {
        if (seen[p] || own[p]) continue;
        const ix = (p % w) + x0,
          iy = Math.floor(p / w) + y0;
        const j = at.get(key(ix, iy));
        if (j == null) holes.push([ix, iy]);
        else if (owner[j] !== k) merge(owner[j], k);
      }
      if (holes.length) fills.set(k, holes);
    }

    const out = [];
    for (let k = 0; k < n; k++) {
      if (!alive[k]) continue;
      members[k].sort((a, b) => a - b);
      out.push(k);
    }
    out.sort((a, b) => members[a][0] - members[b][0]);
    return {
      groups: out.map((k) => members[k].map((i) => cells[i].id)),
      fill: out.map((k) => fills.get(k) || []),
      short: out.filter((k) => count[k] < min).length,
    };
  }

  // Each group is one connected piece under `adj` (used by tests and as a check).
  function isConnected(ids, adj) {
    if (ids.length <= 1) return true;
    const inGroup = new Set(ids);
    const seen = new Set([ids[0]]);
    const stack = [ids[0]];
    while (stack.length) {
      for (const n of adj.get(stack.pop())) {
        if (inGroup.has(n) && !seen.has(n)) {
          seen.add(n);
          stack.push(n);
        }
      }
    }
    return seen.size === ids.length;
  }

  window.SimpleMicroplanGrouping = {
    buildAdjacency,
    groupByBuildings,
    isConnected,
    absorbSingletons,
    mergeSmallCells,
    ringDistance,
    seedOrder,
    CORNERS,
  };
})();
