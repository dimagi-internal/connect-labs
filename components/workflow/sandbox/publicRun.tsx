/**
 * The page behind a public link to a saved run (workflow/public_run_views.py).
 *
 * The copied run is drawn by the frame every report draws in. The frame is mounted as an
 * artifact's is, with no workflow handlers, so it can ask labs for nothing: it has its
 * copy and no one to ask. The page's own address carries the token, so it is not handed in.
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import type { FramePayload } from './protocol';
import { SandboxFrame } from './SandboxFrame';

/** The copy, as public_shares.copy_payload froze it. */
export interface PublicRunCopy {
  renderCode: string;
  definition: Record<string, unknown>;
  instance: Record<string, unknown>;
  workers: unknown[];
  pipelines: Record<string, unknown>;
}

export interface PublicRunData {
  labsOrigin: string;
  mapboxToken: string;
  payload: PublicRunCopy;
}

export function publicRunPayload(data: PublicRunData): FramePayload {
  const { payload } = data;
  const state = (payload.instance.state || {}) as Record<string, unknown>;
  return {
    mode: 'workflow',
    renderCode: payload.renderCode,
    definition: payload.definition,
    instance: payload.instance,
    workers: payload.workers || [],
    pipelines: payload.pipelines || {},
    supply: {},
    workflows: {},
    scope: null,
    config: {},
    links: { auditUrlBase: '', taskUrlBase: '' },
    view: {
      state,
      isCompleted: true,
      asOf: (payload.instance.completed_at as string | null) ?? null,
    },
    today: new Date().toISOString().slice(0, 10),
    mapboxToken: data.mapboxToken || '',
    pageUrl: '',
    apiEndpoints: {},
    flags: [],
    audits: [],
    tasks: [],
    workflowActions: [],
    isEditMode: false,
    userOpportunities: [],
  };
}

function nothingToAsk(): Promise<never> {
  return Promise.reject(new Error('a shared report reads only its saved copy'));
}

export function mountPublicRun(container: HTMLElement, data: PublicRunData) {
  const root = createRoot(container);
  root.render(
    <SandboxFrame
      labsOrigin={data.labsOrigin}
      payload={publicRunPayload(data)}
      mode="workflow"
      querySupply={nothingToAsk}
      queryWorkflow={nothingToAsk}
      shareSelection={() => undefined}
    />,
  );
  return root;
}
