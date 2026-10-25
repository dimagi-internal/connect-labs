// page_showQuery on a workflow run page (./run_page_actions.js): opens the Data page on the
// workflow in a NEW tab, the query in it, run there as the person -- the run page is left
// as it is. Without a click a browser blocks the tab, so the person gets the link instead.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

const SCRIPT = readFileSync(
  new URL('./run_page_actions.js', import.meta.url),
  'utf8',
);

function mount({ opens = true } = {}) {
  const window = new Window({
    url: 'https://labs.example/labs/workflow/9/run/?run_id=4&program_id=3',
  });
  const document = window.document;
  document.body.innerHTML = `<script type="application/json" id="labsdata-run-page">${JSON.stringify(
    { dataPageUrl: '/labs/data/?workflow=9%3Aprogram%3A3' },
  )}</script>`;
  const opened = [];
  window.open = (url, target) => {
    opened.push({ url, target });
    return opens ? {} : null;
  };
  const registered = [];
  window.canopyHost = {
    widget: { registerAction: (name) => registered.push(name) },
  };
  window.eval(SCRIPT);
  return { window, document, opened, registered };
}

describe('page_showQuery on a run page', () => {
  it('opens the Data page on this workflow in a new tab, the query run there', async () => {
    const { window, opened, registered } = mount();
    expect(registered).toEqual(['showQuery']);
    const ack = await window.LABSDATA_RUN_ACTIONS.showQuery({
      sql: 'SELECT 1',
      title: 'One',
      display: { columns: { n: { label: 'N' } } },
    });
    expect(ack).toMatchObject({ ok: true, outcome: 'opened_in_new_tab' });
    expect(opened).toHaveLength(1);
    expect(opened[0].target).toBe('_blank');
    const url = new URL(opened[0].url);
    expect(url.origin + url.pathname).toBe('https://labs.example/labs/data/');
    expect(url.searchParams.get('workflow')).toBe('9:program:3');
    expect(url.searchParams.get('sql')).toBe('SELECT 1');
    expect(url.searchParams.get('run')).toBe('1');
    expect(JSON.parse(url.searchParams.get('display'))).toEqual({
      columns: { n: { label: 'N' } },
    });
    // The run page itself did not move.
    expect(window.location.pathname).toBe('/labs/workflow/9/run/');
  });

  it('with no click to open a tab, offers the person the link', async () => {
    const { window, document } = mount({ opens: false });
    const ack = await window.LABSDATA_RUN_ACTIONS.showQuery({
      sql: 'SELECT 1',
    });
    expect(ack).toMatchObject({
      ok: true,
      shown: false,
      outcome: 'link_offered_to_person',
    });
    const link = document.querySelector('#labsdata-run-page-link a');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener');
    expect(new URL(link.href).searchParams.get('sql')).toBe('SELECT 1');
  });

  it('checks its input and throws to refuse', async () => {
    const { window, opened } = mount();
    const a = window.LABSDATA_RUN_ACTIONS;
    await expect(a.showQuery({ sql: ' ' })).rejects.toThrow(/sql/);
    await expect(a.showQuery({ sql: 'x'.repeat(20001) })).rejects.toThrow(
      /longer/,
    );
    await expect(
      a.showQuery({ sql: 'SELECT 1', display: 'x' }),
    ).rejects.toThrow(/display/);
    await expect(a.showQuery({ sql: 'SELECT 1', title: 5 })).rejects.toThrow(
      /title/,
    );
    expect(opened).toHaveLength(0);
  });
});
