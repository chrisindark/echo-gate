import { createServerClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import { cookies } from 'next/headers';

export async function createSupabaseServerClient(): Promise<SupabaseClient | null> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim() || '';
  const supabaseAnonKey =
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() || '';

  if (!supabaseUrl || !supabaseAnonKey) {
    return null;
  }

  const cookieStore = await cookies();

  return createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options)
          );
        } catch {
          // Called from a Server Component where setting cookies is not permitted;
          // src/proxy.ts handles refreshing session cookies on navigation.
        }
      },
    },
  });
}
