import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim() || '';
const supabaseAnonKey =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() || '';

export const isSupabaseConfigured = Boolean(supabaseUrl && supabaseAnonKey);

export const configuredOAuthProviders: ('github' | 'google')[] = (
  process.env.NEXT_PUBLIC_SUPABASE_OAUTH_PROVIDERS || ''
)
  .split(',')
  .map((p) => p.trim().toLowerCase())
  .filter((p): p is 'github' | 'google' => p === 'github' || p === 'google');

let supabaseInstance: SupabaseClient | null = null;

export function getSupabaseClient(): SupabaseClient | null {
  if (!isSupabaseConfigured) {
    return null;
  }
  if (!supabaseInstance) {
    supabaseInstance = createBrowserClient(supabaseUrl, supabaseAnonKey);
  }
  return supabaseInstance;
}
