'use client';

/**
 * AIChat - Unified AI chat component for workflow and pipeline editors.
 *
 * Features:
 * - Real-time SSE streaming (ChatGPT-style token-by-token display)
 * - Compact dropdown model selection
 * - Multi-line textarea that grows as needed
 * - Stop button for cancelling generation
 * - Conversation history maintained per definition
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  X,
  Send,
  ChevronDown,
  RefreshCw,
  Square,
  Check,
  Code,
  Database,
} from 'lucide-react';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  definitionChanged?: boolean;
  renderCodeChanged?: boolean;
  pipelineSchemaChanged?: boolean;
  isStreaming?: boolean;
}

interface ActiveContext {
  active_tab: 'workflow' | 'pipeline';
  pipeline_id?: number;
  pipeline_alias?: string;
  pipeline_schema?: Record<string, unknown>;
}

interface AIChatProps {
  /** Agent type: 'workflow', 'pipeline', or 'solicitations' */
  agentType: 'workflow' | 'pipeline' | 'solicitations';
  /** Definition ID - used to scope chat history */
  definitionId: number | string;
  /** Opportunity ID - used to scope API requests */
  opportunityId?: number;
  /** Current definition object (workflow definition or pipeline schema) */
  currentDefinition: Record<string, unknown>;
  /** Current render code */
  currentRenderCode?: string;
  /** Callback when definition is updated */
  onDefinitionUpdate: (newDefinition: Record<string, unknown>) => void;
  /** Callback when render code is updated */
  onRenderCodeUpdate?: (newRenderCode: string) => void;
  /** Callback when pipeline schema is updated (for workflow agent with pipeline tools) */
  onPipelineSchemaUpdate?: (
    pipelineId: number,
    schema: Record<string, unknown>,
  ) => void;
  /** Active context - tells the AI which tab the user is on */
  activeContext?: ActiveContext;
  /** API endpoint for chat history */
  historyEndpoint: string;
  /** API endpoint to clear chat history */
  clearEndpoint: string;
  /** Callback to close the panel */
  onClose?: () => void;
  /** Title for the chat panel */
  title?: string;
  /** Placeholder text for input */
  placeholder?: string;
  /** Example prompts to show when empty */
  examplePrompts?: string[];
}

const MODEL_STORAGE_KEY = 'ai_chat_model';

// Available models
const AVAILABLE_MODELS = [
  {
    id: 'claude-sonnet-4.5',
    provider: 'anthropic',
    model: 'anthropic:claude-sonnet-4-5-20250929',
    name: 'Sonnet 4.5',
    fullName: 'Claude Sonnet 4.5',
  },
  {
    id: 'claude-opus-4.5',
    provider: 'anthropic',
    model: 'anthropic:claude-opus-4-5-20251101',
    name: 'Opus 4.5',
    fullName: 'Claude Opus 4.5',
  },
  {
    id: 'gpt-5.2',
    provider: 'openai',
    model: 'openai:gpt-5.2',
    name: 'GPT-5.2',
    fullName: 'GPT-5.2',
  },
  {
    id: 'gpt-5.2-snapshot',
    provider: 'openai',
    model: 'openai:gpt-5.2-2025-12-11',
    name: 'GPT-5.2 Dec',
    fullName: 'GPT-5.2 (Dec 2025)',
  },
] as const;

type ModelId = (typeof AVAILABLE_MODELS)[number]['id'];

const DEFAULT_MODEL: ModelId = 'claude-sonnet-4.5';

function getCSRFToken(): string {
  const tokenInput = document.querySelector<HTMLInputElement>(
    '[name=csrfmiddlewaretoken]',
  );
  return tokenInput ? tokenInput.value : '';
}

