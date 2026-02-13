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

export type ReviewItem = {
  id: number;
  user_email: string;
  rating: number;
  text: string;
  created_at: string;
};

export async function fetchReviewsByStoryId(storyId: number): Promise<ReviewItem[]> {
  if (!API_BASE) return [];
  const url = `${API_BASE.replace(/\/$/, '')}/api/reviews/?story_id=${storyId}`;
  const res = await fetch(url);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

export async function submitReviewApi(payload: {
  story: number;
  rating: number;
  text: string;
}): Promise<ReviewItem | { error: string }> {
  if (!API_BASE) return { error: 'API не настроен' };
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/reviews/`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) return { error: (data.detail || data.non_field_errors?.[0] || 'Ошибка отправки') };
  return data as ReviewItem;
}
