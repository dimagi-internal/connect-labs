// The Data page's page_showQuery, on a workflow run page (templates/workflow/run.html).
//
// A workflow's chat and the Data page's chat are one engine (docs/superpowers/specs/
// 2026-10-24-data-spaces-design.md, "The same chat on workflows"); on a run page
// `page_showQuery` opens the Data page on this workflow in a NEW tab, with the query in it,
// run there as the person -- it never changes the run page. Like every page action it runs
// in this tab, as the person, returns only an acknowledgement (never rows) and throws to
// refuse. What the agent may READ on this page is decided by labs (labsdata/page_holds.py),
// not here: this only opens a page the person could open themselves.
(function () {
  'use strict';
  const node = document.getElementById('labsdata-run-page');
  if (!node) return;
  let cfg;
  try {
    cfg = JSON.parse(node.textContent) || {};
  } catch (e) {
    return;
  }
  if (!cfg.dataPageUrl) return;

  const MAX_SQL = 20000;

  function checkSql(sql) {
    if (typeof sql !== 'string' || !sql.trim())
      throw new Error(
        'sql is required: one SELECT, as you would give data_query',
      );
    if (sql.length > MAX_SQL)
      throw new Error(`sql is longer than ${MAX_SQL} characters`);
  }

  function checkDisplay(value) {
    if (value === undefined || value === null) return null;
    if (typeof value !== 'object' || Array.isArray(value))
      throw new Error('display must be an object (as data_query takes it)');
    if (JSON.stringify(value).length > MAX_SQL)
      throw new Error('display is too large');
    return value;
  }

  // The Data page on this workflow, the query in its editor, run there as the person.
  function dataPageUrl(sql, display) {
    const url = new URL(cfg.dataPageUrl, window.location.origin);
    url.searchParams.set('sql', sql);
    url.searchParams.set('run', '1');
    if (display) url.searchParams.set('display', JSON.stringify(display));
    return url.href;
  }

  // A browser opens a tab only on a click; without one, the person gets the link.
  function offerLink(href, title) {
    const old = document.getElementById('labsdata-run-page-link');
    if (old) old.remove();
    const box = document.createElement('div');
    box.id = 'labsdata-run-page-link';
    box.className =
      'fixed bottom-4 left-4 z-50 max-w-md rounded-md bg-gray-900 text-white text-sm px-4 py-3 shadow-lg flex items-start gap-3';
    const text = document.createElement('span');
    text.className = 'flex-1';
    text.append('The agent has a query for you on the Data page: ');
    const a = document.createElement('a');
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener';
    a.className = 'underline font-semibold';
    a.textContent = title || 'open it';
    a.addEventListener('click', () => box.remove());
    text.append(a);
    const close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    close.addEventListener('click', () => box.remove());
    box.append(text, close);
    document.body.append(box);
  }

  const ACTIONS = {
    showQuery: [
      async ({ sql, display, title } = {}) => {
        checkSql(sql);
        if (title !== undefined && typeof title !== 'string')
          throw new Error('title must be a string');
        const href = dataPageUrl(sql, checkDisplay(display));
        const label =
          (title || '').trim().slice(0, 200) || 'Query from the agent';
        let tab = null;
        try {
          tab = window.open(href, '_blank');
        } catch (e) {
          tab = null;
        }
        if (tab) {
          try {
            tab.opener = null;
          } catch (e) {
            // a cross-origin handle: nothing to clear
          }
          return { ok: true, shown: true, outcome: 'opened_in_new_tab' };
        }
        offerLink(href, label);
        return {
          ok: true,
          shown: false,
          outcome: 'link_offered_to_person',
          note: 'The browser opens a tab only on the person’s click: they have a link to the Data page.',
        };
      },
      {
        description:
          'Show a query on the Data page, in a NEW tab, on this workflow (its opportunities and pipelines as tables), ' +
          'run there in the person’s browser, as them -- this run page is left as it is. Use it instead of handing SQL ' +
          'back in the chat. Validate the SQL with data_validate first. Returns only an acknowledgement: the rows are ' +
          'shown to the person, never returned to you.',
        parameters: {
          type: 'object',
          properties: {
            sql: {
              type: 'string',
              description:
                'One SELECT over `visits` and this workflow’s pipelines p_<alias>.',
            },
            title: {
              type: 'string',
              description: 'A short plain-words title for it.',
            },
            display: {
              type: 'object',
              description:
                'How to show the result, as data_query’s `display` (labels, formats, chart).',
            },
          },
          required: ['sql'],
        },
      },
    ],
  };
  // For tests and the page harness: call an action as canopy would.
  window.LABSDATA_RUN_ACTIONS = Object.fromEntries(
    Object.entries(ACTIONS).map(([name, [fn]]) => [name, fn]),
  );

  function register(host) {
    if (!host || !host.widget || !host.widget.registerAction) return;
    Object.entries(ACTIONS).forEach(([name, [fn, spec]]) =>
      host.widget.registerAction(name, fn, spec),
    );
  }
  if (window.canopyHost) register(window.canopyHost);
  else
    document.addEventListener('canopy:ready', (e) => register(e.detail), {
      once: true,
    });
})();
