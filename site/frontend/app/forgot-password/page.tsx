'use client';

import { useState } from 'react';
import Link from 'next/link';
import { toast } from 'sonner';
import { Header } from '@/components/layout/Header';
import { requestPasswordResetApi } from '@/lib/passwordResetApi';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setSubmitting(true);
    const result = await requestPasswordResetApi(email.trim());
    setSubmitting(false);
    if ('error' in result) {
      toast.error(result.error);
      return;
    }
    setSent(true);
    toast.success('Если email зарегистрирован, вы получите ссылку для сброса пароля.');
  };

  return (
    <div className="min-h-screen">
      <Header />
      <main className="relative z-10 pt-28 pb-20 px-4 flex items-center justify-center min-h-[60vh]">
        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-white/5 p-8 backdrop-blur-xl">
          <h1 className="font-heading text-2xl font-bold text-white mb-2 text-center">
            Восстановление пароля
          </h1>
          <p className="text-zinc-400 text-sm text-center mb-6">
            Введите email — мы отправим ссылку для сброса пароля.
          </p>
          {sent ? (
            <div className="text-center text-zinc-300 text-sm space-y-4">
              <p>Проверьте почту (и папку «Спам»). Ссылка действительна 24 часа.</p>
              <Link href="/login" className="inline-block text-[#00B4D8] hover:underline">
                Вернуться ко входу
              </Link>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email"
                required
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
              />
              <button
                type="submit"
                disabled={submitting || !process.env.NEXT_PUBLIC_API_URL}
                className="w-full py-3.5 rounded-xl bg-[#00B4D8] text-black font-semibold hover:bg-[#00B4D8]/90 disabled:opacity-50 transition-colors"
              >
                {submitting ? 'Отправка...' : 'Отправить ссылку'}
              </button>
              <p className="text-center text-sm text-zinc-500">
                <Link href="/login" className="text-[#00B4D8] hover:underline">
                  Назад ко входу
                </Link>
              </p>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
