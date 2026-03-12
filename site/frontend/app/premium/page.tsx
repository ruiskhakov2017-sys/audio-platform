'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import { Header } from '@/components/layout/Header';
import { useAuthStore } from '@/store/authStore';
import { PREMIUM_PLAN_LIST, formatRub, type PremiumPlanId } from '@/config/pricing';
import {
  Sparkles,
  Headphones,
  Shield,
  Infinity,
  Check,
  Crown,
  Zap,
  Diamond,
} from 'lucide-react';

const GOLD = '#D4AF37';
const GOLD_LIGHT = '#F4E4BC';
const PURPLE = '#6B21A8';
const PURPLE_LIGHT = '#9333EA';
const BLUE_LIGHT = '#60A5FA';

const benefits = [
  {
    icon: Sparkles,
    title: 'Эксклюзив',
    text: 'Истории, которых нет в общем доступе.',
  },
  {
    icon: Headphones,
    title: 'HD Звук',
    text: 'Кристально чистая озвучка с полным погружением.',
  },
  {
    icon: Shield,
    title: 'Анонимность',
    text: 'Никаких следов в истории транзакций, полная приватность.',
  },
  {
    icon: Infinity,
    title: 'Безлимитный доступ',
    text: 'Слушай любые треки из архива без ограничений по времени.',
  },
];

const plans = [
  {
    id: 'tainyi',
    popular: false,
    features: ['Доступ к эксклюзивным историям', 'Базовое качество звука'],
  },
  {
    id: 'gryaznyi',
    popular: true,
    features: ['Всё из тарифа «Тайный»', 'HD озвучка', 'Экономия 17%'],
  },
  {
    id: 'oderzhimyi',
    popular: false,
    features: ['Всё из тарифа «Грязный»', 'Скидка 50% на озвучку сценариев', 'Максимальный приоритет поддержки'],
  },
].map((plan) => {
  const config = PREMIUM_PLAN_LIST.find((p) => p.id === plan.id as PremiumPlanId)!;
  return {
    ...plan,
    name: config.name,
    period: config.period,
    desc: config.description,
    priceRub: config.priceRub,
  };
});

const PLAN_THEME: Record<
  string,
  {
    cardClass: string;
    iconBg: string;
    iconColor: string;
    checkColor: string;
    buttonClass: string;
  }
> = {
  tainyi: {
    cardClass: 'border border-purple-500/40 bg-gradient-to-b from-purple-950/65 to-indigo-950/40 shadow-[0_0_30px_rgba(168,85,247,0.2)] hover:shadow-[0_0_40px_rgba(168,85,247,0.4)]',
    iconBg: 'rgba(147,51,234,0.25)',
    iconColor: PURPLE_LIGHT,
    checkColor: PURPLE_LIGHT,
    buttonClass: 'bg-purple-600 hover:bg-purple-500 text-white shadow-[0_0_20px_rgba(168,85,247,0.2)]',
  },
  gryaznyi: {
    cardClass: 'border border-amber-500/50 bg-gradient-to-b from-amber-900/40 to-orange-950/30 shadow-[0_0_40px_rgba(245,158,11,0.3)] hover:shadow-[0_0_50px_rgba(245,158,11,0.5)]',
    iconBg: 'rgba(245,158,11,0.25)',
    iconColor: '#F59E0B',
    checkColor: '#F59E0B',
    buttonClass: 'bg-amber-500 hover:bg-amber-400 text-zinc-950 shadow-[0_0_20px_rgba(245,158,11,0.3)]',
  },
  oderzhimyi: {
    cardClass: 'border border-blue-500/40 bg-gradient-to-b from-blue-900/70 to-blue-950/40 shadow-[0_0_30px_rgba(59,130,246,0.3)] hover:shadow-[0_0_50px_rgba(59,130,246,0.6)]',
    iconBg: 'rgba(59,130,246,0.25)',
    iconColor: BLUE_LIGHT,
    checkColor: '#3B82F6',
    buttonClass: 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_20px_rgba(59,130,246,0.25)]',
  },
};

