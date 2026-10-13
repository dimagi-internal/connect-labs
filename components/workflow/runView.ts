/**
 * The `view` render code is handed: a run's data whether it is live or saved, and
 * the helpers for its flags, audits and tasks. Shared by the run page
 * (workflow-runner.tsx) and the sandboxed workflow frame (./sandbox/frame.tsx), so
 * render code sees the same `view` in both. What differs is passed in: how the
 * run is completed and how a question is asked (the run page's own dialogs, or the
 * host's, over the bridge), and the `fetch` the flag writes go out on.
 */

import type {
  Audit,
  Flag,
  PipelineResult,
  RunView,
  SupplyResult,
  Task,
  WorkerData,
  WorkflowActionSpec,
  WorkflowState,
} from './types';

/** The run fields the view reads (a WorkflowInstance, or the page's blank one). */
export interface RunViewInstance {
  id: number;
  status: string;
  opportunity_id?: number | null;
  completed_at?: string | null;
  snapshot?: Record<string, unknown> | null;
}

export interface RunViewInput {
  instance: RunViewInstance;
  /** The page's opportunity, when the run names none of its own. */
  opportunityId?: number | null;
  workers: WorkerData[];
  pipelines: Record<string, PipelineResult>;
  supply: Record<string, SupplyResult>;
  state: WorkflowState;
  flags?: Flag[];
  audits?: Audit[];
  tasks?: Task[];
  workflowActions?: WorkflowActionSpec[];
  /** Sent with the flag writes on the run page; ignored by the frame's fetch. */
  csrfToken: string;
  complete(opts?: { confirm?: string }): Promise<boolean>;
  confirm(text: string): Promise<boolean>;
  shareSelection(selection: {
    visible_ids?: string[];
    drilled?: Record<string, unknown>;
  }): void;
}

