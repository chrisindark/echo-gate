'use client';

import { useAuth } from '@/context/auth-context';
import { Card, Heading, Text } from '@radix-ui/themes';
import { AlertCircle, CheckCircle2, Eye, EyeOff, Lock } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import React, { useId, useState } from 'react';

export default function ResetPasswordPage() {
  const router = useRouter();
  const { updatePassword, user, isLoading, isConfigured } = useAuth();

  const passwordId = useId();
  const helpId = useId();
  const toggleWarningId = useId();
  const errorId = useId();

  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isConfigured) {
      setError('Supabase environment variables are not configured.');
      return;
    }
    if (password.length < 8) {
      setError('New password must be at least 8 characters long.');
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      const { error: updateErr } = await updatePassword(password);
      if (updateErr) {
        setError(updateErr.message);
      } else {
        setSuccess(true);
        setTimeout(() => {
          router.push('/settings');
        }, 1500);
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center p-6 py-12">
      <div className="max-w-md w-full space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex p-3 rounded-2xl bg-primary/10 text-primary border border-primary/20 mb-2">
            <Lock className="w-8 h-8" />
          </div>
          <Heading as="h1" size="7" className="tracking-tight">
            Set a new password
          </Heading>
          <Text size="2" color="gray" as="p">
            Choose a strong password for your Echo Gate account.
          </Text>
        </div>

        <Card size="3" className="p-6 border-border/80 space-y-5">
          {success ? (
            <div className="text-center space-y-3 py-2">
              <div className="mx-auto w-11 h-11 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center border border-emerald-500/20">
                <CheckCircle2 className="w-5 h-5" />
              </div>
              <Heading size="4">Password Updated</Heading>
              <Text size="2" color="gray" as="p">
                Redirecting you to your API key settings...
              </Text>
            </div>
          ) : !isLoading && !user ? (
            <div className="text-center space-y-4 py-2">
              <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs flex items-center gap-2 text-left">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>
                  Open the password recovery link sent to your email to verify
                  your session before updating your password.
                </span>
              </div>
              <Link
                href="/forgot-password"
                className="inline-block text-xs font-medium text-primary hover:underline"
              >
                Request a new reset link
              </Link>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
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
                  htmlFor={passwordId}
                  className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5"
                >
                  <Lock className="w-3.5 h-3.5" aria-hidden="true" />
                  <span>New Password</span>
                </label>
                <div className="relative">
                  <input
                    id={passwordId}
                    name="new-password"
                    type={showPassword ? 'text' : 'password'}
                    required
                    minLength={8}
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value);
                      if (error) setError(null);
                    }}
                    placeholder="At least 8 characters"
                    autoComplete="new-password"
                    enterKeyHint="done"
                    aria-describedby={error ? `${helpId} ${errorId}` : helpId}
                    className="w-full min-h-12 rounded-xl border border-border bg-background/60 pl-3.5 pr-12 py-2.5 text-base text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((prev) => !prev)}
                    aria-pressed={showPassword}
                    aria-label={
                      showPassword ? 'Hide password' : 'Show password'
                    }
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
                <p id={helpId} className="text-xs text-muted-foreground">
                  Must be at least 8 characters.
                </p>
              </div>

              <button
                type="submit"
                disabled={isSubmitting}
                className="w-full min-h-12 inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-md shadow-blue-500/20 disabled:opacity-60 transition-all cursor-pointer"
              >
                {isSubmitting ? 'Updating password...' : 'Update password'}
              </button>
            </form>
          )}
        </Card>
      </div>
    </div>
  );
}
