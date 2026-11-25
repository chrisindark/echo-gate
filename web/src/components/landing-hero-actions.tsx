'use client';

import { useAuth } from '@/context/auth-context';
import {
  ArrowRight,
  KeyRound,
  LogIn,
  LogOut,
  Terminal,
  UserPlus,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';

function LegacyHashRedirectInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const hash = searchParams.get('hash');

  useEffect(() => {
    if (hash) {
      router.replace(`/playground?hash=${encodeURIComponent(hash)}`);
    }
  }, [hash, router]);

  return null;
}

export function LegacyHashRedirect() {
  return (
    <Suspense fallback={null}>
      <LegacyHashRedirectInner />
    </Suspense>
  );
}

export function LandingHeroActions() {
  const router = useRouter();
  const { user, tenantId, isLoading, signOut } = useAuth();
  const [signingOut, setSigningOut] = useState(false);

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await signOut();
      router.refresh();
    } finally {
      setSigningOut(false);
    }
  };

  if (user) {
    return (
      <div className="space-y-4">
        <div className="inline-flex flex-wrap items-center justify-center gap-2 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-3.5 py-1.5 text-xs text-emerald-300">
          <span className="w-2 h-2 rounded-full bg-emerald-400" />
          <span>
            Signed in as <strong className="font-semibold">{user.email}</strong>
          </span>
          {tenantId && (
            <code className="font-mono text-[10px] bg-black/30 px-1.5 py-0.5 rounded text-emerald-200">
              {tenantId}
            </code>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/settings"
            className="inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-semibold text-white bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-lg shadow-blue-500/25 transition-all"
          >
            <KeyRound className="w-4 h-4" />
            <span>Manage API Keys</span>
            <ArrowRight className="w-4 h-4" />
          </Link>
          <Link
            href="/playground"
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-card hover:bg-muted px-5 py-3 text-sm font-semibold text-foreground transition-colors"
          >
            <Terminal className="w-4 h-4 text-primary" />
            <span>Open Playground</span>
          </Link>
          <button
            type="button"
            disabled={signingOut}
            onClick={handleSignOut}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-border/80 bg-background hover:bg-muted/70 px-4 py-3 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors cursor-pointer disabled:opacity-50"
          >
            <LogOut className="w-4 h-4" />
            <span>{signingOut ? 'Signing out...' : 'Sign out'}</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center justify-center gap-3">
      <Link
        href="/signup"
        className="inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-semibold text-white bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-lg shadow-blue-500/25 transition-all"
      >
        <UserPlus className="w-4 h-4" />
        <span>Get Started Free</span>
        <ArrowRight className="w-4 h-4" />
      </Link>
      <Link
        href="/login"
        className="inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-card hover:bg-muted px-5 py-3 text-sm font-semibold text-foreground transition-colors"
      >
        <LogIn className="w-4 h-4 text-primary" />
        <span>{isLoading ? 'Sign in' : 'Sign in'}</span>
      </Link>
      <Link
        href="/playground"
        className="inline-flex items-center justify-center gap-2 rounded-xl border border-border/70 bg-background/60 hover:bg-muted/60 px-5 py-3 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        <Terminal className="w-4 h-4" />
        <span>Try Playground</span>
      </Link>
    </div>
  );
}
