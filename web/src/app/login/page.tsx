'use client';

import { useAuth } from '@/context/auth-context';
import { Button, Card, Heading, Text } from '@radix-ui/themes';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  LogOut,
  Mail,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import React, { Suspense, useId, useState } from 'react';

function getSafeNext(nextParam: string | null): string {
  if (!nextParam || !nextParam.startsWith('/') || nextParam.startsWith('//')) {
    return '/settings';
  }
  return nextParam;
}

function LoginFormContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = getSafeNext(searchParams.get('next'));
  const callbackError = searchParams.get('error');

  const {
    signInWithEmail,
    signInWithOAuth,
    signOut,
    isConfigured,
    oauthProviders,
    user,
  } = useAuth();

  const emailId = useId();
  const passwordId = useId();
  const toggleWarningId = useId();
  const errorId = useId();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(callbackError);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<string | null>(null);

  if (user) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 py-16">
        <Card
          size="3"
          className="max-w-md w-full p-8 border-border text-center space-y-4"
        >
          <div className="mx-auto w-12 h-12 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center border border-emerald-500/20">
            <CheckCircle2 className="w-6 h-6" />
          </div>
          <Heading size="5">Already Signed In</Heading>
          <Text size="2" color="gray" as="p">
            You are signed in as{' '}
            <span className="text-foreground font-medium">{user.email}</span>.
          </Text>
          <div className="pt-2 flex flex-wrap justify-center gap-3">
            <Button
              onClick={() => router.push('/settings')}
              variant="solid"
              color="blue"
              className="cursor-pointer"
            >
              Manage API Keys
            </Button>
            <Button
              onClick={() => router.push('/playground')}
              variant="soft"
              color="gray"
              className="cursor-pointer"
            >
              Open Playground
            </Button>
            <Button
              onClick={() => signOut()}
              variant="outline"
              color="gray"
              className="cursor-pointer"
            >
              <LogOut className="w-3.5 h-3.5" />
              Sign out
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isConfigured) {
      setError('Supabase environment variables are not configured yet.');
      return;
    }
    if (!email.trim() || !password) {
      setError('Please provide both your email address and password.');
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const { error: signInError } = await signInWithEmail(
        email.trim(),
        password
      );
      if (signInError) {
        setError(signInError.message || 'Invalid email or password.');
      } else {
        router.push(nextPath);
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'An unexpected error occurred.'
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOAuth = async (provider: 'github' | 'google') => {
    setError(null);
    setOauthLoading(provider);
    try {
      const { error: oauthError } = await signInWithOAuth(provider, nextPath);
      if (oauthError) {
        setError(oauthError.message);
      }
    } finally {
      setOauthLoading(null);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center p-6 py-12">
      <div className="max-w-md w-full space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex p-3 rounded-2xl bg-primary/10 text-primary border border-primary/20 mb-2">
            <KeyRound className="w-8 h-8" />
          </div>
          <Heading as="h1" size="7" className="tracking-tight">
            Sign in to Echo Gate
          </Heading>
          <Text size="2" color="gray" as="p">
            Manage tenant API keys, monitor requests, and track semantic cache
            savings.
          </Text>
        </div>

        {!isConfigured && (
          <Card
            size="2"
            className="p-4 bg-amber-500/10 border-amber-500/30 text-amber-200"
          >
            <div className="flex items-start gap-2.5">
              <AlertCircle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
              <div className="text-xs space-y-1">
                <Text weight="bold" size="2" className="text-amber-300">
                  Supabase Not Configured
                </Text>
                <p className="text-amber-200/80 leading-relaxed">
                  Add{' '}
                  <code className="bg-black/30 px-1 py-0.5 rounded">
                    NEXT_PUBLIC_SUPABASE_URL
                  </code>{' '}
                  and{' '}
                  <code className="bg-black/30 px-1 py-0.5 rounded">
                    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
                  </code>{' '}
                  in your Vercel project settings to enable SaaS sign-in.
                </p>
                <div className="pt-2">
                  <Link
                    href="/settings"
                    className="text-primary hover:underline font-medium"
                  >
                    Continue to Manual Settings →
                  </Link>
                </div>
              </div>
            </div>
          </Card>
        )}

        <Card size="3" className="p-6 border-border/80 space-y-5">
          {oauthProviders.length > 0 && isConfigured && (
            <>
              <div className="grid grid-cols-1 gap-2.5">
                {oauthProviders.map((provider) => (
                  <button
                    key={provider}
                    type="button"
                    disabled={Boolean(oauthLoading)}
                    onClick={() => handleOAuth(provider)}
                    className="w-full min-h-11 inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-card hover:bg-muted/70 px-4 py-2.5 text-sm font-medium text-foreground transition-colors cursor-pointer disabled:opacity-50"
                  >
                    <span>
                      {oauthLoading === provider
                        ? 'Redirecting...'
                        : `Continue with ${provider === 'github' ? 'GitHub' : 'Google'}`}
                    </span>
                  </button>
                ))}
              </div>
              <div className="relative flex items-center justify-center text-xs text-muted-foreground">
                <span className="border-t border-border/70 w-full" />
                <span className="px-3 bg-card shrink-0">or with email</span>
                <span className="border-t border-border/70 w-full" />
              </div>
            </>
          )}

          <form
            onSubmit={handleSubmit}
            className="space-y-4"
            noValidate={false}
          >
            {error && (
              <div
                id={errorId}
                role="status"
                aria-live="polite"
                className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs flex items-center gap-2"
              >
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <div className="space-y-1.5">
              <label
                htmlFor={emailId}
                className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5"
              >
                <Mail className="w-3.5 h-3.5" aria-hidden="true" />
                <span>Email</span>
              </label>
              <input
                id={emailId}
                name="email"
                type="email"
                required
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  if (error) setError(null);
                }}
                placeholder="name@company.com"
                autoComplete="username"
                autoCorrect="off"
                autoCapitalize="none"
                spellCheck={false}
                enterKeyHint="next"
                aria-invalid={Boolean(error)}
                aria-describedby={error ? errorId : undefined}
                className="w-full min-h-12 rounded-xl border border-border bg-background/60 px-3.5 py-2.5 text-base text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2"
              />
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <label
                  htmlFor={passwordId}
                  className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5"
                >
                  <Lock className="w-3.5 h-3.5" aria-hidden="true" />
                  <span>Password</span>
                </label>
                <Link
                  href="/forgot-password"
                  className="text-xs text-primary hover:underline font-medium"
                >
                  Forgot password?
                </Link>
              </div>
              <div className="relative">
                <input
                  id={passwordId}
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  required
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (error) setError(null);
                  }}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  enterKeyHint="done"
                  aria-invalid={Boolean(error)}
                  aria-describedby={error ? errorId : undefined}
                  className="w-full min-h-12 rounded-xl border border-border bg-background/60 pl-3.5 pr-12 py-2.5 text-base text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  aria-pressed={showPassword}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-describedby={toggleWarningId}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors cursor-pointer"
                >
                  {showPassword ? (
                    <EyeOff className="w-4 h-4" aria-hidden="true" />
                  ) : (
                    <Eye className="w-4 h-4" aria-hidden="true" />
                  )}
                </button>
                <span id={toggleWarningId} className="sr-only">
                  Warning: toggling this will display your password on the
                  screen.
                </span>
              </div>
            </div>

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full min-h-12 inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-md shadow-blue-500/20 disabled:opacity-60 transition-all cursor-pointer"
            >
              {isSubmitting ? 'Signing in...' : 'Sign in'}
            </button>

            <div className="pt-2 text-center text-xs text-muted-foreground">
              Don&apos;t have an account?{' '}
              <Link
                href={
                  nextPath !== '/settings'
                    ? `/signup?next=${encodeURIComponent(nextPath)}`
                    : '/signup'
                }
                className="text-primary font-medium hover:underline"
              >
                Create an account
              </Link>
            </div>
          </form>
        </Card>

        <div className="text-center">
          <Link
            href="/settings"
            className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 transition-colors"
          >
            Looking for standalone manual API key settings?{' '}
            <ArrowRight className="w-3 h-3" />
          </Link>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex-1 flex items-center justify-center p-6 text-sm text-muted-foreground">
          Loading sign in...
        </div>
      }
    >
      <LoginFormContent />
    </Suspense>
  );
}
