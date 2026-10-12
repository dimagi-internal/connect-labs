'use client';

/**
 * DynamicWorkflow - Renders AI-generated workflow components.
 *
 * This component takes JSX code as a string, transpiles it using Babel,
 * and renders it dynamically. It provides the workflow props including
 * pipeline data to the generated component. The compile step lives in
 * ./renderCode, shared with the sandboxed page frame (./sandbox/frame.tsx), so
 * the run page and the frame compile render code identically.
 */

import React, { useState, useMemo } from 'react';
import type { WorkflowProps } from './types';
import {
  RenderErrorBoundary as DynamicErrorBoundary,
  compileRenderCode as createComponentFromCode,
  useBabel,
} from './renderCode';

interface DynamicWorkflowProps extends WorkflowProps {
  /** The JSX code to render */
  renderCode: string;
  /** Callback when there's an error */
  onError?: (error: string) => void;
}

/**
 * DynamicWorkflow component
 */
export function DynamicWorkflow({
  renderCode,
  definition,
  instance,
  workers,
  pipelines,
  supply,
  workflows,
  scope,
  config,
  links,
  actions,
  onUpdateState,
  view,
  onError,
}: DynamicWorkflowProps) {
  const babelLoaded = useBabel();
  const [error, setError] = useState<string | null>(null);

  // Memoize the component creation to avoid re-transpiling on every render
  const DynamicComponent = useMemo(() => {
    console.log(
      'DynamicWorkflow: useMemo triggered, babelLoaded:',
      babelLoaded,
      'renderCode length:',
      renderCode?.length,
    );

    if (!babelLoaded) {
      console.log('DynamicWorkflow: Babel not loaded yet');
      return null;
    }

    if (!renderCode) {
      console.log('DynamicWorkflow: No render code provided');
      return null;
    }

    setError(null);
    const component = createComponentFromCode(renderCode);

    if (!component) {
      const errorMsg = 'Failed to create component from render code';
      setError(errorMsg);
      onError?.(errorMsg);
      return null;
    }

    return component;
  }, [babelLoaded, renderCode, onError]);

  // Loading state
  if (!babelLoaded) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-center">
          <i className="fa-solid fa-spinner fa-spin text-3xl text-blue-600 mb-4"></i>
          <p className="text-gray-600">Loading renderer...</p>
        </div>
      </div>
    );
  }

  // Error state
  if (error || !DynamicComponent) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <h3 className="text-red-800 font-medium mb-2">
          Error loading workflow UI
        </h3>
        <p className="text-red-600 text-sm">
          {error || 'Failed to create component'}
        </p>
        <details className="mt-2">
          <summary className="text-sm text-gray-600 cursor-pointer">
            View render code
          </summary>
          <pre className="mt-2 text-xs bg-gray-100 p-2 rounded overflow-auto max-h-64">
            {renderCode}
          </pre>
        </details>
      </div>
    );
  }

  // Render the dynamic component
  return (
    <DynamicErrorBoundary onError={onError}>
      <DynamicComponent
        definition={definition}
        instance={instance}
        workers={workers}
        pipelines={pipelines}
        supply={supply || {}}
        workflows={workflows || {}}
        scope={scope}
        config={config || {}}
        links={links}
        actions={actions}
        onUpdateState={onUpdateState}
        view={view}
      />
    </DynamicErrorBoundary>
  );
}

export default DynamicWorkflow;
