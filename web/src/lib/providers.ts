'use client';

import { useApiKey } from '@/context/api-key-context';
import { useAuth } from '@/context/auth-context';
import { API_ROOT, errorMessageFrom } from '@/lib/api';
import { useCallback, useEffect, useState } from 'react';

export interface BackendModelTargetInfo {
  service: string;
  model: string;
  available: boolean;
  enabled: boolean;
}

export interface BackendModelInfo {
  id: string;
  object: 'model';
  created: number;
  owned_by: string;
  available: boolean;
  enabled: boolean;
  providers: string[];
  context_window: number;
  targets?: BackendModelTargetInfo[] | null;
}

export interface BackendModelListResponse {
  object: 'list';
  data: BackendModelInfo[];
}

export interface ModelCatalog {
  providers: string[];
  modelsByProvider: Record<string, string[]>;
  modelMetadata: Record<string, BackendModelInfo>;
}

const PROVIDER_ORDER = [
  'groq',
  'openrouter',
  'google-genai',
  'gemini',
  'openai',
  'claude',
  'anthropic',
  'ollama',
  'auto',
];

export const AUTO_PROVIDER_ID = 'auto';

export function formatProviderLabel(provider: string): string {
  if (provider === AUTO_PROVIDER_ID) return 'auto (routing aliases)';
  return provider;
}

export function buildAuthHeaders(
  apiKey?: string | null,
  accessToken?: string | null
): Record<string, string> {
  const headers: Record<string, string> = {};
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  } else if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }
  return headers;
}

export async function fetchModelCatalog(options?: {
  apiKey?: string | null;
  accessToken?: string | null;
  signal?: AbortSignal;
}): Promise<ModelCatalog> {
  const headers = buildAuthHeaders(options?.apiKey, options?.accessToken);
  const res = await fetch(`${API_ROOT}/api/v1/models`, {
    headers,
    signal: options?.signal,
  });

  if (!res.ok) {
    let message = `Failed to load models (HTTP ${res.status})`;
    try {
      const body = await res.json();
      message = errorMessageFrom(body, message);
    } catch {
      // ignore non-JSON error body
    }
    throw new Error(message);
  }

  const payload = (await res.json()) as BackendModelListResponse;
  const entries = Array.isArray(payload?.data) ? payload.data : [];

  const modelsByProvider: Record<string, string[]> = {};
  const modelMetadata: Record<string, BackendModelInfo> = {};

  for (const entry of entries) {
    modelMetadata[entry.id] = entry;
    const isAlias =
      entry.owned_by === 'echo-gate' ||
      (Array.isArray(entry.targets) && entry.targets.length > 0);
    const bucket = isAlias
      ? AUTO_PROVIDER_ID
      : entry.owned_by || entry.providers?.[0] || 'default';

    if (!modelsByProvider[bucket]) {
      modelsByProvider[bucket] = [];
    }
    if (!modelsByProvider[bucket].includes(entry.id)) {
      modelsByProvider[bucket].push(entry.id);
    }
  }

  const providers = Object.keys(modelsByProvider).sort((a, b) => {
    const idxA = PROVIDER_ORDER.indexOf(a);
    const idxB = PROVIDER_ORDER.indexOf(b);
    if (idxA !== -1 && idxB !== -1) return idxA - idxB;
    if (idxA !== -1) return -1;
    if (idxB !== -1) return 1;
    return a.localeCompare(b);
  });

  return { providers, modelsByProvider, modelMetadata };
}

export function useModelCatalog() {
  const { apiKey } = useApiKey();
  const { accessToken, isLoading: authLoading } = useAuth();

  const [catalog, setCatalog] = useState<ModelCatalog>({
    providers: [],
    modelsByProvider: {},
    modelMetadata: {},
  });
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const reload = useCallback(() => {
    setIsLoading(true);
    setError(null);
    setReloadTick((prev) => prev + 1);
  }, []);

  useEffect(() => {
    if (authLoading) return;

    const controller = new AbortController();
    let ignore = false;

    const load = async () => {
      try {
        const next = await fetchModelCatalog({
          apiKey,
          accessToken,
          signal: controller.signal,
        });
        if (!ignore) {
          setCatalog(next);
          setError(null);
          setIsLoading(false);
        }
      } catch (err: unknown) {
        if (controller.signal.aborted || ignore) return;
        setCatalog({ providers: [], modelsByProvider: {}, modelMetadata: {} });
        setError(
          err instanceof Error
            ? err.message
            : 'Unable to fetch models from gateway.'
        );
        setIsLoading(false);
      }
    };

    load();

    return () => {
      ignore = true;
      controller.abort();
    };
  }, [apiKey, accessToken, authLoading, reloadTick]);

  return {
    providers: catalog.providers,
    modelsByProvider: catalog.modelsByProvider,
    modelMetadata: catalog.modelMetadata,
    isLoading: authLoading || isLoading,
    error,
    reload,
  };
}
