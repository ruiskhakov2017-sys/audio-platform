const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

export type AuthTokens = {
  access: string;
  refresh: string;
};

export type MeResponse = {
  id: number;
  username: string;
  email: string;
  is_premium: boolean;
};

export async function loginWithDjango(
  email: string,
  password: string
): Promise<{ tokens: AuthTokens; user: MeResponse } | { error: string }> {
  if (!API_BASE) return { error: 'API не настроен' };
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/auth/login/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    return { error: (data.detail as string) || data.email?.[0] || res.statusText };
  }
  const tokens: AuthTokens = await res.json();
  const meRes = await fetch(`${API_BASE.replace(/\/$/, '')}/api/auth/me/`, {
    headers: { Authorization: `Bearer ${tokens.access}` },
  });
  if (!meRes.ok) return { error: 'Не удалось загрузить профиль' };
  const user: MeResponse = await meRes.json();
  return { tokens, user };
}

export async function registerWithDjango(
  email: string,
  password: string
): Promise<{ tokens: AuthTokens; user: MeResponse } | { error: string }> {
  if (!API_BASE) return { error: 'API не настроен' };
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/auth/register/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const msg = data.email?.[0] || data.password?.[0] || data.detail || res.statusText;
    return { error: String(msg) };
  }
  const user = await res.json();
  const loginResult = await loginWithDjango(email, password);
  if ('error' in loginResult) return { tokens: { access: '', refresh: '' }, user };
  return loginResult;
}

export async function fetchMeWithDjango(accessToken: string): Promise<MeResponse | null> {
  if (!API_BASE || !accessToken) return null;
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/auth/me/`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) return null;
  return res.json();
}
