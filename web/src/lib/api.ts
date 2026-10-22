// Base URL of the Echo Gate gateway (FastAPI backend).
//
// Configurable at build time via NEXT_PUBLIC_API_BASE_URL — Next.js inlines
// NEXT_PUBLIC_* variables into the client bundle during `next build`, so the
// value must be set at build time and cannot be changed at runtime. Falls
// back to the local development gateway when unset.
export const API_ROOT =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
