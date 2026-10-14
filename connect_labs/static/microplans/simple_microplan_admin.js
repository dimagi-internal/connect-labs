/* Simple Microplan: fill blank Ward / LGA / State from Labs' own ward boundaries.
   Pure logic (no map, no DOM, no fetch) on window.SimpleMicroplanAdmin, so it is
   unit-tested under node (simple_microplan_admin.test.mjs).

   Connect's work-area import rejects a row with a blank Ward, LGA or State, and
   the OpenStreetMap reverse geocode often has no ward (all of Nigeria, say). The
   page fetches the wards around its new work areas from the boundary-viewport
   endpoint the CHC creator's Boundaries layer uses (level 3, "labs" source) and
   hands the GeoJSON features here: each blank field takes the name, LGA or state
   of the ward containing the work area's centroid. A value already there is kept. */
(function () {
  'use strict';

  const blank = (v) => v == null || String(v).trim() === '';

  function missingAdmin(row) {
    return blank(row['Ward']) || blank(row['LGA']) || blank(row['State']);
  }

  function countMissingAdmin(rows) {
    return rows.filter(missingAdmin).length;
  }

  // Centroid is stored "lng lat" -> [lon, lat]
  function centroidOf(row) {
    const p = String(row['Centroid'] || '')
      .trim()
      .split(/\s+/)
      .map(Number);
    if (p.length < 2 || p.some((n) => !isFinite(n))) return null;
    return [p[0], p[1]];
  }

  // [minLon, minLat, maxLon, maxLat] around the rows that still need a field, or null.
  function bboxOfRowsNeedingAdmin(rows) {
    let box = null;
    for (const row of rows) {
      if (!missingAdmin(row)) continue;
      const c = centroidOf(row);
      if (!c) continue;
      if (!box) box = [c[0], c[1], c[0], c[1]];
      else {
        box[0] = Math.min(box[0], c[0]);
        box[1] = Math.min(box[1], c[1]);
        box[2] = Math.max(box[2], c[0]);
        box[3] = Math.max(box[3], c[1]);
      }
    }
    return box;
  }

  // GeoJSON geometry -> [polygon [ring [[lon, lat], ...], ...], ...]
  function polysOf(geom) {
    if (!geom) return [];
    if (geom.type === 'Polygon') return [geom.coordinates || []];
    if (geom.type === 'MultiPolygon') return geom.coordinates || [];
    return [];
  }

  function inRing(x, y, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0],
        yi = ring[i][1],
        xj = ring[j][0],
        yj = ring[j][1];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi)
        inside = !inside;
    }
    return inside;
  }

  // inside a polygon's outer ring and none of its holes
  function inPolys(x, y, polys) {
    return polys.some(
      (poly) =>
        poly.length &&
        inRing(x, y, poly[0]) &&
        !poly.slice(1).some((hole) => inRing(x, y, hole)),
    );
  }

  function indexWards(features) {
    const out = [];
    for (const f of features || []) {
      const p = (f && f.properties) || {};
      const polys = polysOf(f && f.geometry);
      if (!polys.length || blank(p.name)) continue;
      let a = Infinity,
        b = Infinity,
        c = -Infinity,
        d = -Infinity;
      for (const poly of polys)
        for (const [x, y] of poly[0] || []) {
          if (x < a) a = x;
          if (y < b) b = y;
          if (x > c) c = x;
          if (y > d) d = y;
        }
      out.push({
        polys,
        bbox: [a, b, c, d],
        ward: String(p.name).trim(),
        lga: String(p.lga || '').trim(),
        state: String(p.state || '').trim(),
        rank: p.provider_rank == null ? Infinity : Number(p.provider_rank),
        area: p.area_km2 == null ? Infinity : Number(p.area_km2),
      });
    }
    return out;
  }

  // The ward containing (lon, lat). The merged labs layer can hold the same ward
  // twice (GeoPoDe and GRID3): keep the preferred provider, then the smaller area.
  function wardAt(index, lon, lat) {
    let best = null;
    for (const w of index) {
      const [a, b, c, d] = w.bbox;
      if (lon < a || lon > c || lat < b || lat > d) continue;
      if (!inPolys(lon, lat, w.polys)) continue;
      if (
        !best ||
        w.rank < best.rank ||
        (w.rank === best.rank && w.area < best.area)
      )
        best = w;
    }
    return best;
  }

  // Fill each row's blank Ward / LGA / State from the ward at its centroid.
  // Rows are changed in place; returns how many rows got at least one field.
  function fillAdminFromWards(rows, features) {
    const index = indexWards(features);
    if (!index.length) return 0;
    let filled = 0;
    for (const row of rows) {
      if (!missingAdmin(row)) continue;
      const c = centroidOf(row);
      const w = c && wardAt(index, c[0], c[1]);
      if (!w) continue;
      let changed = false;
      for (const [field, value] of [
        ['Ward', w.ward],
        ['LGA', w.lga],
        ['State', w.state],
      ]) {
        if (blank(row[field]) && value) {
          row[field] = value;
          changed = true;
        }
      }
      if (changed) filled++;
    }
    return filled;
  }

  window.SimpleMicroplanAdmin = {
    missingAdmin,
    countMissingAdmin,
    bboxOfRowsNeedingAdmin,
    wardAt,
    indexWards,
    fillAdminFromWards,
  };
})();
