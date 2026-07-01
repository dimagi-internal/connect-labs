/*
 * Labs context picker — shared Alpine factory.
 *
 * Extracted verbatim from the inline `labsContextSelector()` that used to live in
 * templates/labs/context_selector.html, then parameterized so the SAME core drives
 * two surfaces:
 *   - the navbar context selector (single-select org/program/opp → reload), and
 *   - the microplans service-delivery opportunity picker (multi-select opps → a
 *     caller-supplied callback).
 *
 * `labsContextPicker(config)` returns an Alpine data object. With no config it is
 * byte-for-byte the old single-select navbar behavior. Config:
 *   - mode:      'single' (default) | 'multi'
 *   - scope:     ['org','program','opp'] — overrides the URL-path filter (navbar
 *                leaves this unset and keeps its pathFilters behavior). Multi
 *                callers pass e.g. ['opp'].
 *   - sourceIds: { org, program, opp } json_script element ids (defaults to the
 *                navbar's 'org-data' / 'program-data' / 'opportunity-data').
 *   - preselect: multi mode — array of opp ids to start selected.
 *   - onApply:   override the default apply. Single default = URL reload;
 *                multi default = call onApply(selectedOpps).
 *
 * Loaded as a classic <script> (no build step), like microplans/shared.js.
 */
