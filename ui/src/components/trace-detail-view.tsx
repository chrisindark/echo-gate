'use client';

import { Box, Card, Flex, Heading, Text } from '@radix-ui/themes';
import {
  AlertCircle,
  AlertTriangle,
  BrainCircuit,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  Gauge,
  Layers,
  Sparkles,
} from 'lucide-react';
import { useState } from 'react';
import { cn } from '@/lib/utils';

export interface LatencyBreakdown {
  redis_exact_ms?: number | null;
  qdrant_exact_ms?: number | null;
  intent_classify_ms?: number | null;
  embedding_gen_ms?: number | null;
  qdrant_dense_ms?: number | null;
  qdrant_rrf_ms?: number | null;
  rerank_ms?: number | null;
  cache_lookup_total_ms?: number | null;
  provider_ms?: number | null;
  cache_write_ms?: number | null;
  total_ms?: number | null;
}

export interface MetadataComparison {
  judged?: boolean;
  reason?: string;
  mismatch?: boolean;
  overlap?: number;
  request?: string | null;
  candidate?: string | null;
}

export interface ScoreBreakdownDetails {
  metadata_mismatches?: string[];
  metadata_comparisons?: Record<string, MetadataComparison>;
  entity_conflict?: boolean;
  entity_overlap?: number;
  system_prompt_jaccard?: number;
  temperature_delta?: number;
  instruction_score?: number;
}

export interface ScoreBreakdown {
  scoring_mode?: string;
  base_score?: number;
  final_score?: number;
  factors?: Record<string, number>;
  details?: ScoreBreakdownDetails;
}

export interface TraceLog {
  id: number;
  created_at: string;
  updated_at: string;
  query_text: string;
  response_text?: string | null;
  routing_decision: string;
  provider?: string | null;
  model?: string | null;
  point_id?: string | null;
  exact_hash?: string | null;
  intent?: string | null;
  core_operation?: string | null;
  core_subject?: string | null;
  subject_modifier?: string | null;
  action_modifier?: string | null;
  rerank_score?: number | null;
  score_breakdown?: ScoreBreakdown | null;
  latency_ms?: number | null;
  provider_latency_ms?: number | null;
  cache_lookup_latency_ms?: number | null;
  latency_breakdown?: LatencyBreakdown | Record<string, unknown> | null;
  user_id?: string | null;
  tenant_id?: string | null;
  session_id?: string | null;
  conversation_id?: string | null;
  evaluation_status: string;
  llm_relevance_score?: number | null;
  llm_contradiction_score?: number | null;
  llm_entailment_score?: number | null;
  is_false_positive?: boolean | null;
  error_message?: string | null;
}

interface TraceDetailViewProps {
  trace: TraceLog;
  compact?: boolean;
}

