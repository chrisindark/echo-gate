'use client';

import { useApiKey } from '@/context/api-key-context';
import { API_ROOT } from '@/lib/api';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Flex,
  Heading,
  Select,
  Text,
  TextField,
} from '@radix-ui/themes';
import {
  AlertCircle,
  Check,
  Database,
  EyeOff,
  Pencil,
  Plus,
  RefreshCw,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

const API_BASE = `${API_ROOT}/api/v1/admin/tenant-policies`;

type PiiScrubbingMode = 'inherit' | 'enforced' | 'disabled';
type GuardrailsPolicyMode = 'inherit' | 'off' | 'inject' | 'block';

interface TenantPolicy {
  id: number;
  tenant_id: string;
  semantic_cache_enabled: boolean;
  zero_retention: boolean;
  pii_scrubbing_mode: PiiScrubbingMode;
  guardrails_mode: GuardrailsPolicyMode;
  description: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

interface PolicyListResponse {
  policies: TenantPolicy[];
  total: number;
  skip: number;
  limit: number;
}

interface PolicyForm {
  tenant_id: string;
  semantic_cache_enabled: boolean;
  zero_retention: boolean;
  pii_scrubbing_mode: PiiScrubbingMode;
  guardrails_mode: GuardrailsPolicyMode;
  description: string;
  is_active: boolean;
}

const EMPTY_FORM: PolicyForm = {
  tenant_id: '',
  semantic_cache_enabled: true,
  zero_retention: false,
  pii_scrubbing_mode: 'inherit',
  guardrails_mode: 'inherit',
  description: '',
  is_active: true,
};

export default function TenantPoliciesAdminPage() {
  const { adminKey, quotaTenant } = useApiKey();

  const [policies, setPolicies] = useState<TenantPolicy[]>([]);
  const [total, setTotal] = useState(0);
  const [tenants, setTenants] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const [formOpen, setFormOpen] = useState(false);
  const [editingTenant, setEditingTenant] = useState<string | null>(null);
  const [form, setForm] = useState<PolicyForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const headers = useCallback((): Record<string, string> => {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (adminKey) h['X-Admin-Key'] = adminKey;
    return h;
  }, [adminKey]);

  useEffect(() => {
    if (!adminKey) {
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const [policiesRes, tenantsRes] = await Promise.all([
          fetch(`${API_BASE}/?limit=200`, { headers: headers() }),
          fetch(`${API_BASE}/tenants`, { headers: headers() }),
        ]);

        if (!policiesRes.ok) {
          const text = await policiesRes.text();
          throw new Error(
            text || `Failed to load policies (${policiesRes.status})`
          );
        }

        const policiesJson = await policiesRes.json();
        const payload: PolicyListResponse =
          policiesJson.response ?? policiesJson;
        if (!cancelled) {
          setPolicies(payload.policies ?? []);
          setTotal(payload.total ?? 0);
          setError(null);
        }

        if (tenantsRes.ok) {
          const tenantsJson = await tenantsRes.json();
          if (!cancelled) {
            setTenants(tenantsJson.response ?? []);
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [adminKey, headers, refreshKey]);

  const refresh = () => {
    setNotice(null);
    setError(null);
    setLoading(true);
    setRefreshKey((k) => k + 1);
  };

  if (!adminKey) {
    return (
      <div className="flex-1 overflow-y-auto p-6 md:p-10 bg-background">
        <div className="max-w-2xl mx-auto mt-10">
          <Card size="3" className="p-8 border-border/80 space-y-4 text-center">
            <div className="flex justify-center">
              <div className="p-3 rounded-xl bg-amber-500/10 text-amber-500 border border-amber-500/20">
                <ShieldAlert className="w-7 h-7" />
              </div>
            </div>
            <Heading size="5">Admin API Key Required</Heading>
            <Text color="gray" size="2">
              Per-tenant privacy &amp; semantic cache restrictions can only be
              viewed or modified by operators with a scoped{' '}
              <code className="font-mono text-[11px] bg-muted/60 px-1 py-0.5 rounded">
                X-Admin-Key
              </code>
              .
            </Text>
            <Card
              size="1"
              className="p-4 bg-muted/30 border-border/60 text-left"
            >
              <Text size="1" color="gray" as="p">
                Mint a key on the host running Echo Gate:
              </Text>
              <pre className="mt-2 p-3 rounded-lg bg-muted/60 text-[11px] font-mono overflow-x-auto">
                {`python -m app.scripts.create_admin_key create \\
  --name ops --scopes policy:read,policy:write`}
              </pre>
            </Card>
            <Link href="/settings">
              <Button color="blue" variant="solid">
                <Settings className="w-4 h-4" /> Open Settings
              </Button>
            </Link>
          </Card>
        </div>
      </div>
    );
  }

  const openCreate = () => {
    setEditingTenant(null);
    setForm({ ...EMPTY_FORM, tenant_id: quotaTenant || '' });
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (policy: TenantPolicy) => {
    setEditingTenant(policy.tenant_id);
    setForm({
      tenant_id: policy.tenant_id,
      semantic_cache_enabled: policy.semantic_cache_enabled,
      zero_retention: policy.zero_retention,
      pii_scrubbing_mode: policy.pii_scrubbing_mode,
      guardrails_mode: policy.guardrails_mode,
      description: policy.description ?? '',
      is_active: policy.is_active,
    });
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditingTenant(null);
    setFormError(null);
  };

  const applyPreset = (
    preset:
      'strict_zero_retention' | 'no_cache_with_logs' | 'savings_performance'
  ) => {
    if (preset === 'strict_zero_retention') {
      setForm((prev) => ({
        ...prev,
        semantic_cache_enabled: false,
        zero_retention: true,
        pii_scrubbing_mode: 'enforced',
        guardrails_mode: 'block',
        description:
          prev.description ||
          'Strict Security: Zero-Retention, No Cache, PII Scrubbing ON',
        is_active: true,
      }));
    } else if (preset === 'no_cache_with_logs') {
      setForm((prev) => ({
        ...prev,
        semantic_cache_enabled: false,
        zero_retention: false,
        pii_scrubbing_mode: 'enforced',
        description:
          prev.description ||
          'Isolated Data: No Semantic Cache, PII Scrubbing ON, Usage Logs ON',
        is_active: true,
      }));
    } else {
      setForm((prev) => ({
        ...prev,
        semantic_cache_enabled: true,
        zero_retention: false,
        pii_scrubbing_mode: 'disabled',
        guardrails_mode: 'inherit',
        description:
          prev.description ||
          'Savings & Latency: Full Semantic Cache, Logs ON, PII Scrubbing OFF',
        is_active: true,
      }));
    }
  };

  const savePolicy = async (e: React.FormEvent) => {
    e.preventDefault();
    const targetTenant = (editingTenant ?? form.tenant_id).trim();
    if (!targetTenant) {
      setFormError('Tenant ID is required.');
      return;
    }

    setSaving(true);
    setFormError(null);
    try {
      const res = await fetch(
        `${API_BASE}/${encodeURIComponent(targetTenant)}`,
        {
          method: 'PUT',
          headers: headers(),
          body: JSON.stringify({
            semantic_cache_enabled: form.semantic_cache_enabled,
            zero_retention: form.zero_retention,
            pii_scrubbing_mode: form.pii_scrubbing_mode,
            guardrails_mode: form.guardrails_mode,
            description: form.description.trim() || null,
            is_active: form.is_active,
          }),
        }
      );

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Failed to save policy (${res.status})`);
      }

      closeForm();
      setNotice(`Saved policy for tenant '${targetTenant}'.`);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const deletePolicy = async (tenantId: string) => {
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE}/${encodeURIComponent(tenantId)}`, {
        method: 'DELETE',
        headers: headers(),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Delete failed (${res.status})`);
      }
      setNotice(
        `Removed custom restriction for '${tenantId}'. Tenant reverted to gateway defaults.`
      );
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-10 bg-background">
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Header */}
        <div className="border-b border-border/60 pb-6 flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <ShieldCheck className="w-6 h-6" />
            </div>
            <div>
              <Heading size="7" className="tracking-tight">
                Tenant Privacy &amp; Cache Policies
              </Heading>
              <Text color="gray" size="2" mt="1" as="p">
                Enforce semantic caching, Zero-Retention mode, and PII scrubbing
                per organization/tenant.
              </Text>
            </div>
          </div>

          <Flex gap="2" align="center">
            <Button variant="soft" color="gray" onClick={refresh}>
              <RefreshCw className="w-4 h-4" />
              Refresh
            </Button>
            <Button onClick={openCreate}>
              <Plus className="w-4 h-4" />
              Configure Tenant
            </Button>
          </Flex>
        </div>

        {error && (
          <Card size="2" className="p-4 border-red-500/30 bg-red-500/10">
            <Flex gap="2" align="center">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <Text size="2" className="text-red-400 break-all">
                {error}
              </Text>
            </Flex>
          </Card>
        )}

        {notice && (
          <Card
            size="2"
            className="p-4 border-emerald-500/30 bg-emerald-500/10"
          >
            <Flex gap="2" align="center">
              <Check className="w-4 h-4 text-emerald-500 shrink-0" />
              <Text size="2" className="text-emerald-400">
                {notice}
              </Text>
            </Flex>
          </Card>
        )}

        {/* Upsert Form */}
        {formOpen && (
          <Card size="3" className="p-6 border-primary/30 space-y-5">
            <Flex justify="between" align="center">
              <Heading size="4">
                {editingTenant
                  ? `Edit Policy: ${editingTenant}`
                  : 'Configure Tenant Policy'}
              </Heading>
              <button
                type="button"
                onClick={closeForm}
                className="p-1 text-muted-foreground hover:text-foreground cursor-pointer"
                aria-label="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </Flex>

            {/* Quick Presets */}
            <div className="space-y-2">
              <Text
                size="1"
                weight="bold"
                color="gray"
                className="uppercase tracking-wider"
              >
                Quick Presets
              </Text>
              <Flex gap="2" wrap="wrap">
                <Button
                  type="button"
                  variant="soft"
                  color="red"
                  size="1"
                  onClick={() => applyPreset('strict_zero_retention')}
                >
                  <EyeOff className="w-3.5 h-3.5" />
                  High Security (No Cache + Zero-Retention + PII Scrub)
                </Button>
                <Button
                  type="button"
                  variant="soft"
                  color="amber"
                  size="1"
                  onClick={() => applyPreset('no_cache_with_logs')}
                >
                  <ShieldCheck className="w-3.5 h-3.5" />
                  No Semantic Cache + Keep Billing Logs
                </Button>
                <Button
                  type="button"
                  variant="soft"
                  color="green"
                  size="1"
                  onClick={() => applyPreset('savings_performance')}
                >
                  <Zap className="w-3.5 h-3.5" />
                  Savings &amp; Latency (Full Cache + Logs + No PII Scrub)
                </Button>
              </Flex>
            </div>

            <form onSubmit={savePolicy} className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Text as="label" size="2" weight="bold">
                    Tenant ID
                  </Text>
                  <TextField.Root
                    value={editingTenant ?? form.tenant_id}
                    disabled={editingTenant !== null}
                    onChange={(e) =>
                      setForm({ ...form, tenant_id: e.target.value })
                    }
                    placeholder="e.g. acme-corp or anonymous"
                    list="known-tenants-datalist"
                  />
                  {tenants.length > 0 && editingTenant === null && (
                    <datalist id="known-tenants-datalist">
                      {tenants.map((t) => (
                        <option key={t} value={t} />
                      ))}
                    </datalist>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Text as="label" size="2" weight="bold">
                    Description / Compliance Profile
                  </Text>
                  <TextField.Root
                    value={form.description}
                    onChange={(e) =>
                      setForm({ ...form, description: e.target.value })
                    }
                    placeholder="Optional notes (e.g. HIPAA healthcare org)"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
                <Card size="1" className="p-3.5 space-y-2 border-border/60">
                  <Flex align="center" gap="2">
                    <Checkbox
                      checked={form.semantic_cache_enabled}
                      disabled={form.zero_retention}
                      onCheckedChange={(v) =>
                        setForm({
                          ...form,
                          semantic_cache_enabled: Boolean(v),
                        })
                      }
                      id="policy-semantic-cache"
                    />
                    <Text
                      as="label"
                      htmlFor="policy-semantic-cache"
                      size="2"
                      weight="bold"
                      className="cursor-pointer flex items-center gap-1.5"
                    >
                      <Database className="w-4 h-4 text-emerald-400" />
                      Allow Semantic &amp; Exact Caching
                    </Text>
                  </Flex>
                  <Text size="1" color="gray" as="p" className="pl-6">
                    Uncheck to completely stop Redis exact lookups and Qdrant
                    semantic vector matching for this tenant.
                  </Text>
                </Card>

                <Card size="1" className="p-3.5 space-y-2 border-border/60">
                  <Flex align="center" gap="2">
                    <Checkbox
                      checked={form.zero_retention}
                      onCheckedChange={(v) => {
                        const zr = Boolean(v);
                        setForm({
                          ...form,
                          zero_retention: zr,
                          semantic_cache_enabled: zr
                            ? false
                            : form.semantic_cache_enabled,
                        });
                      }}
                      id="policy-zero-retention"
                    />
                    <Text
                      as="label"
                      htmlFor="policy-zero-retention"
                      size="2"
                      weight="bold"
                      className="cursor-pointer flex items-center gap-1.5"
                    >
                      <EyeOff className="w-4 h-4 text-rose-400" />
                      Enforce Zero-Retention Mode
                    </Text>
                  </Flex>
                  <Text size="1" color="gray" as="p" className="pl-6">
                    Skips Postgres request/usage logs and disables all cache
                    reads &amp; writes across the tenant.
                  </Text>
                </Card>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="space-y-1.5">
                  <Text as="label" size="2" weight="bold" className="block">
                    PII Scrubber Policy
                  </Text>
                  <Select.Root
                    value={form.pii_scrubbing_mode}
                    onValueChange={(v) =>
                      setForm({
                        ...form,
                        pii_scrubbing_mode: v as PiiScrubbingMode,
                      })
                    }
                  >
                    <Select.Trigger className="w-full" />
                    <Select.Content>
                      <Select.Item value="inherit">
                        Inherit (Default / Caller Header)
                      </Select.Item>
                      <Select.Item value="enforced">
                        Enforced (Always Scrub PII)
                      </Select.Item>
                      <Select.Item value="disabled">
                        Disabled (Zero PII Scrubbing)
                      </Select.Item>
                    </Select.Content>
                  </Select.Root>
                </div>

                <div className="space-y-1.5">
                  <Text as="label" size="2" weight="bold" className="block">
                    Guardrails Enforcement
                  </Text>
                  <Select.Root
                    value={form.guardrails_mode}
                    onValueChange={(v) =>
                      setForm({
                        ...form,
                        guardrails_mode: v as GuardrailsPolicyMode,
                      })
                    }
                  >
                    <Select.Trigger className="w-full" />
                    <Select.Content>
                      <Select.Item value="inherit">
                        Inherit (Default / Caller Header)
                      </Select.Item>
                      <Select.Item value="block">
                        Block (Hard Reject Violations)
                      </Select.Item>
                      <Select.Item value="inject">
                        Inject (Soft-Steering Prompt)
                      </Select.Item>
                      <Select.Item value="off">Off</Select.Item>
                    </Select.Content>
                  </Select.Root>
                </div>

                <div className="flex items-end pb-1">
                  <Flex align="center" gap="2">
                    <Checkbox
                      checked={form.is_active}
                      onCheckedChange={(v) =>
                        setForm({ ...form, is_active: Boolean(v) })
                      }
                      id="policy-is-active"
                    />
                    <Text
                      as="label"
                      htmlFor="policy-is-active"
                      size="2"
                      weight="bold"
                      className="cursor-pointer"
                    >
                      Policy Active
                    </Text>
                  </Flex>
                </div>
              </div>

              {formError && (
                <Text size="2" className="text-red-400 block">
                  {formError}
                </Text>
              )}

              <Flex justify="end" gap="2">
                <Button
                  type="button"
                  variant="soft"
                  color="gray"
                  onClick={closeForm}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={saving}>
                  {saving ? 'Saving...' : 'Save Tenant Policy'}
                </Button>
              </Flex>
            </form>
          </Card>
        )}

        {/* Policy Table */}
        <Card size="3" className="p-0 overflow-hidden border-border/80">
          <div className="px-6 py-4 border-b border-border/60 flex items-center justify-between">
            <Text size="2" weight="bold">
              Configured Tenant Policies ({total})
            </Text>
            <Text size="1" color="gray">
              Tenants not listed follow default gateway settings (caching &amp;
              logs enabled).
            </Text>
          </div>

          {loading ? (
            <div className="p-8 text-center text-muted-foreground text-sm">
              Loading tenant policies...
            </div>
          ) : policies.length === 0 ? (
            <div className="p-10 text-center space-y-2">
              <Text size="2" color="gray" as="p">
                No per-tenant privacy restrictions configured yet.
              </Text>
              <Text size="1" color="gray" as="p">
                Click <strong>Configure Tenant</strong> above to lock down a
                tenant (stop semantic caching + enforce Zero-Retention / PII
                scrubbing) or explicitly disable PII scrubbing for maximum cache
                savings.
              </Text>
            </div>
          ) : (
            <div className="divide-y divide-border/50">
              {policies.map((p) => (
                <div
                  key={p.tenant_id}
                  className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-muted/20 transition-colors"
                >
                  <div className="space-y-1.5 min-w-0">
                    <Flex align="center" gap="2" wrap="wrap">
                      <span className="font-mono text-sm font-bold text-foreground">
                        {p.tenant_id}
                      </span>
                      {!p.is_active && (
                        <Badge color="gray" variant="soft" size="1">
                          Inactive
                        </Badge>
                      )}
                      {p.zero_retention ? (
                        <Badge color="red" variant="soft" size="1">
                          Zero-Retention (Logs OFF)
                        </Badge>
                      ) : (
                        <Badge color="blue" variant="soft" size="1">
                          Logs ON
                        </Badge>
                      )}
                      {p.semantic_cache_enabled && !p.zero_retention ? (
                        <Badge color="green" variant="soft" size="1">
                          Semantic Cache ON
                        </Badge>
                      ) : (
                        <Badge color="amber" variant="soft" size="1">
                          Semantic Cache STOPPED
                        </Badge>
                      )}
                      <Badge
                        color={
                          p.pii_scrubbing_mode === 'enforced'
                            ? 'sky'
                            : p.pii_scrubbing_mode === 'disabled'
                              ? 'gray'
                              : 'purple'
                        }
                        variant="soft"
                        size="1"
                      >
                        PII: {p.pii_scrubbing_mode}
                      </Badge>
                      {p.guardrails_mode !== 'inherit' && (
                        <Badge color="violet" variant="soft" size="1">
                          Guardrails: {p.guardrails_mode}
                        </Badge>
                      )}
                    </Flex>
                    {p.description && (
                      <Text size="1" color="gray" as="p">
                        {p.description}
                      </Text>
                    )}
                  </div>

                  <Flex gap="2" className="shrink-0">
                    <Button
                      size="1"
                      variant="soft"
                      color="gray"
                      onClick={() => openEdit(p)}
                    >
                      <Pencil className="w-3.5 h-3.5" />
                      Edit
                    </Button>
                    <Button
                      size="1"
                      variant="soft"
                      color="red"
                      onClick={() => deletePolicy(p.tenant_id)}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Reset
                    </Button>
                  </Flex>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
