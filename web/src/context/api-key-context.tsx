'use client';

import React, { createContext, useContext, useState } from 'react';

interface ApiKeyContextType {
  apiKey: string;
  setApiKey: (key: string) => void;
  adminKey: string;
  setAdminKey: (key: string) => void;
  quotaTenant: string;
  setQuotaTenant: (tenant: string) => void;
  isSettingsOpen: boolean;
  setIsSettingsOpen: (open: boolean) => void;
}

const ApiKeyContext = createContext<ApiKeyContextType | undefined>(undefined);

const API_KEY_STORAGE = 'echo_gate_api_key';
const ADMIN_KEY_STORAGE = 'echo_gate_admin_key';
const QUOTA_TENANT_STORAGE = 'echo_gate_quota_tenant';
// The tenant field used to be an identity header (X-Tenant-ID). The gateway
// ignores that header now — identity comes from the API key — so the value is
// only a local convenience for pre-filling admin quota rules. Read the old key
// once so an existing preference is not lost.
const LEGACY_TENANT_STORAGE = 'echo_gate_tenant_id';

// No default tenant key. It used to be a hardcoded `eg_...` value, which meant
// shipping a real credential in the repo: on a fresh database that key does not
// resolve, and because chat now rejects a supplied-but-invalid key, the
// playground would 401 instead of degrading to anonymous. With an empty default
// the app starts anonymous and operators paste their own key in Settings.
//
// Note that only chat tolerates an empty key (it treats keyless callers as the
// `anonymous` tenant). Analytics and Traces require a real key and will show a
// 401 until one is configured.
const DEFAULT_API_KEY = '';
const DEFAULT_QUOTA_TENANT = '';

function getInitialValue(key: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  try {
    const val = localStorage.getItem(key);
    return val !== null ? val : fallback;
  } catch {
    return fallback;
  }
}

export function ApiKeyProvider({ children }: { children: React.ReactNode }) {
  const [apiKey, setApiKeyState] = useState<string>(() =>
    getInitialValue(API_KEY_STORAGE, DEFAULT_API_KEY)
  );
  // Admin keys are minted with `python -m app.scripts.create_admin_key` and sent
  // as X-Admin-Key. Deliberately no default: an admin key must never be baked
  // into the client bundle.
  const [adminKey, setAdminKeyState] = useState<string>(() =>
    getInitialValue(ADMIN_KEY_STORAGE, '')
  );
  const [quotaTenant, setQuotaTenantState] = useState<string>(() => {
    const current = getInitialValue(QUOTA_TENANT_STORAGE, '');
    return (
      current || getInitialValue(LEGACY_TENANT_STORAGE, DEFAULT_QUOTA_TENANT)
    );
  });
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const setApiKey = (key: string) => {
    setApiKeyState(key);
    try {
      localStorage.setItem(API_KEY_STORAGE, key);
    } catch {
      // ignore
    }
  };

  const setAdminKey = (key: string) => {
    setAdminKeyState(key);
    try {
      localStorage.setItem(ADMIN_KEY_STORAGE, key);
    } catch {
      // ignore
    }
  };

  const setQuotaTenant = (tenant: string) => {
    setQuotaTenantState(tenant);
    try {
      localStorage.setItem(QUOTA_TENANT_STORAGE, tenant);
    } catch {
      // ignore
    }
  };

  return (
    <ApiKeyContext.Provider
      value={{
        apiKey,
        setApiKey,
        adminKey,
        setAdminKey,
        quotaTenant,
        setQuotaTenant,
        isSettingsOpen,
        setIsSettingsOpen,
      }}
    >
      {children}
    </ApiKeyContext.Provider>
  );
}

export function useApiKey(): ApiKeyContextType {
  const context = useContext(ApiKeyContext);
  if (!context) {
    throw new Error('useApiKey must be used within an ApiKeyProvider');
  }
  return context;
}
