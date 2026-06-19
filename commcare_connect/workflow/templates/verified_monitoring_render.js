// Verified Monitoring (N1) — funder-facing verified-coverage dashboard.
// Self-contained: reads everything from instance.state (seeded by survey_sim;
// every KPI computed from row-level records via the survey_quality library) and
// never fetches. Light, Connect-aligned styling.
//
// Layout: the six-cycle TREND is the page hero (edge-to-edge, top; hover a point
// for its value); a cycle selector pivots the page; per cycle a full-width map
// that moves to that cycle's two real wards; a per-surveyor survey-quality
// scorecard; and an independent back-check that opens on one surveyor (click a
// scorecard row to switch) — one row per re-surveyed household, columns grouped
// under Identity / Location / Outcome sections with info buttons (method +
// source). Objective copy; the viewer draws the conclusion.
// Marker string for deploy freshness checks: VERIFIED_MONITORING_RENDER_V69
function WorkflowUI(props) {
  var instance = props.instance || {};
  var data = instance.state || {};
  var prog = data.program || {};
  var rounds = data.rounds || [];
  var trend = data.trend || {};

  var INK = '#111827',
    SUBINK = '#1e293b',
    PANEL = '#ffffff',
    LINE = '#e6e7f0',
    MUT = '#6b7280',
    INDIGO = '#4f46e5',
    AMBER = '#f59e0b',
    ROSE = '#e11d48',
    COMP = '#64748b',
    GREEN = '#059669',
    SLATE = '#94a3b8';
  var sans = "'Work Sans', Inter, system-ui, sans-serif";
  var mono = 'ui-monospace, SFMono-Regular, Menlo, monospace';
  var SHADOW = '0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.04)';

  var [sel, setSel] = React.useState(
    Math.max(0, (data.current_round || rounds.length) - 1),
  );
  if (sel > rounds.length - 1) sel = Math.max(0, rounds.length - 1);
  var rd = rounds[sel] || null;
  // selected surveyor (drives the back-check section); null = round-level view
  var [selSurv, setSelSurv] = React.useState(null);
  // hovered trend point (for the tooltip), back-check info popup {key,x,y},
  // and the selected scorecard quality metric {key,surveyor,value}
  var [hoverPt, setHoverPt] = React.useState(null);
  var [bcInfo, setBcInfo] = React.useState(null);
  var [qSel, setQSel] = React.useState(null);

  // ---- per-round map (shared ConnectMap; moves each round) ----
  var [mapLibReady, setMapLibReady] = React.useState(
    typeof window !== 'undefined' && !!window.ConnectMap && !!window.mapboxgl,
  );
  var [sdOn, setSdOn] = React.useState(true);
  var [pinsOn, setPinsOn] = React.useState(true);
  var [planOn, setPlanOn] = React.useState(true);
  // Two opt-in drill-down layers, both default OFF, both lazy-fetched on toggle:
  //   • Work Areas (waOn) — the sampled work-area polygons (= the sampled building
  //     footprints), arm-coloured with the primary-solid / alternate-dashed split.
  //   • Building Footprints (bfOn) — ALL buildings in the area, neutral amber.
  var [waOn, setWaOn] = React.useState(false);
  var [bfOn, setBfOn] = React.useState(false);
  var mapDivRef = React.useRef(null);
  var mapRef = React.useRef(null);
  var mapLoadedRef = React.useRef(false);
  // Only re-fit the map when the ROUND changes — not on every layer toggle, so a
  // toggle never yanks the user's pan/zoom back to the ward extent.
  var fittedSelRef = React.useRef(null);
  // The map's Layers panel (the SAME component the plan editor docks on its map:
  // static/microplans/map_panel.js) + its mount node; created once the map exists.
  var panelMountRef = React.useRef(null);
  var panelRef = React.useRef(null);
  var waLayerRef = React.useRef(null);
  var bfLayerRef = React.useRef(null);
  // Arm → colour, shared with the plan editor (PlanLayers.ARM_COLOR).
  var ARM = (window.PlanLayers && window.PlanLayers.ARM_COLOR) || {
    intervention: '#10b981',
    comparison: '#3b82f6',
  };
  // The DESIGNED plan's selected-PSU hulls ride on the round's seeded state
  // (overlay.plan_hulls — baked by regenerate.py from the real two-arm plan) and are
  // drawn via the shared PlanLayers, so the monitoring map matches the plan editor.
  // Show-don't-tell: read from state, never fetch. See WORKFLOW_REFERENCE.md §4a.
  //
  // EXCEPTION — the two building layers (opt-in): Work Areas come from the plan
  // endpoint (overlay.plan_url → work_areas, the sampled footprints); Building
  // Footprints come from that plan's footprints endpoint (all buildings in the
  // area). Both too heavy to bake into every round's state, so they lazy-fetch on
  // toggle and cache per URL.
  var fetchCacheRef = React.useRef({});
  var fetchLoadingRef = React.useRef({});

  React.useEffect(
    function () {
      if (mapLibReady) return undefined;
      var t = setInterval(function () {
        if (window.ConnectMap && window.mapboxgl) {
          setMapLibReady(true);
          clearInterval(t);
        }
      }, 150);
      return function () {
        clearInterval(t);
      };
    },
    [mapLibReady],
  );

  React.useEffect(
    function () {
      var overlay = rd && rd.overlay;
      if (!mapLibReady || !overlay || !mapDivRef.current) return undefined;
      var CM = window.ConnectMap;
      var progWard = rd.treatment_ward;
      if (!mapRef.current) {
        var ctr = CM.bounds(overlay.ward_boundaries).getCenter();
        mapRef.current = CM.createMap(mapDivRef.current, {
          center: [ctr.lng, ctr.lat],
          zoom: 10,
          style: 'mapbox://styles/mapbox/light-v11',
        });
      }
      var map = mapRef.current;
      function draw() {
        CM.remove(map, ['vm-sd', 'vm-pins', 'vm-wards']);
        CM.boundary(map, 'vm-wards', overlay.ward_boundaries, {
          activeWard: progWard,
          activeColor: INDIGO,
          mutedColor: COMP,
          labelColor: SUBINK,
          labelHalo: '#ffffff',
        });
        try {
          // Arm-keyed ward washes in two clearly DIFFERENT hues so the arms
          // read apart by fill at a glance: intervention = indigo wash,
          // control = a warm amber wash. Keyed to the title-caption swatches
          // below. The intervention ward keeps a clearly heavier outline so it
          // still reads as the focus arm.
          map.setPaintProperty('vm-wards-fill', 'fill-color', [
            'case',
            ['==', ['get', 'ward'], progWard],
            INDIGO,
            AMBER,
          ]);
          map.setPaintProperty('vm-wards-fill', 'fill-opacity', [
            'case',
            ['==', ['get', 'ward'], progWard],
            0.34,
            0.22,
          ]);
          map.setPaintProperty('vm-wards-line', 'line-color', [
            'case',
            ['==', ['get', 'ward'], progWard],
            INDIGO,
            '#64748b',
          ]);
          map.setPaintProperty('vm-wards-line', 'line-width', [
            'case',
            ['==', ['get', 'ward'], progWard],
            3,
            1.5,
          ]);
          // Color each in-map ward label by its arm so the label itself, not
          // just the wash, says which ward is intervention vs control.
          map.setPaintProperty('vm-wards-label', 'text-color', [
            'case',
            ['==', ['get', 'ward'], progWard],
            INDIGO,
            '#475569',
          ]);
          map.setPaintProperty('vm-wards-label', 'text-halo-color', '#ffffff');
          map.setPaintProperty('vm-wards-label', 'text-halo-width', 2);
          map.setLayoutProperty('vm-wards-label', 'text-size', 13);
        } catch (e) {}
        // Two overlapping point layers, separated by WEIGHT (not shape): the
        // program's delivery visits read as the solid, larger, opaque green
        // layer; the independent survey reads as faint, smaller pins underneath.
        // So the intervention ward fills with green and the control ward —
        // which has survey pins but no delivery — stays visibly green-free.
        if (sdOn && overlay.service_delivery) {
          CM.points(map, 'vm-sd', overlay.service_delivery, {
            color: '#15803d',
            radius: 5.4,
            opacity: 0.92,
          });
          // Give the program-delivery dots a white edge so they separate from
          // one another (less overlap soup) and read clearly apart from the
          // indigo survey pins layered on top.
          try {
            map.setPaintProperty('vm-sd', 'circle-stroke-width', 0.8);
            map.setPaintProperty(
              'vm-sd',
              'circle-stroke-color',
              'rgba(255,255,255,0.85)',
            );
          } catch (e) {}
        }
        if (pinsOn && overlay.survey_pins) {
          // When a scorecard row is selected, filter the pins to THAT surveyor's
          // surveys (each pin carries its surveyor). null selSurv = show all.
          var pinsData = overlay.survey_pins;
          if (selSurv) {
            pinsData = {
              type: 'FeatureCollection',
              features: (pinsData.features || []).filter(function (f) {
                return (f.properties || {}).surveyor === selSurv;
              }),
            };
          }
          // Survey pins stay SUBORDINATE to the solid green delivery layer, but
          // need a crisp outline so 'the survey covered both wards' reads — in
          // the control ward (no delivery) the pins are the only marks, so if
          // they're too faint the gap looks like 'nobody surveyed control'.
          CM.pins(map, 'vm-pins', pinsData, {
            confirmedColor: INDIGO,
            // Three clearly distinct hues so the survey reads apart from the
            // program's green delivery: confirmed = indigo, surveyed-but-not-
            // reached = rose. A dark slate was too close to the indigo confirmed
            // pins (and to the basemap), so the two survey states blurred and the
            // control ward (mostly not-reached) was illegible.
            absentColor: ROSE,
            radius: 3.4,
            opacity: 0.9,
            strokeWidth: 1.2,
            strokeColor: 'rgba(255,255,255,0.95)',
          });
          try {
            // The independent survey reads as a HOLLOW RING (faint fill, coloured
            // ring) — visibly a different MARK from the program's solid green
            // delivery dot. That mark difference (ring vs solid) fights the
            // overplotting that made both wards look equally dotted, and lets the
            // control ward — survey rings but no green fill — read as "program
            // absent". The ring colour still carries confirmed (indigo) vs
            // not-reached (rose). Within the survey, PRIMARY (first-choice) is a
            // larger heavier ring and ALTERNATE (substituted backup) a smaller
            // thinner ring, so the substitution mix stays visible. Ungrounded
            // rounds carry no sample_type → default to the primary ring.
            var isAlt = ['==', ['get', 'sample_type'], 'alternate'];
            map.setPaintProperty('vm-pins', 'circle-radius', [
              'case',
              isAlt,
              3.9,
              5.2,
            ]);
            // faint fill so the ring dominates — hollow vs the solid delivery dot
            map.setPaintProperty('vm-pins', 'circle-opacity', 0.14);
            map.setPaintProperty('vm-pins', 'circle-stroke-width', [
              'case',
              isAlt,
              1.8,
              2.6,
            ]);
            // ring colour carries the survey result (confirmed vs not-reached)
            map.setPaintProperty('vm-pins', 'circle-stroke-color', [
              'case',
              ['get', 'confirmed'],
              INDIGO,
              ROSE,
            ]);
          } catch (e) {}
        }
        // The DESIGNED plan's selected-PSU hulls, drawn with the SAME PlanLayers the
        // editor uses (§4a) — arm-coloured, namespaced vm-plan-psu-* so they sit under
        // the monitoring marks. Read from the round's seeded state (overlay.plan_hulls);
        // never fetched.
        var planHulls = overlay.plan_hulls;
        if (
          planOn &&
          window.PlanLayers &&
          planHulls &&
          (planHulls.features || []).length
        ) {
          window.PlanLayers.hulls(map, {
            data: planHulls,
            src: 'vm-plan-psu',
            fillId: 'vm-plan-psu-fill',
            lineId: 'vm-plan-psu-line',
          });
        } else if (window.PlanLayers) {
          window.PlanLayers.remove(map, [
            'vm-plan-psu-fill',
            'vm-plan-psu-line',
          ]);
        }
        // Two opt-in building layers (Work Areas + Building Footprints), both drawn
        // via the shared PlanLayers, lazy-fetched + cached, UNDER the monitoring marks.
        // Lazy-load a FeatureCollection from `url` (cache per URL); kick a redraw
        // when it resolves. `mapper(json)` shapes the response into an FC.
        var planUrl = overlay.plan_url;
        function loadFc(url, mapper, layerRef) {
          var got = fetchCacheRef.current[url];
          if (got) return got;
          if (!fetchLoadingRef.current[url]) {
            fetchLoadingRef.current[url] = true;
            if (layerRef.current) layerRef.current.setMeta('Loading…');
            fetch(url, { headers: { Accept: 'application/json' } })
              .then(function (r) {
                return r.json();
              })
              .then(function (d) {
                fetchCacheRef.current[url] = mapper(d);
                fetchLoadingRef.current[url] = false;
                if (mapRef.current && mapRef.current.isStyleLoaded()) draw();
              })
              .catch(function () {
                fetchLoadingRef.current[url] = false;
                if (layerRef.current)
                  layerRef.current.setMeta('Failed — toggle to retry');
              });
          }
          return null;
        }
        function firstLayer(ids) {
          return ids.filter(function (id) {
            return map.getLayer(id);
          })[0];
        }

        // Building Footprints — ALL buildings in the round's area (the editor's own
        // footprints endpoint), neutral amber, drawn UNDER the sampled work areas.
        var BF_IDS = ['vm-bf-fill', 'vm-bf-dots'];
        var fpUrl = planUrl ? planUrl + 'footprints/' : null;
        if (bfOn && window.PlanLayers && fpUrl) {
          var bfFc = loadFc(
            fpUrl,
            function (d) {
              return (
                d.footprints || { type: 'FeatureCollection', features: [] }
              );
            },
            bfLayerRef,
          );
          if (bfFc) {
            window.PlanLayers.footprints(map, {
              data: bfFc,
              src: 'vm-bf',
              fillId: BF_IDS[0],
              dotsId: BF_IDS[1],
              before: firstLayer(['vm-wa-fill', 'vm-sd', 'vm-pins']),
            });
            if (bfLayerRef.current)
              bfLayerRef.current.setMeta(
                (bfFc.features || []).length.toLocaleString() + ' buildings',
              );
          }
        } else if (window.PlanLayers) {
          window.PlanLayers.remove(map, BF_IDS);
        }

        // Work Areas — the SAMPLED work-area polygons (= the sampled building
        // footprints), arm-coloured with the primary-solid / alternate-dashed split,
        // drawn ABOVE all-buildings but UNDER the monitoring marks.
        var WA_IDS = ['vm-wa-fill', 'vm-wa-altline', 'vm-wa-dots'];
        if (waOn && window.PlanLayers && planUrl) {
          var csMap = overlay.cluster_surveyor || {};
          var waFc = loadFc(
            planUrl,
            function (d) {
              return {
                type: 'FeatureCollection',
                features: (d.work_areas || [])
                  .filter(function (w) {
                    return w.geometry;
                  })
                  .map(function (w) {
                    var cl = (w.properties || {}).cluster;
                    return {
                      type: 'Feature',
                      geometry: w.geometry,
                      properties: {
                        arm: w.arm,
                        sample_type: (w.properties || {}).sample_type,
                        // owning surveyor (via the round's cluster->surveyor map),
                        // so a scorecard click can filter to one surveyor's PSUs.
                        surveyor: csMap[w.arm + ':' + cl],
                      },
                    };
                  }),
              };
            },
            waLayerRef,
          );
          if (waFc) {
            // Filter to the selected surveyor's work areas when a row is clicked.
            var waShown = selSurv
              ? {
                  type: 'FeatureCollection',
                  features: (waFc.features || []).filter(function (f) {
                    return f.properties.surveyor === selSurv;
                  }),
                }
              : waFc;
            window.PlanLayers.footprints(map, {
              data: waShown,
              src: 'vm-wa',
              fillId: WA_IDS[0],
              altLineId: WA_IDS[1],
              dotsId: WA_IDS[2],
              armProp: 'arm',
              splitByType: true,
              colors: ARM,
              before: firstLayer(['vm-sd', 'vm-pins']),
            });
            if (waLayerRef.current)
              waLayerRef.current.setMeta(
                (waShown.features || []).length.toLocaleString() +
                  ' work areas' +
                  (selSurv ? ' · ' + selSurv : ''),
              );
          }
        } else if (window.PlanLayers) {
          window.PlanLayers.remove(map, WA_IDS);
        }

        // When EITHER building layer is on, fade the AREA fills (ward + PSU hull) so
        // the individual buildings read against a clean background; the hull/ward
        // OUTLINES stay for cluster + ward context. Restored when both are off.
        var buildingsOn = waOn || bfOn;
        try {
          if (map.getLayer('vm-wards-fill'))
            // Keep the two arms ARM-KEYED here too — this toggle handler runs
            // after draw() and would otherwise flatten both wards to one faint
            // wash. Intervention (indigo) reads clearly heavier than control
            // (amber) so the arms are obviously different at a glance; both
            // fade together when a building layer is on.
            map.setPaintProperty('vm-wards-fill', 'fill-opacity', [
              'case',
              ['==', ['get', 'ward'], progWard],
              buildingsOn ? 0.06 : 0.34,
              buildingsOn ? 0.04 : 0.22,
            ]);
          if (map.getLayer('vm-plan-psu-fill'))
            map.setPaintProperty(
              'vm-plan-psu-fill',
              'fill-opacity',
              buildingsOn ? 0.03 : 0.12,
            );
        } catch (e) {}
        // Re-fit ONLY when the round changed (or first draw) — never on a layer
        // toggle, so toggling a layer keeps the user's current pan/zoom.
        if (fittedSelRef.current !== sel) {
          CM.fit(map, overlay.ward_boundaries, 64);
          fittedSelRef.current = sel;
        }
      }
      if (mapLoadedRef.current && map.isStyleLoaded()) draw();
      else
        map.once('load', function () {
          mapLoadedRef.current = true;
          draw();
        });
      return undefined;
    },
    [mapLibReady, sel, sdOn, pinsOn, planOn, waOn, bfOn, selSurv],
  );

  // Build the map's Layers panel ONCE the map exists — reuses the plan editor's
  // MicroplansMapPanel so the selector chrome matches the plan UI exactly. Each
  // layer's onToggle drives the existing React draw state; footprints carries a
  // 'buildings' meta the draw fills in after the lazy fetch. Inspect tab dropped
  // (monitoring has nothing to inspect).
  React.useEffect(
    function () {
      if (
        panelRef.current ||
        !mapLibReady ||
        !window.MicroplansMapPanel ||
        !panelMountRef.current ||
        !mapRef.current
      )
        return undefined;
      var panel = window.MicroplansMapPanel.create({
        map: mapRef.current,
        mount: panelMountRef.current,
        tabs: ['layers'],
      });
      panelRef.current = panel;
      panel
        .registerLayer({
          id: 'delivery',
          label: 'Service delivery',
          color: '#16a34a',
          onToggle: function (on) {
            setSdOn(on);
          },
        })
        .setEnabled(true, false);
      panel
        .registerLayer({
          id: 'survey',
          label: 'Independent survey',
          color: INDIGO,
          onToggle: function (on) {
            setPinsOn(on);
          },
        })
        .setEnabled(true, false);
      panel
        .registerLayer({
          id: 'plan',
          label: 'Plan · sampled PSUs',
          color: GREEN,
          onToggle: function (on) {
            setPlanOn(on);
          },
        })
        .setEnabled(true, false);
      // Work Areas = the SAMPLED work-area polygons (the sampled building
      // footprints, arm-coloured). Distinct from Building Footprints (all buildings).
      waLayerRef.current = panel.registerLayer({
        id: 'work-areas',
        label: 'Work areas',
        color: ARM.intervention,
        meta: 'Toggle to load',
        onToggle: function (on) {
          setWaOn(on);
        },
      });
      // Building Footprints = ALL buildings in the area (neutral amber).
      bfLayerRef.current = panel.registerLayer({
        id: 'footprints',
        label: 'Building footprints',
        color: AMBER,
        meta: 'Toggle to load',
        onToggle: function (on) {
          setBfOn(on);
        },
      });
      return undefined;
    },
    [mapLibReady],
  );

  if (!rd) {
    return (
      <div style={{ padding: '2rem', color: MUT, fontFamily: sans }}>
        Verified Monitoring — no data yet. Seed this run via regenerate.py.
      </div>
    );
  }

  // ---- per-round data ----
  var tWard = rd.treatment_ward || 'Program ward';
  var cWard = rd.comparison_ward || 'Comparison ward';
  var sd = rd.service_delivery_counts || {};
  var ver = rd.intervention_pct,
    self_ = rd.self_report_pct,
    prem = rd.premium_pp;
  var indN = rd.intervention_n || 0;
  var _indP = (ver || 0) / 100;
  var indCI =
    indN > 0 ? 1.96 * Math.sqrt((_indP * (1 - _indP)) / indN) * 100 : null;
  var q = rd.quality || {};
  var bc = rd.backcheck || {};

  // Per-category map-mark counts for the dot key, computed from the round's
  // overlay so the key shows HOW MANY of each mark are on the map (and makes the
  // control ward's "program absent" legible as a number, not just visual).
  function mapMarkCounts() {
    var ov = (rd && rd.overlay) || {};
    var deliv = ((ov.service_delivery || {}).features || []).length;
    var pins = (ov.survey_pins || {}).features || [];
    var conf = 0,
      notReached = 0;
    pins.forEach(function (f) {
      if ((f.properties || {}).confirmed) conf++;
      else notReached++;
    });
    return { delivery: deliv, confirmed: conf, notReached: notReached };
  }
  var mmc = mapMarkCounts();
  function _ct(n) {
    return n != null ? ' (' + n.toLocaleString() + ')' : '';
  }

  // Per-round, per-surveyor back-check — scoped to THIS round's records only.
  // The program rotates wards each cycle, so a surveyor label (T1..T6) is a
  // different person in a different ward each round; pooling their back-checks
  // across cycles would conflate them. Build each surveyor's profile from this
  // round's back-check rows (rd.backcheck.rows, tagged with the original
  // `enumerator`), computed the same way the all-cycles library does.
  function roundBackcheckBySurveyor() {
    var t2 = bc.t2_thresh_m || 25;
    var byS = {};
    (bc.rows || []).forEach(function (r) {
      if (!r.enumerator) return;
      (byS[r.enumerator] = byS[r.enumerator] || []).push(r);
    });
    function t1ok(r) {
      var fs = r.fields || [];
      for (var i = 0; i < fs.length; i++)
        if (fs[i].type === 'type1' && !fs[i].match) return false;
      return true;
    }
    function t2ok(r) {
      return r.gps_delta_m != null && r.gps_delta_m <= t2;
    }
    function t3ok(r) {
      var fs = r.fields || [];
      for (var i = 0; i < fs.length; i++)
        if (fs[i].type === 'outcome') return !!fs[i].match;
      return false;
    }
    function pctOf(rows, pred) {
      if (!rows.length) return null;
      var k = 0;
      for (var i = 0; i < rows.length; i++) if (pred(rows[i])) k++;
      return Math.round((1000 * k) / rows.length) / 10;
    }
    function mismatches(r) {
      var m = 0,
        fs = r.fields || [];
      for (var i = 0; i < fs.length; i++) if (!fs[i].match) m++;
      if (!t2ok(r)) m++;
      return m;
    }
    var out = {};
    Object.keys(byS).forEach(function (s) {
      var rows = byS[s].slice().sort(function (a, b) {
        return mismatches(b) - mismatches(a);
      });
      out[s] = {
        n: rows.length,
        type1_pct: pctOf(rows, t1ok),
        type2_pct: pctOf(rows, t2ok),
        type3_pct: pctOf(rows, t3ok),
        t2_thresh_m: t2,
        rows: rows,
      };
    });
    return out;
  }

  // The back-check always opens on a surveyor — default to the one whose work
  // most needs review (lowest outcome agreement); clicking a scorecard row
  // selects a different one. No confusing round-level mode.
  var sbMap = roundBackcheckBySurveyor();
  var bcIds = Object.keys(sbMap);
  function _t3(k) {
    return sbMap[k] && sbMap[k].type3_pct != null ? sbMap[k].type3_pct : 100;
  }
  var effSurv =
    selSurv && sbMap[selSurv]
      ? selSurv
      : bcIds.length
        ? bcIds.reduce(function (a, b) {
            return _t3(b) < _t3(a) ? b : a;
          }, bcIds[0])
        : null;

  // scorecard quality metrics: what each checks + the library detail key, so a
  // clicked cell can open a relevant info panel below the table.
  var QMETA = {
    evidence: {
      lib: 'evidence_capture',
      label: 'Evidence capture',
      blurb:
        'A proof photo on every "received" record — the auditable evidence behind a coverage claim.',
    },
    gps: {
      lib: 'gps_within_15m',
      label: 'GPS within 15 m',
      blurb:
        "The capture's GPS within 15 m of the assigned household — confirms the surveyor was actually there.",
    },
    completeness: {
      lib: 'field_completeness',
      label: 'Field completeness',
      blurb:
        'Every required field present on the record (no blanks left behind).',
    },
    duration: {
      lib: 'duration_plausibility',
      label: 'Interview duration',
      blurb:
        'Interview length within a plausible band — flags records too fast to be real.',
    },
    consistency: {
      lib: 'consistency_pass',
      label: 'Consistency checks',
      blurb:
        'Internal edit rules pass (e.g. a "received" record must have an eligible child present).',
    },
    duplicates: {
      lib: 'duplicate_integrity',
      label: 'Duplicate integrity',
      blurb:
        'No duplicate household IDs and no repeated (GPS, timestamp) — catches copy-pasted records.',
    },
  };

  // metric drill-through: one row per survey for the clicked quality cell, with
  // that metric's per-record value + flag. Fills the bottom widget (replaces the
  // back-check view while a quality cell is selected).
  function qmetricDrill(surveyor, key) {
    var m = QMETA[key];
    if (!m) return null;
    var scRows = rd.surveyor_scorecard || [];
    var row = surveyor
      ? scRows.filter(function (r) {
          return r.surveyor === surveyor;
        })[0]
      : null;
    var recs = row
      ? (row.records || []).slice()
      : scRows.reduce(function (a, r) {
          return a.concat(r.records || []);
        }, []);
    var val = row ? row[key] : null;
    var valTxt =
      val == null
        ? '—'
        : key === 'duplicates'
          ? val + ' dup'
          : Number(val).toFixed(1) + '%';

    function flagged(r) {
      if (key === 'evidence') return r.recv && r.photo !== true;
      if (key === 'gps') return r.gps != null && r.gps > 15;
      if (key === 'completeness') return (r.miss || []).length > 0;
      if (key === 'duration') return !!r.short;
      if (key === 'consistency') return !r.cons;
      if (key === 'duplicates') return !!r.dup;
      return false;
    }
    function sortVal(r) {
      if (key === 'gps') return -(r.gps || 0);
      if (key === 'duration') return r.dur == null ? 1e9 : r.dur;
      return flagged(r) ? 0 : 1;
    }
    recs.sort(function (a, b) {
      return sortVal(a) - sortVal(b);
    });
    var nFlag = recs.filter(flagged).length;
    var nTotal = recs.length;

    var th = {
      color: MUT,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '.03em',
      padding: '5px 9px',
      textAlign: 'left',
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var thR = Object.assign({}, th, { textAlign: 'right' });
    var td = {
      padding: '6px 9px',
      fontSize: 12.5,
      fontFamily: mono,
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var tdR = Object.assign({}, td, { textAlign: 'right' });
    var hhTd = Object.assign({}, td, {
      color: SUBINK,
      fontWeight: 600,
      fontFamily: 'inherit',
    });
    function bar(frac, color, thrFrac) {
      return (
        <span
          style={{
            position: 'relative',
            display: 'inline-block',
            width: 84,
            height: 7,
            borderRadius: 4,
            background: '#eef2f7',
            overflow: 'hidden',
            verticalAlign: 'middle',
            marginRight: 8,
          }}
        >
          <span
            style={{
              display: 'block',
              height: '100%',
              width: Math.max(0, Math.min(1, frac)) * 100 + '%',
              background: color,
            }}
          />
          {thrFrac != null ? (
            <span
              style={{
                position: 'absolute',
                top: -1,
                bottom: -1,
                left: Math.max(0, Math.min(1, thrFrac)) * 100 + '%',
                width: 0,
                borderLeft: '1.5px solid #475569',
              }}
            />
          ) : null}
        </span>
      );
    }

    var head, rowCells;
    if (key === 'gps') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>GPS offset from assigned</th>
          <th style={thR}>≤ 15 m</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="gps"
            style={Object.assign({}, td, {
              color: bad ? ROSE : SUBINK,
              fontWeight: bad ? 700 : 400,
              whiteSpace: 'nowrap',
            })}
          >
            {bar((r.gps || 0) / 60, bad ? ROSE : INDIGO, 15 / 60)}
            {r.gps == null ? '—' : r.gps.toFixed(0) + ' m'}
          </td>,
          <td
            key="ok"
            style={Object.assign({}, tdR, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'no' : 'yes'}
          </td>,
        ];
      };
    } else if (key === 'evidence') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Received vit-A</th>
          <th style={th}>Proof photo</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td key="recv" style={Object.assign({}, td, { color: SUBINK })}>
            {r.recv ? 'yes' : 'no'}
          </td>,
          <td
            key="photo"
            style={Object.assign({}, td, {
              color: bad ? ROSE : r.recv ? GREEN : MUT,
              fontWeight: bad ? 700 : 400,
            })}
          >
            {r.recv ? (r.photo ? 'yes' : 'MISSING') : 'n/a'}
          </td>,
        ];
      };
    } else if (key === 'duration') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={thR}>Interview duration</th>
          <th style={thR}>Too fast</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="dur"
            style={Object.assign({}, tdR, {
              color: bad ? ROSE : SUBINK,
              fontWeight: bad ? 700 : 400,
            })}
          >
            {bar((r.dur || 0) / 30, bad ? ROSE : INDIGO)}
            {r.dur == null ? '—' : r.dur.toFixed(1) + ' min'}
          </td>,
          <td
            key="ok"
            style={Object.assign({}, tdR, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'yes' : 'no'}
          </td>,
        ];
      };
    } else if (key === 'completeness') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Missing required fields</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="miss"
            style={Object.assign({}, td, {
              color: bad ? ROSE : GREEN,
              fontWeight: bad ? 700 : 400,
            })}
          >
            {bad ? (r.miss || []).join(', ') : 'complete'}
          </td>,
        ];
      };
    } else if (key === 'consistency') {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Received</th>
          <th style={th}>Edit checks</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td key="recv" style={Object.assign({}, td, { color: MUT })}>
            {r.recv ? 'yes' : 'no'}
          </td>,
          <td
            key="ok"
            style={Object.assign({}, td, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'violation' : 'pass'}
          </td>,
        ];
      };
    } else {
      head = (
        <tr>
          <th style={th}>Household</th>
          <th style={th}>Duplicate record</th>
        </tr>
      );
      rowCells = function (r) {
        var bad = flagged(r);
        return [
          <td key="hh" style={hhTd}>
            {r.hh}
          </td>,
          <td
            key="dup"
            style={Object.assign({}, td, {
              color: bad ? ROSE : GREEN,
              fontWeight: 700,
            })}
          >
            {bad ? 'duplicate' : 'unique'}
          </td>,
        ];
      };
    }
    var who = surveyor ? 'Surveyor ' + surveyor : 'all surveyors';
    return (
      <div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            gap: 10,
            flexWrap: 'wrap',
            marginBottom: 2,
          }}
        >
          <div style={{ color: SUBINK, fontWeight: 700, fontSize: 13 }}>
            {m.label}{' '}
            <span style={{ color: MUT, fontWeight: 400 }}>
              · {who} · this cycle
            </span>{' '}
            <span style={{ fontFamily: mono, color: INDIGO }}>{valTxt}</span>
            {/* On-screen count chip so the flagged/within split is SHOWN, not
                just stated in the caption — the all-red visible rows otherwise
                read as a filtered list. */}
            <span
              style={{
                marginLeft: 8,
                fontSize: 10.5,
                fontFamily: mono,
                fontWeight: 700,
                color: ROSE,
                background: '#fff1f2',
                border: '1px solid #fecdd3',
                borderRadius: 5,
                padding: '2px 7px',
                verticalAlign: 'middle',
              }}
            >
              {nFlag} flagged · {nTotal - nFlag} ok · {nTotal} total
            </span>
          </div>
          <button
            onClick={function () {
              setQSel(null);
            }}
            style={{
              cursor: 'pointer',
              border: '1px solid ' + LINE,
              background: '#fff',
              color: INDIGO,
              borderRadius: 7,
              fontSize: 11,
              padding: '3px 9px',
              fontFamily: sans,
            }}
          >
            ← back-check
          </button>
        </div>
        <div style={{ color: MUT, fontSize: 11.5, marginBottom: 8 }}>
          {m.blurb} Every survey this cycle — the full census of {nTotal},
          sorted worst-first: {nFlag} beyond threshold, then the{' '}
          {nTotal - nFlag} within (scroll for the rest).
        </div>
        <div style={{ overflow: 'auto', maxHeight: 380 }}>
          <table
            style={{ borderCollapse: 'collapse', width: '100%', minWidth: 440 }}
          >
            <thead style={{ position: 'sticky', top: 0, background: '#fff' }}>
              {head}
            </thead>
            <tbody>
              {recs.map(function (r, i) {
                return <tr key={i}>{rowCells(r)}</tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  function pct(x) {
    return x == null ? '—' : x.toFixed(1) + '%';
  }
  function pp(x) {
    return x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1) + ' pts';
  }
  function yn(v) {
    return v === true ? 'yes' : v === false ? 'no' : v == null ? '—' : '' + v;
  }
  function metricVal(m) {
    if (!m || m.value == null) return '—';
    if (m.unit === 'count') return '' + m.value;
    return (typeof m.value === 'number' ? m.value.toFixed(1) : m.value) + '%';
  }
  var cardStyle = {
    background: PANEL,
    border: '1px solid ' + LINE,
    borderRadius: 12,
    boxShadow: SHADOW,
    // Clear the host app's sticky top bar when a section is scrolled to, so its
    // header row isn't clipped under the Connect Labs nav.
    scrollMarginTop: 84,
  };

  function sw(color, dashed) {
    return (
      <span
        style={{
          display: 'inline-block',
          width: 14,
          height: dashed ? 0 : 10,
          borderTop: dashed ? '2px dashed ' + color : 'none',
          background: dashed ? 'none' : color,
          borderRadius: dashed ? 0 : 3,
          marginRight: 5,
          verticalAlign: 'middle',
        }}
      />
    );
  }

  // ---- PAGE HERO: the six-cycle trend (edge-to-edge, clickable cycles) ----
  function trendChart() {
    var iv = trend.intervention || [],
      cp = trend.comparison || [],
      srr = trend.self_report || [],
      rr = trend.rounds || [];
    var n = Math.max(iv.length, cp.length, srr.length);
    if (n < 2) return null;
    var w = 1040,
      h = 440,
      padL = 56,
      padR = 188,
      padT = 20,
      padB = 46;
    function X(i) {
      return padL + (i / (n - 1)) * (w - padL - padR);
    }
    function Y(v) {
      return h - padB - ((v || 0) / 100) * (h - padT - padB);
    }
    function poly(a) {
      return a
        .map(function (v, i) {
          return X(i) + ',' + Y(v);
        })
        .join(' ');
    }
    var band = '';
    if (srr.length && iv.length) {
      band =
        srr
          .map(function (v, i) {
            return X(i) + ',' + Y(v);
          })
          .join(' ') +
        ' ' +
        iv
          .slice()
          .reverse()
          .map(function (v, i) {
            return X(iv.length - 1 - i) + ',' + Y(v);
          })
          .join(' ');
    }
    var grid = [0, 25, 50, 75, 100].map(function (g) {
      var isFloor = g === 0;
      return (
        <g key={g}>
          {/* The 0% baseline is drawn heavier (a real axis, not a faint grid
              line) so a control line resting on the floor reads against a solid
              edge instead of vanishing into the bottom grid line. */}
          <line
            x1={padL}
            y1={Y(g)}
            x2={w - padR}
            y2={Y(g)}
            stroke={isFloor ? '#cbd5e1' : LINE}
            strokeWidth={isFloor ? 1.5 : 1}
          />
          <text
            x={22}
            y={Y(g) + 4}
            fill={isFloor ? SUBINK : MUT}
            fontWeight={isFloor ? 600 : 400}
            fontSize="13"
            fontFamily={mono}
          >
            {g + '%'}
          </text>
        </g>
      );
    });
    // DIRECT end-of-line label: a dot at the final point + the line's name and
    // value in the gutter (padR reserves room). `dy` nudges the label off the
    // floor/ceiling when two ends would collide or a line sits at the 0% floor —
    // so the crushed control line still reads its own name + value.
    function endLabel(arr, color, label, dy) {
      if (!arr.length) return null;
      var i = arr.length - 1;
      var ly = Y(arr[i]) + 5 + (dy || 0);
      return (
        <g>
          <circle
            cx={X(i)}
            cy={Y(arr[i])}
            r="6"
            fill={color}
            stroke="#fff"
            strokeWidth="2"
          />
          {/* connector from the dot to a lifted label, so a floor-crushed line's
              label can sit above the axis and still point back at its end-dot. */}
          {dy ? (
            <line
              x1={X(i) + 7}
              y1={Y(arr[i])}
              x2={X(i) + 16}
              y2={ly - 4}
              stroke={color}
              strokeWidth="1"
              strokeOpacity="0.5"
            />
          ) : null}
          <text
            x={X(i) + 18}
            y={ly}
            fill={color}
            fontSize="14.5"
            fontWeight="700"
          >
            {label + ' ' + pct(arr[i])}
          </text>
        </g>
      );
    }
    // ONE label vocabulary for the three lines, used by the markers' hover
    // tooltip, the direct end-labels, and the legend below — so the same line is
    // never called two different things.
    var L_SD = 'Service delivery',
      L_IV = 'Intervention survey',
      L_CP = 'Control survey';
    var SERIES = [
      { arr: cp, color: COMP, label: L_CP },
      { arr: srr, color: AMBER, label: L_SD },
      { arr: iv, color: INDIGO, label: L_IV },
    ];
    function markers() {
      return SERIES.map(function (s) {
        return s.arr.map(function (v, i) {
          var on = hoverPt && hoverPt.label === s.label && hoverPt.i === i;
          return (
            <g key={s.label + i}>
              <circle
                cx={X(i)}
                cy={Y(v)}
                r={on ? 7 : 4.6}
                fill={s.color}
                stroke="#fff"
                strokeWidth="2"
              />
              <circle
                cx={X(i)}
                cy={Y(v)}
                r="11"
                fill="transparent"
                style={{ cursor: 'pointer' }}
                onMouseEnter={(function (px, py, val, col, lab, idx, rnd) {
                  return function () {
                    setHoverPt({
                      x: px,
                      y: py,
                      v: val,
                      color: col,
                      label: lab,
                      i: idx,
                      r: rnd,
                    });
                  };
                })(X(i), Y(v), v, s.color, s.label, i, rr[i])}
                onMouseLeave={function () {
                  setHoverPt(null);
                }}
                onClick={(function (idx) {
                  return function () {
                    setSel(idx);
                  };
                })(i)}
              />
            </g>
          );
        });
      });
    }
    function tip() {
      if (!hoverPt) return null;
      var label = hoverPt.label + ' · R' + hoverPt.r + ' · ' + pct(hoverPt.v);
      var fs = 8.5,
        th = 16,
        textX = 16; // left pad: dot + gap
      // size the box to the text so white text never spills past the dark fill
      var tw = textX + label.length * fs * 0.6 + 8;
      var tx = Math.max(2, Math.min(w - tw - 2, hoverPt.x - tw / 2));
      var ty = hoverPt.y - th - 8;
      if (ty < 2) ty = hoverPt.y + 10;
      return (
        <g pointerEvents="none">
          <rect
            x={tx}
            y={ty}
            width={tw}
            height={th}
            rx="3"
            fill="#0f172a"
            opacity="0.93"
          />
          <circle cx={tx + 8} cy={ty + th / 2} r="2.8" fill={hoverPt.color} />
          <text
            x={tx + textX}
            y={ty + th / 2 + 3}
            fill="#fff"
            fontSize={fs}
            fontFamily={mono}
          >
            {label}
          </text>
        </g>
      );
    }
    return (
      <svg
        width="100%"
        viewBox={'0 0 ' + w + ' ' + h}
        style={{ display: 'block' }}
      >
        {grid}
        <text
          x={-(padT + (h - padB - padT) / 2)}
          y={11}
          transform="rotate(-90)"
          textAnchor="middle"
          fill={MUT}
          fontSize="11.5"
          fontFamily={mono}
          style={{ letterSpacing: '.04em' }}
        >
          % confirmed / reported
        </text>
        {(function () {
          var hx0 = Math.max(padL, X(sel) - 26);
          var hx1 = Math.min(w - padR, X(sel) + 26);
          return (
            <rect
              x={hx0}
              y={padT}
              width={Math.max(0, hx1 - hx0)}
              height={h - padB - padT}
              fill={INDIGO}
              fillOpacity="0.12"
              stroke={INDIGO}
              strokeOpacity="0.35"
              strokeWidth="1"
            />
          );
        })()}
        {band ? (
          <polygon points={band} fill={AMBER} fillOpacity="0.28" />
        ) : null}
        {/* Label the amber band IN PLACE (not only in the footnote): drop a
            small inline tag at the mid-round, vertically between the two series
            it spans (self-report above, intervention survey below). */}
        {/* White halo under the control line so where it rests on the 0% floor
            it still separates from the heavier axis line and reads as its own
            mark rather than merging into the baseline. */}
        <polyline
          points={poly(cp)}
          fill="none"
          stroke="#ffffff"
          strokeWidth="6.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <polyline
          points={poly(cp)}
          fill="none"
          stroke={COMP}
          strokeWidth="3.6"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <polyline
          points={poly(srr)}
          fill="none"
          stroke={AMBER}
          strokeWidth="4"
          strokeDasharray="9 5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <polyline
          points={poly(iv)}
          fill="none"
          stroke={INDIGO}
          strokeWidth="4.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {rr.map(function (r, i) {
          return (
            <g key={i}>
              <text
                x={X(i)}
                y={h - 20}
                fill={i === sel ? INDIGO : MUT}
                fontWeight={i === sel ? '700' : '400'}
                fontSize="13.5"
                fontFamily={mono}
                textAnchor="middle"
              >
                {'R' + r}
              </text>
              <text
                x={X(i)}
                y={h - 5}
                fill={i === sel ? INDIGO : '#94a3b8'}
                fontSize="11.5"
                textAnchor="middle"
              >
                {(rounds[i] || {}).label ||
                  (rounds[i] || {}).treatment_ward ||
                  ''}
              </text>
              <rect
                x={X(i) - 26}
                y={padT}
                width="52"
                height={h - padB - padT}
                fill="transparent"
                style={{ cursor: 'pointer' }}
                onClick={(function (idx) {
                  return function () {
                    setSel(idx);
                  };
                })(i)}
              />
            </g>
          );
        })}
        {markers()}
        {endLabel(srr, AMBER, L_SD)}
        {endLabel(iv, INDIGO, L_IV)}
        {/* Control sits at/near the 0% floor; lift its direct label well above
            the axis (negative dy) so its name + value aren't crushed onto the
            bottom grid line. */}
        {endLabel(cp, COMP, L_CP, -26)}
        {tip()}
      </svg>
    );
  }

  // ---- shared small label ----
  function dlbl(t) {
    return (
      <div
        style={{
          color: MUT,
          fontSize: 11,
          textTransform: 'uppercase',
          letterSpacing: '.04em',
        }}
      >
        {t}
      </div>
    );
  }

  // ---- per-surveyor quality scorecard ----
  // One row per program-ward surveyor; KPI columns computed by
  // commcare_connect.labs.synthetic.generator.core.survey_quality over THAT surveyor's records
  // (their primaries + the back-checks of their work). Cells turn rose when
  // they fall below the column threshold; a surveyor whose integrity signals
  // fail together is tagged REVIEW.
  function scorecardTable() {
    var rows = rd.surveyor_scorecard || [];
    if (!rows.length) return null;
    // Back-check column shows THIS round's per-surveyor outcome agreement (the
    // exact number the drill-in headlines), scoped to the round via the
    // round-level sbMap above — surveyors rotate wards each cycle, so the
    // back-check belongs to the round it happened in, like the other columns.
    rows = rows.map(function (r) {
      var sb = sbMap[r.surveyor];
      return sb
        ? Object.assign({}, r, { backcheck: sb.type3_pct, backcheck_n: sb.n })
        : r;
    });
    var sbVals = Object.keys(sbMap).map(function (k) {
      return sbMap[k];
    });
    var aggBcN = sbVals.reduce(function (a, s) {
      return a + (s.n || 0);
    }, 0);
    var aggBc = aggBcN
      ? sbVals.reduce(function (a, s) {
          return a + (s.type3_pct || 0) * (s.n || 0);
        }, 0) / aggBcN
      : null;
    // [key, label, threshold, lowerIsBetter, isCount, sublabel, tooltip, deemph]
    // Each column carries a plain-language sublabel (shown under the header) and
    // a hover tooltip so a non-technical program lead can read it unaided.
    // deemph=true greys a column that is a near-universal trust-floor (passes at
    // ~100% for everyone), so the discriminating columns hold the eye.
    var COLS_ALL = [
      [
        'evidence',
        'Evidence',
        90,
        false,
        false,
        'photo on file',
        '% of records with a proof photo attached — the auditable evidence behind a coverage claim.',
      ],
      [
        'gps',
        'GPS self-report',
        90,
        false,
        false,
        'self-reported ≤15 m',
        "% of the surveyor's OWN captures whose GPS lands within 15 m of the assigned household. The app gates capture at 15 m, so this passes for almost everyone — a trust-floor, not a discriminator. The independent re-survey's location check is the back-check column.",
        true,
      ],
      [
        'primary_rate',
        'On sampled house',
        85,
        false,
        false,
        'not a substitute',
        '% surveyed at the originally-sampled (primary) household rather than a substitute backup.',
      ],
      [
        'completeness',
        'Complete',
        98,
        false,
        false,
        'no blanks',
        '% of records with every required field filled in — no blanks left behind.',
      ],
      [
        'duration',
        'Interview length',
        90,
        false,
        false,
        'not too fast',
        '% of interviews within a plausible time band — flags records too fast to be real.',
      ],
      [
        'consistency',
        'Passed validation',
        98,
        false,
        false,
        'no logic errors',
        '% passing internal edit checks (e.g. a "received" record must have an eligible child present).',
      ],
      [
        'duplicates',
        'Duplicates',
        0,
        true,
        true,
        'copied records',
        'Count of duplicate household IDs or repeated (GPS, time) — catches copy-pasted records. Lower is better.',
      ],
      [
        'backcheck',
        'Re-survey agreement',
        90,
        false,
        false,
        'agrees on re-visit',
        '% of facts that matched when an independent surveyor re-visited a sample of households.',
      ],
    ];
    // Drop the On-sampled-house column entirely when the round carries no
    // primary-rate data (un-grounded rounds): a present-but-empty headline column
    // is worse than no column. Shows real values (incl. the flagged surveyor's
    // heavy substitution) on plan-grounded rounds.
    var hasPrimary = rows.some(function (r) {
      return r.primary_rate != null;
    });
    var COLS = COLS_ALL.filter(function (c) {
      return c[0] !== 'primary_rate' || hasPrimary;
    });
    function fail(v, thr, lower) {
      if (v == null) return false;
      return lower ? v > thr : v < thr;
    }
    function cellTxt(row, c) {
      var v = row[c[0]];
      if (v == null) return '—';
      if (c[4]) return String(v);
      var s = v.toFixed(1) + '%';
      if (c[0] === 'backcheck' && row.backcheck_n != null)
        s += ' · n=' + row.backcheck_n;
      return s;
    }
    // flag a surveyor for review when >=2 integrity signals fail together.
    // The criteria + their plain-language read are kept in one place so the
    // REVIEW badge and the inline "why flagged" note name the SAME failing cells.
    var FLAG_CRITERIA = [
      ['evidence', 90, false, 'proof photos missing'],
      ['gps', 90, false, 'self-reported GPS off'],
      ['primary_rate', 85, false, 'heavy substitution off the sampled house'],
      ['backcheck', 90, false, 'the independent re-survey disagreed'],
    ];
    function flagReasons(row) {
      return FLAG_CRITERIA.filter(function (c) {
        return fail(row[c[0]], c[1], c[2]);
      });
    }
    function rowFlagged(row) {
      return flagReasons(row).length >= 2;
    }
    // human-readable column label for a criterion key (matches COLS headers)
    function labelFor(key) {
      for (var i = 0; i < COLS_ALL.length; i++)
        if (COLS_ALL[i][0] === key) return COLS_ALL[i][1];
      return key;
    }
    var th = {
      textAlign: 'right',
      color: MUT,
      fontSize: 10.5,
      textTransform: 'uppercase',
      letterSpacing: '.04em',
      padding: '9px 12px',
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var th0 = Object.assign({}, th, { textAlign: 'left' });
    // Larger cell type + taller rows so the values, the REVIEW badge, and the
    // inline "why flagged" note all read at a normal viewport — the scorecard is
    // the narrated subject in scene 3 and must be legible, not sub-legible.
    var td = {
      textAlign: 'right',
      padding: '11px 12px',
      fontSize: 13.5,
      fontFamily: mono,
      borderBottom: '1px solid ' + LINE,
    };
    var td0 = Object.assign({}, td, {
      textAlign: 'left',
      fontFamily: 'inherit',
    });
    var agg = {
      surveyor: '__agg__',
      n: indN,
      evidence: q.evidence_capture && q.evidence_capture.value,
      gps: q.gps_within_15m && q.gps_within_15m.value,
      primary_rate: q.primary_rate && q.primary_rate.value,
      completeness: q.field_completeness && q.field_completeness.value,
      duration: q.duration_plausibility && q.duration_plausibility.value,
      consistency: q.consistency_pass && q.consistency_pass.value,
      duplicates:
        q.duplicate_integrity && q.duplicate_integrity.detail
          ? (q.duplicate_integrity.detail.dup_household_id || 0) +
            (q.duplicate_integrity.detail.dup_gps_time || 0)
          : 0,
      backcheck: aggBc,
      backcheck_n: aggBcN,
    };
    function dataRow(row, isAgg) {
      var fl = !isAgg && rowFlagged(row);
      var on = !isAgg && effSurv === row.surveyor;
      // Build the inline "why flagged" note: name the trigger cells in plain
      // language so the REVIEW tag is self-explaining and anchored to its cells.
      var reasons = fl ? flagReasons(row) : [];
      var noteText = reasons.length
        ? 'REVIEW: ' +
          reasons
            .map(function (c) {
              return labelFor(c[0]) + ' — ' + c[3];
            })
            .join('; ') +
          '. Two or more integrity signals failed together.'
        : null;
      var mainRow = (
        <tr
          key={row.surveyor}
          style={{
            background: on
              ? '#eef2ff'
              : isAgg
                ? '#f8fafc'
                : fl
                  ? '#fff1f2'
                  : 'transparent',
            // The selection accent (blue) and the flagged STATUS RAIL (rose) are
            // distinct signals, so they must not collide on the same left edge.
            // Blue inset stays for "selected"; the flagged row's rose rail is
            // painted as a left border on its first cell below, a different
            // channel, so a selected-and-flagged row shows both.
            boxShadow: on ? 'inset 3px 0 0 ' + INDIGO : 'none',
          }}
        >
          <td
            onClick={
              isAgg
                ? null
                : function () {
                    setQSel(null);
                    setSelSurv(row.surveyor);
                  }
            }
            title={isAgg ? null : "Show this surveyor's back-check below"}
            style={Object.assign({}, td0, {
              fontWeight: isAgg ? 700 : 600,
              fontSize: fl ? 14 : 13.5,
              color: SUBINK,
              cursor: isAgg ? 'default' : 'pointer',
              // STRONG left-edge STATUS RAIL on a flagged REVIEW row — a solid
              // rose band, visually distinct from the blue selection inset, so
              // the flagged row reads heavier than the passing rows and the eye
              // lands on it. Padding shifts to clear the rail.
              borderLeft: fl ? '5px solid ' + ROSE : '5px solid transparent',
              paddingLeft: 14,
            })}
          >
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 9,
                flexWrap: 'wrap',
              }}
            >
              <span>
                {isAgg ? 'Round · all surveyors' : 'Surveyor ' + row.surveyor}
              </span>
              {fl ? (
                <span
                  style={{
                    fontSize: 10.5,
                    color: '#fff',
                    background: ROSE,
                    fontFamily: mono,
                    fontWeight: 800,
                    letterSpacing: '.06em',
                    padding: '3px 9px',
                    borderRadius: 6,
                    lineHeight: 1.2,
                    boxShadow: '0 1px 2px rgba(190,18,60,0.35)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  REVIEW
                </span>
              ) : null}
            </span>
          </td>
          <td style={Object.assign({}, td, { color: MUT })}>{row.n}</td>
          {COLS.map(function (c) {
            var v = row[c[0]];
            var bad = fail(v, c[2], c[3]);
            var isBc = c[0] === 'backcheck';
            // Cells that participate in the REVIEW flag rule (>=2 of these fail
            // together) — so a failing one of these is what the row's REVIEW
            // badge is pointing at. Used to make the offending cell pop hardest.
            var flagCriterion =
              c[0] === 'evidence' ||
              c[0] === 'gps' ||
              c[0] === 'primary_rate' ||
              c[0] === 'backcheck';
            var isOffender = !isAgg && fl && bad && flagCriterion;
            // back-check cell selects the surveyor (drives the section below);
            // a quality cell opens the metric info panel for that surveyor.
            var clickable = isBc ? !isAgg : !!QMETA[c[0]];
            var selCell =
              !isBc &&
              qSel &&
              qSel.key === c[0] &&
              qSel.surveyor === (isAgg ? null : row.surveyor);
            function onCell() {
              if (isBc) {
                if (!isAgg) {
                  setQSel(null);
                  setSelSurv(row.surveyor);
                }
              } else if (QMETA[c[0]]) {
                if (!isAgg) setSelSurv(row.surveyor);
                setQSel({
                  key: c[0],
                  surveyor: isAgg ? null : row.surveyor,
                  value: v,
                });
              }
            }
            // Trust-floor columns (deemph) are greyed so the discriminating
            // columns hold the eye — unless THIS cell is actually below
            // threshold, in which case it still reads in full rose.
            var deemph = c[7] && !bad;
            return (
              <td
                key={c[0]}
                data-cell={(isAgg ? 'all' : row.surveyor) + ':' + c[0]}
                onClick={clickable ? onCell : null}
                title={
                  clickable
                    ? isBc
                      ? "Show this surveyor's back-check below"
                      : 'What this metric checks'
                    : null
                }
                style={Object.assign({}, td, {
                  // Below-threshold cells are marked by RED TEXT only — no bright
                  // fill. The value's own colour carries the signal so the table
                  // reads as clean objective data, not a shouted verdict. A cell
                  // that drives the REVIEW flag (isOffender) gets an underline so
                  // the badge points at concrete cells.
                  color: v == null ? MUT : bad ? ROSE : deemph ? SLATE : GREEN,
                  fontWeight: bad ? 800 : deemph ? 400 : 500,
                  opacity: deemph ? 0.7 : 1,
                  background: 'transparent',
                  cursor: clickable ? 'pointer' : 'default',
                  textDecoration: isOffender ? 'underline' : 'none',
                  textDecorationColor: isOffender ? ROSE : undefined,
                  textUnderlineOffset: isOffender ? 3 : undefined,
                  boxShadow: selCell
                    ? 'inset 0 0 0 1.5px ' + INDIGO
                    : isOffender
                      ? 'inset 0 0 0 1.5px ' + ROSE
                      : 'none',
                })}
              >
                {cellTxt(row, c)}
              </td>
            );
          })}
        </tr>
      );
      var noteRow = noteText ? (
        <tr key={row.surveyor + '-note'}>
          <td
            colSpan={2 + COLS.length}
            style={{
              // carry the rose status rail down onto the note so the badge, the
              // note, and the row read as one flagged block; larger note type so
              // the "why flagged" reason is legible, not fine-print.
              padding: '2px 12px 11px 26px',
              borderLeft: '5px solid ' + ROSE,
              borderBottom: '1px solid ' + LINE,
              fontSize: 12,
              lineHeight: 1.5,
              color: '#9f1239',
              background: on || fl ? '#fff1f2' : 'transparent',
            }}
          >
            {noteText}
          </td>
        </tr>
      ) : null;
      return [mainRow, noteRow];
    }
    return (
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{ borderCollapse: 'collapse', width: '100%', minWidth: 660 }}
        >
          <thead>
            <tr>
              <th style={th0}>Surveyor</th>
              <th style={th}>n</th>
              {COLS.map(function (c) {
                var subLabel = c[5];
                if (c[0] === 'backcheck')
                  subLabel = (subLabel || '') + ' · this round';
                return (
                  <th
                    key={c[0]}
                    style={Object.assign({}, th, {
                      cursor: 'help',
                      // trust-floor columns: greyed header so the eye lands on
                      // the discriminating columns instead.
                      color: c[7] ? '#cbd5e1' : MUT,
                      fontWeight: c[7] ? 400 : 500,
                    })}
                    title={c[6]}
                  >
                    {c[1]}
                    {subLabel ? (
                      <div
                        style={{
                          fontSize: 8.5,
                          color: c[7] ? '#cbd5e1' : '#94a3b8',
                          fontWeight: 400,
                          textTransform: 'none',
                          letterSpacing: 0,
                          marginTop: 1,
                        }}
                      >
                        {subLabel}
                      </div>
                    ) : null}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map(function (row) {
              return dataRow(row, false);
            })}
            {dataRow(agg, true)}
          </tbody>
        </table>
      </div>
    );
  }

  // ---- Layer-3 statistical fabrication screen ("Distributions") ----
  // One row per program-ward surveyor: robust (median/MAD) z-scores vs peers on
  // dose yes-rate, interview speed, and answer-distribution uniformity, plus a
  // composite band — all computed server-side via the shared `outlier` layer and
  // read straight off rd.surveyor_distributions. Needs NO second field visit.
  // (GPS co-location is intentionally omitted: on plan-grounded data every survey
  // lands on a distinct real footprint, so that signal is structurally zero.)
  // Clicking a surveyor drives the same back-check selection as the scorecard.
  function distributionsTable() {
    var rows = rd.surveyor_distributions || [];
    if (!rows.length) return null;
    // per-surveyor survey count, from the scorecard rows in state (same
    // surveyors) — the distributions rows don't carry their own n.
    var scN = {};
    (rd.surveyor_scorecard || []).forEach(function (r) {
      scN[r.surveyor] = r.n;
    });
    var thD = {
      textAlign: 'right',
      color: MUT,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '.04em',
      padding: '7px 10px',
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var thD0 = Object.assign({}, thD, { textAlign: 'left' });
    var tdD = {
      textAlign: 'right',
      padding: '8px 10px',
      fontSize: 12.5,
      fontFamily: mono,
      borderBottom: '1px solid ' + LINE,
    };
    var tdD0 = Object.assign({}, tdD, {
      textAlign: 'left',
      fontFamily: 'inherit',
      fontWeight: 600,
      color: SUBINK,
    });
    var subStyle = {
      fontSize: 9,
      color: '#94a3b8',
      fontWeight: 400,
      fontFamily: mono,
    };
    var glossStyle = {
      fontSize: 8.5,
      color: '#94a3b8',
      fontWeight: 400,
      textTransform: 'none',
      letterSpacing: 0,
    };
    function bandColor(b) {
      return b === 'red' ? ROSE : b === 'amber' ? AMBER : GREEN;
    }
    // Composite band, computed in the render straight from the per-signal z's
    // against the SAME thresholds the legend states (flag |z|>3.5, elevated
    // |z|>=2), so the pill can never disagree with the legend. The server's
    // weighted `band` could read AMBER while a single signal genuinely cleared
    // the flag bar (e.g. interview-speed |z|=6.5) — that contradiction is what
    // this removes: ANY signal over the flag line ⇒ the composite is a real flag.
    var FLAG_Z = 3.5,
      ELEV_Z = 2;
    function compositeBand(r) {
      var zs = [r.yes_z, r.speed_z, r.uniformity_z];
      var maxA = 0;
      for (var i = 0; i < zs.length; i++)
        if (zs[i] != null) maxA = Math.max(maxA, Math.abs(zs[i]));
      return maxA > FLAG_Z ? 'red' : maxA >= ELEV_Z ? 'amber' : 'green';
    }
    // Combined lens: each signal on its own RAW-units axis (minutes / % / HHI)
    // with the team-typical median as the centre line, a dark notch at this
    // surveyor's actual value (where they sit + which side), and a coloured bar
    // whose WIDTH is |z| — a thin mark for a normal surveyor, a fat red bar for
    // a clear outlier.
    var SW = 120; // lens width (px) — wide enough that even a normal-row bar reads at 6-row density
    function _vals(key) {
      return rows
        .map(function (r) {
          return r[key];
        })
        .filter(function (v) {
          return v != null;
        });
    }
    function _domain(key) {
      var v = _vals(key);
      if (!v.length) return { lo: 0, hi: 1, center: 0.5 };
      var s = v.slice().sort(function (a, b) {
        return a - b;
      });
      var n = s.length;
      var med = n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
      var lo = s[0];
      var hi = s[n - 1];
      var pad = (hi - lo) * 0.18 || Math.abs(med) * 0.1 || 1;
      // rawLo/rawHi = the unpadded peer spread, drawn as a faint band so a row's
      // value is read against where the rest of the team actually sits.
      return { lo: lo - pad, hi: hi + pad, center: med, rawLo: lo, rawHi: hi };
    }
    var DOMS = {
      yes_rate: _domain('yes_rate'),
      speed_med: _domain('speed_med'),
      uniformity_hhi: _domain('uniformity_hhi'),
    };
    function px(v, dom) {
      var span = dom.hi - dom.lo || 1;
      var c = Math.max(dom.lo, Math.min(dom.hi, v));
      return ((c - dom.lo) / span) * SW;
    }
    // CAP for the |z| bar: at/above the flag threshold (3.5) the bar is at full
    // width and we draw an off-scale marker, so an off-scale outlier reads as
    // MORE extreme (capped + chevron) rather than identical to its neighbour. The
    // numeric "z ±N" label disambiguates a -9.0 from a +6.7.
    var ZCAP = 3.5;
    function zWidth(z) {
      // |z| -> bar width px: a clear floor so even a normal surveyor's bar is
      // legibly visible, scaling LINEARLY with |z| up to the CAP so in-range rows
      // show genuinely short, magnitude-faithful bars (no all-saturate).
      var frac = Math.min(Math.abs(z), ZCAP) / ZCAP;
      return Math.max(10, Math.min(SW - 6, frac * (SW - 6)));
    }
    // the "lens": faint peer-range band + centre line + |z|-width bar + value
    // notch. Bars are thicker/higher-contrast and a peer band gives the bar a
    // visible reference so its width reads as outlier size on every row.
    function lens(rawVal, z, dom) {
      var a = Math.abs(z);
      var hot = a > 3.5;
      var amb = !hot && a >= 2;
      var col = hot ? ROSE : amb ? AMBER : '#64748b';
      var ctr = px(dom.center, dom);
      var pos = px(rawVal, dom);
      var bandLo = px(dom.rawLo == null ? dom.lo : dom.rawLo, dom);
      var bandHi = px(dom.rawHi == null ? dom.hi : dom.rawHi, dom);
      var w = zWidth(z);
      var left = Math.max(0, Math.min(SW - w, pos - w / 2));
      return (
        <span
          style={{
            position: 'relative',
            width: SW,
            height: 12,
            flex: '0 0 auto',
          }}
          title="shaded = where the rest of the team falls (peer range); line = team-typical; notch = this surveyor's value; bar width = |z| (how big an outlier)"
        >
          {/* baseline track */}
          <span
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              top: 5,
              height: 2,
              background: '#eef2f7',
              borderRadius: 2,
            }}
          />
          {/* peer-range band */}
          <span
            style={{
              position: 'absolute',
              left: Math.min(bandLo, bandHi),
              width: Math.max(2, Math.abs(bandHi - bandLo)),
              top: 2,
              height: 8,
              background: '#e2e8f0',
              borderRadius: 3,
            }}
          />
          {/* team-typical centre line */}
          <span
            style={{
              position: 'absolute',
              left: ctr,
              top: 0,
              height: 12,
              width: 1.5,
              background: '#94a3b8',
            }}
          />
          {/* |z|-width bar */}
          <span
            style={{
              position: 'absolute',
              left: left,
              width: w,
              top: 3,
              height: 6,
              borderRadius: 3,
              background: col,
              opacity: 0.95,
            }}
          />
          {/* off-scale chevron — only when |z| exceeds the cap, pointing in the
              direction of the outlier, so a capped bar still reads as off-scale
              (more extreme), not equal to an at-cap row. */}
          {a > ZCAP ? (
            <span
              title={
                'off-scale: |z| = ' +
                a.toFixed(1) +
                ' (bar capped at ' +
                ZCAP +
                ')'
              }
              style={{
                position: 'absolute',
                top: -1,
                right: z >= 0 ? 0 : 'auto',
                left: z >= 0 ? 'auto' : 0,
                fontSize: 12,
                lineHeight: '14px',
                fontWeight: 700,
                color: col,
              }}
            >
              {z >= 0 ? '▸' : '◂'}
            </span>
          ) : null}
          {/* this surveyor's value notch */}
          <span
            style={{
              position: 'absolute',
              left: pos - 1,
              top: -1,
              height: 14,
              width: 2,
              borderRadius: 1,
              background: '#0f172a',
            }}
          />
        </span>
      );
    }
    // Plain-language read for a signal: lead with WHERE this surveyor sits in
    // words, so the cell is legible without decoding z. `kind` gives the read its
    // direction (faster/slower interviews, more/less uniform answers, higher/
    // lower received-rate).
    function readWord(kind, z) {
      var a = Math.abs(z);
      if (a < 2) return 'typical';
      var strong = a > 3.5;
      if (kind === 'speed')
        return z < 0
          ? strong
            ? 'far faster'
            : 'faster'
          : strong
            ? 'far slower'
            : 'slower';
      if (kind === 'uniformity')
        return z > 0
          ? strong
            ? 'far more uniform'
            : 'more uniform'
          : strong
            ? 'far more varied'
            : 'more varied';
      // yes-rate
      return z > 0
        ? strong
          ? 'far higher'
          : 'higher'
        : strong
          ? 'far lower'
          : 'lower';
    }
    // one compact row per signal: PLAIN READ · value · lens · z-chip
    function cell(rawVal, z, valTxt, dom, kind) {
      if (z == null) return <span style={{ color: MUT }}>—</span>;
      var a = Math.abs(z);
      var hot = a > 3.5;
      var amb = !hot && a >= 2;
      var read = readWord(kind, z);
      return (
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 9,
            justifyContent: 'flex-end',
          }}
        >
          {/* lead: plain-language read (the headline of the cell) */}
          <span
            style={{
              color: hot ? ROSE : amb ? '#b45309' : SUBINK,
              fontWeight: hot ? 700 : amb ? 600 : 500,
              fontFamily: sans,
              fontSize: 11.5,
              minWidth: 84,
              textAlign: 'right',
            }}
          >
            {read}
          </span>
          <span
            style={{
              color: MUT,
              fontSize: 11,
              minWidth: 34,
              textAlign: 'right',
            }}
          >
            {valTxt}
          </span>
          {lens(rawVal, z, dom)}
          {/* z demoted to a secondary chip (full definition on hover) */}
          <span
            title="z = standard deviations from the team-typical value. 0 = typical; further from 0 (either sign) = more unusual; past ±3.5 we flag it."
            style={{
              color: hot ? ROSE : amb ? AMBER : MUT,
              fontWeight: hot ? 700 : 500,
              fontSize: 9.5,
              fontFamily: mono,
              background: hot ? '#fff1f2' : amb ? '#fffbeb' : '#f1f5f9',
              borderRadius: 5,
              padding: '1px 5px',
              minWidth: 30,
              textAlign: 'center',
              cursor: 'help',
            }}
          >
            {Math.abs(z) > 10
              ? 'z' + (z < 0 ? '<−10' : '>10')
              : 'z' + (z >= 0 ? '+' : '') + z.toFixed(1)}
          </span>
        </span>
      );
    }
    function tag(fam) {
      return (
        <span
          style={{
            fontSize: 8,
            fontWeight: 800,
            padding: '1px 4px',
            borderRadius: 4,
            marginLeft: 4,
            background: fam === 'B' ? '#f0fdf4' : '#eef2ff',
            color: fam === 'B' ? GREEN : INDIGO,
          }}
        >
          {fam}
        </span>
      );
    }
    function pill(b) {
      var c = b || 'green';
      return (
        <span
          style={{
            display: 'inline-block',
            padding: '3px 11px',
            borderRadius: 999,
            fontSize: 11,
            fontWeight: 800,
            letterSpacing: '.03em',
            whiteSpace: 'nowrap',
            border:
              '1px solid ' +
              (c === 'red' ? '#fecdd3' : c === 'amber' ? '#fde68a' : '#a7f3d0'),
            background:
              c === 'red' ? '#fff1f2' : c === 'amber' ? '#fffbeb' : '#ecfdf5',
            color: c === 'red' ? ROSE : c === 'amber' ? '#b45309' : GREEN,
          }}
        >
          {c.toUpperCase()}
        </span>
      );
    }
    return (
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{ borderCollapse: 'collapse', width: '100%', minWidth: 560 }}
        >
          <thead>
            <tr>
              <th style={thD0}>Surveyor</th>
              <th
                style={Object.assign({}, thD, { cursor: 'help' })}
                title="Share of 'received' answers, compared with the rest of the team."
              >
                Dose yes-rate{tag('A')}
                <div style={glossStyle}>% saying received</div>
              </th>
              <th
                style={Object.assign({}, thD, { cursor: 'help' })}
                title="Typical (median) minutes per interview — too fast to be real is the classic fabrication tell."
              >
                Interview speed{tag('A')}
                <div style={glossStyle}>median minutes</div>
              </th>
              <th
                style={Object.assign({}, thD, { cursor: 'help' })}
                title="How concentrated this surveyor's answers are (HHI). Real fieldwork sees a natural spread of roof types; an unnaturally uniform mix suggests answers weren't really collected."
              >
                Answer uniformity{tag('B')}
                <div style={glossStyle}>how same-y answers are</div>
              </th>
              <th
                style={Object.assign({}, thD, {
                  cursor: 'help',
                  // Pin the Composite column to the right edge with a fixed width
                  // and no-wrap so the verdict header + RED/AMBER/GREEN pill never
                  // clip to "COM..." when the table is horizontally constrained.
                  position: 'sticky',
                  right: 0,
                  zIndex: 2,
                  background: '#fff',
                  minWidth: 96,
                  width: 96,
                  whiteSpace: 'nowrap',
                  boxShadow: '-6px 0 6px -6px rgba(16,24,40,0.12)',
                })}
                title="Overall band, set by the per-signal thresholds at left: RED if any signal clears the flag line (|z| > 3.5), AMBER if any is elevated (|z| >= 2), else GREEN."
              >
                Composite
                <div style={glossStyle}>any |z|&gt;3.5 ⇒ red</div>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(function (r) {
              var on = effSurv === r.surveyor;
              // Composite band computed here from the per-signal z's so it always
              // agrees with the per-signal thresholds the legend states.
              var band = compositeBand(r);
              // per-surveyor survey count, looked up from the scorecard rows in
              // state (same surveyors) so the row can show its n.
              var sn = scN[r.surveyor];
              // Plain-language explanation for a flagged (red) row, naming the
              // signal that drives the flag in human terms (e.g. interviews far
              // faster than the team norm).
              var teamSpeed = DOMS.speed_med.center;
              var noteText = null;
              if (band === 'red') {
                var parts = [];
                if (
                  r.speed_med != null &&
                  teamSpeed != null &&
                  Math.abs(r.speed_z || 0) > FLAG_Z
                ) {
                  parts.push(
                    'interviews ~' +
                      Math.round(r.speed_med) +
                      ' min vs ~' +
                      Math.round(teamSpeed) +
                      ' min team norm — implausibly fast',
                  );
                }
                if (Math.abs(r.uniformity_z || 0) > FLAG_Z)
                  parts.push('answers far more uniform than peers');
                if (Math.abs(r.yes_z || 0) > FLAG_Z)
                  parts.push('received-rate well off the team');
                noteText = parts.length
                  ? 'Flagged — a signal cleared |z| > ' +
                    FLAG_Z +
                    ': ' +
                    parts.join('; ') +
                    '.'
                  : 'Flagged: behaves unlike peers across multiple signals.';
              }
              return [
                <tr
                  key={r.surveyor}
                  style={{
                    background: on
                      ? '#eef2ff'
                      : band === 'red'
                        ? '#fff1f2'
                        : 'transparent',
                    boxShadow: on ? 'inset 3px 0 0 ' + INDIGO : 'none',
                  }}
                >
                  <td
                    onClick={function () {
                      setQSel(null);
                      setSelSurv(r.surveyor);
                    }}
                    title="Show this surveyor's back-check above"
                    style={Object.assign({}, tdD0, { cursor: 'pointer' })}
                  >
                    <span
                      style={{
                        display: 'inline-block',
                        width: 7,
                        height: 7,
                        borderRadius: '50%',
                        background: bandColor(band),
                        marginRight: 7,
                        verticalAlign: 'middle',
                      }}
                    />
                    {'Surveyor ' + r.surveyor}
                    {sn != null ? (
                      <span
                        style={{
                          color: MUT,
                          fontFamily: mono,
                          fontWeight: 600,
                          fontSize: 10.5,
                          marginLeft: 6,
                        }}
                      >
                        n={sn}
                      </span>
                    ) : null}
                  </td>
                  <td style={tdD}>
                    {cell(
                      r.yes_rate,
                      r.yes_z,
                      r.yes_rate != null ? Math.round(r.yes_rate) + '%' : '—',
                      DOMS.yes_rate,
                      'yes',
                    )}
                  </td>
                  <td style={tdD}>
                    {cell(
                      r.speed_med,
                      r.speed_z,
                      r.speed_med != null ? r.speed_med + 'm' : '—',
                      DOMS.speed_med,
                      'speed',
                    )}
                  </td>
                  <td style={tdD}>
                    {cell(
                      r.uniformity_hhi,
                      r.uniformity_z,
                      r.uniformity_hhi != null
                        ? r.uniformity_hhi.toFixed(2)
                        : '—',
                      DOMS.uniformity_hhi,
                      'uniformity',
                    )}
                  </td>
                  <td
                    style={Object.assign({}, tdD, {
                      // Match the sticky header: pin the verdict pill to the right
                      // edge so it never scrolls under / clips. Background tracks
                      // the row's so the pinned cell reads as part of its row.
                      position: 'sticky',
                      right: 0,
                      zIndex: 1,
                      minWidth: 96,
                      width: 96,
                      whiteSpace: 'nowrap',
                      background: on
                        ? '#eef2ff'
                        : band === 'red'
                          ? '#fff1f2'
                          : '#fff',
                      boxShadow: '-6px 0 6px -6px rgba(16,24,40,0.12)',
                    })}
                  >
                    {pill(band)}
                  </td>
                </tr>,
                noteText ? (
                  <tr key={r.surveyor + '-note'}>
                    <td
                      colSpan={5}
                      style={{
                        padding: '0 10px 9px 24px',
                        borderBottom: '1px solid ' + LINE,
                        fontSize: 11,
                        lineHeight: 1.5,
                        color: '#9f1239',
                        background:
                          on || band === 'red' ? '#fff1f2' : 'transparent',
                      }}
                    >
                      {noteText}
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </tbody>
        </table>
      </div>
    );
  }

  // Back-check sections — descriptive names up front (the J-PAL/IPA
  // "Type 1/2/3" labels are specialist jargon, so the provenance lives behind
  // an info button instead of in the column header).
  function bcSections(sb) {
    return [
      {
        key: 'identity',
        label: 'Identity match',
        pct: sb.type1_pct,
        thr: 90,
        fields: [
          ['child_present', 'Present'],
          ['child_sex', 'Sex'],
          ['child_age_months', 'Age'],
          ['roof_type', 'Roof'],
        ],
        info: "Stable facts that can't change between two visits — the child's sex and age, whether the household exists, and the household's roof type. If the re-survey disagrees here, it's the strongest sign the original record was made up.",
      },
      {
        key: 'location',
        label: 'GPS re-survey concordance',
        pct: sb.type2_pct,
        thr: 90,
        mode: 'distance',
        info: "Distance between the GPS the surveyor logged and where the INDEPENDENT re-survey found the household — a separate measure from the surveyor's own ≤15 m self-report on the scorecard. A large gap means the recorded location was wrong: a fraud-detection back-check.",
      },
      {
        key: 'outcome',
        label: 'Outcome agreement',
        pct: sb.type3_pct,
        thr: 90,
        fields: [['vitamin_a_received', 'Vitamin-A']],
        info: 'Whether the headline result — did the child receive vitamin A — held up when an independent surveyor re-visited. This is the key outcome the whole survey exists to measure.',
      },
    ];
  }

  // surveyor view: TWO rows per re-surveyed household (Original / Backcheck),
  // columns grouped under the three back-check sections. Section info opens as a
  // floating popup (does not reflow the table).
  function surveyorBackcheck(sid, sb) {
    var sections = bcSections(sb);
    var rows = sb.rows || [];
    var thr = sb.t2_thresh_m || 25;
    function fieldOf(row, key) {
      var fs = row.fields || [];
      for (var i = 0; i < fs.length; i++) if (fs[i].key === key) return fs[i];
      return null;
    }
    var th = {
      color: MUT,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '.03em',
      padding: '5px 9px',
      textAlign: 'left',
      borderBottom: '1px solid ' + LINE,
      whiteSpace: 'nowrap',
    };
    var groupTh = {
      padding: '6px 9px 4px',
      borderBottom: '2px solid ' + LINE,
      borderLeft: '1px solid ' + LINE,
      textAlign: 'left',
      verticalAlign: 'bottom',
    };
    var cellBase = {
      padding: '6px 9px',
      fontSize: 12.5,
      fontFamily: mono,
      whiteSpace: 'nowrap',
    };
    // original row: no bottom border (groups the pair); backcheck row: solid
    function cell(extra, bottom) {
      return Object.assign(
        {},
        cellBase,
        { borderBottom: bottom ? '1px solid ' + LINE : 'none' },
        extra || {},
      );
    }
    function ncols(s) {
      return s.mode === 'distance' ? 1 : s.fields.length;
    }
    // identity / outcome value cell for one side of one household
    function vcell(row, key, which, first, bottom) {
      var f = fieldOf(row, key);
      var st = cell(first ? { borderLeft: '1px solid ' + LINE } : {}, bottom);
      if (!f)
        return (
          <td key={which + key} style={st}>
            —
          </td>
        );
      if (which === 'original')
        return (
          <td
            key={which + key}
            style={Object.assign({}, st, { color: SUBINK })}
          >
            {yn(f.original)}
          </td>
        );
      var ch = !f.match;
      // Discordance reads hard: a disagreeing re-survey value is bold rose on a
      // rose wash with an explicit "≠" so the diff is unmissable at row density.
      return (
        <td
          key={which + key}
          style={Object.assign({}, st, {
            color: ch ? ROSE : MUT,
            fontWeight: ch ? 800 : 400,
            background: ch ? '#ffe4e6' : 'transparent',
          })}
        >
          {ch ? '≠ ' : ''}
          {yn(f.backcheck)}
        </td>
      );
    }
    function infoBtn(s) {
      var on = bcInfo && bcInfo.key === s.key;
      return (
        <button
          data-bcinfo={s.key}
          onClick={function (e) {
            e.stopPropagation();
            // Inline disclosure: toggle the explanation block under the section
            // header (pushes content down) rather than floating over the table.
            setBcInfo(
              on
                ? null
                : {
                    key: s.key,
                    label: s.label,
                    info: s.info,
                  },
            );
          }}
          title="What this checks + where it comes from"
          style={{
            cursor: 'pointer',
            marginLeft: 4,
            border: '1px solid ' + (on ? INDIGO : LINE),
            background: on ? INDIGO : '#fff',
            color: on ? '#fff' : MUT,
            borderRadius: 999,
            width: 16,
            height: 16,
            fontSize: 10,
            fontWeight: 800,
            lineHeight: '14px',
            padding: 0,
            fontFamily: sans,
          }}
        >
          i
        </button>
      );
    }
    return (
      <div>
        <div
          style={{
            color: SUBINK,
            fontWeight: 700,
            fontSize: 13,
            marginBottom: 2,
          }}
        >
          Surveyor {sid} {'·'} {sb.n} households independently re-surveyed in{' '}
          {tWard} this round
        </div>
        <div style={{ color: MUT, fontSize: 11.5, marginBottom: 8 }}>
          Two rows per household — what the surveyor recorded vs the independent
          re-survey. Back-checks are a stratified sample of this surveyor's
          surveys in {tWard} this round (n={sb.n}); showing{' '}
          {Math.min(rows.length, sb.n)}, mismatches first. Each section header
          shows the share that agreed {'·'} tap{' '}
          <b style={{ fontFamily: mono }}>i</b> for what it means.
        </div>
        {/* Colour-convention legend — at the TOP so it's read before the rows. */}
        <div
          style={{
            fontSize: 11,
            color: MUT,
            fontFamily: mono,
            marginBottom: 8,
          }}
        >
          <span style={{ color: ROSE, fontWeight: 700 }}>rose</span> = the
          re-survey disagreed with what {sid} recorded
        </div>
        {/* Inline method disclosure — toggled by the 'i' on a section header.
            Renders here (pushing the table down) instead of overlaying it. */}
        {bcInfo
          ? (function () {
              var s = null;
              for (var i = 0; i < sections.length; i++)
                if (sections[i].key === bcInfo.key) s = sections[i];
              if (!s) return null;
              return (
                <div
                  style={{
                    border: '1px solid ' + LINE,
                    borderLeft: '3px solid ' + INDIGO,
                    background: '#f8fafc',
                    borderRadius: 8,
                    padding: '10px 12px',
                    marginBottom: 10,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'baseline',
                      marginBottom: 4,
                    }}
                  >
                    <b style={{ color: SUBINK, fontSize: 12.5 }}>{s.label}</b>
                    <button
                      onClick={function () {
                        setBcInfo(null);
                      }}
                      style={{
                        cursor: 'pointer',
                        border: 'none',
                        background: 'transparent',
                        color: MUT,
                        fontSize: 15,
                        lineHeight: 1,
                        padding: 0,
                      }}
                    >
                      ×
                    </button>
                  </div>
                  <div style={{ color: SUBINK, fontSize: 12, lineHeight: 1.5 }}>
                    {s.info}
                  </div>
                  <div
                    style={{
                      color: MUT,
                      fontSize: 11,
                      lineHeight: 1.5,
                      marginTop: 6,
                    }}
                  >
                    An independent surveyor re-visits a sample of households and
                    re-records the same facts. Standard back-check method
                    (J-PAL/IPA; World Bank DIME {'—'}{' '}
                    <a
                      href="https://dimewiki.worldbank.org/Back_Checks"
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: INDIGO }}
                    >
                      method reference
                    </a>
                    ).
                  </div>
                </div>
              );
            })()
          : null}
        <div style={{ overflow: 'auto', maxHeight: 460 }}>
          <table
            style={{
              borderCollapse: 'collapse',
              width: '100%',
              minWidth: 680,
            }}
          >
            <thead>
              <tr>
                <th
                  style={Object.assign({}, groupTh, { borderLeft: 'none' })}
                  colSpan={2}
                />
                {sections.map(function (s) {
                  var ok = s.pct == null || s.pct >= s.thr;
                  return (
                    <th key={s.key} style={groupTh} colSpan={ncols(s)}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 5,
                          flexWrap: 'wrap',
                        }}
                      >
                        <span
                          style={{
                            color: SUBINK,
                            fontWeight: 700,
                            fontSize: 12.5,
                          }}
                        >
                          {s.label}
                        </span>
                        {/* The agreement % is the headline of each section — render
                            it LARGE so the three section shares carry real visual
                            rank, not equal-weight fine print. Outcome (the key
                            result the survey exists to measure) gets the largest. */}
                        <span
                          style={{
                            color: s.pct == null ? MUT : ok ? GREEN : ROSE,
                            fontFamily: mono,
                            fontWeight: 800,
                            fontSize: s.key === 'outcome' ? 22 : 18,
                            lineHeight: 1.1,
                            flexBasis: '100%',
                          }}
                        >
                          {s.pct == null ? '—' : s.pct.toFixed(0) + '%'}
                          <span
                            style={{
                              fontSize: 10,
                              fontWeight: 600,
                              color: MUT,
                              marginLeft: 5,
                            }}
                          >
                            agree
                          </span>
                        </span>
                        {sb.n != null ? (
                          <span
                            style={{
                              color: MUT,
                              fontFamily: mono,
                              fontWeight: 600,
                              fontSize: 10.5,
                            }}
                          >
                            n={sb.n}
                          </span>
                        ) : null}
                        {/* For the location section the % is "share within
                            threshold" and the cells below are raw distances, so
                            spell that out — otherwise "0%" sitting over "40 m"
                            cells reads as a binding bug. */}
                        {s.mode === 'distance' ? (
                          <span
                            style={{
                              color: MUT,
                              fontWeight: 600,
                              fontSize: 10,
                            }}
                          >
                            within {thr} m · cells = distance
                          </span>
                        ) : null}
                        {infoBtn(s)}
                        {/* Plain-language gloss so a 0% GPS-match share reads as a
                            location-fraud signal, not an instrument failure. */}
                        {s.mode === 'distance' &&
                        s.pct != null &&
                        s.pct === 0 ? (
                          <span
                            style={{
                              flexBasis: '100%',
                              color: ROSE,
                              fontWeight: 600,
                              fontSize: 10,
                              fontFamily: sans,
                              lineHeight: 1.4,
                              marginTop: 2,
                            }}
                          >
                            none of the {sb.n} re-visits landed within {thr} m
                            of where the surveyor logged the household — a
                            location-fraud signal
                          </span>
                        ) : null}
                      </div>
                    </th>
                  );
                })}
              </tr>
              <tr>
                <th style={th}>Household</th>
                <th style={th}>Record</th>
                {sections.map(function (s) {
                  if (s.mode === 'distance')
                    return (
                      <th
                        key={s.key}
                        style={Object.assign({}, th, {
                          borderLeft: '1px solid ' + LINE,
                        })}
                      >
                        Distance
                      </th>
                    );
                  return s.fields.map(function (c, ci) {
                    return (
                      <th
                        key={s.key + c[0]}
                        style={Object.assign(
                          {},
                          th,
                          ci === 0 ? { borderLeft: '1px solid ' + LINE } : {},
                        )}
                      >
                        {c[1]}
                      </th>
                    );
                  });
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map(function (row, ri) {
                var dm = row.gps_delta_m;
                var distBad = dm != null && dm > thr;
                // Count how many checks differ for THIS household (mismatched
                // identity/outcome fields + a GPS gap beyond threshold), so each
                // household pair carries a "N differ" badge and a left accent
                // that gets heavier the more it diverged — discordance salience.
                var nDiff = (row.fields || []).filter(function (f) {
                  return !f.match;
                }).length;
                if (distBad) nDiff++;
                var hasDiff = nDiff > 0;
                return [
                  <tr key={ri + 'o'}>
                    <td
                      rowSpan={2}
                      style={cell(
                        {
                          fontFamily: 'inherit',
                          fontWeight: 600,
                          color: SUBINK,
                          // left accent brackets the household's two rows; rose +
                          // thicker when the pair disagreed, faint grey when clean.
                          borderLeft: hasDiff
                            ? '4px solid ' + ROSE
                            : '4px solid ' + LINE,
                          background: hasDiff ? '#fff5f6' : 'transparent',
                          verticalAlign: 'middle',
                        },
                        true,
                      )}
                    >
                      <div>{row.household_id}</div>
                      <div
                        style={{
                          marginTop: 4,
                          display: 'inline-block',
                          fontSize: 9.5,
                          fontFamily: mono,
                          fontWeight: 700,
                          letterSpacing: '.02em',
                          padding: '2px 6px',
                          borderRadius: 5,
                          color: hasDiff ? '#fff' : GREEN,
                          background: hasDiff ? ROSE : '#ecfdf5',
                          border: hasDiff ? 'none' : '1px solid #a7f3d0',
                        }}
                      >
                        {hasDiff
                          ? nDiff +
                            (nDiff === 1 ? ' field differs' : ' fields differ')
                          : 'all agree'}
                      </div>
                    </td>
                    <td style={cell({ color: MUT }, false)}>
                      Original ({row.enumerator})
                    </td>
                    {sections.map(function (s) {
                      if (s.mode === 'distance')
                        return (
                          <td
                            key="loc"
                            rowSpan={2}
                            style={cell(
                              {
                                borderLeft: '1px solid ' + LINE,
                                color: distBad ? ROSE : SUBINK,
                                fontWeight: distBad ? 700 : 400,
                                verticalAlign: 'middle',
                              },
                              true,
                            )}
                          >
                            {dm == null ? '—' : dm.toFixed(0) + ' m'}
                          </td>
                        );
                      return s.fields.map(function (c, ci) {
                        return vcell(row, c[0], 'original', ci === 0, false);
                      });
                    })}
                  </tr>,
                  <tr key={ri + 'b'}>
                    <td style={cell({ color: MUT }, true)}>
                      Backcheck ({row.backcheck_enumerator})
                    </td>
                    {sections.map(function (s) {
                      if (s.mode === 'distance') return null;
                      return s.fields.map(function (c, ci) {
                        return vcell(row, c[0], 'backcheck', ci === 0, true);
                      });
                    })}
                  </tr>,
                ];
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  function backcheckSection() {
    // a clicked quality cell takes over the widget with a metric drill-through
    if (qSel && QMETA[qSel.key]) return qmetricDrill(qSel.surveyor, qSel.key);
    if (!effSurv || !sbMap[effSurv]) return null;
    return surveyorBackcheck(effSurv, sbMap[effSurv]);
  }

  function roundTabs() {
    return (
      <div
        style={{
          display: 'flex',
          gap: 6,
          flexWrap: 'wrap',
          marginTop: 12,
          alignItems: 'center',
        }}
      >
        <span
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginRight: 4,
          }}
        >
          Cycle
        </span>
        {rounds.map(function (r, i) {
          var on = i === sel;
          return (
            <button
              key={i}
              onClick={function () {
                setSel(i);
              }}
              title={r.treatment_ward + ' vs ' + r.comparison_ward}
              style={{
                cursor: 'pointer',
                fontFamily: mono,
                fontSize: 13,
                padding: '5px 12px',
                borderRadius: 8,
                border: '1px solid ' + (on ? INDIGO : LINE),
                background: on ? INDIGO : '#fff',
                color: on ? '#fff' : SUBINK,
                fontWeight: on ? 700 : 600,
              }}
            >
              {'R' + r.round}
            </button>
          );
        })}
        <span style={{ color: MUT, fontSize: 13, marginLeft: 6 }}>
          {rd.label} · <b style={{ color: INDIGO }}>{tWard}</b> vs{' '}
          <b style={{ color: COMP }}>{cWard}</b>
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        background: 'transparent',
        color: INK,
        fontFamily: sans,
        paddingBottom: 8,
      }}
    >
      <div style={{ fontSize: 18, fontWeight: 700 }}>
        {prog.name || 'Verified Monitoring'}
      </div>
      <div style={{ color: MUT, fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
        Independent rooftop survey (an independent team re-measures the same
        households) · {prog.cadence || 'bi-monthly'} · the program rotates wards
        each cycle, each intervention ward verified against an adjacent control
        ward
      </div>

      {/* PAGE HERO — the six-cycle trend, edge-to-edge */}
      <div
        style={Object.assign(
          { marginTop: 12, padding: '14px 16px' },
          cardStyle,
        )}
      >
        <div
          style={{
            color: SUBINK,
            fontSize: 13,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
          }}
        >
          Service-delivery data vs independent survey —{' '}
          {(trend.rounds || []).length} bi-monthly rounds over time
        </div>
        <div style={{ marginTop: 8 }}>{trendChart()}</div>
        {/* Compact legend: the three lines' names match the chart's DIRECT
            end-labels exactly (one vocabulary), each line also direct-labelled at
            its end. The amber-gap-band definition and the y-axis definition are
            promoted out of fine-print into a single 'i' bubble (hover) so the
            chart frame stays clean. */}
        <div
          style={{
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
            alignItems: 'center',
            fontSize: 12.5,
            color: SUBINK,
            marginTop: 8,
          }}
        >
          <span>{sw(AMBER, true)}Service delivery</span>
          <span>{sw(INDIGO)}Intervention survey</span>
          <span>{sw(COMP)}Control survey</span>
          <span
            title="Y-axis: % of households where vitamin-A delivery was confirmed (survey) or reported (service-delivery data), at each round. Amber band: the gap between the program's service-delivery data and the independent survey. Highlighted column: the selected cycle — click a cycle to open it."
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              color: MUT,
              cursor: 'help',
            }}
          >
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 16,
                height: 16,
                borderRadius: 999,
                border: '1px solid ' + LINE,
                color: INDIGO,
                fontSize: 10.5,
                fontWeight: 800,
                fontFamily: sans,
              }}
            >
              i
            </span>
            <span
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              <span
                style={{
                  display: 'inline-block',
                  width: 16,
                  height: 9,
                  background: AMBER,
                  opacity: 0.45,
                  borderRadius: 2,
                }}
              />
              gap band · axis definitions
            </span>
          </span>
        </div>
      </div>

      {roundTabs()}

      {/* per-cycle: the moving map (full width) */}
      <div style={{ marginTop: 14 }}>
        <div style={Object.assign({ padding: 12 }, cardStyle)}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 6,
              flexWrap: 'wrap',
              gap: 6,
            }}
          >
            <div
              style={{
                color: MUT,
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '.05em',
              }}
            >
              Map · {tWard} (intervention) vs {cWard} (control)
            </div>
            {/* Layer toggles live in the docked Layers panel (top-right of the
                map) — the SAME MicroplansMapPanel the plan editor uses. */}
          </div>
          {/* Per-ward confirmed-rate readout — a neutral two-row table (ward ·
              arm · % confirmed), NOT a verdict banner over the evidence map. The
              map below is the evidence; this just tabulates the rate per ward so
              the viewer draws the conclusion. */}
          <table
            style={{
              borderCollapse: 'collapse',
              marginBottom: 8,
              fontSize: 12,
              fontFamily: mono,
              color: SUBINK,
            }}
          >
            <tbody>
              <tr>
                <td style={{ padding: '2px 12px 2px 0' }}>
                  <span style={{ color: INDIGO }}>▰</span> {tWard}
                </td>
                <td style={{ padding: '2px 12px 2px 0', color: MUT }}>
                  intervention
                </td>
                <td style={{ padding: '2px 0', color: SUBINK }}>
                  {pct(ver)} confirmed
                </td>
              </tr>
              <tr>
                <td style={{ padding: '2px 12px 2px 0' }}>
                  <span style={{ color: AMBER }}>▰</span> {cWard}
                </td>
                <td style={{ padding: '2px 12px 2px 0', color: MUT }}>
                  control
                </td>
                <td style={{ padding: '2px 0', color: SUBINK }}>
                  {pct((trend.comparison || [])[sel])} confirmed
                </td>
              </tr>
            </tbody>
          </table>
          <div ref={panelMountRef} style={{ position: 'relative' }}>
            <div
              ref={mapDivRef}
              style={{
                // Tall focused map for the delivery-vs-survey beat: when this is
                // the narrated subject it must read at a normal viewport, so the
                // solid green delivery dot vs the hollow indigo/rose survey ring
                // separate clearly. Was a ~250px thumbnail competing with the
                // scorecard; now it gets the room the encoding needs.
                height: 520,
                borderRadius: 8,
                overflow: 'hidden',
                background: '#eef2f7',
                border: '1px solid ' + LINE,
              }}
            />
            {/* When a scorecard row is selected, the map shows only that surveyor's
                surveys + work areas — this chip says so and clears the filter. */}
            {selSurv ? (
              <div
                style={{
                  position: 'absolute',
                  top: 8,
                  left: '50%',
                  transform: 'translateX(-50%)',
                  zIndex: 7,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  background: 'rgba(255,255,255,0.97)',
                  border: '1px solid ' + INDIGO,
                  borderRadius: 999,
                  padding: '4px 6px 4px 12px',
                  fontSize: 11.5,
                  fontFamily: mono,
                  color: SUBINK,
                  boxShadow: '0 2px 8px rgba(16,24,40,0.14)',
                }}
              >
                <span>
                  Surveyor <b style={{ color: INDIGO }}>{selSurv}</b> only
                </span>
                <button
                  onClick={function () {
                    setSelSurv(null);
                  }}
                  title="Show all surveyors"
                  style={{
                    cursor: 'pointer',
                    border: '1px solid ' + LINE,
                    background: '#fff',
                    color: MUT,
                    borderRadius: 999,
                    fontSize: 11,
                    lineHeight: 1,
                    padding: '3px 8px',
                    fontFamily: sans,
                  }}
                >
                  show all ✕
                </button>
              </div>
            ) : null}
            {/* Dot key — docked BOTTOM-LEFT over empty basemap with a semi-opaque
                panel, so it never covers the intervention ward's dots (which sit
                top-left). Per-ward confirmed rates moved to the caption above the
                map. */}
            <div
              style={{
                position: 'absolute',
                bottom: 14,
                left: 10,
                background: 'rgba(255,255,255,0.94)',
                border: '1px solid ' + LINE,
                borderRadius: 9,
                padding: '10px 13px',
                fontSize: 12.5,
                fontFamily: mono,
                color: SUBINK,
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                lineHeight: 1.5,
                boxShadow: '0 2px 10px rgba(16,24,40,0.16)',
                pointerEvents: 'none',
                backdropFilter: 'blur(1px)',
              }}
            >
              <span style={{ color: SUBINK, fontWeight: 700, fontSize: 12.5 }}>
                Map key
              </span>
              {/* program delivery = larger SOLID dot — swatch sized to match the
                  enlarged map marks so the legend reads like the map. */}
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 14,
                    height: 14,
                    borderRadius: '50%',
                    background: '#15803d',
                    border: '1.5px solid #fff',
                    boxShadow: '0 0 0 1px #15803d',
                    flex: '0 0 auto',
                  }}
                />
                service delivery (program){_ct(mmc.delivery)}
              </span>
              {/* survey = smaller HOLLOW ring */}
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 13,
                    height: 13,
                    borderRadius: '50%',
                    border: '2.5px solid ' + INDIGO,
                    background: 'transparent',
                    flex: '0 0 auto',
                  }}
                />
                survey confirmed{_ct(mmc.confirmed)}
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 13,
                    height: 13,
                    borderRadius: '50%',
                    border: '2.5px solid ' + ROSE,
                    background: 'transparent',
                    flex: '0 0 auto',
                  }}
                />
                surveyed · not reached{_ct(mmc.notReached)}
              </span>
              <span style={{ color: MUT, fontSize: 11 }}>
                larger ring = primary unit · smaller = alternate (substituted)
              </span>
            </div>
          </div>
          {!mapLibReady ? (
            <div style={{ color: MUT, fontSize: 12, padding: 6 }}>
              loading map…
            </div>
          ) : null}
        </div>
      </div>

      {/* QUALITY — scorecard + its back-check / quality-detail panel, one card */}
      <div style={Object.assign({ marginTop: 16, padding: 14 }, cardStyle)}>
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 10,
            // scroll target: land the whole scorecard clear of the host sticky
            // nav so all surveyor rows (the peers that make T6 read as an
            // outlier) frame together, not sheared at the top edge.
            scrollMarginTop: 96,
          }}
        >
          Quality · {tWard} · R{rd.round} — survey-quality scorecard, one row
          per surveyor
        </div>
        <div
          style={{
            display: 'flex',
            gap: 16,
            marginTop: 0,
            marginBottom: 10,
            fontSize: 11,
            color: MUT,
            fontFamily: mono,
            flexWrap: 'wrap',
          }}
        >
          <span>
            <span style={{ color: GREEN, fontWeight: 700 }}>green</span> =
            within threshold
          </span>
          <span>
            <span style={{ color: ROSE, fontWeight: 700 }}>rose</span> = below
            threshold
          </span>
          <span>quality columns + back-check = this round</span>
          <span style={{ color: INDIGO }}>
            click a quality cell → detail · click a surveyor → back-check below
          </span>
        </div>
        {scorecardTable()}
        {/* in-card detail: this surveyor's back-check (or a clicked quality metric) */}
        <div
          style={{
            marginTop: 16,
            paddingTop: 14,
            borderTop: '1px solid ' + LINE,
            // scroll target: land the back-check sub-section header clear of the
            // host sticky nav so the header + the three agreement shares + the
            // first Original/Backcheck rows frame at the top of the viewport,
            // exactly like the Distributions card below.
            scrollMarginTop: 84,
          }}
        >
          <div
            style={{
              color: MUT,
              fontSize: 11,
              textTransform: 'uppercase',
              letterSpacing: '.05em',
              marginBottom: 10,
              scrollMarginTop: 84,
            }}
          >
            {qSel && QMETA[qSel.key]
              ? 'Survey-quality detail'
              : 'Independent back-check' +
                (effSurv ? ' · Surveyor ' + effSurv : '')}
          </div>
          {backcheckSection()}
        </div>
      </div>

      {/* DISTRIBUTIONS — Layer-3 statistical fabrication screen, one card, no drill */}
      <div
        style={Object.assign(
          { marginTop: 16, padding: 14, scrollMarginTop: 84 },
          cardStyle,
        )}
      >
        <div
          style={{
            color: MUT,
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '.05em',
            marginBottom: 10,
            // scroll target: land this card clear of the host sticky nav so the
            // previous (back-check) table doesn't bleed into the top of frame.
            scrollMarginTop: 84,
          }}
        >
          Distributions · {tWard} · R{rd.round} — per-surveyor distribution
          checks, one row per surveyor
        </div>
        <div
          style={{
            fontSize: 11,
            color: MUT,
            marginBottom: 10,
            lineHeight: 1.5,
            maxWidth: 760,
          }}
        >
          Flags a surveyor whose numbers don't behave like their peers' —
          interviews too short to be real, or answers too uniform to occur
          naturally. Each lens places this surveyor at their real value on its
          own scale (the notch; the line is the team-typical), and the coloured
          bar's width is |z| — how many standard deviations from the
          team-typical (using a robust median-based spread that one outlier
          can't distort). No second field visit required.
        </div>
        <div
          style={{
            display: 'flex',
            gap: 16,
            marginBottom: 10,
            fontSize: 11,
            color: MUT,
            fontFamily: mono,
            flexWrap: 'wrap',
          }}
        >
          <span>
            <span style={{ color: GREEN, fontWeight: 700 }}>●</span> within peer
            range
          </span>
          <span>
            <span style={{ color: AMBER, fontWeight: 700 }}>●</span> elevated —
            corroborating
          </span>
          <span>
            <span style={{ color: ROSE, fontWeight: 700 }}>●</span> flagged
            (|z|&nbsp;&gt;&nbsp;3.5)
          </span>
          <span style={{ color: INDIGO }}>
            A = compare a number · B = compare a distribution
          </span>
          <span style={{ flexBasis: '100%', color: SUBINK }}>
            Composite band = the strongest single signal: any signal past |z|
            &gt; 3.5 ⇒ <b style={{ color: ROSE }}>red</b>; any past |z| &ge; 2 ⇒{' '}
            <b style={{ color: '#b45309' }}>amber</b>; otherwise{' '}
            <b style={{ color: GREEN }}>green</b>. So the pill always agrees
            with the per-signal thresholds above.
          </span>
        </div>
        {/* How to read the lens — a labeled mini-axis so the bar/notch/band
            encoding is legible without hovering each cell. */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flexWrap: 'wrap',
            marginBottom: 12,
            fontSize: 10.5,
            color: MUT,
            fontFamily: mono,
          }}
        >
          <span
            style={{
              position: 'relative',
              display: 'inline-block',
              width: 88,
              height: 12,
              flex: '0 0 auto',
            }}
          >
            <span
              style={{
                position: 'absolute',
                left: 20,
                width: 48,
                top: 2,
                height: 8,
                background: '#e2e8f0',
                borderRadius: 3,
              }}
            />
            <span
              style={{
                position: 'absolute',
                left: 44,
                top: 0,
                height: 12,
                width: 1.5,
                background: '#94a3b8',
              }}
            />
            <span
              style={{
                position: 'absolute',
                left: 30,
                width: 34,
                top: 3,
                height: 6,
                borderRadius: 3,
                background: '#64748b',
                opacity: 0.95,
              }}
            />
            <span
              style={{
                position: 'absolute',
                left: 53,
                top: -1,
                height: 14,
                width: 2,
                background: '#0f172a',
              }}
            />
          </span>
          {/* per-encoding chips — each channel of the lens broken out so the
              four encodings read one at a time instead of as one packed line. */}
          <span
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <span
              style={{
                display: 'inline-block',
                width: 18,
                height: 8,
                background: '#e2e8f0',
                borderRadius: 3,
              }}
            />
            shaded = peer range
          </span>
          <span
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <span
              style={{
                display: 'inline-block',
                width: 2,
                height: 12,
                background: '#94a3b8',
              }}
            />
            grey line = team-typical
          </span>
          <span
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <span
              style={{
                display: 'inline-block',
                width: 2,
                height: 14,
                background: '#0f172a',
              }}
            />
            <b style={{ color: SUBINK }}>dark notch</b> = this surveyor
          </span>
          <span
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <span
              style={{
                display: 'inline-block',
                width: 18,
                height: 6,
                background: '#64748b',
                borderRadius: 3,
              }}
            />
            bar width = how big an outlier (|z|)
          </span>
        </div>
        {distributionsTable()}
      </div>
    </div>
  );
}
