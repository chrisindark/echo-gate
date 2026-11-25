'use client';

import { useApiKey } from '@/context/api-key-context';
import { useAuth } from '@/context/auth-context';
import { API_ROOT, isLocalGatewayUrl } from '@/lib/api';
import {
  ApiKeyCreated,
  ApiKeyItem,
  createUserApiKey,
  listUserApiKeys,
  revokeUserApiKey,
  rotateUserApiKey,
} from '@/lib/user-api';
import {
  Badge,
  Button,
  Card,
  Dialog,
  Heading,
  Text,
  TextField,
} from '@radix-ui/themes';
import {
  AlertTriangle,
  Building2,
  Check,
  Copy,
  Key,
  KeyRound,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import React, { useEffect, useState } from 'react';

export function ApiKeysManager() {
  const { accessToken, tenantId } = useAuth();
  const { apiKey, setApiKey } = useApiKey();

  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(() =>
    Boolean(accessToken)
  );
  const [error, setError] = useState<string | null>(null);

  // Create Key Modal State
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  // One-time Plaintext Key Display Modal State
  const [revealedKey, setRevealedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  // Rotation State
  const [rotatingKeyId, setRotatingKeyId] = useState<number | null>(null);
  const graceHours = 24;

  const refreshKeys = async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await listUserApiKeys(accessToken);
      setKeys(data);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to fetch API keys.'
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!accessToken) return;
    let isMounted = true;
    listUserApiKeys(accessToken)
      .then((data) => {
        if (isMounted) {
          setKeys(data);
          setIsLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (isMounted) {
          setError(
            err instanceof Error ? err.message : 'Failed to fetch API keys.'
          );
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [accessToken]);

  const handleCreateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken || !newKeyName.trim()) return;

    setIsCreating(true);
    setError(null);
    try {
      const created = await createUserApiKey(accessToken, newKeyName.trim());
      setIsCreateOpen(false);
      setNewKeyName('');
      setRevealedKey(created);
      // Automatically set as active key in local playground if none set
      if (!apiKey) {
        setApiKey(created.api_key);
      }
      await refreshKeys();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'Failed to create API key.'
      );
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevokeKey = async (keyId: number) => {
    if (!accessToken) return;
    if (
      !confirm(
        'Are you sure you want to revoke this API key? Applications using it will immediately be rejected.'
      )
    ) {
      return;
    }

    try {
      await revokeUserApiKey(accessToken, keyId);
      await refreshKeys();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to revoke key.');
    }
  };

  const handleRotateKey = async (keyId: number) => {
    if (!accessToken) return;
    setRotatingKeyId(keyId);
    try {
      const result = await rotateUserApiKey(accessToken, keyId, graceHours);
      setRevealedKey(result.new_key);
      await refreshKeys();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to rotate key.');
    } finally {
      setRotatingKeyId(null);
    }
  };

  const handleCopyRevealedKey = () => {
    if (!revealedKey) return;
    navigator.clipboard.writeText(revealedKey.api_key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const formatDate = (isoString?: string | null) => {
    if (!isoString) return '—';
    try {
      return new Date(isoString).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  return (
    <Card
      size="3"
      className="relative overflow-hidden p-6 sm:p-7 border-border/80 bg-gradient-to-br from-card via-card to-primary/[0.03] space-y-6 shadow-xs"
    >
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-5 border-b border-border/50">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="p-2 rounded-xl bg-primary/10 text-primary border border-primary/20 shrink-0">
              <Building2 className="w-5 h-5" />
            </div>
            <Heading
              size="4"
              className="tracking-tight font-semibold text-foreground"
            >
              Production Tenant API Keys
            </Heading>
            <Badge
              color="blue"
              variant="surface"
              size="1"
              className="font-mono text-[10px] tracking-wide"
            >
              {tenantId || 'Personal Tenant'}
            </Badge>
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              RLS Active
            </span>
          </div>
          <Text size="2" color="gray" className="leading-relaxed">
            Keys belong strictly to your isolated tenant. Generate credentials
            for your applications and client SDKs.
          </Text>
        </div>

        <Dialog.Root open={isCreateOpen} onOpenChange={setIsCreateOpen}>
          <Dialog.Trigger>
            <button
              type="button"
              className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-white whitespace-nowrap bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-md shadow-blue-500/20 hover:shadow-lg hover:shadow-blue-500/30 active:scale-[0.98] transition-all duration-200 cursor-pointer shrink-0"
            >
              <Plus className="w-4 h-4 shrink-0" />
              <span>Generate New Key</span>
            </button>
          </Dialog.Trigger>

          <Dialog.Content maxWidth="450px">
            <Dialog.Title>Generate API Key</Dialog.Title>
            <Dialog.Description size="2" mb="4">
              Enter a name to identify this key (e.g. &apos;Production App&apos;
              or &apos;Local Dev&apos;).
            </Dialog.Description>

            <form onSubmit={handleCreateKey} className="space-y-4">
              <TextField.Root
                required
                autoFocus
                placeholder="Key label, e.g. Backend Service"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
              />

              <div className="flex justify-end gap-3 pt-2">
                <Dialog.Close>
                  <Button
                    variant="soft"
                    color="gray"
                    className="cursor-pointer"
                  >
                    Cancel
                  </Button>
                </Dialog.Close>
                <button
                  type="submit"
                  disabled={isCreating || !newKeyName.trim()}
                  className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 shadow-sm shadow-blue-500/20 disabled:opacity-50 transition-all active:scale-[0.98] cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>{isCreating ? 'Generating...' : 'Generate Key'}</span>
                </button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Root>
      </div>

      {error && (
        <div className="p-3.5 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs space-y-1.5">
          <div className="flex items-center gap-2 font-medium">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
          {(error.toLowerCase().includes('fetch') || isLocalGatewayUrl) && (
            <p className="text-[11px] text-muted-foreground leading-relaxed pl-6">
              Unable to reach the Echo Gate backend at{' '}
              <code className="font-mono bg-muted px-1 py-0.5 rounded text-foreground">
                {API_ROOT}
              </code>
              . When deploying the frontend first to Vercel, set{' '}
              <code className="font-mono bg-muted px-1 py-0.5 rounded text-foreground">
                NEXT_PUBLIC_API_BASE_URL
              </code>{' '}
              to your live HTTPS gateway URL, allow this origin in backend{' '}
              <code className="font-mono bg-muted px-1 py-0.5 rounded text-foreground">
                CORS_ORIGINS
              </code>
              , and enable{' '}
              <code className="font-mono bg-muted px-1 py-0.5 rounded text-foreground">
                SUPABASE_AUTH_ENABLED=true
              </code>{' '}
              on the gateway.
            </p>
          )}
        </div>
      )}

      {/* Keys List Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="border-b border-border/60 text-muted-foreground uppercase tracking-wider text-[11px]">
              <th className="py-2.5 px-3">Name</th>
              <th className="py-2.5 px-3">Prefix</th>
              <th className="py-2.5 px-3">Status</th>
              <th className="py-2.5 px-3">Created</th>
              <th className="py-2.5 px-3">Expires</th>
              <th className="py-2.5 px-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40 font-mono">
            {keys.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="py-10 text-center text-muted-foreground font-sans"
                >
                  {isLoading ? (
                    <div className="flex items-center justify-center gap-2 text-xs">
                      <RefreshCw className="w-4 h-4 animate-spin text-primary" />
                      <span>Loading keys...</span>
                    </div>
                  ) : (
                    <div className="space-y-3 py-2">
                      <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary border border-primary/20 flex items-center justify-center mx-auto">
                        <KeyRound className="w-5 h-5" />
                      </div>
                      <p className="text-xs text-muted-foreground">
                        No API keys generated for this tenant yet.
                      </p>
                      <button
                        type="button"
                        onClick={() => setIsCreateOpen(true)}
                        className="inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 shadow-sm shadow-blue-500/20 transition-all active:scale-[0.98] cursor-pointer"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        <span>Create your first key</span>
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ) : (
              keys.map((k) => {
                const isCurrentlyActiveInApp = apiKey.startsWith(k.key_prefix);
                return (
                  <tr
                    key={k.id}
                    className="hover:bg-muted/30 transition-colors"
                  >
                    <td className="py-3 px-3 font-sans font-medium text-foreground">
                      <div className="flex items-center gap-2">
                        <span>{k.name}</span>
                        {isCurrentlyActiveInApp && (
                          <Badge size="1" color="green" variant="surface">
                            Active in Browser
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-muted-foreground">
                      <code>{k.key_prefix}••••••••</code>
                    </td>
                    <td className="py-3 px-3">
                      {k.is_active ? (
                        <Badge size="1" color="green" variant="soft">
                          Active
                        </Badge>
                      ) : (
                        <Badge size="1" color="gray" variant="soft">
                          Revoked
                        </Badge>
                      )}
                    </td>
                    <td className="py-3 px-3 text-muted-foreground font-sans">
                      {formatDate(k.created_at)}
                    </td>
                    <td className="py-3 px-3 text-muted-foreground font-sans">
                      {formatDate(k.expires_at)}
                    </td>
                    <td className="py-3 px-3 text-right space-x-2 font-sans">
                      {k.is_active && (
                        <>
                          <button
                            type="button"
                            disabled={rotatingKeyId === k.id}
                            onClick={() => handleRotateKey(k.id)}
                            title="Rotate Key (24h dual-validity grace period)"
                            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border/80 bg-card hover:bg-muted text-muted-foreground hover:text-foreground text-xs font-medium transition-all shadow-2xs active:scale-[0.97] cursor-pointer disabled:opacity-50"
                          >
                            <RefreshCw
                              className={`w-3.5 h-3.5 ${
                                rotatingKeyId === k.id ? 'animate-spin' : ''
                              }`}
                            />
                            <span className="hidden sm:inline">Rotate</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleRevokeKey(k.id)}
                            title="Revoke Key"
                            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-destructive/20 bg-destructive/5 hover:bg-destructive/15 text-destructive text-xs font-medium transition-all shadow-2xs active:scale-[0.97] cursor-pointer"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                            <span className="hidden sm:inline">Revoke</span>
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* One-Time Key Reveal Modal */}
      <Dialog.Root
        open={Boolean(revealedKey)}
        onOpenChange={(open) => !open && setRevealedKey(null)}
      >
        <Dialog.Content maxWidth="520px">
          <div className="flex items-center gap-2 mb-2 text-emerald-500">
            <Key className="w-5 h-5" />
            <Dialog.Title>API Key Generated</Dialog.Title>
          </div>
          <Dialog.Description size="2" mb="4">
            Copy this key now. For your security,{' '}
            <strong>it will never be displayed again</strong>.
          </Dialog.Description>

          {revealedKey && (
            <div className="space-y-4">
              <div className="p-3 bg-muted/60 border border-border rounded-lg flex items-center justify-between gap-2">
                <code className="text-xs font-mono break-all text-emerald-400 select-all">
                  {revealedKey.api_key}
                </code>
                <button
                  type="button"
                  onClick={handleCopyRevealedKey}
                  className={`inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold text-white transition-all active:scale-[0.98] cursor-pointer shrink-0 ${
                    copied
                      ? 'bg-emerald-600 hover:bg-emerald-500 shadow-sm shadow-emerald-500/20'
                      : 'bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 shadow-sm shadow-blue-500/20'
                  }`}
                >
                  {copied ? (
                    <>
                      <Check className="w-3.5 h-3.5" />
                      <span>Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3.5 h-3.5" />
                      <span>Copy Key</span>
                    </>
                  )}
                </button>
              </div>

              <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-200 text-xs flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span>
                  Store this key safely in your environment variables. If you
                  lose it, you must rotate or generate a replacement.
                </span>
              </div>

              <div className="flex flex-col sm:flex-row justify-between items-stretch sm:items-center gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    setApiKey(revealedKey.api_key);
                    alert(
                      'Key set as active gateway credential for your browser session!'
                    );
                  }}
                  className="inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold text-primary bg-primary/10 hover:bg-primary/15 border border-primary/20 transition-all active:scale-[0.98] cursor-pointer"
                >
                  <Key className="w-3.5 h-3.5" />
                  <span>Use as Active Key in Playground</span>
                </button>
                <Dialog.Close>
                  <Button
                    variant="solid"
                    color="gray"
                    className="cursor-pointer"
                  >
                    Done
                  </Button>
                </Dialog.Close>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Root>
    </Card>
  );
}
