#!/usr/bin/env node
/* Run v5's compute helpers against the parity fixture and dump JSON.
 *
 * Usage:
 *   node run_v5.js [tab2] <fixture-json-path>
 *
 * Reads fixture JSON from the path, loads the v5 helpers by stripping
 * `function WorkflowUI(...)` and everything after from mbw_auditing_v5_render.js,
 * and runs v5_computeMbwAuditingData against the fixture inputs.
 */

const fs = require('fs');
const path = require('path');

function loadV5Helpers() {
  const renderPath = path.join(
    __dirname,
    '..',
    '..',
    'templates',
    'mbw_auditing_v5_render.js',
  );
  const src = fs.readFileSync(renderPath, 'utf8');
  // Helpers live above the WorkflowUI function — cut at the start of
  // `function WorkflowUI(` to get a self-contained helper script.
  const cutAt = src.indexOf('function WorkflowUI(');
  if (cutAt < 0) {
    throw new Error('Could not find WorkflowUI function marker in v5 render');
  }
  const helpers = src.slice(0, cutAt);
  // Eval helpers in this scope so the v5_* functions become callable.
  // Wrap in a Function so we can return the compute entry point.
  const fn = new Function(
    helpers + '\nreturn { v5_computeMbwAuditingData: v5_computeMbwAuditingData };',
  );
  return fn();
}

function main() {
  const args = process.argv.slice(2);
  const tab2 = args[0] === 'tab2';
  const fixtureArgIdx = tab2 ? 1 : 0;
  const fixturePath = args[fixtureArgIdx];
  if (!fixturePath) {
    console.error('Usage: node run_v5.js [tab2] <fixture-json>');
    process.exit(1);
  }
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

  const helpers = loadV5Helpers();

  const params = {
    visitsRows: fixture.visits,
    visitsAggRows: fixture.visits_agg,
    regRows: fixture.registrations,
    gsRows: fixture.gs_forms,
    activeUsernames: fixture.active_usernames,
    flwNames: fixture.flw_names,
    taskFilters: tab2 ? fixture.task_filters : null,
    currentDate: fixture.current_date,
  };

  const result = helpers.v5_computeMbwAuditingData(params);
  process.stdout.write(JSON.stringify(result, null, 2));
}

main();
