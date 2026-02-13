import type { Story } from '@/types/story';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';
const AUTH_ACCESS_KEY = 'auth_access_token';

function getAuthHeaders(): HeadersInit {
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem(AUTH_ACCESS_KEY);
    if (token) (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export type DjangoStory = {
  id: number;
  title: string;
  slug: string;
  description: string;
  genre: { id: number; name: string; slug: string } | null;
  author: { id: number; name: string; bio: string } | null;
  cover_image_url: string | null;
  audio_file_url: string | null;
  duration: number;
  is_premium: boolean;
  created_at: string;
};

export function mapDjangoStoryToStory(row: DjangoStory): Story {
  const tags = row.genre ? [row.genre.name] : [];
  return {
    id: row.id,
    slug: row.slug,
    title: row.title,
    description: row.description ?? '',
    authorName: row.author?.name ?? '',
    coverImage: row.cover_image_url ?? '',
    audioSrc: row.audio_file_url ?? '',
    durationSec: Number(row.duration) || 0,
    isPremium: Boolean(row.is_premium),
    tags,
  };
}

export async function fetchStoriesFromApi(params?: {
  genre?: string;
  author?: string;
  search?: string;
}): Promise<Story[]> {
  if (!API_BASE) return [];
  const searchParams = new URLSearchParams();
  if (params?.genre) searchParams.set('genre', params.genre);
  if (params?.author) searchParams.set('author', params.author);
  if (params?.search) searchParams.set('search', params.search);
  const qs = searchParams.toString();
  const url = `${API_BASE.replace(/\/$/, '')}/api/stories/${qs ? `?${qs}` : ''}`;
  const res = await fetch(url, { headers: getAuthHeaders(), next: { revalidate: 60 } });
  if (!res.ok) return [];
  const data: DjangoStory[] = await res.json();
  return data.map(mapDjangoStoryToStory);
}

export async function fetchStoryBySlugFromApi(slug: string): Promise<Story | null> {
  if (!API_BASE) return null;
  const url = `${API_BASE.replace(/\/$/, '')}/api/stories/${slug}/`;
  const res = await fetch(url, { headers: getAuthHeaders(), next: { revalidate: 60 } });
  if (!res.ok) return null;
  const data: DjangoStory = await res.json();
  return mapDjangoStoryToStory(data);
}

export async function fetchStoryByIdFromApi(id: number | string): Promise<Story | null> {
  if (!API_BASE) return null;
  const list = await fetchStoriesFromApi();
  const story = list.find((s) => String(s.id) === String(id));
  return story ?? null;
}

export async function fetchRelatedStoriesFromApi(storySlug: string): Promise<Story[]> {
  if (!API_BASE) return [];
  const url = `${API_BASE.replace(/\/$/, '')}/api/stories/${encodeURIComponent(storySlug)}/related/`;
  const res = await fetch(url);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data.map((row: DjangoStory) => mapDjangoStoryToStory(row)) : [];
}

export function useDjangoApi(): boolean {
  return Boolean(API_BASE);
}
