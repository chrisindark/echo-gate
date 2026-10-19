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

| Route          | Description                                                                                                                                                                                                                                          |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`            | Chat playground. Provider/model selection, temperature, max tokens, structured-output modes (`text` / `json_object` / `json_schema` with presets), API key, and cache status badges. Supports a `?hash=<exact_hash>` query to reload a stored trace. |
| `/traces`      | Table of gateway request traces.                                                                                                                                                                                                                     |
| `/traces/[id]` | Trace detail with a latency waterfall, intent metadata, and evaluation scores.                                                                                                                                                                       |
| `/analytics`   | Dashboard KPIs, cache performance, model breakdown, and latency metrics.                                                                                                                                                                             |

The sidebar (`src/components/app-sidebar.tsx`) also links `/settings`.

## Scripts

```bash
npm run dev     # development server
npm run build   # production build
npm run start   # serve the production build
npm run lint    # eslint
```

## Notes

- **API base URL** defaults to `http://localhost:8000` and is configurable via
  `NEXT_PUBLIC_API_BASE_URL` at build time (`src/lib/api.ts`; Next.js inlines
  `NEXT_PUBLIC_*` variables into the client bundle during `next build`, so the
  value cannot be changed at runtime). Copy `.env.example` to `.env.local` to
  override it in development.
- The gateway enforces a per-provider model allowlist that this UI mirrors in
  `src/app/page.tsx` (`PROVIDERS`). Keep them in sync with
  `app/modules/chat/chat_schema.py`.
- No charting library is installed; analytics renders cards/tables.
- Before editing, read `AGENTS.md` in this directory — this Next.js version has
  breaking changes versus older releases.
