# Echo Gate UI

The observability and playground frontend for the Echo Gate gateway. It is an
independent **Next.js 16** (App Router, React 19) app using Tailwind CSS v4,
Radix UI Themes, `framer-motion`, and `lucide-react`.

It talks to the FastAPI gateway. The base URL defaults to `http://localhost:8000`
and is configurable via `NEXT_PUBLIC_API_BASE_URL` (see below).

## Getting started

1. Start the gateway (from the repo root):

   ```bash
   poetry run uvicorn app.main:app --port 8000 --reload
   # or start backend + UI together with ./dev.sh
   ```

2. Install and run the UI:

   ```bash
   npm install
   npm run dev
   ```

3. Open [http://localhost:3000](http://localhost:3000).

## Pages

| Route              | Description                                                                                                                                                                                                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`                | SaaS Landing Page with product overview, architecture summary, quickstart SDK snippet, and Supabase auth navigation (`Sign in`, `Sign up`, `Sign out`). Legacy `/?hash=<exact_hash>` links automatically redirect to `/playground?hash=<exact_hash>`.                           |
| `/playground`      | Single-turn Playground. Provider/model selection, temperature, max tokens, structured-output modes (`text` / `json_object` / `json_schema` with presets), prompt optimization toggle, API key, and cache status badges. Supports `?hash=<exact_hash>` to reload a stored trace. |
| `/conversations`   | Stateful multi-turn conversations. Persistent thread history with collapsible sidebar (grouped by date, rename, delete), SSE streaming, collapsible thinking blocks, and prompt optimization savings telemetry. Supports `?c=<conversation_id>`.                                |
| `/traces`          | Table of gateway request traces.                                                                                                                                                                                                                                                |
| `/traces/[id]`     | Trace detail with a latency waterfall, intent metadata, and evaluation scores.                                                                                                                                                                                                  |
| `/analytics`       | Dashboard KPIs, cache performance, model breakdown, and latency metrics.                                                                                                                                                                                                        |
| `/settings`        | Tenant API key management (`eg_...` create/rotate/revoke via Supabase Auth) and manual browser/operator credentials (`X-API-Key`, `X-Admin-Key`).                                                                                                                               |
| `/login`           | Supabase sign-in page with password visibility toggle, forgot-password link, and optional OAuth providers.                                                                                                                                                                      |
| `/signup`          | Supabase sign-up page with email verification flow and resend-confirmation support.                                                                                                                                                                                             |
| `/forgot-password` | Password reset request flow via Supabase Auth email recovery.                                                                                                                                                                                                                   |
| `/reset-password`  | Password update page after clicking a Supabase recovery link.                                                                                                                                                                                                                   |
| `/auth/callback`   | Server route handler exchanging Supabase PKCE `code` / `token_hash` parameters for SSR session cookies.                                                                                                                                                                         |
| `/api/health`      | Frontend health probe returning Supabase & gateway configuration status for Vercel / uptime checks.                                                                                                                                                                             |

The sidebar (`src/components/app-sidebar.tsx`) also links `/quota`, `/admin/cache`, `/admin/explorer`, and auth controls (`Sign in`, `Sign up`, `Sign out`). For deploying `web/` to Vercel, see `../docs/deployment/vercel.md`.

## Scripts

```bash
npm run dev     # development server
npm run build   # production build
npm run start   # serve the production build
npm run lint    # eslint
```

## Notes

- **API base URL** defaults to `http://localhost:8000` for host-native runs and is
  configurable via `NEXT_PUBLIC_API_BASE_URL` at build time (`src/lib/api.ts`;
  Next.js inlines `NEXT_PUBLIC_*` variables into the client bundle during
  `next build`, so the value cannot be changed at runtime). Copy `.env.example`
  to `.env.local` to override it in development. The Compose images build with
  `http://echo-gate:8000` because no host ports are published.
- The model and provider selectors in `/playground` and `/conversations` are driven
  dynamically by `GET /api/v1/models` (`src/lib/providers.ts`), reflecting the
  backend's `DynamicModelRegistry`, configured provider keys, and anonymous access
  policy (`ANONYMOUS_ALLOWED_PROVIDERS`).
- No charting library is installed; analytics renders cards/tables.
- Before editing, read `AGENTS.md` in this directory — this Next.js version has
  breaking changes versus older releases.
