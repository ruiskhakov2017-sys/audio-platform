'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Header } from '@/components/layout/Header';
import { useAuthStore } from '@/store/authStore';
import { PREMIUM_PLAN_LIST, formatRub } from '@/config/pricing';
import { Check, Zap, Crown, Infinity } from 'lucide-react';

const PLAN_FEATURES: Record<string, string[]> = {
  tainyi: ['Доступ к эксклюзивным историям', 'Базовое качество звука'],
  gryaznyi: ['Всё из тарифа «Тайный»', 'HD озвучка', 'Экономия 17%'],
  oderzhimyi: ['Всё из тарифа «Грязный»', 'Скидка 50% на озвучку сценариев', 'Максимальный приоритет поддержки'],
};

const PLAN_ICONS = {
  tainyi: Zap,
  gryaznyi: Crown,
  oderzhimyi: Infinity,
} as const;

const PLANS = PREMIUM_PLAN_LIST.map((plan) => ({
  id: plan.id,
  name: plan.name,
  price: formatRub(plan.priceRub),
  period: plan.period,
  icon: PLAN_ICONS[plan.id],
  features: PLAN_FEATURES[plan.id],
  cta: 'Выбрать',
  highlighted: plan.id === 'gryaznyi',
}));

export default function PricingPage() {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const user = useAuthStore((s) => s.user);
  const [payingPlanId, setPayingPlanId] = useState<string | null>(null);

  const handleBuyClick = async (planId: string) => {
    if (!isAuthenticated || !user?.id) {
      router.push('/login');
      return;
    }
    setPayingPlanId(planId);
    const res = await fetch('/api/payments/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ planId, userId: user.id }),
    });
    const payload = await res.json().catch(() => ({} as { confirmationUrl?: string }));
    setPayingPlanId(null);
    if (!res.ok || !payload.confirmationUrl) return;
    window.location.href = payload.confirmationUrl;
  };

  return (
    <div className="min-h-screen">
      <Header />
      <main className="relative z-10 pt-28 pb-20 px-4">
        <div className="max-w-5xl mx-auto">
          <h1 className="font-heading text-4xl md:text-5xl font-bold text-center text-white mb-4">
            Тарифы
          </h1>
          <p className="text-zinc-400 text-center mb-14 max-w-xl mx-auto">
            Выберите подходящий план и откройте доступ ко всем историям.
          </p>

          <div className="grid md:grid-cols-3 gap-6 md:gap-8">
            {PLANS.map((plan) => {
              const Icon = plan.icon;

              return (
                <div
                  key={plan.id}
                  className={`relative rounded-2xl border p-6 md:p-8 flex flex-col transition-all ${plan.highlighted
                      ? 'border-[#00B4D8]/60 bg-[#00B4D8]/5 shadow-[0_0_40px_rgba(0,180,216,0.15)] scale-[1.02]'
                      : 'border-white/10 bg-white/5 hover:border-white/20'
                    }`}
                >
                  {plan.highlighted && (
                    <span className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 rounded-full bg-[#00B4D8] text-black text-xs font-semibold">
                      Популярный
                    </span>
                  )}
                  <div className="flex items-center gap-3 mb-6">
                    <span
                      className={`flex h-10 w-10 items-center justify-center rounded-xl ${plan.highlighted ? 'bg-[#00B4D8]/20 text-[#00B4D8]' : 'bg-white/10 text-zinc-400'
                        }`}
                    >
                      <Icon className="w-5 h-5" strokeWidth={2} />
                    </span>
                    <h2 className="font-heading text-xl font-bold text-white">{plan.name}</h2>
                  </div>
                  <div className="mb-2">
                    <span className="text-3xl font-bold text-white">{plan.price}</span>
                    <span className="text-zinc-400 text-sm ml-1">{plan.period}</span>
                  </div>
                  <ul className="space-y-3 mb-8 flex-1">
                    {plan.features.map((f) => (
                      <li key={f} className="flex items-start gap-2 text-sm text-zinc-300">
                        <Check
                          className={`w-5 h-5 shrink-0 mt-0.5 ${plan.highlighted ? 'text-[#00B4D8]' : 'text-zinc-500'
                            }`}
                          strokeWidth={2}
                        />
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                  <button
                    type="button"
                    onClick={() => handleBuyClick(plan.id)}
                    disabled={!!payingPlanId}
                    className={`block w-full py-3.5 px-4 rounded-full font-semibold text-center transition-colors disabled:opacity-50 ${plan.highlighted
                        ? 'bg-[#00B4D8] text-black hover:bg-[#00B4D8]/90'
                        : 'bg-white/10 text-white hover:bg-white/20 border border-white/20'
                      }`}
                  >
                    {payingPlanId === plan.id ? 'Обработка платежа...' : plan.cta}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      </main>
    </div>
  );
}
