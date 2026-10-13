/**
 * Turning a definition's render code into a React component: shared by the run page
 * (DynamicWorkflow, on labs' own origin) and the sandboxed page frame
 * (components/workflow/sandbox/frame.tsx, an opaque origin), so the two compile
 * render code identically -- same Babel, same wrapper, same error boundary.
 */

import React, { useEffect, useState } from 'react';
import type { WorkflowProps } from './types';

/** The pinned Babel the frame's CSP names in full (workflow/sandbox.py BABEL_URL). */
export const BABEL_URL =
  'https://unpkg.com/@babel/standalone@7.23.5/babel.min.js';

declare global {
  interface Window {
    Babel?: {
      transform: (
        code: string,
        options: { presets: string[]; plugins?: unknown[] },
      ) => { code: string };
    };
  }
}

/** Load Babel standalone if it is not already on the page. */
export function useBabel(): boolean {
  const [loaded, setLoaded] = useState(!!window.Babel);

  useEffect(() => {
    if (window.Babel) {
      setLoaded(true);
      return;
    }
    const script = document.createElement('script');
    script.src = BABEL_URL;
    script.async = true;
    script.onload = () => setLoaded(true);
    script.onerror = () => console.error('Failed to load Babel');
    document.head.appendChild(script);
  }, []);

  return loaded;
}

/**
 * How the sandbox frame compiles render code differently (components/workflow/
 * sandbox/rewrite.ts): extra Babel plugins, and values the compiled code reaches by
 * name, passed as parameters of its wrapper (never globals). The run page passes
 * none, so it compiles exactly as before.
 */
export interface CompileOptions {
  plugins?: unknown[];
  params?: Record<string, unknown>;
}

/**
 * Transpile render code (which defines `function WorkflowUI(props)`) and return the
 * component, or null with the reason logged.
 */
export function compileRenderCode(
  code: string,
  options: CompileOptions = {},
): React.FC<WorkflowProps> | null {
  if (!window.Babel) {
    console.error('renderCode: Babel not loaded yet');
    return null;
  }
  try {
    const names = Object.keys(options.params || {}).filter((n) =>
      /^[A-Za-z_$][\w$]*$/.test(n),
    );
    const wrappedCode = `
      (function(${['React', ...names].join(', ')}) {
        ${code}
        return WorkflowUI;
      })
    `;
    const transpiled = window.Babel.transform(wrappedCode, {
      presets: ['react'],
      ...(options.plugins && options.plugins.length
        ? { plugins: options.plugins }
        : {}),
    });
    // eslint-disable-next-line no-eval
    const factory = eval(transpiled.code);
    const Component = factory(
      React,
      ...names.map((n) => (options.params as Record<string, unknown>)[n]),
    );
    if (!Component) {
      console.error('renderCode: WorkflowUI function not found in code');
      return null;
    }
    return Component;
  } catch (error) {
    console.error('renderCode: failed to transpile/create component:', error);
    console.error(
      'renderCode: code that failed (first 500 chars):',
      code.substring(0, 500),
    );
    return null;
  }
}

/** Error boundary around a render: a crash shows its message, not a blank page. */
export class RenderErrorBoundary extends React.Component<
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
