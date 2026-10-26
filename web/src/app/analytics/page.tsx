'use client';

import { useApiKey } from '@/context/api-key-context';
import { cn } from '@/lib/utils';
import { API_ROOT } from '@/lib/api';
import { Box, Card, Flex, Heading, Text } from '@radix-ui/themes';
import { motion } from 'framer-motion';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  Clock,
  Database,
  FlaskConical,
  Gauge,
  Layers,
  Sparkles,
  TrendingDown,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

type TimeRange =
  '1h' | '3h' | '6h' | '12h' | '1d' | '3d' | '7d' | '14d' | '30d';

interface TimeRangeOption {
  label: string;
  value: TimeRange;
  short: string;
}

const TIME_RANGES: TimeRangeOption[] = [
  { label: '1 Hour', value: '1h', short: '1h' },
  { label: '3 Hours', value: '3h', short: '3h' },
  { label: '6 Hours', value: '6h', short: '6h' },
  { label: '12 Hours', value: '12h', short: '12h' },
  { label: '1 Day', value: '1d', short: '1d' },
  { label: '3 Days', value: '3d', short: '3d' },
  { label: '7 Days', value: '7d', short: '7d' },
  { label: '14 Days', value: '14d', short: '14d' },
  { label: '30 Days', value: '30d', short: '30d' },
];

const SHADOW_MATCH_LABELS: Record<string, string> = {
  REDIS_EXACT: 'Redis exact',
  QDRANT_EXACT: 'Qdrant exact',
  QDRANT_FAST: 'Qdrant dense fast-path',
  QDRANT_SEMANTIC: 'Qdrant semantic',
  MISS: 'Provider (miss)',
};

interface LatencyComponentBreakdown {
  redis_exact_ms: number | null;
  qdrant_exact_ms: number | null;
  intent_classify_ms: number | null;
  embedding_gen_ms: number | null;
  qdrant_dense_ms: number | null;
  qdrant_rrf_ms: number | null;
  rerank_ms: number | null;
  cache_lookup_total_ms: number | null;
  provider_ms: number | null;
  cache_write_ms: number | null;
  total_ms: number | null;
}

interface LatencyPercentiles {
  p50: number | null;
  p90: number | null;
  p95: number | null;
  p99: number | null;
}

interface LatencyByRoutingDecision {
  routing_decision: string;
  count: number;
  avg_latency_ms: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
}

interface LatencyTimeSeriesPoint {
  time_label: string;
  timestamp: string;
  avg_total_latency_ms: number;
  avg_provider_latency_ms: number | null;
  avg_cache_lookup_latency_ms: number | null;
  requests: number;
}

interface LatencyMetrics {
  time_range?: string;
  total_requests: number;
  avg_latency_ms: number;
  avg_provider_latency_ms: number | null;
  avg_cache_lookup_latency_ms: number | null;
  percentiles: LatencyPercentiles;
  component_breakdown: LatencyComponentBreakdown;
  by_routing_decision: LatencyByRoutingDecision[];
  time_series: LatencyTimeSeriesPoint[];
}

interface AnalyticsData {
  kpis: {
    total_requests: number;
    cache_hit_rate: number;
    avg_latency_ms: number;
    total_cost: number;
    total_tokens: number;
  };
  time_series: Array<{
    date_label: string;
    requests: number;
    cost: number;
    tokens: number;
  }>;
  cache_performance: {
    total: number;
    llm_calls: number;
    qdrant_semantic: number;
    qdrant_exact: number;
    redis_exact: number;
  };
  top_intents: Array<{
    intent: string;
    count: number;
  }>;
  evaluations: {
    evaluated_count: number;
    avg_relevance_score: number;
    avg_contradiction_score?: number | null;
    avg_entailment_score?: number;
    false_positive_rate: number;
  };
  latency_metrics?: LatencyMetrics;
}

interface ShadowSummary {
  cache_mode: string;
  total_evaluations: number;
  would_have_hits: number;
  hit_rate: number;
  match_type_breakdown: Array<{
    match_type: string;
    count: number;
  }>;
  compared_evaluations: number;
  equivalent_evaluations: number;
  non_equivalent_evaluations: number;
  equivalence_rate: number | null;
  avg_latency_saved_ms: number | null;
  projected_latency_saved_ms: number | null;
  avg_cache_lookup_ms: number | null;
  avg_provider_ms: number | null;
  by_intent: Array<{
    intent: string;
    evaluations: number;
    would_have_hits: number;
    hit_rate: number;
  }>;
  by_model: Array<{
    model: string;
    provider: string | null;
    evaluations: number;
    would_have_hits: number;
    hit_rate: number;
  }>;
  readiness: {
    status: 'READY' | 'WARMING_UP' | 'NOT_READY';
    reasons: string[];
    min_evaluations: number;
    min_hit_rate: number;
    min_equivalence_rate: number;
  };
}

