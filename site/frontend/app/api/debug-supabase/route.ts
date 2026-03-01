import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

/** GET /api/debug-supabase — проверка env и количества записей (без секретов). Удалить после отладки. */
export async function GET() {
  const hasUrl = Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL);
  const hasServiceKey = Boolean(process.env.SUPABASE_SERVICE_ROLE_KEY);
  const hasAnonKey = Boolean(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

  const out: { hasUrl: boolean; hasServiceKey: boolean; hasAnonKey: boolean; storyCount?: number; error?: string } = {
    hasUrl,
    hasServiceKey,
    hasAnonKey,
  };

  if (!hasUrl || (!hasServiceKey && !hasAnonKey)) {
    return NextResponse.json(out);
  }

  const key = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  const supabase = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, key);
  const { count, error } = await supabase.from('stories').select('*', { count: 'exact', head: true });

  if (error) {
    out.error = error.message;
    return NextResponse.json(out);
  }
  out.storyCount = count ?? 0;
  return NextResponse.json(out);
}
