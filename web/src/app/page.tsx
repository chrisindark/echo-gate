import {
  LandingHeroActions,
  LegacyHashRedirect,
} from '@/components/landing-hero-actions';
import {
  Activity,
  Braces,
  Database,
  KeyRound,
  Layers,
  ShieldCheck,
  Sparkles,
  Terminal,
  Zap,
} from 'lucide-react';
import Link from 'next/link';

const FEATURES = [
  {
    icon: Database,
    title: '2-Tier Semantic & Exact Cache',
    description:
      'Sub-millisecond Redis exact match backed by Qdrant hybrid BM25 + dense vector search and cross-encoder reranking.',
    accent: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
  },
  {
    icon: Braces,
    title: 'OpenAI & Anthropic Drop-In Parity',
    description:
      'Keep your existing SDKs. Change base_url and pass an eg_... API key for streaming, tool calls, JSON schemas, and vision.',
    accent: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/20',
  },
  {
    icon: KeyRound,
    title: 'Multi-Tenant SaaS Auth & Keys',
    description:
      'Supabase Auth provisions isolated personal tenants with SHA-256 hashed API keys, one-time reveal, and 24h rotation grace periods.',
    accent: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  },
  {
    icon: Sparkles,
    title: 'Prompt Token Optimization',
    description:
      'Lossless whitespace compaction, table serialization, context headroom trimming, and extractive compression before upstream calls.',
    accent: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  },
  {
    icon: Activity,
    title: 'Trace Waterfalls & Analytics',
    description:
      'Inspect per-stage latency across Redis, embeddings, reranking, and upstream providers alongside cost-avoided dashboards.',
    accent: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/20',
  },
  {
    icon: ShieldCheck,
    title: 'Fail-Open Resilience & Postgres RLS',
    description:
      'If cache or inference dependencies degrade, requests automatically fall through to the upstream LLM while Postgres RLS isolates tenant data.',
    accent: 'text-purple-400 bg-purple-500/10 border-purple-500/20',
  },
];

const ARCHITECTURE_STEPS = [
  { label: '01 · Auth & RLS', value: 'Supabase JWT / eg_... Key' },
  { label: '02 · L1 Cache', value: 'Redis Exact SHA-256' },
  { label: '03 · L2 Semantic', value: 'Qdrant Hybrid + Rerank' },
  { label: '04 · Upstream', value: 'OpenAI · Gemini · Claude · Groq' },
];

