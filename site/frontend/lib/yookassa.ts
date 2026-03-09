import { PREMIUM_PLANS, type PremiumPlanId } from '@/config/pricing';

const YOOKASSA_API = 'https://api.yookassa.ru/v3/payments';

type CreateYooPaymentParams = {
  planId: PremiumPlanId;
  userId: string;
  returnUrl: string;
};

type YooCreatePaymentResponse = {
  id: string;
  status: string;
  confirmation?: {
    type?: string;
    confirmation_url?: string;
  };
};

export function addMonths(date: Date, months: number): Date {
  const d = new Date(date);
  d.setMonth(d.getMonth() + months);
  return d;
}

export async function createYooPayment({
  planId,
  userId,
  returnUrl,
}: CreateYooPaymentParams): Promise<YooCreatePaymentResponse> {
  const shopId = process.env.YOOKASSA_SHOP_ID;
  const secretKey = process.env.YOOKASSA_SECRET_KEY;
  if (!shopId || !secretKey) {
    throw new Error('YooKassa credentials are not configured');
  }

  const plan = PREMIUM_PLANS[planId];
  const payload = {
    amount: { value: plan.priceRub.toFixed(2), currency: 'RUB' },
    capture: true,
    confirmation: { type: 'redirect', return_url: returnUrl },
    description: `Подписка ${plan.name} (${plan.period})`,
    metadata: { planId, userId },
  };

  const auth = Buffer.from(`${shopId}:${secretKey}`).toString('base64');
  const response = await fetch(YOOKASSA_API, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Basic ${auth}`,
      'Idempotence-Key': crypto.randomUUID(),
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`YooKassa error: ${errorText}`);
  }

  return (await response.json()) as YooCreatePaymentResponse;
}
