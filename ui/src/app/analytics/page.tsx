'use client';

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Card, Text, Heading, Flex, Box } from '@radix-ui/themes';
import {
  BarChart3,
  Zap,
  Clock,
  Database,
  BrainCircuit,
  AlertCircle,
} from 'lucide-react';

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
    avg_instruction_score: number;
    false_positive_rate: number;
  };
}

export default function AnalyticsPage() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        const end = new Date();
        const start = new Date();
        start.setDate(end.getDate() - 7);
        const params = new URLSearchParams({
          start_time: start.toISOString(),
          end_time: end.toISOString(),
        });

        const res = await fetch(
          `http://localhost:8000/api/v1/analytics/dashboard?${params.toString()}`
        );
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const result = await res.json();
        setData(result.response || result); // Handle both BaseAPIResponse wrapper or direct
      } catch (e: unknown) {
        if (e instanceof Error) {
          setError(e.message);
        } else {
          setError('An unknown error occurred');
        }
      } finally {
        setLoading(false);
      }
    };

    fetchAnalytics();
  }, []);

  const container = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: { staggerChildren: 0.1 },
    },
  };

  const item = {
    hidden: { opacity: 0, y: 20 },
    show: { opacity: 1, y: 0 },
  };

  if (loading) {
    return (
      <div className="p-8 flex justify-center items-center h-full">
        <div className="animate-pulse text-muted-foreground">
          Loading analytics...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 text-red-500">Error loading analytics: {error}</div>
    );
  }

  if (!data) return null;

  const kpis = data.kpis;
  const timeSeries = data.time_series;
  const cacheStats = data.cache_performance;
  const intents = data.top_intents;
  const evals = data.evaluations;

  // Find max requests for chart scaling
  const maxRequests = timeSeries?.length
    ? Math.max(...timeSeries.map((d) => d.requests))
    : 0;

  return (
    <div className="flex-1 overflow-y-auto p-8 bg-background">
      <div className="max-w-6xl mx-auto space-y-8">
        <div>
          <Heading size="8" mb="2">
            Analytics Dashboard
          </Heading>
          <Text color="gray">
            Monitor caching performance, latency, and LLM provider usage.
          </Text>
        </div>

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
                  {kpis?.total_requests.toLocaleString() || 0}
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  Total gateway requests processed
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
                  {kpis?.cache_hit_rate
                    ? (kpis.cache_hit_rate * 100).toFixed(1)
                    : 0}
                  %
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  {cacheStats?.total - cacheStats?.llm_calls} hits /{' '}
                  {cacheStats?.llm_calls} misses
                </Text>
              </Box>
            </Card>
          </motion.div>

          <motion.div variants={item}>
            <Card size="2">
              <Flex align="center" justify="between" mb="2">
                <Text size="2" weight="medium">
                  Avg. Latency
                </Text>
                <Clock className="h-4 w-4 text-blue-500" />
              </Flex>
              <Box>
                <Text size="7" weight="bold">
                  {kpis?.avg_latency_ms ? Math.round(kpis.avg_latency_ms) : 0}ms
                </Text>
                <Text as="p" size="1" color="gray" mt="1">
                  Average across all requests
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

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="grid gap-4 md:grid-cols-2 lg:grid-cols-7"
        >
          <Card className="md:col-span-2 lg:col-span-4" size="3">
            <Box mb="4">
              <Heading size="4">Requests over time</Heading>
              <Text size="2" color="gray">
                Daily request volume for the current range.
              </Text>
            </Box>
            <Box className="h-[300px] flex items-end justify-between px-2 pb-4 gap-1 sm:gap-2">
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
                        transition={{ duration: 1, delay: 0.5 + i * 0.05 }}
                        className="bg-primary/20 hover:bg-primary transition-colors rounded-t-sm w-full relative min-h-[1px]"
                      >
                        <div className="absolute -top-8 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity bg-foreground text-background text-xs py-1 px-2 rounded z-10 whitespace-nowrap">
                          {d.requests} reqs
                        </div>
                      </motion.div>
                      <div className="text-center text-[10px] text-muted-foreground font-medium truncate px-1">
                        {new Date(d.date_label).toLocaleDateString(undefined, {
                          month: 'short',
                          day: 'numeric',
                        })}
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="w-full h-full flex items-center justify-center text-muted-foreground">
                  No data available
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
            <Box className="flex flex-col justify-center items-center h-[300px] pb-8">
              <div className="relative w-48 h-48 rounded-full border-8 border-primary/10 flex items-center justify-center">
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
                    strokeDashoffset={289 - 289 * (kpis?.cache_hit_rate || 0)}
                    strokeLinecap="round"
                  />
                </svg>
                <div className="text-center">
                  <div className="text-3xl font-bold">
                    {cacheStats?.total - cacheStats?.llm_calls || 0}
                  </div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mt-1">
                    Total Hits
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4 mt-8 w-full px-4">
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

        {/* Third Row: Evaluations and Intents */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          className="grid gap-4 md:grid-cols-2"
        >
          <Card size="3">
            <Box mb="4">
              <Heading size="4" className="flex items-center gap-2">
                <BrainCircuit className="w-5 h-5 text-purple-500" />
                LLM-as-a-Judge Evaluations
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
                    {evals?.avg_relevance_score?.toFixed(2) || 'N/A'} / 5
                  </span>
                </div>
                <div className="w-full bg-secondary rounded-full h-2">
                  <div
                    className="bg-emerald-500 h-2 rounded-full"
                    style={{
                      width: `${((evals?.avg_relevance_score || 0) / 5) * 100}%`,
                    }}
                  ></div>
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="font-medium">Avg Instruction Following</span>
                  <span className="text-blue-500 font-bold">
                    {evals?.avg_instruction_score?.toFixed(2) || 'N/A'} / 5
                  </span>
                </div>
                <div className="w-full bg-secondary rounded-full h-2">
                  <div
                    className="bg-blue-500 h-2 rounded-full"
                    style={{
                      width: `${((evals?.avg_instruction_score || 0) / 5) * 100}%`,
                    }}
                  ></div>
                </div>
              </div>
              <div className="space-y-2 pt-2 border-t border-border/50">
                <div className="flex justify-between items-center text-sm">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 text-amber-500" />
                    <span className="font-medium">False Positive Rate</span>
                  </div>
                  <span className="text-amber-500 font-bold">
                    {((evals?.false_positive_rate || 0) * 100).toFixed(1)}%
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
                          ></div>
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
