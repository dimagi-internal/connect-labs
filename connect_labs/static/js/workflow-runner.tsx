/**
 * Workflow Runner - Webpack entry point for rendering workflows.
 *
 * This script:
 * 1. Loads workflow data from the DOM
 * 2. Renders workflow using DynamicWorkflow (AI-generated code)
 * 3. Fetches and passes pipeline data to the workflow component
 * 4. Handles state updates via API
 * 5. Includes AI chat panel for editing workflows
 * 6. Provides controls for saving/discarding changes and editing code
 */

import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { DynamicWorkflow } from '@/components/workflow/DynamicWorkflow';
import { SandboxFrame } from '@/components/workflow/sandbox/SandboxFrame';
import type { FramePayload } from '@/components/workflow/sandbox/protocol';
import type { RouteContext } from '@/components/workflow/sandbox/routes';
import {
  ActionDialog,
  type ActionRequest,
} from '@/components/workflow/ActionDialog';
import { AIChat } from '@/components/AIChat';
import { DEFAULT_RENDER_CODE } from '@/components/workflow/defaultRenderCode';
import { createActionHandlers } from '@/components/workflow/actionHandlers';
import type {
  WorkflowProps,
  WorkflowDataFromDjango,
  TaskUrlParams,
  ActionHandlers,
  CreateTaskParams,
  TaskResult,
  OCSStatusResult,
  OCSBotsResult,
  OCSSessionParams,
  OCSInitiateResult,
  CreateTaskWithOCSParams,
  TaskWithOCSResult,
  PipelineResult,
  RawFetchAnomaly,
  ActiveJobState,
  SaveWorkerResultParams,
  SaveWorkerResultResponse,
  CompleteRunParams,
  CompleteRunResponse,
  RunView,
  Flag,
  Audit,
  Task,
  WorkerData,
  WorkflowState,
  WorkflowActionExecution,
  WorkflowActionWorker,
  PipelineRowsQuery,
  SupplyQuery,
  SupplyResult,
  PageScope,
  WorkflowSourceResult,
  PipelineRowsQueryResult,
} from '@/components/workflow/types';
import {
  MessageCircle,
  Code,
  Save,
  RotateCcw,
  X,
  Check,
  Database,
  Settings,
} from 'lucide-react';
import { PipelineEditor } from './pipeline-editor';
import { LabsReport } from '@/components/workflow/report';
import { createLinkHelpers } from '@/components/workflow/links';
import { buildRunView } from '@/components/workflow/runView';

// The shared report library, for render code to use at runtime (see
// components/workflow/report/index.ts). Published before any workflow renders,
// and built against the SAME React the render is handed.
declare global {
  interface Window {
    LabsReport?: typeof LabsReport;
  }
}
window.LabsReport = LabsReport;

/**
 * Create action handlers for workflow operations.
 */
/**
 * Tell the embedded agent what the page now shows.
 *
 * The canopy SDK's panel (`canopy_host/panel.html`, >= 0.5) declares the run's
 * selection when the page renders and exposes `window.canopyHost`; a report that
 * drills (an organisation, an opportunity, a worker) narrows that selection here
 * so the agent's "these workers" means the ones on screen. The run, its scope
 * and the tools on offer stay as the server declared them -- the SDK keeps them
 * -- and so do the server's filters, which the drill is layered over.
 * A no-op on a page with no panel (a workflow that does not share its runs).
 */
interface CanopyHost {
  pageState(): { filters?: Record<string, unknown> } | null;
  updatePageState(patch: Record<string, unknown>): unknown;
}
let agentBaseFilters: Record<string, unknown> | null = null;
let lastSelection: AgentSelection | null = null;
export interface AgentSelection {
  visible_ids?: string[];
  drilled?: Record<string, unknown>;
}
function shareSelection(selection: AgentSelection): void {
  lastSelection = selection;
  const host = (window as unknown as { canopyHost?: CanopyHost }).canopyHost;
  if (!host || typeof host.updatePageState !== 'function') return;
  if (agentBaseFilters === null) {
    agentBaseFilters = { ...(host.pageState()?.filters || {}) };
  }
  // Nested, never spread: the server's filters name the run's own scope
  // (opportunity_id OR program_id), which every agent tool must be called with,
  // and a drill into an opportunity must not read as a change of that scope.
  const patch: Record<string, unknown> = {
    filters: { ...agentBaseFilters, drilled: selection.drilled || {} },
  };
  if (selection.visible_ids) patch.visible_ids = selection.visible_ids;
  host.updatePageState(patch);
}
// A selection shared before the panel finished loading is replayed once it has.
document.addEventListener('canopy:ready', () => {
  if (lastSelection) shareSelection(lastSelection);
});

// Extended data type
interface ExtendedWorkflowData extends WorkflowDataFromDjango {
  render_code?: string;
  opportunity_id?: number;
  // Program-owned workflows (record has program_id, no owning opportunity)
  // carry their program scope + the opportunities they SPAN. The runner uses
  // these to drive auth-status + pipeline fetches by program_id / spanned opps
  // when there's no single opportunity_id.
  program_id?: number;
  program_scoped?: boolean;
  opportunity_ids?: number[];
  is_edit_mode?: boolean;
  // A page (workflow/page_mode.py): a workflow with no runs.
  is_page?: boolean;
  // The scope in view and the Settings the definition reads (page_mode.py).
  scope?: PageScope;
  config?: Record<string, Record<string, unknown>>;
  // A page runs sandboxed (workflow/sandbox.py): the runner is the HOST -- it draws
  // the frame at `src` and answers it, and compiles and runs no render code itself.
  // `mode: 'workflow'` (phase 2): a workflow's own render, handed what it reads
  // today (workers, pipelines, its run) and answered through routes.ts.
  sandbox?: {
    src: string;
    origin: string;
    mode?: 'page' | 'workflow';
    /** Definitions whose runs this one declares it reads (declared_reads.py). */
    readable_definitions?: number[];
  };
  mapboxToken?: string;
  viewer_is_staff?: boolean;
}

/**
 * Main workflow runner component with state management, pipeline data, and AI chat.
 */
