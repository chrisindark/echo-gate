'use client';

import { useApiKey } from '@/context/api-key-context';
import { API_ROOT } from '@/lib/api';
import {
  Badge,
  Box,
  Button,
  Card,
  Checkbox,
  Flex,
  Heading,
  Select,
  Text,
  TextField,
} from '@radix-ui/themes';
import { AlertCircle, Database, Search, Trash2 } from 'lucide-react';
import { useState } from 'react';

type SearchMode = 'semantic' | 'exact';

type CacheHit = {
  point_id: string;
  score: number | null;
  prompt: string | null;
  user_prompt: string | null;
  model: string | null;
  service_name: string | null;
  intent: string | null;
  scope: string | null;
  tenant_id: string | null;
  exact_hash: string | null;
  cacheable: boolean | null;
  created_at: number | null;
  expires_at: number | null;
  hit_count: number | null;
  entities: string[];
  time_sensitivity: number | null;
  core_operation: string | null;
  core_subject: string | null;
  prompt_version: string | null;
  reranker_version: string | null;
  cache_key_version: string | null;
  provider_cost: number | null;
  latency_ms: number | null;
  token_count: number | null;
  retention_value: number | null;
  rerank_score: number | null;
  vector_similarity: number | null;
  is_equivalent: boolean | null;
  last_audited_at: string | null;
};

type BulkEvictPlan = {
  matched: number;
  deleted: number;
  redis_deleted: number;
  dry_run: boolean;
  filters: Record<string, unknown>;
};

function formatExpiry(expiresAt: number | null): string {
  if (!expiresAt) return 'no expiry';
  const ms = expiresAt * 1000 - Date.now();
  if (ms <= 0) return 'expired';
  const hours = Math.floor(ms / 3_600_000);
  return hours >= 1 ? `expires in ${hours}h` : 'expires soon';
}

function formatAge(createdAt: number | null): string {
  if (!createdAt) return 'unknown age';
  const days = Math.floor((Date.now() - createdAt * 1000) / 86_400_000);
  return days <= 0 ? 'created today' : `created ${days}d ago`;
}

function VerdictBadge({ hit }: { hit: CacheHit }) {
  if (hit.is_equivalent === null) return null;
  return (
    <Badge size="1" color={hit.is_equivalent ? 'green' : 'red'} variant="soft">
      {hit.is_equivalent ? 'audit: equivalent' : 'audit: false positive'}
    </Badge>
  );
}

