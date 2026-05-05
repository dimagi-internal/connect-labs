'use client';

/**
 * DynamicWorkflow - Renders AI-generated workflow components.
 *
 * This component takes JSX code as a string, transpiles it using Babel,
 * and renders it dynamically. It provides the workflow props including
 * pipeline data to the generated component.
 */

import React, { useState, useEffect, useMemo } from 'react';
import type { WorkflowProps } from './types';

// Babel standalone for browser transpilation
declare global {
  interface Window {
    Babel?: {
      transform: (
        code: string,
        options: { presets: string[] },
      ) => { code: string };
    };
  }
}

interface DynamicWorkflowProps extends WorkflowProps {
  /** The JSX code to render */
  renderCode: string;
  /** Callback when there's an error */
  onError?: (error: string) => void;
}

/**
 * Load Babel standalone if not already loaded
 */
function useBabel(): boolean {
  const [loaded, setLoaded] = useState(!!window.Babel);

  useEffect(() => {
    if (window.Babel) {
      setLoaded(true);
      return;
    }

    const script = document.createElement('script');
    script.src = 'https://unpkg.com/@babel/standalone@7.23.5/babel.min.js';
    script.async = true;
    script.onload = () => setLoaded(true);
    script.onerror = () => console.error('Failed to load Babel');
    document.head.appendChild(script);

    return () => {
      // Don't remove - other components might need it
    };
  }, []);

  return loaded;
}

/**
 * Transpile and create a component from JSX code
 */
function createComponentFromCode(code: string): React.FC<WorkflowProps> | null {
  if (!window.Babel) {
    console.error('DynamicWorkflow: Babel not loaded yet');
    return null;
  }

  try {
    console.log(
      'DynamicWorkflow: Attempting to transpile code, length:',
      code.length,
    );

    // Wrap the function in a return statement so we can eval it
    const wrappedCode = `
      (function(React) {
        ${code}
        return WorkflowUI;
      })
    `;

    // Transpile JSX to JS
    const transpiled = window.Babel.transform(wrappedCode, {
      presets: ['react'],
    });

    console.log('DynamicWorkflow: Transpiled successfully');

    // eslint-disable-next-line no-eval
    const factory = eval(transpiled.code);
    const Component = factory(React);

    if (!Component) {
      console.error('DynamicWorkflow: WorkflowUI function not found in code');
      return null;
    }

    console.log('DynamicWorkflow: Component created successfully');
    return Component;
  } catch (error) {
    console.error(
      'DynamicWorkflow: Failed to transpile/create component:',
      error,
    );
    console.error(
      'DynamicWorkflow: Code that failed (first 500 chars):',
      code.substring(0, 500),
    );
    return null;
  }
}

/**
 * Error boundary for catching render errors in dynamic components
 */
class DynamicErrorBoundary extends React.Component<
  { children: React.ReactNode; onError?: (error: string) => void },
  { hasError: boolean; error: string | null }
> {
  constructor(props: {
    children: React.ReactNode;
    onError?: (error: string) => void;
  }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message };
  }

  componentDidCatch(error: Error) {
    this.props.onError?.(error.message);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <h3 className="text-red-800 font-medium mb-2">
            Error rendering workflow
          </h3>
          <p className="text-red-600 text-sm font-mono">{this.state.error}</p>
        </div>
      );
    }

    return this.props.children;
  }
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
        links={links}
        actions={actions}
        onUpdateState={onUpdateState}
        view={view}
      />
    </DynamicErrorBoundary>
  );
}

export default DynamicWorkflow;