function WorkflowRunner({
  workflowData: initialData,
  csrfToken,
}: {
  workflowData: ExtendedWorkflowData;
  csrfToken: string;
}) {
  // Effective scope. A program-owned workflow's record has program_id set and
  // opportunity_id = None, so initialData.opportunity_id is absent. In that
  // mode the runner is PROGRAM-scoped: auth-status verifies program access and
  // pipeline fetches resolve+merge across the workflow's spanned opportunities
  // (opportunity_ids) rather than interpolating a single undefined opp id.
  //
  // The opp to use for inherently single-opp calls (pipeline preview/SQL, which
  // pull CCHQ data per opportunity). Prefer the owning opp; for a program-owned
  // workflow fall back to the first spanned opportunity.
  const primaryOppId =
    initialData.opportunity_id ??
    (initialData.opportunity_ids && initialData.opportunity_ids[0]) ??
    undefined;

  // Append the correct scope param(s) to an outgoing pipeline/auth URL. Opp
  // behavior is unchanged (opportunity_id only); program-scoped requests send
  // program_id so the BE verifies program access and resolves the spanned opps.
  const applyScopeParams = useCallback(
    (params: URLSearchParams): void => {
      if (initialData.opportunity_id) {
        params.set('opportunity_id', String(initialData.opportunity_id));
      } else if (initialData.program_id) {
        params.set('program_id', String(initialData.program_id));
      }
    },
    [initialData.opportunity_id, initialData.program_id],
  );

  // Original values (for detecting changes)
  const originalRenderCode = initialData.render_code || DEFAULT_RENDER_CODE;
  const originalDefinition = initialData.definition;

  // State
  const [definition, setDefinition] = useState(initialData.definition);
  const [renderCode, setRenderCode] = useState(originalRenderCode);
  const [instanceState, setInstanceState] = useState(
    initialData.instance.state,
  );
  const [pipelineData, setPipelineData] = useState<
    Record<string, PipelineResult>
  >(initialData.pipeline_data || {});
  // A COMPLETED run whose snapshot carries its own pipeline results never reads
  // live pipeline data: the view resolves `snapshot.pipelines`, and `{}` is not
  // nullish, so anything streamed is discarded. Such a run must therefore neither
  // stream (see the mount effect) nor WAIT -- it is already complete.
  //
  // Both halves matter. Skipping only the stream leaves this status pinned at
  // 'Connecting...' with nothing left to clear it, and the render is gated on it
  // being null: a permanent spinner on a finished report. And gating on the
  // snapshot actually CARRYING pipelines keeps a template whose hook emits no
  // `pipelines` key falling through to live data, as it always has.
  const snapshotCarriesPipelines = !!(
    initialData.instance.status === 'completed' &&
    initialData.instance.snapshot &&
    (initialData.instance.snapshot as { pipelines?: unknown }).pipelines
  );

  // Supply sources (connect_labs/workflow/supply_sources.py): the `supply` prop.
  // A COMPLETED run whose snapshot froze them reads `snapshot.supply` instead,
  // and never loads live supply data.
  // (Never for a sandboxed page: it has no run, so no snapshot to read.)
  const snapshotSupply =
    !initialData.sandbox &&
    initialData.instance.status === 'completed' &&
    initialData.instance.snapshot
      ? ((
          initialData.instance.snapshot as {
            supply?: Record<string, SupplyResult>;
          }
        ).supply ?? null)
      : null;
  const [supplyData, setSupplyData] = useState<Record<string, SupplyResult>>(
    {},
  );
  const supplyDataUrl = initialData.apiEndpoints?.getSupplyData;
  const querySupplyUrl = initialData.apiEndpoints?.querySupply;
  // Other workflows' runs (workflow/workflow_sources.py): the `workflows` prop.
  const [workflowSourceData, setWorkflowSourceData] = useState<
    Record<string, WorkflowSourceResult>
  >({});
  const workflowDataUrl = initialData.apiEndpoints?.getWorkflowData;
  const queryWorkflowUrl = initialData.apiEndpoints?.queryWorkflow;

  // Host mode (a sandboxed page): the frame gets no visit-level pipelines.
  const sandbox = initialData.sandbox || null;

  // Pipeline loading status - null means loaded/ready, string means loading with message
  const [pipelineLoadingStatus, setPipelineLoadingStatus] = useState<
    string | null
  >(
    // Already have data from server render?
    initialData.pipeline_data &&
      Object.keys(initialData.pipeline_data).length > 0
      ? null
      : // No pipelines configured?
        !initialData.definition.pipeline_sources?.length
        ? null
        : // A finished run carries its own; nothing to wait for. Nor does a page
          // that fetches its own rows (config.noPipelineStream).
          snapshotCarriesPipelines ||
            (definition?.config as Record<string, unknown> | undefined)
              ?.noPipelineStream
          ? null
          : // Need to load
            'Connecting...',
  );
  // Opt-in per definition (`config.renderWhileLoading`): a drill-style page
  // whose headline comes from a saved report should not sit behind the
  // pipeline stream's 20-second (warm) or 6-minute (cold) first event. The
  // gate below stays the default because most render code assumes every
  // pipeline alias is present once it mounts.
  const renderWhileLoading = Boolean(
    (definition?.config as Record<string, unknown> | undefined)
      ?.renderWhileLoading,
  );
  // Opt-out per definition (`config.noPipelineStream`): a page that fetches the
  // few rows it needs (see the `pipeline-rows` endpoint) must not also be handed
  // every pipeline's rows for every opportunity it spans -- for the KMC worker
  // review that was ~30 MB to show one worker's ~250 cases.
  const noPipelineStream = Boolean(
    (definition?.config as Record<string, unknown> | undefined)
      ?.noPipelineStream,
  );
  const [error, setError] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isCodeEditorOpen, setIsCodeEditorOpen] = useState(false);
  const [editingCode, setEditingCode] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  // Workflow framework: auth-requires gate. Templates declare their OAuth
  // dependencies in definition.config.auth_requires (e.g. ["connect",
  // "commcare_hq"]). The runner gates the template render on every required
  // service being active. We block here BEFORE any template-specific work
  // fires — no FLW history fetch, no pipeline streaming, nothing — so users
  // see "Authorize CommCare HQ" within ~1s of landing instead of after the
  // FLW table has rendered and they've clicked Launch.
  const authRequires: string[] = (initialData.definition?.config
    ?.auth_requires as string[]) || ['connect'];
  // Stable primitive for the auth-check URL + effect deps. The BE uses this to
  // skip probes for providers this workflow doesn't require (e.g. a
  // connect-only workflow skips the CommCare metadata round-trip).
  const authRequiresParam = authRequires.join(',');
  const [authStatus, setAuthStatus] = useState<Record<
    string,
    {
      active: boolean;
      authorize_url: string;
      label: string;
      reason?: string;
      message?: string;
      domain?: string;
    }
  > | null>(null);
  const [authChecking, setAuthChecking] = useState(true);

  const refreshAuthStatus = useCallback(() => {
    if (!initialData.apiEndpoints?.authStatus) {
      // Older runner deployments may not have the endpoint wired yet —
      // don't block in that case (defaults to current behavior).
      setAuthChecking(false);
      return;
    }
    setAuthChecking(true);
    const url = new URL(
      initialData.apiEndpoints.authStatus,
      window.location.origin,
    );
    url.searchParams.set(
      'next',
      window.location.pathname + window.location.search,
    );
    // Scope the auth check. Opportunity workflows pass opportunity_id so the BE
    // can do a real CCHQ ping (not just a timestamp check) — catches
    // scope-downgrade-on-refresh, the cause of the "click Authorize → still
    // says CommCare HQ unauthorized" loop. Program-owned workflows have no
    // owning opportunity_id, so applyScopeParams sends program_id instead and
    // the BE verifies program membership. Without this a program-scoped runner
    // sent no scope at all and the CCHQ provider could never clear, wedging the
    // pipeline stream behind the missingAuth gate.
    applyScopeParams(url.searchParams);
    // Tell the BE which providers we actually gate on so it can skip probes
    // this workflow doesn't need.
    url.searchParams.set('requires', authRequiresParam);
    // Fail-open timeout: the gate already treats a fetch error as "let the
    // template try" (the pipeline stream re-surfaces any real auth problem),
    // so if the BE probe hangs on a slow/blipping Connect we abort rather than
    // leave the user staring at the "Checking authorization…" spinner.
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    fetch(url.toString(), { signal: controller.signal })
      .then((r) => r.json())
      .then((data) => {
        setAuthStatus(data);
        setAuthChecking(false);
      })
      .catch(() => {
        // Network error, abort/timeout — let the template try; pipeline-stream
        // errors will surface anything the runner-level gate would have caught.
        setAuthStatus(null);
        setAuthChecking(false);
      })
      .finally(() => clearTimeout(timeoutId));
  }, [
    initialData.apiEndpoints?.authStatus,
    applyScopeParams,
    authRequiresParam,
  ]);

  useEffect(() => {
    refreshAuthStatus();
  }, [refreshAuthStatus]);

  const missingAuth = authStatus
    ? authRequires
        .map((key) => ({ key, ...(authStatus[key] || {}) }))
        .filter((s) => s.active === false)
    : [];

  // Generic, workflow-agnostic surface for the raw-fetch shrink guard (see
  // SQLBackend.stream_raw_visits / fetch_raw_visits): a pipeline's refetch
  // came back suspiciously smaller than what was already cached, so the
  // backend rejected it and kept serving the previous (larger, still-good)
  // data. Shown as a dismissible-by-outcome banner above the render — data
  // keeps showing underneath, unlike the missingAuth gate above, since this
  // isn't blocking: the page is showing GOOD (if slightly older) data, not
  // broken data. Mirrors the missingAuth pattern: computed once here from
  // shared pipelineData state, rendered once, applies to every workflow with
  // no render-code changes needed.
  const rawFetchAnomalies = useMemo(
    () =>
      Object.entries(pipelineData).flatMap(([alias, result]) =>
        (result?.metadata?.raw_fetch_anomalies || []).map((a) => ({
          alias,
          ...a,
        })),
      ),
    [pipelineData],
  );
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Tab state for switching between workflow and pipeline editing
  const [activeTab, setActiveTab] = useState<'workflow' | 'pipeline'>(
    'workflow',
  );
  const [selectedPipelineId, setSelectedPipelineId] = useState<number | null>(
    null,
  );
  const [selectedPipelineData, setSelectedPipelineData] = useState<{
    definition: Record<string, unknown>;
    previewData?: Record<string, unknown>;
  } | null>(null);
  const [isLoadingPipelineEditor, setIsLoadingPipelineEditor] = useState(false);

  // Check if there are unsaved changes
  const hasChanges = useMemo(() => {
    const codeChanged = renderCode !== originalRenderCode;
    const defChanged =
      JSON.stringify(definition) !== JSON.stringify(originalDefinition);
    return codeChanged || defChanged;
  }, [renderCode, definition, originalRenderCode, originalDefinition]);

  // Check if we're in edit mode
  const isEditMode = initialData.is_edit_mode === true;

  // Stream pipeline data via SSE - shows progress during loading
  const streamPipelineData = useCallback(() => {
    if (!initialData.apiEndpoints.streamPipelineData) return;
    if (!definition.pipeline_sources?.length) return;

    setPipelineLoadingStatus('Connecting to pipeline stream...');

    const url = new URL(
      initialData.apiEndpoints.streamPipelineData,
      window.location.origin,
    );
    // Opportunity workflows scope by opportunity_id; program-owned workflows
    // send program_id (the BE stream then resolves the spanned opps from
    // definition.opportunity_ids). Previously a program-scoped runner
    // interpolated `opportunity_id=undefined`, which crashed the stream with a
    // generic internal error before any pipeline ran.
    applyScopeParams(url.searchParams);
    // Forward tolerance param from page URL to pipeline stream
    const pageParams = new URLSearchParams(window.location.search);
    const tolerance = pageParams.get('tolerance');
    if (tolerance) {
      url.searchParams.set('tolerance', tolerance);
    }
    // Same forwarding for the raw-fetch-anomaly banner's action links
    // (?refresh=1 / ?accept_low_count=1 on the PAGE url) — AnalysisPipeline
    // reads these off the SSE endpoint's own request.GET, so without this
    // forward they'd have no effect no matter how the page got reloaded.
    //
    // Both are honored ONCE, then stripped from the visible URL; the in-flight
    // `url` still carries them. Neither is harmless to leave sticky:
    // - accept_low_count DISABLES the shrink-guard safety check. Left in the
    //   address bar, a plain reload (F5) — or the URL getting bookmarked/shared
    //   — would keep bypassing the guard indefinitely, silently reopening the
    //   exact overwrite risk this guard exists to close.
    // - refresh=1 re-downloads every opportunity's whole visit export (tens of
    //   thousands of visits, minutes each). It used to be left in place as
    //   "just means always pull fresh", so every reload of a bookmarked or
    //   shared ?refresh=1 link paid for a full forced refresh again (#1926).
    const oneShotParams = ['refresh', 'accept_low_count'].filter(
      (p) => pageParams.get(p) === '1',
    );
    oneShotParams.forEach((p) => url.searchParams.set(p, '1'));
    if (oneShotParams.length) {
      const cleanUrl = new URL(window.location.href);
      oneShotParams.forEach((p) => cleanUrl.searchParams.delete(p));
      window.history.replaceState(
        null,
        '',
        cleanUrl.pathname + cleanUrl.search,
      );
    }

    const eventSource = new EventSource(url.toString());

    // Track last-received message + receipt timestamp so the onerror handler
    // can surface useful diagnostics instead of the generic
    // "Pipeline stream connection lost".
    let lastMessage: string | null = null;
    let lastMessageAt: number | null = null;
    let receivedAny = false;
    // Set once the payload lands, so the onerror fallback below can tell a
    // stream that failed from one that finished and then dropped its socket.
    let gotPipelines = false;
    const startTs = Date.now();

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        receivedAny = true;
        lastMessageAt = Date.now();

        if (data.error) {
          setPipelineLoadingStatus(null);
          // CCHQ auth-required signal — emitted when the BE detects via
          // verify_hq_access that CCHQ rejected the token even though our
          // local timestamp said it was valid. Note: the typed payload
          // (cchq_auth_required, authorize_url, domain) is nested under
          // `data.data` per send_sse_event's wire format, NOT at the top
          // level. Earlier the check was on `data.cchq_auth_required` and
          // never fired even when the BE was emitting the right signal.
          //
          // Recovery: force-mark CommCare HQ as inactive in our local
          // auth status and re-show the framework gate. The gate's
          // "Authorize CommCare HQ" button is the right next click for
          // the user.
          const payload = data.data || {};
          if (payload.cchq_auth_required) {
            setAuthStatus((prev) =>
              prev
                ? {
                    ...prev,
                    commcare_hq: {
                      ...(prev.commcare_hq || {
                        label: 'CommCare HQ',
                      }),
                      active: false,
                      // Prefer the URL refreshAuthStatus already computed
                      // with next=<this page>. The backend's payload.authorize_url
                      // here is a generic fallback (it has no notion of which
                      // page the user is actually on) and previously always won
                      // this fallback chain since it's practically always truthy
                      // — which sent users who re-authorized mid-run off to a
                      // different, unrelated screen instead of back to their run.
                      authorize_url:
                        prev.commcare_hq?.authorize_url ||
                        payload.authorize_url ||
                        `/labs/commcare/initiate/?next=${encodeURIComponent(
                          window.location.pathname + window.location.search,
                        )}`,
                    },
                  }
                : prev,
            );
            // Don't set the small error banner — the framework gate panel
            // we just reopened is the more useful UI.
          } else if (payload.concurrent_run) {
            // Cache-layer concurrency conflict: another tab/session is
            // running the same pipeline and got there first. Re-running
            // once their write finishes will hit cache cleanly.
            setError(
              `Another pipeline run for this opportunity is already in ` +
                `progress (collided on ${payload.cache_table}). Wait ~30s ` +
                `for it to finish, then click Retry — the cached result ` +
                `should load instantly.`,
            );
          } else {
            setError(data.error);
          }
          eventSource.close();
          return;
        }

        // Progress update - show status message
        if (data.message) {
          lastMessage = data.message;
          setPipelineLoadingStatus(data.message);
        }

        // Data complete - update state and allow render
        if (data.data?.pipelines) {
          gotPipelines = true;
          setPipelineData(data.data.pipelines);
          setPipelineLoadingStatus(null);
          eventSource.close();
        }
      } catch (e) {
        console.error(
          '[WorkflowRunner] Failed to parse pipeline SSE event:',
          e,
        );
      }
    };

    eventSource.onerror = () => {
      setPipelineLoadingStatus(null);
      // Build a descriptive error so users know what was happening when
      // the connection dropped. EventSource onerror fires for any
      // connection close including: backend process crash (OOM, exception),
      // ALB idle timeout (60s of silence), and network blips. We can't
      // distinguish from the browser side — be honest about the
      // possibilities and direct ops at the right place to look.
      const elapsedSec = Math.round((Date.now() - startTs) / 1000);
      let detail: string;
      if (!receivedAny) {
        detail =
          `No events received after ${elapsedSec}s. The server may be ` +
          `unreachable, or the SSE endpoint failed to start. Try reload.`;
      } else if (lastMessage) {
        const sinceLastSec = lastMessageAt
          ? Math.round((Date.now() - lastMessageAt) / 1000)
          : null;
        const sinceLast =
          sinceLastSec !== null ? ` (${sinceLastSec}s since last update)` : '';
        detail =
          `Connection ended while: "${lastMessage}"${sinceLast}. ` +
          `Possible causes: (a) backend process crashed (out-of-memory ` +
          `during a large fetch is the usual culprit — check ECS web ` +
          `logs for "SIGKILL" or "Worker was killed"), (b) backend ` +
          `raised an exception (check logs for traceback), (c) AWS ALB ` +
          `idle-timed-out the SSE stream after 60s of silence. ` +
          `Try reload — cached data should make the second attempt much faster.`;
      } else {
        detail = `Connection dropped after ${elapsedSec}s. Try reload.`;
      }
      eventSource.close();

      // The stream is the fast path, not the only one.
      //
      // Observed in production on 2026-09-17: the SSE endpoint emitted its
      // first event ("Loading workflow configuration...", 75 bytes) and then
      // the connection closed with NO error event -- so the worker died
      // rather than raising, since an exception would have sent one. It
      // reproduced on two unrelated workflows (13005 and 12898), i.e. it was
      // the transport, not any one report. Meanwhile the NON-streaming
      // endpoint serving the identical payload was measured healthy
      // throughout: ~157 MB in 110-150s, every pipeline complete, repeatedly.
      //
      // Every workflow report was therefore unusable while its data sat one
      // URL away. So a failed stream now retries over plain JSON before
      // showing the user an error. `detail` is still reported if that fails
      // too, so a genuine outage reads the same as it did before.
      if (gotPipelines) return;
      const directUrl = new URL(url.toString());
      directUrl.pathname = directUrl.pathname.replace(/stream\/?$/, '');
      setPipelineLoadingStatus(
        'Live updates failed - loading the data directly instead. ' +
          'This can take a few minutes on a large report; leave the tab open.',
      );
      fetch(directUrl.toString(), { credentials: 'same-origin' })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((payload) => {
          const pipelines = payload?.pipelines ?? payload;
          if (
            !pipelines ||
            typeof pipelines !== 'object' ||
            !Object.keys(pipelines).length
          ) {
            throw new Error('response carried no pipeline data');
          }
          setPipelineData(pipelines);
          setPipelineLoadingStatus(null);
        })
        .catch((e) => {
          setPipelineLoadingStatus(null);
          setError(
            `${detail} Loading directly also failed: ${e?.message || e}.`,
          );
        });
    };

    return () => eventSource.close();
  }, [
    initialData.apiEndpoints.streamPipelineData,
    applyScopeParams,
    definition.pipeline_sources,
  ]);

  // Load pipeline data on mount via SSE streaming.
  // Gated on the framework auth check passing — no point hammering CCHQ
  // pipelines if the user's CCHQ token is rejected; surfacing the
  // "Authorize" gate at the top is faster and clearer.
  useEffect(() => {
    if (authChecking || missingAuth.length > 0) return;
    // A COMPLETED run whose snapshot carries its own pipelines never uses live
    // pipeline data: the view reads `snapshot.pipelines`, and `{}` is not nullish,
    // so the streamed result is discarded. Streaming it anyway recomputes the whole
    // extraction the run was saved to avoid -- measured on KMC, 31 MB and ~5.5
    // minutes on a cold cache -- and blocks the page on "Connecting to pipeline
    // stream..." while a banner promises figures that load instantly.
    //
    // Gated on the snapshot actually CARRYING pipelines, because a template whose
    // hook emits no `pipelines` key still falls through to the live data and would
    // otherwise lose it.
    if (snapshotCarriesPipelines) return;
    if (noPipelineStream) return;
    if (
      definition.pipeline_sources?.length &&
      !Object.keys(pipelineData).length
    ) {
      const cleanup = streamPipelineData();
      return cleanup;
    }
  }, [
    snapshotCarriesPipelines,
    noPipelineStream,
    definition.pipeline_sources,
    pipelineData,
    streamPipelineData,
    authChecking,
    missingAuth.length,
  ]);

  // Load every eager supply source once, as the viewer (the server checks access
  // per opportunity, exactly as the supply pages do). Errors land per source in
  // its metadata, so one opportunity failing never blanks the page.
  useEffect(() => {
    if (snapshotSupply || !supplyDataUrl) return;
    const sources = (definition.supply_sources || []) as Array<{
      load?: string;
    }>;
    if (!sources.some((src) => src.load !== 'on_demand')) return;
    const url = new URL(supplyDataUrl, window.location.origin);
    applyScopeParams(url.searchParams);
    let cancelled = false;
    fetch(url.toString(), { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled && data && data.supply) setSupplyData(data.supply);
      })
      .catch((err) => console.error('supply data failed', err));
    return () => {
      cancelled = true;
    };
  }, [
    snapshotSupply,
    supplyDataUrl,
    definition.supply_sources,
    applyScopeParams,
  ]);

  // Load every declared workflow source once, as the viewer: another workflow's
  // latest run, its saved runs or its summary. A source the viewer cannot read
  // comes back with its own error, so one never blanks the page.
  useEffect(() => {
    if (!workflowDataUrl) return;
    const sources = (definition.workflow_sources || []) as unknown[];
    if (!sources.length) return;
    const url = new URL(workflowDataUrl, window.location.origin);
    applyScopeParams(url.searchParams);
    let cancelled = false;
    fetch(url.toString(), { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled && data && data.workflows)
          setWorkflowSourceData(data.workflows);
      })
      .catch((err) => console.error('workflow sources failed', err));
    return () => {
      cancelled = true;
    };
  }, [workflowDataUrl, definition.workflow_sources, applyScopeParams]);

  // Auto-reconnect to running jobs on page load
  // This allows users to close the browser and return later while the Celery task continues
  useEffect(() => {
    const activeJob = initialData.instance.state?.active_job as
      ActiveJobState | undefined;

    // Only reconnect if there's a running job with a task ID
    if (activeJob?.status !== 'running' || !activeJob?.job_id) {
      return;
    }

    console.log(
      '[WorkflowRunner] Reconnecting to running job:',
      activeJob.job_id,
    );

    // Connect to the SSE stream for the running job
    const eventSource = new EventSource(
      `/labs/workflow/api/job/${activeJob.job_id}/status/`,
    );

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.error) {
          console.error('[WorkflowRunner] Job error:', data.error);
          // Update state to reflect error
          setInstanceState((prev) => {
            const prevJob = (prev.active_job as ActiveJobState) || {};
            return {
              ...prev,
              active_job: {
                ...prevJob,
                status: 'failed' as const,
                error: data.error,
              },
            };
          });
          eventSource.close();
          return;
        }

        if (data.data?.status === 'completed') {
          console.log('[WorkflowRunner] Job completed');
          setInstanceState((prev) => {
            const prevJob = (prev.active_job as ActiveJobState) || {};
            const results = data.data.results || {};
            return {
              ...prev,
              active_job: {
                ...prevJob,
                status: 'completed' as const,
              },
              // Merge any results
              ...results,
            };
          });
          eventSource.close();
          return;
        }

        if (data.data?.status === 'cancelled') {
          console.log('[WorkflowRunner] Job was cancelled');
          setInstanceState((prev) => {
            const prevJob = (prev.active_job as ActiveJobState) || {};
            return {
              ...prev,
              active_job: {
                ...prevJob,
                status: 'cancelled' as const,
              },
            };
          });
          eventSource.close();
          return;
        }

        // Stream item result for real-time row updates
        if (data.data?.item_result) {
          setInstanceState((prev) => {
            const existingResults =
              (prev.validation_results as Record<string, unknown>) || {};
            const itemId =
              data.data.item_result.id || data.data.item_result.username;
            return {
              ...prev,
              validation_results: {
                ...existingResults,
                [itemId]: data.data.item_result,
              },
            };
          });
        }

        // Update progress in active_job
        if (data.data) {
          setInstanceState((prev) => {
            const prevJob = (prev.active_job as ActiveJobState) || {};
            return {
              ...prev,
              active_job: {
                ...prevJob,
                status: (data.data.status ||
                  'running') as ActiveJobState['status'],
                processed: data.data.processed,
                total: data.data.total,
                current_stage: data.data.current_stage,
                total_stages: data.data.total_stages,
                stage_name: data.data.stage_name,
              },
            };
          });
        }
      } catch (e) {
        console.error('[WorkflowRunner] Failed to parse SSE event:', e);
      }
    };

    eventSource.onerror = () => {
      console.log(
        '[WorkflowRunner] SSE connection lost, will poll state on next refresh',
      );
      eventSource.close();
    };

    // Cleanup on unmount
    return () => {
      eventSource.close();
    };
  }, []); // Only run once on mount

  // Load pipeline definition for editing
  const loadPipelineForEditing = useCallback(
    async (pipelineId: number) => {
      setIsLoadingPipelineEditor(true);
      setSelectedPipelineId(pipelineId);
      setActiveTab('pipeline');

      try {
        // Pipeline definition + preview are inherently single-opp (CCHQ data is
        // per-opportunity). For a program-owned workflow there's no owning opp,
        // so fall back to the first spanned opportunity via primaryOppId instead
        // of interpolating `opportunity_id=undefined`.
        const defResponse = await fetch(
          `/labs/workflow/api/pipeline/${pipelineId}/?opportunity_id=${primaryOppId}`,
        );
        if (!defResponse.ok) throw new Error('Failed to load pipeline');
        const defData = await defResponse.json();

        // Fetch preview data
        const previewResponse = await fetch(
          `/labs/workflow/api/pipeline/${pipelineId}/preview/?opportunity_id=${primaryOppId}`,
        );
        const previewData = previewResponse.ok
          ? await previewResponse.json()
          : { rows: [], metadata: {} };

        setSelectedPipelineData({
          definition: defData.definition,
          previewData: previewData,
        });
      } catch (e) {
        console.error('Failed to load pipeline for editing:', e);
        setError(String(e));
        setActiveTab('workflow');
      } finally {
        setIsLoadingPipelineEditor(false);
      }
    },
    [primaryOppId],
  );

  // Handle returning to workflow tab
  const handleReturnToWorkflow = useCallback(() => {
    setActiveTab('workflow');
    setSelectedPipelineId(null);
    setSelectedPipelineData(null);
    // Refresh pipeline data in case schema changed
    streamPipelineData();
  }, [streamPipelineData]);

  // Handle state updates
  const handleUpdateState = useCallback(
    async (newState: Record<string, unknown>) => {
      if (isEditMode || !initialData.apiEndpoints.updateState) {
        setInstanceState((prev) => ({ ...prev, ...newState }));
        return;
      }

      try {
        const response = await fetch(initialData.apiEndpoints.updateState, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({ state: newState }),
        });

        if (!response.ok) {
          throw new Error('Failed to update state');
        }

        const result = await response.json();

        if (result.success && result.run) {
          setInstanceState(result.run.state);
        } else if (result.error) {
          throw new Error(result.error);
        }
      } catch (e) {
        console.error('Failed to update workflow state:', e);
        setError(String(e));
        throw e;
      }
    },
    [initialData.apiEndpoints.updateState, csrfToken, isEditMode],
  );

  // Handle workflow definition update from AI
  const handleWorkflowUpdate = useCallback(
    (newDefinition: Record<string, unknown>) => {
      console.log('Workflow definition updated by AI:', newDefinition);
      setDefinition(newDefinition as typeof definition);
    },
    [],
  );

  // Handle render code update from AI
  const handleRenderCodeUpdate = useCallback((newRenderCode: string) => {
    console.log('Render code updated by AI');
    setRenderCode(newRenderCode);
    setRenderError(null);
  }, []);

  // Handle render errors
  const handleRenderError = useCallback((errorMsg: string) => {
    setRenderError(errorMsg);
  }, []);

  // Save changes to backend
  const handleSave = useCallback(async () => {
    setIsSaving(true);
    setError(null);

    try {
      const response = await fetch(
        `/labs/workflow/api/${initialData.definition_id}/render-code/`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({
            component_code: renderCode,
            definition: definition,
          }),
        },
      );

      if (!response.ok) {
        throw new Error('Failed to save changes');
      }

      const result = await response.json();
      if (result.success) {
        // Classes this render code uses that no deployed stylesheet defines.
        // They fail silently in the browser (a missing colour paints nothing, a
        // missing size collapses the element), so surface them here rather than
        // let the author discover it by looking at a blank panel. Advisory —
        // the save has already succeeded. See labs#1294.
        if (result.render_code_warning) {
          console.warn(
            '[render_code] ' + result.render_code_warning.message,
            result.render_code_warning.unresolved_classes,
          );
        }
        setSaveSuccess(true);
        setTimeout(() => setSaveSuccess(false), 2000);
      } else {
        throw new Error(result.error || 'Failed to save');
      }
    } catch (e) {
      console.error('Failed to save changes:', e);
      setError(String(e));
    } finally {
      setIsSaving(false);
    }
  }, [renderCode, definition, initialData.definition_id, csrfToken]);

  // Discard changes
  const handleDiscard = useCallback(() => {
    setRenderCode(originalRenderCode);
    setDefinition(originalDefinition);
    setRenderError(null);
  }, [originalRenderCode, originalDefinition]);

  // Open code editor
  const handleOpenCodeEditor = useCallback(() => {
    setEditingCode(renderCode);
    setIsCodeEditorOpen(true);
  }, [renderCode]);

  // Apply code from editor
  const handleApplyCode = useCallback(() => {
    setRenderCode(editingCode);
    setIsCodeEditorOpen(false);
    setRenderError(null);
  }, [editingCode]);

  // Create action handlers
  // A workflow action a button asked to run (actions.runAction): the dialog's
  // request and the promise it settles.
  const actionOpenRef = React.useRef(false);
  const actionSeq = React.useRef(0);
  const [actionRequest, setActionRequest] = useState<{
    request: ActionRequest;
    resolve: (execution: WorkflowActionExecution | null) => void;
    seq: number;
  } | null>(null);
  const workflowActions = useMemo(
    () => initialData.actions || [],
    [initialData.actions],
  );

  const queryPipelineRowsUrl = initialData.apiEndpoints?.queryPipelineRows;
  const actions = useMemo(() => {
    return {
      ...createActionHandlers(csrfToken),
      // Server-side rows of one pipeline alias (filters/search/order/page in
      // SQL) -- how render code reads a `load: "on_demand"` source, which the
      // page does not stream. A cold cache answers 202 "warming" while a
      // background task fills it; this polls until the rows are ready.
      queryPipelineRows: async (
        alias: string,
        query: PipelineRowsQuery = {},
      ): Promise<PipelineRowsQueryResult> => {
        if (!queryPipelineRowsUrl) {
          throw new Error('queryPipelineRows is not available on this page.');
        }
        const { onStatus, timeoutMs, ...body } = query;
        const url = new URL(queryPipelineRowsUrl, window.location.origin);
        applyScopeParams(url.searchParams);
        const deadline = Date.now() + (timeoutMs ?? 10 * 60 * 1000);
        for (;;) {
          const response = await fetch(url.toString(), {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify({ alias, ...body }),
          });
          let data: Record<string, unknown> = {};
          try {
            data = await response.json();
          } catch {
            throw new Error(
              `queryPipelineRows: server error ${response.status}`,
            );
          }
          if (response.status === 202 && data.status === 'warming') {
            if (onStatus) onStatus(String(data.message || 'Loading...'));
            if (Date.now() > deadline) {
              throw new Error(
                `queryPipelineRows: the ${alias} pipeline is still loading; try again shortly.`,
              );
            }
            const wait = Number(data.retry_after_ms) || 3000;
            await new Promise((r) => setTimeout(r, wait));
            continue;
          }
          if (!response.ok || data.status !== 'ready') {
            throw new Error(
              String(
                data.error || `queryPipelineRows: HTTP ${response.status}`,
              ),
            );
          }
          return {
            rows: (data.rows as Record<string, unknown>[]) || [],
            total: Number(data.total) || 0,
            limit: Number(data.limit) || 0,
            offset: Number(data.offset) || 0,
          };
        }
      },
      // One supply source on demand (supply_sources.py), e.g. a worker's timeline:
      // querySupply('timeline', {args: {supply_point_id: 7}, opportunity_id: 10113}).
      querySupply: async (
        alias: string,
        query: SupplyQuery = {},
      ): Promise<SupplyResult> => {
        if (!querySupplyUrl) {
          throw new Error('querySupply is not available on this page.');
        }
        const url = new URL(querySupplyUrl, window.location.origin);
        applyScopeParams(url.searchParams);
        const response = await fetch(url.toString(), {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({ alias, ...query }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(
            data.error || `querySupply failed (${response.status})`,
          );
        }
        return data as SupplyResult;
      },
      // One workflow source on demand (workflow_sources.py): queryWorkflow('report').
      queryWorkflow: async (alias: string): Promise<WorkflowSourceResult> => {
        if (!queryWorkflowUrl) {
          throw new Error('queryWorkflow is not available on this page.');
        }
        const url = new URL(queryWorkflowUrl, window.location.origin);
        applyScopeParams(url.searchParams);
        const response = await fetch(url.toString(), {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({ alias }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(
            data.error || `queryWorkflow failed (${response.status})`,
          );
        }
        return data as WorkflowSourceResult;
      },
      // The page contract's file and clipboard helpers (what a sandboxed page has
      // instead of a download link and navigator.clipboard), here done directly.
      download: async (file: {
        filename: string;
        mime?: string;
        bytes: string | ArrayBuffer | Uint8Array;
      }): Promise<void> => {
        const blob = new Blob([file.bytes as BlobPart], {
          type: file.mime || 'application/octet-stream',
        });
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = file.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 10_000);
      },
      copyText: (text: string): Promise<void> =>
        navigator.clipboard.writeText(text),
      // Leaving the page (what code used to do with window.location). In the
      // sandboxed frame the same calls are links the host checks first.
      navigate: (
        url: string,
        opts?: { newTab?: boolean },
      ): Promise<boolean> => {
        if (opts?.newTab) window.open(url, '_blank', 'noopener');
        else window.location.assign(url);
        return Promise.resolve(true);
      },
      reload: (): Promise<boolean> => {
        window.location.reload();
        return Promise.resolve(true);
      },
      runAction: (
        key: string,
        args: { workers: WorkflowActionWorker[]; [k: string]: unknown },
        options?: { qaOnly?: boolean },
      ): Promise<WorkflowActionExecution | null> => {
        const spec = workflowActions.find((a) => a.key === key);
        if (!spec) {
          return Promise.reject(
            new Error(`This workflow has no action "${key}".`),
          );
        }
        // One action at a time: a second while the dialog is open would swap what
        // the person is reading for something else under the same buttons.
        if (actionOpenRef.current) {
          return Promise.reject(
            new Error('Another action is waiting for an answer.'),
          );
        }
        actionOpenRef.current = true;
        return new Promise((resolve) =>
          setActionRequest({
            request: {
              key,
              label: spec.label,
              args,
              qaOnly: Boolean(options?.qaOnly),
            },
            resolve,
            seq: ++actionSeq.current,
          }),
        );
      },
    };
  }, [
    csrfToken,
    workflowActions,
    queryPipelineRowsUrl,
    querySupplyUrl,
    queryWorkflowUrl,
    applyScopeParams,
  ]);

  // useRunView: abstracts snapshot-vs-live data reads and exposes view.complete().
  //
  // While the run is in_progress, returns the live workers/pipelines/state from
  // props. Once the run is completed, returns whatever was captured in
  // `instance.snapshot` at completion time. Render code that uses `view.X`
  // works the same in both modes — the contract is maintained by the template's
  // `snapshot_inputs` declaration on the BE side.
  //
  // See WORKFLOW_REFERENCE.md §"Saved-runs templates" for the full contract.
  const view = useMemo<RunView>(() => {
    const inst = initialData.instance;
    const isCompleted = inst.status === 'completed';

    const completeRunUrl = initialData.apiEndpoints.completeRun;
    const completeFn = async (opts?: {
      confirm?: string;
    }): Promise<boolean> => {
      if (isCompleted) {
        return false;
      }
      if (!completeRunUrl) {
        window.alert('Completion is not available for this run.');
        return false;
      }
      if (opts?.confirm && !window.confirm(opts.confirm)) {
        return false;
      }
      try {
        const res = await actions.completeRun(inst.id);
        if (!res.success) {
          window.alert(`Failed to complete run: ${res.error || 'unknown'}`);
          return false;
        }
        // Reload so the runner re-mounts in completed mode and renders from
        // the snapshot the BE just wrote.
        window.location.reload();
        return true;
      } catch (e) {
        window.alert(
          `Failed to complete run: ${
            e instanceof Error ? e.message : String(e)
          }`,
        );
        return false;
      }
    };

    return buildRunView({
      instance: inst,
      opportunityId: initialData.opportunity_id,
      workers: initialData.workers,
      pipelines: pipelineData,
      supply: supplyData,
      state: instanceState,
      flags: initialData.flags,
      audits: initialData.audits as Audit[] | undefined,
      tasks: initialData.tasks as Task[] | undefined,
      workflowActions,
      csrfToken,
      complete: completeFn,
      // The page contract's confirm (a sandboxed page has no window.confirm), here
      // the browser's own.
      confirm: (text: string) => Promise.resolve(window.confirm(text)),
      shareSelection,
    });
  }, [
    initialData.instance,
    initialData.workers,
    initialData.flags,
    initialData.audits,
    initialData.tasks,
    initialData.apiEndpoints.completeRun,
    pipelineData,
    supplyData,
    instanceState,
    actions,
    csrfToken,
    workflowActions,
  ]);

  // Create props for workflow component
  const workflowProps: WorkflowProps = {
    definition: definition,
    instance: {
      ...initialData.instance,
      state: instanceState,
    },
    workers: initialData.workers,
    pipelines: pipelineData,
    supply: snapshotSupply ?? supplyData,
    workflows: workflowSourceData,
    scope: initialData.scope,
    config: initialData.config || {},
    links: createLinkHelpers(initialData.links),
    actions: actions,
    onUpdateState: handleUpdateState,
    view: view,
  };

  // Host mode: the props as plain data for the sandboxed frame. Whatever is in here
  // is readable by the code's author (the spec's residual risk), so a PAGE carries
  // no workers, no pipelines and no run. A WORKFLOW carries what its render reads
  // today -- it is the viewer's own report -- and every write it makes comes back
  // through the host (routes.ts, the action dialog, the completion question).
  const sandboxMode: 'page' | 'workflow' =
    sandbox?.mode === 'workflow' ? 'workflow' : 'page';
  const pageInstance = useMemo(() => {
    // A page has no run, so no snapshot: whatever `instance` arrived with, its
    // snapshot never reaches the frame, and supply is always the live read.
    const { snapshot: _snapshot, ...instance } = initialData.instance as Record<
      string,
      unknown
    >;
    void _snapshot;
    return instance;
  }, [initialData.instance]);
  const frameInstance = useMemo(
    () =>
      sandboxMode === 'workflow'
        ? {
            ...(initialData.instance as Record<string, unknown>),
            state: instanceState,
          }
        : { ...pageInstance, state: instanceState },
    [sandboxMode, initialData.instance, pageInstance, instanceState],
  );
  const frameView = useMemo(
    () => ({
      state: instanceState,
      isCompleted: initialData.instance.status === 'completed',
      asOf:
        initialData.instance.status === 'completed'
          ? (initialData.instance.completed_at ?? null)
          : null,
    }),
    [instanceState, initialData.instance],
  );
  const frameToday = useMemo(() => new Date().toISOString().slice(0, 10), []);
  // The names render code reads from `#user-opportunities`, cut to this
  // workflow's own opportunities (the frame is never handed the viewer's whole list).
  const frameOpportunities = useMemo(() => {
    const ids = new Set(
      [
        ...(initialData.opportunity_ids || []),
        initialData.opportunity_id,
        initialData.instance.opportunity_id,
      ].map((n) => Number(n)),
    );
    try {
      // Two carriers of the viewer's list: the multi-opp run page's own and the
      // header context selector's (the only one on a single-opportunity page).
      const all = ['user-opportunities', 'opportunity-data'].flatMap((id) => {
        const el = document.getElementById(id);
        try {
          return el ? (JSON.parse(el.textContent || '[]') as unknown[]) : [];
        } catch {
          return [];
        }
      });
      const seen = new Set<number>();
      return all
        .filter(
          (o): o is { id: number; name: string } =>
            !!o &&
            typeof o === 'object' &&
            ids.has(Number((o as { id?: unknown }).id)),
        )
        .map((o) => ({ id: Number(o.id), name: String(o.name ?? '') }))
        .filter((o) => !seen.has(o.id) && (seen.add(o.id), true));
    } catch {
      return [];
    }
  }, [
    initialData.opportunity_ids,
    initialData.opportunity_id,
    initialData.instance,
  ]);
  const framePageUrl = window.location.href;
  const sandboxPayload = useMemo<FramePayload | null>(() => {
    if (!sandbox) return null;
    const common = {
      mode: sandboxMode,
      renderCode,
      definition: definition as unknown as Record<string, unknown>,
      instance: frameInstance,
      supply: (sandboxMode === 'workflow'
        ? (snapshotSupply ?? supplyData)
        : supplyData) as Record<string, unknown>,
      workflows: workflowSourceData as Record<string, unknown>,
      scope: initialData.scope ?? null,
      config: (initialData.config || {}) as Record<string, unknown>,
      links: initialData.links,
      view: frameView,
      today: frameToday,
      mapboxToken: initialData.mapboxToken || '',
      pageUrl: framePageUrl,
    };
    // A page and a workflow are handed the same (Jonathan, 2026-10-12: "pages
    // should be completely definable like workflows are"); a page just has no run.
    return {
      ...common,
      workers: initialData.workers as unknown[],
      pipelines: pipelineData as Record<string, unknown>,
      apiEndpoints: initialData.apiEndpoints as unknown as Record<
        string,
        string | null
      >,
      flags: initialData.flags as unknown[],
      audits: initialData.audits as unknown[],
      tasks: initialData.tasks as unknown[],
      workflowActions: workflowActions as unknown[],
      isEditMode,
      userOpportunities: frameOpportunities,
      pipelinesLoading: Boolean(pipelineLoadingStatus),
    };
  }, [
    sandbox,
    sandboxMode,
    renderCode,
    definition,
    frameInstance,
    snapshotSupply,
    supplyData,
    workflowSourceData,
    initialData.scope,
    initialData.config,
    initialData.links,
    initialData.mapboxToken,
    initialData.workers,
    initialData.apiEndpoints,
    initialData.flags,
    initialData.audits,
    initialData.tasks,
    frameView,
    frameToday,
    framePageUrl,
    pipelineData,
    workflowActions,
    isEditMode,
    pipelineLoadingStatus,
    frameOpportunities,
  ]);

  // What the host does for a workflow in the frame: labs' own code, bound to this
  // workflow and run (the frame names neither).
  const sandboxWorkflow = useMemo(() => {
    if (!sandbox) return undefined;
    const ids = (xs: unknown[]) =>
      Array.from(
        new Set(
          xs
            .map((n) => Number(n))
            .filter((n) => Number.isSafeInteger(n) && n > 0),
        ),
      );
    // A page is about its scope: what is under it is what its code may name. A
    // workflow may name only its own opportunities and program.
    const scope = initialData.is_page ? initialData.scope : null;
    const opportunityIds = ids([
      ...(initialData.opportunity_ids || []),
      initialData.opportunity_id,
      initialData.instance.opportunity_id,
      ...(scope?.opportunities || []).map((o) => o.id),
      scope?.opportunity?.id,
    ]);
    const runProgram = (initialData.instance as { program_id?: number })
      .program_id;
    const programIds = ids([
      initialData.program_id,
      runProgram,
      ...(scope?.programs || []).map((p) => p.id),
      scope?.program?.id,
    ]);
    const ownOpp =
      Number(
        initialData.instance.opportunity_id || initialData.opportunity_id,
      ) || 0;
    const ownProgram = Number(initialData.program_id || runProgram) || 0;
    const activeJob = (
      initialData.instance.state as { active_job?: { job_id?: unknown } }
    )?.active_job?.job_id;
    const routeContext: RouteContext = {
      labsOrigin: sandbox?.origin || window.location.origin,
      definitionId: Number(initialData.definition_id) || null,
      runId: Number(initialData.instance.id) || null,
      opportunityIds,
      programIds,
      organizationKey: scope?.organization?.slug || null,
      readableDefinitionIds: ids(sandbox?.readable_definitions || []),
      jobIds: typeof activeJob === 'string' ? [activeJob] : [],
      defaultScope: ownOpp
        ? { name: 'opportunity_id', value: String(ownOpp) }
        : ownProgram
          ? { name: 'program_id', value: String(ownProgram) }
          : null,
    };
    return {
      csrfToken,
      routeContext,
      queryPipelineRows: (alias: string, query: Record<string, unknown>) =>
        actions.queryPipelineRows(alias, query as PipelineRowsQuery),
      updateState: (state: Record<string, unknown>) => handleUpdateState(state),
      runAction: (
        key: string,
        args: Record<string, unknown>,
        options: { qaOnly: boolean },
      ) =>
        actions.runAction(
          key,
          args as { workers: WorkflowActionWorker[] },
          options,
        ),
      actionKeys: () => workflowActions.map((a) => a.key),
      completeRun: async () => {
        if (initialData.instance.status === 'completed') {
          return { success: false, error: 'This run is already complete.' };
        }
        if (!initialData.apiEndpoints.completeRun) {
          return {
            success: false,
            error: 'Completion is not available for this run.',
          };
        }
        return actions.completeRun(initialData.instance.id);
      },
    };
  }, [
    sandboxMode,
    sandbox?.origin,
    sandbox?.readable_definitions,
    csrfToken,
    initialData.opportunity_ids,
    initialData.opportunity_id,
    initialData.instance,
    initialData.definition_id,
    initialData.program_id,
    initialData.apiEndpoints.completeRun,
    actions,
    handleUpdateState,
    workflowActions,
  ]);

  return (
    <div className="flex h-full">
      {/* A workflow action a button asked to run: previewed, confirmed, followed.
          Owned by the runner, not render code, so no template can skip the
          confirmation. */}
      {actionRequest && initialData.apiEndpoints.actionBase && (
        <ActionDialog
          key={actionRequest.seq}
          request={actionRequest.request}
          actionBase={initialData.apiEndpoints.actionBase}
          scopeQuery={initialData.apiEndpoints.actionScope}
          executionBase={initialData.apiEndpoints.actionExecutionBase || ''}
          csrfToken={csrfToken}
          onClose={(execution) => {
            actionRequest.resolve(execution);
            actionOpenRef.current = false;
            setActionRequest(null);
          }}
        />
      )}
      {/* Main Content */}
      <div
        className={`flex-1 min-w-0 transition-all duration-300 ${
          isChatOpen ? 'mr-96' : ''
        }`}
      >
        {/* Tab Bar - Only show in edit mode when pipelines exist */}
        {isEditMode &&
          definition.pipeline_sources &&
          definition.pipeline_sources.length > 0 && (
            <div className="bg-white border-b border-gray-200 px-4">
              <div className="flex items-center gap-1">
                {/* Workflow Tab */}
                <button
                  onClick={() => handleReturnToWorkflow()}
                  className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                    activeTab === 'workflow'
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                >
                  Workflow
                </button>
                {/* Pipeline Tabs */}
                {definition.pipeline_sources.map(
                  (source: { pipeline_id: number; alias: string }) => (
                    <button
                      key={source.pipeline_id}
                      onClick={() => loadPipelineForEditing(source.pipeline_id)}
                      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
                        activeTab === 'pipeline' &&
                        selectedPipelineId === source.pipeline_id
                          ? 'border-orange-600 text-orange-600'
                          : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                      }`}
                    >
                      <Database size={14} />
                      {source.alias}
                      {isLoadingPipelineEditor &&
                        selectedPipelineId === source.pipeline_id && (
                          <i className="fa-solid fa-spinner fa-spin ml-1" />
                        )}
                    </button>
                  ),
                )}
              </div>
            </div>
          )}

        {/* Control Bar - Only show for workflow tab in edit mode */}
        {activeTab === 'workflow' && isEditMode && (
          <div className="bg-white border-b border-gray-200 px-4 py-2 mb-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              {/* Edit Code Button */}
              <button
                onClick={handleOpenCodeEditor}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-md transition-colors"
                title="Edit render code"
              >
                <Code size={16} />
                Edit Code
              </button>

              {/* Pipeline Data Indicator */}
              {definition.pipeline_sources &&
                definition.pipeline_sources.length > 0 && (
                  <div className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 bg-blue-50 rounded-md">
                    <Database size={16} />
                    <span>
                      {definition.pipeline_sources.length} pipeline
                      {definition.pipeline_sources.length > 1 ? 's' : ''}
                    </span>
                    {pipelineLoadingStatus && (
                      <i className="fa-solid fa-spinner fa-spin ml-1" />
                    )}
                  </div>
                )}

              {/* Save/Discard buttons */}
              {hasChanges && (
                <>
                  <div className="h-4 w-px bg-gray-300" />
                  <span className="text-sm text-amber-600 font-medium">
                    Unsaved changes
                  </span>
                  <button
                    onClick={handleSave}
                    disabled={isSaving}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-green-600 hover:bg-green-700 disabled:bg-green-400 rounded-md transition-colors"
                  >
                    {isSaving ? (
                      <i className="fa-solid fa-spinner fa-spin" />
                    ) : saveSuccess ? (
                      <Check size={16} />
                    ) : (
                      <Save size={16} />
                    )}
                    {saveSuccess ? 'Saved!' : 'Save'}
                  </button>
                  <button
                    onClick={handleDiscard}
                    disabled={isSaving}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 disabled:bg-gray-50 rounded-md transition-colors"
                  >
                    <RotateCcw size={16} />
                    Discard
                  </button>
                </>
              )}
            </div>

            {/* Right side - status */}
            <div className="text-xs text-gray-500">
              {renderError ? (
                <span className="text-red-600">Render error</span>
              ) : (
                <span className="text-green-600">Ready</span>
              )}
            </div>
          </div>
        )}

        {/* Workflow Content - Only show when workflow tab is active */}
        {activeTab === 'workflow' && (
          <>
            {/* Show error if any */}
            {error && (
              <div className="bg-red-50 border-l-4 border-red-400 p-4 mb-4 mx-4">
                <div className="flex">
                  <div className="flex-shrink-0">
                    <i className="fa-solid fa-circle-exclamation text-red-400"></i>
                  </div>
                  <div className="ml-3">
                    <p className="text-sm text-red-700">{error}</p>
                    <button
                      onClick={() => setError(null)}
                      className="text-sm text-red-600 underline mt-1"
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Render error */}
            {renderError && (
              <div className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-4 mx-4">
                <div className="flex">
                  <div className="flex-shrink-0">
                    <i className="fa-solid fa-exclamation-triangle text-yellow-400"></i>
                  </div>
                  <div className="ml-3">
                    <p className="text-sm text-yellow-700">
                      Render error: {renderError}
                    </p>
                    <p className="text-xs text-yellow-600 mt-1">
                      Open the code editor to fix the issue.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Workflow framework gate: auth-requires must be satisfied
                before any template-specific render fires. This way every
                template gets uniform "Authorize X" UX without each
                template having to implement its own check. */}
            <div className="px-4">
              {authChecking ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                  <i className="fa-solid fa-spinner fa-spin text-2xl mb-3" />
                  <p className="text-sm">Checking authorization…</p>
                </div>
              ) : missingAuth.length > 0 ? (
                <div className="space-y-4 max-w-2xl mx-auto">
                  <div className="bg-white rounded-lg shadow-sm p-6">
                    <h2 className="text-xl font-bold text-gray-900">
                      {definition.name || 'Workflow'}
                    </h2>
                    <p className="text-gray-500 mt-1">
                      Authorization required before this workflow can run.
                    </p>
                  </div>
                  <div className="bg-amber-50 border border-amber-300 rounded-lg p-5">
                    <div className="flex items-center gap-2 mb-3">
                      <i className="fa-solid fa-key text-amber-600"></i>
                      <span className="font-semibold text-amber-800">
                        This workflow requires{' '}
                        {missingAuth.length === 1 ? 'one' : missingAuth.length}{' '}
                        additional authorization
                        {missingAuth.length === 1 ? '' : 's'}
                      </span>
                    </div>
                    <p className="text-sm text-amber-800 mb-4">
                      Click each <strong>Authorize</strong> button below to
                      grant access. After authorizing, you'll be returned here
                      automatically.
                    </p>
                    <div className="space-y-2 mb-4">
                      {missingAuth.map((svc) => {
                        // Distinguish "token dead — re-auth fixes it" from
                        // "user has no permission to this domain — re-auth
                        // WON'T fix it; needs an HQ admin." Hiding the
                        // Authorize button on no_domain_access stops the loop.
                        const noDomainAccess =
                          svc.reason === 'no_domain_access';
                        return (
                          <div key={svc.key} className="flex items-start gap-3">
                            <i className="fa-solid fa-circle-xmark text-amber-600 mt-1"></i>
                            <div className="flex-1">
                              <div className="flex items-center gap-3">
                                <span className="text-sm font-medium text-gray-800 w-40">
                                  {svc.label || svc.key}
                                </span>
                                {noDomainAccess ? (
                                  <>
                                    <span className="px-3 py-1.5 bg-red-100 text-red-800 text-sm rounded border border-red-300">
                                      Account lacks access to{' '}
                                      {svc.domain || 'this domain'}
                                    </span>
                                    {/* Log-out escape hatch. The "no domain
                                        access" state happens when the cached
                                        OAuth token is bound to a CommCare HQ
                                        account that doesn't have permission
                                        on this opportunity's domain. The
                                        framework correctly hides Authorize
                                        because re-authorizing as the same user
                                        won't help — but the user often DOES
                                        have a different account that has
                                        access. Logging out clears the token;
                                        the next refresh shows the normal
                                        Authorize button so they can sign in
                                        with the right account. */}
                                    {svc.key === 'commcare_hq' && (
                                      <a
                                        href="/labs/commcare/logout/"
                                        className="px-3 py-1.5 bg-white border border-red-400 text-red-800 text-sm rounded hover:bg-red-50 no-underline"
                                      >
                                        Log out of CommCare HQ
                                      </a>
                                    )}
                                  </>
                                ) : svc.authorize_url ? (
                                  <a
                                    href={svc.authorize_url}
                                    className="px-3 py-1.5 bg-amber-600 text-white text-sm rounded hover:bg-amber-700 no-underline"
                                  >
                                    Authorize {svc.label || svc.key}
                                  </a>
                                ) : (
                                  <span className="text-sm text-gray-500">
                                    No authorize URL available — contact
                                    support.
                                  </span>
                                )}
                              </div>
                              {svc.message && (
                                <p className="text-xs text-gray-700 mt-1 ml-0">
                                  {svc.message}
                                </p>
                              )}
                            </div>
                          </div>
                        );
                      })}
                      {/* Show services that ARE active so users see overall progress */}
                      {authStatus &&
                        authRequires
                          .filter((k) => authStatus[k]?.active)
                          .map((k) => (
                            <div key={k} className="flex items-center gap-3">
                              <i className="fa-solid fa-circle-check text-green-600"></i>
                              <span className="text-sm font-medium text-gray-800 w-40">
                                {authStatus[k]?.label || k}
                              </span>
                              <span className="text-sm text-green-700">
                                Active
                              </span>
                            </div>
                          ))}
                    </div>
                    <button
                      onClick={refreshAuthStatus}
                      className="px-4 py-2 bg-white border border-amber-400 text-amber-800 text-sm rounded hover:bg-amber-100"
                    >
                      <i className="fa-solid fa-rotate mr-1"></i> Re-check after
                      authorizing
                    </button>
                  </div>
                </div>
              ) : pipelineLoadingStatus && !renderWhileLoading ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                  <i className="fa-solid fa-spinner fa-spin text-2xl mb-3" />
                  <p className="text-sm">{pipelineLoadingStatus}</p>
                </div>
              ) : (
                <>
                  {/* A workflow that opts in renders while its pipelines load and
                      shows the stream's progress as a strip, not a blank page.
                      Its render code must tolerate `pipelines[alias]` being
                      absent until the stream completes. */}
                  {renderWhileLoading && pipelineLoadingStatus && (
                    <div className="flex items-center gap-2 text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 mb-3">
                      <i className="fa-solid fa-spinner fa-spin" />
                      <span>{pipelineLoadingStatus}</span>
                    </div>
                  )}
                  {rawFetchAnomalies.length > 0 && (
                    <div className="bg-amber-50 border border-amber-300 rounded-lg p-4 mb-4 space-y-3">
                      {rawFetchAnomalies.map(
                        (a: RawFetchAnomaly & { alias: string }, i: number) => {
                          const buildRetryUrl = (
                            extra: Record<string, string>,
                          ) => {
                            const retryUrl = new URL(window.location.href);
                            retryUrl.searchParams.set('refresh', '1');
                            Object.entries(extra).forEach(([k, v]) =>
                              retryUrl.searchParams.set(k, v),
                            );
                            return retryUrl.pathname + retryUrl.search;
                          };
                          return (
                            <div
                              key={`${a.alias}-${a.opportunity_id}-${i}`}
                              className="text-sm text-amber-900"
                            >
                              <strong>{a.alias}</strong> (opportunity{' '}
                              {a.opportunity_id}): the latest data refresh came
                              back with only{' '}
                              {a.attempted_count.toLocaleString()} rows, well
                              below the {a.previous_count.toLocaleString()}{' '}
                              already on record — showing the previous data
                              instead of a possibly-incomplete refresh. This
                              usually clears up on its own within a few minutes.
                              <div className="mt-1 flex gap-4">
                                <a
                                  href={buildRetryUrl({})}
                                  className="underline font-semibold"
                                >
                                  Retry now
                                </a>
                                <a
                                  href={buildRetryUrl({
                                    accept_low_count: '1',
                                  })}
                                  className="underline font-semibold"
                                >
                                  Use the lower count anyway
                                </a>
                              </div>
                            </div>
                          );
                        },
                      )}
                    </div>
                  )}
                  {sandbox && sandboxPayload ? (
                    <SandboxFrame
                      labsOrigin={sandbox.origin || window.location.origin}
                      payload={sandboxPayload}
                      mode={sandboxMode}
                      workflow={sandboxWorkflow}
                      querySupply={(alias, args) =>
                        actions.querySupply(alias, { args } as SupplyQuery)
                      }
                      queryWorkflow={(alias) => actions.queryWorkflow(alias)}
                      shareSelection={(selection) => shareSelection(selection)}
                    />
                  ) : (
                    <DynamicWorkflow
                      // Remount, rather than re-render, the first time pipeline
                      // data lands. DynamicWorkflow memoises the compiled
                      // component on [babelLoaded, renderCode, onError] -- NOT on
                      // `pipelines` -- so without this key it re-renders in place
                      // when data arrives after an empty first render.
                      //
                      // Render code that early-returns (an "authorization
                      // needed" or "no data" panel) BEFORE declaring the rest of
                      // its hooks then runs a different number of hooks on the
                      // second render, and React throws #310 -- the whole report
                      // is replaced by "Error rendering workflow". Reproduced on
                      // workflow 13005 on 2026-09-17 while prototyping the
                      // fallback below; 13005 is not unusual in having that
                      // shape.
                      //
                      // Costs nothing on the normal path: the runner shows its
                      // own loading panel instead of the report while
                      // pipelineLoadingStatus is set (unless a definition opts
                      // into config.renderWhileLoading), so the report usually
                      // mounts once, already holding data, and this key never
                      // changes. It only flips for a definition that renders
                      // while loading, or after the fallback recovers -- exactly
                      // the cases that would otherwise break.
                      key={
                        Object.keys(pipelineData).length
                          ? 'pipelines-loaded'
                          : 'pipelines-empty'
                      }
                      {...workflowProps}
                      renderCode={renderCode}
                      onError={handleRenderError}
                    />
                  )}
                </>
              )}
            </div>
          </>
        )}

        {/* Staff only: whether this workflow's code runs in the sandbox (rolled out
            per workflow, workflow/sandbox.py), and a way to try it there. */}
        {activeTab === 'workflow' &&
          initialData.viewer_is_staff &&
          !initialData.is_page && (
            <div
              className="px-4 mt-6 mb-2 text-xs text-gray-400"
              data-testid="workflow-sandbox-status"
            >
              {sandbox ? (
                <span>
                  <i className="fa-solid fa-shield-halved mr-1" />
                  Staff: this workflow runs in the sandbox.
                </span>
              ) : (
                <span>
                  Staff: this workflow does not run in the sandbox yet.{' '}
                  <a
                    className="underline hover:text-gray-600"
                    href={(() => {
                      const u = new URL(window.location.href);
                      u.searchParams.set('sandbox', '1');
                      return u.pathname + u.search;
                    })()}
                  >
                    View it sandboxed
                  </a>
                </span>
              )}
            </div>
          )}

        {/* Pipeline Editor - Show when pipeline tab is active */}
        {activeTab === 'pipeline' &&
          selectedPipelineId &&
          selectedPipelineData && (
            <div className="h-[calc(100vh-120px)]">
              <PipelineEditor
                definitionId={selectedPipelineId}
                opportunityId={primaryOppId || 0}
                initialDefinition={{
                  id: selectedPipelineId,
                  name:
                    ((
                      selectedPipelineData.definition as Record<string, unknown>
                    ).name as string) || '',
                  description:
                    ((
                      selectedPipelineData.definition as Record<string, unknown>
                    ).description as string) || '',
                  version:
                    ((
                      selectedPipelineData.definition as Record<string, unknown>
                    ).version as number) || 1,
                  schema:
                    ((
                      selectedPipelineData.definition as Record<string, unknown>
                    ).schema as Record<string, unknown>) || {},
                  is_shared:
                    ((
                      selectedPipelineData.definition as Record<string, unknown>
                    ).is_shared as boolean) || false,
                  shared_scope:
                    ((
                      selectedPipelineData.definition as Record<string, unknown>
                    ).shared_scope as string) || 'global',
                }}
                initialPreviewData={
                  selectedPipelineData.previewData as {
                    rows: Record<string, unknown>[];
                    metadata: Record<string, unknown>;
                  }
                }
                isEmbedded={true}
                onClose={handleReturnToWorkflow}
                onSave={() => {
                  // Re-stream pipeline data - may have become stale after schema change
                  streamPipelineData();
                }}
                apiEndpoints={{
                  getDefinition: `/labs/workflow/api/pipeline/${selectedPipelineId}/`,
                  updateSchema: `/labs/workflow/api/pipeline/${selectedPipelineId}/schema/`,
                  preview: `/labs/workflow/api/pipeline/${selectedPipelineId}/preview/`,
                  sqlPreview: `/labs/workflow/api/pipeline/${selectedPipelineId}/sql/`,
                  chatHistory: `/labs/workflow/api/pipeline/${selectedPipelineId}/chat/history/`,
                  chatClear: `/labs/workflow/api/pipeline/${selectedPipelineId}/chat/clear/`,
                }}
              />
            </div>
          )}

        {/* Loading state for pipeline editor */}
        {activeTab === 'pipeline' && isLoadingPipelineEditor && (
          <div className="flex items-center justify-center h-64">
            <div className="text-center">
              <i className="fa-solid fa-spinner fa-spin text-2xl text-gray-400 mb-2" />
              <p className="text-sm text-gray-500">
                Loading pipeline editor...
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Chat Toggle Button - Only show in edit mode */}
      {isEditMode && !isChatOpen && (
        <button
          onClick={() => setIsChatOpen(true)}
          className="fixed bottom-6 right-6 z-50 flex items-center justify-center w-14 h-14 rounded-full shadow-lg transition-all duration-300 bg-blue-600 hover:bg-blue-700 text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          title="Edit workflow with AI"
        >
          <MessageCircle size={24} />
        </button>
      )}

      {/* Chat Panel - Only show in edit mode */}
      {isEditMode && (
        <div
          className={`fixed top-0 right-0 h-full w-96 bg-white shadow-2xl z-40 transform transition-transform duration-300 ease-in-out ${
            isChatOpen ? 'translate-x-0' : 'translate-x-full'
          }`}
        >
          {isChatOpen && (
            <AIChat
              agentType="workflow"
              definitionId={initialData.definition_id}
              opportunityId={primaryOppId}
              currentDefinition={definition}
              currentRenderCode={renderCode}
              onDefinitionUpdate={handleWorkflowUpdate}
              onRenderCodeUpdate={handleRenderCodeUpdate}
              onPipelineSchemaUpdate={async (pipelineId, schema) => {
                // Save pipeline schema via API
                try {
                  const response = await fetch(
                    `/labs/workflow/api/pipeline/${pipelineId}/schema/`,
                    {
                      method: 'POST',
                      headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                      },
                      body: JSON.stringify({ schema }),
                    },
                  );
                  if (response.ok) {
                    // Refresh pipeline data if we're viewing this pipeline
                    if (selectedPipelineId === pipelineId) {
                      loadPipelineForEditing(pipelineId);
                    }
                    // Also refresh workflow pipeline data
                    streamPipelineData();
                  }
                } catch (e) {
                  console.error('Failed to update pipeline schema:', e);
                }
              }}
              activeContext={
                activeTab === 'pipeline' &&
                selectedPipelineId &&
                selectedPipelineData
                  ? {
                      active_tab: 'pipeline',
                      pipeline_id: selectedPipelineId,
                      pipeline_alias:
                        definition.pipeline_sources?.find(
                          (s: { pipeline_id: number }) =>
                            s.pipeline_id === selectedPipelineId,
                        )?.alias || '',
                      pipeline_schema: (
                        selectedPipelineData.definition as Record<
                          string,
                          unknown
                        >
                      )?.schema as Record<string, unknown>,
                    }
                  : { active_tab: 'workflow' }
              }
              historyEndpoint={`/labs/workflow/api/${initialData.definition_id}/chat/history/`}
              clearEndpoint={`/labs/workflow/api/${initialData.definition_id}/chat/clear/`}
              onClose={() => setIsChatOpen(false)}
            />
          )}
        </div>
      )}

      {/* Code Editor Modal - Only show in edit mode */}
      {isEditMode && isCodeEditorOpen && (
        <div className="fixed inset-0 z-50 overflow-hidden">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black bg-opacity-50"
            onClick={() => setIsCodeEditorOpen(false)}
          />

          {/* Modal */}
          <div className="absolute inset-4 md:inset-8 lg:inset-16 bg-white rounded-lg shadow-xl flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <h2 className="text-lg font-semibold text-gray-900">
                Edit Render Code
              </h2>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleApplyCode}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md transition-colors"
                >
                  <Check size={16} />
                  Apply Changes
                </button>
                <button
                  onClick={() => setIsCodeEditorOpen(false)}
                  className="p-1.5 text-gray-500 hover:text-gray-700 rounded-md hover:bg-gray-100"
                >
                  <X size={20} />
                </button>
              </div>
            </div>

            {/* Code Editor */}
            <div className="flex-1 overflow-hidden p-4">
              <textarea
                value={editingCode}
                onChange={(e) => setEditingCode(e.target.value)}
                className="w-full h-full font-mono text-sm p-4 border border-gray-300 rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-gray-50"
                spellCheck={false}
                placeholder="Enter your React component code here..."
              />
            </div>

            {/* Footer with hints */}
            <div className="px-4 py-2 border-t border-gray-200 bg-gray-50 text-xs text-gray-500">
              <p>
                The code should define a function named{' '}
                <code className="bg-gray-200 px-1 rounded">WorkflowUI</code>{' '}
                that receives props:{' '}
                <code className="bg-gray-200 px-1 rounded">definition</code>,
                <code className="bg-gray-200 px-1 rounded">instance</code>,
                <code className="bg-gray-200 px-1 rounded">workers</code>,
                <code className="bg-gray-200 px-1 rounded">pipelines</code>,
                <code className="bg-gray-200 px-1 rounded">links</code>,
                <code className="bg-gray-200 px-1 rounded">actions</code>,
                <code className="bg-gray-200 px-1 rounded">onUpdateState</code>
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Wait for DOM to be ready
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('workflow-root');
  const dataScript = document.getElementById('workflow-data');

  if (container) {
    try {
      if (!dataScript) {
        console.error('No workflow data script found');
        return;
      }

      const workflowData = JSON.parse(
        dataScript.textContent || '{}',
      ) as ExtendedWorkflowData;
      const csrfToken = container.dataset.csrfToken || '';

      // Expose API endpoints globally for render code to use
      // This allows workflow render code to access SSE streaming URLs
      (
        window as unknown as { WORKFLOW_API_ENDPOINTS: Record<string, string> }
      ).WORKFLOW_API_ENDPOINTS = workflowData.apiEndpoints || {};

      console.log('Workflow data loaded:', workflowData);

      const root = createRoot(container);
      root.render(
        <React.StrictMode>
          <WorkflowRunner workflowData={workflowData} csrfToken={csrfToken} />
        </React.StrictMode>,
      );

      console.log('Workflow mounted successfully');
    } catch (e) {
      console.error('Failed to initialize workflow:', e);
      container.innerHTML = `
        <div class="bg-red-50 border-l-4 border-red-400 p-4">
          <div class="flex">
            <div class="flex-shrink-0">
              <i class="fa-solid fa-circle-exclamation text-red-400"></i>
            </div>
            <div class="ml-3">
              <p class="text-sm text-red-700">Failed to initialize workflow: ${e}</p>
            </div>
          </div>
        </div>
      `;
    }
  }
});
