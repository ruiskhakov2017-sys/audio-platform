import type { Story } from '@/types/story';

export type StoryRow = {
  id: number;
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
  const id = typeof row.id === 'number' ? row.id : Number(row.id) || 0;
  return {
    id,
    slug: slugFromTitle(String(row.title ?? '')),
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
  };
}

/** Все метки для отображения и фильтрации (жанры + теги) */
export function getDisplayTags(story: Story): string[] {
  return [...(story.genres ?? []), ...(story.tags ?? [])];
}
