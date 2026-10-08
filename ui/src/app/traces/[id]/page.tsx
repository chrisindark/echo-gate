'use client';

import { Card, Heading, Text } from '@radix-ui/themes';
import { Activity, ArrowLeft, Clock, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { TraceDetailView, TraceLog } from '@/components/trace-detail-view';
import { cn } from '@/lib/utils';

export default function TraceDetailPage() {
  const params = useParams();
  const id = params?.id as string;

  const [trace, setTrace] = useState<TraceLog | null>(null);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!id) return;
    let ignore = false;
    const load = async () => {
      try {
        const res = await fetch(
          `http://localhost:8000/api/v1/gateway-requests/${id}`
        );
        if (!res.ok) {
          if (res.status === 404) {
            throw new Error(`Trace with ID #${id} not found.`);
          }
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        const data = await res.json();
        if (!ignore) {
          setTrace(data.response || data.data || null);
          setError(null);
        }
      } catch (e: unknown) {
        if (!ignore) {
          setError(
            e instanceof Error ? e.message : 'Failed to load trace details'
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
  }, [id, refreshKey]);

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 bg-background">
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Navigation Bar */}
        <div className="flex items-center justify-between border-b border-border/50 pb-4">
          <Link
            href="/traces"
            className="flex items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors group"
          >
            <ArrowLeft className="w-4 h-4 transition-transform group-hover:-translate-x-0.5" />
            <span>Back to Traces</span>
          </Link>

          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setIsRefreshing(true);
                setRefreshKey((k) => k + 1);
              }}
              disabled={isRefreshing}
              className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg border border-border/80 bg-card hover:bg-muted text-foreground transition-all shadow-sm disabled:opacity-50"
            >
              <RefreshCw
                className={cn('w-3.5 h-3.5', isRefreshing && 'animate-spin')}
              />
              {isRefreshing ? 'Refreshing...' : 'Refresh'}
            </button>
          </div>
        </div>

        {/* Content */}
        {loading && !trace ? (
          <div className="py-28 flex flex-col items-center justify-center text-muted-foreground gap-2">
            <Clock className="w-6 h-6 animate-spin text-primary" />
            <div className="text-sm">Loading trace #{id}...</div>
          </div>
        ) : error ? (
          <Card
            size="3"
            className="p-8 text-center space-y-3 max-w-md mx-auto my-12"
          >
            <div className="text-red-500 font-semibold text-base">
              Error Loading Trace
            </div>
            <div className="text-xs text-muted-foreground">{error}</div>
            <div className="pt-2 flex justify-center gap-2">
              <button
                onClick={() => {
                  setLoading(true);
                  setRefreshKey((k) => k + 1);
                }}
                className="px-3 py-1.5 bg-primary text-primary-foreground text-xs font-medium rounded-md"
              >
                Retry
              </button>
              <Link
                href="/traces"
                className="px-3 py-1.5 bg-muted text-foreground text-xs font-medium rounded-md hover:bg-muted/80"
              >
                Go to Traces List
              </Link>
            </div>
          </Card>
        ) : trace ? (
          <div className="space-y-6">
            {/* Title Header */}
            <div>
              <div className="flex items-center gap-2 text-primary text-xs font-semibold uppercase tracking-wider">
                <Activity className="w-4 h-4" />
                <span>Request Trace Inspection</span>
              </div>
              <Heading size="8" mt="1" className="tracking-tight">
                Trace #{trace.id}
              </Heading>
              <Text size="2" color="gray">
                Detailed latency breakdown, routing decisions, prompt and
                completion analysis.
              </Text>
            </div>

            {/* Trace Detail View Component */}
            <TraceDetailView trace={trace} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