export function AIChat({
  agentType,
  definitionId,
  opportunityId,
  currentDefinition,
  currentRenderCode,
  onDefinitionUpdate,
  onRenderCodeUpdate,
  onPipelineSchemaUpdate,
  activeContext,
  historyEndpoint,
  clearEndpoint,
  onClose,
  title,
  placeholder,
  examplePrompts,
}: AIChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedModelId, setSelectedModelId] =
    useState<ModelId>(DEFAULT_MODEL);
  const [showModelDropdown, setShowModelDropdown] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Default values based on agent type
  const defaultTitle =
    agentType === 'solicitations'
      ? 'Solicitations AI'
      : agentType === 'workflow'
      ? 'Workflow AI Editor'
      : 'Pipeline AI Editor';
  const defaultPlaceholder =
    agentType === 'solicitations'
      ? 'Ask about solicitations...'
      : agentType === 'workflow'
      ? 'Describe changes to make...'
      : 'Ask about your pipeline...';
  const defaultExamples =
    agentType === 'solicitations'
      ? [
          'List all active solicitations',
          'Create an RFP for CHW training',
          'Show responses for solicitation 42',
        ]
      : agentType === 'workflow'
      ? [
          'Add a new status called "On Hold"',
          'Show pipeline data in the table',
          'Add a chart',
        ]
      : ['Add a weight field', 'Change to aggregated mode', 'Show a histogram'];

  const selectedModel = AVAILABLE_MODELS.find((m) => m.id === selectedModelId);

  // Initialize
  useEffect(() => {
    const savedModel = localStorage.getItem(MODEL_STORAGE_KEY);
    if (savedModel && AVAILABLE_MODELS.some((m) => m.id === savedModel)) {
      setSelectedModelId(savedModel as ModelId);
    }
    loadHistory();
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [definitionId]);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setShowModelDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Auto-resize textarea
  const adjustTextareaHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      const newHeight = Math.min(textarea.scrollHeight, 200);
      textarea.style.height = `${newHeight}px`;
    }
  }, []);

  useEffect(() => {
    adjustTextareaHeight();
  }, [input, adjustTextareaHeight]);

  const loadHistory = async () => {
    if (!definitionId) return;
    try {
      const response = await fetch(historyEndpoint);
      if (response.ok) {
        const data = await response.json();
        if (data.messages && Array.isArray(data.messages)) {
          setMessages(
            data.messages.map((m: { role: string; content: string }) => ({
              role: m.role as 'user' | 'assistant',
              content: m.content,
            })),
          );
        }
      }
    } catch (e) {
      console.error('Failed to load chat history:', e);
    }
  };

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      if (!input.trim() || isStreaming) return;

      const userMessage = input.trim();
      setInput('');
      setIsStreaming(true);
      setError(null);

      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }

      setMessages((prev) => [...prev, { role: 'user', content: userMessage }]);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: '', isStreaming: true },
      ]);

      // Use AbortController for cancellation
      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      try {
        const modelString =
          selectedModel?.model || 'anthropic:claude-sonnet-4-5-20250929';

        // Build conversation history (exclude the streaming placeholder we just added)
        const historyMessages = messages
          .filter((m) => !m.isStreaming)
          .map((m) => ({ role: m.role, content: m.content }));

        // Use POST with JSON body instead of GET with query params
        const response = await fetch('/ai/stream/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          body: JSON.stringify({
            agent: agentType,
            prompt: userMessage,
            definition_id: definitionId,
            opportunity_id: opportunityId || '',
            current_definition: currentDefinition,
            current_render_code: currentRenderCode || '',
            model: modelString,
            active_context: activeContext || null,
            messages: historyMessages,
          }),
          signal: abortController.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('No response body');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // Process complete SSE events (lines starting with "data: ")
          const lines = buffer.split('\n');
          buffer = lines.pop() || ''; // Keep incomplete line in buffer

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const jsonStr = line.slice(6); // Remove "data: " prefix
              if (!jsonStr.trim()) continue;

              try {
                const data = JSON.parse(jsonStr);

                if (data.event_type === 'delta') {
                  setMessages((prev) => {
                    const newMessages = [...prev];
                    const lastIdx = newMessages.length - 1;
                    if (lastIdx >= 0 && newMessages[lastIdx].isStreaming) {
                      newMessages[lastIdx] = {
                        ...newMessages[lastIdx],
                        content: newMessages[lastIdx].content + data.message,
                      };
                    }
                    return newMessages;
                  });
                } else if (data.event_type === 'complete' && data.data) {
                  const result = data.data;
                  console.log('[AIChat] Complete event received:', {
                    schema_changed: result.schema_changed,
                    definition_changed: result.definition_changed,
                    render_code_changed: result.render_code_changed,
                    pipeline_schema_changed: result.pipeline_schema_changed,
                    has_schema: !!result.schema,
                    has_definition: !!result.definition,
                    has_pipeline_schema_updates:
                      !!result.pipeline_schema_updates,
                  });
                  setMessages((prev) => {
                    const newMessages = [...prev];
                    const lastIdx = newMessages.length - 1;
                    if (lastIdx >= 0) {
                      newMessages[lastIdx] = {
                        role: 'assistant',
                        content: result.message || newMessages[lastIdx].content,
                        definitionChanged:
                          result.definition_changed || result.schema_changed,
                        renderCodeChanged: result.render_code_changed,
                        pipelineSchemaChanged: result.pipeline_schema_changed,
                        isStreaming: false,
                      };
                    }
                    return newMessages;
                  });

                  // Handle definition/schema update
                  if (result.definition_changed && result.definition) {
                    console.log(
                      '[AIChat] Calling onDefinitionUpdate with definition',
                    );
                    onDefinitionUpdate(result.definition);
                  } else if (result.schema_changed && result.schema) {
                    console.log(
                      '[AIChat] Calling onDefinitionUpdate with schema:',
                      result.schema,
                    );
                    onDefinitionUpdate(result.schema);
                  }

                  // Handle render code update
                  if (
                    result.render_code_changed &&
                    result.render_code &&
                    onRenderCodeUpdate
                  ) {
                    onRenderCodeUpdate(result.render_code);
                  }

                  // Handle pipeline schema updates (from workflow agent)
                  if (
                    result.pipeline_schema_changed &&
                    result.pipeline_schema_updates &&
                    onPipelineSchemaUpdate
                  ) {
                    // pipeline_schema_updates is { pipeline_id: schema }
                    Object.entries(result.pipeline_schema_updates).forEach(
                      ([pipelineId, schema]) => {
                        onPipelineSchemaUpdate(
                          parseInt(pipelineId, 10),
                          schema as Record<string, unknown>,
                        );
                      },
                    );
                  }
                } else if (data.event_type === 'error') {
                  setError(data.error || 'An error occurred');
                  setMessages((prev) => {
                    const newMessages = [...prev];
                    const lastIdx = newMessages.length - 1;
                    if (lastIdx >= 0 && newMessages[lastIdx].isStreaming) {
                      newMessages[lastIdx] = {
                        ...newMessages[lastIdx],
                        content: `Error: ${data.error || 'Unknown error'}`,
                        isStreaming: false,
                      };
                    }
                    return newMessages;
                  });
                }
              } catch (parseError) {
                console.error(
                  'Failed to parse SSE event:',
                  parseError,
                  jsonStr,
                );
              }
            }
          }
        }

        // Stream complete
        setIsStreaming(false);
        abortControllerRef.current = null;
      } catch (e) {
        if ((e as Error).name === 'AbortError') {
          // User cancelled - don't show error
          console.log('Stream cancelled by user');
        } else {
          console.error('Error during stream:', e);
          setError(String(e));
          setMessages((prev) => {
            const newMessages = [...prev];
            const lastIdx = newMessages.length - 1;
            if (lastIdx >= 0 && newMessages[lastIdx].isStreaming) {
              newMessages[lastIdx] = {
                ...newMessages[lastIdx],
                content: newMessages[lastIdx].content || 'Connection error.',
                isStreaming: false,
              };
            }
            return newMessages;
          });
        }
        setIsStreaming(false);
        abortControllerRef.current = null;
      }
    },
    [
      input,
      isStreaming,
      agentType,
      definitionId,
      opportunityId,
      currentDefinition,
      currentRenderCode,
      selectedModel,
      onDefinitionUpdate,
      onRenderCodeUpdate,
      onPipelineSchemaUpdate,
      activeContext,
    ],
  );

  const handleModelChange = (modelId: ModelId) => {
    setSelectedModelId(modelId);
    localStorage.setItem(MODEL_STORAGE_KEY, modelId);
    setShowModelDropdown(false);
  };

  const handleClearHistory = async () => {
    try {
      await fetch(clearEndpoint, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
      });
      setMessages([]);
    } catch (e) {
      console.error('Failed to clear chat history:', e);
    }
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setMessages((prev) => {
      const newMessages = [...prev];
      const lastIdx = newMessages.length - 1;
      if (lastIdx >= 0 && newMessages[lastIdx].isStreaming) {
        newMessages[lastIdx] = {
          ...newMessages[lastIdx],
          isStreaming: false,
        };
      }
      return newMessages;
    });
    setIsStreaming(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200">
        <h3 className="font-semibold text-gray-900 text-sm">
          {title || defaultTitle}
        </h3>
        <div className="flex items-center gap-1">
          <button
            onClick={handleClearHistory}
            className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
            title="Clear history"
          >
            <RefreshCw size={16} />
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
              title="Close"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 text-sm py-8">
            <p className="mb-2">Ask me to help!</p>
            <p className="text-xs text-gray-400 space-y-1">
              {(examplePrompts || defaultExamples).map((example, idx) => (
                <span key={idx} className="block">
                  Try: "{example}"
                </span>
              ))}
            </p>
          </div>
        )}

        {messages.map((message, index) => (
          <div
            key={index}
            className={`flex ${
              message.role === 'user' ? 'justify-end' : 'justify-start'
            }`}
          >
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                message.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-900'
              }`}
            >
              <p className="whitespace-pre-wrap">
                {message.content}
                {message.isStreaming && (
                  <span className="inline-flex items-center ml-1">
                    <span
                      className="animate-bounce"
                      style={{ animationDelay: '0ms' }}
                    >
                      .
                    </span>
                    <span
                      className="animate-bounce"
                      style={{ animationDelay: '150ms' }}
                    >
                      .
                    </span>
                    <span
                      className="animate-bounce"
                      style={{ animationDelay: '300ms' }}
                    >
                      .
                    </span>
                  </span>
                )}
              </p>
              {(message.definitionChanged ||
                message.renderCodeChanged ||
                message.pipelineSchemaChanged) && (
                <div className="mt-2 pt-2 border-t border-gray-200 text-xs text-green-600 space-y-1">
                  {message.definitionChanged && (
                    <div className="flex items-center gap-1">
                      <Check size={12} />
                      {agentType === 'workflow' ? 'Definition' : 'Schema'}{' '}
                      updated
                    </div>
                  )}
                  {message.pipelineSchemaChanged && (
                    <div className="flex items-center gap-1">
                      <Database size={12} />
                      Pipeline schema updated
                    </div>
                  )}
                  {message.renderCodeChanged && (
                    <div className="flex items-center gap-1">
                      <Code size={12} />
                      UI updated
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-200 bg-gray-50">
        <form onSubmit={handleSubmit} className="p-2">
          <div className="bg-white border border-gray-300 rounded-lg focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-transparent">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder || defaultPlaceholder}
              disabled={isStreaming}
              rows={1}
              className="w-full px-3 py-2.5 text-sm resize-none focus:outline-none disabled:bg-gray-50 rounded-t-lg"
              style={{ minHeight: '40px', maxHeight: '200px' }}
            />

            {/* Bottom toolbar */}
            <div className="flex items-center justify-between px-2 py-1.5 border-t border-gray-100">
              {/* Left side - Model selector */}
              <div className="flex items-center gap-2">
                <div className="relative" ref={dropdownRef}>
                  <button
                    type="button"
                    onClick={() => setShowModelDropdown(!showModelDropdown)}
                    className="flex items-center gap-1 px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-100 rounded transition-colors"
                  >
                    <span>{selectedModel?.name || 'Select model'}</span>
                    <ChevronDown
                      size={12}
                      className={`transition-transform ${
                        showModelDropdown ? 'rotate-180' : ''
                      }`}
                    />
                  </button>

                  {showModelDropdown && (
                    <div className="absolute bottom-full left-0 mb-1 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-50 py-1 max-h-64 overflow-y-auto">
                      {/* Anthropic Models */}
                      <div className="px-2 py-1 text-[10px] font-medium text-gray-400 uppercase tracking-wide">
                        Anthropic
                      </div>
                      {AVAILABLE_MODELS.filter(
                        (m) => m.provider === 'anthropic',
                      ).map((model) => (
                        <button
                          key={model.id}
                          type="button"
                          onClick={() => handleModelChange(model.id)}
                          className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 flex items-center justify-between ${
                            selectedModelId === model.id
                              ? 'text-blue-600 bg-blue-50'
                              : 'text-gray-700'
                          }`}
                        >
                          <span>{model.fullName}</span>
                          {selectedModelId === model.id && <Check size={12} />}
                        </button>
                      ))}

                      {/* OpenAI Models */}
                      <div className="px-2 py-1 mt-1 text-[10px] font-medium text-gray-400 uppercase tracking-wide border-t border-gray-100">
                        OpenAI
                      </div>
                      {AVAILABLE_MODELS.filter(
                        (m) => m.provider === 'openai',
                      ).map((model) => (
                        <button
                          key={model.id}
                          type="button"
                          onClick={() => handleModelChange(model.id)}
                          className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 flex items-center justify-between ${
                            selectedModelId === model.id
                              ? 'text-blue-600 bg-blue-50'
                              : 'text-gray-700'
                          }`}
                        >
                          <span>{model.fullName}</span>
                          {selectedModelId === model.id && <Check size={12} />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Right side - Stop/Send button */}
              <div className="flex items-center gap-1">
                {isStreaming ? (
                  <button
                    type="button"
                    onClick={handleStop}
                    className="p-1.5 bg-gray-200 hover:bg-gray-300 text-gray-700 rounded-md transition-colors"
                    title="Stop generating"
                  >
                    <Square size={14} fill="currentColor" />
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!input.trim()}
                    className="p-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white rounded-md transition-colors"
                    title="Send message"
                  >
                    <Send size={14} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AIChat;
