'use client';

import { Card, Heading, Text } from '@radix-ui/themes';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Filter,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { TraceDetailView, TraceLog } from '@/components/trace-detail-view';
import { cn } from '@/lib/utils';

type RoutingFilter =
  'ALL' | 'LLM' | 'REDIS_EXACT' | 'QDRANT_EXACT' | 'QDRANT_SEMANTIC';

type LatencyFilter = 'ALL' | 'FAST' | 'SLOW' | 'VERY_SLOW';

type TimeRangeFilter =
  '1h' | '3h' | '6h' | '12h' | '1d' | '3d' | '7d' | '14d' | '30d' | 'ALL';

export default function TracesPage() {
  const [traces, setTraces] = useState<TraceLog[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Filters state
  const [search, setSearch] = useState('');
  const [routingFilter, setRoutingFilter] = useState<RoutingFilter>('ALL');
  const [latencyFilter, setLatencyFilter] = useState<LatencyFilter>('ALL');
  const [timeRange, setTimeRange] = useState<TimeRangeFilter>('7d');
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [falsePositivesOnly, setFalsePositivesOnly] = useState(false);

  // Pagination state
  const [page, setPage] = useState(1);
  const pageSize = 25;

  // Selected trace for drawer
  const [selectedTrace, setSelectedTrace] = useState<TraceLog | null>(null);

  useEffect(() => {
    let ignore = false;
    const load = async () => {
      try {
        const skip = (page - 1) * pageSize;
        const params = new URLSearchParams({
          skip: skip.toString(),
          limit: pageSize.toString(),
        });

        if (search.trim()) params.append('search', search.trim());
        if (routingFilter !== 'ALL')
          params.append('routing_decision', routingFilter);
        if (timeRange !== 'ALL') params.append('time_range', timeRange);

        if (latencyFilter === 'FAST') {
          params.append('max_latency_ms', '100');
        } else if (latencyFilter === 'SLOW') {
          params.append('min_latency_ms', '1000');
        } else if (latencyFilter === 'VERY_SLOW') {
          params.append('min_latency_ms', '3000');
        }

        if (errorsOnly) params.append('has_error', 'true');
        if (falsePositivesOnly) params.append('is_false_positive', 'true');

        const res = await fetch(
          `http://localhost:8000/api/v1/gateway-requests/?${params.toString()}`
        );
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        const responsePayload = data.response || data.data || {};

        if (!ignore) {
          if (Array.isArray(responsePayload)) {
            setTraces(responsePayload);
            setTotal(responsePayload.length);
          } else if (responsePayload.items) {
            setTraces(responsePayload.items);
            setTotal(responsePayload.total || 0);
          } else {
            setTraces([]);
            setTotal(0);
          }
          setError(null);
        }
      } catch (e: unknown) {
        if (!ignore) {
          setError(
            e instanceof Error
              ? e.message
              : 'An unknown error occurred while fetching traces'
          );
        }
      } finally {
        if (!ignore) {
          setLoading(false);
          setIsRefreshing(false);
        }
      }
    };

    load();
    return () => {
      ignore = true;
    };
  }, [
    page,
    pageSize,
    search,
    routingFilter,
    latencyFilter,
    timeRange,
    errorsOnly,
    falsePositivesOnly,
    refreshKey,
  ]);

  // Reset page when filters change
  const handleFilterChange = () => {
    setPage(1);
  };

  // Quick statistics calculated from current view
  const cacheHitCount = traces.filter(
    (t) => t.routing_decision && t.routing_decision !== 'LLM'
  ).length;
  const hitRate =
    traces.length > 0
      ? ((cacheHitCount / traces.length) * 100).toFixed(1)
      : '0';
  const avgLatency =
    traces.length > 0
      ? Math.round(
          traces.reduce((acc, t) => acc + (t.latency_ms || 0), 0) /
            traces.length
        )
      : 0;
  const slowCount = traces.filter((t) => (t.latency_ms || 0) > 1000).length;
  const errorCount = traces.filter(
    (t) => Boolean(t.error_message) || Boolean(t.is_false_positive)
  ).length;

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 bg-background relative">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between border-b border-border/50 pb-5">
          <div>
            <div className="flex items-center gap-2.5">
              <Activity className="w-7 h-7 text-primary" />
              <Heading size="8" className="tracking-tight">
                Request Traces
              </Heading>
            </div>
            <Text color="gray" size="2" mt="1">
              Inspect end-to-end request latencies, component breakdowns, and
              cache performance.
            </Text>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setIsRefreshing(true);
                setRefreshKey((k) => k + 1);
              }}
              disabled={isRefreshing}
              className="flex items-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-lg border border-border/80 bg-card hover:bg-muted text-foreground transition-all shadow-sm disabled:opacity-50"
            >
              <RefreshCw
                className={cn('w-3.5 h-3.5', isRefreshing && 'animate-spin')}
              />
              {isRefreshing ? 'Refreshing...' : 'Refresh'}
            </button>
          </div>
        </div>

        {/* Quick KPI stats */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <Card size="1" className="p-3">
            <Text size="1" color="gray" weight="medium">
              Total Filtered
            </Text>
            <div className="text-xl font-bold mt-0.5">
              {total.toLocaleString()}
            </div>
          </Card>
          <Card size="1" className="p-3">
            <Text size="1" color="gray" weight="medium">
              Cache Hit Rate
            </Text>
            <div className="text-xl font-bold text-emerald-500 mt-0.5">
              {hitRate}%
            </div>
          </Card>
          <Card size="1" className="p-3">
            <Text size="1" color="gray" weight="medium">
              Avg Latency
            </Text>
            <div className="text-xl font-bold text-blue-500 mt-0.5">
              {avgLatency}ms
            </div>
          </Card>
          <Card size="1" className="p-3">
            <Text size="1" color="gray" weight="medium">
              Slow Requests (&gt;1s)
            </Text>
            <div className="text-xl font-bold text-amber-500 mt-0.5">
              {slowCount}
            </div>
          </Card>
          <Card size="1" className="p-3">
            <Text size="1" color="gray" weight="medium">
              Errors / False Positives
            </Text>
            <div className="text-xl font-bold text-red-500 mt-0.5">
              {errorCount}
            </div>
          </Card>
        </div>

        {/* Filters Card */}
        <Card size="2" className="space-y-3.5">
          {/* Search bar */}
          <div className="relative">
            <Search className="w-4 h-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search by query prompt, response text, exact hash, model..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                handleFilterChange();
              }}
              className="w-full pl-9 pr-4 py-2 bg-muted/40 border border-border/80 rounded-xl text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            {search && (
              <button
                onClick={() => {
                  setSearch('');
                  handleFilterChange();
                }}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Filter Pills */}
          <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-border/40 text-xs">
            {/* Routing Decision */}
            <div className="flex items-center gap-1 bg-muted/50 p-1 rounded-lg border border-border/50">
              <span className="text-[10px] uppercase font-semibold text-muted-foreground px-1.5">
                Route
              </span>
              {(
                [
                  'ALL',
                  'LLM',
                  'REDIS_EXACT',
                  'QDRANT_EXACT',
                  'QDRANT_SEMANTIC',
                ] as RoutingFilter[]
              ).map((route) => (
                <button
                  key={route}
                  onClick={() => {
                    setRoutingFilter(route);
                    handleFilterChange();
                  }}
                  className={cn(
                    'px-2 py-0.5 rounded text-[11px] font-medium transition-all',
                    routingFilter === route
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {route === 'ALL'
                    ? 'All'
                    : route === 'REDIS_EXACT'
                      ? 'Redis'
                      : route === 'QDRANT_EXACT'
                        ? 'Qdrant'
                        : route === 'QDRANT_SEMANTIC'
                          ? 'Semantic'
                          : 'LLM'}
                </button>
              ))}
            </div>

            {/* Latency / Speed */}
            <div className="flex items-center gap-1 bg-muted/50 p-1 rounded-lg border border-border/50">
              <span className="text-[10px] uppercase font-semibold text-muted-foreground px-1.5">
                Speed
              </span>
              {(['ALL', 'FAST', 'SLOW', 'VERY_SLOW'] as LatencyFilter[]).map(
                (speed) => (
                  <button
                    key={speed}
                    onClick={() => {
                      setLatencyFilter(speed);
                      handleFilterChange();
                    }}
                    className={cn(
                      'px-2 py-0.5 rounded text-[11px] font-medium transition-all',
                      latencyFilter === speed
                        ? 'bg-background text-foreground shadow-xs font-semibold'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {speed === 'ALL'
                      ? 'All'
                      : speed === 'FAST'
                        ? '&lt;100ms'
                        : speed === 'SLOW'
                          ? '&gt;1s'
                          : '&gt;3s'}
                  </button>
                )
              )}
            </div>

            {/* Time Range */}
            <div className="flex items-center gap-1 bg-muted/50 p-1 rounded-lg border border-border/50">
              <span className="text-[10px] uppercase font-semibold text-muted-foreground px-1.5">
                Range
              </span>
              {(
                [
                  '1h',
                  '3h',
                  '12h',
                  '1d',
                  '3d',
                  '7d',
                  '30d',
                  'ALL',
                ] as TimeRangeFilter[]
              ).map((range) => (
                <button
                  key={range}
                  onClick={() => {
                    setTimeRange(range);
                    handleFilterChange();
                  }}
                  className={cn(
                    'px-2 py-0.5 rounded text-[11px] font-medium transition-all',
                    timeRange === range
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {range === 'ALL' ? 'All' : range}
                </button>
              ))}
            </div>

            {/* Toggles */}
            <button
              onClick={() => {
                setErrorsOnly(!errorsOnly);
                handleFilterChange();
              }}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs font-medium border transition-all flex items-center gap-1.5',
                errorsOnly
                  ? 'bg-red-500/10 border-red-500/40 text-red-500 font-semibold'
                  : 'border-border/60 text-muted-foreground hover:text-foreground'
              )}
            >
              <AlertCircle className="w-3.5 h-3.5" />
              Errors
            </button>

            <button
              onClick={() => {
                setFalsePositivesOnly(!falsePositivesOnly);
                handleFilterChange();
              }}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs font-medium border transition-all flex items-center gap-1.5',
                falsePositivesOnly
                  ? 'bg-amber-500/10 border-amber-500/40 text-amber-500 font-semibold'
                  : 'border-border/60 text-muted-foreground hover:text-foreground'
              )}
            >
              <AlertTriangle className="w-3.5 h-3.5" />
              False Positives
            </button>
          </div>
        </Card>

        {/* Traces List Table */}
        <Card size="2" className="p-0 overflow-hidden border-border/80">
          {loading && !traces.length ? (
            <div className="py-20 flex flex-col items-center justify-center text-muted-foreground gap-2">
              <Clock className="w-6 h-6 animate-spin" />
              <div className="text-sm">Loading request traces...</div>
            </div>
          ) : error ? (
            <div className="py-16 text-center text-red-500 space-y-2">
              <div className="text-sm font-medium">Failed to load traces</div>
              <div className="text-xs text-red-400">{error}</div>
              <button
                onClick={() => {
                  setLoading(true);
                  setRefreshKey((k) => k + 1);
                }}
                className="mt-2 px-3 py-1 bg-primary text-primary-foreground text-xs rounded-md"
              >
                Retry
              </button>
            </div>
          ) : traces.length === 0 ? (
            <div className="py-20 flex flex-col items-center justify-center text-muted-foreground space-y-2">
              <Filter className="w-8 h-8 opacity-40 mb-1" />
              <div className="font-semibold text-foreground">
                No traces found
              </div>
              <div className="text-xs text-muted-foreground max-w-sm text-center">
                Try adjusting your search criteria, clearing filters, or
                widening the selected time range.
              </div>
              <button
                onClick={() => {
                  setSearch('');
                  setRoutingFilter('ALL');
                  setLatencyFilter('ALL');
                  setTimeRange('ALL');
                  setErrorsOnly(false);
                  setFalsePositivesOnly(false);
                  setPage(1);
                }}
                className="mt-3 px-3 py-1.5 bg-muted text-xs font-medium rounded-lg hover:bg-muted/80 text-foreground"
              >
                Reset all filters
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border/60 bg-muted/30 text-muted-foreground uppercase tracking-wider font-semibold text-[10px]">
                    <th className="py-3 px-4">Time</th>
                    <th className="py-3 px-4">Route</th>
                    <th className="py-3 px-4">Query Excerpt</th>
                    <th className="py-3 px-4">Model & Provider</th>
                    <th className="py-3 px-4">Total Latency</th>
                    <th className="py-3 px-4">Cache vs Provider</th>
                    <th className="py-3 px-4">Judge</th>
                    <th className="py-3 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40 font-normal">
                  {traces.map((trace) => {
                    const routingBadgeColor =
                      trace.routing_decision === 'REDIS_EXACT'
                        ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-500'
                        : trace.routing_decision === 'QDRANT_EXACT'
                          ? 'border-blue-500/40 bg-blue-500/10 text-blue-500'
                          : trace.routing_decision === 'QDRANT_SEMANTIC'
                            ? 'border-purple-500/40 bg-purple-500/10 text-purple-500'
                            : 'border-amber-500/40 bg-amber-500/10 text-amber-500';

                    const latency = trace.latency_ms || 0;
                    const latencyBadgeColor =
                      latency < 100
                        ? 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20'
                        : latency < 1000
                          ? 'text-blue-500 bg-blue-500/10 border-blue-500/20'
                          : latency < 3000
                            ? 'text-amber-500 bg-amber-500/10 border-amber-500/20'
                            : 'text-red-500 bg-red-500/10 border-red-500/20';

                    const isSelected = selectedTrace?.id === trace.id;

                    return (
                      <tr
                        key={trace.id}
                        onClick={() => setSelectedTrace(trace)}
                        className={cn(
                          'hover:bg-muted/40 cursor-pointer transition-colors',
                          isSelected && 'bg-primary/5'
                        )}
                      >
                        {/* Time */}
                        <td className="py-3.5 px-4 whitespace-nowrap text-muted-foreground font-mono text-[11px]">
                          <div>
                            {new Date(trace.created_at).toLocaleTimeString([], {
                              hour: '2-digit',
                              minute: '2-digit',
                              second: '2-digit',
                            })}
                          </div>
                          <div className="text-[10px] text-muted-foreground/70">
                            {new Date(trace.created_at).toLocaleDateString([], {
                              month: 'short',
                              day: 'numeric',
                            })}
                          </div>
                        </td>

                        {/* Routing Decision */}
                        <td className="py-3.5 px-4 whitespace-nowrap">
                          <span
                            className={cn(
                              'px-2 py-0.5 rounded-md font-semibold text-[10px] border tracking-wide uppercase',
                              routingBadgeColor
                            )}
                          >
                            {trace.routing_decision}
                          </span>
                        </td>

                        {/* Query */}
                        <td className="py-3.5 px-4 max-w-xs md:max-w-sm truncate text-foreground font-medium">
                          <span title={trace.query_text}>
                            {trace.query_text}
                          </span>
                        </td>

                        {/* Provider / Model */}
                        <td className="py-3.5 px-4 whitespace-nowrap text-muted-foreground">
                          <div className="text-foreground font-medium">
                            {trace.model || 'default'}
                          </div>
                          <div className="text-[10px] text-muted-foreground">
                            {trace.provider || 'unknown'}
                          </div>
                        </td>

                        {/* Total Latency */}
                        <td className="py-3.5 px-4 whitespace-nowrap font-mono font-bold">
                          <span
                            className={cn(
                              'px-2 py-0.5 rounded-md border text-[11px]',
                              latencyBadgeColor
                            )}
                          >
                            {trace.latency_ms !== null
                              ? `${trace.latency_ms} ms`
                              : '—'}
                          </span>
                        </td>

                        {/* Cache vs Provider snippet */}
                        <td className="py-3.5 px-4 whitespace-nowrap font-mono text-[11px] text-muted-foreground">
                          <div className="flex items-center gap-1.5">
                            <span className="text-emerald-500">
                              ⚡
                              {trace.cache_lookup_latency_ms !== null
                                ? `${trace.cache_lookup_latency_ms}ms`
                                : '—'}
                            </span>
                            <span className="text-muted-foreground/60">/</span>
                            <span className="text-amber-500">
                              ☁
                              {trace.provider_latency_ms !== null
                                ? `${trace.provider_latency_ms}ms`
                                : '—'}
                            </span>
                          </div>
                        </td>

                        {/* Judge / Evaluator */}
                        <td className="py-3.5 px-4 whitespace-nowrap">
                          {trace.is_false_positive ? (
                            <span className="px-1.5 py-0.5 rounded bg-red-500/10 text-red-500 border border-red-500/20 text-[10px] font-semibold flex items-center gap-1 w-fit">
                              <AlertTriangle className="w-3 h-3" /> False Pos
                            </span>
                          ) : trace.error_message ? (
                            <span className="px-1.5 py-0.5 rounded bg-red-500/10 text-red-500 border border-red-500/20 text-[10px] font-semibold flex items-center gap-1 w-fit">
                              <AlertCircle className="w-3 h-3" /> Error
                            </span>
                          ) : trace.llm_relevance_score !== null &&
                            trace.llm_relevance_score !== undefined ? (
                            <span className="text-[11px] font-mono text-emerald-500 font-semibold">
                              ★ {trace.llm_relevance_score.toFixed(1)}/5
                            </span>
                          ) : (
                            <span className="text-muted-foreground/60 text-[11px]">
                              {trace.evaluation_status}
                            </span>
                          )}
                        </td>

                        {/* Actions */}
                        <td className="py-3.5 px-4 text-right whitespace-nowrap">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedTrace(trace);
                            }}
                            className="px-2.5 py-1 text-xs font-medium rounded-md bg-muted/80 hover:bg-primary hover:text-primary-foreground transition-colors"
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination Footer */}
          <div className="p-3 border-t border-border/60 bg-muted/10 flex items-center justify-between text-xs text-muted-foreground">
            <div>
              Showing {traces.length ? (page - 1) * pageSize + 1 : 0} to{' '}
              {Math.min(page * pageSize, total)} of {total.toLocaleString()}{' '}
              traces
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
                className="p-1.5 rounded-md border border-border/60 bg-background hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed"
                title="Previous page"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="font-medium text-foreground">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
                className="p-1.5 rounded-md border border-border/60 bg-background hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed"
                title="Next page"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </Card>
      </div>

      {/* Slide-Over Drawer for Trace Detail */}
      <AnimatePresence>
        {selectedTrace && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setSelectedTrace(null)}
              className="fixed inset-0 bg-background/60 backdrop-blur-xs z-40"
            />

            {/* Sliding Drawer */}
            <motion.aside
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ type: 'spring', damping: 28, stiffness: 300 }}
              className="fixed top-0 right-0 h-screen w-full sm:w-[680px] lg:w-[750px] bg-card border-l border-border shadow-2xl z-50 flex flex-col overflow-hidden"
            >
              {/* Drawer Header */}
              <div className="p-4 border-b border-border flex items-center justify-between shrink-0 bg-muted/20">
                <div className="flex items-center gap-2">
                  <Activity className="w-4 h-4 text-primary" />
                  <span className="font-bold text-sm text-foreground">
                    Trace #{selectedTrace.id}
                  </span>
                  <span className="text-xs text-muted-foreground font-mono">
                    (
                    {selectedTrace.exact_hash
                      ? selectedTrace.exact_hash.slice(0, 10) + '...'
                      : ''}
                    )
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <Link
                    href={`/traces/${selectedTrace.id}`}
                    className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-muted hover:bg-muted/80 text-foreground transition-colors"
                  >
                    <span>Full Page</span>
                    <ExternalLink className="w-3 h-3" />
                  </Link>
                  <button
                    onClick={() => setSelectedTrace(null)}
                    className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Drawer Content */}
              <div className="flex-1 overflow-y-auto p-4 sm:p-6">
                <TraceDetailView trace={selectedTrace} compact />
              </div>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
