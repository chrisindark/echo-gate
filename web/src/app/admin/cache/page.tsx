'use client';

import { useApiKey } from '@/context/api-key-context';
import { API_ROOT } from '@/lib/api';
import { Box, Button, Card, Checkbox, Flex, Heading, Text, TextArea } from '@radix-ui/themes';
import { Database, Upload, CheckCircle2, AlertCircle } from 'lucide-react';
import { useState } from 'react';

type WarmupSummary = {
  queued?: number;
  succeeded?: number;
  failed?: number;
};

export default function CacheAdminPage() {
  const { adminKey } = useApiKey();
  const [payload, setPayload] = useState('[]');
  const [force, setForce] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<WarmupSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleWarmup = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const items = JSON.parse(payload);
      if (!Array.isArray(items)) {
        throw new Error('Items must be a JSON array');
      }
      const res = await fetch(`${API_ROOT}/api/v1/admin/cache/warmup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Admin-Key': adminKey || '',
        },
        body: JSON.stringify({ items, force }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || res.statusText);
      }
      const data = await res.json();
      setResult(data.response ?? data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-10 bg-background">
      <div className="max-w-4xl mx-auto space-y-8">
        <div className="border-b border-border/60 pb-6">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <Database className="w-6 h-6" />
            </div>
            <div>
              <Heading size="7" className="tracking-tight">Cache Admin</Heading>
              <Text color="gray" size="2" mt="1">
                Pre-seed cache entries via warmup API
              </Text>
            </div>
          </div>
        </div>

        <Card size="3" className="p-6 space-y-6">
          <Box>
            <Heading size="4" mb="2">Cache Warmup / Pre-seeding</Heading>
            <Text size="2" color="gray">
              POST batches of request+response pairs to warm the cache
            </Text>
          </Box>
          <Box>
            <Text size="2" weight="medium" mb="2">Items (JSON array)</Text>
            <TextArea
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              placeholder='[{"request": {"model": "gemini-3.1-flash-lite", "messages": [{"role": "user", "content": "hello"}]}, "response": {"id": "...", "object": "chat.completion", "created": 123, "model": "...", "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, "tenant_id": null}]'
              rows={12}
              className="font-mono text-xs"
            />
          </Box>
          <Flex justify="between" align="center" gap="4" wrap="wrap">
            <Box>
              <Text as="label" size="2">
                <Flex gap="2" align="center">
                  <Checkbox
                    checked={force}
                    onCheckedChange={(checked) => setForce(checked === true)}
                  />
                  Force write both layers (ignores CACHE_MODE)
                </Flex>
              </Text>
              {!adminKey && (
                <Text size="1" className="text-amber-500" mt="1">
                  No admin key configured — open Settings and add one with the
                  cache:write scope.
                </Text>
              )}
            </Box>
            <Button onClick={handleWarmup} disabled={loading || !adminKey}>
              <Upload className="w-4 h-4" />
              {loading ? 'Warming...' : 'Warm Cache'}
            </Button>
          </Flex>
          {error && (
            <Flex gap="2" align="start" className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
              <AlertCircle className="w-5 h-5 text-red-500 mt-0.5" />
              <Text size="2" className="text-red-500 break-all">{error}</Text>
            </Flex>
          )}
          {result && (
            <Flex gap="2" align="start" className="p-3 bg-green-500/10 border border-green-500/20 rounded-lg">
              <CheckCircle2 className="w-5 h-5 text-green-500 mt-0.5" />
              <Box>
                <Text size="2" weight="medium" className="text-green-500">Warmup Complete</Text>
                <Text size="2" color="gray">Queued: {result.queued}, Succeeded: {result.succeeded}, Failed: {result.failed}</Text>
              </Box>
            </Flex>
          )}
        </Card>
      </div>
    </div>
  );
}
