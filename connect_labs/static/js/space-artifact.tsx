/**
 * Webpack entry for the Data page's artifacts (templates/labsdata/index.html): the
 * workflow sandbox's host, published for the page's own script (static/labsdata/data.js)
 * as `window.LabsArtifact.mount(container, input)`. It draws the same frame a workflow's
 * render does (components/workflow/sandbox/artifact.tsx); the page never compiles or runs
 * an artifact's code itself.
 */

import { mountArtifact } from '@/components/workflow/sandbox/artifact';

declare global {
  interface Window {
    LabsArtifact?: { mount: typeof mountArtifact };
  }
}

window.LabsArtifact = { mount: mountArtifact };
document.dispatchEvent(new CustomEvent('labs-artifact:ready'));