export default function PremiumPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [payingPlanId, setPayingPlanId] = useState<string | null>(null);

  const handleSubscribe = async (planId: string) => {
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
    <div className="min-h-screen bg-[#000000] text-white relative overflow-hidden">
      <Header />

      {/* Background Image (Hero style) */}
      <div className="fixed inset-0 z-0 pointer-events-none">
        <div
          className="absolute inset-0 bg-cover bg-center bg-no-repeat opacity-[0.15] blur-[3px]"
          style={{ backgroundImage: 'url(/hero-bg.png)' }}
          aria-hidden="true"
        />
        <div className="absolute inset-0 bg-black/40" aria-hidden="true" />
      </div>

      <div className="relative z-10">
        {/* Hero */}
        <section className="relative pt-32 pb-24 px-6 overflow-hidden">
          <div
            className="absolute inset-0 z-0 opacity-30"
            style={{
              background: `radial-gradient(ellipse 80% 50% at 50% 0%, ${PURPLE}40, transparent 70%)`,
            }}
          />
          <div
            className="absolute top-20 left-1/2 -translate-x-1/2 w-[600px] h-[400px] rounded-full blur-[120px] z-0 opacity-20"
            style={{ backgroundColor: GOLD }}
          />
          <div className="relative z-10 max-w-4xl mx-auto text-center">
            <motion.h1
              className="font-heading text-4xl md:text-6xl lg:text-7xl font-bold mb-6 leading-tight"
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7 }}
              style={{ color: GOLD_LIGHT }}
            >
              Разблокируй свои самые смелые фантазии
            </motion.h1>
            <motion.p
              className="text-xl md:text-2xl text-zinc-400 max-w-2xl mx-auto"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.2 }}
            >
              Получи неограниченный доступ к эксклюзивной библиотеке Dirty Secrets в HD качестве.
            </motion.p>
          </div>
        </section>

        {/* Benefits */}
        <section className="relative py-20 px-6">
          <div className="max-w-6xl mx-auto">
            <motion.h2
              className="font-heading text-3xl md:text-4xl font-bold text-center mb-16"
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true }}
              style={{ color: GOLD_LIGHT }}
            >
              Почему Premium
            </motion.h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
              {benefits.map((item, i) => (
                <motion.div
                  key={item.title}
                  className="rounded-2xl border border-white/10 bg-white/5 p-8 backdrop-blur-sm hover:border-[#D4AF37]/40 transition-colors"
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.5, delay: i * 0.1 }}
                >
                  <div
                    className="w-14 h-14 rounded-2xl flex items-center justify-center mb-6"
                    style={{ backgroundColor: `${GOLD}20`, border: `1px solid ${GOLD}40` }}
                  >
                    <item.icon className="w-7 h-7" style={{ color: GOLD }} strokeWidth={1.5} />
                  </div>
                  <h3 className="text-xl font-bold text-white mb-2">{item.title}</h3>
                  <p className="text-zinc-400 text-sm leading-relaxed">{item.text}</p>
                </motion.div>
              ))}
            </div>
          </div>
        </section>

        {/* Pricing cards */}
        <section className="relative py-24 px-6">
          <div
            className="absolute inset-0 z-0 opacity-20"
            style={{
              background: `radial-gradient(ellipse 60% 40% at 50% 100%, ${PURPLE}60, transparent 60%)`,
            }}
          />
          <div className="relative z-10 max-w-6xl mx-auto">
            <motion.h2
              className="font-heading text-3xl md:text-4xl font-bold text-center mb-4"
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true }}
              style={{ color: GOLD_LIGHT }}
            >
              Тарифы
            </motion.h2>
            <p className="text-zinc-500 text-center mb-16">Выбери подходящий период и начни слушать без ограничений</p>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8 items-stretch">
              {plans.map((plan, i) => {
                const theme = PLAN_THEME[plan.id];
                return (
                  <motion.div
                    key={plan.id}
                    className={`relative rounded-3xl p-8 flex flex-col backdrop-blur-md transition-all hover:scale-[1.02] ${theme.cardClass}`}
                    initial={{ opacity: 0, y: 40 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.5, delay: i * 0.15 }}
                  >
                    {plan.popular && (
                      <div className="absolute -top-4 left-1/2 -translate-x-1/2 px-4 py-1.5 rounded-full text-sm font-bold bg-amber-500 text-zinc-950 shadow-[0_0_18px_rgba(245,158,11,0.45)]">
                        Хит
                      </div>
                    )}
                    <div className="flex items-center gap-3 mb-6">
                      <div
                        className="w-12 h-12 rounded-xl flex items-center justify-center"
                        style={{ backgroundColor: theme.iconBg }}
                      >
                        {plan.popular ? (
                          <Crown className="w-6 h-6" style={{ color: theme.iconColor }} strokeWidth={1.5} />
                        ) : plan.id === 'oderzhimyi' ? (
                          <Diamond className="w-6 h-6" style={{ color: theme.iconColor }} strokeWidth={1.5} />
                        ) : (
                          <Zap className="w-6 h-6" style={{ color: theme.iconColor }} strokeWidth={1.5} />
                        )}
                      </div>
                      <div>
                        <h3 className="text-2xl font-bold text-white">{plan.name}</h3>
                        <p className="text-zinc-500 text-sm">{plan.period}</p>
                      </div>
                    </div>
                    <p className="text-zinc-400 text-sm mb-6">{plan.desc}</p>
                    <div className="mb-8">
                      <span className="text-4xl font-black text-white">{formatRub(plan.priceRub)}</span>
                    </div>
                    <ul className="space-y-3 mb-8 flex-1">
                      {plan.features.map((f) => (
                        <li key={f} className="flex items-start gap-3 text-zinc-300 text-sm">
                          <Check className="w-5 h-5 shrink-0 mt-0.5" style={{ color: theme.checkColor }} strokeWidth={2.5} />
                          {f}
                        </li>
                      ))}
                    </ul>
                    <button
                      type="button"
                      onClick={() => handleSubscribe(plan.id)}
                      disabled={payingPlanId === plan.id}
                      className={`block w-full py-4 rounded-2xl font-semibold text-center transition-all disabled:opacity-70 ${theme.buttonClass}`}
                    >
                      {payingPlanId === plan.id ? 'Переход к оплате...' : 'Оформить подписку'}
                    </button>
                  </motion.div>
                );
              })}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
