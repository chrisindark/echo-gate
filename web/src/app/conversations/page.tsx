'use client';

import { cn } from '@/lib/utils';
import {
  Box,
  Checkbox,
  Flex,
  Select,
  Slider,
  Text,
  TextArea,
} from '@radix-ui/themes';
import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertCircle,
  AlertTriangle,
  Braces,
  Brain,
  Check,
  ChevronDown,
  Clock,
  Code2,
  Copy,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Settings2,
  Sparkles,
  Square,
  Tag,
  X,
} from 'lucide-react';
import Link from 'next/link';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { ConversationSidebar } from '@/components/conversation-sidebar';
import { MessageContent } from '@/components/chat/message-content';
import { ChatComposer } from '@/components/chat/chat-composer';
import { MaxTokensField } from '@/components/max-tokens-field';
import {
  DEFAULT_MAX_TOKENS,
  clampMaxTokens,
  estimateMessageTokens,
  evaluateContextUsage,
  formatTokenCount,
} from '@/lib/tokens';
import { useStickToBottom } from '@/hooks/use-stick-to-bottom';
import { useApiKey } from '@/context/api-key-context';
import { useAuth } from '@/context/auth-context';
import { API_ROOT, errorMessageFrom } from '@/lib/api';
import {
  AUTO_PROVIDER_ID,
  buildAuthHeaders,
  formatProviderLabel,
  useModelCatalog,
} from '@/lib/providers';
import {
  ConversationItem,
  createConversation,
  deleteConversation,
  fetchConversations,
  getConversation,
  updateConversationTitle,
} from '@/lib/conversations';

const SCHEMA_PRESETS = [
  {
    name: 'Summary & Key Points',
    schemaName: 'summary_response',
    schema: {
      type: 'object',
      properties: {
        summary: {
          type: 'string',
          description: 'Concise summary of the response',
        },
        key_points: {
          type: 'array',
          items: { type: 'string' },
          description: 'Key bullet points or takeaways',
        },
      },
      required: ['summary', 'key_points'],
      additionalProperties: false,
    },
  },
  {
    name: 'Entity Extraction',
    schemaName: 'entity_extraction',
    schema: {
      type: 'object',
      properties: {
        entities: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              category: { type: 'string' },
              confidence: { type: 'number' },
            },
            required: ['name', 'category'],
            additionalProperties: false,
          },
        },
      },
      required: ['entities'],
      additionalProperties: false,
    },
  },
  {
    name: 'Sentiment Analysis',
    schemaName: 'sentiment_analysis',
    schema: {
      type: 'object',
      properties: {
        sentiment: {
          type: 'string',
          enum: ['positive', 'neutral', 'negative'],
        },
        confidence: { type: 'number' },
        reasoning: { type: 'string' },
      },
      required: ['sentiment', 'confidence', 'reasoning'],
      additionalProperties: false,
    },
  },
];

interface CacheInfoType {
  cache_hit?: boolean;
  source?: string;
  score?: number;
  intent?: string;
  entity_tags?: string[];
  exact_hash?: string;
}

interface ChatTurnMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  reasoningContent?: string;
  isThinkingOpen?: boolean;
  cacheStatus?: string | null;
  cacheInfo?: CacheInfoType | null;
  latency?: number | null;
  promptSavings?: string | null;
  modelUsed?: string;
  isStreaming?: boolean;
  createdAt?: string;
}

