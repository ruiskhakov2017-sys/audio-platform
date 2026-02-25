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
  const tags = [...genres, ...tagList];
  return {
    id: row.id,
    slug: slugFromTitle(row.title),
    title: row.title,
    description: row.description ?? '',
    authorName: row.author ?? '',
    coverImage: row.image_url ?? '',
    audioSrc: row.audio_url ?? '',
    durationSec: Number(row.duration) || 0,
    isPremium: Boolean(row.is_premium),
    tags,
  };
}
