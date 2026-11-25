'use client';

import {
  configuredOAuthProviders,
  getSupabaseClient,
  isSupabaseConfigured,
} from '@/lib/supabase';
import { getUserProfile, UserProfile } from '@/lib/user-api';
import type { Session, User } from '@supabase/supabase-js';
import React, { createContext, useContext, useEffect, useState } from 'react';

interface SignUpResult {
  error: Error | null;
  needsEmailConfirmation: boolean;
}

interface AuthContextType {
  user: User | null;
  session: Session | null;
  accessToken: string | null;
  tenantId: string | null;
  profile: UserProfile | null;
  isLoading: boolean;
  isConfigured: boolean;
  oauthProviders: ('github' | 'google')[];
  signInWithEmail: (
    email: string,
    password: string
  ) => Promise<{ error: Error | null }>;
  signUpWithEmail: (
    email: string,
    password: string,
    fullName?: string
  ) => Promise<SignUpResult>;
  signInWithOAuth: (
    provider: 'github' | 'google',
    nextPath?: string
  ) => Promise<{ error: Error | null }>;
  resendSignupConfirmation: (email: string) => Promise<{ error: Error | null }>;
  resetPasswordForEmail: (email: string) => Promise<{ error: Error | null }>;
  updatePassword: (password: string) => Promise<{ error: Error | null }>;
  signOut: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

function getOrigin(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return process.env.NEXT_PUBLIC_APP_URL?.replace(/\/+$/, '') || '';
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(
    () => isSupabaseConfigured
  );

  const supabase = getSupabaseClient();

  const fetchProfile = async (token: string) => {
    try {
      const p = await getUserProfile(token);
      setProfile(p);
    } catch {
      // Backend may be offline or unconfigured; fallback to user metadata or id
      setProfile(null);
    }
  };

  useEffect(() => {
    if (!supabase) {
      return;
    }

    let isMounted = true;

    supabase.auth
      .getSession()
      .then(({ data: { session: initialSession } }) => {
        if (!isMounted) return;
        setSession(initialSession);
        setUser(initialSession?.user ?? null);
        if (initialSession?.access_token) {
          fetchProfile(initialSession.access_token).finally(() => {
            if (isMounted) setIsLoading(false);
          });
        } else {
          setIsLoading(false);
        }
      })
      .catch(() => {
        if (isMounted) setIsLoading(false);
      });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, currentSession) => {
      setSession(currentSession);
      setUser(currentSession?.user ?? null);
      if (currentSession?.access_token) {
        fetchProfile(currentSession.access_token);
      } else {
        setProfile(null);
      }
      setIsLoading(false);
    });

    return () => {
      isMounted = false;
      subscription.unsubscribe();
    };
  }, [supabase]);

  const signInWithEmail = async (email: string, password: string) => {
    if (!supabase) {
      return { error: new Error('Supabase is not configured.') };
    }
    const { error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    return { error: error ? new Error(error.message) : null };
  };

  const signUpWithEmail = async (
    email: string,
    password: string,
    fullName?: string
  ): Promise<SignUpResult> => {
    if (!supabase) {
      return {
        error: new Error('Supabase is not configured.'),
        needsEmailConfirmation: false,
      };
    }
    const origin = getOrigin();
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        emailRedirectTo: origin
          ? `${origin}/auth/callback?next=/settings`
          : undefined,
        data: fullName?.trim() ? { full_name: fullName.trim() } : undefined,
      },
    });
    if (error) {
      return { error: new Error(error.message), needsEmailConfirmation: false };
    }
    return {
      error: null,
      needsEmailConfirmation: Boolean(data.user && !data.session),
    };
  };

  const signInWithOAuth = async (
    provider: 'github' | 'google',
    nextPath = '/settings'
  ) => {
    if (!supabase) {
      return { error: new Error('Supabase is not configured.') };
    }
    const origin = getOrigin();
    const safeNext =
      nextPath.startsWith('/') && !nextPath.startsWith('//')
        ? nextPath
        : '/settings';
    const { error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: origin
          ? `${origin}/auth/callback?next=${encodeURIComponent(safeNext)}`
          : undefined,
      },
    });
    return { error: error ? new Error(error.message) : null };
  };

  const resendSignupConfirmation = async (email: string) => {
    if (!supabase) {
      return { error: new Error('Supabase is not configured.') };
    }
    const origin = getOrigin();
    const { error } = await supabase.auth.resend({
      type: 'signup',
      email,
      options: {
        emailRedirectTo: origin
          ? `${origin}/auth/callback?next=/settings`
          : undefined,
      },
    });
    return { error: error ? new Error(error.message) : null };
  };

  const resetPasswordForEmail = async (email: string) => {
    if (!supabase) {
      return { error: new Error('Supabase is not configured.') };
    }
    const origin = getOrigin();
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: origin
        ? `${origin}/auth/callback?next=/reset-password`
        : undefined,
    });
    return { error: error ? new Error(error.message) : null };
  };

  const updatePassword = async (password: string) => {
    if (!supabase) {
      return { error: new Error('Supabase is not configured.') };
    }
    const { error } = await supabase.auth.updateUser({ password });
    return { error: error ? new Error(error.message) : null };
  };

  const signOut = async () => {
    if (supabase) {
      await supabase.auth.signOut();
    }
    setUser(null);
    setSession(null);
    setProfile(null);
  };

  const refreshProfile = async () => {
    if (session?.access_token) {
      await fetchProfile(session.access_token);
    }
  };

  const tenantId = profile?.tenant_id || (user?.id ? `usr_${user.id}` : null);

  return (
    <AuthContext.Provider
      value={{
        user,
        session,
        accessToken: session?.access_token ?? null,
        tenantId,
        profile,
        isLoading,
        isConfigured: isSupabaseConfigured,
        oauthProviders: configuredOAuthProviders,
        signInWithEmail,
        signUpWithEmail,
        signInWithOAuth,
        resendSignupConfirmation,
        resetPasswordForEmail,
        updatePassword,
        signOut,
        refreshProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
