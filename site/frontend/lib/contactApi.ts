const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

export async function submitContactApi(payload: {
  email: string;
  subject: string;
  message: string;
}): Promise<{ ok: true } | { error: string }> {
  if (!API_BASE) return { error: 'API не настроен' };
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/contact/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    return { error: (data.detail || 'Ошибка отправки') as string };
  }
  return { ok: true };
}
