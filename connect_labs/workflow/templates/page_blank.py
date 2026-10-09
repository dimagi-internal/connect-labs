"""A page: the starting point for a screen that is not a built-in one.

A page is a workflow with no runs (workflow/page_mode.py): render code, drawn by the
workflow runner in page mode, reading what it declares as the viewer. This starter
draws the scope it is opened in -- an organisation's programmes, a programme's
opportunities -- with links into each, and the latest run of every workflow it
declares under `workflow_sources`. Change it like any workflow: pull, edit the JSX,
push (`workflow_update_render_code`), and declare sources with
`workflow_update_definition`.

Open it at /labs/p/<org|programme|opportunity>/<key>/<page.slug>/, or make it the
scope's home in Settings (namespace `labs`, key `home`).
"""

DEFINITION = {
    "name": "Page",
    "description": "A page: a screen of its own, drawn from what this organisation, programme or opportunity holds.",
    "version": 1,
    "kind": "page",
    # Where it is reached inside its scope: /labs/p/<scope>/<key>/<slug>/.
    "page": {"slug": "home"},
    "templateType": "page_blank",
    "statuses": [],
    "config": {
        "templateType": "page_blank",
        "showFilters": False,
        "showSummaryCards": False,
        # A page reads no pipelines: draw at once, and open no pipeline stream.
        "renderWhileLoading": True,
        "noPipelineStream": True,
    },
    "pipeline_sources": [],
    # e.g. {"alias": "review", "workflow": 8481, "read": "latest_run", "opportunity_id": 10113}
    "workflow_sources": [],
    # The scope's Settings this page reads, as `config.<namespace>`.
    "config_reads": ["supply"],
}

RENDER_CODE = r"""function WorkflowUI({ definition, workflows, scope, config }) {
  // ══ A page (workflow/templates/page_blank.py) ══
  // `scope` is what the page is about and what the viewer can see under it
  // (workflow/page_mode.py); `workflows` is each declared workflow source's data.
  var s = scope || {};
  var title =
    (s.organization && s.type === 'organization' && s.organization.name) ||
    (s.program && s.type === 'program' && s.program.name) ||
    (s.opportunity && s.opportunity.name) ||
    (definition && definition.name) ||
    'Page';
  var kind = { organization: 'Organisation', program: 'Programme', opportunity: 'Opportunity' }[s.type] || '';
  var programs = s.programs || [];
  var opportunities = s.opportunities || [];
  var sources = (definition && definition.workflow_sources) || [];
  var wf = workflows || {};

  function card(href, heading, sub, extra) {
    return (
      <div key={href + heading} className="bg-white border border-gray-200 rounded-lg p-4">
        <a href={href} className="text-brand-indigo font-semibold hover:underline">{heading}</a>
        {sub ? <div className="text-sm text-gray-600 mt-1">{sub}</div> : null}
        {extra}
      </div>
    );
  }

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        {kind ? <div className="text-xs text-gray-600 uppercase tracking-wide">{kind}</div> : null}
        <h1 className="text-2xl font-bold text-brand-indigo">{title}</h1>
        {s.type === 'organization' && programs.length ? (
          <p className="text-sm text-gray-600">
            {programs.length} programme{programs.length === 1 ? '' : 's'} and {opportunities.length} opportunit
            {opportunities.length === 1 ? 'y' : 'ies'} you can see.
          </p>
        ) : null}
      </div>

      {programs.length ? (
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-2">Programmes</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {programs.map(function (p) {
              var opps = opportunities.filter(function (o) { return String(o.program) === String(p.id); });
              return card(
                p.page_url,
                p.name,
                opps.length + ' opportunit' + (opps.length === 1 ? 'y' : 'ies'),
                <div className="flex flex-wrap gap-3 mt-2 text-sm">
                  <a href={p.supply_url} className="text-brand-indigo hover:underline">Supply</a>
                  <a href={p.workflows_url} className="text-brand-indigo hover:underline">Workflows</a>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {s.type !== 'organization' && opportunities.length ? (
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-2">Opportunities</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {opportunities.map(function (o) {
              return card(o.workflows_url, o.name, o.visit_count ? o.visit_count + ' visits' : null, null);
            })}
          </div>
        </div>
      ) : null}

      {sources.length ? (
        <div className="mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-2">Workflows</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {sources.map(function (src) {
              var d = wf[src.alias] || {};
              if (d.error) return card('#', src.alias, d.error, null);
              var run = d.latest_run || (d.runs && d.runs[0]);
              return card(
                (run && run.url) || (d.workflow && d.workflow.url) || '#',
                (d.workflow && d.workflow.name) || src.alias,
                run ? (run.name || 'Run ' + run.id) + ' · ' + run.status : d.workflow ? 'No runs yet' : 'Loading…',
                null
              );
            })}
          </div>
        </div>
      ) : null}

      {s.settings_url ? (
        <div className="text-sm text-gray-600">
          What shows here, and which page this {kind.toLowerCase() || 'scope'} opens on, is set in{' '}
          <a href={s.settings_url} className="text-brand-indigo hover:underline">Settings</a>.
        </div>
      ) : null}
    </div>
  );
}
"""

TEMPLATE = {
    "key": "page_blank",
    "name": "Page",
    "description": DEFINITION["description"],
    "icon": "fa-file",
    "color": "indigo",
    "kind": "page",
    "multi_opp": False,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
}
