'use client';

import { useState, useEffect, Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { toast } from 'sonner';
import { Header } from '@/components/layout/Header';
import { confirmPasswordResetApi } from '@/lib/passwordResetApi';

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const uid = searchParams.get('uid') ?? '';
  const token = searchParams.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!uid || !token) {
      toast.error('Неверная или устаревшая ссылка');
    }
  }, [uid, token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 8) {
      toast.error('Пароль не менее 8 символов');
      return;
    }
    if (password !== confirm) {
      toast.error('Пароли не совпадают');
      return;
    }
    if (!uid || !token) return;
    setSubmitting(true);
    const result = await confirmPasswordResetApi(uid, token, password);
    setSubmitting(false);
    if ('error' in result) {
      toast.error(result.error);
      return;
    }
    setDone(true);
    toast.success('Пароль изменён. Войдите с новым паролем.');
    setTimeout(() => router.push('/login'), 1500);
  };

  if (!uid || !token) {
    return (
      <div className="text-center text-zinc-400">
        <p className="mb-4">Ссылка недействительна или устарела.</p>
        <Link href="/forgot-password" className="text-[#00B4D8] hover:underline">
          Запросить новую
        </Link>
      </div>
    );
  }

  if (done) {
    return (
      <p className="text-center text-zinc-300">
        Пароль успешно изменён. Перенаправление на страницу входа...
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Новый пароль (мин. 8 символов)"
        required
        minLength={8}
        className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
      />
      <input
        type="password"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
        placeholder="Повторите пароль"
        required
        minLength={8}
        className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
      />
      <button
        type="submit"
        disabled={submitting || !process.env.NEXT_PUBLIC_API_URL}
        className="w-full py-3.5 rounded-xl bg-[#00B4D8] text-black font-semibold hover:bg-[#00B4D8]/90 disabled:opacity-50 transition-colors"
      >
        {submitting ? 'Сохранение...' : 'Сохранить пароль'}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="min-h-screen">
      <Header />
      <main className="relative z-10 pt-28 pb-20 px-4 flex items-center justify-center min-h-[60vh]">
        <div className="w-full max-w-md rounded-2xl border border-white/10 bg-white/5 p-8 backdrop-blur-xl">
          <h1 className="font-heading text-2xl font-bold text-white mb-2 text-center">
            Новый пароль
          </h1>
          <p className="text-zinc-400 text-sm text-center mb-6">
            Введите новый пароль для вашего аккаунта.
          </p>
          <Suspense fallback={<p className="text-zinc-500 text-center">Загрузка...</p>}>
            <ResetPasswordForm />
          </Suspense>
          <p className="mt-6 text-center text-sm text-zinc-500">
            <Link href="/login" className="text-[#00B4D8] hover:underline">
              Вернуться ко входу
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