export function TraceDetailView({
  trace,
  compact = false,
}: TraceDetailViewProps) {
  const [copiedQuery, setCopiedQuery] = useState(false);
  const [copiedResponse, setCopiedResponse] = useState(false);
  const [copiedHash, setCopiedHash] = useState(false);

  const copyToClipboard = (
    text: string,
    type: 'query' | 'response' | 'hash'
  ) => {
    navigator.clipboard.writeText(text);
    if (type === 'query') {
      setCopiedQuery(true);
      setTimeout(() => setCopiedQuery(false), 2000);
    } else if (type === 'response') {
      setCopiedResponse(true);
      setTimeout(() => setCopiedResponse(false), 2000);
    } else {
      setCopiedHash(true);
      setTimeout(() => setCopiedHash(false), 2000);
    }
  };

  const breakdown: LatencyBreakdown =
    (trace.latency_breakdown as LatencyBreakdown) || {};

  const totalDuration =
    trace.latency_ms ??
    breakdown.total_ms ??
    (trace.cache_lookup_latency_ms || 0) + (trace.provider_latency_ms || 0);

  const stages = [
    {
      key: 'redis_exact_ms',
      name: 'Redis Exact Check',
      category: 'Cache',
      val: breakdown.redis_exact_ms,
      desc: 'In-memory exact key hash check',
      color: 'bg-emerald-500',
    },
    {
      key: 'qdrant_exact_ms',
      name: 'Qdrant Exact Check',
      category: 'Cache',
      val: breakdown.qdrant_exact_ms,
      desc: 'Vector payload hash check',
      color: 'bg-blue-500',
    },
    {
      key: 'intent_classify_ms',
      name: 'Intent Classification',
      category: 'Cache',
      val: breakdown.intent_classify_ms,
      desc: 'Semantic prompt classification',
      color: 'bg-purple-500',
    },
    {
      key: 'embedding_gen_ms',
      name: 'Embedding Generation',
      category: 'Cache',
      val: breakdown.embedding_gen_ms,
      desc: 'Dense embedding inference',
      color: 'bg-indigo-500',
    },
    {
      key: 'qdrant_dense_ms',
      name: 'Dense Vector Search',
      category: 'Cache',
      val: breakdown.qdrant_dense_ms,
      desc: 'Cosine similarity vector lookup',
      color: 'bg-cyan-500',
    },
    {
      key: 'qdrant_rrf_ms',
      name: 'RRF Fusion Search',
      category: 'Cache',
      val: breakdown.qdrant_rrf_ms,
      desc: 'Reciprocal rank fusion ranker',
      color: 'bg-sky-500',
    },
    {
      key: 'rerank_ms',
      name: 'Cross-Encoder Rerank',
      category: 'Cache',
      val: breakdown.rerank_ms,
      desc: 'Neural cross-encoder scoring',
      color: 'bg-violet-500',
    },
    {
      key: 'cache_lookup_total_ms',
      name: 'Total Cache Lookup',
      category: 'Total Cache',
      val:
        breakdown.cache_lookup_total_ms ??
        (trace.cache_lookup_latency_ms !== null
          ? trace.cache_lookup_latency_ms
          : null),
      desc: 'Complete cache evaluation duration',
      color: 'bg-teal-500',
      isSummary: true,
    },
    {
      key: 'provider_ms',
      name: 'LLM Provider Call',
      category: 'Execution',
      val:
        breakdown.provider_ms ??
        (trace.provider_latency_ms !== null ? trace.provider_latency_ms : null),
      desc: 'External LLM model inference on miss',
      color: 'bg-amber-500',
    },
    {
      key: 'cache_write_ms',
      name: 'Cache Storage Write',
      category: 'Execution',
      val: breakdown.cache_write_ms,
      desc: 'Entry persistence to Qdrant/Redis',
      color: 'bg-rose-500',
    },
  ];

  // Active individual stages (excluding summary totals)
  const activeStages = stages.filter(
    (s) => !s.isSummary && s.val !== null && s.val !== undefined && s.val > 0
  );

  // Compute bottleneck
  const slowestStage =
    activeStages.length > 0
      ? [...activeStages].sort((a, b) => (b.val || 0) - (a.val || 0))[0]
      : null;

  const slowestPercentage =
    slowestStage && totalDuration && totalDuration > 0
      ? Math.round(((slowestStage.val || 0) / totalDuration) * 100)
      : 0;

  const routingColor =
    trace.routing_decision === 'REDIS_EXACT'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-500'
      : trace.routing_decision === 'QDRANT_EXACT'
        ? 'border-blue-500/40 bg-blue-500/10 text-blue-500'
        : trace.routing_decision === 'QDRANT_SEMANTIC'
          ? 'border-purple-500/40 bg-purple-500/10 text-purple-500'
          : 'border-amber-500/40 bg-amber-500/10 text-amber-500';

  const breakdownDetails = trace.score_breakdown?.details;
  const factorEntries = Object.entries(trace.score_breakdown?.factors ?? {});
  const metadataComparisons = Object.entries(
    breakdownDetails?.metadata_comparisons ?? {}
  );
  const metadataMismatches = breakdownDetails?.metadata_mismatches ?? [];

  const factorTone = (value: number) =>
    value < 0.999
      ? 'border-red-500/30 bg-red-500/10 text-red-500'
      : value > 1.001
        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
        : 'border-border/60 bg-muted/40 text-muted-foreground';

  const formatFactorName = (name: string) =>
    name
      .split('_')
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ');

  const hasScoreBreakdown = Boolean(trace.score_breakdown);
  const baseScore = trace.score_breakdown?.base_score;
  const finalScore = trace.score_breakdown?.final_score;
  const scoreDelta =
    baseScore !== undefined && finalScore !== undefined
      ? parseFloat((finalScore - baseScore).toFixed(6))
      : null;

  return (
    <div className={cn('space-y-6', compact ? 'p-1' : 'p-2')}>
      {/* Top Banner / Error Banner if failed */}
      {trace.error_message && (
        <div className="p-4 rounded-xl border border-red-500/30 bg-red-500/10 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="text-sm font-semibold text-red-500">
              Request Execution Error
            </div>
            <div className="text-xs text-red-400 font-mono break-all">
              {trace.error_message}
            </div>
          </div>
        </div>
      )}

      {/* False Positive Banner */}
      {trace.is_false_positive && (
        <div className="p-4 rounded-xl border border-amber-500/30 bg-amber-500/10 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="text-sm font-semibold text-amber-500">
              Semantic Cache False Positive
            </div>
            <div className="text-xs text-amber-400">
              The LLM-as-a-Judge determined that this cached response did not
              properly answer the user prompt.
            </div>
          </div>
        </div>
      )}

      {/* Meta Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="p-3 bg-card border border-border/60 rounded-xl">
          <div className="text-[11px] text-muted-foreground uppercase font-medium">
            Routing Decision
          </div>
          <div className="mt-1">
            <span
              className={cn(
                'text-xs font-bold px-2 py-0.5 rounded-md border inline-block',
                routingColor
              )}
            >
              {trace.routing_decision}
            </span>
          </div>
        </div>

        <div className="p-3 bg-card border border-border/60 rounded-xl">
          <div className="text-[11px] text-muted-foreground uppercase font-medium">
            Total Duration
          </div>
          <div className="text-sm font-bold font-mono mt-1 text-foreground flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-primary" />
            {totalDuration !== null ? `${Math.round(totalDuration)} ms` : '—'}
          </div>
        </div>

        <div className="p-3 bg-card border border-border/60 rounded-xl">
          <div className="text-[11px] text-muted-foreground uppercase font-medium">
            Provider / Model
          </div>
          <div className="text-xs font-semibold truncate mt-1 text-foreground">
            {trace.provider || 'unknown'} / {trace.model || 'default'}
          </div>
        </div>

        <div className="p-3 bg-card border border-border/60 rounded-xl">
          <div className="text-[11px] text-muted-foreground uppercase font-medium">
            Timestamp
          </div>
          <div className="text-xs font-medium text-muted-foreground truncate mt-1">
            {new Date(trace.created_at).toLocaleString()}
          </div>
        </div>
      </div>

      {/* Latency Waterfall & Diagnosis Card */}
      <Card size="3" className="border-border/80">
        <Box mb="4">
          <Flex align="center" justify="between">
            <div>
              <Heading size="4" className="flex items-center gap-2">
                <Gauge className="w-5 h-5 text-primary" />
                Latency Waterfall & Diagnostics
              </Heading>
              <Text size="2" color="gray">
                Stage-by-stage timing of gateway routing, vector scoring, and
                inference.
              </Text>
            </div>
            <div className="text-right">
              <span className="text-lg font-bold font-mono text-primary">
                {totalDuration ? `${Math.round(totalDuration)} ms` : '—'}
              </span>
              <div className="text-[10px] text-muted-foreground uppercase">
                End-to-End Latency
              </div>
            </div>
          </Flex>
        </Box>

        {/* Bottleneck Callout */}
        {slowestStage && (
          <div className="mb-5 p-3.5 rounded-xl border border-primary/20 bg-primary/5 flex items-start gap-3">
            <Sparkles className="w-4 h-4 text-primary shrink-0 mt-0.5" />
            <div className="text-xs text-muted-foreground leading-relaxed">
              <span className="font-semibold text-foreground">
                Performance Diagnosis:
              </span>{' '}
              <span className="font-medium text-foreground">
                {slowestStage.name}
              </span>{' '}
              was the primary bottleneck, taking{' '}
              <span className="font-mono font-bold text-primary">
                {slowestStage.val?.toFixed(1)} ms
              </span>{' '}
              ({slowestPercentage}% of total latency).
              {slowestStage.key === 'provider_ms' && (
                <span className="block mt-0.5 text-emerald-500 font-medium">
                  → A cache hit on this query would reduce latency by ~
                  {Math.round(slowestStage.val || 0)} ms.
                </span>
              )}
              {slowestStage.key === 'rerank_ms' &&
                (slowestStage.val || 0) > 100 && (
                  <span className="block mt-0.5 text-amber-500 font-medium">
                    → Cross-encoder reranker latency is noticeable. Consider
                    fine-tuning candidate counts.
                  </span>
                )}
            </div>
          </div>
        )}

        {/* Waterfall Bars */}
        <div className="space-y-3">
          {stages.map((stage) => {
            const val = stage.val;
            const hasVal = val !== null && val !== undefined && val > 0;
            const pct =
              hasVal && totalDuration && totalDuration > 0
                ? Math.min(100, Math.max(2, (val! / totalDuration) * 100))
                : 0;

            return (
              <div
                key={stage.key}
                className={cn(
                  'space-y-1',
                  stage.isSummary &&
                    'pt-2 mt-2 border-t border-border/50 font-medium'
                )}
              >
                <div className="flex justify-between items-center text-xs">
                  <div className="flex items-center gap-2">
                    <div className={cn('w-2 h-2 rounded-full', stage.color)} />
                    <span
                      className={cn(
                        'font-medium',
                        stage.isSummary
                          ? 'text-foreground'
                          : 'text-foreground/90'
                      )}
                    >
                      {stage.name}
                    </span>
                    <span className="text-[11px] text-muted-foreground hidden sm:inline">
                      — {stage.desc}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {hasVal &&
                      totalDuration &&
                      totalDuration > 0 &&
                      !stage.isSummary && (
                        <span className="text-[11px] text-muted-foreground font-mono">
                          {Math.round((val! / totalDuration) * 100)}%
                        </span>
                      )}
                    <span className="font-mono font-semibold text-foreground min-w-[65px] text-right">
                      {hasVal ? `${val!.toFixed(1)} ms` : '—'}
                    </span>
                  </div>
                </div>

                <div className="w-full bg-secondary/80 rounded-full h-1.5 overflow-hidden">
                  <div
                    className={cn(
                      'h-1.5 rounded-full transition-all',
                      stage.color
                    )}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      {/* Query and Response Card */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Query Text */}
        <Card size="3" className="flex flex-col justify-between">
          <Box mb="3">
            <Flex align="center" justify="between">
              <Heading size="3">Query Prompt</Heading>
              <button
                onClick={() => copyToClipboard(trace.query_text, 'query')}
                className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded-md transition-colors text-xs flex items-center gap-1"
                title="Copy prompt"
              >
                {copiedQuery ? (
                  <Check className="w-3.5 h-3.5 text-emerald-500" />
                ) : (
                  <Copy className="w-3.5 h-3.5" />
                )}
                {copiedQuery ? 'Copied' : 'Copy'}
              </button>
            </Flex>
          </Box>
          <div className="p-3.5 rounded-xl bg-muted/40 border border-border/60 text-xs font-mono text-foreground whitespace-pre-wrap max-h-72 overflow-y-auto leading-relaxed">
            {trace.query_text || 'No query text recorded'}
          </div>
        </Card>

        {/* Response Text */}
        <Card size="3" className="flex flex-col justify-between">
          <Box mb="3">
            <Flex align="center" justify="between">
              <Heading size="3">Gateway Response</Heading>
              {trace.response_text && (
                <button
                  onClick={() =>
                    copyToClipboard(trace.response_text || '', 'response')
                  }
                  className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded-md transition-colors text-xs flex items-center gap-1"
                  title="Copy response"
                >
                  {copiedResponse ? (
                    <Check className="w-3.5 h-3.5 text-emerald-500" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                  {copiedResponse ? 'Copied' : 'Copy'}
                </button>
              )}
            </Flex>
          </Box>
          <div className="p-3.5 rounded-xl bg-muted/40 border border-border/60 text-xs font-mono text-foreground whitespace-pre-wrap max-h-72 overflow-y-auto leading-relaxed">
            {trace.response_text || 'No response recorded or streaming/skipped'}
          </div>
        </Card>
      </div>

      {/* Semantic Intent & LLM Judge Evaluations */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Semantic Cache Details */}
        <Card size="3">
          <Box mb="4">
            <Heading size="3" className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-purple-500" />
              Semantic Intent & Cache Metadata
            </Heading>
          </Box>
          <div className="space-y-3 text-xs">
            <div className="flex justify-between py-1.5 border-b border-border/40">
              <span className="text-muted-foreground">Classified Intent</span>
              <span className="font-semibold text-foreground">
                {trace.intent || '—'}
              </span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-border/40">
              <span className="text-muted-foreground">Core Operation</span>
              <span className="font-medium text-foreground">
                {trace.core_operation || '—'}
              </span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-border/40">
              <span className="text-muted-foreground">Core Subject</span>
              <span className="font-medium text-foreground">
                {trace.core_subject || '—'}
              </span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-border/40">
              <span className="text-muted-foreground">Modifiers</span>
              <span className="font-medium text-foreground">
                {[trace.subject_modifier, trace.action_modifier]
                  .filter(Boolean)
                  .join(', ') || '—'}
              </span>
            </div>
            <div className="flex justify-between py-1.5 border-b border-border/40">
              <span className="text-muted-foreground">Rerank Score</span>
              <span className="font-mono font-bold text-foreground">
                {trace.rerank_score !== null && trace.rerank_score !== undefined
                  ? trace.rerank_score.toFixed(4)
                  : '—'}
              </span>
            </div>
            <div className="flex justify-between items-center py-1.5 border-b border-border/40">
              <span className="text-muted-foreground">Exact Hash</span>
              <span className="font-mono text-[11px] text-muted-foreground truncate max-w-[200px] flex items-center gap-1">
                {trace.exact_hash || '—'}
                {trace.exact_hash && (
                  <button
                    onClick={() =>
                      copyToClipboard(trace.exact_hash || '', 'hash')
                    }
                    className="hover:text-foreground"
                  >
                    {copiedHash ? (
                      <Check className="w-3 h-3 text-emerald-500" />
                    ) : (
                      <Copy className="w-3 h-3" />
                    )}
                  </button>
                )}
              </span>
            </div>
            <div className="flex justify-between py-1.5">
              <span className="text-muted-foreground">Qdrant Point ID</span>
              <span className="font-mono text-[11px] text-muted-foreground truncate max-w-[200px]">
                {trace.point_id || '—'}
              </span>
            </div>
          </div>
        </Card>

        {/* LLM-as-a-Judge Evaluation */}
        <Card size="3">
          <Box mb="4">
            <Heading size="3" className="flex items-center gap-2">
              <BrainCircuit className="w-4 h-4 text-emerald-500" />
              LLM-as-a-Judge Evaluation
            </Heading>
          </Box>
          <div className="space-y-4">
            <div className="flex justify-between items-center text-xs pb-2 border-b border-border/40">
              <span className="text-muted-foreground">Status</span>
              <span
                className={cn(
                  'px-2 py-0.5 rounded-md font-semibold text-[11px]',
                  trace.evaluation_status === 'EVALUATED'
                    ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                    : trace.evaluation_status === 'PENDING'
                      ? 'bg-amber-500/10 text-amber-500 border border-amber-500/20'
                      : 'bg-muted text-muted-foreground'
                )}
              >
                {trace.evaluation_status}
              </span>
            </div>

            <div className="space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">Relevance Score</span>
                <span className="font-bold text-foreground">
                  {trace.llm_relevance_score !== null &&
                  trace.llm_relevance_score !== undefined
                    ? `${trace.llm_relevance_score.toFixed(2)} / 5`
                    : '—'}
                </span>
              </div>
              <div className="w-full bg-secondary rounded-full h-1.5">
                <div
                  className="bg-emerald-500 h-1.5 rounded-full"
                  style={{
                    width: `${((trace.llm_relevance_score || 0) / 5) * 100}%`,
                  }}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">
                  Entailment / Groundedness
                </span>
                <span className="font-bold text-foreground">
                  {trace.llm_entailment_score !== null &&
                  trace.llm_entailment_score !== undefined
                    ? `${trace.llm_entailment_score.toFixed(2)} / 5`
                    : '—'}
                </span>
              </div>
              <div className="w-full bg-secondary rounded-full h-1.5">
                <div
                  className="bg-blue-500 h-1.5 rounded-full"
                  style={{
                    width: `${((trace.llm_entailment_score || 0) / 5) * 100}%`,
                  }}
                />
              </div>
            </div>

            <div className="flex justify-between items-center text-xs">
              <span className="text-muted-foreground">Contradiction Score</span>
              {trace.llm_contradiction_score !== null &&
              trace.llm_contradiction_score !== undefined ? (
                <span
                  className={cn(
                    'font-semibold flex items-center gap-1 text-xs',
                    Number(trace.llm_contradiction_score) >= 1
                      ? 'text-red-500'
                      : 'text-emerald-500'
                  )}
                >
                  {Number(trace.llm_contradiction_score) >= 1 ? (
                    <>
                      <AlertTriangle className="w-3.5 h-3.5" />1 (Contradiction
                      Detected)
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="w-3.5 h-3.5" />0 (No
                      Contradiction)
                    </>
                  )}
                </span>
              ) : (
                <span className="font-bold text-muted-foreground">—</span>
              )}
            </div>

            <div className="pt-2 border-t border-border/40 flex justify-between items-center text-xs">
              <span className="text-muted-foreground">False Positive</span>
              <span
                className={cn(
                  'font-semibold flex items-center gap-1',
                  trace.is_false_positive ? 'text-red-500' : 'text-emerald-500'
                )}
              >
                {trace.is_false_positive ? (
                  <>
                    <AlertTriangle className="w-3.5 h-3.5" />
                    True (Incorrect Cache Hit)
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    False (Accurate Cache Hit)
                  </>
                )}
              </span>
            </div>
          </div>
        </Card>
      </div>

      {/* Reranker Score Breakdown */}
      {hasScoreBreakdown && (
        <Card size="3">
          <Box mb="4">
            <Flex align="start" justify="between" gap="3" wrap="wrap">
              <div>
                <Heading size="3" className="flex items-center gap-2">
                  <Gauge className="w-4 h-4 text-primary" />
                  Reranker Score Breakdown
                </Heading>
                <Text size="2" color="gray">
                  How the composite rerank score was assembled from the
                  cross-encoder base score and the bounded similarity factors.
                </Text>
              </div>
              {trace.score_breakdown?.scoring_mode && (
                <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md border border-border/60 bg-muted/40 text-muted-foreground uppercase tracking-wide">
                  {trace.score_breakdown.scoring_mode}
                </span>
              )}
            </Flex>
          </Box>

          {/* Base vs Final score */}
          <div className="grid grid-cols-3 gap-3 mb-5">
            <div className="p-3 rounded-xl bg-muted/40 border border-border/60">
              <div className="text-[11px] text-muted-foreground uppercase font-medium">
                Base Score
              </div>
              <div className="text-sm font-bold font-mono mt-1 text-foreground">
                {baseScore !== undefined ? baseScore.toFixed(6) : '—'}
              </div>
            </div>
            <div className="p-3 rounded-xl bg-muted/40 border border-border/60">
              <div className="text-[11px] text-muted-foreground uppercase font-medium">
                Final Score
              </div>
              <div className="text-sm font-bold font-mono mt-1 text-foreground">
                {finalScore !== undefined ? finalScore.toFixed(6) : '—'}
              </div>
            </div>
            <div className="p-3 rounded-xl bg-muted/40 border border-border/60">
              <div className="text-[11px] text-muted-foreground uppercase font-medium">
                Bounded Delta
              </div>
              {scoreDelta !== null ? (
                <div
                  className={cn(
                    'text-sm font-bold font-mono mt-1',
                    scoreDelta < 0
                      ? 'text-red-500'
                      : scoreDelta > 0
                        ? 'text-emerald-500'
                        : 'text-muted-foreground'
                  )}
                >
                  {scoreDelta > 0 ? '+' : ''}
                  {scoreDelta.toFixed(6)}
                </div>
              ) : (
                <div className="text-sm font-bold font-mono mt-1 text-muted-foreground">
                  —
                </div>
              )}
            </div>
          </div>

          {/* Factors */}
          {factorEntries.length > 0 && (
            <div className="mb-5">
              <div className="text-[11px] text-muted-foreground uppercase font-semibold mb-2">
                Bounded Factors
              </div>
              <div className="flex flex-wrap gap-2">
                {factorEntries.map(([name, value]) => (
                  <div
                    key={name}
                    className={cn(
                      'flex items-center gap-2 px-2.5 py-1 rounded-lg border text-xs',
                      factorTone(value)
                    )}
                  >
                    <span className="font-medium">
                      {formatFactorName(name)}
                    </span>
                    <span className="font-mono font-bold">
                      ×{value.toFixed(3)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Metadata comparisons */}
          {metadataComparisons.length > 0 && (
            <div className="mb-4">
              <Flex align="center" justify="between" className="mb-2">
                <div className="text-[11px] text-muted-foreground uppercase font-semibold">
                  Metadata Comparisons
                </div>
                {metadataMismatches.length > 0 && (
                  <span className="text-[11px] font-semibold text-red-500">
                    {metadataMismatches.length} mismatch
                    {metadataMismatches.length > 1 ? 'es' : ''}
                  </span>
                )}
              </Flex>
              <div className="overflow-x-auto rounded-xl border border-border/60">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-border/60 bg-muted/30 text-muted-foreground uppercase tracking-wider font-semibold text-[10px]">
                      <th className="py-2 px-3">Field</th>
                      <th className="py-2 px-3">Request</th>
                      <th className="py-2 px-3">Candidate</th>
                      <th className="py-2 px-3 text-right">Overlap</th>
                      <th className="py-2 px-3 text-right">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40">
                    {metadataComparisons.map(([field, comparison]) => (
                      <tr key={field}>
                        <td className="py-2 px-3 font-medium text-foreground whitespace-nowrap">
                          {formatFactorName(field)}
                        </td>
                        <td className="py-2 px-3 font-mono text-[11px] text-muted-foreground max-w-[180px] truncate">
                          {comparison.request ?? '—'}
                        </td>
                        <td className="py-2 px-3 font-mono text-[11px] text-muted-foreground max-w-[180px] truncate">
                          {comparison.candidate ?? '—'}
                        </td>
                        <td className="py-2 px-3 text-right font-mono text-foreground">
                          {comparison.judged && comparison.overlap !== undefined
                            ? comparison.overlap.toFixed(4)
                            : '—'}
                        </td>
                        <td className="py-2 px-3 text-right whitespace-nowrap">
                          {comparison.judged ? (
                            comparison.mismatch ? (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-500/10 text-red-500 border border-red-500/20 text-[10px] font-semibold">
                                <AlertTriangle className="w-3 h-3" /> Mismatch
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 text-[10px] font-semibold">
                                <Check className="w-3 h-3" /> Match
                              </span>
                            )
                          ) : (
                            <span className="text-[10px] text-muted-foreground">
                              skipped (
                              {comparison.reason === 'request_empty'
                                ? 'request empty'
                                : comparison.reason === 'candidate_empty'
                                  ? 'candidate empty'
                                  : comparison.reason || 'not judged'}
                              )
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Other signals */}
          {breakdownDetails && (
            <div className="flex flex-wrap gap-x-6 gap-y-2 pt-2 text-xs border-t border-border/40">
              <div className="flex items-center gap-1.5">
                <span className="text-muted-foreground">Entity Conflict</span>
                <span
                  className={cn(
                    'font-semibold',
                    breakdownDetails.entity_conflict
                      ? 'text-red-500'
                      : 'text-emerald-500'
                  )}
                >
                  {breakdownDetails.entity_conflict ? 'Yes' : 'No'}
                </span>
              </div>
              {breakdownDetails.entity_overlap !== undefined && (
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">Entity Overlap</span>
                  <span className="font-mono font-semibold text-foreground">
                    {breakdownDetails.entity_overlap.toFixed(4)}
                  </span>
                </div>
              )}
              {breakdownDetails.system_prompt_jaccard !== undefined && (
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">
                    System Prompt Jaccard
                  </span>
                  <span className="font-mono font-semibold text-foreground">
                    {breakdownDetails.system_prompt_jaccard.toFixed(4)}
                  </span>
                </div>
              )}
              {breakdownDetails.temperature_delta !== undefined && (
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">Temperature Δ</span>
                  <span className="font-mono font-semibold text-foreground">
                    {breakdownDetails.temperature_delta.toFixed(4)}
                  </span>
                </div>
              )}
              {breakdownDetails.instruction_score !== undefined && (
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">
                    Instruction Score
                  </span>
                  <span className="font-mono font-semibold text-foreground">
                    {breakdownDetails.instruction_score.toFixed(4)}
                  </span>
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      {/* Identifiers & Context Table */}
      <Card size="2">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
          <div>
            <div className="text-muted-foreground text-[11px] uppercase">
              Tenant ID
            </div>
            <div className="font-mono mt-0.5 text-foreground truncate">
              {trace.tenant_id || 'default'}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground text-[11px] uppercase">
              User ID
            </div>
            <div className="font-mono mt-0.5 text-foreground truncate">
              {trace.user_id || 'anonymous'}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground text-[11px] uppercase">
              Session ID
            </div>
            <div className="font-mono mt-0.5 text-foreground truncate">
              {trace.session_id || '—'}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground text-[11px] uppercase">
              Conversation ID
            </div>
            <div className="font-mono mt-0.5 text-foreground truncate">
              {trace.conversation_id || '—'}
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}
