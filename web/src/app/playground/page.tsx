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
  AlertCircle,
  Braces,
  Brain,
  Check,
  ChevronDown,
  Clock,
  Code2,
  Copy,
  Database,
  RefreshCw,
  Send,
  Settings2,
  Square,
  Sparkles,
  Tag,
  Terminal,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useStickToBottom } from '@/hooks/use-stick-to-bottom';
import Link from 'next/link';
import { MaxTokensField } from '@/components/max-tokens-field';
import { MessageContent } from '@/components/chat/message-content';
import { DEFAULT_MAX_TOKENS, clampMaxTokens } from '@/lib/tokens';
import { useApiKey } from '@/context/api-key-context';
import { useAuth } from '@/context/auth-context';
import { API_ROOT, errorMessageFrom } from '@/lib/api';
import {
  AUTO_PROVIDER_ID,
  buildAuthHeaders,
  formatProviderLabel,
  useModelCatalog,
} from '@/lib/providers';

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
  source?: string;
  intent?: string;
  score?: number;
  entity_tags?: string[];
  rrf_rank?: number;
  exact_hash?: string;
}

export default function Home() {
  const [prompt, setPrompt] = useState('');
  const [response, setResponse] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [cacheStatus, setCacheStatus] = useState<string | null>(null);
  const [cacheInfo, setCacheInfo] = useState<CacheInfoType | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [lastLatency, setLastLatency] = useState<number | null>(null);
  const [stopped, setStopped] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const { apiKey, setApiKey, isSettingsOpen, setIsSettingsOpen } = useApiKey();
  const { user, accessToken, isLoading: authLoading } = useAuth();
  const {
    providers,
    modelsByProvider,
    modelMetadata,
    isLoading: isLoadingModels,
    error: modelsError,
    reload: reloadModels,
  } = useModelCatalog();

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
  const [stream, setStream] = useState<boolean>(true);
  const [thinking, setThinking] = useState<boolean>(false);
  const [thinkingBudget, setThinkingBudget] = useState<number>(2048);
  const [thinkingContent, setThinkingContent] = useState<string>('');
  const [isThinking, setIsThinking] = useState<boolean>(false);
  const [isThinkingOpen, setIsThinkingOpen] = useState<boolean>(true);
  const [promptOptimization, setPromptOptimization] = useState<boolean>(true);
  const [promptSavings, setPromptSavings] = useState<string | null>(null);

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
  const [copied, setCopied] = useState(false);
  const [rawView, setRawView] = useState(false);

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

  const loadHash = useCallback(
    async (hash: string) => {
      setLoading(true);
      setResponse('');
      setError(null);
      setThinkingContent('');
      setIsThinking(false);
      setCacheStatus(null);
      setCacheInfo(null);
      setLatency(null);
      setPromptSavings(null);

      const startTime = performance.now();
      try {
        const res = await fetch(
          `${API_ROOT}/api/v1/gateway-requests/by-hash/${hash}`,
          {
            headers: buildAuthHeaders(apiKey, accessToken),
          }
        );
        const endTime = performance.now();
        setLatency(Math.round(endTime - startTime));

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
          setPrompt(originalPrompt);
          setResponse(log.response_text || '');
          setCacheStatus('DB RECOVERED');
          setCacheInfo({
            source: 'database',
            intent: log.intent,
            score: log.rerank_score,
            exact_hash: log.exact_hash,
          });
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [apiKey, accessToken]
  );

  const loadedHashRef = useRef<string | null>(null);

  const handleStopStreaming = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setStopped(true);
    setLoading(false);
  };

  const {
    containerRef: feedRef,
    isNearBottom,
    jumpToBottom,
  } = useStickToBottom([response, thinkingContent, loading]);

  useEffect(() => {
    if (!loading) return;
    const start = Date.now();
    const id = window.setInterval(() => setElapsed(Date.now() - start), 100);
    return () => window.clearInterval(id);
  }, [loading]);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (authLoading) return;
    const params = new URLSearchParams(window.location.search);
    const hash = params.get('hash');
    if (!hash) return;
    if (loadedHashRef.current === hash) return;
    loadedHashRef.current = hash;
    loadHash(hash);
  }, [authLoading, apiKey, accessToken, loadHash]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    setStopped(false);
    setLoading(true);
    setResponse('');
    setError(null);
    setThinkingContent('');
    setIsThinking(false);
    setCacheStatus(null);
    setCacheInfo(null);
    setLatency(null);
    setPromptSavings(null);
    setLastLatency(null);

    const startTime = performance.now();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        ...buildAuthHeaders(apiKey, accessToken),
      };

      if (!activeModel) {
        setError(
          modelsError
            ? `Unable to send request: ${modelsError}`
            : 'No available model selected. Configure a provider or sign in.'
        );
        setLoading(false);
        return;
      }

      const bodyPayload: Record<string, unknown> = {
        ...(activeProvider && activeProvider !== AUTO_PROVIDER_ID
          ? { service_name: activeProvider }
          : {}),
        model: activeModel,
        messages: [{ role: 'user', content: prompt }],
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
          setError(
            'Invalid JSON Schema syntax. Please correct the schema in Settings before sending.'
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

      const res = await fetch(`${API_ROOT}/api/v1/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify(bodyPayload),
        signal: abortController.signal,
      });

      const cacheHeader = res.headers.get('X-Cache');
      if (cacheHeader) {
        setCacheStatus(cacheHeader);
      }
      const savingsHeader = res.headers.get('X-EchoGate-Prompt-Savings');
      if (savingsHeader) {
        setPromptSavings(savingsHeader);
      }

      if (!res.ok) {
        let errorMsg = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          errorMsg = errorMessageFrom(errData, errorMsg);
        } catch {
          // ignore json parse error
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
                prompt_optimization?: {
                  applied?: boolean;
                  tokens_saved?: number;
                  chars_saved?: number;
                };
                choices?: Array<{
                  delta?: {
                    reasoning_content?: string;
                    content?: string;
                  };
                }>;
              };

              if (typedChunk.cache_info) {
                setCacheInfo(typedChunk.cache_info);
                setCacheStatus(
                  typedChunk.cache_info.cache_hit ? 'HIT' : 'MISS'
                );
                if (typedChunk.cache_info.exact_hash) {
                  window.history.replaceState(
                    {},
                    '',
                    `/playground?hash=${typedChunk.cache_info.exact_hash}`
                  );
                }
              }

              if (typedChunk.prompt_optimization?.applied) {
                setPromptSavings(
                  `tokens=${typedChunk.prompt_optimization.tokens_saved},chars=${typedChunk.prompt_optimization.chars_saved}`
                );
              }

              const delta = typedChunk.choices?.[0]?.delta;
              if (delta) {
                if (delta.reasoning_content) {
                  accumulatedReasoning += delta.reasoning_content;
                  setThinkingContent(accumulatedReasoning);
                  setIsThinking(true);
                }
                if (delta.content) {
                  accumulatedText += delta.content;

                  // Handle models returning <think>...</think> in content (e.g. DeepSeek-R1 / Ollama)
                  if (accumulatedText.startsWith('<think>')) {
                    const closeIndex = accumulatedText.indexOf('</think>');
                    if (closeIndex !== -1) {
                      const extractedReasoning = accumulatedText
                        .slice(7, closeIndex)
                        .trim();
                      const finalContent = accumulatedText
                        .slice(closeIndex + 8)
                        .trimStart();
                      setThinkingContent(extractedReasoning);
                      setResponse(finalContent);
                      setIsThinking(false);
                    } else {
                      const ongoingReasoning = accumulatedText.slice(7);
                      setThinkingContent(ongoingReasoning);
                      setIsThinking(true);
                    }
                  } else {
                    setResponse(accumulatedText);
                    setIsThinking(false);
                  }
                }
              }
            }
          }
        }

        setIsThinking(false);
        const endTime = performance.now();
        const finalLatency = Math.round(endTime - startTime);
        setLatency(finalLatency);
        setLastLatency(finalLatency);
        return;
      }

      const endTime = performance.now();
      const finalLatency = Math.round(endTime - startTime);
      setLatency(finalLatency);
      setLastLatency(finalLatency);

      const data = await res.json();

      const extractAndSetContent = (rawText: string, reasoning?: string) => {
        if (reasoning) {
          setThinkingContent(reasoning);
        }
        if (
          rawText &&
          rawText.startsWith('<think>') &&
          rawText.includes('</think>')
        ) {
          const closeIndex = rawText.indexOf('</think>');
          const extractedReasoning = rawText.slice(7, closeIndex).trim();
          const cleanText = rawText.slice(closeIndex + 8).trim();
          setThinkingContent(extractedReasoning);
          setResponse(cleanText);
        } else {
          setResponse(rawText);
        }
      };

      if (data.choices && data.choices.length > 0) {
        const msg = data.choices[0].message;
        extractAndSetContent(msg.content || '', msg.reasoning_content);
      } else if (
        data.response &&
        data.response.choices &&
        data.response.choices.length > 0
      ) {
        const msg = data.response.choices[0].message;
        extractAndSetContent(msg.content || '', msg.reasoning_content);
        if (data.response.cache_info) {
          setCacheInfo(data.response.cache_info);
          setCacheStatus(data.response.cache_info.cache_hit ? 'HIT' : 'MISS');
        }
      } else {
        setResponse(JSON.stringify(data, null, 2));
      }

      if (data.cache_info) {
        setCacheInfo(data.cache_info);
        setCacheStatus(data.cache_info.cache_hit ? 'HIT' : 'MISS');

        if (data.cache_info.exact_hash) {
          window.history.replaceState(
            {},
            '',
            `/playground?hash=${data.cache_info.exact_hash}`
          );
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        setStopped(true);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
      abortControllerRef.current = null;
    }
  };

  const isJsonResponse = (() => {
    if (!response) return false;
    const trimmed = response.trim();
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
  })();

  const formattedJson = (() => {
    if (!isJsonResponse) return null;
    try {
      return JSON.stringify(JSON.parse(response.trim()), null, 2);
    } catch {
      return null;
    }
  })();

  return (
    <div className="flex-1 relative flex flex-col md:flex-row h-full overflow-hidden">
      {/* Main Chat Area */}
      <main className="flex-1 flex flex-col h-full relative bg-background overflow-hidden min-w-0">
        {/* Playground Top Bar */}
        <header className="h-14 border-b border-border bg-card/40 backdrop-blur-xs px-4 md:px-6 flex items-center justify-between shrink-0 select-none">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex items-center gap-2">
              <Terminal className="w-4 h-4 text-primary" />
              <span className="font-semibold text-sm text-foreground">
                Playground
              </span>
            </div>
            <div className="hidden sm:flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="text-border">/</span>
              <span className="font-mono px-2 py-0.5 rounded bg-muted/60 border border-border/60 text-[11px] text-foreground font-medium">
                {activeProvider && activeModel
                  ? `${formatProviderLabel(activeProvider)} / ${activeModel}`
                  : isLoadingModels
                    ? 'Loading models...'
                    : 'No models available'}
              </span>
              {promptOptimization && (
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px] font-medium flex items-center gap-1">
                  <Sparkles className="w-3 h-3" /> Opt
                </span>
              )}
              {responseFormatType !== 'text' && (
                <span className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 text-[10px] font-medium flex items-center gap-1">
                  <Braces className="w-3 h-3" />{' '}
                  {responseFormatType === 'json_object'
                    ? 'JSON Mode'
                    : schemaName}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            {(response || error || loading) && (
              <button
                type="button"
                onClick={() => {
                  setResponse('');
                  setError(null);
                  setPrompt('');
                  setThinkingContent('');
                  setLatency(null);
                  setCacheInfo(null);
                  setCacheStatus(null);
                  setPromptSavings(null);
                }}
                className="px-2.5 py-1.5 rounded-lg border border-border/70 hover:bg-muted text-muted-foreground hover:text-foreground text-xs font-medium transition-colors flex items-center gap-1.5 cursor-pointer"
                title="Reset Playground"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Reset</span>
              </button>
            )}
            {(loading || lastLatency !== null || stopped) && (
              <span
                className={cn(
                  'hidden sm:inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium',
                  loading
                    ? 'border-primary/30 bg-primary/10 text-primary'
                    : stopped
                      ? 'border-amber-500/30 bg-amber-500/10 text-amber-500'
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
                ) : stopped ? (
                  <>
                    <span className="h-2 w-2 rounded-full bg-amber-500" />
                    Stopped
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

        {/* Chat History / Output area */}
        <div
          ref={feedRef}
          className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 min-w-0"
          aria-busy={loading}
        >
          <AnimatePresence>
            {!response && !error && !loading && (
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, y: -20 }}
                className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto space-y-4"
              >
                <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center text-primary mb-2">
                  <Sparkles className="w-8 h-8" />
                </div>
                <h2 className="text-2xl font-bold">How can I help you?</h2>
                <p className="text-muted-foreground text-sm">
                  This gateway supports semantic caching. Ask a question, and if
                  you ask a similar one later, it&apos;ll return instantly!
                </p>
              </motion.div>
            )}

            {(loading || response || error) && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="max-w-3xl mx-auto space-y-6 pb-8 min-w-0"
              >
                {/* User Message */}
                <div className="flex justify-end min-w-0">
                  <div className="bg-primary text-primary-foreground px-4 py-3 rounded-2xl rounded-tr-sm max-w-[85%] min-w-0 text-sm sm:text-base shadow-sm leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                    {prompt}
                  </div>
                </div>

                {/* AI Response or Error */}
                <div className="flex justify-start min-w-0">
                  {error ? (
                    <div className="bg-destructive/10 border border-destructive/30 text-destructive px-5 py-4 rounded-2xl rounded-tl-sm max-w-[95%] w-full min-w-0 shadow-sm space-y-2">
                      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider">
                        <AlertCircle className="w-4 h-4 shrink-0" />
                        <span>Gateway / Provider Error</span>
                      </div>
                      <div className="text-sm leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere] max-h-96 overflow-y-auto font-mono">
                        {error}
                      </div>
                    </div>
                  ) : (
                    <div className="bg-card border border-border px-5 py-4 rounded-2xl rounded-tl-sm max-w-[95%] min-w-0 shadow-sm w-full overflow-hidden">
                      {/* Metadata Badges */}
                      {cacheStatus && (
                        <div className="flex flex-wrap items-center gap-2 mb-3 text-xs font-medium">
                          <div
                            className={cn(
                              'px-2 py-1 rounded-md flex items-center gap-1.5 border',
                              cacheStatus === 'HIT' ||
                                cacheStatus === 'DB RECOVERED'
                                ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                                : 'bg-amber-500/10 text-amber-500 border-amber-500/20'
                            )}
                          >
                            <span
                              className={cn(
                                'w-1.5 h-1.5 rounded-full',
                                cacheStatus === 'HIT' ||
                                  cacheStatus === 'DB RECOVERED'
                                  ? 'bg-emerald-500'
                                  : 'bg-amber-500'
                              )}
                            />
                            {cacheStatus === 'HIT'
                              ? 'CACHE HIT'
                              : cacheStatus === 'DB RECOVERED'
                                ? 'DB RECOVERED'
                                : 'CACHE MISS'}
                          </div>

                          {latency !== null && (
                            <div className="px-2 py-1 rounded-md bg-blue-500/10 text-blue-500 border border-blue-500/20 flex items-center gap-1.5">
                              <Clock className="w-3.5 h-3.5" />
                              {latency}ms
                            </div>
                          )}

                          {cacheInfo?.source && (
                            <div className="px-2 py-1 rounded-md bg-purple-500/10 text-purple-500 border border-purple-500/20 flex items-center gap-1.5">
                              <Database className="w-3.5 h-3.5" />
                              {cacheInfo.source}
                            </div>
                          )}

                          {stream && (
                            <div className="px-2 py-1 rounded-md bg-teal-500/10 text-teal-400 border border-teal-500/20 flex items-center gap-1.5">
                              <Sparkles className="w-3.5 h-3.5" />
                              STREAM
                            </div>
                          )}

                          {thinking && (
                            <div className="px-2 py-1 rounded-md bg-violet-500/10 text-violet-400 border border-violet-500/20 flex items-center gap-1.5">
                              <Brain className="w-3.5 h-3.5" />
                              THINKING
                            </div>
                          )}

                          {promptSavings && (
                            <div className="px-2 py-1 rounded-md bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5">
                              <Sparkles className="w-3.5 h-3.5" />
                              Optimized ({promptSavings})
                            </div>
                          )}

                          {responseFormatType !== 'text' && (
                            <div className="px-2 py-1 rounded-md bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 flex items-center gap-1.5">
                              <Braces className="w-3.5 h-3.5" />
                              {responseFormatType === 'json_object'
                                ? 'JSON MODE'
                                : `SCHEMA: ${schemaName}`}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Cache Details Card */}
                      {cacheInfo &&
                        (cacheStatus === 'HIT' ||
                          cacheStatus === 'DB RECOVERED') && (
                          <div className="mb-4 bg-muted/50 rounded-lg p-3 text-lg border border-border/50">
                            <div className="flex items-center gap-1.5 text-muted-foreground font-semibold mb-2 uppercase tracking-wider">
                              <Tag className="w-5 h-5" /> Semantic Match Data
                            </div>
                            <div className="grid grid-cols-2 gap-2 text-muted-foreground">
                              <div>
                                <span className="opacity-70">Intent:</span>{' '}
                                <span className="text-foreground">
                                  {cacheInfo.intent || 'N/A'}
                                </span>
                              </div>
                              <div>
                                <span className="opacity-70">Score:</span>{' '}
                                <span className="text-foreground">
                                  {cacheInfo.score
                                    ? cacheInfo.score.toFixed(4)
                                    : 'Exact'}
                                </span>
                              </div>
                              <div className="col-span-2">
                                <span className="opacity-70">Entities:</span>{' '}
                                <span className="text-foreground">
                                  {cacheInfo.entity_tags?.join(', ') || 'None'}
                                </span>
                              </div>
                            </div>
                          </div>
                        )}

                      {!loading && isJsonResponse && (
                        <div className="flex items-center justify-between mb-3 pb-2 border-b border-border/60 text-xs">
                          <span className="text-muted-foreground flex items-center gap-1.5 font-medium">
                            <Braces className="w-3.5 h-3.5 text-primary" />{' '}
                            Valid JSON Output
                          </span>
                          <div className="flex items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => setRawView(!rawView)}
                              className="px-2 py-1 rounded bg-muted/60 hover:bg-muted border border-border text-foreground flex items-center gap-1 cursor-pointer transition-colors"
                            >
                              <Code2 className="w-3 h-3" />
                              {rawView ? 'Formatted' : 'Raw'}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                navigator.clipboard.writeText(
                                  formattedJson || response
                                );
                                setCopied(true);
                                setTimeout(() => setCopied(false), 2000);
                              }}
                              className="px-2 py-1 rounded bg-muted/60 hover:bg-muted border border-border text-foreground flex items-center gap-1 cursor-pointer transition-colors"
                            >
                              {copied ? (
                                <Check className="w-3 h-3 text-emerald-400" />
                              ) : (
                                <Copy className="w-3 h-3" />
                              )}
                              {copied ? 'Copied' : 'Copy'}
                            </button>
                          </div>
                        </div>
                      )}

                      {/* Thinking Process Accordion */}
                      {thinkingContent && (
                        <div className="mb-4 rounded-xl border border-violet-500/20 bg-violet-950/20 backdrop-blur-sm overflow-hidden transition-all">
                          <button
                            type="button"
                            onClick={() => setIsThinkingOpen(!isThinkingOpen)}
                            className="w-full px-4 py-2.5 flex items-center justify-between text-xs font-medium text-violet-300 hover:bg-violet-500/10 transition-colors cursor-pointer select-none"
                          >
                            <div className="flex items-center gap-2">
                              <Brain
                                className={cn(
                                  'w-4 h-4 text-violet-400',
                                  isThinking && 'animate-pulse text-violet-300'
                                )}
                              />
                              <span>
                                {isThinking
                                  ? 'Thinking in progress...'
                                  : 'Thinking Process'}
                              </span>
                              {isThinking && (
                                <span className="flex h-2 w-2 relative">
                                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-violet-400 opacity-75"></span>
                                  <span className="relative inline-flex rounded-full h-2 w-2 bg-violet-500"></span>
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-1.5 text-violet-400/80">
                              <span className="text-[11px] opacity-75">
                                {
                                  thinkingContent
                                    .trim()
                                    .split(/\s+/)
                                    .filter(Boolean).length
                                }{' '}
                                words
                              </span>
                              <ChevronDown
                                className={cn(
                                  'w-3.5 h-3.5 transition-transform duration-200',
                                  isThinkingOpen && 'rotate-180'
                                )}
                              />
                            </div>
                          </button>
                          <AnimatePresence initial={false}>
                            {isThinkingOpen && (
                              <motion.div
                                initial={{ height: 0, opacity: 0 }}
                                animate={{ height: 'auto', opacity: 1 }}
                                exit={{ height: 0, opacity: 0 }}
                                transition={{ duration: 0.2 }}
                              >
                                <div className="px-4 py-3 border-t border-violet-500/15 text-xs text-violet-200/90 whitespace-pre-wrap font-mono leading-relaxed bg-violet-950/10 max-h-64 overflow-y-auto">
                                  {thinkingContent}
                                  {isThinking && (
                                    <motion.span
                                      animate={{ opacity: [1, 0, 1] }}
                                      transition={{
                                        repeat: Infinity,
                                        duration: 0.8,
                                      }}
                                      className="inline-block w-1.5 h-3 ml-0.5 bg-violet-400 align-middle"
                                    />
                                  )}
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>
                      )}

                      {loading && !response && !thinkingContent ? (
                        <div className="flex gap-1.5 items-center text-muted-foreground py-2">
                          <motion.div
                            animate={{ opacity: [0.4, 1, 0.4] }}
                            transition={{ repeat: Infinity, duration: 1.5 }}
                            className="w-2 h-2 rounded-full bg-primary/60"
                          />
                          <motion.div
                            animate={{ opacity: [0.4, 1, 0.4] }}
                            transition={{
                              repeat: Infinity,
                              duration: 1.5,
                              delay: 0.2,
                            }}
                            className="w-2 h-2 rounded-full bg-primary/60"
                          />
                          <motion.div
                            animate={{ opacity: [0.4, 1, 0.4] }}
                            transition={{
                              repeat: Infinity,
                              duration: 1.5,
                              delay: 0.4,
                            }}
                            className="w-2 h-2 rounded-full bg-primary/60"
                          />
                        </div>
                      ) : isJsonResponse && !rawView && formattedJson ? (
                        <pre className="p-4 rounded-xl bg-muted/40 border border-border/60 font-mono text-sm overflow-x-auto leading-relaxed text-emerald-300 whitespace-pre">
                          <code>{formattedJson}</code>
                          {loading && (
                            <motion.span
                              animate={{ opacity: [1, 0, 1] }}
                              transition={{ repeat: Infinity, duration: 0.8 }}
                              className="inline-block w-2 h-4 ml-1 bg-emerald-300 align-middle rounded-sm"
                            />
                          )}
                        </pre>
                      ) : (
                        <MessageContent
                          content={response}
                          isStreaming={loading}
                        />
                      )}
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

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

        {/* Input Form */}
        <div className="p-4 bg-gradient-to-t from-background via-background to-transparent pt-4">
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
                className="w-full h-12 min-h-[48px] max-h-32 p-3.5 pr-14 bg-transparent border-none focus:ring-0 resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground"
                rows={1}
              />
              <div className="absolute right-2 bottom-2">
                {loading ? (
                  <IconButton
                    type="button"
                    onClick={handleStopStreaming}
                    radius="large"
                    size="3"
                  >
                    <Square className="w-4 h-4 fill-destructive text-destructive" />
                  </IconButton>
                ) : (
                  <IconButton
                    type="submit"
                    disabled={!prompt.trim()}
                    radius="large"
                    size="3"
                  >
                    <Send className="w-4 h-4" />
                  </IconButton>
                )}
              </div>
            </form>
            <div className="flex items-center justify-between mt-2 px-1">
              <span className="text-[10px] text-muted-foreground">
                Echo Gate may produce inaccurate information about people,
                places, or facts.
              </span>
              {responseFormatType !== 'text' && (
                <button
                  type="button"
                  onClick={() => setIsSettingsOpen(true)}
                  className="text-[10px] flex items-center gap-1 text-primary hover:underline cursor-pointer"
                >
                  <Braces className="w-3 h-3" />
                  {responseFormatType === 'json_object'
                    ? 'JSON Mode Active'
                    : `Strict Schema: ${schemaName}`}
                </button>
              )}
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
                    modelContextWindow={
                      activeModel
                        ? modelMetadata[activeModel]?.context_window
                        : undefined
                    }
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

                  <Flex align="center" gap="2">
                    <Checkbox
                      checked={promptOptimization}
                      onCheckedChange={(val) =>
                        setPromptOptimization(Boolean(val))
                      }
                      id="prompt-opt-check"
                    />
                    <Text
                      as="label"
                      size="2"
                      weight="bold"
                      htmlFor="prompt-opt-check"
                      className="cursor-pointer select-none flex items-center gap-1.5"
                    >
                      <Sparkles className="w-4 h-4 text-emerald-400" />
                      Prompt Token Optimization
                    </Text>
                  </Flex>
                </div>
              </div>

              <div className="pt-6 border-t border-border">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2 mb-4">
                  <Braces className="w-4 h-4" /> Structured Output
                </h2>

                <div className="space-y-4">
                  <Flex direction="column" gap="1">
                    <Text as="label" size="2" weight="bold">
                      Format Mode
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
                        <Select.Item value="text">
                          Plain Text (Default)
                        </Select.Item>
                        <Select.Item value="json_object">
                          JSON Mode (Valid Syntax)
                        </Select.Item>
                        <Select.Item value="json_schema">
                          Strict JSON Schema
                        </Select.Item>
                      </Select.Content>
                    </Select.Root>
                  </Flex>

                  {responseFormatType === 'json_object' && (
                    <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/20 text-xs text-blue-300 leading-relaxed">
                      Constrains the model to return syntactically valid JSON.
                      Ensure your prompt asks for a JSON format output.
                    </div>
                  )}

                  {responseFormatType === 'json_schema' && (
                    <div className="space-y-3 pt-1">
                      <Flex direction="column" gap="1">
                        <Text as="label" size="2" weight="bold">
                          Presets
                        </Text>
                        <div className="flex flex-wrap gap-1.5">
                          {SCHEMA_PRESETS.map((preset) => (
                            <button
                              key={preset.name}
                              type="button"
                              onClick={() => {
                                setSchemaName(preset.schemaName);
                                setSchemaContent(
                                  JSON.stringify(preset.schema, null, 2)
                                );
                                setSchemaError(null);
                              }}
                              className="text-[11px] px-2 py-1 bg-muted/60 hover:bg-muted border border-border/80 rounded transition-colors text-foreground cursor-pointer"
                            >
                              {preset.name}
                            </button>
                          ))}
                        </div>
                      </Flex>

                      <Flex direction="column" gap="1">
                        <Text as="label" size="2" weight="bold">
                          Schema Name
                        </Text>
                        <TextField.Root
                          type="text"
                          value={schemaName}
                          onChange={(e) => setSchemaName(e.target.value)}
                          placeholder="e.g. user_details"
                        />
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

              <div className="pt-6 border-t border-border">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-4">
                  Authentication
                </h2>
                <div className="space-y-4">
                  <Flex direction="column" gap="1">
                    <Text as="label" size="2" weight="bold">
                      API Key
                    </Text>
                    <TextField.Root
                      type="password"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder="sk-..."
                    />
                    <Text size="1" color="gray">
                      Your tenant and user identity are resolved from this key.
                      Leave it empty to chat anonymously.
                    </Text>
                  </Flex>
                </div>
              </div>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}
