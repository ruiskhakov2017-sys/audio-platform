import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { ALL_STORIES_FREE_TO_LISTEN } from '@/config/access';

/** Только при ALL_STORIES_FREE_TO_LISTEN: отдать audio_url из таблицы stories (обход secure view). */
export async function GET(req: Request) {
  if (!ALL_STORIES_FREE_TO_LISTEN) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  }

  const url = new URL(req.url);
  const id = url.searchParams.get('id');
  if (!id?.trim()) {
    return NextResponse.json({ error: 'id required' }, { status: 400 });
  }

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !serviceKey) {
    return NextResponse.json({ error: 'Supabase service role not configured' }, { status: 503 });
  }

  const supabase = createClient(supabaseUrl, serviceKey);
  const raw = id.trim();
  const asNum = Number(raw);
  const idFilter = Number.isFinite(asNum) && String(asNum) === raw ? asNum : raw;

  const { data, error } = await supabase.from('stories').select('audio_url').eq('id', idFilter).maybeSingle();
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
  const audioUrl = (data as { audio_url?: string | null } | null)?.audio_url;
  if (!audioUrl?.trim()) {
    return NextResponse.json({ error: 'Not found' }, { status: 404 });
  }

  return NextResponse.json({ audio_url: audioUrl.trim() });
}
