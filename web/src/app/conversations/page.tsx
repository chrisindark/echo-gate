'use client';

import { cn } from '@/lib/utils';
import {
  Box,
  Checkbox,
  Flex,
  IconButton,
  Select,
  Slider,
  Text,
  TextArea,
  TextField,
} from '@radix-ui/themes';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Braces,
  Brain,
  Check,
  ChevronDown,
  Clock,
  Code2,
  Copy,
  PanelLeftOpen,
  Send,
  Settings2,
  Sparkles,
  Square,
  Tag,
} from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';

import { ConversationSidebar } from '@/components/conversation-sidebar';
import { useApiKey } from '@/context/api-key-context';
import { API_ROOT } from '@/lib/api';
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

const PROVIDERS = {
  ollama: [
    'qwen3:8b',
    'llama3.2:3b',
    'deepseek-r1:8b',
    'phi4:14b',
    'mistral:7b',
    'deepseek-coder:6.7b',
  ],
  'google-genai': [
    'gemini-2.5-flash',
    'gemini-2.5-pro',
    'gemini-2.0-flash',
    'gemini-2.0-flash-lite',
  ],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini', 'o3-mini'],
  groq: [
    'llama-3.3-70b-versatile',
    'llama-3.1-8b-instant',
    'mixtral-8x7b-32768',
  ],
  openrouter: [
    'anthropic/claude-3.5-sonnet',
    'openai/gpt-4o',
    'meta-llama/llama-3.3-70b-instruct',
  ],
  claude: [
    'claude-3-7-sonnet-20250219',
    'claude-3-5-sonnet-20241022',
    'claude-3-5-haiku-20241022',
  ],
  mock: ['mock-model'],
};

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

  // Conversation history state
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [isConversationSidebarOpen, setIsConversationSidebarOpen] =
    useState(true);
  const [loadingConversations, setLoadingConversations] = useState(false);

  // Settings state
  const [provider, setProvider] =
    useState<keyof typeof PROVIDERS>('google-genai');
  const [model, setModel] = useState(PROVIDERS['google-genai'][1]);
  const [temperature, setTemperature] = useState<number>(0.7);
  const [maxTokens, setMaxTokens] = useState<number>(8192);
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

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

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
        const conv = await getConversation(conversationId, apiKey);
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
    [apiKey]
  );

  const loadHash = useCallback(async (hash: string) => {
    setLoading(true);
    const startTime = performance.now();
    try {
      const res = await fetch(
        `${API_ROOT}/api/v1/gateway-requests/by-hash/${hash}`
      );
      const endTime = performance.now();
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
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
  }, []);

  // Load conversation list and check initial URL query params
  useEffect(() => {
    const loadInitialState = async () => {
      setLoadingConversations(true);
      try {
        const list = await fetchConversations(apiKey);
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
  }, [apiKey, selectConversation, loadHash]);

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
    await deleteConversation(conversationId, apiKey);
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
      apiKey
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || loading) return;

    const userText = prompt.trim();
    setPrompt('');
    setLoading(true);

    let currentConvId = activeConversationId;
    if (!currentConvId) {
      try {
        const title =
          userText.length > 35 ? userText.slice(0, 35) + '...' : userText;
        const newConv = await createConversation(title, apiKey);
        currentConvId = newConv.id;
        setActiveConversationId(currentConvId);
        setConversations((prev) => [newConv, ...prev]);
        window.history.replaceState({}, '', `/conversations?c=${currentConvId}`);
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
      modelUsed: model,
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
      };
      if (apiKey) headers['X-API-Key'] = apiKey;

      const bodyPayload: Record<string, unknown> = {
        service_name: provider,
        model: model,
        message: userText,
        temperature: temperature,
        max_tokens: maxTokens,
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
          errorMsg = errData.detail || errData.error_message || errorMsg;
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

            try {
              const chunk = JSON.parse(payloadStr);
              if (chunk.error) {
                throw new Error(chunk.error.message || 'Stream error');
              }

              if (chunk.cache_info) {
                latestCacheInfo = chunk.cache_info;
                latestCacheStatus = chunk.cache_info.cache_hit ? 'HIT' : 'MISS';
              }

              const delta = chunk.choices?.[0]?.delta;
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
            } catch {
              // Ignore malformed chunk
            }
          }
        }

        const endTime = performance.now();
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: accumulatedText,
                  reasoningContent: accumulatedReasoning,
                  cacheStatus: latestCacheStatus,
                  cacheInfo: latestCacheInfo,
                  latency: Math.round(endTime - startTime),
                  promptSavings: optSavings,
                  isStreaming: false,
                }
              : m
          )
        );
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
    if (
      !(
        (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))
      )
    ) {
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

      {/* 2. Parameters Settings Drawer */}
      <AnimatePresence>
        {isSettingsOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 320, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            className="flex flex-col border-r border-border bg-card shrink-0 h-full overflow-y-auto absolute left-0 z-40 md:relative md:z-auto"
          >
            <div className="p-6 pt-16 space-y-6">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2 mb-4">
                  <Settings2 className="w-4 h-4" /> Parameters
                </h2>

                <div className="space-y-4">
                  <Flex direction="column" gap="1">
                    <Text as="label" size="2" weight="bold">
                      Provider
                    </Text>
                    <Select.Root
                      value={provider}
                      onValueChange={(val) => {
                        const newProvider = val as keyof typeof PROVIDERS;
                        setProvider(newProvider);
                        setModel(PROVIDERS[newProvider][0]);
                      }}
                    >
                      <Select.Trigger className="w-full" />
                      <Select.Content>
                        {Object.keys(PROVIDERS).map((p) => (
                          <Select.Item key={p} value={p}>
                            {p}
                          </Select.Item>
                        ))}
                      </Select.Content>
                    </Select.Root>
                  </Flex>

                  <Flex direction="column" gap="1">
                    <Text as="label" size="2" weight="bold">
                      Model
                    </Text>
                    <Select.Root value={model} onValueChange={setModel}>
                      <Select.Trigger className="w-full" />
                      <Select.Content>
                        {PROVIDERS[provider].map((m) => (
                          <Select.Item key={m} value={m}>
                            {m}
                          </Select.Item>
                        ))}
                      </Select.Content>
                    </Select.Root>
                  </Flex>

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

                  <Flex direction="column" gap="1">
                    <Text as="label" size="2" weight="bold">
                      Max Tokens
                    </Text>
                    <TextField.Root
                      type="number"
                      value={maxTokens.toString()}
                      onChange={(e) => setMaxTokens(parseInt(e.target.value))}
                    />
                  </Flex>

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

      {/* 3. Main Chat View */}
      <main className="flex-1 flex flex-col h-full relative bg-background overflow-hidden">
        {/* Top Floating Buttons */}
        {!isConversationSidebarOpen && (
          <button
            onClick={() => setIsConversationSidebarOpen(true)}
            title="Open Conversations"
            className="fixed top-4 left-16 z-20 p-2 bg-card border border-border rounded-md shadow-sm hover:bg-muted transition-colors text-foreground"
          >
            <PanelLeftOpen className="w-5 h-5" />
          </button>
        )}

        <button
          onClick={() => setIsSettingsOpen(!isSettingsOpen)}
          title="Toggle Parameters"
          className="fixed top-4 right-4 z-20 p-2 bg-card border border-border rounded-md shadow-sm hover:bg-muted transition-colors text-foreground"
        >
          <Settings2 className="w-5 h-5" />
        </button>

        {/* Message Feed */}
        <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-6 pb-36">
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
                    <div className="bg-primary text-primary-foreground px-5 py-3.5 rounded-2xl rounded-tr-sm max-w-[85%] text-base shadow-sm leading-relaxed whitespace-pre-wrap">
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
                        </pre>
                      </div>
                    ) : (
                      <div className="leading-relaxed whitespace-pre-wrap text-sm relative group/msg">
                        {msg.content}
                        {msg.isStreaming && (
                          <motion.span
                            animate={{ opacity: [1, 0, 1] }}
                            transition={{ repeat: Infinity, duration: 0.8 }}
                            className="inline-block w-1.5 h-3.5 ml-1 bg-primary align-middle rounded-xs"
                          />
                        )}
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

        {/* Floating Stop Generating Button */}
        {loading && stream && (
          <div className="absolute bottom-28 left-0 right-0 flex justify-center z-20 pointer-events-none">
            <button
              onClick={handleStopStreaming}
              className="pointer-events-auto flex items-center gap-2 px-3 py-1.5 rounded-full bg-card border border-border shadow-md hover:bg-muted text-xs font-medium text-foreground transition-all cursor-pointer"
            >
              <Square className="w-3 h-3 fill-destructive text-destructive" />
              <span>Stop Generating</span>
            </button>
          </div>
        )}

        {/* Input Bar Floating at bottom */}
        <div className="absolute bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-background via-background/95 to-transparent pt-8 z-10">
          <div className="max-w-3xl mx-auto">
            <form
              onSubmit={handleSubmit}
              className="relative shadow-lg shadow-black/5 rounded-2xl bg-card border border-border overflow-hidden focus-within:ring-1 focus-within:ring-primary/50 transition-shadow"
            >
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit(e);
                  }
                }}
                placeholder="Message Echo Gate..."
                className="w-full h-14 min-h-[56px] max-h-32 p-4 pr-14 bg-transparent border-none focus:ring-0 resize-none outline-none text-sm text-foreground"
                rows={1}
              />
              <div className="absolute right-2.5 bottom-2.5">
                <IconButton
                  type="submit"
                  disabled={loading || !prompt.trim()}
                  radius="large"
                  size="2"
                >
                  <Send className="w-4 h-4" />
                </IconButton>
              </div>
            </form>

            <div className="flex items-center justify-between mt-2 px-1">
              <span className="text-[11px] text-muted-foreground">
                Echo Gate multi-turn conversation with semantic caching and
                prompt token reduction.
              </span>
              {promptOptimization && (
                <span className="text-[11px] text-amber-500/80 flex items-center gap-1 font-medium">
                  <Sparkles className="w-3 h-3" />
                  Optimization active
                </span>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