// useRunView: abstracts snapshot-vs-live data reads and exposes view.complete().
//
// While the run is in_progress, returns the live workers/pipelines/state from
// props. Once the run is completed, returns whatever was captured in
// `instance.snapshot` at completion time. Render code that uses `view.X`
// works the same in both modes — the contract is maintained by the template's
// `snapshot_inputs` declaration on the BE side.
//
// See WORKFLOW_REFERENCE.md §"Saved-runs templates" for the full contract.
export function buildRunView(input: RunViewInput): RunView {
  const instance = input.instance;
  const isCompleted = instance.status === 'completed';
  const snapshot = (instance.snapshot || null) as {
    workers?: WorkerData[];
    pipelines?: Record<string, PipelineResult>;
    supply?: Record<string, SupplyResult>;
    state?: WorkflowState;
  } | null;

  // Flags are always queried live — Flag lifecycle state-of-truth lives
  // on the Flag record, not the run snapshot. Newest-first so render code
  // that sorts by flagged_at gets sensible ordering.
  const flagsList = (input.flags ?? [])
    .slice()
    .sort((a, b) => (b.flagged_at || '').localeCompare(a.flagged_at || ''));
  const flagsByFlw = new Map<string, Flag[]>();
  for (const f of flagsList) {
    const list = flagsByFlw.get(f.flw_id);
    if (list) {
      list.push(f);
    } else {
      flagsByFlw.set(f.flw_id, [f]);
    }
  }
  const flagsFor = (username: string) => flagsByFlw.get(username) ?? [];

  // Audits + Tasks created against this run — same live-query
  // philosophy as flags (the workflow_run snapshot never freezes them).
  // Render code reads via view.auditsFor(username) / view.tasksFor
  // (username) to know whether a per-row Action affordance should
  // surface as "View" instead of "Create" — i.e., the action was
  // already taken. Works on both in_progress and completed runs.
  const auditsList = (input.audits ?? []) as Audit[];
  const auditsByFlw = new Map<string, Audit[]>();
  for (const a of auditsList) {
    const list = auditsByFlw.get(a.flw_id);
    if (list) {
      list.push(a);
    } else {
      auditsByFlw.set(a.flw_id, [a]);
    }
  }
  const auditsFor = (username: string) => auditsByFlw.get(username) ?? [];

  const tasksList = (input.tasks ?? []) as Task[];
  const tasksByFlw = new Map<string, Task[]>();
  for (const t of tasksList) {
    const list = tasksByFlw.get(t.flw_id);
    if (list) {
      list.push(t);
    } else {
      tasksByFlw.set(t.flw_id, [t]);
    }
  }
  const tasksFor = (username: string) => tasksByFlw.get(username) ?? [];

  // Dedup key for ensureAutoFlags. We track in a closure-scoped Set so
  // repeated render-effect calls within the same page load are no-ops
  // after the first POST, even before the BE reflects the new flag.
  const persistedKeys = new Set<string>(
    flagsList.map((f) => `${f.flw_id}::${f.flag_key}`),
  );
  const inflightKeys = new Set<string>();
  const ensureAutoFlags = async (
    computed: Array<{
      flw_id: string;
      flag_key: string;
      flag_label?: string;
      evidence?: Record<string, unknown>;
    }>,
  ): Promise<Flag[]> => {
    if (isCompleted) return [];
    const runId = instance.id;
    const oppId = instance.opportunity_id || input.opportunityId;
    if (!runId || !oppId) return [];
    const toPost = computed.filter((c) => {
      const k = `${c.flw_id}::${c.flag_key}`;
      if (persistedKeys.has(k) || inflightKeys.has(k)) return false;
      inflightKeys.add(k);
      return true;
    });
    if (!toPost.length) return [];
    const created: Flag[] = [];
    await Promise.all(
      toPost.map(async (c) => {
        try {
          const res = await fetch(`/labs/workflow/api/run/${runId}/flags/`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': input.csrfToken,
            },
            body: JSON.stringify({
              opportunity_id: oppId,
              flw_id: c.flw_id,
              flag_key: c.flag_key,
              flag_label: c.flag_label || c.flag_key,
              evidence: c.evidence || {},
              source: 'auto',
            }),
          });
          if (res.ok) {
            const body = (await res.json()) as Flag;
            persistedKeys.add(`${c.flw_id}::${c.flag_key}`);
            created.push(body);
          } else {
            console.warn(
              `ensureAutoFlags: POST failed for ${c.flw_id}/${c.flag_key} (${res.status})`,
            );
          }
        } catch (e) {
          console.warn('ensureAutoFlags: network error', e);
        }
      }),
    );
    return created;
  };

  // Snapshot preference: serve the stamped snapshot whenever one
  // exists, not just on completed runs. The framework only writes a
  // snapshot at completion in production (`connect_labs/workflow/
  // views.py:430` comment: "Snapshot is null while in_progress;
  // populated on completion"), so the only time an in_progress run
  // has a snapshot is when something explicitly seeded one — currently
  // the synthetic generator, which stamps a preview snapshot onto its
  // backdated in_progress run so the manager-flow demo doesn't open
  // on a real-data live pipeline that happens to back the synthetic
  // opp's CSV folder. Templates can still read `instance.snapshot`
  // directly if they want even finer control.
  if (snapshot) {
    return {
      workers: (snapshot.workers as WorkerData[]) ?? input.workers,
      pipelines:
        (snapshot.pipelines as Record<string, PipelineResult>) ??
        input.pipelines,
      supply: (snapshot.supply as Record<string, SupplyResult>) ?? input.supply,
      state: (snapshot.state as WorkflowState) ?? input.state,
      isCompleted,
      asOf: isCompleted ? (instance.completed_at ?? null) : null,
      complete: input.complete,
      flags: flagsList,
      flagsFor,
      ensureAutoFlags,
      audits: auditsList,
      auditsFor,
      tasks: tasksList,
      tasksFor,
      shareSelection: input.shareSelection,
      confirm: input.confirm,
      workflowActions: input.workflowActions,
    };
  }

  return {
    workers: input.workers,
    pipelines: input.pipelines,
    supply: input.supply,
    state: input.state,
    isCompleted: false,
    asOf: null,
    complete: input.complete,
    flags: flagsList,
    flagsFor,
    ensureAutoFlags,
    audits: auditsList,
    auditsFor,
    tasks: tasksList,
    tasksFor,
    shareSelection: input.shareSelection,
    confirm: input.confirm,
    workflowActions: input.workflowActions,
  };
}
