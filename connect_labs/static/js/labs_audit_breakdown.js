// Shared runner UI primitive: the "Audit results by field worker" breakdown.
//
// One canonical renderer for the per-FLW, dual-track (MUAC / Other) audit
// breakdown — the panel you see on a Weekly Dual-Track Audit run. It is
// consumed identically by:
//   1. the weekly_dual_track_audit workflow template (the opp-level run page),
//   2. the program_audit_creator template (each per-opp row expands to it),
//   3. the pages "flw_audit_breakdown" card.
//
// WHY THIS IS A STATIC FILE (and how to change it):
//   Workflow render code is dynamic (stored on the definition, Babel-transpiled
//   client-side, editable live via the connect_labs MCP). This module is the
//   opposite: it is a STATIC, versioned, unit-tested primitive that render code
//   *calls* rather than re-inlines, so all three surfaces stay byte-identical.
//   It IS editable — you edit THIS file in the repo (in Claude Code, like
//   everything else) and it ships on the next deploy. Do not copy its markup
//   back into a template's render_code; extend it here so every consumer moves
//   together. See connect_labs/workflow/WORKFLOW_REFERENCE.md → "Shared runner
//   UI primitives".
//
// Contract: React is passed IN (never imported) so this works whether React
// comes from the workflow-runner bundle or the pages-cards bundle. The renderer
// returns a React element that self-manages its own collapse state.

