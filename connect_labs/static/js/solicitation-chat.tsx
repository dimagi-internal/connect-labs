import React from 'react';
import { createRoot } from 'react-dom/client';
import { AIChat } from '@/components/AIChat';

document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('solicitation-chat-root');
  if (!container) return;

  const root = createRoot(container);

  function SolicitationChat() {
    const [isOpen, setIsOpen] = React.useState(false);

    return (
      <>
        {/* Floating toggle button */}
        {!isOpen && (
          <button
            onClick={() => setIsOpen(true)}
            className="fixed bottom-6 right-6 z-50 flex items-center justify-center w-14 h-14 rounded-full shadow-lg transition-all duration-300 bg-brand-indigo hover:bg-brand-deep-purple text-white"
            aria-label="Open AI assistant"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </button>
        )}

        {/* Slide-out panel */}
        <div
          className={`fixed top-0 right-0 h-full w-full sm:w-[420px] bg-white shadow-2xl z-40 transform transition-transform duration-300 ease-in-out ${
            isOpen ? 'translate-x-0' : 'translate-x-full'
          } flex flex-col`}
        >
          {isOpen && (
            <AIChat
              agentType="solicitations"
              definitionId="solicitations"
              currentDefinition={{}}
              onDefinitionUpdate={() => {}}
              historyEndpoint=""
              clearEndpoint=""
              onClose={() => setIsOpen(false)}
              title="Solicitations AI"
              placeholder="Ask about solicitations or create a new one..."
              examplePrompts={[
                'List all active solicitations',
                'Create an RFP for CHW training',
                'Show me the latest responses',
              ]}
            />
          )}
        </div>
      </>
    );
  }

  root.render(
    <React.StrictMode>
      <SolicitationChat />
    </React.StrictMode>,
  );
});
