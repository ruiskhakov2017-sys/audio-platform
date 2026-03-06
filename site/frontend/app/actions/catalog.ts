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

/** Топ-12 по прослушиваниям (listens_count desc, created_at desc). Если колонки listens_count нет или все 0 — по дате добавления. */
export async function getTopStories(limit = 12): Promise<Story[]> {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) return [];
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const { data: byListens, error: errListens } = await supabase
    .from('stories')
    .select('*')
    .order('listens_count', { ascending: false, nullsFirst: false })
    .order('created_at', { ascending: false })
    .limit(limit);
  if (!errListens && byListens?.length) {
    return byListens.map((row: Record<string, unknown>) => mapRowToStory(row as Parameters<typeof mapRowToStory>[0]));
  }
  const { data: byDate, error: errDate } = await supabase
    .from('stories')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(limit);
  if (errDate || !byDate) return [];
  return byDate.map((row: Record<string, unknown>) => mapRowToStory(row as Parameters<typeof mapRowToStory>[0]));
}

export type BrowseFilter = 'popular' | 'new' | 'free' | 'editor';

/** Список рассказов для страницы каталога по выбранному фильтру. */
export async function getStoriesForBrowse(filter: BrowseFilter): Promise<Story[]> {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) return [];
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  let query = supabase.from('stories').select('*');

  switch (filter) {
    case 'popular':
      query = query
        .order('listens_count', { ascending: false, nullsFirst: false })
        .order('created_at', { ascending: false });
      break;
    case 'new':
      query = query.order('created_at', { ascending: false });
      break;
    case 'free':
      query = query.eq('is_premium', false).order('created_at', { ascending: false });
      break;
    case 'editor':
      query = query.eq('is_editors_choice', true).order('created_at', { ascending: false });
      break;
    default:
      query = query.order('created_at', { ascending: false });
  }

  const { data, error } = await query.limit(500);
  if (error) return [];
  return (data ?? []).map((row: Record<string, unknown>) => mapRowToStory(row as Parameters<typeof mapRowToStory>[0]));
}

/** Инкремент счётчика прослушиваний у рассказа (при открытии страницы или при нажатии Play). */
export async function incrementListensCount(storyId: number): Promise<void> {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_KEY) return;
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
  const { data: row } = await supabase
    .from('stories')
    .select('listens_count')
    .eq('id', storyId)
    .single();
  if (!row) return;
  const next = (Number((row as { listens_count?: number }).listens_count) || 0) + 1;
  await supabase.from('stories').update({ listens_count: next }).eq('id', storyId);
}
