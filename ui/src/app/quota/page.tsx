'use client';

import { useApiKey } from '@/context/api-key-context';
import { cn } from '@/lib/utils';
import {
  Badge,
  Button,
  Card,
  Heading,
  Select,
  Text,
  TextField,
} from '@radix-ui/themes';
import {
  AlertCircle,
  Check,
  Clock,
  Gauge,
  GaugeCircle,
  Globe,
  Pencil,
  Plus,
  RefreshCw,
  Settings,
  ShieldAlert,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { API_ROOT } from '@/lib/api';
import { useCallback, useEffect, useState } from 'react';

const API_BASE = `${API_ROOT}/api/v1/admin/llm-quota`;

// Defaults hardcoded in the gateway (llm_quota_service.check_quota). Shown so an
// operator can see what applies when no rule matches a scope.
const GATEWAY_DEFAULT_RPM = 10;
const GATEWAY_DEFAULT_TPM = 20000;

interface QuotaRule {
  id: number;
  provider: string | null;
  model: string | null;
  user_id: string | null;
  tenant_id: string | null;
  max_rpm: number | null;
  max_tpm: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface RuleListResponse {
  rules: QuotaRule[];
  total: number;
  skip: number;
  limit: number;
}

interface EffectiveLimits {
  max_rpm: number | null;
  max_tpm: number | null;
  uses_defaults: boolean;
  default_max_rpm: number;
  default_max_tpm: number;
}

interface RuleForm {
  tenant_id: string;
  user_id: string;
  provider: string;
  model: string;
  max_rpm: string;
  max_tpm: string;
  is_active: boolean;
}

const EMPTY_FORM: RuleForm = {
  tenant_id: '',
  user_id: '',
  provider: '',
  model: '',
  max_rpm: '',
  max_tpm: '',
  is_active: true,
};

const GLOBAL_SCOPE = '*';

function scopeLabel(value: string | null, wildcard: string): string {
  return value && value.trim() ? value : wildcard;
}

export default function QuotaPage() {
  const { adminKey, quotaTenant } = useApiKey();

  const [rules, setRules] = useState<QuotaRule[]>([]);
  const [total, setTotal] = useState(0);
  const [tenants, setTenants] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Filters
  const [tenantFilter, setTenantFilter] = useState<string>(GLOBAL_SCOPE);
  const [statusFilter, setStatusFilter] = useState<
    'all' | 'active' | 'inactive'
  >('all');

  // Create/edit form
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<RuleForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Effective-limits preview
  const [effective, setEffective] = useState<EffectiveLimits | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // The admin surface authenticates with X-Admin-Key (a scoped operator
  // credential), never with the tenant X-API-Key.
  const headers = useCallback((): Record<string, string> => {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (adminKey) h['X-Admin-Key'] = adminKey;
    return h;
  }, [adminKey]);

  // Loading lives in one effect (same shape as the traces page): filters,
  // credentials and refreshKey are all inputs, and mutations bump refreshKey.
  useEffect(() => {
    let ignore = false;

    const load = async () => {
      try {
        const params = new URLSearchParams();
        if (tenantFilter !== GLOBAL_SCOPE) {
          params.append('tenant_id', tenantFilter);
        }
        if (statusFilter !== 'all') {
          params.append(
            'is_active',
            statusFilter === 'active' ? 'true' : 'false'
          );
        }

        const res = await fetch(`${API_BASE}/?${params.toString()}`, {
          headers: headers(),
        });
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        const result = await res.json();
        const payload: RuleListResponse = result.response ?? result;
        if (!ignore) {
          setRules(payload.rules ?? []);
          setTotal(payload.total ?? 0);
          setError(null);
        }
      } catch (e) {
        if (!ignore) {
          setError(
            e instanceof Error ? e.message : 'An unknown error occurred'
          );
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }

      // Tenant list only feeds the filter dropdown; failing it must not break
      // the page, so it is fetched separately and never surfaces an error.
      try {
        const res = await fetch(`${API_BASE}/tenants`, { headers: headers() });
        if (res.ok) {
          const result = await res.json();
          if (!ignore) {
            setTenants(result.response ?? []);
          }
        }
      } catch {
        // ignore
      }
    };

    load();
    return () => {
      ignore = true;
    };
  }, [tenantFilter, statusFilter, adminKey, refreshKey, headers]);

  const previewEffective = useCallback(
    async (tenant: string) => {
      if (!tenant) {
        setEffective(null);
        return;
      }
      setPreviewing(true);
      try {
        const params = new URLSearchParams({ tenant_id: tenant });
        const res = await fetch(`${API_BASE}/effective?${params.toString()}`, {
          headers: headers(),
        });
        if (!res.ok) {
          setEffective(null);
          return;
        }
        const result = await res.json();
        setEffective(result.response ?? null);
      } catch {
        setEffective(null);
      } finally {
        setPreviewing(false);
      }
    },
    [headers]
  );

  // Handlers that reload: clear transient banners, show the spinner, refetch.
  const refresh = () => {
    setNotice(null);
    setError(null);
    setLoading(true);
    setRefreshKey((k) => k + 1);
  };

  // The admin surface fails closed, so an unconfigured key is the first-run
  // state, not an error. Explain it rather than showing a bare 401.
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
            <Heading size="5">Admin API Key required</Heading>
            <Text color="gray" size="2">
              This page manages quota rules across every tenant, which is an
              admin operation. It authenticates with a scoped{' '}
              <code className="font-mono text-[11px] bg-muted/60 px-1 py-0.5 rounded">
                X-Admin-Key
              </code>{' '}
              — your tenant API key will not work here.
            </Text>
            <Card
              size="1"
              className="p-4 bg-muted/30 border-border/60 text-left"
            >
              <Text size="1" color="gray" as="p">
                Mint a key on the machine running the gateway:
              </Text>
              <pre className="mt-2 p-3 rounded-lg bg-muted/60 text-[11px] font-mono overflow-x-auto">
                {`python -m app.scripts.create_admin_key create \\
  --name ops --scopes quota:read,quota:write`}
              </pre>
              <Text size="1" color="gray" as="p">
                The plaintext is shown once. Paste it into Settings → Admin API
                Key.
              </Text>
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
    setEditingId(null);
    setForm({ ...EMPTY_FORM, tenant_id: quotaTenant || '' });
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (rule: QuotaRule) => {
    setEditingId(rule.id);
    setForm({
      tenant_id: rule.tenant_id ?? '',
      user_id: rule.user_id ?? '',
      provider: rule.provider ?? '',
      model: rule.model ?? '',
      max_rpm: rule.max_rpm != null ? String(rule.max_rpm) : '',
      max_tpm: rule.max_tpm != null ? String(rule.max_tpm) : '',
      is_active: rule.is_active,
    });
    setFormError(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditingId(null);
    setFormError(null);
  };

  const saveRule = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setFormError(null);

    const maxRpm = form.max_rpm.trim() ? Number(form.max_rpm) : null;
    const maxTpm = form.max_tpm.trim() ? Number(form.max_tpm) : null;

    if (maxRpm === null && maxTpm === null) {
      setFormError('Set at least one of RPM or TPM.');
      setSaving(false);
      return;
    }
    if (
      (maxRpm !== null && (!Number.isInteger(maxRpm) || maxRpm < 1)) ||
      (maxTpm !== null && (!Number.isInteger(maxTpm) || maxTpm < 1))
    ) {
      setFormError('Limits must be whole numbers of 1 or more.');
      setSaving(false);
      return;
    }

    const trimOrNull = (v: string) => (v.trim() ? v.trim() : null);
    const body: Record<string, unknown> = {
      provider: trimOrNull(form.provider),
      model: trimOrNull(form.model),
      user_id: trimOrNull(form.user_id),
      tenant_id: trimOrNull(form.tenant_id),
      max_rpm: maxRpm,
      max_tpm: maxTpm,
      is_active: form.is_active,
    };

    try {
      const res = await fetch(
        editingId === null ? `${API_BASE}/` : `${API_BASE}/${editingId}`,
        {
          method: editingId === null ? 'POST' : 'PATCH',
          headers: headers(),
          body: JSON.stringify(body),
        }
      );

      if (res.status === 404) {
        setFormError('That rule no longer exists. Refresh the list.');
        return;
      }
      if (res.status === 422) {
        const detail = await res.json().catch(() => null);
        setFormError(
          detail?.detail?.[0]?.msg ?? 'The gateway rejected this rule.'
        );
        return;
      }
      if (!res.ok) {
        throw new Error(`HTTP error! status: ${res.status}`);
      }

      setNotice(
        editingId === null ? 'Quota rule created.' : 'Quota rule updated.'
      );
      closeForm();
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : 'An unknown error occurred'
      );
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (rule: QuotaRule) => {
    setNotice(null);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/${rule.id}`, {
        method: 'PATCH',
        headers: headers(),
        body: JSON.stringify({ is_active: !rule.is_active }),
      });
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
      setNotice(
        `Rule #${rule.id} ${rule.is_active ? 'deactivated' : 'activated'}.`
      );
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An unknown error occurred');
    }
  };

  const deleteRule = async (rule: QuotaRule) => {
    setNotice(null);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/${rule.id}`, {
        method: 'DELETE',
        headers: headers(),
      });
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
      setNotice(`Rule #${rule.id} deleted.`);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An unknown error occurred');
    }
  };

  const updateField = (field: keyof RuleForm, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-10 bg-background">
      <div className="max-w-6xl mx-auto space-y-6">
        {/* Page Header */}
        <div className="border-b border-border/60 pb-6">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
                <GaugeCircle className="w-6 h-6" />
              </div>
              <div>
                <Heading size="7" className="tracking-tight">
                  Quota Management
                </Heading>
                <Text color="gray" size="2" mt="1">
                  Per-tenant RPM and TPM limits enforced by{' '}
                  <code className="font-mono text-[11px] bg-muted/60 px-1 py-0.5 rounded">
                    llm_quota_guard
                  </code>{' '}
                  on every chat completion.
                </Text>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="soft"
                color="gray"
                size="2"
                onClick={refresh}
                disabled={loading}
              >
                <RefreshCw
                  className={cn('w-4 h-4', loading && 'animate-spin')}
                />
                Refresh
              </Button>
              <Button
                color="blue"
                variant="solid"
                size="2"
                onClick={openCreate}
              >
                <Plus className="w-4 h-4" /> New Rule
              </Button>
            </div>
          </div>
        </div>

        {/* Effective limits preview */}
        <Card size="2" className="space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <Gauge className="w-5 h-5 text-primary" />
              <Heading size="3">Effective Limits Check</Heading>
              <Text size="1" color="gray">
                Resolve which rules actually win for a tenant.
              </Text>
            </div>
            <div className="flex items-center gap-2">
              <Select.Root
                value={tenantFilter}
                onValueChange={(v) => {
                  setTenantFilter(v);
                  previewEffective(v);
                }}
              >
                <Select.Trigger className="min-w-[180px]" />
                <Select.Content>
                  <Select.Item value={GLOBAL_SCOPE}>All tenants</Select.Item>
                  {(tenants.includes(quotaTenant)
                    ? tenants
                    : [quotaTenant, ...tenants]
                  )
                    .filter((t, i, arr) => t && arr.indexOf(t) === i)
                    .map((t) => (
                      <Select.Item key={t} value={t}>
                        {t}
                      </Select.Item>
                    ))}
                </Select.Content>
              </Select.Root>
            </div>
          </div>

          {previewing ? (
            <Text size="1" color="gray">
              Resolving...
            </Text>
          ) : effective ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="p-3 rounded-xl bg-muted/30 border border-border/50">
                <Text size="1" color="gray" weight="medium">
                  Effective RPM
                </Text>
                <div className="text-xl font-bold text-blue-500 mt-0.5">
                  {effective.max_rpm ?? effective.default_max_rpm}
                </div>
              </div>
              <div className="p-3 rounded-xl bg-muted/30 border border-border/50">
                <Text size="1" color="gray" weight="medium">
                  Effective TPM
                </Text>
                <div className="text-xl font-bold text-purple-500 mt-0.5">
                  {effective.max_tpm ?? effective.default_max_tpm}
                </div>
              </div>
              <div className="p-3 rounded-xl bg-muted/30 border border-border/50 col-span-2 flex items-center">
                {effective.uses_defaults ? (
                  <Text size="1" color="amber">
                    No rule matches this scope — the gateway falls back to its
                    built-in defaults ({GATEWAY_DEFAULT_RPM} RPM /{' '}
                    {GATEWAY_DEFAULT_TPM.toLocaleString()} TPM).
                  </Text>
                ) : (
                  <Text size="1" color="green">
                    The most restrictive matching active rule wins.
                  </Text>
                )}
              </div>
            </div>
          ) : (
            <Text size="1" color="gray">
              Select a tenant to preview the limits that apply to it.
            </Text>
          )}
        </Card>

        {/* Create / Edit form */}
        {formOpen && (
          <Card size="2" className="space-y-4 border-primary/30">
            <div className="flex items-center justify-between">
              <Heading size="3">
                {editingId === null
                  ? 'New Quota Rule'
                  : `Edit Rule #${editingId}`}
              </Heading>
              <button
                onClick={closeForm}
                className="text-muted-foreground hover:text-foreground transition-colors"
                aria-label="Close form"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={saveRule} className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                    <Users className="w-3.5 h-3.5" /> Tenant ID
                  </label>
                  <TextField.Root
                    value={form.tenant_id}
                    onChange={(e) => updateField('tenant_id', e.target.value)}
                    placeholder="Leave blank for global"
                    className="font-mono text-sm"
                  />
                  <Text size="1" color="gray">
                    Blank applies the rule to every tenant.
                  </Text>
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                    <Users className="w-3.5 h-3.5" /> User ID
                  </label>
                  <TextField.Root
                    value={form.user_id}
                    onChange={(e) => updateField('user_id', e.target.value)}
                    placeholder="Leave blank for all users"
                    className="font-mono text-sm"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Provider
                  </label>
                  <TextField.Root
                    value={form.provider}
                    onChange={(e) => updateField('provider', e.target.value)}
                    placeholder="e.g. openai (blank = all)"
                    className="font-mono text-sm"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Model
                  </label>
                  <TextField.Root
                    value={form.model}
                    onChange={(e) => updateField('model', e.target.value)}
                    placeholder="e.g. gpt-4o-mini (blank = all)"
                    className="font-mono text-sm"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Max RPM
                  </label>
                  <TextField.Root
                    type="number"
                    min="1"
                    value={form.max_rpm}
                    onChange={(e) => updateField('max_rpm', e.target.value)}
                    placeholder="Requests per minute"
                    className="font-mono text-sm"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Max TPM
                  </label>
                  <TextField.Root
                    type="number"
                    min="1"
                    value={form.max_tpm}
                    onChange={(e) => updateField('max_tpm', e.target.value)}
                    placeholder="Tokens per minute"
                    className="font-mono text-sm"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => updateField('is_active', !form.is_active)}
                  className={cn(
                    'px-2.5 py-1 rounded-lg text-xs font-medium border transition-all',
                    form.is_active
                      ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-500'
                      : 'border-border/60 text-muted-foreground hover:text-foreground'
                  )}
                >
                  Active: {form.is_active ? 'Yes' : 'No'}
                </button>
                <Text size="1" color="gray">
                  Inactive rules are ignored by the quota guard.
                </Text>
              </div>

              {formError && (
                <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-500 text-xs">
                  <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                  <span>{formError}</span>
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-border/50">
                <Button
                  type="button"
                  variant="soft"
                  color="gray"
                  onClick={closeForm}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  color="blue"
                  variant="solid"
                  disabled={saving}
                >
                  {saving ? (
                    'Saving...'
                  ) : (
                    <>
                      <Check className="w-4 h-4" />
                      {editingId === null ? 'Create Rule' : 'Save Changes'}
                    </>
                  )}
                </Button>
              </div>
            </form>
          </Card>
        )}

        {/* Notices */}
        {notice && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 text-xs">
            <Check className="w-4 h-4" />
            {notice}
          </div>
        )}
        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-500 text-xs">
            <AlertCircle className="w-4 h-4" />
            {error}
          </div>
        )}

        {/* Filters */}
        <Card size="2" className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase font-semibold text-muted-foreground">
              Status
            </span>
            <div className="flex items-center gap-1 bg-muted/50 p-1 rounded-lg border border-border/50">
              {(['all', 'active', 'inactive'] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={cn(
                    'px-2 py-0.5 rounded text-[11px] font-medium transition-all capitalize',
                    statusFilter === s
                      ? 'bg-background text-foreground shadow-xs font-semibold'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          <Text size="1" color="gray">
            {total} rule{total === 1 ? '' : 's'} matching
          </Text>
        </Card>

        {/* Rules table */}
        <Card size="2" className="p-0 overflow-hidden border-border/80">
          {loading && !rules.length ? (
            <div className="py-20 flex flex-col items-center justify-center text-muted-foreground gap-2">
              <Clock className="w-6 h-6 animate-spin" />
              <div className="text-sm">Loading quota rules...</div>
            </div>
          ) : error && !rules.length ? (
            <div className="py-16 text-center space-y-2">
              <div className="text-sm font-medium text-red-500">
                Failed to load quota rules
              </div>
              <div className="text-xs text-red-400">{error}</div>
              <button
                onClick={refresh}
                className="mt-2 px-3 py-1 bg-primary text-primary-foreground text-xs rounded-md"
              >
                Retry
              </button>
            </div>
          ) : rules.length === 0 ? (
            <div className="py-20 flex flex-col items-center justify-center text-muted-foreground space-y-2">
              <Gauge className="w-8 h-8 opacity-40 mb-1" />
              <div className="font-semibold text-foreground">
                No quota rules found
              </div>
              <div className="text-xs text-muted-foreground max-w-sm text-center">
                Without a rule, the gateway falls back to its built-in defaults
                of {GATEWAY_DEFAULT_RPM} RPM and{' '}
                {GATEWAY_DEFAULT_TPM.toLocaleString()} TPM.
              </div>
              <button
                onClick={openCreate}
                className="mt-3 px-3 py-1.5 bg-primary text-primary-foreground text-xs font-medium rounded-lg hover:opacity-90"
              >
                Create your first rule
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border/60 bg-muted/30 text-muted-foreground uppercase tracking-wider font-semibold text-[10px]">
                    <th className="py-3 px-4">ID</th>
                    <th className="py-3 px-4">Scope</th>
                    <th className="py-3 px-4">Max RPM</th>
                    <th className="py-3 px-4">Max TPM</th>
                    <th className="py-3 px-4">Status</th>
                    <th className="py-3 px-4">Updated</th>
                    <th className="py-3 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40 font-normal">
                  {rules.map((rule) => (
                    <tr
                      key={rule.id}
                      className={cn(
                        'hover:bg-muted/40 transition-colors',
                        !rule.is_active && 'opacity-60'
                      )}
                    >
                      <td className="py-3.5 px-4 font-mono text-[11px] text-muted-foreground">
                        {rule.id}
                      </td>

                      {/* Scope */}
                      <td className="py-3.5 px-4">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-mono text-[11px] text-foreground">
                            tenant:{' '}
                            {rule.tenant_id ? (
                              rule.tenant_id
                            ) : (
                              <span className="text-muted-foreground italic">
                                global
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="font-mono text-[10px] text-muted-foreground mt-0.5">
                          user: {scopeLabel(rule.user_id, GLOBAL_SCOPE)} ·
                          provider: {scopeLabel(rule.provider, GLOBAL_SCOPE)} ·
                          model: {scopeLabel(rule.model, GLOBAL_SCOPE)}
                        </div>
                      </td>

                      <td className="py-3.5 px-4 font-mono text-[11px] text-blue-500">
                        {rule.max_rpm ?? (
                          <span className="text-muted-foreground italic">
                            n/a
                          </span>
                        )}
                      </td>

                      <td className="py-3.5 px-4 font-mono text-[11px] text-purple-500">
                        {rule.max_tpm ? (
                          rule.max_tpm.toLocaleString()
                        ) : (
                          <span className="text-muted-foreground italic">
                            n/a
                          </span>
                        )}
                      </td>

                      <td className="py-3.5 px-4">
                        {rule.is_active ? (
                          <Badge color="green" variant="soft" size="1">
                            Active
                          </Badge>
                        ) : (
                          <Badge color="gray" variant="soft" size="1">
                            Inactive
                          </Badge>
                        )}
                      </td>

                      <td className="py-3.5 px-4 text-muted-foreground font-mono text-[10px] whitespace-nowrap">
                        {new Date(rule.updated_at).toLocaleDateString([], {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </td>

                      <td className="py-3.5 px-4">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() =>
                              previewEffective(rule.tenant_id ?? '')
                            }
                            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                            title="Show effective limits for this tenant"
                          >
                            <Globe className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => toggleActive(rule)}
                            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                            title={rule.is_active ? 'Deactivate' : 'Activate'}
                          >
                            <Check className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => openEdit(rule)}
                            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                            title="Edit rule"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => deleteRule(rule)}
                            className="p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-muted transition-colors"
                            title="Delete rule"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
