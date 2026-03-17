import type { Story } from '@/types/story';

export type StoryRow = {
  id: number;
  slug?: string | null;
  title: string;
  author?: string | null;
  genre?: string | null;
  genres?: string[] | null;
  image_url: string;
  audio_url: string;
  duration: number;
  is_premium: boolean;
  description?: string | null;
  tags?: string[] | null;
  content?: string | null;
  created_at?: string;
  listens_count?: number | null;
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
  const safeSlug = String(row.slug ?? '').trim();
  const generatedSlug = slugFromTitle(String(row.title ?? ''));
  const slug = safeSlug || `${generatedSlug}-${id}`;
  return {
    id,
    rawId,
    slug,
    title: String(row.title ?? ''),
    description: String(row.description ?? ''),
    authorName: String(row.author ?? ''),
    coverImage: String(row.image_url ?? ''),
    audioSrc: String(row.audio_url ?? ''),
    durationSec: Number(row.duration) || 0,
    isPremium: Boolean(row.is_premium),
    genres,
    tags: tagList,
    ...(row.content != null && { content: String(row.content) }),
    ...(row.listens_count != null && { listensCount: Number(row.listens_count) || 0 }),
  };
}

/** Все метки для отображения и фильтрации (жанры + теги) */
export function getDisplayTags(story: Story): string[] {
  return [...(story.genres ?? []), ...(story.tags ?? [])];
}
