import { createSupabaseServerClient } from '@/lib/supabase-server';
import type { EmailOtpType } from '@supabase/supabase-js';
import { NextResponse, type NextRequest } from 'next/server';

function getSafeNextPath(nextParam: string | null): string {
  if (!nextParam || !nextParam.startsWith('/') || nextParam.startsWith('//')) {
    return '/settings';
  }
  return nextParam;
}

export async function GET(request: NextRequest) {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get('code');
  const tokenHash = requestUrl.searchParams.get('token_hash');
  const type = requestUrl.searchParams.get('type') as EmailOtpType | null;
  const nextPath = getSafeNextPath(requestUrl.searchParams.get('next'));
  const errorDescription =
    requestUrl.searchParams.get('error_description') ||
    requestUrl.searchParams.get('error');

  if (errorDescription) {
    const loginUrl = new URL('/login', requestUrl.origin);
    loginUrl.searchParams.set('error', errorDescription);
    return NextResponse.redirect(loginUrl);
  }

  const supabase = await createSupabaseServerClient();
  if (!supabase) {
    return NextResponse.redirect(new URL('/login', requestUrl.origin));
  }

  if (code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(new URL(nextPath, requestUrl.origin));
    }
    const loginUrl = new URL('/login', requestUrl.origin);
    loginUrl.searchParams.set('error', error.message);
    return NextResponse.redirect(loginUrl);
  }

  if (tokenHash && type) {
    const { error } = await supabase.auth.verifyOtp({
      type,
      token_hash: tokenHash,
    });
    if (!error) {
      return NextResponse.redirect(new URL(nextPath, requestUrl.origin));
    }
    const loginUrl = new URL('/login', requestUrl.origin);
    loginUrl.searchParams.set('error', error.message);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.redirect(new URL(nextPath, requestUrl.origin));
}