export default function AnalyticsPage() {
  const { apiKey } = useApiKey();
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [isFetching, setIsFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<TimeRange>('7d');
  const [shadow, setShadow] = useState<ShadowSummary | null>(null);
  const [shadowError, setShadowError] = useState<string | null>(null);

  const refetch = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ time_range: timeRange });
    const headers: Record<string, string> = {};
    if (apiKey) headers['X-API-Key'] = apiKey;

    fetch(`${API_ROOT}/api/v1/analytics/dashboard?${params.toString()}`, {
      headers,
    })
      .then((res) => {
        if (!res.ok) {
          if (res.status === 401) {
            throw new Error(
              'Unauthorized (401): Valid API key required. Go to Settings to configure your key.'
            );
          }
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then((result) => {
        setData(result.response || result);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'An unknown error occurred');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [timeRange, apiKey]);

  useEffect(() => {
    let ignore = false;
    const fetchAnalytics = async () => {
      try {
        const params = new URLSearchParams({
          time_range: timeRange,
        });
        const headers: Record<string, string> = {};
        if (apiKey) headers['X-API-Key'] = apiKey;

        const res = await fetch(
          `${API_ROOT}/api/v1/analytics/dashboard?${params.toString()}`,
          { headers }
        );
        if (!res.ok) {
          if (res.status === 401) {
            throw new Error(
              'Unauthorized (401): Valid API key required. Go to Settings to configure your key.'
            );
          }
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        const result = await res.json();
        if (!ignore) {
          setData(result.response || result);
          setError(null);
        }
      } catch (e: unknown) {
        if (!ignore) {
          setError(
            e instanceof Error ? e.message : 'An unknown error occurred'
          );
        }
      } finally {
        if (!ignore) {
          setLoading(false);
          setIsFetching(false);
        }
      }
    };

    fetchAnalytics();
    return () => {
      ignore = true;
    };
  }, [timeRange, apiKey]);

  // Shadow-pipeline readiness is fetched separately so a slow/absent shadow
  // window never blocks the rest of the dashboard.
  useEffect(() => {
    let ignore = false;
    const headers: Record<string, string> = {};
    if (apiKey) headers['X-API-Key'] = apiKey;

    fetch(
      `${API_ROOT}/api/v1/shadow-cache/summary?time_range=${timeRange}`,
      { headers }
    )
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then((result) => {
        if (!ignore) {
          setShadow(result.response || result);
          setShadowError(null);
        }
      })
      .catch((e: unknown) => {
        if (!ignore) {
          setShadowError(
            e instanceof Error ? e.message : 'An unknown error occurred'
          );
        }
      });

    return () => {
      ignore = true;
    };
  }, [timeRange, apiKey]);

  const container = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: { staggerChildren: 0.08 },
    },
  };

  const item = {
    hidden: { opacity: 0, y: 15 },
    show: { opacity: 1, y: 0 },
  };

  if (loading && !data) {
    return (
      <div className="p-8 flex justify-center items-center h-full">
        <div className="animate-pulse text-muted-foreground flex items-center gap-2">
          <Clock className="w-5 h-5 animate-spin" />
          Loading analytics...
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="p-8 flex flex-col items-center justify-center h-full gap-4">
        <div className="text-red-500 font-medium">
          Error loading analytics: {error}
        </div>
        <button
          onClick={refetch}
          className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!data) return null;

  const kpis = data.kpis;
  const timeSeries = data.time_series;
  const cacheStats = data.cache_performance;
  const intents = data.top_intents;
  const evals = data.evaluations;
  const latency = data.latency_metrics;

  const shadowReadiness = shadow?.readiness;
  const readinessStatus = shadowReadiness?.status ?? 'NOT_READY';
  const readinessLabel =
    readinessStatus === 'READY'
      ? 'Ready for active caching'
      : readinessStatus === 'WARMING_UP'
        ? 'Warming up'
        : 'Not ready';
  const readinessBadgeClass =
    readinessStatus === 'READY'
      ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30'
      : readinessStatus === 'WARMING_UP'
        ? 'bg-amber-500/10 text-amber-600 border-amber-500/30'
        : 'bg-secondary text-muted-foreground border-border';
  const ReadinessIcon =
    readinessStatus === 'READY'
      ? CheckCircle2
      : readinessStatus === 'WARMING_UP'
        ? Clock
        : AlertTriangle;
  const shadowHitRatePct = (shadow?.hit_rate ?? 0) * 100;
  const shadowEquivalencePct =
    shadow?.equivalence_rate !== null && shadow?.equivalence_rate !== undefined
      ? shadow.equivalence_rate * 100
      : null;
  const shadowMatchBreakdown = shadow?.match_type_breakdown ?? [];
  const shadowMaxMatchCount = shadowMatchBreakdown.length
    ? Math.max(...shadowMatchBreakdown.map((m) => m.count))
    : 0;

  const maxRequests = timeSeries?.length
    ? Math.max(...timeSeries.map((d) => d.requests))
    : 0;

  const latencySeries = latency?.time_series || [];
  const maxLatency = latencySeries.length
    ? Math.max(...latencySeries.map((d) => d.avg_total_latency_ms))
    : 0;

  const isSubDaily = ['1h', '3h', '6h', '12h', '1d'].includes(timeRange);

  const formatTickLabel = (label: string) => {
    try {
      const d = new Date(label);
      if (isNaN(d.getTime())) return label;
      if (isSubDaily) {
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
      return d.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
      });
    } catch {
      return label;
    }
  };

  // Speedup calculations
  const cacheHits = (cacheStats?.total || 0) - (cacheStats?.llm_calls || 0);
  const avgProviderLatency = latency?.avg_provider_latency_ms || 0;
  const avgCacheLatency = latency?.avg_cache_lookup_latency_ms || 0;
  const speedupRatio =
    avgCacheLatency > 0 && avgProviderLatency > 0
      ? (avgProviderLatency / avgCacheLatency).toFixed(1)
      : null;
  const estimatedTimeSavedSec =
    avgProviderLatency > avgCacheLatency && cacheHits > 0
      ? (((avgProviderLatency - avgCacheLatency) * cacheHits) / 1000).toFixed(1)
      : null;

  // Pipeline latency breakdown items
  const componentList = [
    {
      key: 'redis_exact_ms',
      label: 'Redis Exact Match',
      category: 'Cache Lookup',
      desc: 'In-memory exact hash hit check',
      value: latency?.component_breakdown?.redis_exact_ms,
      color: 'bg-emerald-500',
    },
    {
      key: 'qdrant_exact_ms',
      label: 'Qdrant Exact Match',
      category: 'Cache Lookup',
      desc: 'Vector DB payload hash check',
      value: latency?.component_breakdown?.qdrant_exact_ms,
      color: 'bg-blue-500',
    },
    {
      key: 'intent_classify_ms',
      label: 'Intent Classification',
      category: 'Cache Lookup',
      desc: 'Semantic intent identification',
      value: latency?.component_breakdown?.intent_classify_ms,
      color: 'bg-purple-500',
    },
    {
      key: 'embedding_gen_ms',
      label: 'Embedding Generation',
      category: 'Cache Lookup',
      desc: 'Dense embedding inference',
      value: latency?.component_breakdown?.embedding_gen_ms,
      color: 'bg-indigo-500',
    },
    {
      key: 'qdrant_dense_ms',
      label: 'Qdrant Dense Search',
      category: 'Cache Lookup',
      desc: 'Vector index similarity search',
      value: latency?.component_breakdown?.qdrant_dense_ms,
      color: 'bg-cyan-500',
    },
    {
      key: 'qdrant_rrf_ms',
      label: 'Qdrant RRF Fusion',
      category: 'Cache Lookup',
      desc: 'Reciprocal rank fusion scoring',
      value: latency?.component_breakdown?.qdrant_rrf_ms,
      color: 'bg-sky-500',
    },
    {
      key: 'rerank_ms',
      label: 'Cross-Encoder Rerank',
      category: 'Cache Lookup',
      desc: 'Neural cross-encoder verification',
      value: latency?.component_breakdown?.rerank_ms,
      color: 'bg-violet-500',
    },
    {
      key: 'cache_lookup_total_ms',
      label: 'Total Cache Lookup',
      category: 'Cache Lookup',
      desc: 'End-to-end cache pipeline time',
      value: latency?.component_breakdown?.cache_lookup_total_ms,
      color: 'bg-teal-500',
      isTotal: true,
    },
    {
      key: 'provider_ms',
      label: 'LLM Provider Call',
      category: 'Execution',
      desc: 'Direct upstream model inference on miss',
      value: latency?.component_breakdown?.provider_ms,
      color: 'bg-amber-500',
    },
    {
      key: 'cache_write_ms',
      label: 'Cache Storage / Write',
      category: 'Execution',
      desc: 'Persisting prompt and response to cache',
      value: latency?.component_breakdown?.cache_write_ms,
      color: 'bg-rose-500',
    },
    {
      key: 'total_ms',
      label: 'End-to-End Pipeline',
      category: 'Total',
      desc: 'Complete gateway traversal latency',
      value: latency?.component_breakdown?.total_ms,
      color: 'bg-primary',
      isTotal: true,
    },
  ];

  const maxComponentValue = Math.max(
    ...componentList.filter((c) => !c.isTotal).map((c) => c.value || 0),
    1
  );

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 bg-background">
      <div className="max-w-6xl mx-auto space-y-8">
        {/* Header and Time Range Selector */}
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between border-b border-border/50 pb-6">
          <div>
            <Heading size="8" mb="1" className="tracking-tight">
              Analytics Dashboard
            </Heading>
            <Text color="gray" size="2">
              Monitor latency metrics, caching efficiency, and LLM provider
              usage.
            </Text>
          </div>

          {/* Time Range Pills */}
          <div className="flex flex-col items-start md:items-end gap-1.5">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Time Range
            </span>
            <div className="flex items-center gap-1 p-1 bg-muted/60 border border-border/60 rounded-xl overflow-x-auto max-w-full">
              {TIME_RANGES.map((range) => {
                const isActive = timeRange === range.value;
                return (
                  <button
                    key={range.value}
                    onClick={() => {
                      if (timeRange !== range.value) {
                        setIsFetching(true);
                        setTimeRange(range.value);
                      }
                    }}
                    disabled={isFetching}
                    className={cn(
                      'px-2.5 py-1 text-xs font-medium rounded-lg transition-all whitespace-nowrap',
                      isActive
                        ? 'bg-background text-foreground shadow-sm font-semibold border border-border/80'
                        : 'text-muted-foreground hover:text-foreground hover:bg-background/40',
                      isFetching && 'opacity-60 cursor-not-allowed'
                    )}
                  >
                    {range.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Top KPI Summary Cards */}
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="grid gap-4 md:grid-cols-2 lg:grid-cols-4"
        >
          <motion.div variants={item}>
            <Card size="2">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Total Requests
                </Text>
                <BarChart3 className="h-4 w-4 text-muted-foreground" />
              </Flex>
              <Box>
                <Text size="7" weight="bold">
                  {kpis?.total_requests?.toLocaleString() || 0}
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  In selected{' '}
                  {TIME_RANGES.find(
                    (t) => t.value === timeRange
                  )?.label.toLowerCase()}
                </Text>
              </Box>
            </Card>
          </motion.div>

          <motion.div variants={item}>
            <Card size="2">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Cache Hit Rate
                </Text>
                <Zap className="h-4 w-4 text-emerald-500" />
              </Flex>
              <Box>
                <Text size="7" weight="bold">
                  {kpis?.cache_hit_rate !== undefined &&
                  kpis?.cache_hit_rate !== null
                    ? Number(kpis.cache_hit_rate).toFixed(1)
                    : '0.0'}
                  %
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  {(cacheStats?.total || 0) - (cacheStats?.llm_calls || 0)} hits
                  / {cacheStats?.llm_calls || 0} misses
                </Text>
              </Box>
            </Card>
          </motion.div>

          <motion.div variants={item}>
            <Card size="2">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Avg. Gateway Latency
                </Text>
                <Clock className="h-4 w-4 text-blue-500" />
              </Flex>
              <Box>
                <Text size="7" weight="bold">
                  {kpis?.avg_latency_ms ? Math.round(kpis.avg_latency_ms) : 0}ms
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  {latency?.percentiles?.p50
                    ? `P50: ${Math.round(latency.percentiles.p50)}ms · P95: ${Math.round(latency.percentiles.p95 || 0)}ms`
                    : 'Average across all requests'}
                </Text>
              </Box>
            </Card>
          </motion.div>

          <motion.div variants={item}>
            <Card size="2">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Total Cost
                </Text>
                <Database className="h-4 w-4 text-purple-500" />
              </Flex>
              <Box>
                <Text size="7" weight="bold">
                  ${kpis?.total_cost ? kpis.total_cost.toFixed(4) : '0.0000'}
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  {kpis?.total_tokens?.toLocaleString() || 0} tokens consumed
                </Text>
              </Box>
            </Card>
          </motion.div>
        </motion.div>

        {/* Cache Readiness — Shadow Pipeline */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
        >
          <Card size="3">
            <Flex align="start" justify="between" wrap="wrap" gap="3" mb="3">
              <Box>
                <Flex align="center" gap="2">
                  <FlaskConical className="w-5 h-5 text-primary" />
                  <Heading size="4">Cache Readiness — Shadow Pipeline</Heading>
                </Flex>
                <Text size="2" color="gray">
                  Providers still answer every request; the full cache pipeline
                  replays in the background so you can see what active caching
                  would do before switching it on.
                </Text>
              </Box>
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold',
                  readinessBadgeClass
                )}
              >
                <ReadinessIcon className="w-3.5 h-3.5" />
                {readinessLabel}
              </span>
            </Flex>

            {shadowError ? (
              <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-xl flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
                <div className="text-xs text-muted-foreground leading-relaxed">
                  <span className="font-semibold text-foreground">
                    Shadow telemetry unavailable.
                  </span>{' '}
                  {shadowError}
                </div>
              </div>
            ) : !shadow || shadow.total_evaluations === 0 ? (
              <div className="p-3 bg-secondary/60 border border-border/60 rounded-xl flex items-start gap-3">
                <FlaskConical className="w-5 h-5 text-muted-foreground shrink-0 mt-0.5" />
                <div className="text-xs text-muted-foreground leading-relaxed">
                  <span className="font-semibold text-foreground">
                    No shadow evaluations in this window.
                  </span>{' '}
                  {shadow?.cache_mode === 'active' ? (
                    <>
                      Caching is currently{' '}
                      <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-background border border-border/60">
                        active
                      </code>
                      , so requests are served from the cache and nothing is
                      replayed. Shadow evaluations only accumulate in shadow
                      mode.
                    </>
                  ) : (
                    <>
                      Set{' '}
                      <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-background border border-border/60">
                        CACHE_MODE=shadow
                      </code>{' '}
                      on the gateway and send traffic through{' '}
                      <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-background border border-border/60">
                        /api/v1/chat/completions
                      </code>
                      . Cross-check{' '}
                      <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-background border border-border/60">
                        SHADOW_SAMPLE_RATE
                      </code>{' '}
                      if only a fraction of requests is recorded.
                    </>
                  )}
                </div>
              </div>
            ) : (
              <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="bg-background/80 rounded-lg p-3 border border-border/40">
                    <div className="text-[10px] text-muted-foreground uppercase font-medium">
                      Would-be hit rate
                    </div>
                    <div className="text-xl font-bold font-mono mt-1 text-emerald-500">
                      {shadowHitRatePct.toFixed(1)}%
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-1">
                      {shadow.would_have_hits.toLocaleString()} of{' '}
                      {shadow.total_evaluations.toLocaleString()} requests would
                      have been served from cache
                    </div>
                  </div>

                  <div className="bg-background/80 rounded-lg p-3 border border-border/40">
                    <div className="text-[10px] text-muted-foreground uppercase font-medium">
                      Judge equivalence
                    </div>
                    <div
                      className={cn(
                        'text-xl font-bold font-mono mt-1',
                        shadowEquivalencePct === null
                          ? 'text-muted-foreground'
                          : shadowEquivalencePct >= 90
                            ? 'text-emerald-500'
                            : shadowEquivalencePct >= 75
                              ? 'text-amber-500'
                              : 'text-rose-500'
                      )}
                    >
                      {shadowEquivalencePct === null
                        ? '—'
                        : `${shadowEquivalencePct.toFixed(1)}%`}
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-1">
                      {shadow.compared_evaluations > 0
                        ? `${shadow.equivalent_evaluations} equivalent · ${shadow.non_equivalent_evaluations} not equivalent of ${shadow.compared_evaluations} compared`
                        : 'No would-be hit has been judged against a fresh answer yet'}
                    </div>
                  </div>

                  <div className="bg-background/80 rounded-lg p-3 border border-border/40">
                    <div className="text-[10px] text-muted-foreground uppercase font-medium">
                      Projected latency saved
                    </div>
                    <div className="text-xl font-bold font-mono mt-1 text-blue-500">
                      {shadow.projected_latency_saved_ms !== null
                        ? `${shadow.projected_latency_saved_ms.toLocaleString()} ms`
                        : '—'}
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-1">
                      {shadow.avg_latency_saved_ms !== null
                        ? `Avg ${Math.round(shadow.avg_latency_saved_ms)} ms per would-be hit`
                        : 'No would-be hits measured'}
                      {shadow.avg_cache_lookup_ms !== null &&
                      shadow.avg_provider_ms !== null
                        ? ` · ${Math.round(shadow.avg_cache_lookup_ms)} ms lookup vs ${Math.round(shadow.avg_provider_ms)} ms provider`
                        : ''}
                    </div>
                  </div>
                </div>

                <div className="mt-3 bg-background/80 rounded-lg p-3 border border-border/40">
                  <div className="text-[10px] text-muted-foreground uppercase font-medium mb-2">
                    Where matches would have come from
                  </div>
                  <div className="space-y-2">
                    {shadowMatchBreakdown.map((match) => (
                      <div key={match.match_type} className="space-y-1">
                        <div className="flex justify-between text-xs">
                          <span className="font-medium">
                            {SHADOW_MATCH_LABELS[match.match_type] ??
                              match.match_type.replace(/_/g, ' ')}
                          </span>
                          <span className="text-muted-foreground">
                            {match.count.toLocaleString()}
                          </span>
                        </div>
                        <div className="w-full bg-secondary rounded-full h-1.5">
                          <div
                            className={cn(
                              'h-1.5 rounded-full',
                              match.match_type === 'MISS'
                                ? 'bg-muted-foreground/40'
                                : 'bg-emerald-500'
                            )}
                            style={{
                              width: `${shadowMaxMatchCount > 0 ? (match.count / shadowMaxMatchCount) * 100 : 0}%`,
                            }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {shadowReadiness && shadowReadiness.reasons.length > 0 ? (
                  <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl flex items-start gap-3 mt-3">
                    <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
                    <div className="text-xs text-muted-foreground leading-relaxed">
                      <span className="font-semibold text-foreground">
                        What is keeping this from being ready:
                      </span>
                      <ul className="list-disc pl-4 mt-1 space-y-0.5">
                        {shadowReadiness.reasons.map((reason, idx) => (
                          <li key={idx}>{reason}</li>
                        ))}
                      </ul>
                      <p className="mt-1">
                        Floors: {shadowReadiness.min_evaluations} evaluations ·{' '}
                        {(shadowReadiness.min_hit_rate * 100).toFixed(0)}%
                        would-be hit rate ·{' '}
                        {(shadowReadiness.min_equivalence_rate * 100).toFixed(0)}%
                        equivalence.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl flex items-start gap-3 mt-3">
                    <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0 mt-0.5" />
                    <div className="text-xs text-muted-foreground leading-relaxed">
                      <span className="font-semibold text-foreground">
                        Shadow traffic supports active caching.
                      </span>{' '}
                      Would-be hits clear the hit-rate floor and judged answers
                      agree. Set{' '}
                      <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-background border border-border/60">
                        CACHE_MODE=active
                      </code>{' '}
                      to start serving from the cache; keep shadow mode running
                      in a canary environment if you want a control group.
                    </div>
                  </div>
                )}

                {shadow.by_model.length > 0 && (
                  <div className="mt-3">
                    <div className="text-[10px] text-muted-foreground uppercase font-medium mb-2">
                      Would-be hit rate by model
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {shadow.by_model.slice(0, 6).map((model) => (
                        <div
                          key={`${model.provider ?? 'unknown'}:${model.model}`}
                          className="flex items-center justify-between gap-3 bg-background/80 rounded-lg px-3 py-2 border border-border/40 text-xs"
                        >
                          <div className="min-w-0">
                            <div className="font-medium truncate">
                              {model.model}
                            </div>
                            <div className="text-[10px] text-muted-foreground truncate">
                              {model.provider ?? 'unknown'} ·{' '}
                              {model.evaluations.toLocaleString()} evaluated
                            </div>
                          </div>
                          <span className="font-mono font-bold shrink-0">
                            {(model.hit_rate * 100).toFixed(1)}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </Card>
        </motion.div>

        {/* Section Header: Latency Metrics */}
        <div className="pt-2">
          <Flex align="center" gap="2" mb="1">
            <Gauge className="w-5 h-5 text-primary" />
            <Heading size="5">Latency Metrics & Service Traversal</Heading>
          </Flex>
          <Text size="2" color="gray">
            Breakdown of gateway performance across in-memory cache, vector
            lookups, reranking, and model providers.
          </Text>
        </div>

        {/* Latency Overview Cards */}
        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="grid gap-4 md:grid-cols-2 lg:grid-cols-4"
        >
          <motion.div variants={item}>
            <Card size="2" className="border-border/80">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Cache Lookup Avg
                </Text>
                <Zap className="h-4 w-4 text-emerald-500" />
              </Flex>
              <Box>
                <Text size="6" weight="bold" className="text-emerald-500">
                  {latency?.avg_cache_lookup_latency_ms !== null &&
                  latency?.avg_cache_lookup_latency_ms !== undefined
                    ? `${latency.avg_cache_lookup_latency_ms.toFixed(1)}ms`
                    : 'N/A'}
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  Redis + Qdrant search & rerank
                </Text>
              </Box>
            </Card>
          </motion.div>

          <motion.div variants={item}>
            <Card size="2" className="border-border/80">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  LLM Provider Avg
                </Text>
                <Activity className="h-4 w-4 text-amber-500" />
              </Flex>
              <Box>
                <Text size="6" weight="bold" className="text-amber-500">
                  {latency?.avg_provider_latency_ms !== null &&
                  latency?.avg_provider_latency_ms !== undefined
                    ? `${latency.avg_provider_latency_ms.toFixed(0)}ms`
                    : 'N/A'}
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  Direct upstream API call on miss
                </Text>
              </Box>
            </Card>
          </motion.div>

          <motion.div variants={item}>
            <Card size="2" className="border-border/80">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Cache Speedup
                </Text>
                <TrendingDown className="h-4 w-4 text-emerald-500" />
              </Flex>
              <Box>
                <Text size="6" weight="bold" className="text-emerald-500">
                  {speedupRatio ? `${speedupRatio}x faster` : 'N/A'}
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  {estimatedTimeSavedSec
                    ? `~${estimatedTimeSavedSec}s saved by cache`
                    : 'Cached vs provider duration'}
                </Text>
              </Box>
            </Card>
          </motion.div>

          <motion.div variants={item}>
            <Card size="2" className="border-border/80">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Percentiles (P50 - P99)
                </Text>
                <Sparkles className="h-4 w-4 text-purple-500" />
              </Flex>
              <Box>
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-bold text-foreground">
                    P50:{' '}
                    {latency?.percentiles?.p50
                      ? Math.round(latency.percentiles.p50)
                      : 0}
                    ms
                  </span>
                  <span className="text-xs text-muted-foreground">·</span>
                  <span className="text-sm font-bold text-foreground">
                    P95:{' '}
                    {latency?.percentiles?.p95
                      ? Math.round(latency.percentiles.p95)
                      : 0}
                    ms
                  </span>
                </div>
                <Text as="p" size="1" color="gray" mt="1">
                  P90:{' '}
                  {latency?.percentiles?.p90
                    ? Math.round(latency.percentiles.p90)
                    : 0}
                  ms · P99:{' '}
                  {latency?.percentiles?.p99
                    ? Math.round(latency.percentiles.p99)
                    : 0}
                  ms
                </Text>
              </Box>
            </Card>
          </motion.div>
        </motion.div>

        {/* Detailed Breakdown: Component Latency & Latency by Routing */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="grid gap-4 md:grid-cols-2"
        >
          {/* Left Column: Granular Component Breakdown */}
          <Card size="3" className="flex flex-col justify-between">
            <Box mb="4">
              <Heading size="4" className="flex items-center gap-2">
                <Layers className="w-5 h-5 text-blue-500" />
                Service Component Breakdown
              </Heading>
              <Text size="2" color="gray">
                Average latency recorded for each individual step of the request
                lifecycle.
              </Text>
            </Box>

            <div className="space-y-3.5">
              {componentList.map((comp) => {
                const hasValue =
                  comp.value !== null && comp.value !== undefined;
                const pct =
                  hasValue && maxComponentValue > 0
                    ? Math.min(
                        100,
                        Math.max(3, (comp.value! / maxComponentValue) * 100)
                      )
                    : 0;

                return (
                  <div
                    key={comp.key}
                    className={cn(
                      'space-y-1',
                      comp.isTotal &&
                        'pt-2 mt-2 border-t border-border/60 font-medium'
                    )}
                  >
                    <div className="flex justify-between items-center text-xs sm:text-sm">
                      <div className="flex items-center gap-2">
                        <div
                          className={cn('w-2 h-2 rounded-full', comp.color)}
                        />
                        <span className="font-medium text-foreground">
                          {comp.label}
                        </span>
                        <span className="text-[11px] text-muted-foreground hidden sm:inline">
                          ({comp.desc})
                        </span>
                      </div>
                      <span className="font-mono font-semibold text-foreground">
                        {hasValue ? `${comp.value!.toFixed(1)} ms` : '—'}
                      </span>
                    </div>

                    {!comp.isTotal && (
                      <div className="w-full bg-secondary/80 rounded-full h-1.5 overflow-hidden">
                        <div
                          className={cn(
                            'h-1.5 rounded-full transition-all',
                            comp.color
                          )}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Right Column: Latency by Routing Decision */}
          <Card size="3" className="flex flex-col justify-between">
            <Box mb="4">
              <Heading size="4" className="flex items-center gap-2">
                <Zap className="w-5 h-5 text-emerald-500" />
                Latency by Routing Decision
              </Heading>
              <Text size="2" color="gray">
                Comparison of request durations between cache hits and full LLM
                generation.
              </Text>
            </Box>

            <div className="space-y-4">
              {latency?.by_routing_decision &&
              latency.by_routing_decision.length > 0 ? (
                latency.by_routing_decision.map((route) => {
                  const isLlm = route.routing_decision === 'LLM';
                  const badgeColor = isLlm
                    ? 'border-amber-500/40 bg-amber-500/10 text-amber-500'
                    : route.routing_decision === 'REDIS_EXACT'
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-500'
                      : route.routing_decision === 'QDRANT_EXACT'
                        ? 'border-blue-500/40 bg-blue-500/10 text-blue-500'
                        : 'border-purple-500/40 bg-purple-500/10 text-purple-500';

                  return (
                    <div
                      key={route.routing_decision}
                      className="p-3.5 rounded-xl border border-border/60 bg-muted/20 space-y-2"
                    >
                      <div className="flex items-center justify-between">
                        <span
                          className={cn(
                            'text-xs font-semibold px-2 py-0.5 rounded-md border',
                            badgeColor
                          )}
                        >
                          {route.routing_decision}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {route.count}{' '}
                          {route.count === 1 ? 'request' : 'requests'}
                        </span>
                      </div>

                      <div className="grid grid-cols-3 gap-2 pt-1 text-center">
                        <div className="bg-background/80 rounded-lg p-2 border border-border/40">
                          <div className="text-[10px] text-muted-foreground uppercase font-medium">
                            Average
                          </div>
                          <div className="text-sm font-bold font-mono mt-0.5">
                            {route.avg_latency_ms.toFixed(1)} ms
                          </div>
                        </div>

                        <div className="bg-background/80 rounded-lg p-2 border border-border/40">
                          <div className="text-[10px] text-muted-foreground uppercase font-medium">
                            P50 (Median)
                          </div>
                          <div className="text-sm font-bold font-mono mt-0.5">
                            {route.p50_latency_ms !== null
                              ? `${route.p50_latency_ms.toFixed(1)} ms`
                              : '—'}
                          </div>
                        </div>

                        <div className="bg-background/80 rounded-lg p-2 border border-border/40">
                          <div className="text-[10px] text-muted-foreground uppercase font-medium">
                            P95 (Tail)
                          </div>
                          <div className="text-sm font-bold font-mono mt-0.5">
                            {route.p95_latency_ms !== null
                              ? `${route.p95_latency_ms.toFixed(1)} ms`
                              : '—'}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="text-sm text-muted-foreground py-8 text-center">
                  No latency records found for this time range.
                </div>
              )}

              {/* Cache advantage callout */}
              <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl flex items-start gap-3 mt-4">
                <Sparkles className="w-5 h-5 text-emerald-500 shrink-0 mt-0.5" />
                <div className="text-xs text-muted-foreground leading-relaxed">
                  <span className="font-semibold text-foreground">
                    Exact & Semantic cache hits
                  </span>{' '}
                  bypass model generation entirely, reducing latency by up to{' '}
                  <span className="text-emerald-500 font-bold">95-99%</span> and
                  saving upstream API quotas.
                </div>
              </div>
            </div>
          </Card>
        </motion.div>

        {/* Latency Over Time Trend */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <Card size="3">
            <Box mb="4">
              <Heading size="4">Latency Over Time</Heading>
              <Text size="2" color="gray">
                Average gateway request latency trends across the selected
                range.
              </Text>
            </Box>

            <Box className="h-[280px] flex items-end justify-between px-2 pb-4 gap-1 sm:gap-2">
              {latencySeries.length > 0 ? (
                latencySeries.map((d, i) => {
                  const height =
                    maxLatency > 0
                      ? (d.avg_total_latency_ms / maxLatency) * 100
                      : 0;
                  return (
                    <div
                      key={i}
                      className="w-full flex flex-col justify-end gap-2 group h-full min-w-0"
                    >
                      <motion.div
                        initial={{ height: 0 }}
                        animate={{ height: `${height}%` }}
                        transition={{ duration: 0.8, delay: 0.2 + i * 0.03 }}
                        className="bg-blue-500/30 hover:bg-blue-500 transition-colors rounded-t-sm w-full relative min-h-[2px]"
                      >
                        <div className="absolute -top-12 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity bg-foreground text-background text-xs py-1.5 px-2.5 rounded-lg z-20 whitespace-nowrap shadow-lg pointer-events-none">
                          <div className="font-bold">
                            {d.avg_total_latency_ms.toFixed(1)} ms
                          </div>
                          <div className="text-[10px] opacity-80">
                            {d.requests} requests
                          </div>
                        </div>
                      </motion.div>
                      <div className="text-center text-[10px] text-muted-foreground font-medium truncate px-0.5">
                        {formatTickLabel(d.time_label || d.timestamp)}
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="w-full h-full flex items-center justify-center text-muted-foreground">
                  No latency data available for this range
                </div>
              )}
            </Box>
          </Card>
        </motion.div>

        {/* Third Row: Requests Over Time and Cache Performance */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="grid gap-4 md:grid-cols-2 lg:grid-cols-7"
        >
          <Card className="md:col-span-2 lg:col-span-4" size="3">
            <Box mb="4">
              <Heading size="4">Requests Over Time</Heading>
              <Text size="2" color="gray">
                Request volume distribution for the selected time range.
              </Text>
            </Box>
            <Box className="h-[280px] flex items-end justify-between px-2 pb-4 gap-1 sm:gap-2">
              {timeSeries?.length > 0 ? (
                timeSeries.map((d, i) => {
                  const height =
                    maxRequests > 0 ? (d.requests / maxRequests) * 100 : 0;
                  return (
                    <div
                      key={i}
                      className="w-full flex flex-col justify-end gap-2 group h-full min-w-0"
                    >
                      <motion.div
                        initial={{ height: 0 }}
                        animate={{ height: `${height}%` }}
                        transition={{ duration: 0.8, delay: 0.3 + i * 0.03 }}
                        className="bg-primary/25 hover:bg-primary transition-colors rounded-t-sm w-full relative min-h-[2px]"
                      >
                        <div className="absolute -top-8 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity bg-foreground text-background text-xs py-1 px-2 rounded-lg z-20 whitespace-nowrap shadow-md pointer-events-none">
                          {d.requests} reqs
                        </div>
                      </motion.div>
                      <div className="text-center text-[10px] text-muted-foreground font-medium truncate px-0.5">
                        {formatTickLabel(d.date_label)}
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="w-full h-full flex items-center justify-center text-muted-foreground">
                  No request data available
                </div>
              )}
            </Box>
          </Card>

          <Card className="md:col-span-2 lg:col-span-3" size="3">
            <Box mb="4">
              <Heading size="4">Cache Performance</Heading>
              <Text size="2" color="gray">
                Hits by routing decision.
              </Text>
            </Box>
            <Box className="flex flex-col justify-center items-center h-[280px] pb-6">
              <div className="relative w-44 h-44 rounded-full border-8 border-primary/10 flex items-center justify-center">
                <svg className="absolute inset-0 w-full h-full -rotate-90">
                  <circle
                    cx="50%"
                    cy="50%"
                    r="46%"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="8%"
                    className="text-emerald-500"
                    strokeDasharray="289"
                    strokeDashoffset={
                      289 -
                      (289 *
                        Math.min(100, Math.max(0, kpis?.cache_hit_rate || 0))) /
                        100
                    }
                    strokeLinecap="round"
                  />
                </svg>
                <div className="text-center">
                  <div className="text-3xl font-bold">
                    {(cacheStats?.total || 0) - (cacheStats?.llm_calls || 0)}
                  </div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mt-1">
                    Total Hits
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-6 w-full px-4">
                <div className="flex items-center gap-2 text-xs">
                  <div className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
                  Semantic ({cacheStats?.qdrant_semantic || 0})
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <div className="w-2.5 h-2.5 rounded-full bg-blue-500" />
                  Qdrant Exact ({cacheStats?.qdrant_exact || 0})
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <div className="w-2.5 h-2.5 rounded-full bg-purple-500" />
                  Redis Exact ({cacheStats?.redis_exact || 0})
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <div className="w-2.5 h-2.5 rounded-full bg-gray-500" />
                  Miss / LLM ({cacheStats?.llm_calls || 0})
                </div>
              </div>
            </Box>
          </Card>
        </motion.div>

        {/* Fourth Row: Evaluations and Intents */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          className="grid gap-4 md:grid-cols-2"
        >
          <Card size="3">
            <Box mb="4">
              <Heading size="4" className="flex items-center gap-2">
                <BrainCircuit className="w-5 h-5 text-purple-500" />
                Judge Evaluations
              </Heading>
              <Text size="2" color="gray">
                Based on {evals?.evaluated_count || 0} evaluated requests
              </Text>
            </Box>
            <Box className="space-y-6">
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="font-medium">Avg Relevance Score</span>
                  <span className="text-emerald-500 font-bold">
                    {evals?.avg_relevance_score !== null &&
                    evals?.avg_relevance_score !== undefined
                      ? `${evals.avg_relevance_score.toFixed(2)} / 5`
                      : 'N/A'}
                  </span>
                </div>
                <div className="w-full bg-secondary rounded-full h-2">
                  <div
                    className="bg-emerald-500 h-2 rounded-full"
                    style={{
                      width: `${((evals?.avg_relevance_score || 0) / 5) * 100}%`,
                    }}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="font-medium">
                    Avg Entailment / Groundedness
                  </span>
                  <span className="text-blue-500 font-bold">
                    {evals?.avg_entailment_score !== null &&
                    evals?.avg_entailment_score !== undefined
                      ? `${evals.avg_entailment_score.toFixed(2)} / 5`
                      : 'N/A'}
                  </span>
                </div>
                <div className="w-full bg-secondary rounded-full h-2">
                  <div
                    className="bg-blue-500 h-2 rounded-full"
                    style={{
                      width: `${((evals?.avg_entailment_score || 0) / 5) * 100}%`,
                    }}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="font-medium">Avg Contradiction Rate</span>
                  <span className="text-rose-500 font-bold">
                    {evals?.avg_contradiction_score !== null &&
                    evals?.avg_contradiction_score !== undefined
                      ? `${(evals.avg_contradiction_score * 100).toFixed(1)}%`
                      : 'N/A'}
                  </span>
                </div>
                <div className="w-full bg-secondary rounded-full h-2">
                  <div
                    className="bg-rose-500 h-2 rounded-full"
                    style={{
                      width: `${Math.min(100, Math.max(0, (evals?.avg_contradiction_score || 0) * 100))}%`,
                    }}
                  />
                </div>
              </div>
              <div className="space-y-2 pt-2 border-t border-border/50">
                <div className="flex justify-between items-center text-sm">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 text-amber-500" />
                    <span className="font-medium">False Positive Rate</span>
                  </div>
                  <span className="text-amber-500 font-bold">
                    {evals?.false_positive_rate !== undefined &&
                    evals?.false_positive_rate !== null
                      ? Number(evals.false_positive_rate).toFixed(1)
                      : '0.0'}
                    %
                  </span>
                </div>
                <p className="text-xs text-muted-foreground">
                  Semantic cache hits that the Judge determined were incorrect.
                </p>
              </div>
            </Box>
          </Card>

          <Card size="3">
            <Box mb="4">
              <Heading size="4">Top Intents</Heading>
              <Text size="2" color="gray">
                Most frequent classified semantic intents.
              </Text>
            </Box>
            <Box>
              <div className="space-y-4">
                {intents?.length > 0 ? (
                  intents.slice(0, 5).map((intent, idx) => {
                    const maxIntentCount = Math.max(
                      ...intents.map((i) => i.count)
                    );
                    return (
                      <div key={idx} className="space-y-1">
                        <div className="flex justify-between text-sm">
                          <span className="font-medium capitalize">
                            {intent.intent.replace(/_/g, ' ')}
                          </span>
                          <span className="text-muted-foreground">
                            {intent.count}
                          </span>
                        </div>
                        <div className="w-full bg-secondary rounded-full h-1.5">
                          <div
                            className="bg-primary h-1.5 rounded-full"
                            style={{
                              width: `${(intent.count / maxIntentCount) * 100}%`,
                            }}
                          />
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="text-sm text-muted-foreground py-4 text-center">
                    No intent data available.
                  </div>
                )}
              </div>
            </Box>
          </Card>
        </motion.div>
      </div>
    </div>
  );
}