export default function ChatPage() {
  const [prompt, setPrompt] = useState('');
  const [messages, setMessages] = useState<ChatTurnMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const { apiKey, isSettingsOpen, setIsSettingsOpen } = useApiKey();
  const { user, accessToken } = useAuth();
  const {
    providers,
    modelsByProvider,
    modelMetadata,
    isLoading: isLoadingModels,
    error: modelsError,
    reload: reloadModels,
  } = useModelCatalog();

  // Conversation history state
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [isConversationSidebarOpen, setIsConversationSidebarOpen] =
    useState(true);
  const [loadingConversations, setLoadingConversations] = useState(false);

  // Settings state (driven dynamically by GET /api/v1/models)
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [selectedModel, setSelectedModel] = useState<string>('');

  const activeProvider = useMemo(() => {
    if (selectedProvider && providers.includes(selectedProvider)) {
      return selectedProvider;
    }
    return providers[0] ?? '';
  }, [providers, selectedProvider]);

  const availableModels = useMemo(
    () => (activeProvider ? (modelsByProvider[activeProvider] ?? []) : []),
    [modelsByProvider, activeProvider]
  );

  const activeModel = useMemo(() => {
    if (selectedModel && availableModels.includes(selectedModel)) {
      return selectedModel;
    }
    return availableModels[0] ?? '';
  }, [availableModels, selectedModel]);
  const [temperature, setTemperature] = useState<number>(0.7);
  const [maxTokens, setMaxTokens] = useState<number>(DEFAULT_MAX_TOKENS);
  const [dismissedWarningKey, setDismissedWarningKey] = useState<string | null>(
    null
  );
  const [stream, setStream] = useState<boolean>(true);
  const [thinking, setThinking] = useState<boolean>(false);
  const [thinkingBudget, setThinkingBudget] = useState<number>(2048);
  const [promptOptimization, setPromptOptimization] = useState<boolean>(true);

  // Structured Output state
  const [responseFormatType, setResponseFormatType] = useState<
    'text' | 'json_object' | 'json_schema'
  >('text');
  const [schemaName, setSchemaName] = useState('summary_response');
  const [strictSchema, setStrictSchema] = useState(true);
  const [schemaContent, setSchemaContent] = useState(() =>
    JSON.stringify(SCHEMA_PRESETS[0].schema, null, 2)
  );
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [rawViewMap, setRawViewMap] = useState<Record<string, boolean>>({});
  const [elapsed, setElapsed] = useState(0);
  const [lastLatency, setLastLatency] = useState<number | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const {
    containerRef: feedRef,
    isNearBottom,
    jumpToBottom,
  } = useStickToBottom([messages, loading]);

  const scrollToBottom = useCallback(() => {
    jumpToBottom('smooth');
  }, [jumpToBottom]);

  const activeModelContextWindow = activeModel
    ? modelMetadata[activeModel]?.context_window
    : undefined;

  const estimatedConversationTokens = useMemo(
    () => estimateMessageTokens(messages, prompt),
    [messages, prompt]
  );

  const contextUsage = useMemo(
    () =>
      evaluateContextUsage(
        estimatedConversationTokens,
        activeModelContextWindow
      ),
    [estimatedConversationTokens, activeModelContextWindow]
  );

  const warningScopeKey = `${activeConversationId ?? 'new'}:${activeModel}`;
  const showContextWarning = Boolean(
    contextUsage &&
    (contextUsage.level === 'critical' ||
      (contextUsage.level === 'warning' &&
        dismissedWarningKey !== warningScopeKey))
  );

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    if (!loading) return;
    const start = Date.now();
    const id = window.setInterval(() => setElapsed(Date.now() - start), 100);
    return () => window.clearInterval(id);
  }, [loading]);

  const handleSchemaChange = (value: string) => {
    setSchemaContent(value);
    try {
      JSON.parse(value);
      setSchemaError(null);
    } catch (err: unknown) {
      setSchemaError(err instanceof Error ? err.message : 'Invalid JSON');
    }
  };

  const formatSchema = () => {
    try {
      const parsed = JSON.parse(schemaContent);
      setSchemaContent(JSON.stringify(parsed, null, 2));
      setSchemaError(null);
    } catch (err: unknown) {
      setSchemaError(err instanceof Error ? err.message : 'Invalid JSON');
    }
  };

  const selectConversation = useCallback(
    async (conversationId: string) => {
      try {
        setLoading(true);
        const conv = await getConversation(conversationId, apiKey, accessToken);
        setActiveConversationId(conv.id);

        const mapped: ChatTurnMessage[] = conv.messages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          createdAt: m.created_at,
        }));
        setMessages(mapped);

        window.history.replaceState({}, '', `/conversations?c=${conv.id}`);
      } catch (err) {
        console.error('Failed to load conversation:', err);
      } finally {
        setLoading(false);
      }
    },
    [apiKey, accessToken]
  );

  const loadHash = useCallback(
    async (hash: string) => {
      setLoading(true);
      const startTime = performance.now();
      try {
        const res = await fetch(
          `${API_ROOT}/api/v1/gateway-requests/by-hash/${hash}`,
          {
            headers: buildAuthHeaders(apiKey, accessToken),
          }
        );
        const endTime = performance.now();
        const data = await res.json();
        if (!res.ok) {
          throw new Error(errorMessageFrom(data, `HTTP ${res.status}`));
        }

        const log = data.response || data.data;
        if (log) {
          let originalPrompt = log.query_text || '';
          const parts = originalPrompt.split('|user: ');
          if (parts.length > 1) {
            originalPrompt = parts.slice(1).join('|user: ');
          }

          const userMsg: ChatTurnMessage = {
            id: 'hash-user-' + hash,
            role: 'user',
            content: originalPrompt,
          };
          const assistantMsg: ChatTurnMessage = {
            id: 'hash-asst-' + hash,
            role: 'assistant',
            content: log.response_text || '',
            cacheStatus: 'DB RECOVERED',
            cacheInfo: {
              source: 'database',
              intent: log.intent,
              score: log.rerank_score,
              exact_hash: log.exact_hash,
            },
            latency: Math.round(endTime - startTime),
          };

          setMessages([userMsg, assistantMsg]);
          setActiveConversationId(null);
        }
      } catch (err: unknown) {
        console.error('Failed to load from DB:', err);
      } finally {
        setLoading(false);
      }
    },
    [apiKey, accessToken]
  );

  // Load conversation list and check initial URL query params
  useEffect(() => {
    const loadInitialState = async () => {
      setLoadingConversations(true);
      try {
        const list = await fetchConversations(apiKey, accessToken);
        setConversations(list);

        const params = new URLSearchParams(window.location.search);
        const convParam = params.get('c');
        const hashParam = params.get('hash');

        if (convParam) {
          await selectConversation(convParam);
        } else if (hashParam) {
          await loadHash(hashParam);
        }
      } catch (err) {
        console.error('Failed to load initial conversations:', err);
      } finally {
        setLoadingConversations(false);
      }
    };

    loadInitialState();
  }, [apiKey, accessToken, selectConversation, loadHash]);

  const handleNewConversation = () => {
    if (loading && abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setActiveConversationId(null);
    setMessages([]);
    setPrompt('');
    window.history.replaceState({}, '', '/conversations');
  };

  const handleDeleteConversation = async (conversationId: string) => {
    await deleteConversation(conversationId, apiKey, accessToken);
    setConversations((prev) => prev.filter((c) => c.id !== conversationId));
    if (activeConversationId === conversationId) {
      handleNewConversation();
    }
  };

  const handleRenameConversation = async (
    conversationId: string,
    newTitle: string
  ) => {
    const updated = await updateConversationTitle(
      conversationId,
      newTitle,
      apiKey,
      accessToken
    );
    setConversations((prev) =>
      prev.map((c) => (c.id === conversationId ? updated : c))
    );
  };

  const handleStopStreaming = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setLoading(false);
      setMessages((prev) =>
        prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m))
      );
    }
  };

  const handleSubmit = async () => {
    if (!prompt.trim() || loading || !activeModel) return;

    const userText = prompt.trim();
    setPrompt('');
    setLoading(true);

    let currentConvId = activeConversationId;
    if (!currentConvId) {
      try {
        const title =
          userText.length > 35 ? userText.slice(0, 35) + '...' : userText;
        const newConv = await createConversation(title, apiKey, accessToken);
        currentConvId = newConv.id;
        setActiveConversationId(currentConvId);
        setConversations((prev) => [newConv, ...prev]);
        window.history.replaceState(
          {},
          '',
          `/conversations?c=${currentConvId}`
        );
      } catch (err) {
        console.error('Failed to auto-create conversation:', err);
      }
    }

    const userMsgId = 'user-' + Date.now();
    const userMsg: ChatTurnMessage = {
      id: userMsgId,
      role: 'user',
      content: userText,
      createdAt: new Date().toISOString(),
    };

    const assistantMsgId = 'assistant-' + Date.now();
    const assistantMsg: ChatTurnMessage = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      reasoningContent: '',
      modelUsed: activeModel,
      isStreaming: stream,
      isThinkingOpen: true,
      createdAt: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const startTime = performance.now();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        ...buildAuthHeaders(apiKey, accessToken),
      };

      const bodyPayload: Record<string, unknown> = {
        ...(activeProvider && activeProvider !== AUTO_PROVIDER_ID
          ? { service_name: activeProvider }
          : {}),
        model: activeModel,
        message: userText,
        temperature: temperature,
        max_tokens: clampMaxTokens(maxTokens),
        stream: stream,
        thinking: thinking,
        thinking_budget: thinking ? thinkingBudget : undefined,
        prompt_optimization: promptOptimization,
      };

      if (responseFormatType === 'json_object') {
        bodyPayload.response_format = { type: 'json_object' };
      } else if (responseFormatType === 'json_schema') {
        let parsedSchema: Record<string, unknown>;
        try {
          parsedSchema = JSON.parse(schemaContent);
        } catch {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId
                ? {
                    ...m,
                    content:
                      'Error: Invalid JSON Schema syntax. Please correct schema in Settings.',
                    isStreaming: false,
                  }
                : m
            )
          );
          setLoading(false);
          return;
        }
        bodyPayload.response_format = {
          type: 'json_schema',
          json_schema: {
            name: schemaName.trim() || 'structured_response',
            strict: strictSchema,
            schema: parsedSchema,
          },
        };
      }

      const endpoint = currentConvId
        ? `${API_ROOT}/api/v1/conversations/${currentConvId}/chat`
        : `${API_ROOT}/api/v1/chat/completions`;

      const reqPayload = currentConvId
        ? bodyPayload
        : {
            ...bodyPayload,
            messages: [{ role: 'user', content: userText }],
          };

      const res = await fetch(endpoint, {
        method: 'POST',
        headers,
        body: JSON.stringify(reqPayload),
        signal: abortController.signal,
      });

      const cacheHeader = res.headers.get('X-Cache');
      const optSavings = res.headers.get('X-EchoGate-Prompt-Savings');

      if (!res.ok) {
        let errorMsg = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          errorMsg = errorMessageFrom(errData, errorMsg);
        } catch {
          // ignore
        }
        throw new Error(errorMsg);
      }

      const contentType = res.headers.get('content-type') || '';
      if (stream && contentType.includes('text/event-stream') && res.body) {
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let accumulatedText = '';
        let accumulatedReasoning = '';
        let latestCacheInfo: CacheInfoType | null = null;
        let latestCacheStatus = cacheHeader;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith('data:')) continue;

            const payloadStr = trimmed.slice(5).trim();
            if (payloadStr === '[DONE]') {
              break;
            }

            let chunk: Record<string, unknown>;
            try {
              chunk = JSON.parse(payloadStr) as Record<string, unknown>;
            } catch {
              // Ignore malformed chunk
              continue;
            }

            if (chunk.error && typeof chunk.error === 'object') {
              const streamErr = chunk.error as Record<string, unknown>;
              throw new Error(
                typeof streamErr.message === 'string' &&
                  streamErr.message.trim()
                  ? streamErr.message
                  : 'Stream error'
              );
            }

            {
              const typedChunk = chunk as {
                cache_info?: CacheInfoType & { cache_hit?: boolean };
                choices?: Array<{
                  delta?: {
                    reasoning_content?: string;
                    content?: string;
                  };
                }>;
              };

              if (typedChunk.cache_info) {
                latestCacheInfo = typedChunk.cache_info;
                latestCacheStatus = typedChunk.cache_info.cache_hit
                  ? 'HIT'
                  : 'MISS';
              }

              const delta = typedChunk.choices?.[0]?.delta;
              if (delta) {
                if (delta.reasoning_content) {
                  accumulatedReasoning += delta.reasoning_content;
                }
                if (delta.content) {
                  accumulatedText += delta.content;

                  if (accumulatedText.startsWith('<think>')) {
                    const closeIndex = accumulatedText.indexOf('</think>');
                    if (closeIndex !== -1) {
                      accumulatedReasoning = accumulatedText
                        .slice(7, closeIndex)
                        .trim();
                      accumulatedText = accumulatedText
                        .slice(closeIndex + 8)
                        .trim();
                    }
                  }
                }

                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsgId
                      ? {
                          ...m,
                          content: accumulatedText,
                          reasoningContent: accumulatedReasoning,
                          cacheStatus: latestCacheStatus,
                          cacheInfo: latestCacheInfo,
                        }
                      : m
                  )
                );
              }
            }
          }
        }

        const endTime = performance.now();
        const finalLatency = Math.round(endTime - startTime);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: accumulatedText,
                  reasoningContent: accumulatedReasoning,
                  cacheStatus: latestCacheStatus,
                  cacheInfo: latestCacheInfo,
                  latency: finalLatency,
                  promptSavings: optSavings,
                  isStreaming: false,
                }
              : m
          )
        );
        setLastLatency(finalLatency);
      } else {
        // Non-streaming response
        const data = await res.json();
        const endTime = performance.now();
        const latencyMs = Math.round(endTime - startTime);

        const respObj = data.response ?? data;
        let assistantContent = '';
        let assistantReasoning = '';
        let respCacheInfo: CacheInfoType | null = null;
        let respCacheStatus = cacheHeader;

        if (respObj.choices && respObj.choices.length > 0) {
          const msg = respObj.choices[0].message;
          const rawText = msg.content || '';
          assistantReasoning = msg.reasoning_content || '';

          if (rawText.startsWith('<think>')) {
            const closeIndex = rawText.indexOf('</think>');
            if (closeIndex !== -1) {
              assistantReasoning = rawText.slice(7, closeIndex).trim();
              assistantContent = rawText.slice(closeIndex + 8).trim();
            } else {
              assistantContent = rawText;
            }
          } else {
            assistantContent = rawText;
          }
        }

        if (respObj.cache_info) {
          respCacheInfo = respObj.cache_info;
          respCacheStatus = respObj.cache_info.cache_hit ? 'HIT' : 'MISS';
        }

        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: assistantContent,
                  reasoningContent: assistantReasoning,
                  cacheStatus: respCacheStatus,
                  cacheInfo: respCacheInfo,
                  latency: latencyMs,
                  promptSavings: optSavings,
                  isStreaming: false,
                }
              : m
          )
        );
        setLastLatency(latencyMs);
      }
    } catch (err: unknown) {
      const errorMsg =
        err instanceof Error
          ? err.name === 'AbortError'
            ? 'Generation stopped by user.'
            : err.message
          : String(err);

      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                content: m.content ? m.content : `Error: ${errorMsg}`,
                isStreaming: false,
              }
            : m
        )
      );
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  };

  const isJsonText = (text: string) => {
    if (!text) return false;
    const trimmed = text.trim();
    if (!(
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    )) {
      return false;
    }
    try {
      JSON.parse(trimmed);
      return true;
    } catch {
      return false;
    }
  };

  const formatJson = (text: string) => {
    try {
      return JSON.stringify(JSON.parse(text.trim()), null, 2);
    } catch {
      return text;
    }
  };

  return (
    <div className="flex-1 relative flex h-full overflow-hidden bg-background">
      {/* 1. Left Conversation History Sidebar */}
      <ConversationSidebar
        isOpen={isConversationSidebarOpen}
        onToggle={() =>
          setIsConversationSidebarOpen(!isConversationSidebarOpen)
        }
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={selectConversation}
        onNewConversation={handleNewConversation}
        onDeleteConversation={handleDeleteConversation}
        onRenameConversation={handleRenameConversation}
        loading={loadingConversations}
      />

      {/* Main Chat View */}
      <main className="flex-1 flex flex-col h-full relative bg-background overflow-hidden min-w-0">
        {/* Conversations Top Bar */}
        <header className="h-14 border-b border-border bg-card/40 backdrop-blur-xs px-4 md:px-6 flex items-center justify-between shrink-0 select-none">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              onClick={() =>
                setIsConversationSidebarOpen(!isConversationSidebarOpen)
              }
              className={cn(
                'p-1.5 rounded-lg border transition-colors flex items-center gap-1.5 text-xs font-medium cursor-pointer',
                isConversationSidebarOpen
                  ? 'bg-muted border-border/80 text-foreground'
                  : 'bg-card border-border/80 hover:bg-muted text-foreground'
              )}
              title={
                isConversationSidebarOpen
                  ? 'Collapse Conversations'
                  : 'Show Conversations'
              }
            >
              {isConversationSidebarOpen ? (
                <PanelLeftClose className="w-4 h-4 text-primary" />
              ) : (
                <PanelLeftOpen className="w-4 h-4" />
              )}
              <span className="hidden sm:inline">Chats</span>
            </button>
            <div className="flex items-center gap-2 min-w-0">
              <MessagesSquare className="w-4 h-4 text-primary shrink-0" />
              <span className="font-semibold text-sm text-foreground truncate max-w-[180px] sm:max-w-xs md:max-w-md">
                {conversations.find((c) => c.id === activeConversationId)
                  ?.title || 'New Conversation'}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="hidden sm:flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="font-mono px-2 py-0.5 rounded bg-muted/60 border border-border/60 text-[11px] text-foreground font-medium">
                {activeProvider && activeModel
                  ? `${formatProviderLabel(activeProvider)} / ${activeModel}`
                  : isLoadingModels
                    ? 'Loading models...'
                    : 'No models available'}
              </span>
              {contextUsage && (
                <span
                  title={`Estimated conversation context: ~${contextUsage.estimatedTokens.toLocaleString()} / ${contextUsage.contextWindow.toLocaleString()} tokens (${contextUsage.percent}%)`}
                  className={cn(
                    'font-mono px-2 py-0.5 rounded border text-[11px] font-medium flex items-center gap-1',
                    contextUsage.level === 'critical'
                      ? 'bg-destructive/10 text-destructive border-destructive/30'
                      : contextUsage.level === 'warning'
                        ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
                        : 'bg-muted/60 text-muted-foreground border-border/60'
                  )}
                >
                  {contextUsage.level !== 'normal' && (
                    <AlertTriangle className="w-3 h-3 shrink-0" />
                  )}
                  ~{formatTokenCount(contextUsage.estimatedTokens)} /{' '}
                  {formatTokenCount(contextUsage.contextWindow)} ctx (
                  {contextUsage.percent}%)
                </span>
              )}
              {promptOptimization && (
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px] font-medium flex items-center gap-1">
                  <Sparkles className="w-3 h-3" /> Opt
                </span>
              )}
            </div>

            {(loading || lastLatency !== null) && (
              <span
                className={cn(
                  'hidden sm:inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium',
                  loading
                    ? 'border-primary/30 bg-primary/10 text-primary'
                    : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
                )}
                aria-live="polite"
                aria-atomic="true"
              >
                {loading ? (
                  <>
                    <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
                    Generating… {(elapsed / 1000).toFixed(1)}s
                  </>
                ) : (
                  <>
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    Complete · {lastLatency}ms
                  </>
                )}
              </span>
            )}

            <button
              type="button"
              onClick={() => setIsSettingsOpen(!isSettingsOpen)}
              className={cn(
                'px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-all flex items-center gap-1.5 cursor-pointer',
                isSettingsOpen
                  ? 'bg-primary text-primary-foreground border-primary shadow-xs'
                  : 'bg-card border-border/80 hover:bg-muted text-foreground'
              )}
              title="Toggle Parameters"
            >
              <Settings2 className="w-4 h-4" />
              <span className="hidden sm:inline">Parameters</span>
            </button>
          </div>
        </header>

        {loading && (
          <div className="h-0.5 w-full overflow-hidden bg-primary/15">
            <motion.div
              className="h-full w-2/5 bg-primary"
              animate={{ x: ['-100%', '250%'] }}
              transition={{
                repeat: Infinity,
                duration: 1.3,
                ease: 'easeInOut',
              }}
            />
          </div>
        )}

        {/* Message Feed */}
        <div
          ref={feedRef}
          className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 min-w-0"
          aria-busy={loading}
        >
          {messages.length === 0 && !loading && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="h-full min-h-[50vh] flex flex-col items-center justify-center text-center max-w-md mx-auto space-y-4"
            >
              <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center text-primary mb-2 shadow-inner">
                <Sparkles className="w-8 h-8" />
              </div>
              <h2 className="text-2xl font-bold">How can I help you?</h2>
              <p className="text-muted-foreground text-sm leading-relaxed">
                Send a message to start a multi-turn conversation. Echo Gate
                dynamically optimizes prompt tokens and leverages semantic
                caching.
              </p>
            </motion.div>
          )}

          <div className="max-w-3xl mx-auto space-y-6">
            {messages.map((msg) => {
              if (msg.role === 'user') {
                return (
                  <motion.div
                    key={msg.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex justify-end"
                  >
                    <div className="bg-primary text-primary-foreground px-5 py-3.5 rounded-2xl rounded-tr-sm max-w-[85%] min-w-0 text-base shadow-sm leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                      {msg.content}
                    </div>
                  </motion.div>
                );
              }

              // Assistant message
              const isJson = isJsonText(msg.content);
              const formatted = isJson ? formatJson(msg.content) : null;
              const isRaw = rawViewMap[msg.id] ?? false;

              return (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-start w-full"
                >
                  <div className="bg-card border border-border px-5 py-4 rounded-2xl rounded-tl-sm max-w-[95%] shadow-sm w-full space-y-3">
                    {/* Metadata Header Badges */}
                    {(msg.cacheStatus ||
                      msg.latency !== null ||
                      msg.promptSavings ||
                      msg.modelUsed) && (
                      <div className="flex flex-wrap items-center gap-2 text-xs font-medium pb-2 border-b border-border/50">
                        {msg.cacheStatus && (
                          <div
                            className={cn(
                              'px-2 py-0.5 rounded-md flex items-center gap-1.5 border',
                              msg.cacheStatus === 'HIT' ||
                                msg.cacheStatus === 'DB RECOVERED'
                                ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                                : 'bg-amber-500/10 text-amber-500 border-amber-500/20'
                            )}
                          >
                            <span
                              className={cn(
                                'w-1.5 h-1.5 rounded-full',
                                msg.cacheStatus === 'HIT' ||
                                  msg.cacheStatus === 'DB RECOVERED'
                                  ? 'bg-emerald-500'
                                  : 'bg-amber-500'
                              )}
                            />
                            {msg.cacheStatus === 'HIT'
                              ? 'CACHE HIT'
                              : msg.cacheStatus === 'DB RECOVERED'
                                ? 'DB RECOVERED'
                                : 'CACHE MISS'}
                          </div>
                        )}

                        {msg.latency !== undefined && msg.latency !== null && (
                          <div className="px-2 py-0.5 rounded-md bg-blue-500/10 text-blue-500 border border-blue-500/20 flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {msg.latency}ms
                          </div>
                        )}

                        {msg.promptSavings && (
                          <div
                            title={msg.promptSavings}
                            className="px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-400 border border-amber-500/20 flex items-center gap-1"
                          >
                            <Sparkles className="w-3 h-3" />
                            {msg.promptSavings}
                          </div>
                        )}

                        {msg.modelUsed && (
                          <div className="px-2 py-0.5 rounded-md bg-purple-500/10 text-purple-400 border border-purple-500/20">
                            {msg.modelUsed}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Semantic Match Metadata Box */}
                    {msg.cacheInfo &&
                      (msg.cacheStatus === 'HIT' ||
                        msg.cacheStatus === 'DB RECOVERED') && (
                        <div className="bg-muted/50 rounded-lg p-2.5 text-xs border border-border/50">
                          <div className="flex items-center gap-1 text-muted-foreground font-semibold mb-1 uppercase tracking-wider text-[10px]">
                            <Tag className="w-3.5 h-3.5" /> Semantic Cache Match
                          </div>
                          <div className="grid grid-cols-2 gap-2 text-muted-foreground text-[11px]">
                            <div>
                              <span className="opacity-70">Intent:</span>{' '}
                              <span className="text-foreground">
                                {msg.cacheInfo.intent || 'N/A'}
                              </span>
                            </div>
                            <div>
                              <span className="opacity-70">Score:</span>{' '}
                              <span className="text-foreground">
                                {msg.cacheInfo.score
                                  ? msg.cacheInfo.score.toFixed(4)
                                  : 'Exact'}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}

                    {/* Thinking / Reasoning Accordion */}
                    {msg.reasoningContent && (
                      <div className="rounded-xl border border-violet-500/20 bg-violet-950/20 overflow-hidden">
                        <button
                          type="button"
                          onClick={() => {
                            setMessages((prev) =>
                              prev.map((m) =>
                                m.id === msg.id
                                  ? {
                                      ...m,
                                      isThinkingOpen: !(
                                        m.isThinkingOpen ?? true
                                      ),
                                    }
                                  : m
                              )
                            );
                          }}
                          className="w-full px-3 py-2 flex items-center justify-between text-xs text-violet-300 hover:bg-violet-500/10 transition-colors cursor-pointer select-none"
                        >
                          <div className="flex items-center gap-2">
                            <Brain className="w-4 h-4 text-violet-400" />
                            <span className="font-medium">
                              Thinking Process
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5 text-violet-400/80 text-[11px]">
                            <span>
                              {
                                msg.reasoningContent
                                  .trim()
                                  .split(/\s+/)
                                  .filter(Boolean).length
                              }{' '}
                              words
                            </span>
                            <ChevronDown
                              className={cn(
                                'w-3.5 h-3.5 transition-transform',
                                (msg.isThinkingOpen ?? true) && 'rotate-180'
                              )}
                            />
                          </div>
                        </button>

                        <AnimatePresence initial={false}>
                          {(msg.isThinkingOpen ?? true) && (
                            <motion.div
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: 'auto', opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                              transition={{ duration: 0.2 }}
                            >
                              <div className="px-3 py-2.5 border-t border-violet-500/15 text-xs text-violet-200/90 whitespace-pre-wrap font-mono leading-relaxed bg-violet-950/10 max-h-56 overflow-y-auto">
                                {msg.reasoningContent}
                                {msg.isStreaming && (
                                  <motion.span
                                    animate={{ opacity: [1, 0, 1] }}
                                    transition={{
                                      repeat: Infinity,
                                      duration: 0.8,
                                    }}
                                    className="inline-block w-1.5 h-3.5 ml-1 bg-violet-400 align-middle rounded-xs"
                                  />
                                )}
                              </div>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </div>
                    )}

                    {/* Assistant Content Output */}
                    {msg.isStreaming && !msg.content ? (
                      <div className="flex gap-1.5 items-center text-muted-foreground py-2">
                        <motion.div
                          animate={{ opacity: [0.4, 1, 0.4] }}
                          transition={{ repeat: Infinity, duration: 1.2 }}
                          className="w-2 h-2 rounded-full bg-primary/60"
                        />
                        <motion.div
                          animate={{ opacity: [0.4, 1, 0.4] }}
                          transition={{
                            repeat: Infinity,
                            duration: 1.2,
                            delay: 0.2,
                          }}
                          className="w-2 h-2 rounded-full bg-primary/60"
                        />
                        <motion.div
                          animate={{ opacity: [0.4, 1, 0.4] }}
                          transition={{
                            repeat: Infinity,
                            duration: 1.2,
                            delay: 0.4,
                          }}
                          className="w-2 h-2 rounded-full bg-primary/60"
                        />
                      </div>
                    ) : isJson && !isRaw && formatted ? (
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs pb-1 border-b border-border/50">
                          <span className="text-muted-foreground flex items-center gap-1 font-medium">
                            <Braces className="w-3.5 h-3.5 text-primary" />{' '}
                            Structured JSON
                          </span>
                          <div className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() =>
                                setRawViewMap((prev) => ({
                                  ...prev,
                                  [msg.id]: !isRaw,
                                }))
                              }
                              className="px-2 py-0.5 rounded bg-muted/70 hover:bg-muted text-[11px] text-foreground flex items-center gap-1"
                            >
                              <Code2 className="w-3 h-3" />
                              Raw
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                navigator.clipboard.writeText(formatted);
                                setCopiedId(msg.id);
                                setTimeout(() => setCopiedId(null), 2000);
                              }}
                              className="px-2 py-0.5 rounded bg-muted/70 hover:bg-muted text-[11px] text-foreground flex items-center gap-1"
                            >
                              {copiedId === msg.id ? (
                                <Check className="w-3 h-3 text-emerald-400" />
                              ) : (
                                <Copy className="w-3 h-3" />
                              )}
                              Copy
                            </button>
                          </div>
                        </div>
                        <pre className="p-3 rounded-xl bg-muted/40 border border-border/60 font-mono text-xs overflow-x-auto text-emerald-300 whitespace-pre">
                          <code>{formatted}</code>
                          {msg.isStreaming && (
                            <motion.span
                              animate={{ opacity: [1, 0, 1] }}
                              transition={{ repeat: Infinity, duration: 0.8 }}
                              className="inline-block w-1.5 h-3.5 ml-1 bg-emerald-300 align-middle rounded-xs"
                            />
                          )}
                        </pre>
                      </div>
                    ) : (
                      <div className="relative group/msg min-w-0">
                        <MessageContent
                          content={msg.content}
                          isStreaming={msg.isStreaming}
                        />
                        {!msg.isStreaming && msg.content && (
                          <button
                            type="button"
                            onClick={() => {
                              navigator.clipboard.writeText(msg.content);
                              setCopiedId(msg.id);
                              setTimeout(() => setCopiedId(null), 2000);
                            }}
                            className="absolute -top-1 right-0 opacity-0 group-hover/msg:opacity-100 transition-opacity p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground"
                            title="Copy message"
                          >
                            {copiedId === msg.id ? (
                              <Check className="w-3.5 h-3.5 text-emerald-400" />
                            ) : (
                              <Copy className="w-3.5 h-3.5" />
                            )}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </motion.div>
              );
            })}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Stop Generating Button */}
        {loading && stream && (
          <div className="px-4 pt-2 flex justify-center">
            <button
              onClick={handleStopStreaming}
              className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-card border border-border shadow-md hover:bg-muted text-xs font-medium text-foreground transition-all cursor-pointer"
            >
              <Square className="w-3 h-3 fill-destructive text-destructive" />
              <span>Stop Generating</span>
            </button>
          </div>
        )}

        {/* Jump to latest */}
        {!isNearBottom && (
          <div className="px-4 pt-2 flex justify-center">
            <button
              type="button"
              onClick={() => jumpToBottom('smooth')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-primary text-primary-foreground shadow-md text-xs font-medium transition-all cursor-pointer"
            >
              ↓ Jump to latest
            </button>
          </div>
        )}

        {/* Input Bar */}
        <div className="p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] bg-gradient-to-t from-background via-background/95 to-transparent pt-4">
          <div className="max-w-3xl mx-auto">
            {showContextWarning && contextUsage && (
              <div
                className={cn(
                  'mb-2.5 px-3.5 py-2.5 rounded-xl border text-xs flex items-start justify-between gap-3 shadow-sm',
                  contextUsage.level === 'critical'
                    ? 'bg-destructive/10 border-destructive/30 text-destructive'
                    : 'bg-amber-500/10 border-amber-500/30 text-amber-200'
                )}
              >
                <div className="flex items-start gap-2 leading-relaxed">
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                  <div>
                    <span className="font-semibold">
                      {contextUsage.level === 'critical'
                        ? 'Approaching model context limit'
                        : 'Large conversation context'}
                    </span>{' '}
                    (~{formatTokenCount(contextUsage.estimatedTokens)} /{' '}
                    {formatTokenCount(contextUsage.contextWindow)} tokens,{' '}
                    {contextUsage.percent}% of {activeModel}&apos;s context
                    window).{' '}
                    {contextUsage.level === 'critical'
                      ? 'Next messages are much more likely to hallucinate, lose instructions, or be truncated.'
                      : 'As threads grow this large, LLMs are more prone to hallucinations or missing earlier details.'}
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    type="button"
                    onClick={handleNewConversation}
                    className={cn(
                      'px-2.5 py-1 rounded-md border text-[11px] font-medium transition-colors cursor-pointer whitespace-nowrap',
                      contextUsage.level === 'critical'
                        ? 'border-destructive/40 hover:bg-destructive/15 text-destructive'
                        : 'border-amber-500/40 hover:bg-amber-500/15 text-amber-200'
                    )}
                  >
                    New Chat
                  </button>
                  {contextUsage.level === 'warning' && (
                    <button
                      type="button"
                      onClick={() => setDismissedWarningKey(warningScopeKey)}
                      className="p-1 rounded-md hover:bg-amber-500/15 text-amber-200/80 hover:text-amber-100 cursor-pointer"
                      title="Dismiss warning"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            )}

            <ChatComposer
              value={prompt}
              onChange={setPrompt}
              onSubmit={handleSubmit}
              onStop={handleStopStreaming}
              loading={loading}
              disabled={!activeModel}
              placeholder="Message Echo Gate..."
              tokenCount={
                prompt.trim() ? Math.ceil(prompt.trim().length / 4) : undefined
              }
              autoFocus={!loading}
              hint={
                loading
                  ? 'Stop generating to send a new message'
                  : 'Enter to send · Shift+Enter for newline'
              }
            />

            <div className="flex items-center justify-between mt-2 px-1 gap-2">
              <span className="text-[11px] text-muted-foreground">
                Echo Gate multi-turn conversation with semantic caching and
                prompt token reduction.
              </span>
              <div className="flex items-center gap-3 shrink-0">
                {contextUsage && (
                  <span
                    className={cn(
                      'sm:hidden font-mono text-[11px]',
                      contextUsage.level === 'critical'
                        ? 'text-destructive font-medium'
                        : contextUsage.level === 'warning'
                          ? 'text-amber-400 font-medium'
                          : 'text-muted-foreground'
                    )}
                  >
                    ~{formatTokenCount(contextUsage.estimatedTokens)} /{' '}
                    {formatTokenCount(contextUsage.contextWindow)} (
                    {contextUsage.percent}%)
                  </span>
                )}
                {promptOptimization && (
                  <span className="text-[11px] text-amber-500/80 flex items-center gap-1 font-medium">
                    <Sparkles className="w-3 h-3" />
                    Optimization active
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* Parameters Inspector (Right Panel) */}
      <AnimatePresence>
        {isSettingsOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 320, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="flex flex-col border-l border-border bg-card shrink-0 h-full overflow-hidden absolute right-0 z-40 md:relative md:z-auto shadow-xl md:shadow-none"
          >
            <div className="h-14 border-b border-border px-4 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                <Settings2 className="w-4 h-4 text-primary" />
                <span>Parameters</span>
              </div>
              <button
                onClick={() => setIsSettingsOpen(false)}
                className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                title="Close parameters"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6">
              <div>
                <div className="space-y-4">
                  {modelsError ? (
                    <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs space-y-2">
                      <div className="flex items-start gap-2">
                        <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                        <span>{modelsError}</span>
                      </div>
                      <button
                        type="button"
                        onClick={reloadModels}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-destructive/30 hover:bg-destructive/15 text-[11px] font-medium cursor-pointer"
                      >
                        <RefreshCw className="w-3 h-3" />
                        <span>Retry loading models</span>
                      </button>
                    </div>
                  ) : isLoadingModels && providers.length === 0 ? (
                    <div className="p-3 rounded-lg border border-border/60 bg-muted/30 text-xs text-muted-foreground flex items-center gap-2">
                      <RefreshCw className="w-3.5 h-3.5 animate-spin text-primary" />
                      <span>Loading models from gateway...</span>
                    </div>
                  ) : providers.length === 0 ? (
                    <div className="p-3 rounded-lg border border-amber-500/25 bg-amber-500/10 text-xs text-amber-200">
                      No enabled providers/models returned by the gateway.
                    </div>
                  ) : (
                    <>
                      <Flex direction="column" gap="1">
                        <Text as="label" size="2" weight="bold">
                          Provider
                        </Text>
                        <Select.Root
                          value={activeProvider}
                          onValueChange={(val) => {
                            setSelectedProvider(val);
                            const nextModels = modelsByProvider[val] ?? [];
                            if (nextModels.length > 0) {
                              setSelectedModel(nextModels[0]);
                            }
                          }}
                        >
                          <Select.Trigger className="w-full" />
                          <Select.Content>
                            {providers.map((p) => (
                              <Select.Item key={p} value={p}>
                                {formatProviderLabel(p)}
                              </Select.Item>
                            ))}
                          </Select.Content>
                        </Select.Root>
                      </Flex>

                      <Flex direction="column" gap="1">
                        <Flex justify="between" align="center">
                          <Text as="label" size="2" weight="bold">
                            Model
                          </Text>
                          {activeModel && modelMetadata[activeModel] && (
                            <Text size="1" color="gray" className="font-mono">
                              {Math.round(
                                modelMetadata[activeModel].context_window / 1000
                              )}
                              k ctx
                            </Text>
                          )}
                        </Flex>
                        <Select.Root
                          value={activeModel}
                          onValueChange={setSelectedModel}
                        >
                          <Select.Trigger className="w-full" />
                          <Select.Content>
                            {availableModels.map((m) => (
                              <Select.Item key={m} value={m}>
                                {m}
                              </Select.Item>
                            ))}
                          </Select.Content>
                        </Select.Root>
                      </Flex>
                    </>
                  )}

                  {!apiKey && !user && (
                    <div className="p-2.5 rounded-lg border border-border/70 bg-muted/30 text-[11px] text-muted-foreground leading-relaxed">
                      Anonymous access is limited to free-tier providers (Groq,
                      OpenRouter).{' '}
                      <Link
                        href="/login"
                        className="text-primary font-medium hover:underline"
                      >
                        Sign in
                      </Link>{' '}
                      or{' '}
                      <Link
                        href="/signup"
                        className="text-primary font-medium hover:underline"
                      >
                        sign up
                      </Link>{' '}
                      to unlock OpenAI, Claude, and Gemini.
                    </div>
                  )}

                  <Box>
                    <Flex justify="between" mb="1">
                      <Text as="label" size="2" weight="bold">
                        Temperature
                      </Text>
                      <Text size="1" color="gray">
                        {temperature}
                      </Text>
                    </Flex>
                    <Slider
                      min={0}
                      max={2}
                      step={0.1}
                      value={[temperature]}
                      onValueChange={(val) => setTemperature(val[0])}
                    />
                  </Box>

                  <MaxTokensField
                    value={maxTokens}
                    onChange={setMaxTokens}
                    modelContextWindow={activeModelContextWindow}
                  />

                  <Flex align="center" gap="2" pt="1">
                    <Checkbox
                      checked={stream}
                      onCheckedChange={(val) => setStream(Boolean(val))}
                      id="stream-check"
                    />
                    <Text
                      as="label"
                      size="2"
                      weight="bold"
                      htmlFor="stream-check"
                      className="cursor-pointer select-none"
                    >
                      Stream Responses (SSE)
                    </Text>
                  </Flex>

                  <Flex align="center" gap="2" pt="1">
                    <Checkbox
                      checked={promptOptimization}
                      onCheckedChange={(val) =>
                        setPromptOptimization(Boolean(val))
                      }
                      id="opt-check"
                    />
                    <Text
                      as="label"
                      size="2"
                      weight="bold"
                      htmlFor="opt-check"
                      className="cursor-pointer select-none flex items-center gap-1.5"
                    >
                      <Sparkles className="w-4 h-4 text-amber-400" />
                      Prompt Token Optimization
                    </Text>
                  </Flex>

                  <Flex align="center" gap="2" pt="1">
                    <Checkbox
                      checked={thinking}
                      onCheckedChange={(val) => setThinking(Boolean(val))}
                      id="thinking-check"
                    />
                    <Text
                      as="label"
                      size="2"
                      weight="bold"
                      htmlFor="thinking-check"
                      className="cursor-pointer select-none flex items-center gap-1.5"
                    >
                      <Brain className="w-4 h-4 text-violet-400" />
                      Thinking / Reasoning Mode
                    </Text>
                  </Flex>

                  {thinking && (
                    <div className="pl-6 space-y-2 pt-1 pb-1">
                      <Flex justify="between">
                        <Text size="1" color="gray">
                          Reasoning Budget (Tokens)
                        </Text>
                        <Text size="1" weight="bold">
                          {thinkingBudget}
                        </Text>
                      </Flex>
                      <Slider
                        value={[thinkingBudget]}
                        onValueChange={(val) => setThinkingBudget(val[0])}
                        min={1024}
                        max={16384}
                        step={512}
                      />
                    </div>
                  )}

                  <Flex direction="column" gap="1" pt="2">
                    <Text as="label" size="2" weight="bold">
                      Response Format
                    </Text>
                    <Select.Root
                      value={responseFormatType}
                      onValueChange={(val) =>
                        setResponseFormatType(
                          val as 'text' | 'json_object' | 'json_schema'
                        )
                      }
                    >
                      <Select.Trigger className="w-full" />
                      <Select.Content>
                        <Select.Item value="text">Text (Default)</Select.Item>
                        <Select.Item value="json_object">
                          JSON Object
                        </Select.Item>
                        <Select.Item value="json_schema">
                          Strict JSON Schema
                        </Select.Item>
                      </Select.Content>
                    </Select.Root>
                  </Flex>

                  {responseFormatType === 'json_schema' && (
                    <div className="space-y-3 pt-2">
                      <Flex direction="column" gap="1">
                        <Text as="label" size="2" weight="bold">
                          Schema Preset
                        </Text>
                        <Select.Root
                          value={schemaName}
                          onValueChange={(val) => {
                            setSchemaName(val);
                            const preset = SCHEMA_PRESETS.find(
                              (p) => p.schemaName === val
                            );
                            if (preset) {
                              setSchemaContent(
                                JSON.stringify(preset.schema, null, 2)
                              );
                              setSchemaError(null);
                            }
                          }}
                        >
                          <Select.Trigger className="w-full" />
                          <Select.Content>
                            {SCHEMA_PRESETS.map((preset) => (
                              <Select.Item
                                key={preset.schemaName}
                                value={preset.schemaName}
                              >
                                {preset.name}
                              </Select.Item>
                            ))}
                          </Select.Content>
                        </Select.Root>
                      </Flex>

                      <Flex align="center" gap="2">
                        <Checkbox
                          checked={strictSchema}
                          onCheckedChange={(val) =>
                            setStrictSchema(Boolean(val))
                          }
                          id="strict-schema-check"
                        />
                        <Text
                          as="label"
                          size="2"
                          htmlFor="strict-schema-check"
                          className="cursor-pointer select-none"
                        >
                          Strict Schema Enforcement
                        </Text>
                      </Flex>

                      <Flex direction="column" gap="1">
                        <Flex justify="between" align="center">
                          <Text as="label" size="2" weight="bold">
                            JSON Schema
                          </Text>
                          <button
                            type="button"
                            onClick={formatSchema}
                            className="text-[11px] text-primary hover:underline cursor-pointer"
                          >
                            Prettify
                          </button>
                        </Flex>
                        <TextArea
                          value={schemaContent}
                          onChange={(e) => handleSchemaChange(e.target.value)}
                          rows={8}
                          className="font-mono text-xs"
                          placeholder="Enter valid JSON Schema..."
                        />
                        {schemaError ? (
                          <span className="text-[11px] text-red-400">
                            ⚠ {schemaError}
                          </span>
                        ) : (
                          <span className="text-[11px] text-emerald-400 flex items-center gap-1">
                            ✓ Valid JSON Schema
                          </span>
                        )}
                      </Flex>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}
