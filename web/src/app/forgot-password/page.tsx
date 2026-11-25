'use client';

import { useAuth } from '@/context/auth-context';
import { Card, Heading, Text } from '@radix-ui/themes';
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  KeyRound,
  Mail,
} from 'lucide-react';
import Link from 'next/link';
import React, { useId, useState } from 'react';

export default function ForgotPasswordPage() {
  const { resetPasswordForEmail, isConfigured } = useAuth();
  const emailId = useId();
  const statusId = useId();

  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isConfigured) {
      setError('Supabase environment variables are not configured yet.');
      return;
    }
    if (!email.trim()) {
      setError('Please enter your account email address.');
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      const { error: resetError } = await resetPasswordForEmail(email.trim());
      if (resetError) {
        setError(resetError.message);
      } else {
        setSentTo(email.trim());
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
            <KeyRound className="w-8 h-8" />
          </div>
          <Heading as="h1" size="7" className="tracking-tight">
            Reset your password
          </Heading>
          <Text size="2" color="gray" as="p">
            Enter your email and we&apos;ll send a secure Supabase recovery
            link.
          </Text>
        </div>

        <Card size="3" className="p-6 border-border/80 space-y-5">
          {sentTo ? (
            <div className="text-center space-y-4 py-2">
              <div className="mx-auto w-11 h-11 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center border border-emerald-500/20">
                <CheckCircle2 className="w-5 h-5" />
              </div>
              <div className="space-y-1.5">
                <Heading size="4">Check your email</Heading>
                <Text size="2" color="gray" as="p" className="leading-relaxed">
                  If an account exists for{' '}
                  <span className="text-foreground font-medium">{sentTo}</span>,
                  a password reset link is on its way.
                </Text>
              </div>
              <Link
                href="/login"
                className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                Return to Sign in
              </Link>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              {error && (
                <div
                  id={statusId}
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
                  <span>Account Email</span>
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
                  enterKeyHint="done"
                  className="w-full min-h-12 rounded-xl border border-border bg-background/60 px-3.5 py-2.5 text-base text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2"
                />
              </div>

              <button
                type="submit"
                disabled={isSubmitting}
                className="w-full min-h-12 inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-md shadow-blue-500/20 disabled:opacity-60 transition-all cursor-pointer"
              >
                {isSubmitting ? 'Sending reset link...' : 'Send reset link'}
              </button>

              <div className="pt-1 text-center">
                <Link
                  href="/login"
                  className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Sign in
                </Link>
              </div>
            </form>
          )}
        </Card>
      </div>
    </div>
  );
}
