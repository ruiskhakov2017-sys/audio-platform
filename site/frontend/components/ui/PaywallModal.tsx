'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { toast } from 'sonner';
import { usePlayerStore } from '@/store/playerStore';
import { X } from 'lucide-react';

export function PaywallModal() {
  const { isPaywallOpen, setPaywallOpen } = usePlayerStore();

  useEffect(() => {
    if (isPaywallOpen) {
      toast.info('Нужен Premium', { id: 'paywall', description: 'Доступно только по подписке' });
    }
  }, [isPaywallOpen]);

  if (!isPaywallOpen) return null;

  const handleClose = () => setPaywallOpen(false);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="paywall-title"
    >
      <button
        type="button"
        onClick={handleClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-md"
        aria-label="Закрыть"
      />
      <div className="relative w-full max-w-md rounded-2xl border border-amber-500/20 bg-zinc-900/80 backdrop-blur-xl p-8 shadow-2xl shadow-amber-950/20">
        <button
          type="button"
          onClick={handleClose}
          className="absolute right-4 top-4 p-1.5 rounded-full text-zinc-500 hover:text-white hover:bg-white/10 transition-colors"
          aria-label="Закрыть"
        >
          <X className="w-5 h-5" strokeWidth={2} />
        </button>
        <h2 id="paywall-title" className="font-heading text-2xl font-bold text-white mb-3 text-center pr-10">
          Premium Access Only
        </h2>
        <p className="text-zinc-400 text-center mb-8 text-sm leading-relaxed">
          Эта история слишком откровенна для публичного доступа. Оформите подписку, чтобы услышать всё.
        </p>
        <Link
          href="/pricing"
          onClick={handleClose}
          className="block w-full py-3.5 px-4 rounded-full bg-amber-500 text-black font-semibold hover:bg-amber-400 transition-colors shadow-lg shadow-amber-500/20 text-center"
        >
          Перейти к тарифам
        </Link>
      </div>
    </div>
  );
}
