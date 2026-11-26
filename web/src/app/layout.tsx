import { AppShell } from '@/components/app-shell';
import { ApiKeyProvider } from '@/context/api-key-context';
import { AuthProvider } from '@/context/auth-context';
import { ThemeProvider } from '@/components/theme-provider';
import '@radix-ui/themes/styles.css';
import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import React from 'react';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

function resolveSiteUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_APP_URL?.trim();
  if (explicit) return explicit.replace(/\/+$/, '');
  const vercelProd = process.env.VERCEL_PROJECT_PRODUCTION_URL?.trim();
  if (vercelProd) return `https://${vercelProd}`;
  const vercelUrl = process.env.VERCEL_URL?.trim();
  if (vercelUrl) return `https://${vercelUrl}`;
  return 'http://localhost:3000';
}

export const metadata: Metadata = {
  metadataBase: new URL(resolveSiteUrl()),
  title: {
    default: 'Echo Gate — Semantic Caching & Multi-Provider LLM Gateway',
    template: '%s | Echo Gate',
  },
  description:
    'Production LLM gateway with 2-tier semantic caching (Redis + Qdrant), OpenAI & Anthropic SDK drop-in parity, prompt optimization, and multi-tenant API key management.',
  openGraph: {
    title: 'Echo Gate — Semantic Caching & Multi-Provider LLM Gateway',
    description:
      'Cut LLM latency and token spend with semantic caching, multi-provider routing, and tenant-isolated API keys.',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Echo Gate — Semantic Caching LLM Gateway',
    description:
      'Semantic caching, OpenAI/Anthropic SDK parity, prompt optimization, and observability.',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('echo_gate_theme')||'dark';if(t==='dark')document.documentElement.classList.add('dark');}catch(e){document.documentElement.classList.add('dark');}})();",
          }}
        />
      </head>
      <body className="h-full flex flex-col bg-background text-foreground">
        <ThemeProvider>
          <AuthProvider>
            <ApiKeyProvider>
              <React.Suspense
                fallback={
                  <main className="flex-1 flex flex-col min-h-dvh w-full min-w-0">
                    {children}
                  </main>
                }
              >
                <AppShell>{children}</AppShell>
              </React.Suspense>
            </ApiKeyProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
