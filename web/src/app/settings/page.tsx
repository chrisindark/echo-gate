'use client';

import { ApiKeysManager } from '@/components/api-keys-manager';
import { useApiKey } from '@/context/api-key-context';
import { useAuth } from '@/context/auth-context';
import { API_ROOT } from '@/lib/api';
import {
  Badge,
  Button,
  Card,
  Heading,
  Text,
  TextField,
} from '@radix-ui/themes';
import {
  ArrowRight,
  Building2,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Eye,
  EyeOff,
  GaugeCircle,
  Key,
  KeyRound,
  Lock,
  LogIn,
  LogOut,
  RefreshCw,
  Save,
  Server,
  Shield,
  ShieldCheck,
  UserPlus,
} from 'lucide-react';
import Link from 'next/link';
import React, { useState } from 'react';

export default function SettingsPage() {
  const { user, tenantId, signOut, isConfigured } = useAuth();
  const {
    apiKey,
    setApiKey,
    adminKey,
    setAdminKey,
    quotaTenant,
    setQuotaTenant,
  } = useApiKey();

  const [inputKey, setInputKey] = useState(apiKey);
  const [inputAdminKey, setInputAdminKey] = useState(adminKey);
  const [inputQuotaTenant, setInputQuotaTenant] = useState(quotaTenant);
  const [showKey, setShowKey] = useState(false);
  const [showAdminKey, setShowAdminKey] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showManualCredentials, setShowManualCredentials] = useState(!user);

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    setApiKey(inputKey.trim());
    setAdminKey(inputAdminKey.trim());
    setQuotaTenant(inputQuotaTenant.trim());
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const handleCopy = () => {
    if (!inputKey) return;
    navigator.clipboard.writeText(inputKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-10 bg-background">
      <div className="max-w-4xl mx-auto space-y-8">
        {/* Page Header */}
        <div className="border-b border-border/60 pb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <Key className="w-6 h-6" />
            </div>
            <div>
              <Heading size="7" className="tracking-tight">
                Gateway Settings
              </Heading>
              <Text color="gray" size="2" mt="1">
                Manage your credentials, self-service tenant API keys, and
                operator controls.
              </Text>
            </div>
          </div>

          {user && (
            <div className="flex items-center gap-3 bg-muted/40 p-2 px-3 rounded-xl border border-border">
              <div className="text-right text-xs">
                <div className="font-medium text-foreground">{user.email}</div>
                <div className="text-muted-foreground font-mono text-[11px]">
                  Tenant: {tenantId}
                </div>
              </div>
              <Button
                size="1"
                variant="soft"
                color="gray"
                onClick={() => signOut()}
                title="Sign out"
              >
                <LogOut className="w-3.5 h-3.5" />
              </Button>
            </div>
          )}
        </div>

        {/* Supabase SaaS Logged-In User View */}
        {user ? (
          <ApiKeysManager />
        ) : isConfigured ? (
          <div className="relative overflow-hidden rounded-2xl border border-border/80 bg-gradient-to-br from-card via-card to-primary/[0.04] p-6 sm:p-7 shadow-xs space-y-6">
            {/* Ambient decorative background glow */}
            <div
              className="pointer-events-none absolute -top-24 -right-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl opacity-70"
              aria-hidden="true"
            />

            <div className="relative flex flex-col lg:flex-row lg:items-center justify-between gap-5">
              <div className="flex items-start gap-4">
                <div className="p-3 rounded-2xl bg-primary/10 text-primary border border-primary/20 shadow-xs shrink-0 flex items-center justify-center">
                  <Building2 className="w-5 h-5 text-primary" />
                </div>
                <div className="space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Heading size="4" className="tracking-tight font-semibold text-foreground">
                      Production Tenant Management
                    </Heading>
                    <Badge color="blue" variant="surface" size="1" className="font-mono text-[10px] tracking-wider uppercase">
                      Cloud SaaS
                    </Badge>
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                      Automated Isolation
                    </span>
                  </div>
                  <Text size="2" color="gray" className="leading-relaxed max-w-2xl">
                    Authenticate to automatically provision an isolated personal tenant, generate cryptographically hashed API keys with one-click copy, and manage zero-downtime key rotations with automated grace periods.
                  </Text>
                </div>
              </div>

              {/* Aesthetic Action Buttons */}
              <div className="flex flex-wrap items-center gap-3 shrink-0 sm:self-start lg:self-center">
                <Link href="/login" className="group shrink-0">
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-white whitespace-nowrap bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-md shadow-blue-500/25 hover:shadow-lg hover:shadow-blue-500/35 active:scale-[0.98] transition-all duration-200 cursor-pointer"
                  >
                    <LogIn className="w-4 h-4 shrink-0" />
                    <span>Sign in</span>
                    <ArrowRight className="w-3.5 h-3.5 transition-transform duration-200 group-hover:translate-x-0.5 shrink-0" />
                  </button>
                </Link>
                <Link href="/signup" className="group shrink-0">
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-foreground whitespace-nowrap bg-card hover:bg-muted/80 border border-border/90 hover:border-primary/40 shadow-xs hover:shadow-sm active:scale-[0.98] transition-all duration-200 cursor-pointer"
                  >
                    <UserPlus className="w-4 h-4 text-primary shrink-0" />
                    <span>Create Account</span>
                  </button>
                </Link>
              </div>
            </div>

            {/* Feature Highlights Grid */}
            <div className="relative grid grid-cols-1 md:grid-cols-3 gap-3 pt-4 border-t border-border/50">
              <div className="flex items-start gap-3 p-3.5 rounded-xl bg-muted/30 border border-border/50 text-xs transition-colors hover:bg-muted/40">
                <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
                  <ShieldCheck className="w-4 h-4" />
                </div>
                <div className="space-y-0.5 min-w-0">
                  <div className="font-medium text-foreground">Postgres RLS Isolation</div>
                  <div className="text-[11px] text-muted-foreground leading-snug">Strict tenant schema isolation for traces, semantic cache, and quota rules.</div>
                </div>
              </div>

              <div className="flex items-start gap-3 p-3.5 rounded-xl bg-muted/30 border border-border/50 text-xs transition-colors hover:bg-muted/40">
                <div className="p-1.5 rounded-lg bg-blue-500/10 text-blue-500 shrink-0">
                  <KeyRound className="w-4 h-4" />
                </div>
                <div className="space-y-0.5 min-w-0">
                  <div className="font-medium text-foreground">Instant Scoped API Keys</div>
                  <div className="text-[11px] text-muted-foreground leading-snug">Generate <code className="font-mono text-[10px] bg-muted/60 px-1 py-0.5 rounded">eg_...</code> keys with one-click clipboard copy and auto-activation.</div>
                </div>
              </div>

              <div className="flex items-start gap-3 p-3.5 rounded-xl bg-muted/30 border border-border/50 text-xs transition-colors hover:bg-muted/40">
                <div className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-500 shrink-0">
                  <RefreshCw className="w-4 h-4" />
                </div>
                <div className="space-y-0.5 min-w-0">
                  <div className="font-medium text-foreground">Zero-Downtime Rotation</div>
                  <div className="text-[11px] text-muted-foreground leading-snug">Seamless credential rotation with 24h dual-validity grace periods for client SDKs.</div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="relative overflow-hidden rounded-2xl border border-border/80 bg-gradient-to-br from-card via-card to-amber-500/[0.03] p-6 shadow-xs">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div className="flex items-start gap-4">
                <div className="p-3 rounded-2xl bg-amber-500/10 text-amber-500 border border-amber-500/20 shadow-xs shrink-0 flex items-center justify-center">
                  <Building2 className="w-5 h-5" />
                </div>
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Heading size="4" className="tracking-tight font-semibold text-foreground">
                      Production Tenant Management
                    </Heading>
                    <Badge color="amber" variant="surface" size="1">
                      Local / Standalone Mode
                    </Badge>
                  </div>
                  <Text size="2" color="gray" className="leading-relaxed">
                    Cloud multi-tenant authentication is optional. To enable self-service signups and personal tenant keys, provide <code className="font-mono text-xs bg-muted/60 px-1 py-0.5 rounded">NEXT_PUBLIC_SUPABASE_URL</code> in your environment, or continue using the manual browser authentication below.
                  </Text>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Credentials Form Card */}
        <div className="space-y-4">
          {user && (
            <div className="flex items-center justify-between">
              <Text size="2" weight="medium" color="gray">
                Manual & Operator Credentials
              </Text>
              <Button
                variant="ghost"
                size="1"
                color="gray"
                onClick={() => setShowManualCredentials(!showManualCredentials)}
              >
                {showManualCredentials ? (
                  <>
                    Hide Details <ChevronUp className="w-3.5 h-3.5 ml-1" />
                  </>
                ) : (
                  <>
                    Show Details <ChevronDown className="w-3.5 h-3.5 ml-1" />
                  </>
                )}
              </Button>
            </div>
          )}

          {(!user || showManualCredentials) && (
            <form onSubmit={handleSave} className="space-y-6">
              <Card size="3" className="p-6 border-border/80 space-y-6">
                <div className="flex items-center justify-between pb-4 border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <Shield className="w-5 h-5 text-emerald-500" />
                    <Heading size="4">Browser Authentication</Heading>
                  </div>
                  <Badge
                    color={inputKey ? 'green' : 'amber'}
                    variant="soft"
                    size="2"
                  >
                    {inputKey ? 'API Key Active' : 'Anonymous'}
                  </Badge>
                </div>

                <div className="space-y-5">
                  {/* API Key */}
                  <div className="space-y-1.5">
                    <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                      <span className="flex items-center gap-1.5">
                        <Lock className="w-3.5 h-3.5" /> Active API Key
                        (X-API-Key)
                      </span>
                      <span className="text-[11px] text-muted-foreground font-normal">
                        Stored in browser localStorage
                      </span>
                    </label>
                    <div className="flex items-center gap-2">
                      <div className="relative flex-1">
                        <TextField.Root
                          type={showKey ? 'text' : 'password'}
                          value={inputKey}
                          onChange={(e) => setInputKey(e.target.value)}
                          placeholder="eg_..."
                          className="font-mono text-sm"
                        />
                        <button
                          type="button"
                          onClick={() => setShowKey(!showKey)}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                          title={showKey ? 'Hide key' : 'Show key'}
                        >
                          {showKey ? (
                            <EyeOff className="w-4 h-4" />
                          ) : (
                            <Eye className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                      <Button
                        type="button"
                        variant="soft"
                        color="gray"
                        onClick={handleCopy}
                        disabled={!inputKey}
                        title="Copy to clipboard"
                      >
                        {copied ? (
                          <Check className="w-4 h-4 text-emerald-500" />
                        ) : (
                          <Copy className="w-4 h-4" />
                        )}
                      </Button>
                    </div>
                    <Text size="1" color="gray">
                      Used by the Playground, Traces, and Analytics pages to
                      scope queries to your tenant.
                    </Text>
                  </div>

                  {/* Admin API Key */}
                  <div className="space-y-1.5">
                    <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                      <span className="flex items-center gap-1.5">
                        <Shield className="w-3.5 h-3.5" /> Operator Admin Key
                        (X-Admin-Key)
                      </span>
                      <span className="text-[11px] text-muted-foreground font-normal">
                        Required for the Quota & Cache Admin pages
                      </span>
                    </label>
                    <div className="relative">
                      <TextField.Root
                        type={showAdminKey ? 'text' : 'password'}
                        value={inputAdminKey}
                        onChange={(e) => setInputAdminKey(e.target.value)}
                        placeholder="ega_..."
                        className="font-mono text-sm"
                      />
                      <button
                        type="button"
                        onClick={() => setShowAdminKey(!showAdminKey)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                        title={showAdminKey ? 'Hide key' : 'Show key'}
                      >
                        {showAdminKey ? (
                          <EyeOff className="w-4 h-4" />
                        ) : (
                          <Eye className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                    <Text size="1" color="gray">
                      Cross-tenant operator credential created on the gateway host
                      with{' '}
                      <code className="font-mono bg-muted/60 px-1 py-0.5 rounded">
                        python -m app.scripts.create_admin_key create
                      </code>
                      .
                    </Text>
                  </div>

                  {/* Default tenant for admin quota rules */}
                  <div className="space-y-1.5 pt-2">
                    <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                      <span className="flex items-center gap-1.5">
                        <GaugeCircle className="w-3.5 h-3.5" /> Default Quota
                        Rule Tenant
                      </span>
                      <span className="text-[11px] text-muted-foreground font-normal">
                        Quota page only
                      </span>
                    </label>
                    <TextField.Root
                      type="text"
                      value={inputQuotaTenant}
                      onChange={(e) => setInputQuotaTenant(e.target.value)}
                      placeholder="e.g. acme"
                      className="font-mono text-sm"
                    />
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center justify-between pt-4 border-t border-border/50">
                  <div className="text-xs text-muted-foreground flex items-center gap-2">
                    <Server className="w-4 h-4" />
                    <span>Gateway Backend: {API_ROOT}</span>
                  </div>
                  <button
                    type="submit"
                    className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-white whitespace-nowrap bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-md shadow-blue-500/20 hover:shadow-lg hover:shadow-blue-500/30 active:scale-[0.98] transition-all duration-200 cursor-pointer"
                  >
                    {saved ? (
                      <>
                        <Check className="w-4 h-4 text-emerald-300" />
                        <span>Saved Successfully</span>
                      </>
                    ) : (
                      <>
                        <Save className="w-4 h-4" />
                        <span>Save Credentials</span>
                      </>
                    )}
                  </button>
                </div>
              </Card>
            </form>
          )}
        </div>

        {/* Helpful Info Card */}
        <Card size="2" className="p-5 bg-muted/20 border-border/60">
          <Heading size="3" className="mb-2">
            How Multi-Tenancy & Keys Work
          </Heading>
          <div className="space-y-2 text-xs text-muted-foreground leading-relaxed">
            <p>
              • <strong>One-Time Key Generation:</strong> Plaintext keys are
              returned exactly once upon creation. Only cryptographic SHA-256
              hashes are saved in the database.
            </p>
            <p>
              • <strong>Row-Level Security (RLS):</strong> The gateway verifies
              your key and binds the active tenant context{' '}
              <code className="text-foreground font-mono bg-muted/60 px-1 py-0.5 rounded">
                app.current_tenant
              </code>
              . Cache entries, traces, and metrics remain isolated per tenant.
            </p>
          </div>
        </Card>
      </div>
    </div>
  );
}
