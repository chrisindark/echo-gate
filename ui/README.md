# Echo Gate UI

The observability and playground frontend for the Echo Gate gateway. It is an
independent **Next.js 16** (App Router, React 19) app using Tailwind CSS v4,
Radix UI Themes, `framer-motion`, and `lucide-react`.

It talks to the FastAPI gateway at `http://localhost:8000` (currently hardcoded,
see below).

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

| Route          | Description                                                                                                                                                                                                                                                                |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`            | Chat playground. Provider/model selection, temperature, max tokens, structured-output modes (`text` / `json_object` / `json_schema` with presets), API key + tenant/user headers, and cache status badges. Supports a `?hash=<exact_hash>` query to reload a stored trace. |
| `/traces`      | Table of gateway request traces.                                                                                                                                                                                                                                           |
| `/traces/[id]` | Trace detail with a latency waterfall, intent metadata, and evaluation scores.                                                                                                                                                                                             |
| `/analytics`   | Dashboard KPIs, cache performance, model breakdown, and latency metrics.                                                                                                                                                                                                   |

The sidebar (`src/components/app-sidebar.tsx`) also links `/cache` and
`/settings`, which do not have routes yet.

## Scripts

```bash
npm run dev     # development server
npm run build   # production build
npm run start   # serve the production build
npm run lint    # eslint
```

## Notes

- **API base URL is hardcoded** to `http://localhost:8000` in `src/app/page.tsx`
  and the trace pages; there is no environment variable yet.
- The gateway enforces a per-provider model allowlist that this UI mirrors in
  `src/app/page.tsx` (`PROVIDERS`). Keep them in sync with
  `app/modules/chat/chat_schema.py`.
- No charting library is installed; analytics renders cards/tables.
- Before editing, read `AGENTS.md` in this directory — this Next.js version has
  breaking changes versus older releases.
