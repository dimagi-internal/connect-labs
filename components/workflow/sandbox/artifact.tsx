/**
 * A data space artifact (labsdata/models.py SpaceArtifact), drawn by the workflow sandbox.
 *
 * An artifact is a small labs page: JSX render code and the named queries it reads. It is
 * drawn by EXACTLY the frame and host a workflow or page is (./SandboxFrame.tsx, ./host.ts,
 * ./frame.tsx: the opaque-origin iframe, the shell's enforced CSP, the MessageChannel
 * bridge) and compiled by the same code (../renderCode via ../DynamicWorkflow), so its
 * code gets what a page's does -- React, `window.LabsReport`, Chart.js, Tailwind -- and
 * nothing else. There is no second renderer: this module only turns what the Data page
 * (the host) fetched into the frame's payload, and mounts the frame.
 *
 * Its data arrives the way a workflow's does: each named query's rows, run by the host
 * over the space as the person (labsdata/service.py run_artifact), are the render's
 * `pipelines[<name>]` -- `{rows: [{column: value}], metadata}` -- so an artifact's author
 * writes `pipelines.visits_by_month.rows` exactly as a workflow author writes
 * `pipelines.visits.rows`. Drawn as a page (no run): the frame's `fetch`, supply and
 * workflow reads, actions and run writes are all refused, so it asks for nothing else.
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import type { FramePayload } from './protocol';
import { SandboxFrame } from './SandboxFrame';

/** One named query's result as the host fetched it (service.run_artifact). */
export interface ArtifactQueryResult {
  columns?: string[];
  column_types?: string[];
  rows?: unknown[][];
  row_count?: number;
  truncated?: boolean;
  row_cap?: { limit?: number; source?: string } | null;
  error?: { code?: string; message?: string } | null;
}

export interface ArtifactInput {
  labsOrigin: string;
  artifact: {
    id?: number | null;
    name: string;
    version?: number | null;
    code: string;
    /** Set for an agent's draft, never saved. */
    draft?: boolean;
  };
  results: Record<string, ArtifactQueryResult>;
  space?: { id?: number | null; name?: string } | null;
  pageUrl?: string;
  today?: string;
}

/**
 * The named queries' results as a render's `pipelines`: each row an object keyed by its
 * column, and the metadata a workflow's pipeline carries (row count, name, whether the
 * row cap cut it, and a failed query's error).
 */
export function artifactPipelines(
  results: Record<string, ArtifactQueryResult>,
): Record<string, { rows: Record<string, unknown>[]; metadata: object }> {
  const out: Record<
    string,
    { rows: Record<string, unknown>[]; metadata: object }
  > = {};
  for (const [name, r] of Object.entries(results || {})) {
    const columns = Array.isArray(r?.columns) ? r.columns.map(String) : [];
    const rows = (Array.isArray(r?.rows) ? r.rows : []).map((row) => {
      const obj: Record<string, unknown> = {};
      columns.forEach((c, i) => {
        obj[c] = Array.isArray(row) ? row[i] : undefined;
      });
      return obj;
    });
    out[name] = {
      rows,
      metadata: {
        row_count: rows.length,
        from_cache: false,
        pipeline_name: name,
        terminal_stage: 'aggregated',
        columns,
        column_types: Array.isArray(r?.column_types) ? r.column_types : [],
        truncated: Boolean(r?.truncated),
        row_cap: r?.row_cap?.limit ?? null,
        ...(r?.error
          ? { error: String(r.error.message || r.error.code || 'failed') }
          : {}),
      },
    };
  }
  return out;
}

/** What the frame is handed for an artifact: a page's payload, its pipelines the rows. */
export function artifactPayload(input: ArtifactInput): FramePayload {
  const a = input.artifact;
  return {
    mode: 'page',
    renderCode: a.code,
    definition: {
      id: a.id ?? null,
      name: a.name,
      kind: 'artifact',
      version: a.version ?? null,
      draft: Boolean(a.draft),
    },
    instance: {},
    workers: [],
    pipelines: artifactPipelines(input.results),
    supply: {},
    workflows: {},
    scope: input.space
      ? { space: { id: input.space.id ?? null, name: input.space.name || '' } }
      : null,
    config: {},
    links: { auditUrlBase: '', taskUrlBase: '' },
    view: { state: {}, isCompleted: false, asOf: null },
    today: input.today || new Date().toISOString().slice(0, 10),
    mapboxToken: '',
    pageUrl: input.pageUrl || '',
  };
}

function onlyItsQueries(): Promise<never> {
  return Promise.reject(new Error('an artifact reads only its own queries'));
}

export interface MountedArtifact {
  update(input: ArtifactInput): void;
  destroy(): void;
}

/**
 * Draw an artifact in `container`, in the workflow sandbox's frame. `update` redraws it
 * (a new version, new rows) through the same frame: the host sends only what changed.
 */
export function mountArtifact(
  container: HTMLElement,
  input: ArtifactInput,
): MountedArtifact {
  const root = createRoot(container);
  const draw = (next: ArtifactInput) =>
    root.render(
      <SandboxFrame
        labsOrigin={next.labsOrigin}
        payload={artifactPayload(next)}
        mode="page"
        querySupply={onlyItsQueries}
        queryWorkflow={onlyItsQueries}
        shareSelection={() => undefined}
      />,
    );
  draw(input);
  return { update: draw, destroy: () => root.unmount() };
}
