/**
 * Shared utilities for streaming Celery task progress via SSE.
 *
 * Provides a unified interface for both React (workflow-runner) and
 * Alpine.js (audit wizard) components to consume task progress updates.
 */

/**
 * Standard progress data structure from Celery tasks.
 */
export interface TaskProgress {
  status: 'pending' | 'running' | 'completed' | 'failed' | 'error' | string;
  message?: string;
  stage_name?: string;
  current_stage?: number;
  total_stages?: number;
  processed?: number;
  total?: number;
  result?: Record<string, unknown>;
  error?: string;
}

/**
 * Callbacks for task progress events.
 */
export interface TaskProgressCallbacks {
  /** Called on each progress update */
  onProgress: (data: TaskProgress) => void;
  /** Called when task completes successfully */
  onComplete: (result: Record<string, unknown>) => void;
  /** Called when task fails or connection is lost */
  onError: (error: string) => void;
}

/**
 * Options for task progress streaming.
 */
export interface TaskProgressOptions {
  /** URL for SSE stream endpoint */
  streamUrl: string;
  /** URL for polling fallback (optional) */
  statusUrl?: string;
  /** Polling interval in ms when using fallback (default: 1000) */
  pollInterval?: number;
  /** Whether to use polling fallback on SSE error (default: true) */
  useFallback?: boolean;
}

/**
 * Create an SSE stream for Celery task progress with optional polling fallback.
 *
 * @param options - Stream configuration options
 * @param callbacks - Progress, completion, and error callbacks
 * @returns Cleanup function to close the connection
 *
 * @example
 * ```typescript
 * const cleanup = streamTaskProgress(
 *   {
 *     streamUrl: `/audit/api/audit/task/${taskId}/stream/`,
 *     statusUrl: `/audit/api/audit/task/${taskId}/status/`,
 *   },
 *   {
 *     onProgress: (data) => setProgress(data),
 *     onComplete: (result) => handleComplete(result),
 *     onError: (error) => handleError(error),
 *   }
 * );
 *
 * // Later, to cleanup:
 * cleanup();
 * ```
 */
export function streamTaskProgress(
  options: TaskProgressOptions,
  callbacks: TaskProgressCallbacks,
): () => void {
  const {
    streamUrl,
    statusUrl,
    pollInterval = 1000,
    useFallback = true,
  } = options;
  const { onProgress, onComplete, onError } = callbacks;

  let closed = false;
  let eventSource: EventSource | null = null;
  let pollTimeoutId: ReturnType<typeof setTimeout> | null = null;

  /**
   * Handle incoming progress data from either SSE or polling.
   */
  const handleProgressData = (data: TaskProgress): boolean => {
    if (closed) return true;

    if (data.status === 'completed') {
      onComplete(data.result || {});
      return true; // Signal to close connection
    }

    if (data.status === 'failed' || data.status === 'error' || data.error) {
      onError(data.error || data.message || 'Task failed');
      return true; // Signal to close connection
    }

    onProgress(data);
    return false; // Continue streaming
  };

  /**
   * Poll for status updates (fallback when SSE fails).
   */
  const pollStatus = async (): Promise<void> => {
    if (closed || !statusUrl) return;

    try {
      const response = await fetch(statusUrl);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data: TaskProgress = await response.json();
      const shouldStop = handleProgressData(data);

      if (!shouldStop && !closed) {
        pollTimeoutId = setTimeout(pollStatus, pollInterval);
      }
    } catch (e) {
      if (!closed) {
        // Increase interval on errors, but keep trying
        pollTimeoutId = setTimeout(pollStatus, pollInterval * 2);
      }
    }
  };

  /**
   * Start SSE stream.
   */
  const startStream = (): void => {
    if (closed) return;

    eventSource = new EventSource(streamUrl);

    eventSource.onmessage = (event) => {
      try {
        const data: TaskProgress = JSON.parse(event.data);
        const shouldStop = handleProgressData(data);

        if (shouldStop && eventSource) {
          eventSource.close();
          eventSource = null;
        }
      } catch (e) {
        console.error('[TaskProgress] Failed to parse SSE event:', event.data);
      }
    };

    eventSource.onerror = () => {
      if (closed) return;

      // Close the failed SSE connection
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }

      // Fall back to polling if configured
      if (useFallback && statusUrl) {
        console.log('[TaskProgress] SSE failed, falling back to polling');
        pollStatus();
      } else {
        onError('Connection lost');
      }
    };
  };

  // Start the stream
  startStream();

  // Return cleanup function
  return () => {
    closed = true;

    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }

    if (pollTimeoutId) {
      clearTimeout(pollTimeoutId);
      pollTimeoutId = null;
    }
  };
}

/**
 * Format progress as a human-readable string.
 *
 * @param progress - Progress data
 * @returns Formatted string like "Stage 2/4: Extracting images (50/100)"
 */
export function formatProgress(progress: TaskProgress): string {
  const parts: string[] = [];

  if (progress.current_stage && progress.total_stages) {
    parts.push(`Stage ${progress.current_stage}/${progress.total_stages}`);
  }

  if (progress.stage_name) {
    parts.push(progress.stage_name);
  } else if (progress.message) {
    parts.push(progress.message);
  }

  if (
    progress.processed !== undefined &&
    progress.total &&
    progress.total > 0
  ) {
    parts.push(`(${progress.processed}/${progress.total})`);
  }

  return parts.join(': ') || 'Processing...';
}

/**
 * Calculate progress percentage.
 *
 * @param progress - Progress data
 * @returns Percentage (0-100) or null if not calculable
 */
export function calculateProgressPercent(
  progress: TaskProgress,
): number | null {
  // If we have processed/total, use that
  if (
    progress.processed !== undefined &&
    progress.total !== undefined &&
    progress.total > 0
  ) {
    return Math.round((progress.processed / progress.total) * 100);
  }

  // If we have stage info, calculate based on stages
  if (
    progress.current_stage !== undefined &&
    progress.total_stages !== undefined &&
    progress.total_stages > 0
  ) {
    return Math.round((progress.current_stage / progress.total_stages) * 100);
  }

  return null;
}

// Export for use in Alpine.js via window
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).TaskProgress = {
    streamTaskProgress,
    formatProgress,
    calculateProgressPercent,
  };
}
