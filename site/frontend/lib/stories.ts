import type { Story } from '@/types/story';
import { ALL_STORIES_FREE_TO_LISTEN } from '@/config/access';

export type StoryRow = {
  id: number;
  title: string;
  author?: string | null;
  description?: string | null;
  genre?: string | null;
  tags?: string[] | null;
  image_url: string;
  audio_url: string | null;
  duration: number;
  is_premium: boolean;
  plays_count?: number | null;
  created_at?: string;
  genres?: string[] | null;
  listens_count?: number | null;
  is_editors_choice?: boolean | null;
  text_url?: string | null;
};

function slugFromTitle(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9\u0400-\u04ff]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function mapRowToStory(row: StoryRow): Story {
  const genres = Array.isArray(row.genres) ? row.genres : row.genre ? [row.genre] : [];
  const tagList = Array.isArray(row.tags) ? row.tags : [];
  const rawIdVal = row.id as unknown;
  const rawId = String(rawIdVal);
  let id = 0;
  if (typeof rawIdVal === 'number' && Number.isFinite(rawIdVal)) {
    id = rawIdVal;
  } else if (typeof rawIdVal === 'string') {
    const numeric = Number(rawIdVal);
    if (Number.isFinite(numeric)) {
      id = numeric;
    } else {
      let hash = 0;
      for (let i = 0; i < rawIdVal.length; i += 1) {
        hash = (hash * 31 + rawIdVal.charCodeAt(i)) >>> 0;
      }
      id = hash || 1;
    }
  }
  const generatedSlug = slugFromTitle(String(row.title ?? ''));
  const slug = `${generatedSlug}-${id}`;
  return {
    id,
    rawId,
    slug,
    title: String(row.title ?? ''),
    description: String(row.description ?? ''),
    authorName: String(row.author ?? ''),
    coverImage: String(row.image_url ?? ''),
    audioSrc: row.audio_url ? String(row.audio_url) : '',
    durationSec: Number(row.duration) || 0,
    isPremium: ALL_STORIES_FREE_TO_LISTEN ? false : Boolean(row.is_premium),
    genres,
    tags: tagList,
    ...(row.listens_count != null && { listensCount: Number(row.listens_count) || 0 }),
  };
}

/** Все метки для отображения и фильтрации (жанры + теги) */
export function getDisplayTags(story: Story): string[] {
  return [...(story.genres ?? []), ...(story.tags ?? [])];
}
