/**
 * Webpack entry for the sandboxed page frame (workflow/sandbox_frame.html): React,
 * the shared report library and the frame runtime. Runs in an opaque origin; it
 * reaches labs only through the bridge to the page around it.
 * See components/workflow/sandbox/frame.tsx.
 */

import { LabsReport } from '@/components/workflow/report';
import { startFrame } from '@/components/workflow/sandbox/frame';

declare global {
  interface Window {
    LabsReport?: typeof LabsReport;
  }
}
// The same report library the run page publishes, built against the React the render is handed.
window.LabsReport = LabsReport;

const root = document.getElementById('sandbox-root');
if (root) startFrame(window, root);
