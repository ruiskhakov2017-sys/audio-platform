const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';
const AUTH_ACCESS_KEY = 'auth_access_token';

export type AdminStats = {
  total_users: number;
  premium_users: number;
  total_stories: number;
  total_listen_seconds: number;
};

export async function fetchAdminStatsApi(): Promise<AdminStats | null> {
  if (!API_BASE) return null;
  const token = typeof window !== 'undefined' ? localStorage.getItem(AUTH_ACCESS_KEY) : null;
  if (!token) return null;
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/admin/stats/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return null;
  return res.json();
}
