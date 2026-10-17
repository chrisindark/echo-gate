'use client';

import React, { createContext, useContext, useState } from 'react';

interface ApiKeyContextType {
  apiKey: string;
  setApiKey: (key: string) => void;
  userId: string;
  setUserId: (id: string) => void;
  tenantId: string;
  setTenantId: (tenant: string) => void;
  isSettingsOpen: boolean;
  setIsSettingsOpen: (open: boolean) => void;
}

const ApiKeyContext = createContext<ApiKeyContextType | undefined>(undefined);

const API_KEY_STORAGE = 'echo_gate_api_key';
const USER_ID_STORAGE = 'echo_gate_user_id';
const TENANT_ID_STORAGE = 'echo_gate_tenant_id';

const DEFAULT_API_KEY = 'eg_cDmUa2FOI0CrArXqWP49AsEKMLpqKJyCoC0gM3nfh0g';
const DEFAULT_USER_ID = 'dev';
const DEFAULT_TENANT_ID = 'dev';

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
  const [userId, setUserIdState] = useState<string>(() =>
    getInitialValue(USER_ID_STORAGE, DEFAULT_USER_ID)
  );
  const [tenantId, setTenantIdState] = useState<string>(() =>
    getInitialValue(TENANT_ID_STORAGE, DEFAULT_TENANT_ID)
  );
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const setApiKey = (key: string) => {
    setApiKeyState(key);
    try {
      localStorage.setItem(API_KEY_STORAGE, key);
    } catch {
      // ignore
    }
  };

  const setUserId = (id: string) => {
    setUserIdState(id);
    try {
      localStorage.setItem(USER_ID_STORAGE, id);
    } catch {
      // ignore
    }
  };

  const setTenantId = (tenant: string) => {
    setTenantIdState(tenant);
    try {
      localStorage.setItem(TENANT_ID_STORAGE, tenant);
    } catch {
      // ignore
    }
  };

  return (
    <ApiKeyContext.Provider
      value={{
        apiKey,
        setApiKey,
        userId,
        setUserId,
        tenantId,
        setTenantId,
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
