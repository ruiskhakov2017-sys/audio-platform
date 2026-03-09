import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { PREMIUM_PLANS, type PremiumPlanId } from '@/config/pricing';
import { addMonths } from '@/lib/yookassa';

type YooWebhookPayload = {
  event?: string;
  object?: {
    status?: string;
    metadata?: {
      planId?: string;
      userId?: string;
    };
  };
};

function getSupabaseAdmin() {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceKey =
    process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !serviceKey) return null;
  return createClient(supabaseUrl, serviceKey);
}

export async function POST(req: Request) {
  const payload = (await req.json().catch(() => ({}))) as YooWebhookPayload;
  if (payload.event !== 'payment.succeeded' || payload.object?.status !== 'succeeded') {
    return NextResponse.json({ ok: true });
  }

  const planId = payload.object.metadata?.planId as PremiumPlanId | undefined;
  const userId = payload.object.metadata?.userId;
  if (!planId || !PREMIUM_PLANS[planId] || !userId) {
    return NextResponse.json({ error: 'Некорректные metadata' }, { status: 400 });
  }

  const supabase = getSupabaseAdmin();
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase not configured' }, { status: 503 });
  }

  const premiumUntil = addMonths(new Date(), PREMIUM_PLANS[planId].durationMonths).toISOString();
  const subscriptionStatus = 'active';

  const { data: authUser } = await supabase.auth.admin.getUserById(userId);
  const currentMeta = (authUser.user?.user_metadata ?? {}) as Record<string, unknown>;

  await supabase.auth.admin.updateUserById(userId, {
    user_metadata: {
      ...currentMeta,
      is_premium: true,
      subscriptionStatus,
      premiumUntil,
    },
  });

  await supabase
    .from('profiles')
    .upsert(
      {
        id: userId,
        subscription_status: subscriptionStatus,
        premium_until: premiumUntil,
      },
      { onConflict: 'id' }
    );

  await supabase
    .from('users')
    .update({
      subscription_status: subscriptionStatus,
      premium_until: premiumUntil,
    })
    .eq('id', userId);

  return NextResponse.json({ ok: true });
}
