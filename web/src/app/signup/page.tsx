'use client';

import { useAuth } from '@/context/auth-context';
import { Button, Card, Heading, Text, TextField } from '@radix-ui/themes';
import { AlertCircle, CheckCircle2, Lock, Mail, UserPlus } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import React, { useState } from 'react';

export default function SignUpPage() {
  const router = useRouter();
  const { signUpWithEmail, isConfigured, user } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (user) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 bg-background">
        <Card
          size="3"
          className="max-w-md w-full p-8 border-border text-center space-y-4"
        >
          <div className="mx-auto w-12 h-12 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center border border-emerald-500/20">
            <CheckCircle2 className="w-6 h-6" />
          </div>
          <Heading size="5">Already Signed In</Heading>
          <Text size="2" color="gray">
            You are signed in as{' '}
            <span className="text-foreground font-medium">{user.email}</span>.
          </Text>
          <div className="pt-2 flex justify-center gap-3">
            <Button
              onClick={() => router.push('/settings')}
              variant="solid"
              color="blue"
            >
              Go to Settings & API Keys
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) {
      setError('Please provide both email and password.');
      return;
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters long.');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const { error: signUpError } = await signUpWithEmail(
        email.trim(),
        password
      );
      if (signUpError) {
        setError(signUpError.message || 'Failed to create account.');
      } else {
        setSuccess(
          'Account created! If email confirmation is enabled on your Supabase project, check your inbox. Otherwise you can sign in right away.'
        );
        setTimeout(() => {
          router.push('/settings');
        }, 2000);
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : 'An unexpected error occurred.'
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center p-6 bg-background">
      <div className="max-w-md w-full space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex p-3 rounded-2xl bg-primary/10 text-primary border border-primary/20 mb-2">
            <UserPlus className="w-8 h-8" />
          </div>
          <Heading size="7" className="tracking-tight">
            Create an Account
          </Heading>
          <Text size="2" color="gray">
            Sign up to provision a personal tenant, generate API keys, and track
            LLM usage.
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
                  Provide{' '}
                  <code className="bg-black/30 px-1 py-0.5 rounded">
                    NEXT_PUBLIC_SUPABASE_URL
                  </code>{' '}
                  and{' '}
                  <code className="bg-black/30 px-1 py-0.5 rounded">
                    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
                  </code>{' '}
                  in your environment.
                </p>
              </div>
            </div>
          </Card>
        )}

        <form onSubmit={handleSubmit}>
          <Card size="3" className="p-6 border-border/80 space-y-5">
            {error && (
              <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            {success && (
              <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>{success}</span>
              </div>
            )}

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                <Mail className="w-3.5 h-3.5" /> Email
              </label>
              <TextField.Root
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@company.com"
                className="text-sm"
                autoComplete="email"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                <Lock className="w-3.5 h-3.5" /> Password
              </label>
              <TextField.Root
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="At least 6 characters"
                className="text-sm"
                autoComplete="new-password"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                <Lock className="w-3.5 h-3.5" /> Confirm Password
              </label>
              <TextField.Root
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm your password"
                className="text-sm"
                autoComplete="new-password"
              />
            </div>

            <Button
              type="submit"
              disabled={isSubmitting || !isConfigured}
              color="blue"
              variant="solid"
              size="3"
              className="w-full justify-center mt-2"
            >
              {isSubmitting ? 'Creating account...' : 'Create Account'}
            </Button>

            <div className="pt-2 text-center text-xs text-muted-foreground">
              Already have an account?{' '}
              <Link
                href="/login"
                className="text-primary font-medium hover:underline"
              >
                Sign in
              </Link>
            </div>
          </Card>
        </form>
      </div>
    </div>
  );
}
