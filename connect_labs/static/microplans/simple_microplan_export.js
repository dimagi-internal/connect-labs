/* Simple Microplan: the two files Connect imports for a microplan.
   Pure logic (no map, no DOM, no fetch) on window.SimpleMicroplanExport, so it
   is unit-tested under node (simple_microplan_export.test.mjs).

   1. Work-area CSV -- the columns, in the order, that the CHC creator exports
      (core/workarea.py:CSV_HEADERS) and Connect's WorkAreaCSVImporter reads
      (CONNECT_IMPORT_CONTRACT.md), Implementation Area included.
   2. Implementation Area CSV -- one row per boundary polygon, for Connect's
      ImplementationAreaCSVImporter: `Implementation Area Name`, `Centroid`,
      `Boundary` (always a single POLYGON: a boundary made of several pieces
      becomes several areas, each named on its own). Connect links a work area
      to its Implementation Area by exact name, so both files take the name
      from the same place: areasOf().

   Also the Work Area Group naming: <WARD>-G-001, with no spaces anywhere in a
   group name (the app's selected-at() splits on spaces). */
(function () {
  'use strict';

  const WORK_AREA_HEADERS = [
    'Area Slug',
    'Ward',
    'Centroid',
    'Boundary',
    'Building Count',
    'Expected Visit Count',
    'Target Population',
    'LGA',
    'State',
    'Implementation Area',
    'Work Area Group Name',
  ];
  const IMPLEMENTATION_AREA_HEADERS = [
    'Implementation Area Name',
    'Centroid',
    'Boundary',
  ];

  const clean = (v) => String(v == null ? '' : v).trim();

  // even-odd point-in-polygon over a polygon's rings (holes included)
  function inPoly(x, y, poly) {
    let inside = false;
    for (const ring of poly) {
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const xi = ring[i][0],
          yi = ring[i][1],
          xj = ring[j][0],
          yj = ring[j][1];
        if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi)
          inside = !inside;
      }
    }
    return inside;
  }

  function ringCentroid(ring) {
    let a = 0,
      sx = 0,
      sy = 0;
    for (let i = 1; i < ring.length; i++) {
      const px = ring[i - 1][0],
        py = ring[i - 1][1],
        x = ring[i][0],
        y = ring[i][1];
      const cr = px * y - x * py;
      a += cr;
      sx += (px + x) * cr;
      sy += (py + y) * cr;
    }
    a /= 2;
    if (!a) return { area: 0, cx: ring[0][0], cy: ring[0][1] };
    return { area: Math.abs(a), cx: sx / (6 * a), cy: sy / (6 * a) };
  }

  // area centroid of a part's polygons, holes subtracted -> [lon, lat]
  function partCentroid(polys) {
    let A = 0,
      cx = 0,
      cy = 0;
    for (const poly of polys) {
      poly.forEach((ring, r) => {
        const c = ringCentroid(ring);
        const w = r === 0 ? c.area : -c.area;
        A += w;
        cx += c.cx * w;
        cy += c.cy * w;
      });
    }
    if (!A) {
      const c = ringCentroid(polys[0][0]);
      return [c.cx, c.cy];
    }
    return [cx / A, cy / A];
  }

  // Centroid is stored "lng lat" -> [lon, lat]
  function centroidOf(row) {
    const p = clean(row['Centroid']).split(/\s+/).map(Number);
    if (p.length < 2 || p.some((n) => !isFinite(n))) return null;
    return [p[0], p[1]];
  }

  // The ward most of the work areas inside a polygon carry, or ''.
  function dominantWard(poly, rows) {
    const counts = new Map();
    for (const r of rows || []) {
      const w = clean(r['Ward']);
      const c = w && centroidOf(r);
      if (c && inPoly(c[0], c[1], poly))
        counts.set(w, (counts.get(w) || 0) + 1);
    }
    let best = '',
      n = 0;
    counts.forEach((k, w) => {
      if (k > n) {
        best = w;
        n = k;
      }
    });
    return best;
  }

  // Every boundary polygon is one Implementation Area:
  // [{part, poly, polygon, name}]. Name: the one typed in (part.names[poly]),
  // else the ward -- the boundary's own, else the one its work areas carry --
  // numbered when a ward has several pieces, else "Implementation Area N".
  function areasOf(parts, rows) {
    const out = [];
    (parts || []).forEach((part, pi) => {
      const polys = (part && part.polys) || [];
      const typed = Array.isArray(part.names) ? part.names : [];
      polys.forEach((polygon, k) => {
        let name = clean(typed[k]);
        // a name saved before areas were per polygon applies to a lone polygon
        if (!name && polys.length === 1) name = clean(part.name);
        if (!name) {
          const ward = clean(part.ward) || dominantWard(polygon, rows);
          if (ward) name = polys.length > 1 ? ward + ' ' + (k + 1) : ward;
        }
        out.push({ part: pi, poly: k, polygon, name });
      });
    });
    out.forEach((a, i) => {
      if (!a.name) a.name = 'Implementation Area ' + (i + 1);
    });
    return out;
  }

  // One boundary per polygon, each with a stable id: a boundary added in
  // several pieces (a ward in two parts, an uploaded multi-polygon) becomes
  // several boundaries, named "<ward> 1", "<ward> 2" when it has a ward.
  // `firstId` is the next free id; ids already on a part are kept.
  function splitBoundary(parts, firstId) {
    let id = firstId || 1;
    const out = [];
    for (const part of parts || []) {
      const polys = (part && part.polys) || [];
      const typed = Array.isArray(part.names) ? part.names : [];
      polys.forEach((polygon, k) => {
        const p = Object.assign({}, part, { polys: [polygon] });
        delete p.names;
        if (polys.length > 1) {
          delete p.id;
          const ward = clean(part.ward);
          p.name = clean(typed[k]) || (ward ? ward + ' ' + (k + 1) : '');
          if (!p.name) delete p.name;
        } else if (!clean(p.name) && clean(typed[0])) {
          p.name = clean(typed[0]);
        }
        if (p.id == null) p.id = id++;
        out.push(p);
      });
    }
    return out;
  }

  // Next free boundary id.
  function nextBoundaryId(parts) {
    return (
      (parts || []).reduce((m, p) => Math.max(m, Number(p.id) || 0), 0) + 1
    );
  }

  // Names used more than once (case-insensitive) -- Connect matches by name,
  // so two areas sharing one would be indistinguishable.
  function duplicateNames(areas) {
    const seen = new Map();
    areas.forEach((a) => {
      const k = a.name.toLowerCase();
      seen.set(k, (seen.get(k) || []).concat(a.name));
    });
    return [...seen.values()].filter((v) => v.length > 1).map((v) => v[0]);
  }

  // Index of the area a work area belongs to: the polygon containing its
  // centroid, else the one whose centroid is nearest (a boundary cell can
  // overflow its boundary by up to one cell), else -1.
  function areaIndexFor(row, areas) {
    const c = centroidOf(row);
    if (!c || !areas.length) return -1;
    for (let i = 0; i < areas.length; i++) {
      if (inPoly(c[0], c[1], areas[i].polygon)) return i;
    }
    let best = -1,
      bestD = Infinity;
    areas.forEach((a, i) => {
      const [x, y] = partCentroid([a.polygon]);
      const d = (x - c[0]) ** 2 + (y - c[1]) ** 2;
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    return best;
  }

  /* ---- Work Area Group names ---- */
  // a group name with no spaces: runs of whitespace become "_"
  function groupNameSafe(name) {
    return clean(name).replace(/\s+/g, '_');
  }
  // the <WARD> in <WARD>-G-001 for a work area, or '' when it has no ward
  function groupWardKey(row) {
    return groupNameSafe(row && row['Ward']);
  }
  // The ward most of a group's work areas are in (so a group that straddles a
  // ward line is named after where most of it is), or ''.
  function groupWardKeyOf(members) {
    const counts = new Map();
    for (const r of members) {
      const k = groupWardKey(r);
      if (k) counts.set(k, (counts.get(k) || 0) + 1);
    }
    let best = '',
      n = 0;
    counts.forEach((c, k) => {
      if (c > n) {
        best = k;
        n = c;
      }
    });
    return best;
  }
  // Numbers <key>-G-001, -G-002 … per key, carrying on after the names in
  // `keepNames` (groups that stay when only some work areas are regrouped).
  function groupNamer(keepNames) {
    const next = new Map();
    for (const g of keepNames || []) {
      const m = String(g).match(/^(.+)-G-(\d+)$/);
      if (m) next.set(m[1], Math.max(next.get(m[1]) || 0, Number(m[2])));
    }
    return (key) => {
      const n = (next.get(key) || 0) + 1;
      next.set(key, n);
      return key + '-G-' + String(n).padStart(3, '0');
    };
  }

  // "POLYGON((x y, ...))" (how the editor stores it) -> "POLYGON ((x y, ...))",
  // the spacing of the CHC creator's export (shapely WKT).
  function workAreaWkt(wkt) {
    return clean(wkt).replace(/^POLYGON\s*\(\(/i, 'POLYGON ((');
  }

  const fmt6 = (n) => String(Number(n.toFixed(6)));
  const ringWkt = (ring) =>
    '(' + ring.map((p) => fmt6(p[0]) + ' ' + fmt6(p[1])).join(',') + ')';

  // one polygon (holes kept) as WKT
  function polygonWkt(polygon) {
    return 'POLYGON(' + polygon.map(ringWkt).join(',') + ')';
  }

  function csvEscape(v) {
    v = String(v == null ? '' : v);
    return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  }

  function toCsv(fieldnames, rows) {
    return (
      fieldnames.join(',') +
      '\n' +
      rows
        .map((r) => fieldnames.map((f) => csvEscape(r[f])).join(','))
        .join('\n') +
      '\n'
    );
  }

  // The work-area rows as exported: each takes the name of the Implementation
  // Area it sits in, Boundary in the creator's WKT spacing, a group name with
  // no spaces. Any extra columns (the singleton ones) follow the standard ones.
  // The stored rows are not changed.
  function workAreaRows(rows, parts) {
    const areas = areasOf(parts, rows);
    return rows.map((r) => {
      const i = areaIndexFor(r, areas);
      return Object.assign({}, r, {
        Boundary: workAreaWkt(r['Boundary']),
        'Implementation Area': i >= 0 ? areas[i].name : '',
        'Work Area Group Name': groupNameSafe(r['Work Area Group Name']),
      });
    });
  }

  function workAreaCsv(rows, parts) {
    const out = workAreaRows(rows, parts);
    const fieldnames = [...WORK_AREA_HEADERS];
    const seen = new Set(fieldnames);
    for (const r of out)
      for (const k of Object.keys(r))
        if (!seen.has(k)) {
          seen.add(k);
          fieldnames.push(k);
        }
    return toCsv(fieldnames, out);
  }

  function implementationAreaRows(parts, rows) {
    return areasOf(parts, rows).map((a) => {
      const [lon, lat] = partCentroid([a.polygon]);
      return {
        'Implementation Area Name': a.name,
        Centroid: fmt6(lon) + ' ' + fmt6(lat),
        Boundary: polygonWkt(a.polygon),
      };
    });
  }

  function implementationAreaCsv(parts, rows) {
    return toCsv(
      IMPLEMENTATION_AREA_HEADERS,
      implementationAreaRows(parts, rows),
    );
  }

  window.SimpleMicroplanExport = {
    WORK_AREA_HEADERS,
    IMPLEMENTATION_AREA_HEADERS,
    areasOf,
    splitBoundary,
    nextBoundaryId,
    duplicateNames,
    areaIndexFor,
    partCentroid,
    workAreaWkt,
    polygonWkt,
    groupNameSafe,
    groupWardKey,
    groupWardKeyOf,
    groupNamer,
    workAreaRows,
    workAreaCsv,
    implementationAreaRows,
    implementationAreaCsv,
  };
})();
