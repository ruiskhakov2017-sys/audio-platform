import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { mapRowToStory } from '@/lib/stories';

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const SUPABASE_SERVICE_KEY =
  process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

/** GET /api/stories — список рассказов для каталога (service role, обходит RLS). */
export async function GET() {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) {
    return NextResponse.json({ error: 'Supabase not configured' }, { status: 503 });
  }
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const { data, error } = await supabase
    .from('stories')
    .select('*')
    .order('created_at', { ascending: false });

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const stories = (data ?? []).map((row: Record<string, unknown>) =>
    mapRowToStory(row as Parameters<typeof mapRowToStory>[0])
  );
  return NextResponse.json(stories);
}
