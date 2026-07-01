import React from 'react';
import { createRoot } from 'react-dom/client';
import ReportBuilder from '@/components/code-gen/CodeEditor';

// Wait for DOM to be ready
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('code-editor-root');

  if (container) {
    const root = createRoot(container);
    root.render(
      <React.StrictMode>
        <ReportBuilder />
      </React.StrictMode>,
    );
  }
});
