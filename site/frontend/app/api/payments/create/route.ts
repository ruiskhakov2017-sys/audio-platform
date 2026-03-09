import { NextResponse } from 'next/server';
import { PREMIUM_PLANS, type PremiumPlanId } from '@/config/pricing';
import { createYooPayment } from '@/lib/yookassa';

type CreatePaymentRequest = {
  planId?: string;
  userId?: string;
};

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as CreatePaymentRequest;
  const planId = body.planId as PremiumPlanId | undefined;
  const userId = body.userId;

  if (!planId || !PREMIUM_PLANS[planId]) {
    return NextResponse.json({ error: 'Некорректный тариф' }, { status: 400 });
  }
  if (!userId) {
    return NextResponse.json({ error: 'userId обязателен' }, { status: 400 });
  }

  try {
    const appUrl = process.env.NEXT_PUBLIC_APP_URL || 'http://localhost:3000';
    const payment = await createYooPayment({
      planId,
      userId,
      returnUrl: `${appUrl.replace(/\/$/, '')}/profile`,
    });
    return NextResponse.json({
      paymentId: payment.id,
      status: payment.status,
      confirmationUrl: payment.confirmation?.confirmation_url ?? null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Ошибка создания платежа';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
