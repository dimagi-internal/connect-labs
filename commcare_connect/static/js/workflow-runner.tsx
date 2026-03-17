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
import { AIChat } from '@/components/AIChat';
import { DEFAULT_RENDER_CODE } from '@/components/workflow/defaultRenderCode';
import { streamTaskProgress } from './task-progress';
import type {
  WorkflowProps,
  WorkflowDataFromDjango,
  LinkHelpers,
  AuditUrlParams,
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
  ActiveJobState,
  SaveWorkerResultParams,
  SaveWorkerResultResponse,
  CompleteRunParams,
  CompleteRunResponse,
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

/**
 * Create action handlers for workflow operations.
 */
function createActionHandlers(csrfToken: string): ActionHandlers {
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
      const taskResult = await createActionHandlers(csrfToken).createTask(
        params,
      );
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
    ): (() => void) => {
      const eventSource = new EventSource(
        `/labs/workflow/api/job/${taskId}/status/`,
      );

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.error) {
            onError(data.error);
            eventSource.close();
            return;
          }

          if (data.data?.status === 'completed') {
            onComplete(data.data.results || {});
            eventSource.close();
            return;
          }

          if (data.data?.status === 'cancelled') {
            onCancelled();
            eventSource.close();
            return;
          }

          // Stream item result for real-time row updates
          if (data.data?.item_result) {
            onItemResult(data.data.item_result);
          }

          // Progress update
          onProgress({
            status: data.data?.status || 'running',
            current_stage: data.data?.current_stage,
            total_stages: data.data?.total_stages,
            stage_name: data.data?.stage_name,
            processed: data.data?.processed,
            total: data.data?.total,
            message: data.message,
          });
        } catch {
          console.error('Failed to parse SSE event:', event.data);
        }
      };

      eventSource.onerror = () => {
        eventSource.close();
        onError('Connection lost');
      };

      // Return cleanup function
      return () => eventSource.close();
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
      // Use the shared task-progress utility with polling fallback
      return streamTaskProgress(
        {
          streamUrl: `/audit/api/audit/task/${taskId}/stream/`,
          statusUrl: `/audit/api/audit/task/${taskId}/status/`,
          useFallback: true,
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

/**
 * Create link helpers from base URLs.
 */
function createLinkHelpers(baseUrls: {
  auditUrlBase: string;
  taskUrlBase: string;
}): LinkHelpers {
  return {
    auditUrl: (params: AuditUrlParams): string => {
      const urlParams = new URLSearchParams();

      if (params.username) urlParams.set('usernames', params.username);
      if (params.usernames) urlParams.set('usernames', params.usernames);
      if (params.count) urlParams.set('count', String(params.count));
      urlParams.set('audit_type', params.audit_type || 'last_n_per_flw');
      urlParams.set('granularity', params.granularity || 'per_flw');
      if (params.start_date) urlParams.set('start_date', params.start_date);
      if (params.end_date) urlParams.set('end_date', params.end_date);
      if (params.title) urlParams.set('title', params.title);
      if (params.tag) urlParams.set('tag', params.tag);
      if (params.auto_create) urlParams.set('auto_create', 'true');

      return `${baseUrls.auditUrlBase}?${urlParams.toString()}`;
    },

    taskUrl: (params: TaskUrlParams): string => {
      const urlParams = new URLSearchParams();

      if (params.username) urlParams.set('username', params.username);
      if (params.title) urlParams.set('title', params.title);
      if (params.description) urlParams.set('description', params.description);
      if (params.audit_session_id)
        urlParams.set('audit_session_id', String(params.audit_session_id));
      if (params.workflow_instance_id)
        urlParams.set(
          'workflow_instance_id',
          String(params.workflow_instance_id),
        );
      if (params.priority) urlParams.set('priority', params.priority);

      return `${baseUrls.taskUrlBase}?${urlParams.toString()}`;
    },
  };
}

// Extended data type
interface ExtendedWorkflowData extends WorkflowDataFromDjango {
  render_code?: string;
  opportunity_id?: number;
  is_edit_mode?: boolean;
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
      : // Need to load
        'Connecting...',
  );
  const [error, setError] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isCodeEditorOpen, setIsCodeEditorOpen] = useState(false);
  const [editingCode, setEditingCode] = useState('');
  const [isSaving, setIsSaving] = useState(false);
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
    if (initialData.opportunity_id) {
      url.searchParams.set(
        'opportunity_id',
        String(initialData.opportunity_id),
      );
    }
    // Forward tolerance param from page URL to pipeline stream
    const pageParams = new URLSearchParams(window.location.search);
    const tolerance = pageParams.get('tolerance');
    if (tolerance) {
      url.searchParams.set('tolerance', tolerance);
    }

    const eventSource = new EventSource(url.toString());

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.error) {
          setPipelineLoadingStatus(null);
          setError(data.error);
          eventSource.close();
          return;
        }

        // Progress update - show status message
        if (data.message) {
          setPipelineLoadingStatus(data.message);
        }

        // Data complete - update state and allow render
        if (data.data?.pipelines) {
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
      setError('Pipeline stream connection lost');
      eventSource.close();
    };

    return () => eventSource.close();
  }, [
    initialData.apiEndpoints.streamPipelineData,
    initialData.opportunity_id,
    definition.pipeline_sources,
  ]);

  // Load pipeline data on mount via SSE streaming
  useEffect(() => {
    // Only stream if we have pipeline sources and no data yet
    if (
      definition.pipeline_sources?.length &&
      !Object.keys(pipelineData).length
    ) {
      const cleanup = streamPipelineData();
      return cleanup;
    }
  }, [definition.pipeline_sources, pipelineData, streamPipelineData]);

  // Auto-reconnect to running jobs on page load
  // This allows users to close the browser and return later while the Celery task continues
  useEffect(() => {
    const activeJob = initialData.instance.state?.active_job as
      | ActiveJobState
      | undefined;

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
        // Fetch pipeline definition
        const defResponse = await fetch(
          `/labs/workflow/api/pipeline/${pipelineId}/?opportunity_id=${initialData.opportunity_id}`,
        );
        if (!defResponse.ok) throw new Error('Failed to load pipeline');
        const defData = await defResponse.json();

        // Fetch preview data
        const previewResponse = await fetch(
          `/labs/workflow/api/pipeline/${pipelineId}/preview/?opportunity_id=${initialData.opportunity_id}`,
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
    [initialData.opportunity_id],
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
  const actions = useMemo(() => {
    return createActionHandlers(csrfToken);
  }, [csrfToken]);

  // Create props for workflow component
  const workflowProps: WorkflowProps = {
    definition: definition,
    instance: {
      ...initialData.instance,
      state: instanceState,
    },
    workers: initialData.workers,
    pipelines: pipelineData,
    links: createLinkHelpers(initialData.links),
    actions: actions,
    onUpdateState: handleUpdateState,
  };

  return (
    <div className="flex h-full">
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

            {/* Render the workflow - only when pipeline data is loaded */}
            <div className="px-4">
              {pipelineLoadingStatus ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                  <i className="fa-solid fa-spinner fa-spin text-2xl mb-3" />
                  <p className="text-sm">{pipelineLoadingStatus}</p>
                </div>
              ) : (
                <DynamicWorkflow
                  {...workflowProps}
                  renderCode={renderCode}
                  onError={handleRenderError}
                />
              )}
            </div>
          </>
        )}

        {/* Pipeline Editor - Show when pipeline tab is active */}
        {activeTab === 'pipeline' &&
          selectedPipelineId &&
          selectedPipelineData && (
            <div className="h-[calc(100vh-120px)]">
              <PipelineEditor
                definitionId={selectedPipelineId}
                opportunityId={initialData.opportunity_id || 0}
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
              opportunityId={initialData.opportunity_id}
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
