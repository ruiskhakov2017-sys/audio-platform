const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';
const AUTH_ACCESS_KEY = 'auth_access_token';

export async function mockPaymentApi(planId: string): Promise<{ success: true } | { error: string }> {
  if (!API_BASE) return { error: 'API не настроен' };
  const token = typeof window !== 'undefined' ? localStorage.getItem(AUTH_ACCESS_KEY) : null;
  if (!token) return { error: 'Требуется авторизация' };
  const res = await fetch(`${API_BASE.replace(/\/$/, '')}/api/payment/mock/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ plan_id: planId }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    return { error: (data.detail || 'Ошибка оплаты') as string };
  }
  return { success: true };
}
