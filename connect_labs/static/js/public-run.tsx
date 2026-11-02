/**
 * Webpack entry for the page behind a public link to a saved run
 * (templates/workflow/public_run.html). See components/workflow/sandbox/publicRun.tsx.
 */

import { mountPublicRun } from '@/components/workflow/sandbox/publicRun';

const root = document.getElementById('public-run-root');
const data = document.getElementById('public-run-data');
if (root && data) mountPublicRun(root, JSON.parse(data.textContent || '{}'));