(function () {
  'use strict';

  var LabsAudit = {};

  // ── Pure helpers (unit-tested; no React, no DOM) ──────────────────────────
  function statsOf(s) {
    return (s && s.assessment_stats) || {};
  }
  function imagesOf(s) {
    return (s && s.image_count) || 0;
  }
  function aiReviewedOf(s) {
    var a = statsOf(s);
    return (a.ai_match || 0) + (a.ai_no_match || 0);
  }
  function humanReviewedOf(s) {
    var a = statsOf(s);
    return (a.pass || 0) + (a.fail || 0) + (a.duplicate_fake || 0);
  }
  function duplicateFakeOf(s) {
    return statsOf(s).duplicate_fake || 0;
  }
  function clusterCountOf(s) {
    return (s && s.visit_clusters && s.visit_clusters.length) || 0;
  }

  // Group sessions by opportunity → field worker → { muac, rest }.
  function groupByOppFlw(sessions) {
    var byOpp = {};
    (sessions || []).forEach(function (s) {
      var oid = s.opportunity_id != null ? String(s.opportunity_id) : 'unknown';
      var flw = s.flw_username || 'unknown';
      if (!byOpp[oid]) byOpp[oid] = { flws: {}, order: [] };
      if (!byOpp[oid].flws[flw]) {
        byOpp[oid].flws[flw] = {
          name: s.flw_display_name || s.flw_username || flw,
          muac: null,
          rest: null,
        };
        byOpp[oid].order.push(flw);
      }
      if (s.tag === 'muac') byOpp[oid].flws[flw].muac = s;
      else if (s.tag === 'rest') byOpp[oid].flws[flw].rest = s;
    });
    return byOpp;
  }

  // Per-opp rollup for the group header line.
  function oppSummary(oppData) {
    var out = {
      flws: 0,
      muacImages: 0,
      muacAiReviewed: 0,
      muacFlagged: 0,
      restImages: 0,
      restReviewed: 0,
    };
    oppData.order.forEach(function (flw) {
      var r = oppData.flws[flw];
      out.flws++;
      if (r.muac) {
        out.muacImages += imagesOf(r.muac);
        out.muacAiReviewed += aiReviewedOf(r.muac);
        out.muacFlagged += statsOf(r.muac).ai_no_match || 0;
      }
      if (r.rest) {
        out.restImages += imagesOf(r.rest);
        out.restReviewed += humanReviewedOf(r.rest);
      }
    });
    return out;
  }

  // Deep-link to a single audit session's bulk review screen.
  function bulkUrl(s, workflowRunId) {
    var params = new URLSearchParams();
    if (s.opportunity_id != null)
      params.set('opportunity_id', s.opportunity_id);
    if (workflowRunId) params.set('workflow_run_id', workflowRunId);
    return '/audit/' + s.id + '/bulk/?' + params.toString();
  }

  // Merge-fetch the sessions for a workflow run across one or more opps. The
  // sessions endpoint is opp-scoped (labs enforces opp scope per request), so we
  // fetch each opp and de-dup by session id — the exact loop the opp-level run
  // page has always used.
  function fetchSessions(workflowRunId, oppIds) {
    if (!workflowRunId || !oppIds || !oppIds.length) return Promise.resolve([]);
    return Promise.all(
      oppIds.map(function (opp) {
        return fetch(
          '/audit/api/workflow/' +
            workflowRunId +
            '/sessions/?opportunity_id=' +
            opp,
        )
          .then(function (res) {
            return res.json();
          })
          .then(function (data) {
            return data && data.success && data.sessions ? data.sessions : [];
          })
          .catch(function () {
            return [];
          });
      }),
    ).then(function (arrs) {
      var seen = {};
      var all = [];
      arrs.forEach(function (list) {
        list.forEach(function (s) {
          if (!seen[s.id]) {
            seen[s.id] = true;
            all.push(s);
          }
        });
      });
      return all;
    });
  }

  // ── One compact audit line: status + images + pass/fail/pending + AI ──────
  // Self-manages its own "expanded" state for the Duplicate Groupings panel via
  // a tiny wrapper component (needs React.useState, unlike the rest of this file's
  // stateless helpers).
  function makeAuditLine(React) {
    var h = React.createElement;
    return function AuditLine(props) {
      var label = props.label;
      var s = props.session;
      var workflowRunId = props.workflowRunId;

      var st = React.useState(false);
      var expanded = st[0];
      var setExpanded = st[1];

      if (!s)
        return h(
          'div',
          { className: 'text-xs text-gray-400 pl-2' },
          label + ': not created',
        );

      var a = statsOf(s);
      var images = imagesOf(s);
      var reviewed = humanReviewedOf(s);
      var pending = Math.max(0, images - reviewed);
      var duplicates = duplicateFakeOf(s);
      var clusters = (s && s.visit_clusters) || [];
      var done = s.status === 'completed';
      var failed = done && s.overall_result === 'fail';
      var statusText = failed ? 'Fail' : done ? 'Completed' : 'In progress';
      var statusClass = failed
        ? 'bg-red-100 text-red-700'
        : done
        ? 'bg-green-100 text-green-700'
        : 'bg-yellow-100 text-yellow-700';

      return h(
        'div',
        {
          className:
            'flex items-center gap-3 px-3 py-1.5 rounded bg-gray-50 border border-gray-200 text-xs',
        },
        h('span', { className: 'font-semibold text-gray-700 w-12' }, label),
        h(
          'span',
          { className: 'px-1.5 py-0.5 rounded ' + statusClass },
          statusText,
        ),
        h('span', { className: 'text-gray-500 w-16' }, images + ' images'),
        h(
          'span',
          { className: 'flex-1' },
          h(
            'span',
            { className: 'text-green-600 font-medium' },
            (a.pass || 0) + ' pass',
          ),
          ' · ',
          h(
            'span',
            { className: 'text-red-600 font-medium' },
            (a.fail || 0) + ' fail',
          ),
          ' · ',
          h(
            'span',
            { className: 'text-orange-600 font-medium' },
            duplicates + ' duplicates',
          ),
          ' · ',
          h('span', { className: 'text-gray-500' }, pending + ' pending'),
        ),
        label === 'MUAC'
          ? h(
              'span',
              {
                className:
                  (a.ai_no_match || 0) > 0
                    ? 'text-amber-600 font-medium'
                    : 'text-gray-500',
              },
              'AI: ' +
                (a.ai_no_match || 0) +
                ' flagged / ' +
                aiReviewedOf(s) +
                ' reviewed',
            )
          : h('span', { className: 'text-gray-300' }, 'no AI'),
        clusters.length > 0
          ? h(
              'button',
              {
                onClick: function () {
                  setExpanded(!expanded);
                },
                className:
                  'px-2 py-0.5 rounded bg-purple-50 text-purple-700 hover:bg-purple-100 font-medium whitespace-nowrap',
              },
              clusters.length +
                ' Duplicate Grouping' +
                (clusters.length === 1 ? '' : 's'),
            )
          : null,
        h(
          'a',
          {
            href: bulkUrl(s, workflowRunId),
            title: 'Open in Connect',
            className: 'text-blue-500 hover:text-blue-700',
          },
          h('i', {
            className: 'fa-solid fa-arrow-up-right-from-square',
          }),
        ),
        expanded
          ? h(
              'div',
              {
                className:
                  'basis-full mt-1.5 pl-16 space-y-1 text-xs text-gray-700',
              },
              clusters.map(function (c, i) {
                return h(
                  'div',
                  { key: c.group_id, className: 'flex items-center gap-2' },
                  h(
                    'span',
                    null,
                    'Group ' + (i + 1) + ' — ' + c.image_count + ' images',
                  ),
                  h(
                    'a',
                    {
                      href:
                        '/audit/api/' +
                        s.id +
                        '/visit-clusters/' +
                        c.group_id +
                        '/export.csv',
                      className: 'text-blue-500 hover:underline',
                    },
                    h('i', { className: 'fa-solid fa-download mr-1' }),
                    'Download CSV',
                  ),
                );
              }),
            )
          : null,
      );
    };
  }

  // ── The self-managing breakdown component ─────────────────────────────────
  // Built once per React instance and cached, so parent re-renders reconcile it
  // as the SAME component type and its collapse state survives.
  function makeBreakdown(React) {
    var h = React.createElement;
    var AuditLine = makeAuditLine(React);
    return function FlwAuditBreakdown(props) {
      var sessions = props.sessions || [];
      var oppNames = props.oppNames || {};
      var workflowRunId = props.workflowRunId;
      var loading = !!props.loading;
      // title === null hides the header (e.g. when nested under an opp row).
      var title =
        props.title === undefined
          ? 'Audit results by field worker'
          : props.title;
      var startCollapsed = !!props.startCollapsed;
      var emptyText = props.emptyText || 'No sessions yet.';

      var st = React.useState({});
      var collapsedOpps = st[0];
      var setCollapsedOpps = st[1];

      var grouped = groupByOppFlw(sessions);

      var header = title
        ? h(
            'h3',
            { className: 'text-sm font-medium text-gray-700 mb-3' },
            h('i', { className: 'fa-solid fa-user-check mr-2 text-gray-400' }),
            title,
          )
        : null;

      var body;
      if (loading) {
        body = h(
          'div',
          { className: 'text-sm text-gray-500' },
          h('i', { className: 'fa-solid fa-spinner fa-spin mr-2' }),
          'Loading…',
        );
      } else if (!sessions.length) {
        body = h('div', { className: 'text-sm text-gray-500' }, emptyText);
      } else {
        body = h(
          'div',
          { className: 'space-y-4' },
          Object.keys(grouped).map(function (oid) {
            var oppData = grouped[oid];
            var sum = oppSummary(oppData);
            var collapsed =
              oid in collapsedOpps ? collapsedOpps[oid] : startCollapsed;
            return h(
              'div',
              {
                key: oid,
                className: 'border border-gray-200 rounded-lg overflow-hidden',
              },
              h(
                'div',
                {
                  className:
                    'bg-gray-50 px-4 py-3 border-b border-gray-200 cursor-pointer hover:bg-gray-100',
                  onClick: function () {
                    setCollapsedOpps(function (c) {
                      var n = Object.assign({}, c);
                      n[oid] = !collapsed;
                      return n;
                    });
                  },
                },
                h(
                  'div',
                  {
                    className:
                      'text-sm font-semibold text-gray-900 flex items-center',
                  },
                  h('i', {
                    className:
                      'fa-solid mr-2 text-gray-400 ' +
                      (collapsed ? 'fa-chevron-right' : 'fa-chevron-down'),
                  }),
                  oppNames[oid] || 'Opportunity ' + oid,
                  h(
                    'span',
                    { className: 'ml-2 text-xs text-gray-400 font-mono' },
                    '#' + oid,
                  ),
                ),
                h(
                  'div',
                  { className: 'text-xs text-gray-500 mt-1 pl-6' },
                  sum.flws + ' field worker' + (sum.flws === 1 ? '' : 's'),
                  ' · MUAC ' +
                    sum.muacImages +
                    ' imgs, ' +
                    sum.muacAiReviewed +
                    ' AI-reviewed, ',
                  h(
                    'span',
                    {
                      className:
                        sum.muacFlagged > 0 ? 'text-amber-600 font-medium' : '',
                    },
                    sum.muacFlagged + ' flagged',
                  ),
                  ' · Other ' +
                    sum.restImages +
                    ' imgs, ' +
                    sum.restReviewed +
                    ' human-reviewed',
                ),
              ),
              !collapsed
                ? h(
                    'div',
                    { className: 'divide-y divide-gray-100' },
                    oppData.order.map(function (flw) {
                      var r = oppData.flws[flw];
                      return h(
                        'div',
                        { key: flw, className: 'px-4 py-3' },
                        h(
                          'div',
                          {
                            className:
                              'text-sm font-medium text-gray-800 mb-1.5',
                          },
                          r.name,
                        ),
                        h(
                          'div',
                          { className: 'space-y-1' },
                          h(AuditLine, {
                            label: 'MUAC',
                            session: r.muac,
                            workflowRunId: workflowRunId,
                          }),
                          h(AuditLine, {
                            label: 'Other',
                            session: r.rest,
                            workflowRunId: workflowRunId,
                          }),
                        ),
                      );
                    }),
                  )
                : null,
            );
          }),
        );
      }

      return h('div', null, header, body);
    };
  }

  var _cachedReact = null;
  var _cachedComponent = null;

  // Public: render the breakdown. Pass the host's React and a props object:
  //   { sessions, oppNames?, workflowRunId?, loading?, title?, startCollapsed? }
  LabsAudit.renderFlwBreakdown = function (React, props) {
    if (_cachedReact !== React) {
      _cachedReact = React;
      _cachedComponent = makeBreakdown(React);
    }
    return React.createElement(_cachedComponent, props || {});
  };

  LabsAudit.groupByOppFlw = groupByOppFlw;
  LabsAudit.oppSummary = oppSummary;
  LabsAudit.bulkUrl = bulkUrl;
  LabsAudit.fetchSessions = fetchSessions;
  LabsAudit.humanReviewedOf = humanReviewedOf;
  LabsAudit.duplicateFakeOf = duplicateFakeOf;
  LabsAudit.clusterCountOf = clusterCountOf;

  if (typeof window !== 'undefined') window.LabsAudit = LabsAudit;
  if (typeof module !== 'undefined' && module.exports)
    module.exports = LabsAudit;
})();