export default function LandingPage() {
  return (
    <div className="flex-1 flex flex-col bg-background">
      <LegacyHashRedirect />

      {/* Hero Section */}
      <section className="relative overflow-hidden border-b border-border/60">
        <div
          className="pointer-events-none absolute inset-x-0 -top-40 h-96 bg-[radial-gradient(ellipse_at_top,rgba(59,130,246,0.18),transparent_65%)]"
          aria-hidden="true"
        />
        <div className="relative max-w-6xl mx-auto px-4 sm:px-6 pt-16 pb-20 sm:pt-24 sm:pb-24 text-center space-y-8">
          <div className="inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-3.5 py-1 text-xs font-medium text-primary">
            <Zap className="w-3.5 h-3.5" aria-hidden="true" />
            <span>Semantic Caching &amp; Multi-Provider LLM Gateway</span>
          </div>

          <div className="space-y-4 max-w-3xl mx-auto">
            <h1 className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight text-foreground leading-[1.1]">
              Cut LLM Latency &amp; Spend{' '}
              <span className="bg-gradient-to-r from-blue-400 via-indigo-400 to-emerald-400 bg-clip-text text-transparent">
                Without Changing Your SDK
              </span>
            </h1>
            <p className="text-base sm:text-lg text-muted-foreground leading-relaxed max-w-2xl mx-auto">
              Echo Gate sits in front of OpenAI, Anthropic, Gemini, Groq, and
              Ollama with 2-tier semantic caching, prompt optimization, and
              row-level tenant isolation backed by Supabase.
            </p>
          </div>

          <LandingHeroActions />

          {/* Pipeline Strip */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 pt-6 max-w-4xl mx-auto text-left">
            {ARCHITECTURE_STEPS.map((step) => (
              <div
                key={step.label}
                className="rounded-xl border border-border/70 bg-card/70 px-4 py-3"
              >
                <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                  {step.label}
                </div>
                <div className="text-xs font-semibold text-foreground mt-1">
                  {step.value}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features Grid */}
      <section
        id="features"
        className="max-w-6xl mx-auto w-full px-4 sm:px-6 py-16 sm:py-20 space-y-10"
      >
        <div className="max-w-2xl space-y-2">
          <div className="text-xs font-mono uppercase tracking-wider text-primary">
            Platform Capabilities
          </div>
          <h2 className="text-2xl sm:text-3xl font-bold tracking-tight text-foreground">
            Built for production LLM workloads
          </h2>
          <p className="text-sm text-muted-foreground leading-relaxed">
            Every request flows through tenant verification, optional prompt
            optimization, fail-open cache lookup, and structured telemetry.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {FEATURES.map((feature) => (
            <article
              key={feature.title}
              className="rounded-2xl border border-border/80 bg-card/60 p-6 space-y-3 hover:border-primary/35 transition-colors"
            >
              <div
                className={`w-10 h-10 rounded-xl border flex items-center justify-center ${feature.accent}`}
              >
                <feature.icon className="w-5 h-5" aria-hidden="true" />
              </div>
              <h3 className="text-base font-semibold text-foreground">
                {feature.title}
              </h3>
              <p className="text-xs sm:text-sm text-muted-foreground leading-relaxed">
                {feature.description}
              </p>
            </article>
          ))}
        </div>
      </section>

      {/* SaaS Quickstart Section */}
      <section id="quickstart" className="border-t border-border/60 bg-card/30">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16 sm:py-20 grid grid-cols-1 lg:grid-cols-2 gap-10 items-center">
          <div className="space-y-6">
            <div className="space-y-2">
              <div className="text-xs font-mono uppercase tracking-wider text-emerald-400">
                3-Step SaaS Onboarding
              </div>
              <h2 className="text-2xl sm:text-3xl font-bold tracking-tight text-foreground">
                From signup to cached completions in two minutes
              </h2>
              <p className="text-sm text-muted-foreground leading-relaxed">
                Authenticate via Supabase to provision your isolated tenant,
                generate a scoped API key, and route traffic through Echo Gate.
              </p>
            </div>

            <ol className="space-y-4 text-sm">
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 rounded-full bg-primary/15 border border-primary/30 text-primary text-xs font-mono font-bold flex items-center justify-center shrink-0 mt-0.5">
                  1
                </span>
                <div>
                  <div className="font-semibold text-foreground">
                    Create your Supabase-backed account
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    <Link
                      href="/signup"
                      className="text-primary hover:underline"
                    >
                      Sign up
                    </Link>{' '}
                    or{' '}
                    <Link
                      href="/login"
                      className="text-primary hover:underline"
                    >
                      sign in
                    </Link>{' '}
                    to automatically bind your personal{' '}
                    <code className="font-mono text-[11px] bg-muted px-1 py-0.5 rounded">
                      usr_&lt;uuid&gt;
                    </code>{' '}
                    tenant.
                  </p>
                </div>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 rounded-full bg-primary/15 border border-primary/30 text-primary text-xs font-mono font-bold flex items-center justify-center shrink-0 mt-0.5">
                  2
                </span>
                <div>
                  <div className="font-semibold text-foreground">
                    Generate a tenant API key
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Open{' '}
                    <Link
                      href="/settings"
                      className="text-primary hover:underline"
                    >
                      Settings
                    </Link>{' '}
                    to mint an{' '}
                    <code className="font-mono text-[11px] bg-muted px-1 py-0.5 rounded">
                      eg_...
                    </code>{' '}
                    credential with zero-downtime key rotation.
                  </p>
                </div>
              </li>
              <li className="flex items-start gap-3">
                <span className="w-6 h-6 rounded-full bg-primary/15 border border-primary/30 text-primary text-xs font-mono font-bold flex items-center justify-center shrink-0 mt-0.5">
                  3
                </span>
                <div>
                  <div className="font-semibold text-foreground">
                    Test live in the Playground &amp; inspect traces
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Explore streaming, JSON schemas, and cache hits in the{' '}
                    <Link
                      href="/playground"
                      className="text-primary hover:underline"
                    >
                      Playground
                    </Link>
                    ,{' '}
                    <Link
                      href="/conversations"
                      className="text-primary hover:underline"
                    >
                      Conversations
                    </Link>
                    , and{' '}
                    <Link
                      href="/analytics"
                      className="text-primary hover:underline"
                    >
                      Analytics
                    </Link>
                    .
                  </p>
                </div>
              </li>
            </ol>
          </div>

          {/* Code Snippet Card */}
          <div className="rounded-2xl border border-border bg-card overflow-hidden shadow-xl">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/70 bg-muted/40">
              <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
                <Layers className="w-3.5 h-3.5 text-primary" />
                <span>openai_quickstart.py</span>
              </div>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                Drop-in Parity
              </span>
            </div>
            <pre className="p-5 text-xs font-mono leading-relaxed text-foreground/90 overflow-x-auto">
              <code>{`from openai import OpenAI

client = OpenAI(
    base_url="https://your-gateway.example/api/v1",
    api_key="eg_your_tenant_api_key",
)

resp = client.chat.completions.create(
    model="gemini-3.1-flash-lite",
    messages=[{"role": "user", "content": "Explain semantic caching."}],
)

print(resp.choices[0].message.content)`}</code>
            </pre>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border/60 py-8 px-4 sm:px-6 bg-background">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-primary" aria-hidden="true" />
            <span className="font-semibold text-foreground">Echo Gate</span>
            <span>· Semantic Caching LLM Gateway</span>
          </div>
          <nav
            aria-label="Footer"
            className="flex flex-wrap items-center justify-center gap-4"
          >
            <Link
              href="/playground"
              className="hover:text-foreground transition-colors"
            >
              Playground
            </Link>
            <Link
              href="/analytics"
              className="hover:text-foreground transition-colors"
            >
              Analytics
            </Link>
            <Link
              href="/traces"
              className="hover:text-foreground transition-colors"
            >
              Traces
            </Link>
            <Link
              href="/settings"
              className="hover:text-foreground transition-colors"
            >
              API Keys
            </Link>
            <Link
              href="/login"
              className="hover:text-foreground transition-colors"
            >
              Sign in
            </Link>
            <Link
              href="/signup"
              className="hover:text-foreground transition-colors"
            >
              Sign up
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