(function () {
  'use strict';

  function labsContextPicker(config) {
    config = config || {};
    const mode = config.mode === 'multi' ? 'multi' : 'single';
    const scope = config.scope || null; // null → use pathFilters (navbar)
    const sourceIds = Object.assign(
      { org: 'org-data', program: 'program-data', opp: 'opportunity-data' },
      config.sourceIds || {},
    );

    return {
      // ---- shared state ----
      mode: mode,
      open: false,
      search: '',
      selectedOrg: null,
      selectedProgram: null,
      selectedOpp: null,
      selectedOpps: [], // multi mode only
      cacheTolerance: '',
      orgData: [],
      programData: [],
      oppData: [],
      currentPath: window.location.pathname,
      _scope: scope,

      init() {
        const orgEl = document.getElementById(sourceIds.org);
        if (orgEl) this.orgData = JSON.parse(orgEl.textContent);
        const progEl = document.getElementById(sourceIds.program);
        if (progEl) this.programData = JSON.parse(progEl.textContent);
        const oppEl = document.getElementById(sourceIds.opp);
        if (oppEl) this.oppData = JSON.parse(oppEl.textContent);

        if (this.mode === 'multi') {
          (config.preselect || []).forEach((id) => {
            const opp = this.oppData.find((o) => o.id === parseInt(id, 10));
            if (opp && !this.isOppSelected(opp.id)) this.selectedOpps.push(opp);
          });
          return;
        }

        // ---- single mode: initialize from URL params (navbar, unchanged) ----
        const urlParams = new URLSearchParams(window.location.search);
        const orgId = urlParams.get('organization_id');
        const programId = urlParams.get('program_id');
        const oppId = urlParams.get('opportunity_id');
        const cacheTolPct = urlParams.get('cache_tolerance_pct');

        if (orgId) {
          const org = this.orgData.find((o) => o.slug === orgId);
          if (org) this.selectedOrg = { slug: org.slug, name: org.name };
        }
        if (programId) {
          const program = this.programData.find(
            (p) => p.id === parseInt(programId, 10),
          );
          if (program)
            this.selectedProgram = { id: program.id, name: program.name };
        }
        if (oppId) {
          const opp = this.oppData.find((o) => o.id === parseInt(oppId, 10));
          if (opp) this.selectedOpp = { id: opp.id, name: opp.name };
        }
        if (cacheTolPct) this.cacheTolerance = cacheTolPct;
      },

      // ---- which filters this surface supports ----
      // navbar (scope unset): per-URL pathFilters. scoped callers: use config.scope.
      pathFilters: {
        '/labs/overview': ['orgs', 'programs', 'opps'],
        '/labs/workflow/': ['opps'],
        '/labs/pipelines/': ['opps'],
        '/labs/scout/': ['opps'],
        '/labs/scout-prod/': ['opps'],
        '/solicitations/': ['programs'],
        '/explorer/': ['orgs', 'programs', 'opps'],
        '/audit/': ['programs', 'opps'],
        '/tasks/': ['programs', 'opps'],
        '/coverage/': ['opps'],
        '/custom_analysis/': ['opps'],
        '/funder/': ['orgs', 'programs'],
        '/labs/synthetic/': ['opps'],
      },

      pathSupports(filter) {
        if (this._scope) return this._scope.includes(filter.replace(/s$/, ''));
        for (const [path, filters] of Object.entries(this.pathFilters)) {
          if (this.currentPath.includes(path) && filters.includes(filter)) {
            return true;
          }
        }
        return false;
      },

      get showOrgs() {
        return this.pathSupports('orgs');
      },
      get showPrograms() {
        return this.pathSupports('programs');
      },
      get showOpps() {
        return this.pathSupports('opps');
      },

      get hasSelection() {
        if (this.mode === 'multi') return this.selectedOpps.length > 0;
        return this.selectedOrg || this.selectedProgram || this.selectedOpp;
      },

      get filteredPrograms() {
        if (!this.selectedOrg) return this.programData;
        return this.programData.filter(
          (p) => p.organization === this.selectedOrg.slug,
        );
      },

      get filteredOpps() {
        if (!this.showPrograms || !this.selectedProgram) return this.oppData;
        return this.oppData.filter(
          (o) => o.program === this.selectedProgram.id,
        );
      },

      matchesSearch(text) {
        if (!this.search) return true;
        return String(text).toLowerCase().includes(this.search.toLowerCase());
      },

      isSelected(type, id) {
        if (type === 'org')
          return this.selectedOrg && this.selectedOrg.slug === id;
        if (type === 'program')
          return this.selectedProgram && this.selectedProgram.id === id;
        if (type === 'opp') {
          if (this.mode === 'multi') return this.isOppSelected(id);
          return this.selectedOpp && this.selectedOpp.id === id;
        }
        return false;
      },

      selectOrg(org) {
        this.selectedOrg = { slug: org.slug, name: org.name };
      },
      selectProgram(program) {
        this.selectedProgram = { id: program.id, name: program.name };
      },
      selectOpp(opp) {
        if (this.mode === 'multi') {
          this.toggleOpp(opp);
          return;
        }
        this.selectedOpp = { id: opp.id, name: opp.name };
      },

      // ---- multi-select helpers (mode === 'multi') ----
      isOppSelected(id) {
        return this.selectedOpps.some((o) => o.id === id);
      },
      toggleOpp(opp) {
        const i = this.selectedOpps.findIndex((o) => o.id === opp.id);
        if (i >= 0) this.selectedOpps.splice(i, 1);
        else this.selectedOpps.push({ id: opp.id, name: opp.name });
      },
      removeOpp(id) {
        const i = this.selectedOpps.findIndex((o) => o.id === id);
        if (i >= 0) this.selectedOpps.splice(i, 1);
      },
      // Chip swatch color, indexed by selection order to match the server's
      // microplans/service_delivery OPP_COLORS (via window.Microplans.oppColorFor).
      oppColor(index) {
        if (window.Microplans && window.Microplans.oppColorFor)
          return window.Microplans.oppColorFor(index);
        return '#2563eb';
      },

      clearSelection() {
        this.selectedOrg = null;
        this.selectedProgram = null;
        this.selectedOpp = null;
        this.selectedOpps = [];
      },

      applySelection() {
        // Multi mode (or any caller-supplied onApply) hands off to the callback.
        if (this.mode === 'multi' || typeof config.onApply === 'function') {
          config.onApply &&
            config.onApply(
              this.mode === 'multi'
                ? this.selectedOpps.slice()
                : {
                    org: this.selectedOrg,
                    program: this.selectedProgram,
                    opp: this.selectedOpp,
                  },
            );
          this.open = false;
          return;
        }

        // ---- single mode default: navbar URL/reload behavior (unchanged) ----
        const hasSelection =
          (this.selectedOrg && this.showOrgs) ||
          (this.selectedProgram && this.showPrograms) ||
          (this.selectedOpp && this.showOpps);

        if (!hasSelection) {
          const form = document.createElement('form');
          form.method = 'POST';
          form.action = '/labs/clear-context/';
          const csrfToken =
            document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
            document.querySelector('meta[name="csrf-token"]')?.content ||
            this.getCookie('csrftoken');
          if (csrfToken) {
            const csrfInput = document.createElement('input');
            csrfInput.type = 'hidden';
            csrfInput.name = 'csrfmiddlewaretoken';
            csrfInput.value = csrfToken;
            form.appendChild(csrfInput);
          }
          document.body.appendChild(form);
          form.submit();
        } else {
          const url = new URL(window.location.href);
          url.searchParams.delete('organization_id');
          url.searchParams.delete('program_id');
          url.searchParams.delete('opportunity_id');
          url.searchParams.delete('cache_tolerance_pct');

          if (this.selectedOrg && this.showOrgs)
            url.searchParams.set('organization_id', this.selectedOrg.slug);
          if (this.selectedProgram && this.showPrograms)
            url.searchParams.set('program_id', this.selectedProgram.id);
          if (this.selectedOpp && this.showOpps)
            url.searchParams.set('opportunity_id', this.selectedOpp.id);

          if (this.cacheTolerance !== '' && this.cacheTolerance !== null) {
            const tolerance = parseFloat(this.cacheTolerance);
            if (!isNaN(tolerance) && tolerance >= 0 && tolerance <= 100)
              url.searchParams.set('cache_tolerance_pct', tolerance);
          }

          const newUrl = url.toString();
          const currentUrl = window.location.href;

          const onScoutPage =
            this.currentPath.includes('/labs/scout/') ||
            this.currentPath.includes('/labs/scout-prod/');
          if (onScoutPage && this.selectedOpp && window._scoutWidget) {
            window._scoutWidget.setTenant(this.selectedOpp.id);
          }

          if (newUrl === currentUrl) window.location.reload();
          else window.location.href = newUrl;
        }
      },

      getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
          const cookies = document.cookie.split(';');
          for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === name + '=') {
              cookieValue = decodeURIComponent(
                cookie.substring(name.length + 1),
              );
              break;
            }
          }
        }
        return cookieValue;
      },
    };
  }

  // Back-compat alias: the old navbar template called labsContextSelector().
  window.labsContextPicker = labsContextPicker;
  window.labsContextSelector = function () {
    return labsContextPicker({ mode: 'single' });
  };
})();
