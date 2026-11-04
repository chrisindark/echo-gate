'use client';

import { useApiKey } from '@/context/api-key-context';
import { API_ROOT } from '@/lib/api';
import { Box, Button, Card, Flex, Heading, Text, TextField } from '@radix-ui/themes';
import { AlertCircle, Database, Search, Trash2 } from 'lucide-react';
import { useState } from 'react';

type CacheHit = {
  point_id: string;
  score: number | null;
  prompt: string | null;
  user_prompt: string | null;
  model: string | null;
  intent: string | null;
  scope: string | null;
  tenant_id: string | null;
  exact_hash: string | null;
  cacheable: boolean | null;
  expires_at: number | null;
  hit_count: number | null;
};

function formatExpiry(expiresAt: number | null): string {
  if (!expiresAt) return 'no expiry';
  const ms = expiresAt * 1000 - Date.now();
  if (ms <= 0) return 'expired';
  const hours = Math.floor(ms / 3_600_000);
  return hours >= 1 ? `expires in ${hours}h` : 'expires soon';
}

export default function CacheExplorerPage() {
  const { adminKey } = useApiKey();
  const [query, setQuery] = useState('');
  const [tenantFilter, setTenantFilter] = useState('');
  const [modelFilter, setModelFilter] = useState('');
  const [results, setResults] = useState<CacheHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingEvict, setPendingEvict] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const search = async () => {
    if (!query.trim()) return;
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
          query: query.trim(),
          limit: 20,
          tenant_id: tenantFilter.trim() || null,
          model: modelFilter.trim() || null,
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
                Search stored cache entries semantically and evict them
              </Text>
            </div>
          </div>
        </div>

        <Card size="3" className="p-6 space-y-4">
          <Flex gap="3" wrap="wrap">
            <TextField.Root
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Prompt text to match..."
              className="flex-1 min-w-[16rem]"
              onKeyDown={(e) => e.key === 'Enter' && search()}
            />
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
              className="w-40"
            />
            <Button onClick={search} disabled={loading || !adminKey || !query.trim()}>
              <Search className="w-4 h-4" />
              {loading ? 'Searching...' : 'Search'}
            </Button>
          </Flex>
          <Text size="1" color="gray">
            Requires X-Admin-Key with the cache:read scope. Results are not
            filtered by cacheability, so expired and cross-tenant entries are
            visible here.
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
                    <Flex gap="4" wrap="wrap">
                      {hit.model && <Text size="1" color="gray">model: {hit.model}</Text>}
                      {hit.intent && <Text size="1" color="gray">intent: {hit.intent}</Text>}
                      {hit.scope && <Text size="1" color="gray">scope: {hit.scope}</Text>}
                      {hit.tenant_id && <Text size="1" color="gray">tenant: {hit.tenant_id}</Text>}
                      {hit.score !== null && (
                        <Text size="1" color="gray">score: {hit.score.toFixed(4)}</Text>
                      )}
                      {hit.hit_count !== null && (
                        <Text size="1" color="gray">hits: {hit.hit_count}</Text>
                      )}
                      <Text size="1" color={hit.cacheable === false ? 'red' : 'gray'}>
                        {hit.cacheable === false ? 'not cacheable' : formatExpiry(hit.expires_at)}
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
      </div>
    </div>
  );
}
