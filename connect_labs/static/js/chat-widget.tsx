import React from 'react';
import { createRoot } from 'react-dom/client';
import { ChatWidget } from '@/components/ChatWidget';

// Wait for DOM to be ready
document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('chat-widget-root');

  if (container) {
    // Create a root at the body level for the widget
    // The container div is just for data attributes
    const widgetRoot = document.createElement('div');
    document.body.appendChild(widgetRoot);
    const root = createRoot(widgetRoot);

    root.render(
      <React.StrictMode>
        <ChatWidget containerId="chat-widget-root" />
      </React.StrictMode>,
    );
  }
});
