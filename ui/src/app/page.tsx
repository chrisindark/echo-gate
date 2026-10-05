'use client';

import {
  TextField,
  Text,
  Select,
  Flex,
  Box,
  Slider,
  IconButton,
} from '@radix-ui/themes';
import { cn } from '@/lib/utils';
import { AnimatePresence, motion } from 'framer-motion';
import { Clock, Database, Send, Settings2, Sparkles, Tag } from 'lucide-react';
import { useState, useEffect } from 'react';

const PROVIDERS = {
  ollama: [
    'qwen3:8b',
    'qwen2.5-coder:14b',
    'qwen2.5-coder:1.5b',
    'qwen2.5-coder:3b',
    'qwen2.5-coder:7b',
  ],
  gemini: [
    'gemini-3.1-flash-lite',
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
  ],
  'google-genai': [
    'gemini-3.1-flash-lite',
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
  ],
  openai: ['gpt-4o-mini'],
  groq: ['openai/gpt-oss-20b', 'openai/gpt-oss-safeguard-20b'],
  openrouter: [
    'qwen/qwen3.8-27b:free',
    'google/gemma-4-26b-a4b-it:free',
    'google/gemma-4-31b-it:free',
  ],
};

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
  const [cacheStatus, setCacheStatus] = useState<string | null>(null);
  const [cacheInfo, setCacheInfo] = useState<CacheInfoType | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Settings state
  const [apiKey, setApiKey] = useState('dev');
  const [provider, setProvider] =
    useState<keyof typeof PROVIDERS>('google-genai');
  const [model, setModel] = useState(PROVIDERS['google-genai'][0]);
  const [temperature, setTemperature] = useState<number>(1.0);
  const [maxTokens, setMaxTokens] = useState<number>(8192);
  const [userId, setUserId] = useState('');
  const [tenantId, setTenantId] = useState('');

  const loadHash = async (hash: string) => {
    setLoading(true);
    setResponse('');
    setCacheStatus(null);
    setCacheInfo(null);
    setLatency(null);

    const startTime = performance.now();
    try {
      const res = await fetch(
        `http://localhost:8000/api/v1/gateway-requests/by-hash/${hash}`
      );
      const endTime = performance.now();
      setLatency(Math.round(endTime - startTime));

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
      setResponse(
        `Failed to load from DB: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const hash = params.get('hash');
    if (hash) {
      // eslint-disable-next-line
      loadHash(hash);
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    setLoading(true);
    setResponse('');
    setCacheStatus(null);
    setCacheInfo(null);
    setLatency(null);

    const startTime = performance.now();

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };

      if (apiKey) headers['X-API-Key'] = apiKey;
      if (userId) headers['x-user-id'] = userId;
      if (tenantId) headers['x-tenant-id'] = tenantId;

      const res = await fetch('http://localhost:8000/api/v1/chat/completions', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          service_name: provider,
          model: model,
          messages: [{ role: 'user', content: prompt }],
          temperature: temperature,
          max_tokens: maxTokens,
        }),
      });

      const endTime = performance.now();
      setLatency(Math.round(endTime - startTime));

      const cacheHeader = res.headers.get('X-Cache');
      setCacheStatus(cacheHeader || 'UNKNOWN');

      const data = await res.json();

      if (!res.ok) {
        throw new Error(
          data.detail || data.error_message || `HTTP ${res.status}`
        );
      }

      if (data.choices && data.choices.length > 0) {
        setResponse(data.choices[0].message.content);
      } else if (
        data.response &&
        data.response.choices &&
        data.response.choices.length > 0
      ) {
        setResponse(data.response.choices[0].message.content);
        if (data.response.cache_info) setCacheInfo(data.response.cache_info);
      } else {
        setResponse(JSON.stringify(data, null, 2));
      }

      if (data.cache_info) {
        setCacheInfo(data.cache_info);
        if (data.cache_info.exact_hash) {
          window.history.replaceState(
            {},
            '',
            `/?hash=${data.cache_info.exact_hash}`
          );
        }
      }
    } catch (err: unknown) {
      setResponse(`Error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 relative flex flex-col md:flex-row h-full overflow-hidden">
      {/* Settings Panel */}
      <AnimatePresence>
        {isSettingsOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0, x: -50 }}
            animate={{ width: 320, opacity: 1, x: 0 }}
            exit={{ width: 0, opacity: 0, x: -50 }}
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
                  </Flex>
                  <Flex direction="column" gap="1">
                    <Text as="label" size="2" weight="bold">
                      User ID
                    </Text>
                    <TextField.Root
                      type="text"
                      value={userId}
                      onChange={(e) => setUserId(e.target.value)}
                    />
                  </Flex>
                  <Flex direction="column" gap="1">
                    <Text as="label" size="2" weight="bold">
                      Tenant ID
                    </Text>
                    <TextField.Root
                      type="text"
                      value={tenantId}
                      onChange={(e) => setTenantId(e.target.value)}
                    />
                  </Flex>
                </div>
              </div>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* Main Chat Area */}
      <main className="flex-1 flex flex-col h-full relative bg-background">
        {/* Settings Toggle Button */}
        <button
          onClick={() => setIsSettingsOpen(!isSettingsOpen)}
          className="fixed top-4 right-4 z-50 p-2 bg-card border border-border rounded-md shadow-sm hover:bg-muted transition-colors text-foreground"
        >
          <Settings2 className="w-5 h-5" />
        </button>

        {/* Chat History / Output area */}
        <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-6">
          <AnimatePresence>
            {!response && !loading && (
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

            {(loading || response) && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="max-w-3xl mx-auto space-y-6 pb-24"
              >
                {/* User Message */}
                <div className="flex justify-end">
                  <div className="bg-primary text-primary-foreground px-5 py-3.5 rounded-2xl rounded-tr-sm max-w-[85%] text-lg shadow-sm">
                    {prompt}
                  </div>
                </div>

                {/* AI Response */}
                <div className="flex justify-start">
                  <div className="bg-card border border-border px-5 py-4 rounded-2xl rounded-tl-sm max-w-[95%] shadow-sm w-full">
                    {/* Metadata Badges */}
                    {cacheStatus && (
                      <div className="flex flex-wrap items-center gap-2 mb-4 text-lg font-medium">
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

                    {loading ? (
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
                    ) : (
                      <div className="leading-relaxed whitespace-pre-wrap text-lg">
                        {response}
                      </div>
                    )}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Input Form Floating at bottom */}
        <div className="absolute bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-background via-background to-transparent pt-12">
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
                className="w-full h-14 min-h-[56px] max-h-32 p-4 pr-14 bg-transparent border-none focus:ring-0 resize-none outline-none text-lg"
                rows={1}
              />
              <div className="absolute right-2 bottom-2">
                <IconButton
                  type="submit"
                  disabled={loading || !prompt.trim()}
                  radius="large"
                  size="3"
                >
                  <Send className="w-4 h-4" />
                </IconButton>
              </div>
            </form>
            <div className="text-center mt-2">
              <span className="text-[10px] text-muted-foreground">
                Echo Gate may produce inaccurate information about people,
                places, or facts.
              </span>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
