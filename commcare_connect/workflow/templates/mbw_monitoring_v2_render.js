function WorkflowUI({
  definition,
  instance,
  workers,
  pipelines,
  links,
  actions,
  onUpdateState,
}) {
  // =========================================================================
  // State key migration helpers
  // =========================================================================
  var savedWorkers =
    instance.state?.selected_workers || instance.state?.selected_flws || [];
  var savedResults =
    instance.state?.worker_results || instance.state?.flw_results || {};

  // =========================================================================
  // Step management: 'select' or 'dashboard'
  // =========================================================================
  var [step, setStep] = React.useState(
    savedWorkers.length > 0 ? 'dashboard' : 'select',
  );

  // =========================================================================
  // STEP 1: FLW Selection State
  // =========================================================================
  var [selectedFlws, setSelectedFlws] = React.useState({});
  var [flwHistory, setFlwHistory] = React.useState({});
  var [historyLoading, setHistoryLoading] = React.useState(false);
  var [title, setTitle] = React.useState('');
  var [tag, setTag] = React.useState('');
  var [gsAppId, setGsAppId] = React.useState(
    instance.state?.gs_app_id || '2ca67a89dd8a2209d75ed5599b45a5d1',
  );
  var [launching, setLaunching] = React.useState(false);
  var [selSearch, setSelSearch] = React.useState('');
  var [selSort, setSelSort] = React.useState({ col: 'name', dir: 'asc' });

  // =========================================================================
  // STEP 2: Dashboard State
  // =========================================================================
  var [dashData, setDashData] = React.useState(null);
  var [jobMessages, setJobMessages] = React.useState([]);
  var [jobError, setJobError] = React.useState(null);
  var [jobRunning, setJobRunning] = React.useState(false);
  var [analysisComplete, setAnalysisComplete] = React.useState(false);
  var [oauthStatus, setOauthStatus] = React.useState(null);
  var jobCleanupRef = React.useRef(null);
  var [activeTab, setActiveTab] = React.useState('overview');
  var [guideSection, setGuideSection] = React.useState({});
  var [overviewSearch, setOverviewSearch] = React.useState('');
  var [overviewSort, setOverviewSort] = React.useState({
    col: 'display_name',
    dir: 'asc',
  });
  var [gpsSort, setGpsSort] = React.useState({ col: 'username', dir: 'asc' });
  var [fuSort, setFuSort] = React.useState({ col: 'username', dir: 'asc' });
  var [expandedGps, setExpandedGps] = React.useState(null);
  var [gpsDetail, setGpsDetail] = React.useState(null);
  var [gpsDetailLoading, setGpsDetailLoading] = React.useState(false);
  var [gpsDetailSort, setGpsDetailSort] = React.useState({
    col: 'visit_date',
    dir: 'asc',
  });
  var [expandedFu, setExpandedFu] = React.useState(null);
  var [showCompleteModal, setShowCompleteModal] = React.useState(false);
  var [completeNotes, setCompleteNotes] = React.useState('');
  var [completing, setCompleting] = React.useState(false);
  var [workerResults, setWorkerResults] = React.useState(savedResults);
  var [savingResult, setSavingResult] = React.useState(null);

  // Additional state for filters, notes modal, toast
  var [filterFlws, setFilterFlws] = React.useState([]);
  var [filterMothers, setFilterMothers] = React.useState([]);
  var [showAllVisits, setShowAllVisits] = React.useState(false);
  var [showEligibleOnly, setShowEligibleOnly] = React.useState(true);
  var [showFlwNotesModal, setShowFlwNotesModal] = React.useState(false);
  var [flwNotesUsername, setFlwNotesUsername] = React.useState('');
  var [flwNotesText, setFlwNotesText] = React.useState('');
  var [flwNotesResult, setFlwNotesResult] = React.useState(null);
  var [toastMessage, setToastMessage] = React.useState('');
  var [filterStartDate, setFilterStartDate] = React.useState('');
  var [filterEndDate, setFilterEndDate] = React.useState('');
  var [appVersionOp, setAppVersionOp] = React.useState(
    instance.state?.app_version_op || 'gt',
  );
  var [appVersionVal, setAppVersionVal] = React.useState(
    instance.state?.app_version_val || '14',
  );
  var [appliedAppVersionOp, setAppliedAppVersionOp] = React.useState(
    instance.state?.app_version_op || 'gt',
  );
  var [appliedAppVersionVal, setAppliedAppVersionVal] = React.useState(
    instance.state?.app_version_val || '14',
  );
  var ALLOWED_STATUS_FILTERS = [
    'approved',
    'pending',
    'rejected',
    'over_limit',
  ];
  var _statusFilterKey = 'mbw_pending_filters:' + (instance.id || 'default');
  var _hydrateStatusFilter = function () {
    try {
      var raw = sessionStorage.getItem(_statusFilterKey);
      if (raw) {
        var parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          var filtered = parsed.filter(function (v) {
            return (
              typeof v === 'string' &&
              v &&
              ALLOWED_STATUS_FILTERS.indexOf(v) !== -1
            );
          });
          if (filtered.length > 0) return filtered;
        }
      }
    } catch (e) {}
    return null;
  };
  var _normalizeStatusFilter = function (val) {
    if (Array.isArray(val)) {
      var filtered = val.filter(function (v) {
        return (
          typeof v === 'string' && v && ALLOWED_STATUS_FILTERS.indexOf(v) !== -1
        );
      });
      return filtered.length > 0 ? filtered : null;
    }
    if (
      val != null &&
      typeof val === 'string' &&
      val &&
      ALLOWED_STATUS_FILTERS.indexOf(val) !== -1
    )
      return [val];
    return null;
  };
  var [statusFilter, setStatusFilter] = React.useState(function () {
    return (
      _hydrateStatusFilter() ||
      _normalizeStatusFilter(instance.state?.status_filter) || ['approved']
    );
  });
  var [appliedStatusFilter, setAppliedStatusFilter] = React.useState(
    function () {
      return (
        _hydrateStatusFilter() ||
        _normalizeStatusFilter(instance.state?.status_filter) || ['approved']
      );
    },
  );
  React.useEffect(function () {
    sessionStorage.removeItem(_statusFilterKey);
  }, []);
  var [hiddenCategories, setHiddenCategories] = React.useState({});

  // GPS Map state (per-FLW drill-down)
  var [leafletReady, setLeafletReady] = React.useState(false);
  var [showMapVisits, setShowMapVisits] = React.useState(true);
  var [showMapMothers, setShowMapMothers] = React.useState(true);
  var [selectedMother, setSelectedMother] = React.useState(null);
  var mapInstanceRef = React.useRef(null);
  var markersRef = React.useRef(null);

  // Aggregate GPS Map state (all FLWs)
  var [showAggregateMap, setShowAggregateMap] = React.useState(false);
  var aggregateMapRef = React.useRef(null);
  var aggregateMarkersRef = React.useRef(null);

  // OCS Task Modal state
  var [showOcsModal, setShowOcsModal] = React.useState(false);
  var [ocsModalFlw, setOcsModalFlw] = React.useState(null);
  var [ocsLoading, setOcsLoading] = React.useState(false);
  var [ocsBots, setOcsBots] = React.useState([]);
  var [selectedBot, setSelectedBot] = React.useState('');
  var [ocsPrompt, setOcsPrompt] = React.useState('');
  var [ocsCreating, setOcsCreating] = React.useState(false);
  var [ocsError, setOcsError] = React.useState('');
  var [createdTaskUsernames, setCreatedTaskUsernames] = React.useState([]);

  // Inline task expansion state
  var taskRequestIdRef = React.useRef(0);
  var [expandedTaskFlw, setExpandedTaskFlw] = React.useState(null);
  var [taskDetail, setTaskDetail] = React.useState(null);
  var [taskTranscript, setTaskTranscript] = React.useState(null);
  var [taskLoading, setTaskLoading] = React.useState(false);
  var [taskStatus, setTaskStatus] = React.useState('');
  var [taskOriginalStatus, setTaskOriginalStatus] = React.useState('');
  var [taskSaving, setTaskSaving] = React.useState(false);
  var [showCloseForm, setShowCloseForm] = React.useState(false);
  var [closeAction, setCloseAction] = React.useState('none');
  var [closeNote, setCloseNote] = React.useState('');
  var [monthlyViewPct, setMonthlyViewPct] = React.useState(false);
  var [monthlyCountMode, setMonthlyCountMode] = React.useState('ratio'); // 'ratio' | 'completed' | 'scheduled'

  // Column selector for Overview table
  var OVERVIEW_COLUMNS = [
    { id: 'flw_name', label: 'FLW Name', locked: true },
    { id: 'last_active', label: 'Last Active' },
    { id: 'mothers', label: '# Mothers' },
    { id: 'gs_score', label: 'GS Score' },
    { id: 'post_test', label: 'Post-Test' },
    { id: 'followup_rate', label: 'Follow-up Rate' },
    { id: 'eligible_5', label: 'Eligible 5+' },
    { id: 'ebf_pct', label: '% EBF' },
    { id: 'revisit_dist', label: 'Revisit Dist.' },
    { id: 'meter_visit', label: 'Meter/Visit' },
    { id: 'dist_ratio', label: 'Dist. Ratio' },
    { id: 'minute_visit', label: 'Minute/Visit' },
    { id: 'phone_dup', label: 'Phone Dup %' },
    { id: 'anc_pnc', label: 'ANC = PNC' },
    { id: 'parity', label: 'Parity' },
    { id: 'age', label: 'Age' },
    { id: 'age_reg', label: 'Age = Reg' },
    { id: 'actions', label: 'Actions', locked: true },
  ];
  var [visibleCols, setVisibleCols] = React.useState(
    OVERVIEW_COLUMNS.map(function (c) {
      return c.id;
    }),
  );
  var [showColPicker, setShowColPicker] = React.useState(false);
  var isColVisible = function (id) {
    return visibleCols.indexOf(id) >= 0;
  };
  var toggleCol = function (id) {
    setVisibleCols(function (prev) {
      return prev.indexOf(id) >= 0
        ? prev.filter(function (c) {
            return c !== id;
          })
        : prev.concat([id]);
    });
  };

  // Centralized color thresholds for Eligible 5+ / % Still Eligible
  var ELIGIBLE_THRESHOLDS = { green: 85, yellow: 50 };
  var getEligibleColor = function (pct) {
    if (pct >= ELIGIBLE_THRESHOLDS.green) return 'green';
    if (pct >= ELIGIBLE_THRESHOLDS.yellow) return 'yellow';
    return 'red';
  };

  // CSRF helper
  var getCSRF = React.useCallback(function () {
    return (
      document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
      document.cookie.match(/csrftoken=([^;]+)/)?.[1] ||
      ''
    );
  }, []);

  // =========================================================================
  // Fetch audit history on mount (for selection step)
  // =========================================================================
  React.useEffect(
    function () {
      if (!instance.opportunity_id) return;
      // Skip FLW history fetch when reopening a saved run — dashboard loads from snapshot
      var existingWorkers =
        instance.state?.selected_workers || instance.state?.selected_flws || [];
      if (existingWorkers.length > 0) return;
      setHistoryLoading(true);
      fetch('/custom_analysis/mbw_monitoring/api/opportunity-flws/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRF(),
        },
        body: JSON.stringify({ opportunities: [instance.opportunity_id] }),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.success) {
            var hm = {};
            (data.flws || []).forEach(function (f) {
              hm[f.username] = f.history || {};
            });
            setFlwHistory(hm);
          }
        })
        .catch(function (err) {
          console.error('Failed to fetch FLW history:', err);
        })
        .finally(function () {
          setHistoryLoading(false);
        });
    },
    [instance.opportunity_id],
  );

  // =========================================================================
  // OAuth: Check auth status on dashboard load
  // =========================================================================
  React.useEffect(
    function () {
      if (step !== 'dashboard') return;
      fetch(
        '/custom_analysis/mbw_monitoring/api/oauth-status/?next=' +
          encodeURIComponent(window.location.pathname + window.location.search),
      )
        .then(function (r) {
          return r.json();
        })
        .then(function (status) {
          setOauthStatus(status);
        })
        .catch(function () {
          // Network error â€” leave oauthStatus null so UI doesn't block
        });
    },
    [step],
  );

  // =========================================================================
  // Pipeline + Job: Detect loaded pipeline data and run analysis via job handler
  // =========================================================================

  // Helper: check if pipelines are ready (visits must have data; others just need to exist)
  var pipelinesReady =
    pipelines &&
    pipelines.visits &&
    pipelines.visits.rows &&
    pipelines.visits.rows.length > 0 &&
    ['registrations', 'gs_forms'].every(function (key) {
      return !pipelines[key] || pipelines[key].rows !== undefined;
    });

  var pipelinesPartial =
    pipelines &&
    ((pipelines.visits &&
      pipelines.visits.rows &&
      pipelines.visits.rows.length > 0) ||
      (pipelines.registrations &&
        pipelines.registrations.rows &&
        pipelines.registrations.rows.length > 0) ||
      (pipelines.gs_forms &&
        pipelines.gs_forms.rows &&
        pipelines.gs_forms.rows.length > 0));

  // Build FLW names from workers prop
  var flwNameMap = React.useMemo(
    function () {
      var m = {};
      (workers || []).forEach(function (w) {
        if (w.username) m[w.username.toLowerCase()] = w.name || w.username;
      });
      return m;
    },
    [workers],
  );

  // Run analysis job when pipelines are ready
  var runAnalysis = React.useCallback(
    function () {
      if (!pipelinesReady || !actions || !actions.startJob) return;
      if (jobRunning) return;

      var sessionFlwsList =
        instance.state?.selected_workers || instance.state?.selected_flws || [];

      setJobRunning(true);
      setJobError(null);
      setJobMessages(['Starting analysis...']);
      setDashData(null);
      setAnalysisComplete(false);

      actions
        .startJob(instance.id, {
          job_type: 'mbw_monitoring',
          pipeline_data: {
            visits: { rows: pipelines.visits.rows },
            registrations: { rows: pipelines.registrations.rows },
            gs_forms: { rows: pipelines.gs_forms.rows },
          },
          active_usernames: sessionFlwsList,
          flw_names: flwNameMap,
          flw_statuses: instance.state?.flw_statuses || {},
          opportunity_id: instance.opportunity_id,
        })
        .then(function (resp) {
          if (!resp || !resp.success) {
            setJobRunning(false);
            setJobError(resp?.error || 'Failed to start analysis job');
            return;
          }
          var taskId = resp.task_id;
          if (!taskId) {
            setJobRunning(false);
            setJobError('No task ID returned from job');
            return;
          }

          setJobMessages(function (prev) {
            return prev.concat(['Job started (task: ' + taskId + ')']);
          });

          // Stream job progress
          var cleanup = actions.streamJobProgress(
            taskId,
            // onProgress
            function (data) {
              if (data.message) {
                setJobMessages(function (prev) {
                  return prev.concat([data.message]);
                });
              }
            },
            // onItemResult
            function (item) {
              // Individual item results (not used for MBW monitoring)
            },
            // onComplete
            function (results) {
              setJobRunning(false);
              setAnalysisComplete(true);

              // Build dashData in the shape the tabs expect
              var gpsData = results.gps_data || {};
              var followupData = results.followup_data || {};
              var qualityMetrics = results.quality_metrics || {};
              var overviewSummary = results.overview_data || {};
              var performanceData = results.performance_data || [];

              // Build overview flw_summaries by merging data from multiple result sections
              var activeUsernamesList =
                instance.state?.selected_workers ||
                instance.state?.selected_flws ||
                [];
              var overviewFlwSummaries = activeUsernamesList.map(
                function (username) {
                  var uLower = username.toLowerCase();
                  var displayName = flwNameMap[uLower] || username;

                  // From GPS data
                  var gpsFlw =
                    (gpsData.flw_summaries || []).find(function (g) {
                      return g.username === uLower;
                    }) || {};
                  var medianMeters = (gpsData.median_meters_by_flw || {})[
                    uLower
                  ];
                  var medianMinutes = (gpsData.median_minutes_by_flw || {})[
                    uLower
                  ];

                  // From follow-up data
                  var fuFlw =
                    (followupData.flw_summaries || []).find(function (f) {
                      return f.username === uLower;
                    }) || {};

                  // From quality metrics
                  var quality = qualityMetrics[uLower] || {};

                  // From overview summary
                  var motherCount =
                    (overviewSummary.mother_counts || {})[uLower] || 0;
                  var ebfPct = (overviewSummary.ebf_pct_by_flw || {})[uLower];

                  // Build cases_still_eligible from drilldown
                  var drilldown =
                    (followupData.flw_drilldown || {})[uLower] || [];
                  var eligibleMothers = drilldown.filter(function (m) {
                    return m.eligible;
                  });
                  var stillOnTrack = 0;
                  eligibleMothers.forEach(function (m) {
                    var completedCount = 0;
                    var missedCount = 0;
                    (m.visits || []).forEach(function (v) {
                      if (v.status && v.status.indexOf('Completed') === 0)
                        completedCount++;
                      if (v.status === 'Missed') missedCount++;
                    });
                    if (completedCount >= 5 || missedCount <= 1) stillOnTrack++;
                  });
                  var totalEligible = eligibleMothers.length;

                  return Object.assign(
                    {
                      username: uLower,
                      display_name: displayName,
                      cases_registered: motherCount,
                      eligible_mothers: totalEligible,
                      first_gs_score: null, // populated below from gs_forms pipeline
                      post_test_attempts: null,
                      followup_rate: fuFlw.completion_rate || 0,
                      ebf_pct: ebfPct != null ? ebfPct : null,
                      revisit_distance_km:
                        gpsFlw.avg_case_distance_km != null
                          ? Math.round(gpsFlw.avg_case_distance_km * 100) / 100
                          : null,
                      median_meters_per_visit:
                        medianMeters != null ? medianMeters : null,
                      median_minutes_per_visit:
                        medianMinutes != null ? medianMinutes : null,
                      cases_still_eligible: {
                        eligible: stillOnTrack,
                        total: totalEligible,
                        pct:
                          totalEligible > 0
                            ? Math.round((stillOnTrack / totalEligible) * 100)
                            : 0,
                      },
                    },
                    quality,
                  );
                },
              );

              // Enrich with GS scores from gs_forms pipeline data
              var gsFormRows =
                (pipelines.gs_forms && pipelines.gs_forms.rows) || [];
              var gsByFlw = {};
              gsFormRows.forEach(function (row) {
                var connectId =
                  (row.computed || row).user_connect_id || row.username || '';
                var uLower = connectId.toLowerCase();
                var score = parseFloat((row.computed || row).gs_score);
                if (!isNaN(score)) {
                  if (!gsByFlw[uLower]) gsByFlw[uLower] = [];
                  gsByFlw[uLower].push({
                    score: score,
                    date: (row.computed || row).assessment_date || '',
                  });
                }
              });
              overviewFlwSummaries.forEach(function (flw) {
                var gsEntries = gsByFlw[flw.username] || [];
                if (gsEntries.length > 0) {
                  // Use the oldest (first) GS score
                  gsEntries.sort(function (a, b) {
                    return (a.date || '').localeCompare(b.date || '');
                  });
                  flw.first_gs_score = Math.round(gsEntries[0].score);
                }
              });

              var builtDashData = {
                success: true,
                gps_data: gpsData,
                followup_data: followupData,
                overview_data: {
                  flw_summaries: overviewFlwSummaries,
                  visit_status_distribution:
                    followupData.visit_status_distribution || {},
                },
                performance_data: performanceData,
                active_usernames: activeUsernamesList
                  .map(function (u) {
                    return u.toLowerCase();
                  })
                  .sort(),
                flw_names: flwNameMap,
                open_tasks: instance.state?.open_tasks || {},
                open_task_usernames: Object.keys(
                  instance.state?.open_tasks || {},
                ),
                monitoring_session: instance.state?.monitoring_session || null,
              };

              setDashData(builtDashData);

              // Restore worker results from monitoring session if available
              if (builtDashData.monitoring_session?.flw_results) {
                setWorkerResults(builtDashData.monitoring_session.flw_results);
              }
            },
            // onError
            function (error) {
              setJobRunning(false);
              setJobError(error || 'Analysis job failed');
            },
            // onCancelled
            function () {
              setJobRunning(false);
              setJobError('Analysis job was cancelled');
            },
          );

          jobCleanupRef.current = cleanup;
        })
        .catch(function (err) {
          setJobRunning(false);
          setJobError('Failed to start job: ' + (err.message || err));
        });
    },
    [
      pipelinesReady,
      jobRunning,
      instance.id,
      instance.state,
      pipelines,
      flwNameMap,
      actions,
    ],
  );

  // Cleanup job stream on unmount
  React.useEffect(function () {
    return function () {
      if (jobCleanupRef.current) jobCleanupRef.current();
    };
  }, []);

  // =========================================================================
  // Sticky table headers via JS (CSS sticky breaks in Chrome due to ancestors)
  // =========================================================================
  React.useEffect(
    function () {
      var HEADER_HEIGHT = 64;
      var theadCache = [];

      function getDocumentOffsetTop(el) {
        var top = 0;
        while (el) {
          top += el.offsetTop;
          el = el.offsetParent;
        }
        return top;
      }

      function cacheTheads() {
        theadCache = [];
        document
          .querySelectorAll('[data-sticky-header] thead')
          .forEach(function (thead) {
            var table = thead.closest('table');
            if (!table) return;
            theadCache.push({
              thead: thead,
              table: table,
              offsetTop: getDocumentOffsetTop(thead),
            });
          });
      }

      function handleScroll() {
        if (theadCache.length === 0) cacheTheads();
        var scrollY = window.scrollY || window.pageYOffset;
        var threshold = scrollY + HEADER_HEIGHT;

        theadCache.forEach(function (d) {
          var tableBottom = d.offsetTop + d.table.offsetHeight;
          var theadH = d.thead.offsetHeight;
          if (threshold > d.offsetTop && threshold < tableBottom - theadH) {
            var offset = threshold - d.offsetTop;
            d.thead.style.transform = 'translateY(' + offset + 'px)';
            d.thead.style.position = 'relative';
            d.thead.style.zIndex = '20';
            d.thead.style.boxShadow = '0 1px 3px rgba(0,0,0,0.1)';
            // Ensure opaque background on all th cells
            Array.from(d.thead.querySelectorAll('th')).forEach(function (th) {
              if (!th.style.backgroundColor)
                th.style.backgroundColor = '#f9fafb';
            });
          } else {
            d.thead.style.transform = '';
            d.thead.style.position = '';
            d.thead.style.zIndex = '';
            d.thead.style.boxShadow = '';
            Array.from(d.thead.querySelectorAll('th')).forEach(function (th) {
              th.style.backgroundColor = '';
            });
          }
        });
      }

      // Small delay to let React finish rendering the active tab
      var timer = setTimeout(function () {
        cacheTheads();
        handleScroll();
      }, 50);

      var resizeHandler = function () {
        theadCache = [];
      };
      window.addEventListener('scroll', handleScroll, { passive: true });
      window.addEventListener('resize', resizeHandler);

      return function () {
        clearTimeout(timer);
        window.removeEventListener('scroll', handleScroll);
        window.removeEventListener('resize', resizeHandler);
        theadCache.forEach(function (d) {
          d.thead.style.transform = '';
          d.thead.style.position = '';
          d.thead.style.zIndex = '';
          d.thead.style.boxShadow = '';
        });
      };
    },
    [activeTab, analysisComplete, showAggregateMap, expandedGps],
  );

  // Load Leaflet + MarkerCluster from CDN for GPS map
  React.useEffect(function () {
    if (window.L && window.L.markerClusterGroup) {
      setLeafletReady(true);
      return;
    }
    [
      'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
      'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css',
      'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css',
    ].forEach(function (href) {
      var link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      document.head.appendChild(link);
    });
    var s1 = document.createElement('script');
    s1.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    s1.onload = function () {
      var s2 = document.createElement('script');
      s2.src =
        'https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js';
      s2.onload = function () {
        setLeafletReady(true);
      };
      document.head.appendChild(s2);
    };
    document.head.appendChild(s1);
  }, []);

  // GPS Map initialization and marker update
  React.useEffect(
    function () {
      if (!leafletReady || !expandedGps || !gpsDetail) {
        if (mapInstanceRef.current) {
          mapInstanceRef.current.remove();
          mapInstanceRef.current = null;
        }
        return;
      }
      var mapDiv = document.getElementById('gps-map-' + expandedGps);
      if (!mapDiv) return;

      // Create map if not exists
      if (!mapInstanceRef.current) {
        mapInstanceRef.current = L.map(mapDiv, { scrollWheelZoom: true });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '© OpenStreetMap contributors',
          maxZoom: 19,
        }).addTo(mapInstanceRef.current);
      }
      var map = mapInstanceRef.current;

      // Remove old markers
      if (markersRef.current) {
        map.removeLayer(markersRef.current);
      }

      var allVisits = (gpsDetail.visits || []).filter(function (v) {
        return v.gps;
      });
      var visitsToShow = selectedMother
        ? allVisits.filter(function (v) {
            return v.mother_case_id === selectedMother;
          })
        : allVisits;

      if (visitsToShow.length === 0) {
        markersRef.current = null;
        map.setView([0, 0], 2);
        return;
      }

      var cluster = L.markerClusterGroup({
        maxClusterRadius: 40,
        disableClusteringAtZoom: 16,
      });
      var hasMarkers = false;

      // Mothers layer (orange markers)
      if (showMapMothers) {
        var motherMap = {};
        visitsToShow.forEach(function (v) {
          var mid = v.mother_case_id || v.case_id;
          if (!mid) return;
          if (
            !motherMap[mid] ||
            (v.visit_date || '') > (motherMap[mid].latest || '')
          ) {
            motherMap[mid] = motherMap[mid] || { visits: [] };
            motherMap[mid].lat = v.gps.latitude;
            motherMap[mid].lng = v.gps.longitude;
            motherMap[mid].name = v.entity_name || mid;
            motherMap[mid].latest = v.visit_date;
          }
          motherMap[mid].visits.push(v);
        });
        Object.keys(motherMap).forEach(function (mid) {
          var m = motherMap[mid];
          var marker = L.circleMarker([m.lat, m.lng], {
            radius: 9,
            fillColor: '#f97316',
            color: '#ea580c',
            weight: 1.5,
            fillOpacity: 0.7,
          });
          marker.bindPopup(
            '<strong>' +
              escapeHtml(m.name) +
              '</strong><br/>' +
              'Visits: ' +
              m.visits.length +
              '<br/>' +
              'Last: ' +
              escapeHtml(m.latest || '-'),
          );
          marker.on('click', function () {
            setSelectedMother(mid);
          });
          cluster.addLayer(marker);
          hasMarkers = true;
        });
      }

      // Visits layer (blue/red markers)
      if (showMapVisits) {
        visitsToShow.forEach(function (v) {
          var color = v.is_flagged ? '#dc2626' : '#3b82f6';
          var borderColor = v.is_flagged ? '#991b1b' : '#1d4ed8';
          var radius = v.is_flagged ? 7 : 6;
          var marker = L.circleMarker([v.gps.latitude, v.gps.longitude], {
            radius: radius,
            fillColor: color,
            color: borderColor,
            weight: 1.5,
            fillOpacity: 0.7,
          });
          marker.bindPopup(
            '<strong>' +
              escapeHtml(v.entity_name || '-') +
              '</strong><br/>' +
              'Date: ' +
              escapeHtml(v.visit_date || '-') +
              '<br/>' +
              'Form: ' +
              escapeHtml(v.form_name || '-') +
              (v.distance_from_prev_km != null
                ? '<br/>Dist: ' + v.distance_from_prev_km + ' km'
                : '') +
              (v.is_flagged
                ? '<br/><span style="color:#dc2626;font-weight:bold">Flagged</span>'
                : ''),
          );
          marker.on('click', function () {
            setSelectedMother(v.mother_case_id || v.case_id);
          });
          cluster.addLayer(marker);
          hasMarkers = true;
        });
      }

      map.addLayer(cluster);
      markersRef.current = cluster;
      if (hasMarkers) {
        map.fitBounds(cluster.getBounds(), { padding: [30, 30] });
      }

      return function () {
        if (markersRef.current && map) {
          map.removeLayer(markersRef.current);
          markersRef.current = null;
        }
      };
    },
    [
      leafletReady,
      expandedGps,
      gpsDetail,
      showMapVisits,
      showMapMothers,
      selectedMother,
    ],
  );

  // Aggregate GPS Map — all FLW visits with color-coded pins
  React.useEffect(
    function () {
      function teardown() {
        if (aggregateMarkersRef.current && aggregateMapRef.current) {
          aggregateMapRef.current.removeLayer(aggregateMarkersRef.current);
          aggregateMarkersRef.current = null;
        }
        if (aggregateMapRef.current) {
          aggregateMapRef.current.remove();
          aggregateMapRef.current = null;
        }
      }
      if (!leafletReady || !showAggregateMap) {
        teardown();
        return;
      }
      var coords = (gpsData && gpsData.all_coordinates) || [];
      if (coords.length === 0) {
        teardown();
        return;
      }

      var mapDiv = document.getElementById('aggregate-gps-map');
      if (!mapDiv) {
        teardown();
        return;
      }

      // Create map if not exists
      if (!aggregateMapRef.current) {
        aggregateMapRef.current = L.map(mapDiv, { scrollWheelZoom: true });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '© OpenStreetMap contributors',
          maxZoom: 19,
        }).addTo(aggregateMapRef.current);
      }
      var map = aggregateMapRef.current;

      // Remove old markers
      if (aggregateMarkersRef.current) {
        map.removeLayer(aggregateMarkersRef.current);
      }

      // Build color palette: unique hue per FLW
      var usernames = {};
      coords.forEach(function (c) {
        usernames[c.u] = true;
      });
      var sortedUsers = Object.keys(usernames).sort();
      var flwColorMap = {};
      sortedUsers.forEach(function (u, i) {
        var hue = Math.round((i * 360) / sortedUsers.length);
        flwColorMap[u] = 'hsl(' + hue + ', 70%, 45%)';
      });

      // Filter by active FLW filter if any
      var activeCoords =
        filterFlws.length > 0
          ? coords.filter(function (c) {
              return filterFlws.indexOf(c.u) >= 0;
            })
          : coords;

      var cluster = L.markerClusterGroup({
        maxClusterRadius: 40,
        disableClusteringAtZoom: 16,
      });

      activeCoords.forEach(function (c) {
        var color = c.f ? '#dc2626' : flwColorMap[c.u] || '#3b82f6';
        var borderColor = c.f ? '#991b1b' : '#374151';
        var radius = c.f ? 7 : 5;
        var flwName = '';
        for (var gi = 0; gi < gpsFlws.length; gi++) {
          if (gpsFlws[gi].username === c.u) {
            flwName = gpsFlws[gi].display_name || c.u;
            break;
          }
        }
        if (!flwName) flwName = c.u;
        var marker = L.circleMarker([c.lat, c.lng], {
          radius: radius,
          fillColor: color,
          color: borderColor,
          weight: 1,
          fillOpacity: 0.7,
        });
        marker.bindPopup(
          '<strong>' +
            escapeHtml(flwName) +
            '</strong>' +
            (c.e ? '<br/>' + escapeHtml(c.e) : '') +
            (c.d ? '<br/>Date: ' + escapeHtml(c.d) : '') +
            (c.f
              ? '<br/><span style="color:#dc2626;font-weight:bold">Flagged</span>'
              : ''),
        );
        cluster.addLayer(marker);
      });

      map.addLayer(cluster);
      aggregateMarkersRef.current = cluster;
      if (activeCoords.length > 0) {
        map.fitBounds(cluster.getBounds(), { padding: [30, 30] });
      }

      return function () {
        if (aggregateMarkersRef.current && aggregateMapRef.current) {
          aggregateMapRef.current.removeLayer(aggregateMarkersRef.current);
          aggregateMarkersRef.current = null;
        }
      };
    },
    [leafletReady, showAggregateMap, gpsData, filterFlws, activeTab],
  );

  // =========================================================================
  // Helpers
  // =========================================================================
  var escapeHtml = function (str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  };
  var toggleFlw = function (username) {
    setSelectedFlws(function (prev) {
      var next = Object.assign({}, prev);
      next[username] = !next[username];
      return next;
    });
  };
  var toggleAll = function () {
    var allSel =
      workers.length > 0 &&
      workers.every(function (w) {
        return selectedFlws[w.username];
      });
    var updated = {};
    workers.forEach(function (w) {
      updated[w.username] = !allSel;
    });
    setSelectedFlws(updated);
  };
  var selectedCount = Object.values(selectedFlws).filter(Boolean).length;

  var handleLaunch = function () {
    var selected = Object.entries(selectedFlws)
      .filter(function (e) {
        return e[1];
      })
      .map(function (e) {
        return e[0];
      });
    if (selected.length === 0) return;
    setLaunching(true);
    onUpdateState({
      selected_workers: selected,
      selected_flws: selected,
      title: title || definition.name,
      tag: tag,
      gs_app_id: gsAppId,
      app_version_op: appVersionOp,
      app_version_val: appVersionVal,
      status_filter: statusFilter,
      worker_results: {},
      flw_results: {},
    })
      .then(function () {
        setStep('dashboard');
        setLaunching(false);
      })
      .catch(function () {
        setLaunching(false);
      });
  };

  // Sort helper — supports nested keys like 'cases_still_eligible.pct'
  var getNestedValue = function (obj, key) {
    if (!obj || !key) return undefined;
    var parts = key.split('.');
    var val = obj;
    for (var i = 0; i < parts.length; i++) {
      if (val == null) return undefined;
      val = val[parts[i]];
    }
    return val;
  };

  var sortRows = function (rows, sortState) {
    var col = sortState.col;
    var dir = sortState.dir;
    return rows.slice().sort(function (a, b) {
      var va = getNestedValue(a, col);
      var vb = getNestedValue(b, col);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === 'string') {
        var cmp = va.localeCompare(vb);
        return dir === 'asc' ? cmp : -cmp;
      }
      return dir === 'asc' ? va - vb : vb - va;
    });
  };

  var toggleSort = function (setter, current, col) {
    if (current.col === col) {
      setter({ col: col, dir: current.dir === 'asc' ? 'desc' : 'asc' });
    } else {
      setter({ col: col, dir: 'asc' });
    }
  };

  var sortIcon = function (sortState, col) {
    if (sortState.col !== col) return '';
    return sortState.dir === 'asc' ? ' ▲' : ' ▼';
  };

  var pctColor = function (val, goodThreshold, badThreshold) {
    if (val == null) return 'text-gray-400';
    if (val >= goodThreshold) return 'text-green-700';
    if (val >= badThreshold) return 'text-amber-600';
    return 'text-red-700';
  };

  var resultBadge = function (result) {
    if (!result) return null;
    var colors = {
      eligible_for_renewal: 'bg-green-100 text-green-800',
      probation: 'bg-amber-100 text-amber-800',
      suspended: 'bg-red-100 text-red-800',
    };
    return React.createElement(
      'span',
      {
        className:
          'px-2 py-0.5 rounded text-xs font-medium ' +
          (colors[result] || 'bg-gray-100 text-gray-700'),
      },
      result.replace(/_/g, ' '),
    );
  };

  // Save worker assessment result (optimistic UI — updates instantly, reverts on error)
  // Toggle behavior: clicking the active status clears it
  var handleAssessment = function (username, result) {
    if (!actions || !actions.saveWorkerResult) {
      showToast('Assessment not available — please hard-refresh (Cmd+Shift+R)');
      return;
    }
    // Toggle: if already set to this result, clear it
    var currentResult = (workerResults[username] || {}).result;
    var newResult = currentResult === result ? null : result;

    // Optimistic: update UI immediately
    var previous = Object.assign({}, workerResults);
    var updated = Object.assign({}, workerResults);
    updated[username] = {
      result: newResult,
      notes: (workerResults[username] || {}).notes || '',
    };
    setWorkerResults(updated);

    // Save to backend (UI already updated, no need to disable buttons)
    actions
      .saveWorkerResult(instance.id, {
        username: username,
        result: newResult,
        notes: updated[username].notes,
      })
      .then(function (resp) {
        if (resp && resp.success !== false) {
          showToast(
            newResult
              ? 'Assessment saved: ' + newResult.replace(/_/g, ' ')
              : 'Assessment cleared',
          );
        } else {
          setWorkerResults(previous);
          showToast('Failed to save: ' + (resp.error || 'Unknown error'));
        }
      })
      .catch(function (err) {
        setWorkerResults(previous);
        console.error('Assessment save failed:', err);
        showToast('Assessment save failed: ' + (err.message || err));
      });
  };

  // Save snapshot — reused by manual button and auto-save on Complete

  // Complete session — auto-saves snapshot first (best-effort)
  var handleComplete = function () {
    if (!actions || !actions.completeRun) {
      showToast('Complete not available — please hard-refresh (Cmd+Shift+R)');
      return;
    }
    setCompleting(true);
    // Save snapshot first, then complete the run
    Promise.resolve(true)
      .catch(function () {
        return false;
      })
      .then(function () {
        return actions.completeRun(instance.id, {
          overall_result: 'completed',
          notes: completeNotes,
        });
      })
      .then(function (resp) {
        if (resp && resp.success !== false) {
          setShowCompleteModal(false);
          setCompleting(false);
          window.location.reload();
        } else {
          showToast('Failed to complete: ' + (resp.error || 'Unknown error'));
          setCompleting(false);
        }
      })
      .catch(function (err) {
        console.error('Complete failed:', err);
        showToast('Complete failed: ' + (err.message || err));
        setCompleting(false);
      });
  };

  // GPS detail — toggle which FLW is expanded; the useEffect below handles fetching
  var fetchGpsDetail = function (username) {
    if (expandedGps === username) {
      setExpandedGps(null);
      return;
    }
    setExpandedGps(username);
    setSelectedMother(null);
  };

  // Fetch GPS visits when expandedGps changes (useEffect runs after render commit,
  // avoiding the inline-fetch-in-click-handler issue that silently failed to fire)
  React.useEffect(
    function () {
      if (!expandedGps) {
        setGpsDetail(null);
        setGpsDetailLoading(false);
        return;
      }
      // Check embedded visits first (available when loaded from snapshot)
      var currentGpsFlws =
        (dashData && dashData.gps_data && dashData.gps_data.flw_summaries) ||
        [];
      var flw = currentGpsFlws.find(function (f) {
        return f.username === expandedGps;
      });
      if (flw && flw.visits && flw.visits.length > 0) {
        setGpsDetail({ success: true, visits: flw.visits });
        setGpsDetailLoading(false);
        return;
      }
      // Fetch from API (SSE data doesn't embed visits to save memory)
      setGpsDetailLoading(true);
      setGpsDetail(null);
      var cancelled = false;
      var end = new Date();
      var start = new Date();
      start.setDate(end.getDate() - 30);
      var params = new URLSearchParams({
        start_date: start.toISOString().split('T')[0],
        end_date: end.toISOString().split('T')[0],
      });
      if (instance.opportunity_id) {
        params.set('opportunity_id', String(instance.opportunity_id));
      }
      if (appliedAppVersionOp && appliedAppVersionVal) {
        params.set('app_version_op', appliedAppVersionOp);
        params.set('app_version_val', appliedAppVersionVal);
      }
      if (appliedStatusFilter && appliedStatusFilter.length > 0) {
        params.set('status_filter', appliedStatusFilter.join(','));
      }
      var url =
        '/custom_analysis/mbw_monitoring/api/gps/' +
        encodeURIComponent(expandedGps) +
        '/?' +
        params.toString();
      fetch(url, { credentials: 'same-origin' })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (!cancelled) {
            setGpsDetail(data);
            setGpsDetailLoading(false);
          }
        })
        .catch(function () {
          if (!cancelled) {
            setGpsDetail({ success: false, visits: [] });
            setGpsDetailLoading(false);
          }
        });
      return function () {
        cancelled = true;
      };
    },
    [
      expandedGps,
      dashData,
      instance.opportunity_id,
      appliedAppVersionOp,
      appliedAppVersionVal,
      appliedStatusFilter,
    ],
  );

  // Toast helper
  var showToast = function (msg) {
    setToastMessage(msg);
    setTimeout(function () {
      setToastMessage('');
    }, 3000);
  };

  // Filter helpers
  var addToFilter = function (username) {
    setFilterFlws(function (prev) {
      if (prev.indexOf(username) >= 0) return prev;
      return prev.concat([username]);
    });
    var flwNames = dashData?.flw_names || {};
    showToast('Filtered to ' + (flwNames[username] || username));
  };

  var resetFilters = function () {
    setFilterFlws([]);
    setFilterMothers([]);
    setAppVersionOp('gt');
    setAppVersionVal('14');
    setAppliedAppVersionOp('gt');
    setAppliedAppVersionVal('14');
  };

  // FLW Notes modal helpers
  var openFlwNotesModal = function (username) {
    var wr = workerResults[username] || {};
    setFlwNotesUsername(username);
    setFlwNotesText(wr.notes || '');
    setFlwNotesResult(wr.result || null);
    setShowFlwNotesModal(true);
  };

  var saveFlwNotes = function () {
    if (!actions || !actions.saveWorkerResult) {
      showToast('Save not available — please hard-refresh (Cmd+Shift+R)');
      return;
    }
    var username = flwNotesUsername;
    var result = flwNotesResult;
    var notes = flwNotesText;
    actions
      .saveWorkerResult(instance.id, {
        username: username,
        result: result,
        notes: notes,
      })
      .then(function (resp) {
        if (resp && resp.success !== false) {
          var updated = Object.assign({}, workerResults);
          updated[username] = { result: result, notes: notes };
          setWorkerResults(updated);
          showToast('Notes saved');
        } else {
          showToast('Failed to save notes: ' + (resp.error || 'Unknown error'));
        }
        setShowFlwNotesModal(false);
      })
      .catch(function (err) {
        console.error('Notes save failed:', err);
        showToast('Notes save failed: ' + (err.message || err));
      });
  };

  // Visit status style helper (inline styles to avoid Tailwind purge)
  var getVisitStatusStyle = function (status) {
    if (!status) return { backgroundColor: '#f3f4f6', color: '#1f2937' };
    if (status === 'Completed - On Time')
      return { backgroundColor: '#dcfce7', color: '#166534' };
    if (status === 'Completed - Late')
      return { backgroundColor: '#f0fdf4', color: '#15803d' };
    if (status === 'Due - On Time')
      return { backgroundColor: '#fef9c3', color: '#854d0e' };
    if (status === 'Due - Late')
      return { backgroundColor: '#ffedd5', color: '#9a3412' };
    if (status === 'Missed')
      return { backgroundColor: '#fee2e2', color: '#991b1b' };
    return { backgroundColor: '#f3f4f6', color: '#1f2937' };
  };

  // =========================================================================
  // RENDER: STEP 1 - FLW SELECTION
  // =========================================================================
  if (step === 'select') {
    var filteredWorkers = workers;
    if (selSearch) {
      var q = selSearch.toLowerCase();
      filteredWorkers = workers.filter(function (w) {
        return (
          (w.name || '').toLowerCase().indexOf(q) >= 0 ||
          (w.username || '').toLowerCase().indexOf(q) >= 0
        );
      });
    }

    // Sort filtered workers
    var sortCol = selSort.col;
    var sortDir = selSort.dir;
    filteredWorkers = filteredWorkers.slice().sort(function (a, b) {
      var ha = flwHistory[a.username] || {};
      var hb = flwHistory[b.username] || {};
      var va, vb;
      if (sortCol === 'name') {
        va = (a.name || a.username || '').toLowerCase();
        vb = (b.name || b.username || '').toLowerCase();
      } else if (sortCol === 'username') {
        va = (a.username || '').toLowerCase();
        vb = (b.username || '').toLowerCase();
      } else if (sortCol === 'audit_count') {
        va = ha.audit_count || 0;
        vb = hb.audit_count || 0;
      } else if (sortCol === 'last_audit_date') {
        va = ha.last_audit_date || '';
        vb = hb.last_audit_date || '';
      } else if (sortCol === 'last_audit_result') {
        va = ha.last_audit_result || '';
        vb = hb.last_audit_result || '';
      } else if (sortCol === 'open_task_count') {
        va = ha.open_task_count || 0;
        vb = hb.open_task_count || 0;
      } else {
        va = '';
        vb = '';
      }
      var cmp =
        typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb));
      return sortDir === 'asc' ? cmp : -cmp;
    });

    var selSortHeader = function (col, label, align) {
      var active = selSort.col === col;
      return (
        <th
          className={
            'px-4 py-2 text-xs font-medium text-gray-500 uppercase cursor-pointer hover:bg-gray-100 select-none' +
            (align === 'center' ? ' text-center' : ' text-left')
          }
          onClick={function () {
            setSelSort({
              col: col,
              dir: active && selSort.dir === 'asc' ? 'desc' : 'asc',
            });
          }}
        >
          {label} {active ? (selSort.dir === 'asc' ? '▲' : '▼') : ''}
        </th>
      );
    };

    return (
      <div className="space-y-6">
        <div className="bg-white rounded-lg shadow-sm p-6">
          <h2 className="text-xl font-bold text-gray-900">
            Select FLWs for Monitoring
          </h2>
          <p className="text-gray-600 mt-1">
            Choose which frontline workers to include in this monitoring
            session.
          </p>
          <div className="grid grid-cols-3 gap-4 mt-4">
            <div>
              <label className="text-sm font-medium text-gray-700">
                Session Title
              </label>
              <input
                type="text"
                value={title}
                onChange={function (e) {
                  setTitle(e.target.value);
                }}
                placeholder="e.g., March 2025 Review"
                className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700">
                Tag (optional)
              </label>
              <input
                type="text"
                value={tag}
                onChange={function (e) {
                  setTag(e.target.value);
                }}
                placeholder="e.g., monthly-review"
                className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700">
                GS App ID
              </label>
              <input
                type="text"
                value={gsAppId}
                onChange={function (e) {
                  setGsAppId(e.target.value);
                }}
                placeholder="CommCare HQ app ID for Gold Standard forms"
                className="mt-1 w-full border rounded-md px-3 py-2 text-sm font-mono text-xs"
              />
              <p className="text-xs text-gray-400 mt-0.5">
                Supervisor app containing Gold Standard Visit Checklist
              </p>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-lg shadow-sm overflow-hidden">
          {historyLoading && (
            <div className="px-4 py-2 text-xs text-gray-400 bg-gray-50 border-b">
              Loading audit history...
            </div>
          )}
          <div className="px-4 py-2 bg-gray-50 border-b flex items-center gap-2">
            <input
              type="text"
              value={selSearch}
              onChange={function (e) {
                setSelSearch(e.target.value);
              }}
              placeholder="Search FLWs..."
              className="border rounded px-2 py-1 text-sm flex-1"
            />
            <span className="text-sm text-gray-500">
              {selectedCount} selected
            </span>
          </div>
          <div className="max-h-96 overflow-y-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="px-4 py-2 text-left w-10">
                    <input
                      type="checkbox"
                      checked={
                        workers.length > 0 &&
                        workers.every(function (w) {
                          return selectedFlws[w.username];
                        })
                      }
                      onChange={toggleAll}
                    />
                  </th>
                  {selSortHeader('name', 'FLW (' + workers.length + ')')}
                  {selSortHeader('username', 'Connect ID')}
                  {selSortHeader('audit_count', 'Past Audits', 'center')}
                  {selSortHeader('last_audit_date', 'Last Audit Date')}
                  {selSortHeader('last_audit_result', 'Last Result')}
                  {selSortHeader('open_task_count', 'Open Tasks')}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {filteredWorkers.map(function (w) {
                  var h = flwHistory[w.username] || {};
                  return (
                    <tr
                      key={w.username}
                      className="hover:bg-gray-50 cursor-pointer"
                      onClick={function () {
                        toggleFlw(w.username);
                      }}
                    >
                      <td className="px-4 py-2">
                        <input
                          type="checkbox"
                          checked={!!selectedFlws[w.username]}
                          onChange={function () {
                            toggleFlw(w.username);
                          }}
                          onClick={function (e) {
                            e.stopPropagation();
                          }}
                        />
                      </td>
                      <td className="px-4 py-2">
                        <div className="font-medium text-sm">
                          {w.name || w.username}
                        </div>
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-500 font-mono">
                        {w.username}
                      </td>
                      <td className="px-4 py-2 text-center text-sm text-gray-600">
                        {h.audit_count > 0 ? (
                          h.audit_count
                        ) : (
                          <span className="text-gray-300">{'—'}</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-sm text-gray-600">
                        {h.last_audit_date ? (
                          new Date(h.last_audit_date).toLocaleDateString(
                            'en-US',
                            { month: 'short', day: 'numeric', year: 'numeric' },
                          )
                        ) : (
                          <span className="text-gray-300">{'—'}</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-sm">
                        {h.last_audit_result ? (
                          <span
                            className={
                              h.last_audit_result === 'eligible_for_renewal'
                                ? 'text-green-700 bg-green-50 px-2 py-0.5 rounded text-xs'
                                : h.last_audit_result === 'probation'
                                ? 'text-amber-700 bg-amber-50 px-2 py-0.5 rounded text-xs'
                                : h.last_audit_result === 'suspended'
                                ? 'text-red-700 bg-red-50 px-2 py-0.5 rounded text-xs'
                                : 'text-gray-600 text-xs'
                            }
                          >
                            {h.last_audit_result.replace(/_/g, ' ')}
                          </span>
                        ) : (
                          <span className="text-gray-300">{'—'}</span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        {h.open_task_count > 0 ? (
                          <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
                            {h.open_task_count} open
                          </span>
                        ) : (
                          <span className="text-gray-300 text-sm">{'—'}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="flex justify-end">
          <button
            onClick={handleLaunch}
            disabled={selectedCount === 0 || launching}
            className="px-6 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
          >
            {launching
              ? 'Launching...'
              : 'Launch Dashboard (' + selectedCount + ' FLWs)'}
          </button>
        </div>
      </div>
    );
  }

  // =========================================================================
  // RENDER: STEP 2 - DASHBOARD
  // =========================================================================
  var sessionFlws =
    instance.state?.selected_workers || instance.state?.selected_flws || [];
  var totalFlws = sessionFlws.length;
  var assessedCount = Object.values(workerResults).filter(function (r) {
    return r && r.result;
  }).length;
  var progressPct =
    totalFlws > 0 ? Math.round((assessedCount / totalFlws) * 100) : 0;
  var isCompleted = instance.status === 'completed';
  var monitoringSession = dashData?.monitoring_session || null;
  var isSessionActive = monitoringSession
    ? monitoringSession.status === 'in_progress'
    : !isCompleted;

  // ---- OAuth expired state ----
  if (
    oauthStatus &&
    (!oauthStatus.connect?.active || !oauthStatus.commcare?.active)
  ) {
    var expiredServices = [];
    if (!oauthStatus.connect?.active)
      expiredServices.push({
        name: 'Connect',
        key: 'connect',
        url: oauthStatus.connect?.authorize_url,
      });
    if (!oauthStatus.commcare?.active)
      expiredServices.push({
        name: 'CommCare HQ',
        key: 'commcare',
        url: oauthStatus.commcare?.authorize_url,
      });
    if (!oauthStatus.ocs?.active)
      expiredServices.push({
        name: 'OCS',
        key: 'ocs',
        url: oauthStatus.ocs?.authorize_url,
      });
    var activeServices = [];
    if (oauthStatus.connect?.active) activeServices.push('Connect');
    if (oauthStatus.commcare?.active) activeServices.push('CommCare HQ');
    if (oauthStatus.ocs?.active) activeServices.push('OCS');

    return (
      <div className="space-y-4">
        <div className="bg-white rounded-lg shadow-sm p-6">
          <h2 className="text-xl font-bold text-gray-900">
            {instance.state?.title || 'MBW Monitoring'}
          </h2>
          <p className="text-gray-500 mt-1">
            Authentication required before loading data
          </p>
        </div>
        <div className="bg-red-50 border border-red-300 rounded-lg p-5">
          <div className="flex items-center gap-2 mb-3">
            <i className="fa-solid fa-triangle-exclamation text-red-600"></i>
            <span className="font-semibold text-red-800">
              OAuth tokens expired
            </span>
          </div>
          <p className="text-sm text-red-700 mb-4">
            One or more authentication tokens have expired. Please re-authorize
            before loading data.
          </p>
          <div className="space-y-2 mb-4">
            {expiredServices.map(function (svc) {
              return (
                <div key={svc.key} className="flex items-center gap-3">
                  <i className="fa-solid fa-circle-xmark text-red-500"></i>
                  <span className="text-sm font-medium text-gray-800 w-32">
                    {svc.name}
                  </span>
                  {svc.url ? (
                    <a
                      href={svc.url}
                      className="px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-700 no-underline"
                    >
                      Authorize {svc.name}
                    </a>
                  ) : (
                    <span className="text-sm text-gray-500">
                      No authorization URL available
                    </span>
                  )}
                </div>
              );
            })}
            {activeServices.map(function (name) {
              return (
                <div key={name} className="flex items-center gap-3">
                  <i className="fa-solid fa-circle-check text-green-500"></i>
                  <span className="text-sm font-medium text-gray-800 w-32">
                    {name}
                  </span>
                  <span className="text-sm text-green-600">Active</span>
                </div>
              );
            })}
          </div>
          <button
            onClick={function () {
              window.location.reload();
            }}
            className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
          >
            <i className="fa-solid fa-rotate-right mr-1"></i> Retry
          </button>
        </div>
      </div>
    );
  }

  // ---- Pipeline loading / Job running / Error state ----
  if (!analysisComplete || !dashData) {
    var visitCount =
      pipelines && pipelines.visits && pipelines.visits.rows
        ? pipelines.visits.rows.length
        : 0;
    var regCount =
      pipelines && pipelines.registrations && pipelines.registrations.rows
        ? pipelines.registrations.rows.length
        : 0;
    var gsCount =
      pipelines && pipelines.gs_forms && pipelines.gs_forms.rows
        ? pipelines.gs_forms.rows.length
        : 0;

    return (
      <div className="space-y-4">
        <div className="bg-white rounded-lg shadow-sm p-6">
          <h2 className="text-xl font-bold text-gray-900">
            {instance.state?.title || 'MBW Monitoring V2'}
          </h2>
          <p className="text-gray-500 mt-1">Pipeline-based dashboard</p>
        </div>

        {/* Pipeline Status */}
        <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            Pipeline Data Sources
          </h3>
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <i
                className={
                  'fa-solid ' +
                  (visitCount > 0
                    ? 'fa-circle-check text-green-500'
                    : 'fa-spinner fa-spin text-blue-500')
                }
              ></i>
              <span className="text-sm text-gray-700">Visit Forms</span>
              <span className="text-xs text-gray-500 ml-auto">
                {visitCount > 0 ? visitCount + ' rows' : 'Loading...'}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <i
                className={
                  'fa-solid ' +
                  (regCount > 0
                    ? 'fa-circle-check text-green-500'
                    : pipelines &&
                      pipelines.registrations &&
                      pipelines.registrations.rows
                    ? 'fa-circle-check text-amber-500'
                    : 'fa-spinner fa-spin text-blue-500')
                }
              ></i>
              <span className="text-sm text-gray-700">Registration Forms</span>
              <span className="text-xs text-gray-500 ml-auto">
                {regCount > 0
                  ? regCount + ' rows'
                  : pipelines &&
                    pipelines.registrations &&
                    pipelines.registrations.rows
                  ? '0 rows (none found)'
                  : 'Loading...'}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <i
                className={
                  'fa-solid ' +
                  (gsCount > 0
                    ? 'fa-circle-check text-green-500'
                    : pipelines && pipelines.gs_forms && pipelines.gs_forms.rows
                    ? 'fa-circle-check text-amber-500'
                    : 'fa-spinner fa-spin text-blue-500')
                }
              ></i>
              <span className="text-sm text-gray-700">Gold Standard Forms</span>
              <span className="text-xs text-gray-500 ml-auto">
                {gsCount > 0
                  ? gsCount + ' rows'
                  : pipelines && pipelines.gs_forms && pipelines.gs_forms.rows
                  ? '0 rows (none found)'
                  : 'Loading...'}
              </span>
            </div>
          </div>
        </div>

        {/* Error State */}
        {jobError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <div className="flex items-center gap-2 text-red-800">
              <i className="fa-solid fa-circle-exclamation"></i>
              <span className="font-medium">{jobError}</span>
            </div>
            <div className="mt-3">
              <button
                onClick={function () {
                  setJobError(null);
                  runAnalysis();
                }}
                disabled={!pipelinesReady}
                className="px-4 py-2 bg-red-600 text-white rounded text-sm hover:bg-red-700 disabled:opacity-50"
              >
                Retry Analysis
              </button>
            </div>
          </div>
        )}

        {/* Job Running State */}
        {jobRunning && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <div className="flex items-center gap-3 mb-3">
              <div className="animate-spin h-5 w-5 border-2 border-blue-600 border-t-transparent rounded-full"></div>
              <span className="font-medium text-blue-800">
                Running analysis...
              </span>
            </div>
            <div className="space-y-1 text-sm text-blue-700 max-h-40 overflow-y-auto">
              {jobMessages.map(function (msg, i) {
                return <div key={i}>{msg}</div>;
              })}
            </div>
          </div>
        )}

        {/* Run Analysis Button */}
        {!jobRunning && !jobError && pipelinesReady && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-4">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-medium text-green-800">
                  All pipelines loaded
                </span>
                <p className="text-sm text-green-600 mt-1">
                  {visitCount} visits, {regCount} registrations, {gsCount} GS
                  forms loaded.
                </p>
              </div>
              <button
                onClick={runAnalysis}
                className="px-6 py-2.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 shadow-sm"
              >
                <i className="fa-solid fa-play mr-2"></i> Run Analysis
              </button>
            </div>
          </div>
        )}

        {/* Waiting for pipelines */}
        {!jobRunning && !jobError && !pipelinesReady && pipelinesPartial && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <div className="flex items-center gap-3">
              <div className="animate-spin h-5 w-5 border-2 border-blue-600 border-t-transparent rounded-full"></div>
              <span className="font-medium text-blue-800">
                Waiting for all pipeline data to load...
              </span>
            </div>
          </div>
        )}

        {!jobRunning && !jobError && !pipelinesPartial && (
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <div className="flex items-center gap-3">
              <div className="animate-spin h-5 w-5 border-2 border-gray-400 border-t-transparent rounded-full"></div>
              <span className="font-medium text-gray-600">
                Initializing pipeline data sources...
              </span>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ---- Dashboard data is loaded ----
  var overviewFlws = dashData?.overview_data?.flw_summaries || [];
  var gpsData = dashData?.gps_data || {};
  var gpsFlws = gpsData.flw_summaries || [];
  var followupData = dashData?.followup_data || {};
  var fuFlws = followupData.flw_summaries || [];
  var fuDrilldown = followupData.flw_drilldown || {};
  var visitDist = dashData?.overview_data?.visit_status_distribution || {};
  var openTasks = dashData?.open_tasks || {};
  var openTaskUsernames =
    dashData?.open_task_usernames || Object.keys(openTasks);
  var flwNames = dashData?.flw_names || {};
  var activeUsernames = dashData?.active_usernames || [];

  // Build mother IDs from drilldown
  var allMotherIds = [];
  var motherNamesMap = {};
  Object.keys(fuDrilldown).forEach(function (u) {
    (fuDrilldown[u] || []).forEach(function (m) {
      if (m.mother_case_id && !motherNamesMap[m.mother_case_id]) {
        motherNamesMap[m.mother_case_id] = m.mother_name || m.mother_case_id;
        allMotherIds.push(m.mother_case_id);
      }
    });
  });
  allMotherIds.sort(function (a, b) {
    return (motherNamesMap[a] || a).localeCompare(motherNamesMap[b] || b);
  });

  // ---- Build monthly visit schedule data ----
  var MONTHLY_VISIT_MONTHS = [
    '2025-09',
    '2025-10',
    '2025-11',
    '2025-12',
    '2026-01',
    '2026-02',
    '2026-03',
    '2026-04',
    '2026-05',
    '2026-06',
    '2026-07',
  ];
  var MONTHLY_VISIT_TYPES = [
    'ANC',
    'Postnatal',
    'Week 1',
    'Month 1',
    'Month 3',
    'Month 6',
  ];
  var MONTHLY_VISIT_LABELS = {
    '2025-09': 'Sep 25',
    '2025-10': 'Oct 25',
    '2025-11': 'Nov 25',
    '2025-12': 'Dec 25',
    '2026-01': 'Jan 26',
    '2026-02': 'Feb 26',
    '2026-03': 'Mar 26',
    '2026-04': 'Apr 26',
    '2026-05': 'May 26',
    '2026-06': 'Jun 26',
    '2026-07': 'Jul 26',
  };
  var monthlyVisitData = {};
  MONTHLY_VISIT_TYPES.forEach(function (vt) {
    monthlyVisitData[vt] = {};
    MONTHLY_VISIT_MONTHS.forEach(function (m) {
      monthlyVisitData[vt][m] = { completed: 0, total: 0 };
    });
  });
  Object.keys(fuDrilldown).forEach(function (username) {
    if (filterFlws.length > 0 && filterFlws.indexOf(username) < 0) return;
    (fuDrilldown[username] || []).forEach(function (mother) {
      (mother.visits || []).forEach(function (v) {
        var sched = v.visit_date_scheduled;
        if (!sched || !v.visit_type) return;
        var monthKey = sched.substring(0, 7);
        if (
          !monthlyVisitData[v.visit_type] ||
          !monthlyVisitData[v.visit_type][monthKey]
        )
          return;
        monthlyVisitData[v.visit_type][monthKey].total += 1;
        if (v.status && v.status.indexOf('Completed') === 0) {
          monthlyVisitData[v.visit_type][monthKey].completed += 1;
        }
      });
    });
  });

  var fmtVisitCell = function (completed, total) {
    if (total === 0) return null;
    if (monthlyViewPct) return Math.round((completed / total) * 100) + '%';
    if (monthlyCountMode === 'completed') return String(completed);
    if (monthlyCountMode === 'scheduled') return String(total);
    return completed + ' / ' + total;
  };

  // ---- Build FLW prompt for OCS AI Assistant ----
  var buildFLWPrompt = function (username) {
    var ov =
      overviewFlws.find(function (f) {
        return f.username === username;
      }) || {};
    var fu =
      fuFlws.find(function (f) {
        return f.username === username;
      }) || {};
    var vtKeys = ['anc', 'postnatal', 'week1', 'month1', 'month3', 'month6'];
    var vtLabels = {
      anc: 'ANC',
      postnatal: 'Postnatal',
      week1: 'Week 1-2',
      month1: 'Month 1',
      month3: 'Month 3',
      month6: 'Month 6',
    };

    // Red flag detection
    var redFlags = [];
    if (ov.first_gs_score != null && ov.first_gs_score < 50)
      redFlags.push({
        label: 'Low Gold Standard Score',
        detail: 'GS Score: ' + ov.first_gs_score + '% (below 50% threshold)',
      });
    if (ov.followup_rate != null && ov.followup_rate < 50)
      redFlags.push({
        label: 'Low Follow-Up Visit Rate',
        detail:
          'Follow-up Rate: ' + ov.followup_rate + '% (below 50% threshold)',
      });
    if (
      ov.cases_still_eligible &&
      ov.cases_still_eligible.pct != null &&
      ov.cases_still_eligible.pct < 50
    )
      redFlags.push({
        label: 'Low Case Eligibility Rate',
        detail:
          'Eligible 5+: ' +
          ov.cases_still_eligible.pct +
          '% (below 50% threshold)',
      });
    if (ov.median_meters_per_visit != null && ov.median_meters_per_visit < 100)
      redFlags.push({
        label: 'Low Travel Distance Per Visit',
        detail:
          'Meter/Visit: ' +
          ov.median_meters_per_visit +
          'm (below 100m threshold)',
      });
    if (ov.phone_dup_pct != null && ov.phone_dup_pct > 30)
      redFlags.push({
        label: 'High Phone Duplicate Rate',
        detail: 'Phone Dup: ' + ov.phone_dup_pct + '% (above 30% threshold)',
      });
    if (ov.anc_pnc_same_date_count != null && ov.anc_pnc_same_date_count >= 5)
      redFlags.push({
        label: 'ANC/PNC Same-Date Anomaly',
        detail:
          'ANC=PNC same date: ' +
          ov.anc_pnc_same_date_count +
          ' cases (5+ threshold)',
      });
    if (ov.ebf_pct != null && (ov.ebf_pct <= 30 || ov.ebf_pct > 95))
      redFlags.push({
        label: 'Abnormal EBF Rate',
        detail:
          'EBF Rate: ' +
          ov.ebf_pct +
          '% (' +
          (ov.ebf_pct <= 30 ? 'below 30%' : 'above 95%') +
          ' threshold)',
      });

    var behavior =
      redFlags.length > 0
        ? redFlags
            .map(function (r) {
              return r.label;
            })
            .join(', ')
        : 'General Performance Review';

    var lines = [];
    lines.push('FLW Name: ' + (ov.display_name || username));
    lines.push('Username: ' + username);
    lines.push(
      'Last Active: ' +
        (ov.last_active_days != null
          ? ov.last_active_days + ' days ago (' + ov.last_active_date + ')'
          : '—'),
    );
    lines.push('');
    lines.push('Behavior Being Investigated: ' + behavior);
    lines.push('');
    lines.push('Performance Summary:');
    lines.push(
      '- Mothers registered: ' +
        (ov.cases_registered != null ? ov.cases_registered : '—'),
    );
    lines.push(
      '- Eligible mothers: ' +
        (ov.eligible_mothers != null ? ov.eligible_mothers : '—'),
    );
    lines.push(
      '- GS Score: ' +
        (ov.first_gs_score != null ? ov.first_gs_score + '%' : '—'),
    );
    lines.push(
      '- Follow-up Rate: ' +
        (ov.followup_rate != null ? ov.followup_rate + '%' : '—'),
    );
    lines.push(
      '- Cases still eligible (5+): ' +
        (ov.cases_still_eligible && ov.cases_still_eligible.pct != null
          ? ov.cases_still_eligible.pct +
            '% (' +
            ov.cases_still_eligible.eligible +
            '/' +
            ov.cases_still_eligible.total +
            ')'
          : '—'),
    );
    lines.push(
      '- % EBF (Exclusive Breastfeeding): ' +
        (ov.ebf_pct != null ? ov.ebf_pct + '%' : '—'),
    );
    lines.push(
      '- Revisit Dist. (avg same-mother): ' +
        (ov.revisit_distance_km != null ? ov.revisit_distance_km + ' km' : '—'),
    );
    lines.push(
      '- Meter/Visit (median inter-visit): ' +
        (ov.median_meters_per_visit != null
          ? ov.median_meters_per_visit + ' m'
          : '—'),
    );
    lines.push(
      '- Minute/Visit (median inter-visit): ' +
        (ov.median_minutes_per_visit != null
          ? ov.median_minutes_per_visit + ' min'
          : '—'),
    );
    lines.push(
      '- Phone Dup %: ' +
        (ov.phone_dup_pct != null ? ov.phone_dup_pct + '%' : '—'),
    );
    lines.push(
      '- ANC = PNC (same-date count): ' +
        (ov.anc_pnc_same_date_count != null ? ov.anc_pnc_same_date_count : '—'),
    );
    lines.push(
      '- Parity concentration: ' +
        (ov.parity_concentration &&
        ov.parity_concentration.pct_duplicate != null
          ? ov.parity_concentration.pct_duplicate +
            '% duplicate (mode: ' +
            ov.parity_concentration.mode_value +
            ', ' +
            ov.parity_concentration.mode_pct +
            '%)'
          : '—'),
    );
    lines.push(
      '- Age concentration: ' +
        (ov.age_concentration && ov.age_concentration.pct_duplicate != null
          ? ov.age_concentration.pct_duplicate +
            '% duplicate (mode: ' +
            ov.age_concentration.mode_value +
            ', ' +
            ov.age_concentration.mode_pct +
            '%)'
          : '—'),
    );
    lines.push(
      '- Age = Reg date: ' +
        (ov.age_equals_reg_pct != null ? ov.age_equals_reg_pct + '%' : '—'),
    );
    lines.push('');
    lines.push('Visit Overview:');
    var completionPct =
      fu.total_visits > 0
        ? Math.round((fu.completed_total / fu.total_visits) * 100)
        : 0;
    lines.push('- Total visits: ' + (fu.total_visits || 0));
    lines.push(
      '- Completed: ' +
        (fu.completed_total || 0) +
        ' (' +
        completionPct +
        '% completion rate) — on time: ' +
        (fu.completed_on_time || 0) +
        ', late: ' +
        (fu.completed_late || 0),
    );
    lines.push('- Due (on time): ' + (fu.due_on_time || 0));
    lines.push('- Due (late): ' + (fu.due_late || 0));
    lines.push('- Missed: ' + (fu.missed_total || 0));
    lines.push('');
    lines.push('Breakdown by visit type:');
    vtKeys.forEach(function (vt) {
      var comp =
        (fu[vt + '_completed_on_time'] || 0) +
        (fu[vt + '_completed_late'] || 0);
      var dueLate = fu[vt + '_due_late'] || 0;
      var missed = fu[vt + '_missed'] || 0;
      lines.push(
        '- ' +
          (vtLabels[vt] || vt) +
          ': ' +
          comp +
          ' completed, ' +
          dueLate +
          ' due late, ' +
          missed +
          ' missed',
      );
    });
    lines.push('');
    if (redFlags.length > 0) {
      lines.push('Red Flag Indicators:');
      redFlags.forEach(function (r) {
        lines.push('- ' + r.detail);
      });
    } else {
      lines.push('Red Flag Indicators:');
      lines.push('No red flag indicators detected.');
    }
    return lines.join('\n');
  };

  // ---- OCS Modal handlers ----
  var openOcsModal = function (f) {
    setOcsModalFlw(f);
    setOcsError('');
    setOcsCreating(false);
    setSelectedBot('');
    setOcsPrompt(buildFLWPrompt(f.username));
    setShowOcsModal(true);
    setOcsLoading(true);
    actions.listOCSBots().then(function (result) {
      setOcsLoading(false);
      if (result.success && result.bots) {
        setOcsBots(result.bots);
        if (result.bots.length === 1) setSelectedBot(result.bots[0].id);
      } else if (result.needs_oauth) {
        setOcsError('OCS authentication required. Please contact admin.');
      } else {
        setOcsError(result.error || 'Failed to load bots');
      }
    });
  };

  var handleCreateTaskWithOCS = function () {
    if (!selectedBot) {
      setOcsError('Please select a bot');
      return;
    }
    if (!ocsPrompt.trim()) {
      setOcsError('Prompt instructions cannot be empty');
      return;
    }
    var f = ocsModalFlw;
    var today = new Date().toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });

    setOcsCreating(true);
    setOcsError('');
    actions
      .createTaskWithOCS({
        username: f.username,
        flw_name: f.display_name || f.username,
        title:
          'MBW Follow-up: ' + (f.display_name || f.username) + ' - ' + today,
        description: 'MBW Follow-up',
        priority: 'medium',
        ocs: {
          experiment: selectedBot,
          prompt_text: ocsPrompt,
        },
      })
      .then(function (result) {
        setOcsCreating(false);
        if (result.success) {
          setShowOcsModal(false);
          setCreatedTaskUsernames(function (prev) {
            return prev.concat([f.username]);
          });
          showToast(
            'Task created' +
              (result.ocs && result.ocs.success
                ? ' and AI session initiated'
                : '') +
              ' for ' +
              (f.display_name || f.username),
          );
          // Poll to link OCS session_id right after creation
          if (result.task_id && result.ocs && result.ocs.success) {
            var pollTaskId = result.task_id;
            var pollAttempt = 0;
            var maxAttempts = 5;
            var pollInterval = 2000;
            var doPoll = function () {
              pollAttempt++;
              actions.getAISessions(pollTaskId).then(function (sessResult) {
                if (sessResult && sessResult.sessions) {
                  var latest =
                    sessResult.sessions[sessResult.sessions.length - 1];
                  if (latest && latest.session_id) {
                    console.log('OCS session linked:', latest.session_id);
                    return;
                  }
                }
                if (pollAttempt < maxAttempts) {
                  setTimeout(doPoll, pollInterval);
                }
              });
            };
            setTimeout(doPoll, pollInterval);
          }
        } else {
          setOcsError(result.error || 'Failed to create task');
        }
      });
  };

  // ---- Inline task handlers ----
  var toggleTaskExpand = function (username) {
    if (expandedTaskFlw === username) {
      taskRequestIdRef.current++;
      setExpandedTaskFlw(null);
      setTaskDetail(null);
      setTaskTranscript(null);
      setShowCloseForm(false);
      return;
    }
    var taskInfo = openTasks[username];
    if (!taskInfo) return;
    var requestId = ++taskRequestIdRef.current;
    setExpandedTaskFlw(username);
    setTaskLoading(true);
    setTaskDetail(null);
    setTaskTranscript(null);
    setShowCloseForm(false);
    setCloseAction('none');
    setCloseNote('');
    if (!actions || !actions.getTaskDetail) {
      showToast(
        'Task detail not available — please hard-refresh (Cmd+Shift+R)',
      );
      setTaskLoading(false);
      return;
    }
    actions
      .getTaskDetail(taskInfo.task_id)
      .then(function (result) {
        if (requestId !== taskRequestIdRef.current) return;
        if (result.success && result.task) {
          setTaskDetail(result.task);
          setTaskStatus(result.task.status || 'investigating');
          setTaskOriginalStatus(result.task.status || 'investigating');
          return actions.getAISessions(taskInfo.task_id).then(function () {
            if (requestId !== taskRequestIdRef.current) return;
            return actions.getAITranscript(taskInfo.task_id);
          });
        } else {
          setTaskLoading(false);
          showToast(
            'Failed to load task: ' + (result.error || 'Unknown error'),
          );
        }
      })
      .then(function (transcriptResult) {
        if (requestId !== taskRequestIdRef.current) return;
        setTaskLoading(false);
        if (transcriptResult && transcriptResult.success) {
          setTaskTranscript(transcriptResult.messages || []);
        } else if (transcriptResult) {
          setTaskTranscript([]);
        }
      })
      .catch(function (err) {
        if (requestId !== taskRequestIdRef.current) return;
        setTaskLoading(false);
        console.error('Error loading task:', err);
      });
  };

  var handleTaskSave = function () {
    if (!taskDetail || taskStatus === taskOriginalStatus) return;
    var reqId = taskRequestIdRef.current;
    setTaskSaving(true);
    actions
      .updateTask(taskDetail.id, { status: taskStatus })
      .then(function (result) {
        if (reqId !== taskRequestIdRef.current) return;
        setTaskSaving(false);
        if (result.success) {
          setTaskOriginalStatus(taskStatus);
          showToast('Task status updated');
        } else {
          showToast('Failed to update: ' + (result.error || 'Unknown error'));
        }
      })
      .catch(function () {
        if (reqId !== taskRequestIdRef.current) return;
        setTaskSaving(false);
      });
  };

  var handleTaskClose = function () {
    if (!taskDetail) return;
    var reqId = taskRequestIdRef.current;
    setTaskSaving(true);
    actions
      .updateTask(taskDetail.id, {
        status: 'closed',
        resolution_details: {
          official_action: closeAction,
          resolution_note: closeNote,
        },
      })
      .then(function (result) {
        if (reqId !== taskRequestIdRef.current) return;
        setTaskSaving(false);
        if (result.success) {
          showToast('Task closed');
          // Remove from local open_tasks
          var newOpenTasks = Object.assign({}, openTasks);
          delete newOpenTasks[expandedTaskFlw];
          if (dashData) {
            setDashData(
              Object.assign({}, dashData, {
                open_tasks: newOpenTasks,
                open_task_usernames: Object.keys(newOpenTasks),
              }),
            );
          }
          setExpandedTaskFlw(null);
          setTaskDetail(null);
          setTaskTranscript(null);
          setShowCloseForm(false);
        } else {
          showToast('Failed to close: ' + (result.error || 'Unknown error'));
        }
      })
      .catch(function () {
        if (reqId !== taskRequestIdRef.current) return;
        setTaskSaving(false);
      });
  };

  var handleTaskRefreshTranscript = function () {
    if (!taskDetail) return;
    var requestId = ++taskRequestIdRef.current;
    setTaskLoading(true);
    actions
      .getAITranscript(taskDetail.id, undefined, true)
      .then(function (result) {
        if (requestId !== taskRequestIdRef.current) return;
        setTaskLoading(false);
        if (result.success) {
          setTaskTranscript(result.messages || []);
          showToast('Transcript refreshed');
        }
      })
      .catch(function () {
        if (requestId !== taskRequestIdRef.current) return;
        setTaskLoading(false);
      });
  };

  var TASK_STATUS_OPTIONS = [
    { value: 'investigating', label: 'Investigating', color: 'blue' },
    {
      value: 'flw_action_in_progress',
      label: 'FLW Action In Progress',
      color: 'yellow',
    },
    {
      value: 'flw_action_completed',
      label: 'FLW Action Completed',
      color: 'green',
    },
    { value: 'review_needed', label: 'Review Needed', color: 'purple' },
  ];

  // Apply filters
  var flwFilterSet = filterFlws.length > 0 ? filterFlws : null;
  var motherFilterSet = filterMothers.length > 0 ? filterMothers : null;

  var filteredOverview = overviewFlws.filter(function (f) {
    if (flwFilterSet && flwFilterSet.indexOf(f.username) < 0) return false;
    return true;
  });
  if (overviewSearch) {
    var sq = overviewSearch.toLowerCase();
    filteredOverview = filteredOverview.filter(function (f) {
      return (
        (f.display_name || '').toLowerCase().indexOf(sq) >= 0 ||
        (f.username || '').toLowerCase().indexOf(sq) >= 0
      );
    });
  }

  var filteredGpsFlws = gpsFlws.filter(function (f) {
    if (flwFilterSet && flwFilterSet.indexOf(f.username) < 0) return false;
    return true;
  });

  // Compute dist_ratio for GPS and Overview (revisit_distance_km * 1000 / median_meters_per_visit)
  filteredGpsFlws = filteredGpsFlws.map(function (g) {
    return Object.assign({}, g, {
      dist_ratio:
        g.avg_case_distance_km != null &&
        g.median_meters_per_visit != null &&
        g.median_meters_per_visit > 0
          ? Math.round(
              ((g.avg_case_distance_km * 1000) / g.median_meters_per_visit) *
                10,
            ) / 10
          : null,
    });
  });
  filteredOverview = filteredOverview.map(function (f) {
    return Object.assign({}, f, {
      dist_ratio:
        f.revisit_distance_km != null &&
        f.median_meters_per_visit != null &&
        f.median_meters_per_visit > 0
          ? Math.round(
              ((f.revisit_distance_km * 1000) / f.median_meters_per_visit) * 10,
            ) / 10
          : null,
    });
  });

  var filteredFuFlws = fuFlws.filter(function (f) {
    if (flwFilterSet && flwFilterSet.indexOf(f.username) < 0) return false;
    if (motherFilterSet) {
      var mothers = fuDrilldown[f.username] || [];
      return mothers.some(function (m) {
        return motherFilterSet.indexOf(m.mother_case_id) >= 0;
      });
    }
    return true;
  });

  var sortedOverview = sortRows(filteredOverview, overviewSort);
  var sortedGps = sortRows(filteredGpsFlws, gpsSort);
  var sortedFu = sortRows(filteredFuFlws, fuSort);

  // Compute overall follow-up rate
  var overallFuRate = 0;
  if (filteredFuFlws.length > 0) {
    var totalRate = 0;
    filteredFuFlws.forEach(function (f) {
      totalRate += f.completion_rate || 0;
    });
    overallFuRate = Math.round(totalRate / filteredFuFlws.length);
  }

  // Count FLWs by result for complete modal
  var countByResult = function (resultVal) {
    var count = 0;
    sessionFlws.forEach(function (u) {
      var wr = workerResults[u] || {};
      if (resultVal === null) {
        if (!wr.result) count++;
      } else {
        if (wr.result === resultVal) count++;
      }
    });
    return count;
  };

  // Table header helper
  var Th = function (props) {
    return (
      <th
        className={
          'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:bg-gray-100 whitespace-nowrap ' +
          (props.className || '')
        }
        onClick={props.onClick}
        title={props.tooltip || ''}
      >
        {props.children}
        {props.sortIndicator || ''}
      </th>
    );
  };

  // Non-sortable header helper
  var ThStatic = function (props) {
    return (
      <th
        className={
          'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap ' +
          (props.className || '')
        }
        title={props.tooltip || ''}
      >
        {props.children}
      </th>
    );
  };

  // FLW Notes Modal
  var FlwNotesModal = function () {
    if (!showFlwNotesModal) return null;
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
        <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full">
          <div className="px-4 pt-5 pb-4 sm:p-6">
            <h3 className="text-lg font-medium text-gray-900 mb-3">
              Notes for {flwNames[flwNotesUsername] || flwNotesUsername}
            </h3>
            <textarea
              value={flwNotesText}
              onChange={function (e) {
                setFlwNotesText(e.target.value);
              }}
              rows={4}
              placeholder="Add notes about this FLW's assessment..."
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-indigo-500 focus:border-indigo-500 text-sm"
            />
            <div className="mt-3 flex items-center gap-2">
              <span className="text-sm text-gray-600">Result:</span>
              <button
                onClick={function () {
                  setFlwNotesResult('eligible_for_renewal');
                }}
                className={
                  'px-3 py-1 rounded text-xs font-medium border transition-colors ' +
                  (flwNotesResult === 'eligible_for_renewal'
                    ? 'bg-green-600 text-white border-green-600'
                    : 'bg-green-50 text-green-800 hover:bg-green-100 border-green-300')
                }
              >
                <i className="fa-solid fa-circle-check mr-1"></i> Eligible
              </button>
              <button
                onClick={function () {
                  setFlwNotesResult('probation');
                }}
                className={
                  'px-3 py-1 rounded text-xs font-medium border transition-colors ' +
                  (flwNotesResult === 'probation'
                    ? 'bg-amber-600 text-white border-amber-600'
                    : 'bg-amber-50 text-amber-800 hover:bg-amber-100 border-amber-300')
                }
              >
                <i className="fa-solid fa-triangle-exclamation mr-1"></i>{' '}
                Probation
              </button>
              <button
                onClick={function () {
                  setFlwNotesResult('suspended');
                }}
                className={
                  'px-3 py-1 rounded text-xs font-medium border transition-colors ' +
                  (flwNotesResult === 'suspended'
                    ? 'bg-red-600 text-white border-red-600'
                    : 'bg-red-50 text-red-800 hover:bg-red-100 border-red-300')
                }
              >
                <i className="fa-solid fa-ban mr-1"></i> Suspended
              </button>
              {flwNotesResult && (
                <button
                  onClick={function () {
                    setFlwNotesResult(null);
                  }}
                  className="px-3 py-1 rounded text-xs text-gray-600 hover:bg-gray-100 border border-gray-300"
                >
                  Clear
                </button>
              )}
            </div>
          </div>
          <div className="bg-gray-50 px-4 py-3 sm:flex sm:flex-row-reverse rounded-b-lg">
            <button
              onClick={saveFlwNotes}
              className="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-indigo-600 text-base font-medium text-white hover:bg-indigo-700 sm:ml-3 sm:w-auto sm:text-sm"
            >
              Save
            </button>
            <button
              onClick={function () {
                setShowFlwNotesModal(false);
              }}
              className="mt-3 w-full inline-flex justify-center rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-gray-700 hover:bg-gray-50 sm:mt-0 sm:w-auto sm:text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  };

  // Complete modal
  var CompleteModal = function () {
    if (!showCompleteModal) return null;
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
        <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full">
          <div className="px-4 pt-5 pb-4 sm:p-6">
            <h3 className="text-lg font-medium text-gray-900 mb-3">
              Complete Monitoring Audit
            </h3>
            <p className="text-sm text-gray-600 mb-4">
              {assessedCount} of {totalFlws} FLWs have been assessed.
            </p>
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Assessment Summary
              </label>
              <div className="space-y-1.5 text-sm">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-green-500 inline-block"></span>
                  <span className="text-gray-700">Eligible for Renewal:</span>
                  <span className="font-medium">
                    {countByResult('eligible_for_renewal')}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block"></span>
                  <span className="text-gray-700">Probation:</span>
                  <span className="font-medium">
                    {countByResult('probation')}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-red-500 inline-block"></span>
                  <span className="text-gray-700">Suspended:</span>
                  <span className="font-medium">
                    {countByResult('suspended')}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-gray-400 inline-block"></span>
                  <span className="text-gray-700">Not assessed:</span>
                  <span className="font-medium">{countByResult(null)}</span>
                </div>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Notes
              </label>
              <textarea
                value={completeNotes}
                onChange={function (e) {
                  setCompleteNotes(e.target.value);
                }}
                rows={3}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-indigo-500 focus:border-indigo-500 text-sm"
                placeholder="Overall monitoring notes..."
              />
            </div>
          </div>
          <div className="bg-gray-50 px-4 py-3 sm:flex sm:flex-row-reverse rounded-b-lg">
            <button
              onClick={handleComplete}
              disabled={completing}
              className="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-indigo-600 text-base font-medium text-white hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed sm:ml-3 sm:w-auto sm:text-sm"
            >
              {completing ? 'Completing...' : 'Complete Audit'}
            </button>
            <button
              onClick={function () {
                setShowCompleteModal(false);
              }}
              className="mt-3 w-full inline-flex justify-center rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-gray-700 hover:bg-gray-50 sm:mt-0 sm:w-auto sm:text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  };

  // Helper: get visible mothers for follow-up drilldown
  var getVisibleMothers = function (mothers) {
    if (!mothers) return [];
    var filtered = mothers;
    if (showEligibleOnly) {
      filtered = filtered.filter(function (m) {
        return m.eligible;
      });
    }
    if (motherFilterSet) {
      filtered = filtered.filter(function (m) {
        return motherFilterSet.indexOf(m.mother_case_id) >= 0;
      });
    }
    return filtered;
  };

  // Helper: get visible visits for a mother
  var getVisibleVisits = function (mother) {
    if (!mother || !mother.visits) return [];
    if (showAllVisits) return mother.visits;
    return mother.visits.filter(function (v) {
      return v.status && v.status.indexOf('Due') >= 0;
    });
  };

  // Helper: get GPS trailing 7 days max
  var getMaxDailyTravel = function (flw) {
    if (!flw.trailing_7_days || flw.trailing_7_days.length === 0) return 1;
    var maxVal = 0;
    flw.trailing_7_days.forEach(function (d) {
      if (d.distance_km > maxVal) maxVal = d.distance_km;
    });
    return maxVal || 1;
  };

  // Per-visit-type column keys
  var visitTypes = ['anc', 'postnatal', 'week1', 'month1', 'month3', 'month6'];
  var visitTypeLabels = {
    anc: 'ANC',
    postnatal: 'Postnatal',
    week1: 'Week 1',
    month1: 'Month 1',
    month3: 'Month 3',
    month6: 'Month 6',
  };

  return (
    <div className="space-y-4">
      {FlwNotesModal()}
      {CompleteModal()}

      {/* OCS Task + AI Assistant Modal */}
      {showOcsModal && ocsModalFlw && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          onClick={function () {
            if (!ocsCreating) setShowOcsModal(false);
          }}
        >
          <div
            className="absolute inset-0 bg-black bg-opacity-50"
            style={{
              backdropFilter: 'blur(4px)',
              WebkitBackdropFilter: 'blur(4px)',
            }}
          ></div>
          <div
            className="relative bg-white rounded-xl shadow-2xl max-w-lg w-full mx-4 overflow-hidden"
            onClick={function (e) {
              e.stopPropagation();
            }}
          >
            {/* FLW Header */}
            <div className="bg-gradient-to-r from-purple-600 to-purple-500 px-6 py-4 flex items-center justify-between">
              <div className="flex items-center">
                <div className="w-10 h-10 rounded-full bg-white bg-opacity-20 flex items-center justify-center text-white font-bold mr-3">
                  {(ocsModalFlw.display_name || ocsModalFlw.username || '')
                    .charAt(0)
                    .toUpperCase()}
                </div>
                <div>
                  <div className="text-white font-semibold">
                    {ocsModalFlw.display_name || ocsModalFlw.username}
                  </div>
                  <div className="text-purple-200 text-xs">
                    {ocsModalFlw.username}
                  </div>
                </div>
              </div>
              <button
                onClick={function () {
                  if (!ocsCreating) setShowOcsModal(false);
                }}
                className="text-white text-opacity-70 hover:text-opacity-100 transition-colors"
                disabled={ocsCreating}
              >
                <i className="fa-solid fa-times text-lg"></i>
              </button>
            </div>

            {/* Modal Body */}
            <div className="px-6 py-5 space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-900">
                  Create Task & Initiate AI
                </h3>
                <p className="text-sm text-gray-500 mt-1">
                  Configure and initiate an AI assistant conversation for this
                  FLW.
                </p>
              </div>

              {/* Bot Selector */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Bot <span className="text-red-500">*</span>
                </label>
                {ocsLoading ? (
                  <div className="flex items-center text-sm text-gray-500 py-2">
                    <i className="fa-solid fa-spinner fa-spin mr-2"></i> Loading
                    bots...
                  </div>
                ) : (
                  <select
                    value={selectedBot}
                    onChange={function (e) {
                      setSelectedBot(e.target.value);
                      setOcsError('');
                    }}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:ring-purple-500 focus:border-purple-500"
                  >
                    <option value="">-- Select a bot --</option>
                    {ocsBots.map(function (bot) {
                      return (
                        <option key={bot.id} value={bot.id}>
                          {bot.name}
                          {bot.version ? ' (v' + bot.version + ')' : ''}
                        </option>
                      );
                    })}
                  </select>
                )}
              </div>

              {/* Prompt Instructions */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Prompt Instructions <span className="text-red-500">*</span>
                </label>
                <textarea
                  value={ocsPrompt}
                  onChange={function (e) {
                    setOcsPrompt(e.target.value);
                    setOcsError('');
                  }}
                  rows={16}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:ring-purple-500 focus:border-purple-500 font-mono"
                  placeholder="Instructions for the bot..."
                />
                <p className="text-xs text-gray-400 mt-1">
                  Auto-populated from dashboard data. You can edit before
                  sending.
                </p>
              </div>

              {/* Error Display */}
              {ocsError && (
                <div className="bg-red-50 border border-red-200 rounded-md px-3 py-2 text-sm text-red-700">
                  <i className="fa-solid fa-circle-exclamation mr-1"></i>{' '}
                  {ocsError}
                </div>
              )}
            </div>

            {/* Footer Buttons */}
            <div className="bg-gray-50 px-6 py-4 flex justify-end gap-3 border-t border-gray-200">
              <button
                onClick={function () {
                  setShowOcsModal(false);
                }}
                disabled={ocsCreating}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateTaskWithOCS}
                disabled={ocsCreating || ocsLoading || !selectedBot}
                className="inline-flex items-center px-4 py-2 text-sm font-medium text-white bg-green-600 border border-transparent rounded-md hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {ocsCreating ? (
                  <span>
                    <i className="fa-solid fa-spinner fa-spin mr-2"></i>{' '}
                    Creating...
                  </span>
                ) : (
                  <span>
                    <i className="fa-solid fa-robot mr-2"></i> Create Task &
                    Initiate AI
                  </span>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast Notification */}
      {toastMessage && (
        <div className="fixed bottom-4 right-4 z-50 bg-gray-900 text-white px-4 py-3 rounded-lg shadow-lg text-sm">
          {toastMessage}
        </div>
      )}

      {/* Monitoring Session Header */}
      <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4 mb-4">
        <div className="flex justify-between items-center">
          <div>
            <h2 className="font-semibold text-indigo-900">
              {instance.state?.title || definition.name}
            </h2>
            <p className="text-sm text-indigo-700 mt-1">
              Progress: <span className="font-medium">{assessedCount}</span> /{' '}
              <span className="font-medium">{totalFlws}</span> FLWs assessed
            </p>
          </div>
          <div className="flex gap-2">
            <a
              href="/labs/workflow/"
              className="inline-flex items-center px-3 py-1.5 text-sm text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
            >
              <i className="fa-solid fa-arrow-left mr-1"></i> Back to Workflows
            </a>
            {isCompleted ? (
              <span className="inline-flex items-center px-3 py-1.5 text-sm text-green-700 bg-green-50 border border-green-200 rounded-md">
                <i className="fa-solid fa-check-circle mr-1"></i> Completed
              </span>
            ) : (
              <button
                onClick={function () {
                  setShowCompleteModal(true);
                }}
                className={
                  'inline-flex items-center px-3 py-1.5 text-sm text-white rounded-md ' +
                  (false
                    ? 'bg-indigo-300 cursor-not-allowed'
                    : 'bg-indigo-600 hover:bg-indigo-700')
                }
              >
                <i className="fa-solid fa-check mr-1"></i> Complete Audit
              </button>
            )}
          </div>
        </div>
        {/* Progress bar */}
        <div className="mt-3 bg-indigo-100 rounded-full h-2">
          <div
            className="bg-indigo-600 h-2 rounded-full transition-all"
            style={{ width: progressPct + '%' }}
          ></div>
        </div>
        {dashData && (
          <div className="mt-2 text-xs text-gray-400">
            Data loaded via pipeline analysis
          </div>
        )}
      </div>

      {/* Tab Navigation */}
      <div className="border-b border-gray-200 mb-4 flex items-end">
        <nav className="-mb-px flex space-x-6">
          {[
            { id: 'overview', label: 'Overview', icon: 'fa-chart-line' },
            { id: 'gps', label: 'GPS Analysis', icon: 'fa-location-dot' },
            {
              id: 'followup',
              label: 'Follow-Up Rate',
              icon: 'fa-clipboard-check',
            },
            {
              id: 'performance',
              label: 'FLW Performance',
              icon: 'fa-ranking-star',
            },
            { id: 'guide', label: 'Guide', icon: 'fa-book' },
          ].map(function (t) {
            var active = activeTab === t.id;
            return (
              <button
                key={t.id}
                onClick={function () {
                  setActiveTab(t.id);
                }}
                className={
                  'whitespace-nowrap py-3 px-1 border-b-2 font-medium text-sm transition-colors ' +
                  (active
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300')
                }
              >
                <i className={'fa-solid ' + t.icon + ' mr-1'}></i> {t.label}
              </button>
            );
          })}
        </nav>
        <div className="flex items-center gap-3 ml-auto">
          <button
            onClick={function () {
              setDashData(null);
              setAnalysisComplete(false);
              setJobMessages([]);
              setJobError(null);
            }}
            disabled={jobRunning}
            className={
              'inline-flex items-center gap-1 px-3 py-1.5 text-sm font-medium rounded-md border transition-colors ' +
              (analysisComplete && !jobRunning
                ? 'text-blue-700 bg-blue-50 border-blue-200 hover:bg-blue-100'
                : 'text-gray-400 bg-gray-50 border-gray-200 cursor-not-allowed')
            }
          >
            {'↻'} Re-run Analysis
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm mb-4">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Start Date <span className="text-gray-400">(GPS only)</span>
            </label>
            <input
              type="date"
              value={filterStartDate}
              onChange={function (e) {
                setFilterStartDate(e.target.value);
              }}
              className="border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:ring-blue-500 focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              End Date <span className="text-gray-400">(GPS only)</span>
            </label>
            <input
              type="date"
              value={filterEndDate}
              onChange={function (e) {
                setFilterEndDate(e.target.value);
              }}
              className="border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:ring-blue-500 focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              App Version <span className="text-gray-400">(GPS only)</span>
            </label>
            <div className="flex gap-1">
              <select
                value={appVersionOp}
                onChange={function (e) {
                  setAppVersionOp(e.target.value);
                }}
                className="border border-gray-300 rounded-md px-2 py-1.5 text-sm focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="">No filter</option>
                <option value="gt">{'>'}</option>
                <option value="gte">{'>='}</option>
                <option value="eq">{'='}</option>
                <option value="lte">{'<='}</option>
                <option value="lt">{'<'}</option>
              </select>
              <input
                type="number"
                value={appVersionVal}
                onChange={function (e) {
                  setAppVersionVal(e.target.value);
                }}
                placeholder="#"
                disabled={!appVersionOp}
                className="border border-gray-300 rounded-md px-2 py-1.5 text-sm focus:ring-blue-500 focus:border-blue-500 w-16"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Visit Status
            </label>
            <div className="flex gap-1 flex-wrap">
              {[
                { value: 'approved', label: 'Approved' },
                { value: 'pending', label: 'Pending' },
                { value: 'rejected', label: 'Rejected' },
                { value: 'over_limit', label: 'Over Limit' },
              ].map(function (opt) {
                var isActive = statusFilter.indexOf(opt.value) !== -1;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    aria-pressed={isActive}
                    onClick={function () {
                      setStatusFilter(function (prev) {
                        if (prev.indexOf(opt.value) !== -1) {
                          var next = prev.filter(function (v) {
                            return v !== opt.value;
                          });
                          return next.length > 0 ? next : prev;
                        }
                        return prev.concat([opt.value]);
                      });
                    }}
                    className={
                      'px-2.5 py-1 text-xs font-medium rounded-full border transition-colors cursor-pointer ' +
                      (isActive
                        ? 'bg-blue-100 text-blue-800 border-blue-300'
                        : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50')
                    }
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Filter by FLW
            </label>
            <select
              multiple
              value={filterFlws}
              onChange={function (e) {
                var opts = e.target.options;
                var vals = [];
                for (var i = 0; i < opts.length; i++) {
                  if (opts[i].selected) vals.push(opts[i].value);
                }
                setFilterFlws(vals);
              }}
              className="border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:ring-blue-500 focus:border-blue-500 w-full"
              style={{ minHeight: '34px', maxHeight: '80px' }}
            >
              {activeUsernames.map(function (u) {
                return (
                  <option key={u} value={u}>
                    {flwNames[u] || u}
                  </option>
                );
              })}
            </select>
          </div>
          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Filter by Mother
            </label>
            <select
              multiple
              value={filterMothers}
              onChange={function (e) {
                var opts = e.target.options;
                var vals = [];
                for (var i = 0; i < opts.length; i++) {
                  if (opts[i].selected) vals.push(opts[i].value);
                }
                setFilterMothers(vals);
              }}
              className="border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:ring-blue-500 focus:border-blue-500 w-full"
              style={{ minHeight: '34px', maxHeight: '80px' }}
            >
              {allMotherIds.map(function (m) {
                return (
                  <option key={m} value={m}>
                    {motherNamesMap[m] || m}
                  </option>
                );
              })}
            </select>
          </div>
          <button
            onClick={function () {
              var opChanged = appVersionOp !== appliedAppVersionOp;
              var valChanged = appVersionVal !== appliedAppVersionVal;
              var statusChanged =
                JSON.stringify(statusFilter.slice().sort()) !==
                JSON.stringify(appliedStatusFilter.slice().sort());
              if (opChanged || valChanged || statusChanged) {
                try {
                  sessionStorage.setItem(
                    _statusFilterKey,
                    JSON.stringify(statusFilter),
                  );
                } catch (e) {}
                setAppliedAppVersionOp(appVersionOp);
                setAppliedAppVersionVal(appVersionVal);
                setAppliedStatusFilter(statusFilter);

                setDashData(null);
              }
            }}
            className="inline-flex items-center px-4 py-1.5 text-sm text-white bg-blue-600 rounded-md hover:bg-blue-700"
          >
            <i className="fa-solid fa-filter mr-1"></i> Apply
          </button>
          <button
            onClick={resetFilters}
            className="inline-flex items-center px-4 py-1.5 text-sm text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Reset
          </button>
        </div>
      </div>

      {/* ============================================================ */}
      {/* OVERVIEW TAB */}
      {/* ============================================================ */}
      {activeTab === 'overview' && (
        <div>
          {/* FLW Overview Table */}
          <div className="bg-white border border-gray-200 rounded-lg shadow-sm">
            <div className="px-6 py-3 border-b border-gray-200 bg-gray-50">
              <div className="flex items-center gap-3">
                <h2 className="text-lg font-semibold text-gray-900">
                  FLW Overview{' '}
                  <span className="text-sm text-gray-600 font-normal">
                    ({filteredOverview.length} FLWs)
                  </span>
                </h2>
                <div style={{ position: 'relative' }}>
                  <button
                    onClick={function () {
                      setShowColPicker(!showColPicker);
                    }}
                    className="inline-flex items-center px-3 py-1.5 border border-gray-300 rounded-md text-sm text-gray-700 bg-white hover:bg-gray-50"
                  >
                    <i className="fa-solid fa-table-columns mr-2"></i>
                    Columns
                    <span className="ml-1.5 bg-gray-100 text-gray-600 text-xs px-1.5 py-0.5 rounded-full">
                      {visibleCols.length - 2}/{OVERVIEW_COLUMNS.length - 2}
                    </span>
                  </button>
                  {showColPicker && (
                    <div
                      style={{ position: 'fixed', inset: 0, zIndex: 40 }}
                      onClick={function () {
                        setShowColPicker(false);
                      }}
                    ></div>
                  )}
                  {showColPicker && (
                    <div
                      style={{
                        position: 'absolute',
                        left: 0,
                        top: '100%',
                        marginTop: '4px',
                        zIndex: 50,
                        width: '220px',
                        backgroundColor: 'white',
                        border: '1px solid #e5e7eb',
                        borderRadius: '8px',
                        boxShadow: '0 10px 15px -3px rgba(0,0,0,0.1)',
                      }}
                    >
                      <div className="px-3 py-2 border-b border-gray-200">
                        <span className="text-xs font-medium text-gray-500 uppercase">
                          Toggle Columns
                        </span>
                      </div>
                      <div
                        style={{ maxHeight: '300px', overflowY: 'auto' }}
                        className="py-1"
                      >
                        {OVERVIEW_COLUMNS.map(function (col) {
                          return (
                            <label
                              key={col.id}
                              className={
                                'flex items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50' +
                                (col.locked ? ' opacity-50 cursor-default' : '')
                              }
                              style={
                                col.locked ? { pointerEvents: 'none' } : {}
                              }
                            >
                              <input
                                type="checkbox"
                                checked={isColVisible(col.id)}
                                disabled={!!col.locked}
                                onChange={function () {
                                  toggleCol(col.id);
                                }}
                                className="mr-2 rounded border-gray-300"
                                style={{ accentColor: '#2563eb' }}
                              />
                              {col.label}
                            </label>
                          );
                        })}
                      </div>
                      <div className="px-3 py-2 border-t border-gray-200 flex gap-3">
                        <button
                          onClick={function () {
                            setVisibleCols(
                              OVERVIEW_COLUMNS.map(function (c) {
                                return c.id;
                              }),
                            );
                          }}
                          className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                        >
                          Show All
                        </button>
                        <button
                          onClick={function () {
                            setVisibleCols(
                              OVERVIEW_COLUMNS.filter(function (c) {
                                return c.locked;
                              }).map(function (c) {
                                return c.id;
                              }),
                            );
                          }}
                          className="text-xs text-gray-600 hover:text-gray-800 font-medium"
                        >
                          Minimal
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
            <div
              style={{
                width: 0,
                minWidth: '100%',
                overflowX: 'auto',
                WebkitOverflowScrolling: 'touch',
              }}
            >
              <table
                data-sticky-header
                className="divide-y divide-gray-200"
                style={{ width: 'max-content', minWidth: '100%' }}
              >
                <thead className="bg-gray-50">
                  <tr>
                    {isColVisible('flw_name') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'display_name',
                          );
                        }}
                        sortIndicator={sortIcon(overviewSort, 'display_name')}
                        tooltip="Frontline worker name and ID"
                      >
                        FLW Name
                      </Th>
                    )}
                    {isColVisible('last_active') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'last_active_days',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'last_active_days',
                        )}
                        tooltip="Days since FLW was last active on Connect"
                      >
                        Last Active
                      </Th>
                    )}
                    {isColVisible('mothers') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'cases_registered',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'cases_registered',
                        )}
                        tooltip="Unique mothers from CCHQ registration forms. Eligible count in parentheses."
                      >
                        # Mothers
                      </Th>
                    )}
                    {isColVisible('gs_score') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'first_gs_score',
                          );
                        }}
                        sortIndicator={sortIcon(overviewSort, 'first_gs_score')}
                        tooltip="Oldest Gold Standard Visit Checklist score"
                      >
                        GS Score
                      </Th>
                    )}
                    {isColVisible('post_test') && (
                      <ThStatic tooltip="Post-test attempts - TBD">
                        Post-Test
                      </ThStatic>
                    )}
                    {isColVisible('followup_rate') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'followup_rate',
                          );
                        }}
                        sortIndicator={sortIcon(overviewSort, 'followup_rate')}
                        tooltip="Completed / total visits due 5+ days ago"
                      >
                        Follow-up Rate
                      </Th>
                    )}
                    {isColVisible('eligible_5') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'cases_still_eligible.pct',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'cases_still_eligible.pct',
                        )}
                        tooltip="Eligible mothers still on track"
                      >
                        Eligible 5+
                      </Th>
                    )}
                    {isColVisible('ebf_pct') && (
                      <Th
                        onClick={function () {
                          toggleSort(setOverviewSort, overviewSort, 'ebf_pct');
                        }}
                        sortIndicator={sortIcon(overviewSort, 'ebf_pct')}
                        tooltip="% of FLW's postnatal visits reporting exclusive breastfeeding (EBF)"
                      >
                        % EBF
                      </Th>
                    )}
                    {isColVisible('revisit_dist') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'revisit_distance_km',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'revisit_distance_km',
                        )}
                        tooltip="Median haversine distance (km) between successive GPS"
                      >
                        Revisit Dist.
                      </Th>
                    )}
                    {isColVisible('meter_visit') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'median_meters_per_visit',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'median_meters_per_visit',
                        )}
                        tooltip="Median haversine distance (m) between consecutive visits"
                      >
                        Meter/Visit
                      </Th>
                    )}
                    {isColVisible('dist_ratio') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'dist_ratio',
                          );
                        }}
                        sortIndicator={sortIcon(overviewSort, 'dist_ratio')}
                        tooltip="Revisit distance / meter per visit. Higher values may indicate suspicious patterns."
                      >
                        Dist. Ratio
                      </Th>
                    )}
                    {isColVisible('minute_visit') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'median_minutes_per_visit',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'median_minutes_per_visit',
                        )}
                        tooltip="Median time gap (min) between consecutive form submissions"
                      >
                        Minute/Visit
                      </Th>
                    )}
                    {isColVisible('phone_dup') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'phone_dup_pct',
                          );
                        }}
                        sortIndicator={sortIcon(overviewSort, 'phone_dup_pct')}
                        tooltip="% of FLW's mothers sharing a phone number"
                      >
                        Phone Dup %
                      </Th>
                    )}
                    {isColVisible('anc_pnc') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'anc_pnc_same_date_count',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'anc_pnc_same_date_count',
                        )}
                        tooltip="Count of mothers where ANC and PNC same date"
                      >
                        {'ANC = PNC'}
                      </Th>
                    )}
                    {isColVisible('parity') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'parity_concentration.pct_duplicate',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'parity_concentration.pct_duplicate',
                        )}
                        tooltip="% of FLW's mothers with duplicate parity value"
                      >
                        Parity
                      </Th>
                    )}
                    {isColVisible('age') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'age_concentration.pct_duplicate',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'age_concentration.pct_duplicate',
                        )}
                        tooltip="% of FLW's mothers with duplicate age value"
                      >
                        Age
                      </Th>
                    )}
                    {isColVisible('age_reg') && (
                      <Th
                        onClick={function () {
                          toggleSort(
                            setOverviewSort,
                            overviewSort,
                            'age_equals_reg_pct',
                          );
                        }}
                        sortIndicator={sortIcon(
                          overviewSort,
                          'age_equals_reg_pct',
                        )}
                        tooltip="% of mothers whose DOB month+day matches registration date"
                      >
                        {'Age = Reg'}
                      </Th>
                    )}
                    {isColVisible('actions') && (
                      <ThStatic className="text-right">Actions</ThStatic>
                    )}
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {sortedOverview.map(function (f) {
                    var wr = workerResults[f.username] || {};
                    var cse = f.cases_still_eligible || {};
                    var hasNotes = wr.notes && wr.notes.length > 0;
                    return (
                      <React.Fragment key={f.username}>
                        <tr className="hover:bg-gray-50">
                          {/* FLW Name */}
                          {isColVisible('flw_name') && (
                            <td className="px-4 py-3 text-sm">
                              <div className="flex items-center">
                                <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-xs font-bold mr-2">
                                  {(f.display_name || f.username || '')
                                    .charAt(0)
                                    .toUpperCase()}
                                </div>
                                <div>
                                  <div className="font-medium text-gray-900">
                                    {f.display_name || f.username}
                                  </div>
                                  {f.display_name !== f.username && (
                                    <div className="text-xs text-gray-500">
                                      {f.username}
                                    </div>
                                  )}
                                </div>
                              </div>
                            </td>
                          )}
                          {/* Last Active */}
                          {isColVisible('last_active') && (
                            <td
                              className="px-4 py-3 text-sm"
                              title={f.last_active_date || ''}
                            >
                              {f.last_active_days != null ? (
                                <span
                                  className={
                                    f.last_active_days <= 7
                                      ? 'text-green-600 font-medium'
                                      : f.last_active_days <= 15
                                      ? 'text-yellow-600'
                                      : 'text-red-600'
                                  }
                                >
                                  {f.last_active_days + 'd ago'}
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* # Mothers */}
                          {isColVisible('mothers') && (
                            <td className="px-4 py-3 text-sm text-gray-900">
                              {f.cases_registered || 0}
                              <span className="text-xs text-gray-400 ml-1">
                                ({f.eligible_mothers || 0} eligible)
                              </span>
                            </td>
                          )}
                          {/* GS Score */}
                          {isColVisible('gs_score') && (
                            <td className="px-4 py-3 text-sm">
                              {f.first_gs_score != null ? (
                                <span
                                  className={
                                    Number(f.first_gs_score) >= 70
                                      ? 'text-green-600 font-medium'
                                      : Number(f.first_gs_score) >= 50
                                      ? 'text-yellow-600'
                                      : 'text-red-600'
                                  }
                                >
                                  {f.first_gs_score}%
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Post-Test */}
                          {isColVisible('post_test') && (
                            <td className="px-4 py-3 text-sm text-gray-400">
                              {'—'}
                            </td>
                          )}
                          {/* Follow-up Rate */}
                          {isColVisible('followup_rate') && (
                            <td className="px-4 py-3 text-sm">
                              <div className="flex items-center">
                                <div className="w-16 bg-gray-200 rounded-full h-2 mr-2">
                                  <div
                                    className={
                                      'h-2 rounded-full transition-all ' +
                                      ((f.followup_rate || 0) >= 75
                                        ? 'bg-green-500'
                                        : (f.followup_rate || 0) >= 50
                                        ? 'bg-yellow-500'
                                        : 'bg-red-500')
                                    }
                                    style={{
                                      width:
                                        Math.min(100, f.followup_rate || 0) +
                                        '%',
                                    }}
                                  ></div>
                                </div>
                                <span
                                  className={
                                    'font-bold text-xs ' +
                                    ((f.followup_rate || 0) >= 75
                                      ? 'text-green-600'
                                      : (f.followup_rate || 0) >= 50
                                      ? 'text-yellow-600'
                                      : 'text-red-600')
                                  }
                                >
                                  {f.followup_rate != null
                                    ? f.followup_rate + '%'
                                    : '—'}
                                </span>
                              </div>
                            </td>
                          )}
                          {/* Eligible 5+ */}
                          {isColVisible('eligible_5') && (
                            <td className="px-4 py-3 text-sm">
                              {cse.total > 0 ? (
                                <span
                                  className={
                                    getEligibleColor(cse.pct) === 'green'
                                      ? 'text-green-600 font-medium'
                                      : getEligibleColor(cse.pct) === 'yellow'
                                      ? 'text-yellow-600'
                                      : 'text-red-600'
                                  }
                                >
                                  {cse.eligible}/{cse.total} ({cse.pct}%)
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* % EBF */}
                          {isColVisible('ebf_pct') && (
                            <td className="px-4 py-3 text-sm">
                              {f.ebf_pct != null ? (
                                <span
                                  className={
                                    f.ebf_pct >= 50 && f.ebf_pct <= 85
                                      ? 'text-green-600 font-medium'
                                      : (f.ebf_pct >= 31 && f.ebf_pct < 50) ||
                                        (f.ebf_pct > 85 && f.ebf_pct <= 95)
                                      ? 'text-yellow-600'
                                      : 'text-red-600'
                                  }
                                >
                                  {f.ebf_pct}%
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Revisit Dist */}
                          {isColVisible('revisit_dist') && (
                            <td className="px-4 py-3 text-sm text-gray-900">
                              {f.revisit_distance_km != null ? (
                                <span>
                                  {f.revisit_distance_km + ' km'}{' '}
                                  <span className="text-gray-500">
                                    ({f.cases_with_revisits || 0})
                                  </span>
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Meter/Visit */}
                          {isColVisible('meter_visit') && (
                            <td className="px-4 py-3 text-sm">
                              {f.median_meters_per_visit != null ? (
                                <span
                                  className={
                                    f.median_meters_per_visit >= 1000
                                      ? 'text-green-600 font-medium'
                                      : f.median_meters_per_visit >= 100
                                      ? 'text-yellow-600'
                                      : 'text-red-600'
                                  }
                                >
                                  {f.median_meters_per_visit + ' m'}
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Dist. Ratio */}
                          {isColVisible('dist_ratio') && (
                            <td className="px-4 py-3 text-sm text-gray-900">
                              {f.dist_ratio != null ? (
                                f.dist_ratio
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Minute/Visit */}
                          {isColVisible('minute_visit') && (
                            <td className="px-4 py-3 text-sm text-gray-900">
                              {f.median_minutes_per_visit != null ? (
                                f.median_minutes_per_visit + ' min'
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Phone Dup % */}
                          {isColVisible('phone_dup') && (
                            <td className="px-4 py-3 text-sm">
                              {f.phone_dup_pct != null ? (
                                <span
                                  className={
                                    f.phone_dup_pct <= 10
                                      ? 'text-green-600 font-medium'
                                      : f.phone_dup_pct <= 30
                                      ? 'text-yellow-600'
                                      : 'text-red-600'
                                  }
                                >
                                  {f.phone_dup_pct}%
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* ANC != PNC */}
                          {isColVisible('anc_pnc') && (
                            <td className="px-4 py-3 text-sm">
                              {f.anc_pnc_same_date_count != null ? (
                                <span
                                  className={
                                    f.anc_pnc_same_date_count <= 1
                                      ? 'text-green-600 font-medium'
                                      : f.anc_pnc_same_date_count < 5
                                      ? 'text-yellow-600'
                                      : 'text-red-600'
                                  }
                                >
                                  {f.anc_pnc_same_date_count}
                                </span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Parity */}
                          {isColVisible('parity') && (
                            <td className="px-4 py-3 text-sm text-gray-900">
                              {f.parity_concentration ? (
                                <div>
                                  <span>
                                    {f.parity_concentration.pct_duplicate}%
                                  </span>
                                  <div className="text-xs text-gray-400">
                                    {f.parity_concentration.mode_pct}% /{' '}
                                    {f.parity_concentration.mode_value}
                                  </div>
                                </div>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Age */}
                          {isColVisible('age') && (
                            <td className="px-4 py-3 text-sm text-gray-900">
                              {f.age_concentration ? (
                                <div>
                                  <span>
                                    {f.age_concentration.pct_duplicate}%
                                  </span>
                                  <div className="text-xs text-gray-400">
                                    {f.age_concentration.mode_pct}% /{' '}
                                    {f.age_concentration.mode_value}
                                  </div>
                                </div>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Age != Reg */}
                          {isColVisible('age_reg') && (
                            <td className="px-4 py-3 text-sm text-gray-900">
                              {f.age_equals_reg_pct != null ? (
                                <span>{f.age_equals_reg_pct}%</span>
                              ) : (
                                <span className="text-gray-400">{'—'}</span>
                              )}
                            </td>
                          )}
                          {/* Actions */}
                          {isColVisible('actions') && (
                            <td className="px-4 py-3 text-sm text-right whitespace-nowrap">
                              <div className="flex items-center justify-end gap-1">
                                {/* Assessment buttons */}
                                {isSessionActive && (
                                  <div className="inline-flex items-center gap-1 mr-2">
                                    <button
                                      onClick={function () {
                                        handleAssessment(
                                          f.username,
                                          'eligible_for_renewal',
                                        );
                                      }}
                                      disabled={!!savingResult}
                                      className={
                                        'px-2 py-1 rounded text-xs font-medium border transition-colors ' +
                                        (wr.result === 'eligible_for_renewal'
                                          ? 'bg-green-600 text-white border-green-600'
                                          : 'bg-green-50 text-green-800 border-green-300 hover:bg-green-100')
                                      }
                                      title="Eligible for Renewal"
                                    >
                                      <i className="fa-solid fa-circle-check"></i>
                                    </button>
                                    <button
                                      onClick={function () {
                                        handleAssessment(
                                          f.username,
                                          'probation',
                                        );
                                      }}
                                      disabled={!!savingResult}
                                      className={
                                        'px-2 py-1 rounded text-xs font-medium border transition-colors ' +
                                        (wr.result === 'probation'
                                          ? 'bg-amber-600 text-white border-amber-600'
                                          : 'bg-amber-50 text-amber-800 border-amber-300 hover:bg-amber-100')
                                      }
                                      title="Probation"
                                    >
                                      <i className="fa-solid fa-triangle-exclamation"></i>
                                    </button>
                                    <button
                                      onClick={function () {
                                        handleAssessment(
                                          f.username,
                                          'suspended',
                                        );
                                      }}
                                      disabled={!!savingResult}
                                      className={
                                        'px-2 py-1 rounded text-xs font-medium border transition-colors ' +
                                        (wr.result === 'suspended'
                                          ? 'bg-red-600 text-white border-red-600'
                                          : 'bg-red-50 text-red-800 border-red-300 hover:bg-red-100')
                                      }
                                      title="Suspended"
                                    >
                                      <i className="fa-solid fa-ban"></i>
                                    </button>
                                    <button
                                      onClick={function () {
                                        openFlwNotesModal(f.username);
                                      }}
                                      className={
                                        'px-2 py-1 rounded text-xs border transition-colors ' +
                                        (hasNotes
                                          ? 'bg-yellow-100 text-yellow-800 border-yellow-300'
                                          : 'bg-gray-100 text-gray-700 border-gray-300 hover:bg-gray-200')
                                      }
                                      title="Notes"
                                    >
                                      <i className="fa-solid fa-note-sticky"></i>
                                    </button>
                                  </div>
                                )}
                                {!isSessionActive && wr.result && (
                                  <div className="mr-2">
                                    {resultBadge(wr.result)}
                                  </div>
                                )}
                                <button
                                  onClick={function () {
                                    addToFilter(f.username);
                                  }}
                                  className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-100"
                                  title="Add this FLW to filter"
                                >
                                  <i className="fa-solid fa-filter mr-1"></i>{' '}
                                  Filter
                                </button>
                                {openTaskUsernames.indexOf(f.username) >= 0 ||
                                openTasks[f.username] ? (
                                  <button
                                    onClick={function () {
                                      toggleTaskExpand(f.username);
                                    }}
                                    className={
                                      'inline-flex items-center px-2 py-1 border rounded text-xs ' +
                                      (expandedTaskFlw === f.username
                                        ? 'border-purple-400 text-purple-700 bg-purple-50'
                                        : 'border-gray-300 text-gray-500 hover:bg-gray-100')
                                    }
                                    title="View open task"
                                  >
                                    <i
                                      className={
                                        'fa-solid mr-1 ' +
                                        (expandedTaskFlw === f.username
                                          ? 'fa-chevron-up'
                                          : 'fa-clipboard-list')
                                      }
                                    ></i>{' '}
                                    Task
                                  </button>
                                ) : (
                                  <button
                                    onClick={function () {
                                      if (
                                        !actions ||
                                        !actions.createTaskWithOCS
                                      ) {
                                        showToast(
                                          'Task creation not available — please hard-refresh (Cmd+Shift+R)',
                                        );
                                        return;
                                      }
                                      openOcsModal(f);
                                    }}
                                    disabled={
                                      createdTaskUsernames.indexOf(
                                        f.username,
                                      ) >= 0
                                    }
                                    className={
                                      'inline-flex items-center px-2 py-1 border rounded text-xs ' +
                                      (createdTaskUsernames.indexOf(
                                        f.username,
                                      ) >= 0
                                        ? 'border-gray-200 text-gray-400 cursor-not-allowed'
                                        : 'border-blue-300 text-blue-700 hover:bg-blue-50')
                                    }
                                    title={
                                      createdTaskUsernames.indexOf(
                                        f.username,
                                      ) >= 0
                                        ? 'Task recently created'
                                        : 'Create task & initiate AI for this FLW'
                                    }
                                  >
                                    <i className="fa-solid fa-plus mr-1"></i>{' '}
                                    Task
                                  </button>
                                )}
                              </div>
                            </td>
                          )}
                        </tr>
                        {expandedTaskFlw === f.username && (
                          <tr key={f.username + '-task'}>
                            <td
                              colSpan={visibleCols.length}
                              className="px-0 py-0 bg-gray-50"
                              style={{ position: 'relative' }}
                            >
                              <div
                                className="border-t border-b border-purple-200 bg-white mx-4 my-2 rounded-lg shadow-sm overflow-hidden"
                                style={{ maxWidth: 'calc(100vw - 220px)' }}
                              >
                                {taskLoading && !taskDetail && (
                                  <div className="p-6 text-center text-gray-500">
                                    <i className="fa-solid fa-spinner fa-spin mr-2"></i>{' '}
                                    Loading task...
                                  </div>
                                )}
                                {taskDetail && (
                                  <div>
                                    {/* Task header */}
                                    <div className="px-4 py-3 bg-purple-50 border-b border-purple-100 flex items-center justify-between">
                                      <div className="flex items-center gap-2">
                                        <i className="fa-solid fa-clipboard-list text-purple-600"></i>
                                        <span className="font-medium text-sm text-purple-900">
                                          {taskDetail.title}
                                        </span>
                                        <span
                                          className={
                                            'px-2 py-0.5 rounded-full text-xs font-medium ' +
                                            (taskStatus === 'investigating'
                                              ? 'bg-blue-100 text-blue-700'
                                              : taskStatus ===
                                                'flw_action_in_progress'
                                              ? 'bg-yellow-100 text-yellow-700'
                                              : taskStatus ===
                                                'flw_action_completed'
                                              ? 'bg-green-100 text-green-700'
                                              : taskStatus === 'review_needed'
                                              ? 'bg-purple-100 text-purple-700'
                                              : 'bg-gray-100 text-gray-700')
                                          }
                                        >
                                          {(
                                            TASK_STATUS_OPTIONS.find(
                                              function (s) {
                                                return s.value === taskStatus;
                                              },
                                            ) || {}
                                          ).label || taskStatus}
                                        </span>
                                      </div>
                                      <button
                                        onClick={function () {
                                          setExpandedTaskFlw(null);
                                        }}
                                        className="text-gray-400 hover:text-gray-600 text-sm"
                                      >
                                        <i className="fa-solid fa-xmark"></i>
                                      </button>
                                    </div>

                                    <div
                                      className="flex flex-col lg:flex-row"
                                      style={{ minWidth: 0 }}
                                    >
                                      {/* AI Conversation panel */}
                                      <div
                                        className="border-r border-gray-100"
                                        style={{
                                          flex: '1 1 0%',
                                          minWidth: 0,
                                          overflow: 'hidden',
                                        }}
                                      >
                                        <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center justify-between">
                                          <span className="text-xs font-medium text-gray-600">
                                            <i className="fa-solid fa-comments mr-1"></i>{' '}
                                            AI Conversation
                                          </span>
                                          <button
                                            onClick={
                                              handleTaskRefreshTranscript
                                            }
                                            disabled={taskLoading}
                                            className="text-xs text-blue-600 hover:text-blue-800 disabled:text-gray-400"
                                            title="Refresh from OCS"
                                          >
                                            <i
                                              className={
                                                'fa-solid fa-rotate-right' +
                                                (taskLoading ? ' fa-spin' : '')
                                              }
                                            ></i>{' '}
                                            Refresh
                                          </button>
                                        </div>
                                        <div
                                          className="p-3 overflow-y-auto space-y-2"
                                          style={{
                                            minHeight: '120px',
                                            maxHeight: '520px',
                                          }}
                                        >
                                          {taskTranscript &&
                                          taskTranscript.length > 0 ? (
                                            taskTranscript.map(
                                              function (msg, idx) {
                                                var isAssistant =
                                                  msg.role === 'assistant';
                                                return (
                                                  <div
                                                    key={idx}
                                                    className={
                                                      'flex ' +
                                                      (isAssistant
                                                        ? 'justify-start'
                                                        : 'justify-end')
                                                    }
                                                  >
                                                    <div
                                                      className={
                                                        'rounded-lg px-3 py-2 text-sm ' +
                                                        (isAssistant
                                                          ? 'bg-gray-100 text-gray-800'
                                                          : 'bg-blue-500 text-white')
                                                      }
                                                      style={{
                                                        maxWidth: '90%',
                                                      }}
                                                    >
                                                      <div
                                                        className="whitespace-pre-wrap break-words"
                                                        style={{
                                                          overflowWrap:
                                                            'anywhere',
                                                        }}
                                                      >
                                                        {msg.content}
                                                      </div>
                                                      {msg.created_at && (
                                                        <div
                                                          className={
                                                            'text-xs mt-1 ' +
                                                            (isAssistant
                                                              ? 'text-gray-400'
                                                              : 'text-blue-200')
                                                          }
                                                        >
                                                          {new Date(
                                                            msg.created_at,
                                                          ).toLocaleString()}
                                                        </div>
                                                      )}
                                                    </div>
                                                  </div>
                                                );
                                              },
                                            )
                                          ) : taskTranscript &&
                                            taskTranscript.length === 0 ? (
                                            oauthStatus &&
                                            !oauthStatus.ocs?.active ? (
                                              <div className="text-center py-4">
                                                <div className="text-amber-600 text-sm mb-2">
                                                  <i className="fa-solid fa-link-slash mr-1"></i>{' '}
                                                  OCS authorization required to
                                                  load AI conversation
                                                </div>
                                                {oauthStatus.ocs
                                                  ?.authorize_url ? (
                                                  <a
                                                    href={
                                                      oauthStatus.ocs
                                                        .authorize_url
                                                    }
                                                    className="inline-block px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 no-underline"
                                                  >
                                                    <i className="fa-solid fa-arrow-right-to-bracket mr-1"></i>{' '}
                                                    Connect to OCS
                                                  </a>
                                                ) : null}
                                              </div>
                                            ) : (
                                              <div className="text-center text-gray-400 text-sm py-4">
                                                <i className="fa-solid fa-comment-slash mr-1"></i>{' '}
                                                No messages yet
                                              </div>
                                            )
                                          ) : !taskLoading ? (
                                            <div className="text-center text-gray-400 text-sm py-4">
                                              <i className="fa-solid fa-circle-info mr-1"></i>{' '}
                                              Transcript not available
                                            </div>
                                          ) : null}
                                        </div>
                                      </div>

                                      {/* Task controls panel */}
                                      <div className="w-full lg:w-64 p-4 space-y-3 bg-gray-50">
                                        {/* Status dropdown */}
                                        <div>
                                          <label className="block text-xs font-medium text-gray-600 mb-1">
                                            Status
                                          </label>
                                          <select
                                            value={taskStatus}
                                            onChange={function (e) {
                                              setTaskStatus(e.target.value);
                                            }}
                                            className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-purple-400 focus:border-purple-400"
                                          >
                                            {TASK_STATUS_OPTIONS.map(
                                              function (opt) {
                                                return (
                                                  <option
                                                    key={opt.value}
                                                    value={opt.value}
                                                  >
                                                    {opt.label}
                                                  </option>
                                                );
                                              },
                                            )}
                                          </select>
                                        </div>

                                        {/* Save / Discard */}
                                        <div className="flex gap-2">
                                          <button
                                            onClick={handleTaskSave}
                                            disabled={
                                              taskSaving ||
                                              taskStatus === taskOriginalStatus
                                            }
                                            className={
                                              'flex-1 px-3 py-1.5 rounded text-xs font-medium ' +
                                              (taskStatus !== taskOriginalStatus
                                                ? 'bg-purple-600 text-white hover:bg-purple-700'
                                                : 'bg-gray-200 text-gray-400 cursor-not-allowed')
                                            }
                                          >
                                            {taskSaving ? 'Saving...' : 'Save'}
                                          </button>
                                          <button
                                            onClick={function () {
                                              setTaskStatus(taskOriginalStatus);
                                            }}
                                            disabled={
                                              taskStatus === taskOriginalStatus
                                            }
                                            className={
                                              'flex-1 px-3 py-1.5 rounded text-xs font-medium border ' +
                                              (taskStatus !== taskOriginalStatus
                                                ? 'border-gray-300 text-gray-700 hover:bg-gray-100'
                                                : 'border-gray-200 text-gray-400 cursor-not-allowed')
                                            }
                                          >
                                            Discard
                                          </button>
                                        </div>

                                        {/* Close Task */}
                                        <div className="border-t border-gray-200 pt-3">
                                          {!showCloseForm ? (
                                            <button
                                              onClick={function () {
                                                setShowCloseForm(true);
                                              }}
                                              className="w-full px-3 py-1.5 rounded text-xs font-medium border border-red-300 text-red-600 hover:bg-red-50"
                                            >
                                              <i className="fa-solid fa-circle-xmark mr-1"></i>{' '}
                                              Close Task
                                            </button>
                                          ) : (
                                            <div className="space-y-2">
                                              <div className="text-xs font-medium text-gray-600">
                                                Outcome
                                              </div>
                                              <div className="space-y-1">
                                                {[
                                                  { v: 'none', l: 'None' },
                                                  {
                                                    v: 'satisfactory',
                                                    l: 'Satisfactory',
                                                  },
                                                  { v: 'warned', l: 'Warned' },
                                                  {
                                                    v: 'suspended',
                                                    l: 'Suspended',
                                                  },
                                                ].map(function (o) {
                                                  return (
                                                    <label
                                                      key={o.v}
                                                      className="flex items-center gap-2 text-xs cursor-pointer"
                                                    >
                                                      <input
                                                        type="radio"
                                                        name="close_action"
                                                        value={o.v}
                                                        checked={
                                                          closeAction === o.v
                                                        }
                                                        onChange={function () {
                                                          setCloseAction(o.v);
                                                        }}
                                                        className="text-purple-600 focus:ring-purple-500"
                                                      />
                                                      {o.l}
                                                    </label>
                                                  );
                                                })}
                                              </div>
                                              <textarea
                                                value={closeNote}
                                                onChange={function (e) {
                                                  setCloseNote(e.target.value);
                                                }}
                                                placeholder="Resolution note (optional)"
                                                rows={2}
                                                className="w-full text-xs border border-gray-300 rounded px-2 py-1 focus:ring-1 focus:ring-purple-400"
                                              />
                                              <div className="flex gap-2">
                                                <button
                                                  onClick={handleTaskClose}
                                                  disabled={taskSaving}
                                                  className="flex-1 px-3 py-1.5 rounded text-xs font-medium bg-red-600 text-white hover:bg-red-700"
                                                >
                                                  {taskSaving
                                                    ? 'Closing...'
                                                    : 'Confirm Close'}
                                                </button>
                                                <button
                                                  onClick={function () {
                                                    setShowCloseForm(false);
                                                  }}
                                                  className="flex-1 px-3 py-1.5 rounded text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-100"
                                                >
                                                  Cancel
                                                </button>
                                              </div>
                                            </div>
                                          )}
                                        </div>
                                      </div>
                                    </div>
                                  </div>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                  {sortedOverview.length === 0 && (
                    <tr>
                      <td
                        colSpan={visibleCols.length}
                        className="px-4 py-8 text-center text-sm text-gray-500"
                      >
                        No FLW data available
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* GPS ANALYSIS TAB */}
      {/* ============================================================ */}
      {activeTab === 'gps' && (
        <div>
          {/* GPS Summary Cards */}
          <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm mb-4">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="border-l-4 border-blue-500 pl-3">
                <div className="text-xs text-gray-600">Total Visits</div>
                <div className="text-xl font-bold text-gray-900">
                  {gpsData.total_visits || 0}
                </div>
              </div>
              <div className="border-l-4 border-red-500 pl-3">
                <div className="text-xs text-gray-600">Flagged</div>
                <div
                  className={
                    'text-xl font-bold ' +
                    ((gpsData.total_flagged || 0) > 0
                      ? 'text-red-600'
                      : 'text-gray-900')
                  }
                >
                  {gpsData.total_flagged || 0}
                </div>
              </div>
              <div className="border-l-4 border-green-500 pl-3">
                <div className="text-xs text-gray-600">Date Range</div>
                <div className="text-sm font-medium text-gray-900">
                  {gpsData.date_range_start || '-'} to{' '}
                  {gpsData.date_range_end || '-'}
                </div>
              </div>
              <div className="border-l-4 border-purple-500 pl-3">
                <div className="text-xs text-gray-600">Flag Threshold</div>
                <div className="text-lg font-bold text-gray-900">5 km</div>
              </div>
            </div>
          </div>

          {/* Aggregate GPS Map — all FLW visits */}
          {leafletReady && (gpsData.all_coordinates || []).length > 0 && (
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm mb-4">
              <div className="px-6 py-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-gray-700">
                  <i className="fa-solid fa-map text-blue-600 mr-2"></i>
                  All Visits Map
                  <span className="text-xs text-gray-500 font-normal ml-2">
                    ({(gpsData.all_coordinates || []).length} GPS points)
                  </span>
                </h2>
                <button
                  onClick={function () {
                    setShowAggregateMap(function (p) {
                      return !p;
                    });
                  }}
                  className={
                    'px-3 py-1.5 text-xs font-medium border rounded ' +
                    (showAggregateMap
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50')
                  }
                >
                  <i
                    className={
                      'fa-solid mr-1 ' +
                      (showAggregateMap ? 'fa-chevron-up' : 'fa-chevron-down')
                    }
                  ></i>
                  {showAggregateMap ? 'Hide Map' : 'Show Map'}
                </button>
              </div>
              {showAggregateMap && (
                <div className="p-4">
                  <div
                    id="aggregate-gps-map"
                    style={{
                      height: '450px',
                      width: '100%',
                      borderRadius: '0.375rem',
                      border: '1px solid #e5e7eb',
                    }}
                  ></div>
                  {/* FLW Color Legend */}
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 max-h-24 overflow-y-auto">
                    {(function () {
                      var usernames = {};
                      (gpsData.all_coordinates || []).forEach(function (c) {
                        usernames[c.u] = true;
                      });
                      var sortedUsers = Object.keys(usernames).sort();
                      return sortedUsers.map(function (u, i) {
                        var hue = Math.round((i * 360) / sortedUsers.length);
                        var color = 'hsl(' + hue + ', 70%, 45%)';
                        var name = u;
                        for (var gi = 0; gi < gpsFlws.length; gi++) {
                          if (gpsFlws[gi].username === u) {
                            name = gpsFlws[gi].display_name || u;
                            break;
                          }
                        }
                        return (
                          <span key={u} className="inline-flex items-center">
                            <span
                              className="inline-block w-3 h-3 rounded-full mr-1"
                              style={{ backgroundColor: color }}
                            ></span>
                            {name}
                          </span>
                        );
                      });
                    })()}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* GPS FLW Table */}
          <div
            className="bg-white border border-gray-200 rounded-lg shadow-sm"
            style={{ overflow: 'clip' }}
          >
            <div className="px-6 py-3 border-b border-gray-200 bg-gray-50">
              <h2 className="text-lg font-semibold text-gray-900">
                FLW GPS Analysis{' '}
                <span className="text-sm text-gray-600 font-normal">
                  ({filteredGpsFlws.length} FLWs)
                </span>
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table
                data-sticky-header
                className="min-w-full divide-y divide-gray-200"
              >
                <thead className="bg-gray-50">
                  <tr>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'display_name');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'display_name')}
                      tooltip="Frontline worker name and ID"
                    >
                      FLW Name
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'total_visits');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'total_visits')}
                      tooltip="Total form submissions within the selected date range."
                    >
                      Total Visits
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'visits_with_gps');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'visits_with_gps')}
                      tooltip="Visits with parseable GPS coordinates (lat, lon)."
                    >
                      With GPS
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'flagged_visits');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'flagged_visits')}
                      tooltip="Visits flagged for anomalous GPS"
                    >
                      Flagged
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'unique_cases');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'unique_cases')}
                      tooltip="Count of distinct mother case IDs visited by this FLW."
                    >
                      Unique Cases
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'avg_case_distance_km');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'avg_case_distance_km')}
                      tooltip="Avg haversine distance (km) between successive visits to same case. (N) = cases with 2+ visits."
                    >
                      Revisit Dist.
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(
                          setGpsSort,
                          gpsSort,
                          'median_meters_per_visit',
                        );
                      }}
                      sortIndicator={sortIcon(
                        gpsSort,
                        'median_meters_per_visit',
                      )}
                      tooltip="Median haversine distance (m) between consecutive visits to different mothers in same day."
                    >
                      Meter/Visit
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'dist_ratio');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'dist_ratio')}
                      tooltip="Revisit distance / meter per visit. Higher values may indicate suspicious patterns."
                    >
                      Dist. Ratio
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setGpsSort, gpsSort, 'max_case_distance_km');
                      }}
                      sortIndicator={sortIcon(gpsSort, 'max_case_distance_km')}
                      tooltip="Largest haversine distance (km) between visits to same case"
                    >
                      Max Revisit Dist.
                    </Th>
                    <ThStatic tooltip="Daily visit count sparkline for the last 7 days">
                      Trailing 7 Days
                    </ThStatic>
                    <ThStatic className="text-right">Actions</ThStatic>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {sortedGps.map(function (g) {
                    var isExpanded = expandedGps === g.username;
                    var gpsPct =
                      g.total_visits > 0
                        ? Math.round((g.visits_with_gps / g.total_visits) * 100)
                        : 0;
                    var maxTravel = getMaxDailyTravel(g);
                    return React.createElement(
                      React.Fragment,
                      { key: g.username },
                      <tr className="hover:bg-gray-50">
                        {/* FLW Name */}
                        <td className="px-4 py-3 text-sm">
                          <div className="flex items-center">
                            <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-xs font-bold mr-2">
                              {(g.display_name || g.username || '')
                                .charAt(0)
                                .toUpperCase()}
                            </div>
                            <div>
                              <div className="font-medium text-gray-900">
                                {g.display_name || g.username}
                              </div>
                              {g.display_name !== g.username && (
                                <div className="text-xs text-gray-500">
                                  {g.username}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        {/* Total Visits */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {g.total_visits || 0}
                        </td>
                        {/* With GPS */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {g.visits_with_gps || 0}
                          <span className="text-gray-500 ml-1">
                            ({gpsPct}%)
                          </span>
                        </td>
                        {/* Flagged */}
                        <td className="px-4 py-3 text-sm">
                          <span
                            className={
                              (g.flagged_visits || 0) > 0
                                ? 'text-red-600 font-bold'
                                : 'text-gray-900'
                            }
                          >
                            {g.flagged_visits || 0}
                          </span>
                        </td>
                        {/* Unique Cases */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {g.unique_cases || 0}
                        </td>
                        {/* Revisit Dist. */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {g.avg_case_distance_km != null ? (
                            <span>
                              {g.avg_case_distance_km + ' km'}{' '}
                              <span className="text-gray-500">
                                ({g.cases_with_revisits || 0})
                              </span>
                            </span>
                          ) : (
                            <span className="text-gray-400">{'—'}</span>
                          )}
                        </td>
                        {/* Meter/Visit */}
                        <td className="px-4 py-3 text-sm">
                          {g.median_meters_per_visit != null ? (
                            <span
                              className={
                                g.median_meters_per_visit >= 1000
                                  ? 'text-green-600 font-medium'
                                  : g.median_meters_per_visit >= 100
                                  ? 'text-yellow-600'
                                  : 'text-red-600'
                              }
                            >
                              {g.median_meters_per_visit + ' m'}
                            </span>
                          ) : (
                            <span className="text-gray-400">{'—'}</span>
                          )}
                        </td>
                        {/* Dist. Ratio */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {g.dist_ratio != null ? (
                            g.dist_ratio
                          ) : (
                            <span className="text-gray-400">{'—'}</span>
                          )}
                        </td>
                        {/* Max Revisit Dist. */}
                        <td className="px-4 py-3 text-sm">
                          {g.max_case_distance_km != null ? (
                            <span
                              className={
                                g.max_case_distance_km > 5
                                  ? 'text-red-600 font-bold'
                                  : 'text-gray-900'
                              }
                            >
                              {g.max_case_distance_km} km
                            </span>
                          ) : (
                            <span className="text-gray-400">{'—'}</span>
                          )}
                        </td>
                        {/* Trailing 7 Days - BAR CHART */}
                        <td className="px-4 py-3 text-sm">
                          {g.trailing_7_days && g.trailing_7_days.length > 0 ? (
                            <div className="flex items-center gap-2">
                              <div
                                className="inline-flex items-end gap-0.5"
                                style={{ height: '24px' }}
                              >
                                {g.trailing_7_days.map(function (day, idx) {
                                  var barH = Math.max(
                                    2,
                                    Math.min(
                                      24,
                                      (day.distance_km / maxTravel) * 24,
                                    ),
                                  );
                                  return (
                                    <span
                                      key={idx}
                                      className="w-2 rounded-sm bg-blue-500"
                                      style={{ height: barH + 'px' }}
                                      title={
                                        day.date +
                                        ': ' +
                                        day.distance_km +
                                        ' km'
                                      }
                                    ></span>
                                  );
                                })}
                              </div>
                              <span className="text-xs text-gray-500">
                                Avg: {g.avg_daily_travel_km || '-'} km/d
                              </span>
                            </div>
                          ) : (
                            <span className="text-gray-400">-</span>
                          )}
                        </td>
                        {/* Actions */}
                        <td className="px-4 py-3 text-sm text-right whitespace-nowrap">
                          <div className="flex items-center justify-end gap-1">
                            <button
                              onClick={function () {
                                addToFilter(g.username);
                              }}
                              className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-100"
                              title="Add this FLW to filter"
                            >
                              <i className="fa-solid fa-filter mr-1"></i> Filter
                            </button>
                            <button
                              onClick={function () {
                                fetchGpsDetail(g.username);
                              }}
                              className="inline-flex items-center px-2 py-1 border border-blue-300 rounded text-xs text-blue-700 hover:bg-blue-50"
                            >
                              <i
                                className={
                                  'fa-solid mr-1 ' +
                                  (isExpanded
                                    ? 'fa-chevron-up'
                                    : 'fa-chevron-down')
                                }
                              ></i>
                              {isExpanded ? 'Hide' : 'Details'}
                            </button>
                          </div>
                        </td>
                      </tr>,
                      isExpanded && gpsDetailLoading && (
                        <tr key={g.username + '_loading'}>
                          <td
                            colSpan={11}
                            className="p-0 border-b-2 border-blue-200"
                          >
                            <div className="bg-blue-50 px-6 py-4 border-t border-blue-200 text-center">
                              <i className="fa-solid fa-spinner fa-spin text-blue-600 mr-2"></i>
                              <span className="text-sm text-gray-600">
                                Loading visit details...
                              </span>
                            </div>
                          </td>
                        </tr>
                      ),
                      isExpanded &&
                        gpsDetail &&
                        (function () {
                          var displayVisits = selectedMother
                            ? (gpsDetail.visits || []).filter(function (v) {
                                return (
                                  v.mother_case_id === selectedMother ||
                                  v.case_id === selectedMother
                                );
                              })
                            : gpsDetail.visits || [];
                          displayVisits = sortRows(
                            displayVisits,
                            gpsDetailSort,
                          );
                          return (
                            <tr key={g.username + '_detail'}>
                              <td
                                colSpan={11}
                                className="p-0 border-b-2 border-blue-200"
                              >
                                <div className="bg-blue-50 px-6 py-3 border-t border-blue-200">
                                  <div className="flex justify-between items-center mb-2">
                                    <h4 className="text-sm font-semibold text-gray-900">
                                      <i className="fa-solid fa-location-dot text-blue-600 mr-1"></i>
                                      Visit Details ({displayVisits.length}{' '}
                                      visits
                                      {selectedMother ? ' — filtered' : ''})
                                    </h4>
                                    <div className="flex items-center gap-2">
                                      <button
                                        onClick={function () {
                                          setExpandedGps(null);
                                        }}
                                        className="text-gray-500 hover:text-gray-700 text-xs"
                                      >
                                        <i className="fa-solid fa-times mr-1"></i>{' '}
                                        Close
                                      </button>
                                    </div>
                                  </div>

                                  {/* GPS Map */}
                                  {leafletReady && (
                                    <div className="mb-3">
                                      <div className="flex items-center gap-3 mb-2">
                                        <span className="text-xs font-semibold text-gray-700">
                                          <i className="fa-solid fa-map text-blue-600 mr-1"></i>{' '}
                                          Map
                                        </span>
                                        <div className="inline-flex items-center gap-1">
                                          <button
                                            onClick={function () {
                                              setShowMapVisits(function (p) {
                                                return !p;
                                              });
                                            }}
                                            className={
                                              'px-3 py-1 text-xs font-medium border rounded ' +
                                              (showMapVisits
                                                ? 'bg-blue-600 text-white border-blue-600'
                                                : 'bg-white text-gray-400 border-gray-300 hover:bg-gray-50')
                                            }
                                          >
                                            <i
                                              className={
                                                'fa-solid fa-circle mr-1'
                                              }
                                              style={{
                                                color: showMapVisits
                                                  ? '#93c5fd'
                                                  : '#d1d5db',
                                                fontSize: '8px',
                                              }}
                                            ></i>
                                            Visits
                                          </button>
                                          <button
                                            onClick={function () {
                                              setShowMapMothers(function (p) {
                                                return !p;
                                              });
                                            }}
                                            className="px-3 py-1 text-xs font-medium border rounded"
                                            style={
                                              showMapMothers
                                                ? {
                                                    backgroundColor: '#f97316',
                                                    color: '#fff',
                                                    borderColor: '#f97316',
                                                  }
                                                : {
                                                    backgroundColor: '#fff',
                                                    color: '#9ca3af',
                                                    borderColor: '#d1d5db',
                                                  }
                                            }
                                          >
                                            <i
                                              className={
                                                'fa-solid fa-circle mr-1'
                                              }
                                              style={{
                                                color: showMapMothers
                                                  ? '#fdba74'
                                                  : '#d1d5db',
                                                fontSize: '8px',
                                              }}
                                            ></i>
                                            Mothers
                                          </button>
                                        </div>
                                        {selectedMother && (
                                          <div
                                            className="inline-flex items-center px-2 py-1 rounded text-xs"
                                            style={{
                                              backgroundColor: '#dbeafe',
                                              color: '#1e40af',
                                            }}
                                          >
                                            <i className="fa-solid fa-filter mr-1"></i>
                                            {(
                                              gpsDetail.visits.find(
                                                function (v) {
                                                  return (
                                                    v.mother_case_id ===
                                                    selectedMother
                                                  );
                                                },
                                              ) || {}
                                            ).entity_name || 'Selected mother'}
                                            <button
                                              onClick={function () {
                                                setSelectedMother(null);
                                              }}
                                              className="ml-1 hover:opacity-70"
                                              style={{ color: '#2563eb' }}
                                            >
                                              <i className="fa-solid fa-times"></i>
                                            </button>
                                          </div>
                                        )}
                                      </div>
                                      <div
                                        id={'gps-map-' + g.username}
                                        style={{
                                          height: '350px',
                                          width: '100%',
                                          borderRadius: '0.375rem',
                                          border: '1px solid #e5e7eb',
                                        }}
                                      ></div>
                                    </div>
                                  )}

                                  {/* Visits Table */}
                                  {displayVisits.length > 0 ? (
                                    <div
                                      className="overflow-x-auto bg-white rounded border border-gray-200"
                                      style={{
                                        maxHeight: '400px',
                                        overflowY: 'auto',
                                      }}
                                    >
                                      <table className="min-w-full divide-y divide-gray-200">
                                        <thead className="bg-gray-50 sticky top-0">
                                          <tr>
                                            <th
                                              className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:text-gray-700 select-none"
                                              onClick={function () {
                                                toggleSort(
                                                  setGpsDetailSort,
                                                  gpsDetailSort,
                                                  'visit_date',
                                                );
                                              }}
                                            >
                                              Date
                                              {sortIcon(
                                                gpsDetailSort,
                                                'visit_date',
                                              )}
                                            </th>
                                            <th
                                              className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:text-gray-700 select-none"
                                              onClick={function () {
                                                toggleSort(
                                                  setGpsDetailSort,
                                                  gpsDetailSort,
                                                  'form_name',
                                                );
                                              }}
                                            >
                                              Form
                                              {sortIcon(
                                                gpsDetailSort,
                                                'form_name',
                                              )}
                                            </th>
                                            <th
                                              className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:text-gray-700 select-none"
                                              onClick={function () {
                                                toggleSort(
                                                  setGpsDetailSort,
                                                  gpsDetailSort,
                                                  'entity_name',
                                                );
                                              }}
                                            >
                                              Entity
                                              {sortIcon(
                                                gpsDetailSort,
                                                'entity_name',
                                              )}
                                            </th>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                                              GPS
                                            </th>
                                            <th
                                              className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:text-gray-700 select-none"
                                              onClick={function () {
                                                toggleSort(
                                                  setGpsDetailSort,
                                                  gpsDetailSort,
                                                  'distance_from_prev_km',
                                                );
                                              }}
                                            >
                                              Revisit Dist.
                                              {sortIcon(
                                                gpsDetailSort,
                                                'distance_from_prev_km',
                                              )}
                                            </th>
                                            <th
                                              className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:text-gray-700 select-none"
                                              onClick={function () {
                                                toggleSort(
                                                  setGpsDetailSort,
                                                  gpsDetailSort,
                                                  'is_flagged',
                                                );
                                              }}
                                            >
                                              Status
                                              {sortIcon(
                                                gpsDetailSort,
                                                'is_flagged',
                                              )}
                                            </th>
                                          </tr>
                                        </thead>
                                        <tbody className="bg-white divide-y divide-gray-200">
                                          {displayVisits.map(function (v, vi) {
                                            return (
                                              <tr
                                                key={vi}
                                                className={
                                                  v.is_flagged
                                                    ? 'bg-red-50'
                                                    : 'hover:bg-gray-50'
                                                }
                                              >
                                                <td className="px-4 py-2 text-sm text-gray-900">
                                                  {v.visit_date || '-'}
                                                </td>
                                                <td className="px-4 py-2 text-sm text-gray-900">
                                                  {v.form_name || '-'}
                                                </td>
                                                <td className="px-4 py-2 text-sm text-gray-900">
                                                  {v.entity_name || '-'}
                                                </td>
                                                <td className="px-4 py-2 text-sm text-gray-500">
                                                  {v.gps ? (
                                                    <span>
                                                      {v.gps.latitude.toFixed(
                                                        4,
                                                      )}
                                                      ,{' '}
                                                      {v.gps.longitude.toFixed(
                                                        4,
                                                      )}
                                                    </span>
                                                  ) : (
                                                    <span className="text-gray-400">
                                                      No GPS
                                                    </span>
                                                  )}
                                                </td>
                                                <td className="px-4 py-2 text-sm">
                                                  {v.distance_from_prev_km !=
                                                  null ? (
                                                    <span
                                                      className={
                                                        v.distance_from_prev_km >
                                                        5
                                                          ? 'text-red-600 font-bold'
                                                          : 'text-gray-900'
                                                      }
                                                    >
                                                      {v.distance_from_prev_km}{' '}
                                                      km
                                                    </span>
                                                  ) : (
                                                    <span className="text-gray-400">
                                                      -
                                                    </span>
                                                  )}
                                                </td>
                                                <td className="px-4 py-2 text-sm">
                                                  {v.is_flagged ? (
                                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-800">
                                                      <i className="fa-solid fa-flag mr-1"></i>{' '}
                                                      Flagged
                                                    </span>
                                                  ) : (
                                                    <span className="text-green-600">
                                                      <i className="fa-solid fa-check"></i>
                                                    </span>
                                                  )}
                                                </td>
                                              </tr>
                                            );
                                          })}
                                        </tbody>
                                      </table>
                                    </div>
                                  ) : (
                                    <div className="text-center text-sm text-gray-500 py-3">
                                      No visits found for this FLW.
                                    </div>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })(),
                    );
                  })}
                  {sortedGps.length === 0 && (
                    <tr>
                      <td
                        colSpan={11}
                        className="px-4 py-8 text-center text-sm text-gray-500"
                      >
                        No GPS data available
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* FOLLOW-UP RATE TAB */}
      {/* ============================================================ */}
      {activeTab === 'followup' && (
        <div>
          {/* Follow-up Summary Cards */}
          <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm mb-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="border-l-4 border-blue-500 pl-3">
                <div className="text-xs text-gray-600">Total Visit Cases</div>
                <div className="text-xl font-bold text-gray-900">
                  {followupData.total_cases || 0}
                </div>
              </div>
              <div className="border-l-4 border-blue-500 pl-3">
                <div className="text-xs text-gray-600">Total FLWs</div>
                <div className="text-xl font-bold text-gray-900">
                  {filteredFuFlws.length}
                </div>
              </div>
              <div className="border-l-4 border-green-500 pl-3">
                <div className="text-xs text-gray-600">Avg Follow-up Rate</div>
                <div
                  className={
                    'text-xl font-bold ' +
                    (overallFuRate >= 75
                      ? 'text-green-600'
                      : overallFuRate >= 50
                      ? 'text-yellow-600'
                      : 'text-red-600')
                  }
                >
                  {overallFuRate}%
                </div>
              </div>
            </div>
          </div>

          {/* Visit Status Distribution — per-visit-type stacked bar chart */}
          {visitDist &&
            visitDist.by_visit_type &&
            visitDist.totals &&
            visitDist.totals.total > 0 &&
            (function () {
              var categories = [
                {
                  key: 'completed_on_time',
                  label: 'Completed On Time',
                  color: '#22c55e',
                },
                {
                  key: 'completed_late',
                  label: 'Completed Late',
                  color: '#86efac',
                },
                { key: 'due_on_time', label: 'Due On Time', color: '#facc15' },
                { key: 'due_late', label: 'Due Late', color: '#fb923c' },
                { key: 'missed', label: 'Missed', color: '#ef4444' },
                { key: 'not_due_yet', label: 'Not Due Yet', color: '#9ca3af' },
              ];
              var visibleCategories = categories.filter(function (c) {
                return !hiddenCategories[c.key];
              });
              var maxTotal = Math.max.apply(
                null,
                visitDist.by_visit_type.map(function (vt) {
                  var sum = 0;
                  visibleCategories.forEach(function (c) {
                    sum += vt[c.key] || 0;
                  });
                  return sum;
                }),
              );
              var chartHeight = 180;

              return (
                <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm mb-4">
                  <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider mb-4">
                    Visit Status Distribution
                  </h3>
                  {/* Bar chart */}
                  <div
                    className="flex items-end justify-center gap-3"
                    style={{ height: chartHeight + 30 }}
                  >
                    {visitDist.by_visit_type.map(function (vt) {
                      var visibleTotal = 0;
                      visibleCategories.forEach(function (c) {
                        visibleTotal += vt[c.key] || 0;
                      });
                      var barHeight =
                        maxTotal > 0
                          ? Math.round((visibleTotal / maxTotal) * chartHeight)
                          : 0;

                      return (
                        <div
                          key={vt.visit_type}
                          className="flex flex-col items-center"
                          style={{ flex: '1 1 0', maxWidth: 80 }}
                        >
                          {/* Stacked bar */}
                          <div
                            className="w-full flex flex-col-reverse rounded-t overflow-hidden border border-gray-200"
                            style={{ height: Math.max(barHeight, 2) }}
                          >
                            {visibleCategories.map(function (c) {
                              var count = vt[c.key] || 0;
                              if (count === 0) return null;
                              var segPct =
                                visibleTotal > 0
                                  ? (count / visibleTotal) * 100
                                  : 0;
                              return (
                                <div
                                  key={c.key}
                                  style={{
                                    height: segPct + '%',
                                    backgroundColor: c.color,
                                    transition: 'all 0.3s',
                                    minHeight: count > 0 ? 2 : 0,
                                  }}
                                  title={
                                    c.label +
                                    ': ' +
                                    count +
                                    ' (' +
                                    Math.round(segPct) +
                                    '%)'
                                  }
                                ></div>
                              );
                            })}
                          </div>
                          {/* Total count */}
                          <div className="text-xs text-gray-500 mt-1 font-medium">
                            {visibleTotal}
                          </div>
                          {/* Visit type label */}
                          <div className="text-xs text-gray-700 font-medium mt-0.5 text-center">
                            {vt.visit_type}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  {/* Interactive legend */}
                  <div className="flex flex-wrap justify-center gap-3 mt-4">
                    {categories.map(function (c) {
                      var isHidden = !!hiddenCategories[c.key];
                      return (
                        <button
                          key={c.key}
                          onClick={function () {
                            setHiddenCategories(function (prev) {
                              var next = Object.assign({}, prev);
                              if (next[c.key]) {
                                delete next[c.key];
                              } else {
                                next[c.key] = true;
                              }
                              return next;
                            });
                          }}
                          className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs cursor-pointer border border-transparent hover:border-gray-300"
                          style={{
                            opacity: isHidden ? 0.4 : 1,
                            textDecoration: isHidden ? 'line-through' : 'none',
                          }}
                        >
                          <span
                            className="inline-block w-3 h-3 rounded"
                            style={{ backgroundColor: c.color }}
                          ></span>
                          {c.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })()}

          {/* Follow-up FLW Table */}
          <div
            className="bg-white border border-gray-200 rounded-lg shadow-sm"
            style={{ overflow: 'clip' }}
          >
            <div className="px-6 py-3 border-b border-gray-200 bg-gray-50">
              <h2 className="text-lg font-semibold text-gray-900">
                FLW Follow-Up Rates{' '}
                <span className="text-sm text-gray-600 font-normal">
                  ({filteredFuFlws.length} FLWs)
                </span>
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table
                data-sticky-header
                className="min-w-full divide-y divide-gray-200"
              >
                <thead className="bg-gray-50">
                  <tr>
                    <Th
                      onClick={function () {
                        toggleSort(setFuSort, fuSort, 'display_name');
                      }}
                      sortIndicator={sortIcon(fuSort, 'display_name')}
                    >
                      FLW Name
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setFuSort, fuSort, 'completion_rate');
                      }}
                      sortIndicator={sortIcon(fuSort, 'completion_rate')}
                      tooltip="Completed / total visits due 5+ days ago"
                    >
                      Follow-up Rate
                    </Th>
                    <Th
                      onClick={function () {
                        toggleSort(setFuSort, fuSort, 'completed_total');
                      }}
                      sortIndicator={sortIcon(fuSort, 'completed_total')}
                      tooltip="Total completed visits out of all scheduled visits"
                    >
                      Completed
                    </Th>
                    <ThStatic tooltip="Visits not yet completed but not past expiry">
                      Due
                    </ThStatic>
                    <ThStatic tooltip="Visits past their expiry date that were never completed.">
                      Missed
                    </ThStatic>
                    {visitTypes.map(function (vt) {
                      return (
                        <ThStatic key={vt} tooltip="Per-visit-type breakdown">
                          {visitTypeLabels[vt]}
                        </ThStatic>
                      );
                    })}
                    <ThStatic className="text-right">Actions</ThStatic>
                  </tr>
                </thead>
                {sortedFu.map(function (f) {
                  var isExpanded = expandedFu === f.username;
                  var statusColor = f.status_color || 'red';
                  var barColorClass =
                    statusColor === 'green'
                      ? 'bg-green-500'
                      : statusColor === 'yellow'
                      ? 'bg-yellow-500'
                      : 'bg-red-500';
                  var textColorClass =
                    statusColor === 'green'
                      ? 'text-green-600'
                      : statusColor === 'yellow'
                      ? 'text-yellow-600'
                      : 'text-red-600';
                  var avatarClass =
                    statusColor === 'green'
                      ? 'bg-green-100 text-green-700'
                      : statusColor === 'yellow'
                      ? 'bg-yellow-100 text-yellow-700'
                      : 'bg-red-100 text-red-700';
                  var dueTotalVal = (f.due_on_time || 0) + (f.due_late || 0);
                  var completedPct =
                    f.total_visits > 0
                      ? Math.round((f.completed_total / f.total_visits) * 100)
                      : 0;
                  var drillMothers = fuDrilldown[f.username] || [];

                  return (
                    <tbody
                      key={f.username}
                      className="divide-y divide-gray-200"
                    >
                      {/* FLW summary row */}
                      <tr
                        className={
                          'hover:bg-gray-50 cursor-pointer ' +
                          (isExpanded ? 'bg-blue-50' : 'bg-white')
                        }
                        onClick={function () {
                          setExpandedFu(isExpanded ? null : f.username);
                        }}
                      >
                        {/* FLW Name */}
                        <td className="px-4 py-3 text-sm">
                          <div className="flex items-center">
                            <i
                              className={
                                'fa-solid fa-chevron-right text-gray-400 mr-2 text-xs transition-transform duration-200 ' +
                                (isExpanded ? 'rotate-90' : '')
                              }
                              style={
                                isExpanded ? { transform: 'rotate(90deg)' } : {}
                              }
                            ></i>
                            <div
                              className={
                                'w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold mr-2 ' +
                                avatarClass
                              }
                            >
                              {(f.display_name || f.username || '')
                                .charAt(0)
                                .toUpperCase()}
                            </div>
                            <div>
                              <div className="font-medium text-gray-900">
                                {f.display_name || f.username}
                              </div>
                              {f.display_name !== f.username && (
                                <div className="text-xs text-gray-500">
                                  {f.username}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        {/* Follow-up Rate */}
                        <td className="px-4 py-3 text-sm">
                          <div className="flex items-center">
                            <div className="w-16 bg-gray-200 rounded-full h-2 mr-2">
                              <div
                                className={
                                  'h-2 rounded-full transition-all ' +
                                  barColorClass
                                }
                                style={{
                                  width:
                                    Math.min(100, f.completion_rate || 0) + '%',
                                }}
                              ></div>
                            </div>
                            <span className={'font-bold ' + textColorClass}>
                              {f.completion_rate != null
                                ? f.completion_rate + '%'
                                : '—'}
                            </span>
                          </div>
                        </td>
                        {/* Completed */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {f.completed_total || 0}
                          <span className="text-xs text-gray-400 ml-1">
                            {f.total_visits > 0
                              ? '(' + completedPct + '%)'
                              : ''}
                          </span>
                        </td>
                        {/* Due */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {dueTotalVal}
                        </td>
                        {/* Missed */}
                        <td className="px-4 py-3 text-sm text-gray-900">
                          {f.missed_total || 0}
                        </td>
                        {/* Per-visit-type columns */}
                        {visitTypes.map(function (vt) {
                          var comp =
                            (f[vt + '_completed_on_time'] || 0) +
                            (f[vt + '_completed_late'] || 0);
                          var due =
                            (f[vt + '_due_on_time'] || 0) +
                            (f[vt + '_due_late'] || 0);
                          var missed = f[vt + '_missed'] || 0;
                          return (
                            <td
                              key={vt}
                              className="px-4 py-3 whitespace-nowrap text-xs"
                            >
                              <div className="text-green-600">
                                <i className="fa-solid fa-check mr-1"></i>
                                {comp}
                              </div>
                              <div className="text-gray-500">
                                <i className="fa-solid fa-clock mr-1"></i>
                                {due}
                              </div>
                              <div className="text-red-500">
                                <i className="fa-solid fa-xmark mr-1"></i>
                                {missed}
                              </div>
                            </td>
                          );
                        })}
                        {/* Actions */}
                        <td
                          className="px-4 py-3 text-sm text-right whitespace-nowrap"
                          onClick={function (e) {
                            e.stopPropagation();
                          }}
                        >
                          <div className="flex items-center justify-end gap-1">
                            <button
                              onClick={function () {
                                addToFilter(f.username);
                              }}
                              className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-100"
                              title="Add this FLW to filter"
                            >
                              <i className="fa-solid fa-filter mr-1"></i> Filter
                            </button>
                          </div>
                        </td>
                      </tr>
                      {/* Inline drill-down row */}
                      {isExpanded && (
                        <tr>
                          <td
                            colSpan={12}
                            className="p-0 bg-gray-50 border-l-4 border-blue-400"
                          >
                            {/* Header bar */}
                            <div className="px-6 py-3 border-b border-gray-200 flex justify-between items-center">
                              <h3 className="text-sm font-semibold text-gray-900">
                                Visits for {f.display_name || f.username}
                              </h3>
                              <div className="flex items-center gap-3">
                                <label
                                  className="inline-flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer"
                                  onClick={function (e) {
                                    e.stopPropagation();
                                  }}
                                >
                                  <input
                                    type="checkbox"
                                    checked={showEligibleOnly}
                                    onChange={function (e) {
                                      setShowEligibleOnly(e.target.checked);
                                    }}
                                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500 h-3.5 w-3.5"
                                  />
                                  Full intervention bonus only
                                </label>
                                <label
                                  className="inline-flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer"
                                  onClick={function (e) {
                                    e.stopPropagation();
                                  }}
                                >
                                  <input
                                    type="checkbox"
                                    checked={showAllVisits}
                                    onChange={function (e) {
                                      setShowAllVisits(e.target.checked);
                                    }}
                                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500 h-3.5 w-3.5"
                                  />
                                  Show missed/completed visits
                                </label>
                                <button
                                  onClick={function (e) {
                                    e.stopPropagation();
                                    setExpandedFu(null);
                                  }}
                                  className="text-gray-500 hover:text-gray-700"
                                >
                                  <i className="fa-solid fa-times"></i>
                                </button>
                              </div>
                            </div>
                            {/* Mother groups */}
                            {getVisibleMothers(drillMothers).length > 0 ? (
                              <div>
                                {getVisibleMothers(drillMothers).map(
                                  function (mother) {
                                    var visibleVisits =
                                      getVisibleVisits(mother);
                                    var fuRateColor =
                                      (mother.follow_up_rate || 0) >= 80
                                        ? 'bg-green-100 text-green-800'
                                        : (mother.follow_up_rate || 0) >= 60
                                        ? 'bg-yellow-100 text-yellow-800'
                                        : 'bg-red-100 text-red-800';
                                    return (
                                      <div
                                        key={mother.mother_case_id}
                                        className="border-b border-gray-100"
                                      >
                                        {/* Mother header */}
                                        <div className="px-6 py-2 bg-gray-100 flex items-center justify-between">
                                          <span className="text-sm font-medium text-gray-700 flex items-center">
                                            {mother.mother_name ? (
                                              <span>
                                                {mother.mother_name}
                                                <span className="text-gray-400 font-normal text-xs ml-1">
                                                  (
                                                  {mother.mother_case_id.substring(
                                                    0,
                                                    8,
                                                  )}
                                                  ...)
                                                </span>
                                              </span>
                                            ) : (
                                              <span className="font-mono text-xs">
                                                {mother.mother_case_id}
                                              </span>
                                            )}
                                            {!mother.eligible && (
                                              <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 ml-2">
                                                Not eligible
                                              </span>
                                            )}
                                          </span>
                                          <span
                                            className={
                                              'text-xs px-2 py-1 rounded ' +
                                              fuRateColor
                                            }
                                          >
                                            {mother.completed || 0}/
                                            {mother.total || 0} (
                                            {mother.follow_up_rate || 0}%)
                                          </span>
                                        </div>
                                        {/* Mother metadata row */}
                                        <div className="px-6 py-1.5 bg-gray-50 flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-500 border-b border-gray-100">
                                          {mother.registration_date && (
                                            <span>
                                              <i className="fa-solid fa-calendar-plus mr-1 text-gray-400"></i>
                                              Registered:{' '}
                                              <span className="text-gray-700">
                                                {mother.registration_date}
                                              </span>
                                            </span>
                                          )}
                                          {mother.age && (
                                            <span>
                                              <i className="fa-solid fa-user mr-1 text-gray-400"></i>
                                              Age:{' '}
                                              <span className="text-gray-700">
                                                {mother.age}
                                              </span>
                                            </span>
                                          )}
                                          {mother.phone_number && (
                                            <span>
                                              <i className="fa-solid fa-phone mr-1 text-gray-400"></i>
                                              <span className="text-gray-700">
                                                {mother.phone_number}
                                              </span>
                                            </span>
                                          )}
                                          {mother.household_size && (
                                            <span>
                                              <i className="fa-solid fa-people-roof mr-1 text-gray-400"></i>
                                              Household:{' '}
                                              <span className="text-gray-700">
                                                {mother.household_size}
                                              </span>
                                            </span>
                                          )}
                                          {mother.preferred_time_of_visit && (
                                            <span>
                                              <i className="fa-solid fa-clock mr-1 text-gray-400"></i>
                                              Preferred time:{' '}
                                              <span className="text-gray-700">
                                                {mother.preferred_time_of_visit}
                                              </span>
                                            </span>
                                          )}
                                          {mother.expected_delivery_date && (
                                            <span>
                                              <i className="fa-solid fa-calendar mr-1 text-gray-400"></i>
                                              EDD:{' '}
                                              <span className="text-gray-700">
                                                {mother.expected_delivery_date}
                                              </span>
                                            </span>
                                          )}
                                          {mother.anc_completion_date && (
                                            <span>
                                              <i className="fa-solid fa-check-circle mr-1 text-green-500"></i>
                                              ANC completed:{' '}
                                              <span className="text-gray-700">
                                                {mother.anc_completion_date}
                                              </span>
                                            </span>
                                          )}
                                          {mother.pnc_completion_date && (
                                            <span>
                                              <i className="fa-solid fa-check-circle mr-1 text-green-500"></i>
                                              PNC completed:{' '}
                                              <span className="text-gray-700">
                                                {mother.pnc_completion_date}
                                              </span>
                                            </span>
                                          )}
                                          {mother.baby_dob && (
                                            <span>
                                              <i className="fa-solid fa-baby mr-1 text-gray-400"></i>
                                              Baby DOB:{' '}
                                              <span className="text-gray-700">
                                                {mother.baby_dob}
                                              </span>
                                            </span>
                                          )}
                                          {!mother.registration_date &&
                                            !mother.age &&
                                            !mother.phone_number &&
                                            !mother.household_size &&
                                            !mother.preferred_time_of_visit &&
                                            !mother.anc_completion_date &&
                                            !mother.pnc_completion_date &&
                                            !mother.expected_delivery_date &&
                                            !mother.baby_dob && (
                                              <span className="text-gray-400 italic">
                                                No metadata available
                                              </span>
                                            )}
                                        </div>
                                        {/* Visits table */}
                                        <table className="w-full table-fixed divide-y divide-gray-200">
                                          <thead>
                                            <tr>
                                              <th className="w-[25%] px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                Visit Type
                                              </th>
                                              <th className="w-[25%] px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                Scheduled
                                              </th>
                                              <th className="w-[25%] px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                Expiry Date
                                              </th>
                                              <th className="w-[25%] px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                                Status
                                              </th>
                                            </tr>
                                          </thead>
                                          <tbody>
                                            {visibleVisits.length > 0 ? (
                                              visibleVisits.map(
                                                function (visit, vi) {
                                                  return (
                                                    <tr
                                                      key={vi}
                                                      className="hover:bg-gray-50"
                                                    >
                                                      <td className="px-4 py-2 text-sm text-gray-900">
                                                        {visit.visit_type}
                                                      </td>
                                                      <td className="px-4 py-2 text-sm text-gray-900">
                                                        {visit.visit_date_scheduled ||
                                                          '-'}
                                                      </td>
                                                      <td className="px-4 py-2 text-sm text-gray-900">
                                                        {visit.visit_expiry_date ||
                                                          '-'}
                                                      </td>
                                                      <td className="px-4 py-2 text-sm">
                                                        <span
                                                          className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                                                          style={getVisitStatusStyle(
                                                            visit.status,
                                                          )}
                                                        >
                                                          {visit.status}
                                                        </span>
                                                      </td>
                                                    </tr>
                                                  );
                                                },
                                              )
                                            ) : (
                                              <tr>
                                                <td
                                                  colSpan={4}
                                                  className="px-4 py-3 text-center text-xs text-gray-400 italic"
                                                >
                                                  No due visits
                                                </td>
                                              </tr>
                                            )}
                                          </tbody>
                                        </table>
                                      </div>
                                    );
                                  },
                                )}
                              </div>
                            ) : (
                              <div className="p-6 text-center text-gray-500">
                                {'No due visits found for this FLW.'}
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  );
                })}
                {sortedFu.length === 0 && (
                  <tbody>
                    <tr>
                      <td
                        colSpan={12}
                        className="px-4 py-8 text-center text-sm text-gray-500"
                      >
                        No follow-up data available. Ensure CommCare HQ is
                        authorized.
                      </td>
                    </tr>
                  </tbody>
                )}
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ========== FLW PERFORMANCE TAB ========== */}
      {activeTab === 'performance' && (
        <div>
          <div
            className="bg-white rounded-lg shadow-sm border border-gray-200"
            style={{ overflow: 'clip' }}
          >
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-ranking-star mr-1"></i> FLW
                Performance by Assessment Status
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                Aggregated case metrics grouped by each FLW's latest known
                assessment outcome across all completed monitoring runs.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table
                data-sticky-header
                className="min-w-full divide-y divide-gray-200 text-sm"
              >
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Status
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      # FLWs
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Total Cases
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Eligible at Reg
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Still Eligible
                    </th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      % Still Eligible
                    </th>
                    <th
                      className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"
                      title="Eligible cases with 0 or 1 missed visits / eligible cases"
                    >
                      % &le;1 Missed
                    </th>
                    <th
                      className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"
                      title="Eligible cases with 3+ completed visits among those whose Month 1 visit is due (5-day buffer)"
                    >
                      % 4 Visits On Track
                    </th>
                    <th
                      className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"
                      title="Eligible cases with 4+ completed visits among those whose Month 3 visit is due (5-day buffer)"
                    >
                      % 5 Visits Complete
                    </th>
                    <th
                      className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"
                      title="Eligible cases with 5+ completed visits among those whose Month 6 visit is due (5-day buffer)"
                    >
                      % 6 Visits Complete
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {(dashData?.performance_data || []).map(function (row) {
                    var statusColors = {
                      eligible_for_renewal: '#22c55e',
                      probation: '#eab308',
                      suspended: '#ef4444',
                      none: '#9ca3af',
                    };
                    var color = statusColors[row.status_key] || '#9ca3af';
                    return (
                      <tr key={row.status_key} className="hover:bg-gray-50">
                        <td className="px-3 py-2 whitespace-nowrap">
                          <span className="inline-flex items-center gap-1.5">
                            <span
                              style={{
                                width: 10,
                                height: 10,
                                borderRadius: '50%',
                                backgroundColor: color,
                                display: 'inline-block',
                              }}
                            ></span>
                            <span className="font-medium text-gray-900">
                              {row.status}
                            </span>
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.num_flws}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.total_cases}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.total_cases_eligible_at_registration}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.total_cases_still_eligible}
                        </td>
                        <td
                          className="px-3 py-2 text-right font-medium"
                          style={{
                            color:
                              getEligibleColor(row.pct_still_eligible) ===
                              'green'
                                ? '#22c55e'
                                : getEligibleColor(row.pct_still_eligible) ===
                                  'yellow'
                                ? '#eab308'
                                : '#ef4444',
                          }}
                        >
                          {row.pct_still_eligible}%
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.pct_missed_1_or_less_visits}%
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.pct_4_visits_on_track}%
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.pct_5_visits_complete}%
                        </td>
                        <td className="px-3 py-2 text-right text-gray-700">
                          {row.pct_6_visits_complete}%
                        </td>
                      </tr>
                    );
                  })}
                  {/* Totals row */}
                  {(function () {
                    var perf = dashData?.performance_data || [];
                    if (perf.length === 0) return null;
                    var totals = {
                      num_flws: 0,
                      total_cases: 0,
                      total_cases_eligible_at_registration: 0,
                      total_cases_still_eligible: 0,
                    };
                    perf.forEach(function (r) {
                      totals.num_flws += r.num_flws;
                      totals.total_cases += r.total_cases;
                      totals.total_cases_eligible_at_registration +=
                        r.total_cases_eligible_at_registration;
                      totals.total_cases_still_eligible +=
                        r.total_cases_still_eligible;
                    });
                    var pctStill =
                      totals.total_cases_eligible_at_registration > 0
                        ? Math.round(
                            (totals.total_cases_still_eligible /
                              totals.total_cases_eligible_at_registration) *
                              100,
                          )
                        : 0;
                    // Weighted averages for percentage columns
                    var totalMissedNum = 0;
                    var total4Num = 0;
                    var total4Den = 0;
                    var total5Num = 0;
                    var total5Den = 0;
                    var total6Num = 0;
                    var total6Den = 0;
                    perf.forEach(function (r) {
                      totalMissedNum +=
                        (r.pct_missed_1_or_less_visits *
                          r.total_cases_eligible_at_registration) /
                        100;
                    });
                    var pctMissed =
                      totals.total_cases_eligible_at_registration > 0
                        ? Math.round(
                            (totalMissedNum /
                              totals.total_cases_eligible_at_registration) *
                              100,
                          )
                        : 0;
                    return (
                      <tr className="bg-gray-50 font-semibold border-t-2 border-gray-300">
                        <td className="px-3 py-2 text-gray-900">Total</td>
                        <td className="px-3 py-2 text-right text-gray-900">
                          {totals.num_flws}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-900">
                          {totals.total_cases}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-900">
                          {totals.total_cases_eligible_at_registration}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-900">
                          {totals.total_cases_still_eligible}
                        </td>
                        <td className="px-3 py-2 text-right text-gray-900">
                          {pctStill}%
                        </td>
                        <td className="px-3 py-2 text-right text-gray-900">
                          {pctMissed}%
                        </td>
                        <td className="px-3 py-2 text-right text-gray-500">
                          -
                        </td>
                        <td className="px-3 py-2 text-right text-gray-500">
                          -
                        </td>
                        <td className="px-3 py-2 text-right text-gray-500">
                          -
                        </td>
                      </tr>
                    );
                  })()}
                </tbody>
              </table>
            </div>
            {(!dashData?.performance_data ||
              dashData.performance_data.length === 0) && (
              <div className="px-4 py-8 text-center text-sm text-gray-500">
                No performance data available. Data will appear after the
                dashboard finishes loading.
              </div>
            )}
          </div>

          {/* Monthly Visit Schedule Table */}
          <div
            className="bg-white rounded-lg shadow-sm border border-gray-200 mt-4"
            style={{ overflow: 'clip' }}
          >
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50 flex items-start justify-between">
              <div>
                <h3 className="text-sm font-semibold text-gray-700">
                  <i className="fa-solid fa-calendar-check mr-1"></i> Monthly
                  Visit Schedule
                </h3>
                <p className="text-xs text-gray-500 mt-1">
                  {monthlyViewPct
                    ? 'Completion rate (%) by type and month'
                    : monthlyCountMode === 'completed'
                    ? 'Completed visits by type and month'
                    : monthlyCountMode === 'scheduled'
                    ? 'Total scheduled visits by type and month'
                    : 'Completed vs total scheduled visits by type and month'}{' '}
                  (Sep 2025 &ndash; Jul 2026).
                </p>
              </div>
              <div className="flex items-center gap-1.5 ml-3 mt-0.5">
                {!monthlyViewPct && (
                  <span className="inline-flex rounded border border-gray-300 overflow-hidden">
                    {[
                      { key: 'ratio', label: 'X / Y' },
                      { key: 'completed', label: 'Completed' },
                      { key: 'scheduled', label: 'Scheduled' },
                    ].map(function (opt) {
                      var active = monthlyCountMode === opt.key;
                      return (
                        <button
                          key={opt.key}
                          onClick={function () {
                            setMonthlyCountMode(opt.key);
                          }}
                          className={
                            'px-2 py-1 text-xs font-medium whitespace-nowrap ' +
                            (active
                              ? 'bg-blue-600 text-white'
                              : 'bg-white text-gray-600 hover:bg-gray-100')
                          }
                        >
                          {opt.label}
                        </button>
                      );
                    })}
                  </span>
                )}
                <button
                  onClick={function () {
                    setMonthlyViewPct(!monthlyViewPct);
                  }}
                  className="px-2.5 py-1 text-xs font-medium rounded border border-gray-300 bg-white text-gray-600 hover:bg-gray-100 whitespace-nowrap"
                  title={
                    monthlyViewPct
                      ? 'Switch to counts'
                      : 'Switch to percentages'
                  }
                >
                  {monthlyViewPct ? (
                    <span>
                      <i className="fa-solid fa-hashtag mr-1"></i>Counts
                    </span>
                  ) : (
                    <span>
                      <i className="fa-solid fa-percent mr-1"></i>Percent
                    </span>
                  )}
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table
                data-sticky-header
                className="min-w-full divide-y divide-gray-200 text-sm"
              >
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider sticky left-0 bg-gray-50 z-10">
                      Visit Type
                    </th>
                    {MONTHLY_VISIT_MONTHS.map(function (m) {
                      return (
                        <th
                          key={m}
                          className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap"
                        >
                          {MONTHLY_VISIT_LABELS[m]}
                        </th>
                      );
                    })}
                    <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Total
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {MONTHLY_VISIT_TYPES.map(function (vt) {
                    var rowData = monthlyVisitData[vt] || {};
                    var totalCompleted = 0;
                    var totalAll = 0;
                    MONTHLY_VISIT_MONTHS.forEach(function (m) {
                      totalCompleted += (rowData[m] || {}).completed || 0;
                      totalAll += (rowData[m] || {}).total || 0;
                    });
                    return (
                      <tr key={vt} className="hover:bg-gray-50">
                        <td className="px-3 py-2 whitespace-nowrap font-medium text-gray-900 sticky left-0 bg-white z-10">
                          {vt}
                        </td>
                        {MONTHLY_VISIT_MONTHS.map(function (m) {
                          var cell = rowData[m] || { completed: 0, total: 0 };
                          var display = fmtVisitCell(
                            cell.completed,
                            cell.total,
                          );
                          return (
                            <td
                              key={m}
                              className="px-3 py-2 text-center whitespace-nowrap text-gray-700"
                            >
                              {display === null ? (
                                <span className="text-gray-300">&ndash;</span>
                              ) : (
                                display
                              )}
                            </td>
                          );
                        })}
                        <td className="px-3 py-2 text-center font-semibold whitespace-nowrap text-gray-900">
                          {fmtVisitCell(totalCompleted, totalAll) || (
                            <span className="text-gray-300">&ndash;</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {/* Totals row */}
                  <tr className="bg-gray-50 font-semibold border-t-2 border-gray-300">
                    <td className="px-3 py-2 text-gray-900 sticky left-0 bg-gray-50 z-10">
                      Total
                    </td>
                    {MONTHLY_VISIT_MONTHS.map(function (m) {
                      var colCompleted = 0;
                      var colTotal = 0;
                      MONTHLY_VISIT_TYPES.forEach(function (vt) {
                        var cell = (monthlyVisitData[vt] || {})[m] || {
                          completed: 0,
                          total: 0,
                        };
                        colCompleted += cell.completed;
                        colTotal += cell.total;
                      });
                      return (
                        <td
                          key={m}
                          className="px-3 py-2 text-center whitespace-nowrap text-gray-900"
                        >
                          {fmtVisitCell(colCompleted, colTotal) || (
                            <span className="text-gray-300">&ndash;</span>
                          )}
                        </td>
                      );
                    })}
                    {(function () {
                      var grandCompleted = 0;
                      var grandTotal = 0;
                      MONTHLY_VISIT_TYPES.forEach(function (vt) {
                        MONTHLY_VISIT_MONTHS.forEach(function (m) {
                          var cell = (monthlyVisitData[vt] || {})[m] || {
                            completed: 0,
                            total: 0,
                          };
                          grandCompleted += cell.completed;
                          grandTotal += cell.total;
                        });
                      });
                      return (
                        <td className="px-3 py-2 text-center whitespace-nowrap text-gray-900">
                          {fmtVisitCell(grandCompleted, grandTotal) || (
                            <span className="text-gray-300">&ndash;</span>
                          )}
                        </td>
                      );
                    })()}
                  </tr>
                </tbody>
              </table>
            </div>
            {Object.keys(fuDrilldown).length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-gray-500">
                No visit data available. Data will appear after the dashboard
                finishes loading.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ========== GUIDE TAB ========== */}
      {activeTab === 'guide' && (
        <div className="space-y-3">
          {/* Header */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-5">
            <h2 className="text-lg font-bold text-gray-800">
              <i className="fa-solid fa-book mr-2 text-blue-500"></i>
              MBW Monitoring Dashboard &mdash; Indicators &amp; Columns Guide
            </h2>
            <p className="text-sm text-gray-600 mt-2">
              This guide explains every column and indicator shown in the
              dashboard.
            </p>
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
              <div className="bg-blue-50 rounded px-3 py-2 text-blue-700 font-medium">
                <i className="fa-solid fa-chart-line mr-1"></i> Overview &mdash;
                16 columns
              </div>
              <div className="bg-green-50 rounded px-3 py-2 text-green-700 font-medium">
                <i className="fa-solid fa-location-dot mr-1"></i> GPS Analysis
                &mdash; 9 columns + map
              </div>
              <div className="bg-amber-50 rounded px-3 py-2 text-amber-700 font-medium">
                <i className="fa-solid fa-clipboard-check mr-1"></i> Follow-Up
                Rate &mdash; 6+ columns
              </div>
              <div className="bg-purple-50 rounded px-3 py-2 text-purple-700 font-medium">
                <i className="fa-solid fa-ranking-star mr-1"></i> FLW
                Performance &mdash; 10 columns
              </div>
            </div>
          </div>

          {/* ---- SECTION: Filter Bar ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-filter mr-2 text-blue-500"></i> Filter
                Bar
              </h3>
            </div>
            <div className="p-4 space-y-4 text-sm text-gray-700">
              <p>
                The filter bar above the tabs controls which data is displayed
                across all tabs.
              </p>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Filter
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Default
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Description
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Visit Status</td>
                      <td className="px-3 py-1.5">Approved only</td>
                      <td className="px-3 py-1.5">
                        Filters all tabs by Connect visit approval status:{' '}
                        <strong>Approved</strong>, <strong>Pending</strong>,{' '}
                        <strong>Rejected</strong>, <strong>Over Limit</strong>.
                        Select one or more. Applied server-side &mdash; reuses
                        cached data, no re-download.
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        App Version{' '}
                        <span className="text-gray-400">(GPS only)</span>
                      </td>
                      <td className="px-3 py-1.5">&gt; 14</td>
                      <td className="px-3 py-1.5">
                        Filters GPS visits by app build version. Configurable
                        operator (&gt;, &ge;, =, &le;, &lt;) and version number.
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">FLW filter</td>
                      <td className="px-3 py-1.5">All</td>
                      <td className="px-3 py-1.5">
                        Multi-select list to show only specific FLWs across all
                        tabs. Client-side &mdash; no reload needed.
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Mother filter</td>
                      <td className="px-3 py-1.5">All</td>
                      <td className="px-3 py-1.5">
                        Multi-select list for Follow-Up tab mother drill-down.
                        Client-side.
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p>
                <strong>Apply</strong> sends Visit Status and App Version
                changes to the server. <strong>Reset</strong> restores defaults
                (Approved only, &gt; 14). Changing Visit Status does{' '}
                <em>not</em> re-download from Connect &mdash; it reuses cached
                data and applies the filter via SQL, so it completes in seconds.
              </p>
            </div>
          </div>

          {/* ---- SECTION: Tab 1 Overview ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-chart-line mr-2 text-blue-500"></i>{' '}
                Tab 1: Overview
              </h3>
            </div>
            <div className="p-4 space-y-4 text-sm text-gray-700">
              <p>
                The Overview tab provides a single table with one row per FLW.
                Each column summarizes a different dimension of performance.
              </p>

              {/* # Mothers */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800"># Mothers</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Total unique mothers
                  registered by this FLW, with eligible count in parentheses.
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> Counts unique
                  mother case IDs from &quot;Register Mother&quot; forms.
                  Parenthesized count = mothers with{' '}
                  <code className="bg-gray-100 px-1 rounded text-xs">
                    eligible_full_intervention_bonus = &quot;1&quot;
                  </code>
                  .
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Mother count: unique{' '}
                    <code>form.var_visit_1..6.mother_case_id</code> per FLW
                  </div>
                  <div>
                    Eligible: <code>form.eligible_full_intervention_bonus</code>{' '}
                    = &quot;1&quot;
                  </div>
                </div>
              </div>

              {/* Last Active */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Last Active</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Number of days since the FLW
                  was last active on the Connect platform.
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> Uses the{' '}
                  <code className="bg-gray-100 px-1 rounded text-xs">
                    last_active
                  </code>{' '}
                  field from Connect user data &mdash; the date of the
                  FLW&apos;s most recent form submission or module completion.
                  Displayed as &quot;Xd ago&quot;.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data source:</strong>
                  </div>
                  <div>
                    Connect user export: <code>last_active</code> (DateTimeField
                    on OpportunityAccess)
                  </div>
                </div>
                <div className="mt-2 flex gap-2 text-xs">
                  <span className="bg-green-100 text-green-800 px-2 py-0.5 rounded">
                    &le;7 days Green
                  </span>
                  <span className="bg-yellow-100 text-yellow-800 px-2 py-0.5 rounded">
                    8&ndash;15 days Yellow
                  </span>
                  <span className="bg-red-100 text-red-800 px-2 py-0.5 rounded">
                    &gt;15 days Red
                  </span>
                </div>
              </div>

              {/* GS Score */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">GS Score</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> The FLW&apos;s Gold Standard
                  Visit Checklist score (%).
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> A supervisor
                  completes a checklist form while observing the FLW. The
                  dashboard shows the <strong>first (oldest)</strong> GS score
                  on record.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Score: <code>form.checklist_percentage</code> (0&ndash;100)
                  </div>
                  <div>
                    FLW identity: <code>form.load_flw_connect_id</code>
                  </div>
                  <div>
                    Ordering: <code>form.meta.timeEnd</code> (oldest first)
                  </div>
                </div>
                <div className="mt-2 flex gap-2 text-xs">
                  <span className="bg-green-100 text-green-800 px-2 py-0.5 rounded">
                    &ge;70% Green
                  </span>
                  <span className="bg-yellow-100 text-yellow-800 px-2 py-0.5 rounded">
                    50&ndash;69% Yellow
                  </span>
                  <span className="bg-red-100 text-red-800 px-2 py-0.5 rounded">
                    &lt;50% Red
                  </span>
                </div>
              </div>

              {/* Follow-up Rate */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Follow-up Rate</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Percentage of scheduled visits
                  completed, considering only eligible mothers with a 5-day
                  grace period.
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> (completed visits /
                  total visits due 5+ days ago for eligible mothers) &times;
                  100.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Scheduled dates:{' '}
                    <code>form.var_visit_1..6.visit_date_scheduled</code>
                  </div>
                  <div>
                    Expiry dates:{' '}
                    <code>form.var_visit_1..6.visit_expiry_date</code>
                  </div>
                  <div>
                    Visit type: <code>form.var_visit_1..6.visit_type</code>
                  </div>
                  <div>
                    Eligibility:{' '}
                    <code>form.eligible_full_intervention_bonus</code> =
                    &quot;1&quot;
                  </div>
                  <div>
                    Completion: <code>form.@name</code> mapped via completion
                    flags
                  </div>
                </div>
                <div className="mt-2 flex gap-2 text-xs">
                  <span className="bg-green-100 text-green-800 px-2 py-0.5 rounded">
                    &ge;80% Green
                  </span>
                  <span className="bg-yellow-100 text-yellow-800 px-2 py-0.5 rounded">
                    60&ndash;79% Yellow
                  </span>
                  <span className="bg-red-100 text-red-800 px-2 py-0.5 rounded">
                    &lt;60% Red
                  </span>
                </div>
              </div>

              {/* Eligible 5+ */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Eligible 5+</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Among eligible mothers, how
                  many are &quot;still on track&quot; (count and %).
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> A mother is on
                  track if she has <strong>5+ completed visits</strong> OR{' '}
                  <strong>&le;1 missed visit</strong>.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Eligibility:{' '}
                    <code>form.eligible_full_intervention_bonus</code> =
                    &quot;1&quot;
                  </div>
                  <div>
                    Completed: visits with status starting with
                    &quot;Completed&quot;
                  </div>
                  <div>
                    Missed: visits past{' '}
                    <code>form.var_visit_N.visit_expiry_date</code>
                  </div>
                </div>
                <div className="mt-2 flex gap-2 text-xs">
                  <span className="bg-green-100 text-green-800 px-2 py-0.5 rounded">
                    &ge;85% Green
                  </span>
                  <span className="bg-yellow-100 text-yellow-800 px-2 py-0.5 rounded">
                    50&ndash;84% Yellow
                  </span>
                  <span className="bg-red-100 text-red-800 px-2 py-0.5 rounded">
                    &lt;50% Red
                  </span>
                </div>
              </div>

              {/* % EBF */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">
                  % EBF (Exclusive Breastfeeding)
                </h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Percentage of postnatal visits
                  reporting exclusive breastfeeding.
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> (EBF visits / total
                  visits with breastfeeding data) &times; 100. Rates too low may
                  indicate counseling gaps; rates above 95% may indicate
                  fabrication.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths</strong> (multi-choice field &mdash;
                    &quot;ebf&quot; token = exclusive):
                  </div>
                  <div>
                    <code>form.feeding_history.pnc_current_bf_status</code>
                  </div>
                  <div>
                    <code>form.feeding_history.oneweek_current_bf_status</code>
                  </div>
                  <div>
                    <code>form.feeding_history.onemonth_current_bf_status</code>
                  </div>
                  <div>
                    <code>
                      form.feeding_history.threemonth_current_bf_status
                    </code>
                  </div>
                  <div>
                    <code>form.feeding_history.sixmonth_current_bf_status</code>
                  </div>
                </div>
                <div className="mt-2 flex gap-2 text-xs">
                  <span className="bg-green-100 text-green-800 px-2 py-0.5 rounded">
                    50&ndash;85% Green
                  </span>
                  <span className="bg-yellow-100 text-yellow-800 px-2 py-0.5 rounded">
                    31&ndash;49% or 86&ndash;95% Yellow
                  </span>
                  <span className="bg-red-100 text-red-800 px-2 py-0.5 rounded">
                    &le;30% or &gt;95% Red
                  </span>
                </div>
              </div>

              {/* Revisit Dist. */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Revisit Dist.</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Average distance (km) between
                  successive GPS coordinates when the FLW revisits the{' '}
                  <strong>same mother</strong>. Number in parentheses (N) =
                  distinct mothers with 2+ GPS visits (the denominator for the
                  average).
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> Group visits by
                  mother case ID, sort by time, calculate Haversine distance
                  between consecutive visits, then average across all pairs.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    GPS: <code>form.meta.location</code> or{' '}
                    <code>form.meta.location.#text</code>
                  </div>
                  <div>
                    Mother case ID:{' '}
                    <code>form.parents.parent.case.@case_id</code>
                  </div>
                  <div>
                    Ordering: <code>form.meta.timeEnd</code>
                  </div>
                </div>
              </div>

              {/* Meter/Visit */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Meter/Visit</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Median distance (meters)
                  between consecutive visits to{' '}
                  <strong>different mothers</strong> within a single day.
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> Per working day:
                  list visits chronologically, keep first per mother, require 2+
                  unique mothers, calculate distances between consecutive pairs,
                  take median across all days.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    GPS: <code>form.meta.location</code> or{' '}
                    <code>form.meta.location.#text</code>
                  </div>
                  <div>
                    Ordering: <code>form.meta.timeEnd</code>
                  </div>
                  <div>
                    Dedup: <code>form.parents.parent.case.@case_id</code>
                  </div>
                  <div>
                    Version filter: <code>form.meta.app_build_version</code>
                  </div>
                </div>
                <div className="mt-2 text-xs">
                  <span className="bg-red-100 text-red-800 px-2 py-0.5 rounded">
                    Red flag: &lt;100 meters
                  </span>
                </div>
              </div>

              {/* Dist. Ratio */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Dist. Ratio</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Ratio of revisit distance (km)
                  to meter per visit (km). A dimensionless number.
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong>{' '}
                  <code>Revisit Dist. &times; 1000 / Meter/Visit</code>. For
                  example, if revisit distance is 2 km and meter/visit is 500 m,
                  the ratio is 4.0.
                </p>
                <p className="mt-1">
                  <strong>Why it matters:</strong> A high ratio means revisit
                  distance is disproportionately large compared to daily
                  inter-visit travel &mdash; could indicate GPS spoofing or
                  fabricated revisits. Values close to 1.0 are expected.
                </p>
              </div>

              {/* Minute/Visit */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Minute/Visit</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Median time gap (minutes)
                  between consecutive visits to different mothers within a day.
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> Same grouping as
                  Meter/Visit, but calculates time difference between
                  consecutive form submissions instead of distance.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Submission time: <code>form.meta.timeEnd</code>
                  </div>
                  <div>
                    Dedup: <code>form.parents.parent.case.@case_id</code>
                  </div>
                </div>
              </div>

              {/* Phone Dup % */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Phone Dup %</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Percentage of mothers whose
                  phone numbers appear more than once across the FLW&apos;s
                  caseload.
                </p>
                <p className="mt-1">
                  <strong>Why it matters:</strong> High duplicate rates across
                  different mothers may indicate fabricated registrations.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Phone: <code>form.mother_details.phone_number</code>
                  </div>
                  <div>
                    Fallback:{' '}
                    <code>form.mother_details.back_up_phone_number</code>
                  </div>
                </div>
              </div>

              {/* ANC = PNC */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">ANC = PNC</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Number of mothers where ANC
                  and PNC completion dates fall on the <strong>same day</strong>
                  .
                </p>
                <p className="mt-1">
                  <strong>Why it matters:</strong> ANC (during pregnancy) and
                  PNC (after delivery) on the same day is biologically
                  impossible &mdash; strongly suggests data fabrication.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    ANC date:{' '}
                    <code>form.visit_completion.anc_completion_date</code>
                  </div>
                  <div>
                    PNC date: <code>form.pnc_completion_date</code>
                  </div>
                </div>
              </div>

              {/* Parity */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Parity</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> How concentrated (repetitive)
                  the parity values are across the FLW&apos;s mothers. Parity =
                  number of times a woman has given birth (live births or
                  stillbirths after 24 weeks).
                </p>
                <p className="mt-1">
                  <strong>How it&apos;s calculated:</strong> (mothers with
                  duplicate parity values / total with parity data) &times; 100.
                  Also shows the mode value and its percentage.
                </p>
                <p className="mt-1">
                  <strong>Why it matters:</strong> A natural population should
                  have diverse parity values. High concentration of a single
                  value may indicate copy-pasting or fabrication.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data path:</strong>
                  </div>
                  <div>
                    <code>
                      form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks
                    </code>
                  </div>
                </div>
              </div>

              {/* Age */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Age</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> How concentrated (repetitive)
                  the age values are across the FLW&apos;s mothers. Same logic
                  as Parity.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Primary: <code>form.mother_details.mother_dob</code> (age =
                    today &minus; DOB)
                  </div>
                  <div>
                    Fallback 1:{' '}
                    <code>form.mother_details.age_in_years_rounded</code>
                  </div>
                  <div>
                    Fallback 2: <code>form.mother_details.mothers_age</code>
                  </div>
                </div>
              </div>

              {/* Age = Reg */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Age = Reg</h4>
                <p className="mt-1">
                  <strong>What it shows:</strong> Percentage of mothers whose
                  date of birth has the <strong>same month and day</strong> as
                  their registration date.
                </p>
                <p className="mt-1">
                  <strong>Why it matters:</strong> Statistically very unlikely
                  &mdash; suggests the FLW entered the registration date as the
                  DOB instead of asking.
                </p>
                <div className="mt-2 bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                  <div>
                    <strong>Data paths:</strong>
                  </div>
                  <div>
                    Mother DOB: <code>form.mother_details.mother_dob</code>
                  </div>
                  <div>
                    Registration date: <code>received_on</code> (fallback:{' '}
                    <code>metadata.timeEnd</code>)
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="border-l-4 border-blue-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Actions</h4>
                <p className="mt-1">
                  Interactive buttons per FLW: assessment (Eligible for Renewal
                  / Probation / Suspended), notes, filter, and task creation
                  (with optional AI via OCS).
                </p>
              </div>
            </div>
          </div>

          {/* ---- SECTION: Tab 2 GPS Analysis ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-location-dot mr-2 text-green-500"></i>{' '}
                Tab 2: GPS Analysis
              </h3>
            </div>
            <div className="p-4 space-y-4 text-sm text-gray-700">
              <p>
                The GPS Analysis tab focuses on geographic patterns to detect
                suspicious travel behavior.
              </p>

              <div className="bg-blue-50 rounded p-3 text-xs">
                <div className="font-semibold text-blue-800 mb-1">
                  Shared Data Paths (all GPS columns):
                </div>
                <div className="font-mono leading-relaxed text-blue-900">
                  <div>
                    GPS: <code>form.meta.location</code> or{' '}
                    <code>form.meta.location.#text</code> (&quot;lat lon alt
                    accuracy&quot;)
                  </div>
                  <div>
                    Visit datetime: <code>form.meta.timeEnd</code>
                  </div>
                  <div>
                    Mother case ID:{' '}
                    <code>form.parents.parent.case.@case_id</code>
                  </div>
                  <div>
                    Case ID: <code>form.case.@case_id</code>
                  </div>
                  <div>
                    App version: <code>form.meta.app_build_version</code>
                  </div>
                  <div>
                    Form name: <code>form.@name</code>
                  </div>
                </div>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                Summary Cards
              </h4>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Card
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Description
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Total Visits</td>
                      <td className="px-3 py-1.5">
                        Form submissions within selected date range
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Flagged Visits
                      </td>
                      <td className="px-3 py-1.5">
                        Visits where distance from previous visit to same mother
                        &gt; 5 km
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Date Range</td>
                      <td className="px-3 py-1.5">
                        Selected date range for analysis
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Flag Threshold
                      </td>
                      <td className="px-3 py-1.5">5 km</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                FLW Table Columns
              </h4>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">With GPS</h4>
                <p className="mt-1">
                  Count and percentage of visits with parseable GPS coordinates.
                  Shown as &quot;X (Y%)&quot;.
                </p>
              </div>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Flagged</h4>
                <p className="mt-1">
                  Visits where distance between consecutive visits to the{' '}
                  <strong>same mother</strong> exceeds 5 km. Red text when any
                  are flagged.
                </p>
              </div>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Unique Cases</h4>
                <p className="mt-1">
                  Distinct mother cases visited. Data path: count of unique{' '}
                  <code className="bg-gray-100 px-1 rounded text-xs">
                    form.case.@case_id
                  </code>
                  .
                </p>
              </div>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Revisit Dist.</h4>
                <p className="mt-1">
                  Average Haversine distance (km) between successive visits to
                  the <strong>same mother</strong>. Number in parentheses (N) =
                  distinct mothers with 2+ GPS visits (the denominator for the
                  average). Same metric as the Overview tab.
                </p>
              </div>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Meter/Visit</h4>
                <p className="mt-1">
                  Median Haversine distance (meters) between consecutive visits
                  to <strong>different mothers</strong> within a single day.
                  Same metric as the Overview tab. Color-coded: green &ge;1000
                  m, yellow &ge;100 m, red &lt;100 m.
                </p>
              </div>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Dist. Ratio</h4>
                <p className="mt-1">
                  Ratio of Revisit Dist. to Meter/Visit:{' '}
                  <code className="bg-gray-100 px-1 rounded text-xs">
                    (revisit_km &times; 1000) / meter_per_visit
                  </code>
                  . High values (e.g. &gt;5) indicate the revisit distance is
                  disproportionately large vs. daily travel.
                </p>
              </div>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">
                  Max Revisit Dist.
                </h4>
                <p className="mt-1">
                  Largest Haversine distance (km) between consecutive visits to
                  the same mother. Red and bold when &gt; 5 km.
                </p>
              </div>

              <div className="border-l-4 border-green-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Trailing 7 Days</h4>
                <p className="mt-1">
                  Sparkline bar chart showing daily travel over the last 7 days.
                  Each bar = total path distance that day (sum of distances
                  between consecutive visit locations).
                </p>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                Aggregate Map
              </h4>
              <p>
                A collapsible map at the top of the GPS tab showing{' '}
                <strong>all FLW visits</strong> on a single map. Each FLW is
                assigned a unique color (HSL hue rotation). Markers are
                clustered at higher zoom levels for performance. Click markers
                for visit details (FLW name, entity, date, flagged status).
                Flagged visits appear as red markers regardless of FLW color.
              </p>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                GPS Drill-Down
              </h4>
              <p>
                Clicking &quot;Details&quot; shows individual visit records
                with: Date, Form type, Entity (mother name), GPS coordinates,
                Revisit Dist. (distance from previous visit to same mother), and
                Flag status (&gt; 5 km). An interactive map shows visit and
                mother locations.
              </p>
            </div>
          </div>

          {/* ---- SECTION: Tab 3 Follow-Up Rate ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-clipboard-check mr-2 text-amber-500"></i>{' '}
                Tab 3: Follow-Up Rate
              </h3>
            </div>
            <div className="p-4 space-y-4 text-sm text-gray-700">
              <p>
                The Follow-Up Rate tab tracks whether each FLW is completing
                their scheduled visits on time.
              </p>

              <div className="bg-amber-50 rounded p-3 text-xs">
                <div className="font-semibold text-amber-800 mb-1">
                  Key Data Paths (two data sources merged):
                </div>
                <div className="font-mono leading-relaxed text-amber-900">
                  <div className="font-semibold mt-1">
                    From &quot;Register Mother&quot; forms (CCHQ) &mdash;
                    expected visits:
                  </div>
                  <div>
                    &bull; <code>form.var_visit_1..6.visit_type</code>,{' '}
                    <code>.visit_date_scheduled</code>,{' '}
                    <code>.visit_expiry_date</code>,{' '}
                    <code>.mother_case_id</code>
                  </div>
                  <div>
                    &bull; Create flags:{' '}
                    <code>form.var_visit_N.create_antenatal_visit</code>,{' '}
                    <code>create_postnatal_visit</code>, etc. = &quot;1&quot;
                  </div>
                  <div>
                    &bull; Eligibility:{' '}
                    <code>form.eligible_full_intervention_bonus</code> =
                    &quot;1&quot;
                  </div>
                  <div className="font-semibold mt-2">
                    From visit forms (Connect API) &mdash; completed visits:
                  </div>
                  <div>
                    &bull; <code>form.@name</code> mapped to visit type
                  </div>
                  <div>
                    &bull; <code>form.parents.parent.case.@case_id</code>{' '}
                    (mother link)
                  </div>
                  <div>
                    &bull; Completion flags:{' '}
                    <code>antenatal_visit_completion</code>,{' '}
                    <code>postnatal_visit_completion</code>, etc.
                  </div>
                </div>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                FLW Table Columns
              </h4>

              <div className="border-l-4 border-amber-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Follow-up Rate</h4>
                <p className="mt-1">
                  Same as Overview: (completed / due 5+ days ago for eligible
                  mothers) &times; 100. Shown with colored progress bar.
                </p>
              </div>
              <div className="border-l-4 border-amber-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">
                  Completed / Due / Missed
                </h4>
                <p className="mt-1">
                  <strong>Completed:</strong> Both on-time and late, with % of
                  total. <strong>Due:</strong> Not yet completed but before
                  expiry. <strong>Missed:</strong> Past expiry, never completed.
                </p>
              </div>
              <div className="border-l-4 border-amber-200 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">
                  Per-Visit-Type Breakdown
                </h4>
                <p className="mt-1">
                  Six mini-columns (ANC, Postnatal, Week 1, Month 1, Month 3,
                  Month 6) showing completed/due/missed counts individually.
                </p>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                Visit Status Definitions
              </h4>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Status
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Meaning
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Completed - On Time
                      </td>
                      <td className="px-3 py-1.5">
                        Within on-time window (7 days; 4 for Postnatal)
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Completed - Late
                      </td>
                      <td className="px-3 py-1.5">
                        After on-time window but before expiry
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Due - On Time</td>
                      <td className="px-3 py-1.5">
                        Not completed, within on-time window
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Due - Late</td>
                      <td className="px-3 py-1.5">
                        Not completed, past on-time but before expiry
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Missed</td>
                      <td className="px-3 py-1.5">
                        Past expiry, will never be completed
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Not Due Yet</td>
                      <td className="px-3 py-1.5">
                        Scheduled date hasn&apos;t arrived
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                On-Time Windows
              </h4>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Visit Type
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Window
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    <tr>
                      <td className="px-3 py-1.5">ANC Visit</td>
                      <td className="px-3 py-1.5">
                        7 days from scheduled date
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Postnatal / Post Delivery</td>
                      <td className="px-3 py-1.5">
                        <strong>4 days</strong> from delivery (clinical urgency)
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">1 Week Visit</td>
                      <td className="px-3 py-1.5">7 days</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">1 Month Visit</td>
                      <td className="px-3 py-1.5">7 days</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">3 Month Visit</td>
                      <td className="px-3 py-1.5">7 days</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">6 Month Visit</td>
                      <td className="px-3 py-1.5">7 days</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                Mother Drill-Down Fields
              </h4>
              <p>Clicking an FLW row expands per-mother details:</p>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Field
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Data Path
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Source
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    <tr>
                      <td className="px-3 py-1.5">Mother name</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>form.mother_details.format_mother_name</code>
                      </td>
                      <td className="px-3 py-1.5">Register Mother</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Age</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>form.mother_details.mother_dob</code>
                      </td>
                      <td className="px-3 py-1.5">Register Mother</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Phone</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>form.mother_details.phone_number</code>
                      </td>
                      <td className="px-3 py-1.5">Register Mother</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Registration date</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>received_on</code>
                      </td>
                      <td className="px-3 py-1.5">Register Mother</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Household size</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>form.number_of_other_household_members</code>
                      </td>
                      <td className="px-3 py-1.5">Register Mother</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Preferred visit time</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>form.var_visit_1.preferred_visit_time</code>
                      </td>
                      <td className="px-3 py-1.5">Register Mother</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">ANC completion</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>form.visit_completion.anc_completion_date</code>
                      </td>
                      <td className="px-3 py-1.5">ANC Visit</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">PNC completion</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>form.pnc_completion_date</code>
                      </td>
                      <td className="px-3 py-1.5">Post Delivery Visit</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Expected delivery</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>
                          form.mother_birth_outcome.expected_delivery_date
                        </code>
                      </td>
                      <td className="px-3 py-1.5">Register Mother</td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5">Baby DOB</td>
                      <td className="px-3 py-1.5 font-mono">
                        <code>
                          form.capture_the_following_birth_details.baby_dob
                        </code>
                      </td>
                      <td className="px-3 py-1.5">Post Delivery Visit</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ---- SECTION: Tab 4 FLW Performance ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-ranking-star mr-2 text-purple-500"></i>{' '}
                Tab 4: FLW Performance
              </h3>
            </div>
            <div className="p-4 space-y-4 text-sm text-gray-700">
              <p>
                Aggregates case-level metrics grouped by each FLW&apos;s latest
                assessment status.
              </p>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                Assessment Status Categories
              </h4>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Status
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Color
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Meaning
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Eligible for Renewal
                      </td>
                      <td className="px-3 py-1.5">
                        <span className="inline-block w-3 h-3 rounded-full bg-green-500 mr-1"></span>{' '}
                        Green
                      </td>
                      <td className="px-3 py-1.5">
                        Good performance, eligible for contract renewal
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Probation</td>
                      <td className="px-3 py-1.5">
                        <span className="inline-block w-3 h-3 rounded-full bg-yellow-500 mr-1"></span>{' '}
                        Yellow
                      </td>
                      <td className="px-3 py-1.5">
                        Underperforming, not eligible for renewal
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">Suspended</td>
                      <td className="px-3 py-1.5">
                        <span className="inline-block w-3 h-3 rounded-full bg-red-500 mr-1"></span>{' '}
                        Red
                      </td>
                      <td className="px-3 py-1.5">
                        Evidence of fraud or severe deficiencies
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">No Category</td>
                      <td className="px-3 py-1.5">
                        <span className="inline-block w-3 h-3 rounded-full bg-gray-400 mr-1"></span>{' '}
                        Gray
                      </td>
                      <td className="px-3 py-1.5">Not yet assessed</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2 font-mono text-xs">
                <strong>Data path:</strong>{' '}
                <code>
                  run.data.state.worker_results.&#123;username&#125;.result
                </code>{' '}
                (most recent assessment)
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                Performance Table Columns
              </h4>
              <div className="space-y-3">
                <div className="border-l-4 border-purple-200 pl-4 py-2">
                  <h4 className="font-semibold text-gray-800">
                    # FLWs / Total Cases / Eligible at Reg
                  </h4>
                  <p className="mt-1">
                    Count of FLWs in status group, total registered mothers, and
                    mothers with{' '}
                    <code className="bg-gray-100 px-1 rounded text-xs">
                      eligible_full_intervention_bonus = &quot;1&quot;
                    </code>
                    .
                  </p>
                </div>
                <div className="border-l-4 border-purple-200 pl-4 py-2">
                  <h4 className="font-semibold text-gray-800">
                    Still Eligible / % Still Eligible
                  </h4>
                  <p className="mt-1">
                    Among eligible mothers: those with 5+ completed visits OR
                    &le;1 missed visit. Percentage = (still eligible / eligible
                    at reg) &times; 100.
                  </p>
                  <div className="mt-2 flex gap-2 text-xs">
                    <span className="bg-green-100 text-green-800 px-2 py-0.5 rounded">
                      &ge;85% Green
                    </span>
                    <span className="bg-yellow-100 text-yellow-800 px-2 py-0.5 rounded">
                      50&ndash;84% Yellow
                    </span>
                    <span className="bg-red-100 text-red-800 px-2 py-0.5 rounded">
                      &lt;50% Red
                    </span>
                  </div>
                </div>
                <div className="border-l-4 border-purple-200 pl-4 py-2">
                  <h4 className="font-semibold text-gray-800">
                    % &le;1 Missed
                  </h4>
                  <p className="mt-1">
                    Percentage of <strong>eligible</strong> mothers (
                    <code>eligible_full_intervention_bonus = "1"</code>) with 0
                    or 1 missed visits.
                  </p>
                </div>
                <div className="border-l-4 border-purple-200 pl-4 py-2">
                  <h4 className="font-semibold text-gray-800">
                    % 4 Visits On Track
                  </h4>
                  <p className="mt-1">
                    Among <strong>eligible</strong> mothers whose Month 1 visit
                    is due (5-day grace): % with 3+ completed visits.
                  </p>
                  <div className="mt-1 bg-gray-50 rounded px-3 py-1 font-mono text-xs">
                    Denominator: <code>visit_date_scheduled</code> for &quot;1
                    Month Visit&quot; &le; today &minus; 5 days
                  </div>
                </div>
                <div className="border-l-4 border-purple-200 pl-4 py-2">
                  <h4 className="font-semibold text-gray-800">
                    % 5 Visits Complete
                  </h4>
                  <p className="mt-1">
                    Among <strong>eligible</strong> mothers whose Month 3 visit
                    is due: % with 4+ completed visits.
                  </p>
                  <div className="mt-1 bg-gray-50 rounded px-3 py-1 font-mono text-xs">
                    Denominator: <code>visit_date_scheduled</code> for &quot;3
                    Month Visit&quot; &le; today &minus; 5 days
                  </div>
                </div>
                <div className="border-l-4 border-purple-200 pl-4 py-2">
                  <h4 className="font-semibold text-gray-800">
                    % 6 Visits Complete
                  </h4>
                  <p className="mt-1">
                    Among <strong>eligible</strong> mothers whose Month 6 visit
                    is due: % with 5+ completed visits.
                  </p>
                  <div className="mt-1 bg-gray-50 rounded px-3 py-1 font-mono text-xs">
                    Denominator: <code>visit_date_scheduled</code> for &quot;6
                    Month Visit&quot; &le; today &minus; 5 days
                  </div>
                </div>
              </div>

              <h4 className="font-semibold text-gray-800 border-b border-gray-200 pb-1">
                Monthly Visit Schedule Sub-Table
              </h4>
              <p>
                Below the performance table, a second table shows visit
                completion rates by <strong>visit type</strong> and{' '}
                <strong>month</strong>.
              </p>
              <ul className="list-disc pl-5 space-y-1 text-xs">
                <li>
                  <strong>Rows:</strong> One per visit type (ANC, Postnatal,
                  Week 1, Month 1, Month 3, Month 6) + Totals
                </li>
                <li>
                  <strong>Columns:</strong> One per month + Total column
                </li>
                <li>
                  <strong>Display modes</strong> (toggle buttons): X/Y ratio,
                  Completed only, Scheduled only, % Percent
                </li>
              </ul>
              <div className="bg-gray-50 rounded px-3 py-2 font-mono text-xs leading-relaxed">
                <div>
                  <strong>Data paths:</strong>
                </div>
                <div>
                  Visit type: <code>form.var_visit_N.visit_type</code>
                </div>
                <div>
                  Month bucket:{' '}
                  <code>form.var_visit_N.visit_date_scheduled</code>
                </div>
                <div>
                  Completion: pipeline form submissions matched via completion
                  flags
                </div>
              </div>
            </div>
          </div>

          {/* ---- SECTION: Red Flag Indicators ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-triangle-exclamation mr-2 text-red-500"></i>{' '}
                Red Flag Indicators
              </h3>
            </div>
            <div className="p-4 text-sm text-gray-700">
              <p className="mb-3">
                When creating a task for an FLW (via OCS AI), the system
                automatically detects these red flags and includes them in the
                AI prompt.
              </p>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Red Flag
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        Threshold
                      </th>
                      <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                        What It Means
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Low Gold Standard Score
                      </td>
                      <td className="px-3 py-1.5">&lt; 50%</td>
                      <td className="px-3 py-1.5">
                        Poorly performed on supervised assessment
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Low Follow-Up Visit Rate
                      </td>
                      <td className="px-3 py-1.5">&lt; 50%</td>
                      <td className="px-3 py-1.5">
                        More than half of due visits incomplete
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Low Case Eligibility Rate
                      </td>
                      <td className="px-3 py-1.5">Eligible 5+ &lt; 50%</td>
                      <td className="px-3 py-1.5">
                        Most eligible mothers are off track
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Low Travel Distance
                      </td>
                      <td className="px-3 py-1.5">Meter/Visit &lt; 100m</td>
                      <td className="px-3 py-1.5">
                        Forms submitted from same location
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        High Phone Duplicate Rate
                      </td>
                      <td className="px-3 py-1.5">Phone Dup &gt; 30%</td>
                      <td className="px-3 py-1.5">
                        Too many mothers sharing phone numbers
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        ANC/PNC Same-Date
                      </td>
                      <td className="px-3 py-1.5">ANC=PNC &ge; 5</td>
                      <td className="px-3 py-1.5">
                        Multiple biologically impossible same-day completions
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3 py-1.5 font-medium">
                        Abnormal EBF Rate
                      </td>
                      <td className="px-3 py-1.5">&le; 30% or &gt; 95%</td>
                      <td className="px-3 py-1.5">
                        Breastfeeding rate outside expected range
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ---- SECTION: Color Coding Reference ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-palette mr-2 text-indigo-500"></i>{' '}
                Color Coding Reference
              </h3>
            </div>
            <div className="p-4 space-y-4 text-sm text-gray-700">
              <div>
                <h4 className="font-semibold text-gray-800 mb-2">
                  Follow-Up Rate / GS Score
                </h4>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="bg-green-100 text-green-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Green</div>Follow-up &ge;80%
                    | GS &ge;70%
                  </div>
                  <div className="bg-yellow-100 text-yellow-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Yellow</div>Follow-up
                    60&ndash;79% | GS 50&ndash;69%
                  </div>
                  <div className="bg-red-100 text-red-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Red</div>Follow-up &lt;60% |
                    GS &lt;50%
                  </div>
                </div>
              </div>
              <div>
                <h4 className="font-semibold text-gray-800 mb-2">% EBF</h4>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="bg-green-100 text-green-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Green</div>50&ndash;85%
                  </div>
                  <div className="bg-yellow-100 text-yellow-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Yellow</div>31&ndash;49% or
                    86&ndash;95%
                  </div>
                  <div className="bg-red-100 text-red-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Red</div>&le;30% or &gt;95%
                  </div>
                </div>
              </div>
              <div>
                <h4 className="font-semibold text-gray-800 mb-2">
                  Eligible 5+ / % Still Eligible
                </h4>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="bg-green-100 text-green-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Green</div>&ge;85%
                  </div>
                  <div className="bg-yellow-100 text-yellow-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Yellow</div>50&ndash;84%
                  </div>
                  <div className="bg-red-100 text-red-800 rounded px-3 py-2 text-center">
                    <div className="font-semibold">Red</div>&lt;50%
                  </div>
                </div>
              </div>
              <div>
                <h4 className="font-semibold text-gray-800 mb-2">GPS Flags</h4>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="bg-red-50 text-red-800 rounded px-3 py-2 text-center">
                    Revisit to same mother &gt; 5 km
                  </div>
                  <div className="bg-red-50 text-red-800 rounded px-3 py-2 text-center">
                    Max Revisit Dist. &gt; 5 km
                  </div>
                  <div className="bg-red-50 text-red-800 rounded px-3 py-2 text-center">
                    Meter/Visit &lt; 100 m
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* ---- SECTION: Key Definitions ---- */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 rounded-t-lg">
              <h3 className="text-sm font-semibold text-gray-700">
                <i className="fa-solid fa-circle-info mr-2 text-gray-500"></i>{' '}
                Key Definitions
              </h3>
            </div>
            <div className="p-4 space-y-4 text-sm text-gray-700">
              <div className="border-l-4 border-gray-300 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Eligibility</h4>
                <p className="mt-1">
                  A mother is &quot;eligible for the full intervention
                  bonus&quot; if{' '}
                  <code className="bg-gray-100 px-1 rounded text-xs">
                    eligible_full_intervention_bonus = &quot;1&quot;
                  </code>{' '}
                  in her registration form. Set at registration, does not
                  change. Follow-up rate and Performance tab metrics only count
                  eligible mothers.
                </p>
              </div>
              <div className="border-l-4 border-gray-300 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">
                  Grace Period (5 days)
                </h4>
                <p className="mt-1">
                  The follow-up rate only counts visits whose scheduled date was
                  5+ days ago. This gives FLWs a reasonable window to complete
                  recent visits before they affect their score.
                </p>
              </div>
              <div className="border-l-4 border-gray-300 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">
                  Haversine Distance
                </h4>
                <p className="mt-1">
                  Straight-line distance between two GPS points on Earth&apos;s
                  surface, accounting for curvature (radius = 6,371 km). Used
                  for all distance calculations. Real travel distances are
                  longer, but Haversine provides a consistent baseline.
                </p>
              </div>
              <div className="border-l-4 border-gray-300 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">
                  Assessment Status
                </h4>
                <p className="mt-1">
                  Result assigned during monitoring or audit:{' '}
                  <strong>Eligible for Renewal</strong> (good),{' '}
                  <strong>Probation</strong> (underperforming),{' '}
                  <strong>Suspended</strong> (fraud/severe issues &mdash; label
                  only, no platform action). Dashboard uses the most recent
                  assessment.
                </p>
                <div className="mt-1 bg-gray-50 rounded px-3 py-1 font-mono text-xs">
                  <code>
                    run.data.state.worker_results.&#123;username&#125;.result
                  </code>
                </div>
              </div>
              <div className="border-l-4 border-gray-300 pl-4 py-2">
                <h4 className="font-semibold text-gray-800">Visit Types</h4>
                <div className="mt-2 overflow-x-auto">
                  <table className="min-w-full text-xs">
                    <thead>
                      <tr className="bg-gray-50">
                        <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                          Visit
                        </th>
                        <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                          When
                        </th>
                        <th className="px-3 py-1.5 text-left font-medium text-gray-600">
                          Purpose
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      <tr>
                        <td className="px-3 py-1.5 font-medium">ANC Visit</td>
                        <td className="px-3 py-1.5">~28 weeks of pregnancy</td>
                        <td className="px-3 py-1.5">
                          Antenatal care assessment
                        </td>
                      </tr>
                      <tr>
                        <td className="px-3 py-1.5 font-medium">Postnatal</td>
                        <td className="px-3 py-1.5">At delivery (EDD)</td>
                        <td className="px-3 py-1.5">
                          Immediate postnatal care
                        </td>
                      </tr>
                      <tr>
                        <td className="px-3 py-1.5 font-medium">
                          1 Week Visit
                        </td>
                        <td className="px-3 py-1.5">7 days after delivery</td>
                        <td className="px-3 py-1.5">Early newborn care</td>
                      </tr>
                      <tr>
                        <td className="px-3 py-1.5 font-medium">
                          1 Month Visit
                        </td>
                        <td className="px-3 py-1.5">30 days after delivery</td>
                        <td className="px-3 py-1.5">Growth monitoring</td>
                      </tr>
                      <tr>
                        <td className="px-3 py-1.5 font-medium">
                          3 Month Visit
                        </td>
                        <td className="px-3 py-1.5">90 days after delivery</td>
                        <td className="px-3 py-1.5">Continued follow-up</td>
                      </tr>
                      <tr>
                        <td className="px-3 py-1.5 font-medium">
                          6 Month Visit
                        </td>
                        <td className="px-3 py-1.5">180 days after delivery</td>
                        <td className="px-3 py-1.5">Final program visit</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