export default function CacheExplorerPage() {
  const { adminKey } = useApiKey();
  const [mode, setMode] = useState<SearchMode>('semantic');
  const [query, setQuery] = useState('');
  const [tenantFilter, setTenantFilter] = useState('');
  const [modelFilter, setModelFilter] = useState('');
  const [intentFilter, setIntentFilter] = useState('');
  const [scopeFilter, setScopeFilter] = useState('');
  const [results, setResults] = useState<CacheHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingEvict, setPendingEvict] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Bulk eviction state.
  const [bulkIntent, setBulkIntent] = useState('');
  const [bulkModel, setBulkModel] = useState('');
  const [bulkOlderThan, setBulkOlderThan] = useState('');
  const [bulkExpired, setBulkExpired] = useState(false);
  const [bulkPlan, setBulkPlan] = useState<BulkEvictPlan | null>(null);
  const [bulkLoading, setBulkLoading] = useState(false);
  const [pendingBulk, setPendingBulk] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);

  const filters = {
    tenant_id: tenantFilter.trim() || null,
    model: modelFilter.trim() || null,
    intent: intentFilter.trim() || null,
    scope: scopeFilter.trim() || null,
  };

  const search = async () => {
    const value = query.trim();
    if (!value) return;
    setLoading(true);
    setError(null);
    setNotice(null);
    setPendingEvict(null);
    try {
      const res = await fetch(`${API_ROOT}/api/v1/admin/cache/search`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Admin-Key': adminKey || '',
        },
        body: JSON.stringify({
          mode,
          ...(mode === 'exact' ? { exact_hash: value } : { query: value }),
          limit: 20,
          ...filters,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      // BaseAPIResponse envelope: { success, response: { count, results } }
      setResults(data.response?.results ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResults(null);
    } finally {
      setLoading(false);
    }
  };

  const evict = async (hit: CacheHit) => {
    setError(null);
    setNotice(null);
    try {
      const params = hit.exact_hash
        ? `?exact_hash=${encodeURIComponent(hit.exact_hash)}`
        : '';
      const res = await fetch(
        `${API_ROOT}/api/v1/admin/cache/entry/${encodeURIComponent(hit.point_id)}${params}`,
        {
          method: 'DELETE',
          headers: { 'X-Admin-Key': adminKey || '' },
        },
      );
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      const redis = data.response?.redis_deleted;
      setResults((prev) =>
        (prev ?? []).filter((r) => r.point_id !== hit.point_id),
      );
      setNotice(
        redis === false
          ? `Evicted ${hit.point_id} from Qdrant; the Redis copy could not be removed.`
          : `Evicted ${hit.point_id}.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPendingEvict(null);
    }
  };

  const bulkFilters = () => ({
    intent: bulkIntent.trim() || null,
    model: bulkModel.trim() || null,
    older_than_days: bulkOlderThan.trim() ? Number(bulkOlderThan) : null,
    expired_only: bulkExpired,
  });

  const hasBulkFilter =
    !!bulkIntent.trim() || !!bulkModel.trim() || !!bulkOlderThan.trim() || bulkExpired;

  const runBulk = async (dryRun: boolean) => {
    if (!hasBulkFilter) {
      setBulkError('Set at least one filter: intent, model, age, or expired only.');
      return;
    }
    if (!dryRun && Number.isNaN(Number(bulkOlderThan))) {
      setBulkError('older_than_days must be a number.');
      return;
    }

    setBulkLoading(true);
    setBulkError(null);
    setBulkNotice(null);
    try {
      const res = await fetch(`${API_ROOT}/api/v1/admin/cache/evict`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Admin-Key': adminKey || '',
        },
        body: JSON.stringify({ ...bulkFilters(), dry_run: dryRun, limit: 1000 }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      const plan: BulkEvictPlan = data.response;
      if (dryRun) {
        setBulkPlan(plan);
        setPendingBulk(false);
      } else {
        setBulkPlan(null);
        setPendingBulk(false);
        setBulkNotice(
          `Evicted ${plan.deleted} of ${plan.matched} matched entries ` +
            `(${plan.redis_deleted} Redis key${plan.redis_deleted === 1 ? '' : 's'} removed).`,
        );
      }
    } catch (e) {
      setBulkError(e instanceof Error ? e.message : String(e));
      setBulkPlan(null);
      setPendingBulk(false);
    } finally {
      setBulkLoading(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-10 bg-background">
      <div className="max-w-6xl mx-auto space-y-8">
        <div className="border-b border-border/60 pb-6">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <Database className="w-6 h-6" />
            </div>
            <div>
              <Heading size="7" className="tracking-tight">Cache Explorer</Heading>
              <Text color="gray" size="2" mt="1">
                Search stored cache entries by prompt or exact hash, inspect their
                metadata and audit verdicts, and evict them
              </Text>
            </div>
          </div>
        </div>

        <Card size="3" className="p-6 space-y-4">
          <Flex gap="3" wrap="wrap" align="center">
            <Select.Root value={mode} onValueChange={(v) => setMode(v as SearchMode)}>
              <Select.Trigger className="w-44" />
              <Select.Content>
                <Select.Item value="semantic">Semantic (prompt)</Select.Item>
                <Select.Item value="exact">Exact hash</Select.Item>
              </Select.Content>
            </Select.Root>
            <TextField.Root
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                mode === 'exact'
                  ? 'Exact cache hash (SHA-256)...'
                  : 'Prompt text to match...'
              }
              className="flex-1 min-w-[16rem]"
              onKeyDown={(e) => e.key === 'Enter' && search()}
            />
            <Button onClick={search} disabled={loading || !adminKey || !query.trim()}>
              <Search className="w-4 h-4" />
              {loading ? 'Searching...' : 'Search'}
            </Button>
          </Flex>
          <Flex gap="3" wrap="wrap">
            <TextField.Root
              value={tenantFilter}
              onChange={(e) => setTenantFilter(e.target.value)}
              placeholder="tenant (all)"
              className="w-40"
            />
            <TextField.Root
              value={modelFilter}
              onChange={(e) => setModelFilter(e.target.value)}
              placeholder="model (all)"
              className="w-44"
            />
            <TextField.Root
              value={intentFilter}
              onChange={(e) => setIntentFilter(e.target.value)}
              placeholder="intent (all)"
              className="w-40"
            />
            <TextField.Root
              value={scopeFilter}
              onChange={(e) => setScopeFilter(e.target.value)}
              placeholder="scope (all)"
              className="w-36"
            />
          </Flex>
          <Text size="1" color="gray">
            Requires X-Admin-Key with the cache:read scope. Exact-hash mode does
            not call the embedding engine, so it still works when inference is
            down. Results are not filtered by cacheability, so expired and
            cross-tenant entries are visible here.
          </Text>
        </Card>

        {error && (
          <Flex gap="2" align="start" className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
            <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
            <Text size="2" className="text-red-500 break-all">{error}</Text>
          </Flex>
        )}
        {notice && (
          <Flex gap="2" align="start" className="p-3 bg-green-500/10 border border-green-500/20 rounded-lg">
            <Text size="2" className="text-green-500 break-all">{notice}</Text>
          </Flex>
        )}

        {results !== null && results.length === 0 && !loading && (
          <Card size="3" className="p-6">
            <Text size="2" color="gray">No matching cache entries.</Text>
          </Card>
        )}

        {results !== null && results.length > 0 && (
          <div className="space-y-3">
            <Text size="2" color="gray">{results.length} entries</Text>
            {results.map((hit) => (
              <Card key={hit.point_id} size="2" className="p-4">
                <Flex justify="between" align="start" gap="4">
                  <Box className="flex-1 space-y-2 min-w-0">
                    <Text size="2" weight="medium" className="line-clamp-2">
                      {hit.user_prompt || hit.prompt || hit.point_id}
                    </Text>
                    <Flex gap="3" wrap="wrap" align="center">
                      {hit.model && <Text size="1" color="gray">model: {hit.model}</Text>}
                      {hit.intent && <Text size="1" color="gray">intent: {hit.intent}</Text>}
                      {hit.scope && <Text size="1" color="gray">scope: {hit.scope}</Text>}
                      {hit.tenant_id && <Text size="1" color="gray">tenant: {hit.tenant_id}</Text>}
                      {hit.score !== null && (
                        <Text size="1" color="gray">similarity: {hit.score.toFixed(4)}</Text>
                      )}
                      {hit.rerank_score !== null && (
                        <Text size="1" color="gray">
                          rerank: {hit.rerank_score.toFixed(4)}
                        </Text>
                      )}
                      {hit.hit_count !== null && (
                        <Text size="1" color="gray">hits: {hit.hit_count}</Text>
                      )}
                      <Text size="1" color={hit.cacheable === false ? 'red' : 'gray'}>
                        {hit.cacheable === false ? 'not cacheable' : formatExpiry(hit.expires_at)}
                      </Text>
                      <Text size="1" color="gray">{formatAge(hit.created_at)}</Text>
                      <VerdictBadge hit={hit} />
                    </Flex>
                    <Flex gap="3" wrap="wrap">
                      {hit.entities.length > 0 && (
                        <Text size="1" color="gray">
                          entities: {hit.entities.join(', ')}
                        </Text>
                      )}
                      {hit.core_operation && (
                        <Text size="1" color="gray">
                          operation: {hit.core_operation}
                          {hit.core_subject ? ` / ${hit.core_subject}` : ''}
                        </Text>
                      )}
                      {hit.provider_cost !== null && (
                        <Text size="1" color="gray">
                          cost avoided: ${hit.provider_cost.toFixed(5)}
                        </Text>
                      )}
                      {hit.latency_ms !== null && (
                        <Text size="1" color="gray">latency: {hit.latency_ms}ms</Text>
                      )}
                      {hit.token_count !== null && (
                        <Text size="1" color="gray">tokens: {hit.token_count}</Text>
                      )}
                      {hit.retention_value !== null && (
                        <Text size="1" color="gray">
                          retention: {hit.retention_value.toFixed(2)}
                        </Text>
                      )}
                      {hit.last_audited_at && (
                        <Text size="1" color="gray">
                          audited: {new Date(hit.last_audited_at).toLocaleString()}
                        </Text>
                      )}
                    </Flex>
                    <Flex gap="3" wrap="wrap">
                      {hit.exact_hash && (
                        <Text size="1" color="gray" className="break-all">
                          hash: {hit.exact_hash}
                        </Text>
                      )}
                      <Text size="1" color="gray" className="break-all">
                        point: {hit.point_id}
                      </Text>
                    </Flex>
                  </Box>
                  <Flex gap="2" className="shrink-0">
                    {pendingEvict === hit.point_id ? (
                      <>
                        <Button
                          size="1"
                          color="red"
                          onClick={() => evict(hit)}
                        >
                          Confirm evict
                        </Button>
                        <Button
                          size="1"
                          variant="soft"
                          color="gray"
                          onClick={() => setPendingEvict(null)}
                        >
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <Button
                        size="1"
                        color="red"
                        variant="soft"
                        onClick={() => setPendingEvict(hit.point_id)}
                      >
                        <Trash2 className="w-3 h-3" />
                        Evict
                      </Button>
                    )}
                  </Flex>
                </Flex>
              </Card>
            ))}
          </div>
        )}

        <Card size="3" className="p-6 space-y-4">
          <div>
            <Heading size="4">Bulk eviction</Heading>
            <Text size="1" color="gray">
              Evict every entry matching a filter. Filters are combined, at least
              one is required, and a single call is capped at 1000 entries.
            </Text>
          </div>
          <Flex gap="3" wrap="wrap">
            <TextField.Root
              value={bulkIntent}
              onChange={(e) => setBulkIntent(e.target.value)}
              placeholder="intent (any)"
              className="w-40"
            />
            <TextField.Root
              value={bulkModel}
              onChange={(e) => setBulkModel(e.target.value)}
              placeholder="model (any)"
              className="w-44"
            />
            <TextField.Root
              value={bulkOlderThan}
              onChange={(e) => setBulkOlderThan(e.target.value)}
              placeholder="older than (days)"
              className="w-44"
              type="number"
            />
            <Text as="label" size="2">
              <Flex gap="2" align="center" className="h-8">
                <Checkbox
                  checked={bulkExpired}
                  onCheckedChange={(checked) => setBulkExpired(checked === true)}
                />
                Expired only
              </Flex>
            </Text>
          </Flex>

          <Flex gap="2" align="center" wrap="wrap">
            <Button
              variant="soft"
              onClick={() => runBulk(true)}
              disabled={bulkLoading || !adminKey || !hasBulkFilter}
            >
              {bulkLoading ? 'Checking...' : 'Preview matches'}
            </Button>
            {bulkPlan && (
              <Button
                color="red"
                onClick={() => (pendingBulk ? runBulk(false) : setPendingBulk(true))}
                disabled={bulkLoading || bulkPlan.matched === 0}
              >
                <Trash2 className="w-4 h-4" />
                {pendingBulk
                  ? `Confirm evicting ${bulkPlan.matched}`
                  : `Evict ${bulkPlan.matched} matched`}
              </Button>
            )}
            {pendingBulk && (
              <Button variant="soft" color="gray" onClick={() => setPendingBulk(false)}>
                Cancel
              </Button>
            )}
          </Flex>

          <Text size="1" color="gray">
            Preview only counts matches; nothing is removed until you confirm.
            A sweep also deletes the Redis copy of each matched exact hash.
          </Text>

          {bulkError && (
            <Flex gap="2" align="start" className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
              <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
              <Text size="2" className="text-red-500 break-all">{bulkError}</Text>
            </Flex>
          )}
          {bulkPlan && (
            <Text size="2" color={bulkPlan.matched ? 'gray' : 'green'}>
              {bulkPlan.matched === 0
                ? 'No entries match these filters.'
                : `${bulkPlan.matched} entr${bulkPlan.matched === 1 ? 'y' : 'ies'} match ${JSON.stringify(bulkPlan.filters)}.`}
            </Text>
          )}
          {bulkNotice && (
            <Flex gap="2" align="start" className="p-3 bg-green-500/10 border border-green-500/20 rounded-lg">
              <Text size="2" className="text-green-500 break-all">{bulkNotice}</Text>
            </Flex>
          )}
        </Card>
      </div>
    </div>
  );
}
