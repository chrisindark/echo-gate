# Deploying Echo Gate Web (`web/`) to Vercel

This guide covers deploying the Echo Gate Next.js frontend (`web/`) to **Vercel** first—with **Supabase Auth** enabled for SaaS sign-up, sign-in, password reset, and sign-out—and connecting it to your FastAPI gateway backend when ready.

---

## 1. Vercel Project Setup

1. Import the `echo-gate` repository in Vercel (**Add New → Project**).
2. Set **Root Directory** to `web`.
3. Leave **Framework Preset** as `Next.js` (also declared in `web/vercel.json`).
4. Note: `web/package.json` guards the monorepo `husky` `prepare` hook so `npm install` on Vercel skips git hook setup cleanly when `.git` is outside `web/`.

---

## 2. Environment Variables on Vercel

Configure these in **Project Settings → Environment Variables** (available to Production and Preview):

| Variable | Required | Description |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Yes (for SaaS auth) | Your Supabase project URL (`https://<ref>.supabase.co`). |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Yes (for SaaS auth) | Your Supabase publishable / anon key. |
| `NEXT_PUBLIC_API_BASE_URL` | When backend is live | HTTPS base URL of your Echo Gate FastAPI gateway (e.g. `https://echo-gate-api.onrender.com`). Defaults to `http://localhost:8000` if unset. |
| `NEXT_PUBLIC_APP_URL` | Optional | Canonical origin if using a custom domain (otherwise `VERCEL_PROJECT_PRODUCTION_URL` / `VERCEL_URL` are used for metadata, `robots.txt`, and `sitemap.xml`). |
| `NEXT_PUBLIC_SUPABASE_OAUTH_PROVIDERS` | Optional | Comma-separated OAuth providers enabled in Supabase Auth (supports `github`, `google`). |

> **Note on `NEXT_PUBLIC_*` variables:** Next.js inlines `NEXT_PUBLIC_*` environment variables at build time. Trigger a **Redeploy** on Vercel whenever you change `NEXT_PUBLIC_API_BASE_URL` or Supabase keys.

---

## 3. Supabase Auth URL Configuration

In your Supabase Dashboard (**Authentication → URL Configuration**):

1. **Site URL:** Set to your production Vercel URL, e.g. `https://your-app.vercel.app`.
2. **Redirect URLs:** Add:
   - `https://your-app.vercel.app/auth/callback`
   - `https://*-your-scope.vercel.app/auth/callback` (if using Vercel Preview deployments)
   - `http://localhost:3000/auth/callback` (for local development)

This enables:
- Email verification links (`/signup` → `/auth/callback` → `/settings`)
- Password recovery links (`/forgot-password` → `/auth/callback?next=/reset-password` → `/reset-password`)
- OAuth callbacks (when `NEXT_PUBLIC_SUPABASE_OAUTH_PROVIDERS` is configured)

---

## 4. Connecting the FastAPI Gateway Backend

Once your Echo Gate backend is deployed (see [`llm-only-free-tier.md`](./llm-only-free-tier.md) or Docker/Helm guides):

1. Set **`NEXT_PUBLIC_API_BASE_URL`** in Vercel to your HTTPS gateway URL and redeploy `web/`.
2. Set **`CORS_ORIGINS`** on the Echo Gate backend to include your Vercel origin(s), e.g.:
   ```dotenv
   CORS_ORIGINS=https://your-app.vercel.app,http://localhost:3000
   ```
3. Enable Supabase JWT verification and restrict anonymous providers on the backend so logged-in users unlock all configured providers while unauthenticated visitors only access free-tier providers (`groq`, `openrouter`):
   ```dotenv
   SUPABASE_AUTH_ENABLED=true
   SUPABASE_JWT_SECRET=<your-supabase-jwt-secret>
   SUPABASE_JWT_AUDIENCE=authenticated
   SUPABASE_JWT_ALGORITHMS=HS256
   ANONYMOUS_ALLOWED_PROVIDERS=groq,openrouter
   ```
   *(When `SUPABASE_AUTH_ENABLED=true` or `PYTHON_ENV=production`, `ANONYMOUS_ALLOWED_PROVIDERS` automatically defaults to `groq,openrouter` unless overridden.)*
4. Mount the `api_keys` / user surface on the gateway (`ENABLED_SURFACES=all`, or include `api_keys` alongside `health,models,chat,anthropic`).

---

## 5. Verification Checklist

1. **Frontend health check:** `GET https://your-app.vercel.app/api/health` returns `200` with `supabaseConfigured: true`.
2. **Landing & Auth routes:**
   - `/` renders the SaaS landing page with **Sign in** and **Sign up** navigation links.
   - `/signup` creates a Supabase user and handles email confirmation + resend.
   - `/login`, `/forgot-password`, `/reset-password`, and **Sign out** (both in the top navbar and workspace sidebar) work end-to-end.
3. **API Key provisioning:** Once the backend is connected, signing in and opening `/settings` loads `/api/v1/user/me` and lets the user create, rotate, and revoke `eg_...` keys.
