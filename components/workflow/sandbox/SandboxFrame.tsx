/**
 * The runner's HOST MODE for a page: draws the sandboxed frame where DynamicWorkflow
 * would have run the render code, passes it the page's data, and answers it
 * (./host.ts). Everything the person sees from the host -- a confirm, the page's
 * render error, "this page tried to leave its frame" -- is drawn here, as text.
 */

import React, { useEffect, useRef, useState } from 'react';
import { createSandboxHost, type SandboxHost } from './host';
import type { FramePayload } from './protocol';

export interface SandboxFrameProps {
  shellUrl: string;
  labsOrigin: string;
  payload: FramePayload;
  querySupply(alias: string, args: Record<string, unknown>): Promise<unknown>;
  queryWorkflow(alias: string): Promise<unknown>;
  shareSelection(selection: {
    visible_ids: string[];
    drilled: Record<string, unknown>;
  }): void;
}

function saveFile(file: { filename: string; mime: string; bytes: Uint8Array }) {
  const blob = new Blob([file.bytes as BlobPart], { type: file.mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function SandboxFrame(props: SandboxFrameProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<SandboxHost | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const [left, setLeft] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [question, setQuestion] = useState<{
    text: string;
    answer: (yes: boolean) => void;
  } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const host = createSandboxHost({
      container: containerRef.current,
      shellUrl: props.shellUrl,
      labsOrigin: props.labsOrigin,
      payload: propsRef.current.payload,
      onLeave: () => setLeft(true),
      handlers: {
        querySupply: (alias, args) => propsRef.current.querySupply(alias, args),
        queryWorkflow: (alias) => propsRef.current.queryWorkflow(alias),
        navigate: (href, newTab) => {
          if (newTab) window.open(href, '_blank', 'noopener,noreferrer');
          else window.location.assign(href);
        },
        shareSelection: (selection) =>
          propsRef.current.shareSelection(selection),
        copyText: (text) => navigator.clipboard.writeText(text),
        confirm: (text) =>
          new Promise<boolean>((resolve) => {
            // One question at a time: a second while one is open is a no.
            setQuestion((current) => {
              if (current) {
                resolve(false);
                return current;
              }
              return {
                text,
                answer: (yes) => {
                  setQuestion(null);
                  resolve(yes);
                },
              };
            });
          }),
        download: saveFile,
        reportError: (message) => setRenderError(message),
      },
    });
    hostRef.current = host;
    return () => {
      host.destroy();
      hostRef.current = null;
    };
    // The frame is drawn once per shell; data changes go through update().
  }, [props.shellUrl, props.labsOrigin]);

  useEffect(() => {
    hostRef.current?.update(props.payload);
  }, [props.payload]);

  if (left) {
    return (
      <div
        className="bg-red-50 border-l-4 border-red-400 p-4 text-sm text-red-700"
        data-testid="page-sandbox-left"
      >
        This page tried to leave its frame, so it has been stopped. Reload to
        draw it again.
      </div>
    );
  }

  return (
    <div>
      {renderError && (
        <div
          className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-4 text-sm text-yellow-700"
          data-testid="page-sandbox-error"
        >
          The page failed to draw: {renderError}
        </div>
      )}
      <div ref={containerRef} data-testid="page-sandbox" />
      {question && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          data-testid="page-sandbox-confirm"
        >
          <div className="max-w-md w-full rounded-lg bg-white shadow-xl p-5">
            <div className="text-xs uppercase tracking-wide text-gray-500 mb-2">
              This page asks
            </div>
            <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">
              {question.text}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => question.answer(false)}
                className="px-3 py-1.5 text-sm rounded-md bg-gray-100 hover:bg-gray-200 text-gray-800"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => question.answer(true)}
                className="px-3 py-1.5 text-sm rounded-md bg-brand-indigo text-white hover:opacity-90"
              >
                OK
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
