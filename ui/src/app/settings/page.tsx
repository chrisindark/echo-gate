'use client';

import { useApiKey } from '@/context/api-key-context';
import {
  Badge,
  Button,
  Card,
  Heading,
  Text,
  TextField,
} from '@radix-ui/themes';
import {
  Check,
  Copy,
  Eye,
  EyeOff,
  Key,
  Lock,
  Save,
  Server,
  Shield,
  User,
  Users,
} from 'lucide-react';
import React, { useState } from 'react';

export default function SettingsPage() {
  const { apiKey, setApiKey, userId, setUserId, tenantId, setTenantId } =
    useApiKey();

  const [inputKey, setInputKey] = useState(apiKey);
  const [inputUser, setInputUser] = useState(userId);
  const [inputTenant, setInputTenant] = useState(tenantId);
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    setApiKey(inputKey.trim());
    setUserId(inputUser.trim());
    setTenantId(inputTenant.trim());
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
        <div className="border-b border-border/60 pb-6">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <Key className="w-6 h-6" />
            </div>
            <div>
              <Heading size="7" className="tracking-tight">
                Gateway Settings
              </Heading>
              <Text color="gray" size="2" mt="1">
                Configure your API key and tenant credentials used across Chat,
                Traces, and Analytics.
              </Text>
            </div>
          </div>
        </div>

        {/* Credentials Form Card */}
        <form onSubmit={handleSave} className="space-y-6">
          <Card size="3" className="p-6 border-border/80 space-y-6">
            <div className="flex items-center justify-between pb-4 border-b border-border/50">
              <div className="flex items-center gap-2">
                <Shield className="w-5 h-5 text-emerald-500" />
                <Heading size="4">Tenant Identity & Authentication</Heading>
              </div>
              <Badge
                color={inputKey ? 'green' : 'amber'}
                variant="soft"
                size="2"
              >
                {inputKey ? 'API Key Active' : 'No Key Set'}
              </Badge>
            </div>

            <div className="space-y-5">
              {/* API Key */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Lock className="w-3.5 h-3.5" /> API Key (X-API-Key)
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
                  This key determines which tenant data is visible. When
                  provided, requests to Chat, Traces, and Analytics are scoped
                  to this key.
                </Text>
              </div>

              {/* Grid for Tenant ID & User ID */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-2">
                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                    <Users className="w-3.5 h-3.5" /> Tenant ID
                  </label>
                  <TextField.Root
                    type="text"
                    value={inputTenant}
                    onChange={(e) => setInputTenant(e.target.value)}
                    placeholder="e.g. dev, acme"
                    className="font-mono text-sm"
                  />
                  <Text size="1" color="gray">
                    The tenant boundary for Row-Level Security in Postgres.
                  </Text>
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                    <User className="w-3.5 h-3.5" /> User ID (Optional)
                  </label>
                  <TextField.Root
                    type="text"
                    value={inputUser}
                    onChange={(e) => setInputUser(e.target.value)}
                    placeholder="e.g. dev-user"
                    className="font-mono text-sm"
                  />
                  <Text size="1" color="gray">
                    Sub-identity for per-user quotas and audit traces.
                  </Text>
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center justify-between pt-4 border-t border-border/50">
              <div className="text-xs text-muted-foreground flex items-center gap-2">
                <Server className="w-4 h-4" />
                <span>Gateway Backend: http://localhost:8000</span>
              </div>
              <Button type="submit" color="blue" variant="solid" size="2">
                {saved ? (
                  <>
                    <Check className="w-4 h-4" /> Saved Successfully
                  </>
                ) : (
                  <>
                    <Save className="w-4 h-4" /> Save Credentials
                  </>
                )}
              </Button>
            </div>
          </Card>
        </form>

        {/* Helpful Info Card */}
        <Card size="2" className="p-5 bg-muted/20 border-border/60">
          <Heading size="3" className="mb-2">
            How API Keys Work in Echo Gate
          </Heading>
          <div className="space-y-2 text-xs text-muted-foreground leading-relaxed">
            <p>
              • <strong>Created once:</strong> Plaintext keys are generated once
              via{' '}
              <code className="text-foreground font-mono bg-muted/60 px-1 py-0.5 rounded">
                POST /api/v1/admin/api-keys/
              </code>
              . Only cryptographic hashes are stored in the database.
            </p>
            <p>
              • <strong>Row-Level Security (RLS):</strong> The gateway verifies
              the key and sets PostgreSQL transaction-level session config{' '}
              <code className="text-foreground font-mono bg-muted/60 px-1 py-0.5 rounded">
                app.current_tenant
              </code>
              . Traces, metrics, and cache items remain strictly
              tenant-isolated.
            </p>
            <p>
              • <strong>Client Persistence:</strong> Saving your key here stores
              it in your browser’s localStorage so Chat completions, Request
              Traces, and Analytics dashboards remain synced.
            </p>
          </div>
        </Card>
      </div>
    </div>
  );
}
