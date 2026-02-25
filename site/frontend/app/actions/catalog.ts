'use server';

import { createClient } from '@supabase/supabase-js';
import { mapRowToStory } from '@/lib/stories';
import type { Story } from '@/types/story';

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const SUPABASE_SERVICE_KEY =
  process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

/** Загрузка списка рассказов из Supabase для каталога (с service role, обходит RLS). */
export async function getStoriesForCatalog(): Promise<Story[]> {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) return [];
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const { data, error } = await supabase
    .from('stories')
    .select('*')
    .order('created_at', { ascending: false });
  if (error || !data) return [];
  return data.map((row: Record<string, unknown>) => mapRowToStory(row as Parameters<typeof mapRowToStory>[0]));
}
