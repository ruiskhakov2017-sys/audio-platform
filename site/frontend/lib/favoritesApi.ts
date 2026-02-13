const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';
const AUTH_ACCESS_KEY = 'auth_access_token';

function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(AUTH_ACCESS_KEY);
}

export async function fetchFavoritesFromApi(): Promise<number[]> {
  const token = getAccessToken();
  if (!API_BASE || !token) return [];
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/auth/favorites/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data.story_ids) ? data.story_ids : [];
}

export async function toggleFavoriteApi(storySlug: string): Promise<{ is_favorite: boolean } | null> {
  const token = getAccessToken();
  if (!API_BASE || !token) return null;
  const res = await fetch(
    `${API_BASE.replace(/\/$/, '')}/api/stories/${encodeURIComponent(storySlug)}/favorite/`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    }
  );
  if (!res.ok) return null;
  return res.json();
}
