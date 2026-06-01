/*
 * Pure-logic tests for labsContextPicker (no DOM/Alpine).
 * Run: node commcare_connect/static/labs/context_picker.test.mjs
 * Stubs window/document so the classic-script factory loads under node.
 */
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

global.window = {
  location: {
    pathname: '/labs/overview',
    search: '',
    href: 'https://x/labs/overview',
  },
  Microplans: { oppColorFor: (i) => ['#a', '#b', '#c'][i % 3] },
};
global.document = { getElementById: () => null, cookie: '' };

const here = dirname(fileURLToPath(import.meta.url));
await import('file://' + join(here, 'context_picker.js'));
const { labsContextPicker } = window;

let fails = 0;
const ok = (c, m) => {
  if (!c) {
    console.log('FAIL:', m);
    fails++;
  } else console.log('ok:', m);
};

// single-mode (navbar) parity
const s = labsContextPicker();
s.orgData = [{ slug: 'acme', name: 'Acme' }];
s.programData = [
  { id: 1, name: 'P1', organization: 'acme' },
  { id: 2, name: 'P2', organization: 'other' },
];
s.oppData = [
  { id: 10, name: 'Opp10', program: 1 },
  { id: 11, name: 'Opp11', program: 2 },
];
ok(s.mode === 'single', 'default mode is single');
s.currentPath = '/labs/overview';
ok(
  s.showOrgs && s.showPrograms && s.showOpps,
  'overview shows all columns (pathFilters)',
);
s.currentPath = '/labs/workflow/123';
ok(
  !s.showOrgs && !s.showPrograms && s.showOpps,
  'workflow shows opps only (pathFilters)',
);
s.selectOrg({ slug: 'acme', name: 'Acme' });
ok(
  s.filteredPrograms.length === 1 && s.filteredPrograms[0].id === 1,
  'filteredPrograms filters by org',
);
s.currentPath = '/labs/overview';
s.selectProgram({ id: 1, name: 'P1' });
ok(
  s.filteredOpps.length === 1 && s.filteredOpps[0].id === 10,
  'filteredOpps filters by program',
);
s.selectOpp({ id: 10, name: 'Opp10' });
ok(
  s.selectedOpp.id === 10 && s.isSelected('opp', 10),
  'selectOpp single + isSelected',
);
s.search = 'opp1';
ok(s.matchesSearch('Opp10') && !s.matchesSearch('Zzz'), 'matchesSearch');
s.clearSelection();
ok(!s.selectedOrg && !s.selectedOpp, 'clearSelection');

// multi-mode (service-delivery picker)
const m = labsContextPicker({ mode: 'multi', scope: ['opp'] });
m.oppData = [
  { id: 10, name: 'O10' },
  { id: 11, name: 'O11' },
];
ok(m.mode === 'multi', 'multi mode set');
ok(
  !m.showOrgs && !m.showPrograms && m.showOpps,
  'scope=[opp] → only opps (no pathFilters)',
);
m.selectOpp({ id: 10, name: 'O10' });
m.selectOpp({ id: 11, name: 'O11' });
ok(m.selectedOpps.length === 2 && m.hasSelection, 'multi select adds two');
m.selectOpp({ id: 10, name: 'O10' });
ok(
  m.selectedOpps.length === 1 && !m.isOppSelected(10),
  'click again removes (toggle)',
);
ok(
  m.oppColor(0) === '#a' && m.oppColor(1) === '#b',
  'oppColor uses Microplans.oppColorFor',
);
m.removeOpp(11);
ok(m.selectedOpps.length === 0, 'removeOpp');

let applied = null;
const cb = labsContextPicker({
  mode: 'multi',
  onApply: (sel) => (applied = sel),
});
cb.selectedOpps = [{ id: 1 }, { id: 2 }];
cb.applySelection();
ok(applied && applied.length === 2, 'multi applySelection calls onApply');

console.log(fails === 0 ? '\nALL PASS' : `\n${fails} FAILED`);
process.exit(fails ? 1 : 0);
