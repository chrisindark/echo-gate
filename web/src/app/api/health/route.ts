import { API_ROOT, isGatewayUrlConfigured } from '@/lib/api';
import { isSupabaseConfigured } from '@/lib/supabase';
import { NextResponse } from 'next/server';

export function GET() {
  return NextResponse.json({
    status: 'ok',
    app: 'echo-gate-web',
    supabaseConfigured: isSupabaseConfigured,
    gatewayConfigured: isGatewayUrlConfigured,
    gatewayUrl: API_ROOT,
  });
}
