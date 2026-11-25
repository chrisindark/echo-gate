// Base URL of the Echo Gate gateway (FastAPI backend).
//
// Configurable at build time via NEXT_PUBLIC_API_BASE_URL — Next.js inlines
// NEXT_PUBLIC_* variables into the client bundle during `next build`, so the
// value must be set at build time and cannot be changed at runtime. Falls
// back to the local development gateway when unset or empty.
const rawApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || '';

export const isGatewayUrlConfigured = Boolean(rawApiBaseUrl);

export const API_ROOT = (rawApiBaseUrl || 'http://localhost:8000').replace(
  /\/+$/,
  ''
);

export const isLocalGatewayUrl =
  /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(API_ROOT);

/**
 * Pull a human-readable message out of an error response body.
 *
 * Gateway errors put the text under `detail`, which is sometimes a string,
 * sometimes an object (`{message, error_code, provider}`), and for FastAPI
 * validation failures an array of `{msg}` entries. Rendering the raw value
 * would show "[object Object]" instead of the provider's message.
 */
export function errorMessageFrom(body: unknown, fallback: string): string {
  const text = (value: unknown): string | null =>
    typeof value === 'string' && value.trim() ? value : null;

  if (typeof body === 'string') return text(body) ?? fallback;
  if (!body || typeof body !== 'object') return fallback;

  const payload = body as Record<string, unknown>;
  const detail = payload.detail;

  if (typeof detail === 'string') return text(detail) ?? fallback;
  if (Array.isArray(detail)) {
    const first = detail.find(
      (item) => item && typeof item === 'object' && 'msg' in item
    ) as Record<string, unknown> | undefined;
    return text(first?.msg) ?? fallback;
  }
  if (detail && typeof detail === 'object') {
    const nested = detail as Record<string, unknown>;
    return text(nested.message) ?? text(nested.error_message) ?? fallback;
  }

  const error = payload.error;
  if (error && typeof error === 'object') {
    const nested = error as Record<string, unknown>;
    const fromError = text(nested.message);
    if (fromError) return fromError;
  }

  return (
    text(payload.error_message) ??
    text(payload.message) ??
    text(error) ??
    fallback
  );
}
