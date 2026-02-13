const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

export async function requestPasswordResetApi(email: string): Promise<{ ok: true } | { error: string }> {
  if (!API_BASE) return { error: 'API не настроен' };
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/auth/password-reset/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { error: (data.detail || 'Ошибка') as string };
  return { ok: true };
}

export async function confirmPasswordResetApi(
  uid: string,
  token: string,
  newPassword: string
): Promise<{ ok: true } | { error: string }> {
  if (!API_BASE) return { error: 'API не настроен' };
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/auth/password-reset-confirm/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uid, token, new_password: newPassword }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { error: (data.detail || 'Ошибка') as string };
  return { ok: true };
}
