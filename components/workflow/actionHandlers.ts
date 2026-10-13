/**
 * The `actions` render code is handed for jobs, tasks, audits and coaching: each a
 * fetch to a labs endpoint. Shared by the run page (workflow-runner.tsx, on labs'
 * own origin) and the sandboxed workflow frame (./sandbox/frame.tsx), where the
 * same calls go out through the frame's `fetch`, which is a bridge request the
 * host checks against ./sandbox/routes.ts and makes as the viewer. So both places
 * run this one implementation.
 */

import { streamTaskProgress } from '../../connect_labs/static/js/task-progress';
import type {
  ActionHandlers,
  CreateTaskParams,
  TaskResult,
  OCSStatusResult,
  OCSBotsResult,
  OCSSessionParams,
  OCSInitiateResult,
  CreateTaskWithOCSParams,
  TaskWithOCSResult,
  SaveWorkerResultParams,
  SaveWorkerResultResponse,
  CompleteRunParams,
  CompleteRunResponse,
  TaskUrlParams,
} from './types';

export function createActionHandlers(csrfToken: string): ActionHandlers {
  return {
    createTask: async (params: CreateTaskParams): Promise<TaskResult> => {
      try {
        const response = await fetch('/tasks/api/single-create/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({
            username: params.username,
            title: params.title,
            description: params.description || '',
            priority: params.priority || 'medium',
            flw_name: params.flw_name || params.username,
          }),
        });

        const data = await response.json();
        return {
          success: data.success,
          task_id: data.task_id,
          error: data.error,
        };
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to create task',
        };
      }
    },

    checkOCSStatus: async (): Promise<OCSStatusResult> => {
      try {
        const response = await fetch('/labs/workflow/api/ocs/status/');
        return await response.json();
      } catch (e) {
        return {
          connected: false,
          error: e instanceof Error ? e.message : 'Failed to check OCS status',
        };
      }
    },

    listOCSBots: async (): Promise<OCSBotsResult> => {
      try {
        const response = await fetch('/labs/workflow/api/ocs/bots/');

        if (response.status === 401) {
          const data = await response.json();
          return {
            success: false,
            needs_oauth: true,
            error: data.error || 'OCS authentication required',
          };
        }

        return await response.json();
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to list OCS bots',
        };
      }
    },

    initiateOCSSession: async (
      taskId: number,
      params: OCSSessionParams,
    ): Promise<OCSInitiateResult> => {
      try {
        const response = await fetch(`/tasks/${taskId}/ai/initiate/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({
            identifier: params.identifier,
            experiment: params.experiment,
            prompt_text: params.prompt_text,
            platform: params.platform || 'commcare_connect',
            start_new_session: params.start_new_session ?? true,
          }),
        });

        const data = await response.json();
        return {
          success: data.success,
          message: data.message,
          error: data.error,
        };
      } catch (e) {
        return {
          success: false,
          error:
            e instanceof Error ? e.message : 'Failed to initiate OCS session',
        };
      }
    },

    createTaskWithOCS: async (
      params: CreateTaskWithOCSParams,
    ): Promise<TaskWithOCSResult> => {
      const taskResult =
        await createActionHandlers(csrfToken).createTask(params);
      if (!taskResult.success || !taskResult.task_id) {
        return taskResult;
      }

      if (params.ocs && taskResult.task_id) {
        const ocsResult = await createActionHandlers(
          csrfToken,
        ).initiateOCSSession(taskResult.task_id, {
          identifier: params.username,
          ...params.ocs,
        });
        return { ...taskResult, ocs: ocsResult };
      }

      return taskResult;
    },

    // Job Management Actions
    startJob: async (
      runId: number,
      jobConfig: Record<string, unknown>,
    ): Promise<{ success: boolean; task_id?: string; error?: string }> => {
      try {
        const response = await fetch(
          `/labs/workflow/api/run/${runId}/job/start/`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify({ job_config: jobConfig }),
          },
        );

        return await response.json();
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to start job',
        };
      }
    },

    cancelJob: async (
      taskId: string,
      runId?: number,
    ): Promise<{ success: boolean; error?: string }> => {
      try {
        const response = await fetch(
          `/labs/workflow/api/job/${taskId}/cancel/`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify({ run_id: runId }),
          },
        );

        return await response.json();
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to cancel job',
        };
      }
    },

    deleteRun: async (
      runId: number,
    ): Promise<{ success: boolean; error?: string }> => {
      try {
        const response = await fetch(
          `/labs/workflow/api/run/${runId}/delete/`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken,
            },
          },
        );

        return await response.json();
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to delete run',
        };
      }
    },

    streamJobProgress: (
      taskId: string,
      onProgress: (data: {
        status: string;
        current_stage?: number;
        total_stages?: number;
        stage_name?: string;
        processed?: number;
        total?: number;
        message?: string;
      }) => void,
      onItemResult: (item: Record<string, unknown>) => void,
      onComplete: (results: Record<string, unknown>) => void,
      onError: (error: string) => void,
      onCancelled: () => void,
      // Optional: lets the server read this run's authoritative active_job so a
      // reconnect to a dead/expired task terminates instead of spinning forever.
      runId?: number,
    ): (() => void) => {
      // Poll-first: hit the JSON status endpoint on an interval instead of holding
      // an SSE connection. A held stream pins a worker thread for the whole job, and
      // the ASGI web tier has only WEB_CONCURRENCY workers, so concurrent users (or
      // per-row streams) starve each other. Polling holds nothing and scales; the
      // progress it reads is already persisted to run state, so a missed poll is
      // never a correctness problem. The SSE endpoint remains for opt-in low-latency.
      const qs = runId != null ? `?run_id=${runId}` : '';
      return streamTaskProgress(
        {
          transport: 'poll',
          statusUrl: `/labs/workflow/api/job/${taskId}/status.json${qs}`,
          // SSE fallback target if a caller ever forces transport: 'sse'.
          streamUrl: `/labs/workflow/api/job/${taskId}/status/${qs}`,
        },
        {
          onProgress: (data) =>
            onProgress({
              status: data.status || 'running',
              current_stage: data.current_stage,
              total_stages: data.total_stages,
              stage_name: data.stage_name,
              processed: data.processed,
              total: data.total,
              message: data.message,
            }),
          onItemResult,
          onComplete: (result) => onComplete(result || {}),
          onError,
          onCancelled,
        },
      );
    },

    // Audit Creation Actions
    createAudit: async (config: {
      opportunities: Array<{ id: number; name?: string }>;
      criteria: Record<string, unknown>;
      visit_ids?: number[];
      flw_visit_ids?: Record<string, number[]>;
      template_overrides?: Record<string, unknown>;
      workflow_run_id?: number;
      ai_agent_id?: string; // Optional AI agent to run after creation
      ai_auto_apply_actions?: string[]; // Which AI verdicts pre-tag a human result ([] = flag only)
    }): Promise<{
      success: boolean;
      task_id?: string;
      error?: string;
    }> => {
      try {
        const response = await fetch('/audit/api/audit/create-async/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify(config),
        });

        return await response.json();
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to create audit',
        };
      }
    },

    getAuditStatus: async (
      taskId: string,
    ): Promise<{
      status: string;
      message?: string;
      current_stage?: number;
      total_stages?: number;
      stage_name?: string;
      processed?: number;
      total?: number;
      result?: Record<string, unknown>;
      error?: string;
    }> => {
      try {
        const response = await fetch(`/audit/api/audit/task/${taskId}/status/`);
        return await response.json();
      } catch (e) {
        return {
          status: 'error',
          error: e instanceof Error ? e.message : 'Failed to get status',
        };
      }
    },

    streamAuditProgress: (
      taskId: string,
      onProgress: (data: {
        status: string;
        message?: string;
        current_stage?: number;
        total_stages?: number;
        stage_name?: string;
        processed?: number;
        total?: number;
      }) => void,
      onComplete: (result: Record<string, unknown>) => void,
      onError: (error: string) => void,
    ): (() => void) => {
      // Poll-first (no held SSE connection — see streamJobProgress). The SSE
      // stream endpoint stays wired as the opt-in transport / fallback.
      return streamTaskProgress(
        {
          transport: 'poll',
          statusUrl: `/audit/api/audit/task/${taskId}/status/`,
          streamUrl: `/audit/api/audit/task/${taskId}/stream/`,
        },
        { onProgress, onComplete, onError },
      );
    },

    cancelAudit: async (
      taskId: string,
    ): Promise<{ success: boolean; error?: string }> => {
      try {
        const response = await fetch(
          `/audit/api/audit/task/${taskId}/cancel/`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken,
            },
          },
        );

        if (!response.ok) {
          const errorData = await response.json();
          return {
            success: false,
            error: errorData.error || 'Failed to cancel',
          };
        }

        return { success: true };
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to cancel',
        };
      }
    },

    // MBW Monitoring Actions
    saveWorkerResult: async (
      runId: number,
      params: SaveWorkerResultParams,
    ): Promise<SaveWorkerResultResponse> => {
      try {
        const response = await fetch(
          `/labs/workflow/api/run/${runId}/worker-result/`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify(params),
          },
        );

        return await response.json();
      } catch (e) {
        return {
          success: false,
          error:
            e instanceof Error ? e.message : 'Failed to save worker result',
        };
      }
    },

    completeRun: async (
      runId: number,
      params?: CompleteRunParams,
    ): Promise<CompleteRunResponse> => {
      try {
        const response = await fetch(
          `/labs/workflow/api/run/${runId}/complete/`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify(params || {}),
          },
        );

        return await response.json();
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to complete run',
        };
      }
    },

    openTaskCreator: (params: TaskUrlParams): void => {
      const urlParams = new URLSearchParams();
      if (params.username) urlParams.set('username', params.username);
      if (params.title) urlParams.set('title', params.title);
      if (params.description) urlParams.set('description', params.description);
      if (params.coaching_prompt)
        urlParams.set('coaching_prompt', params.coaching_prompt);
      if (params.workflow_instance_id)
        urlParams.set(
          'workflow_instance_id',
          String(params.workflow_instance_id),
        );
      if (params.priority) urlParams.set('priority', params.priority);

      window.open(`/tasks/new/?${urlParams.toString()}`, '_blank');
    },

    // --- Generic task management handlers (reusable by any workflow template) ---

    getTaskDetail: async (taskId: number): Promise<Record<string, unknown>> => {
      try {
        const response = await fetch(`/tasks/api/${taskId}/`, {
          headers: { 'X-CSRFToken': csrfToken },
        });
        try {
          return await response.json();
        } catch {
          return { success: false, error: `Server error: ${response.status}` };
        }
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to fetch task',
        };
      }
    },

    getAITranscript: async (
      taskId: number,
      sessionId?: string,
      refresh?: boolean,
    ): Promise<Record<string, unknown>> => {
      try {
        const params = new URLSearchParams();
        if (sessionId) params.set('session_id', sessionId);
        if (refresh) params.set('refresh', 'true');
        const response = await fetch(
          `/tasks/${taskId}/ai/transcript/?${params.toString()}`,
          {
            headers: { 'X-CSRFToken': csrfToken },
          },
        );
        try {
          return await response.json();
        } catch {
          return { success: false, error: `Server error: ${response.status}` };
        }
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to fetch transcript',
        };
      }
    },

    getAISessions: async (taskId: number): Promise<Record<string, unknown>> => {
      try {
        const response = await fetch(`/tasks/${taskId}/ai/sessions/`);
        const data = await response.json();
        return data;
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to fetch AI sessions',
        };
      }
    },

    updateTask: async (
      taskId: number,
      data: Record<string, unknown>,
    ): Promise<Record<string, unknown>> => {
      try {
        const response = await fetch(`/tasks/api/${taskId}/update/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify(data),
        });
        try {
          return await response.json();
        } catch {
          return { success: false, error: `Server error: ${response.status}` };
        }
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to update task',
        };
      }
    },

    saveAITranscript: async (
      taskId: number,
      data: Record<string, unknown>,
    ): Promise<Record<string, unknown>> => {
      try {
        const response = await fetch(`/tasks/${taskId}/ai/save-transcript/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify(data),
        });
        try {
          return await response.json();
        } catch {
          return { success: false, error: `Server error: ${response.status}` };
        }
      } catch (e) {
        return {
          success: false,
          error: e instanceof Error ? e.message : 'Failed to save transcript',
        };
      }
    },
  };
}
