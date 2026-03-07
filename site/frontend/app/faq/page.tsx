'use client';

import { Header } from '@/components/layout/Header';

export default function FaqPage() {
  return (
    <div className="min-h-screen bg-[#000814] text-white">
      <div
        className="fixed inset-0 z-0 pointer-events-none opacity-[0.03]"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
        }}
      />
      <div
        className="fixed inset-0 z-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(0,180,216,0.08) 0%, transparent 50%)',
        }}
      />
      <Header />
      <main className="relative z-10 pt-28 pb-24 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="font-heading text-4xl md:text-5xl font-bold text-white mb-4">
            Вопросы и ответы
          </h1>
          <p className="text-zinc-400 text-lg">
            Частые вопросы о сервисе и подписке. Раздел в разработке.
          </p>
        </div>
      </main>
    </div>
  );
}
