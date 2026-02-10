'use client';

import { useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Header } from '@/components/layout/Header';
import { useAuthStore } from '@/store/authStore';

export default function SignupPage() {
  const router = useRouter();
  const register = useAuthStore((s) => s.register);
  const error = useAuthStore((s) => s.error);
  const setError = useAuthStore((s) => s.setError);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setSubmitting(true);
    const { error: err, needsEmailConfirm } = await register(email, password, name);
    setSubmitting(false);
    if (err) return;
    if (needsEmailConfirm) {
      setMessage('Проверьте почту: мы отправили ссылку для подтверждения.');
      return;
    }
    router.push('/');
  };

  return (
    <div className="min-h-screen">
      <Header />
      <div className="fixed inset-0 z-0">
        <Image
          src="/bg1.jpg"
          alt=""
          fill
          className="object-cover"
          priority
          unoptimized
        />
        <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      </div>

      <main className="relative z-10 flex min-h-screen items-center justify-center px-4 pt-24 pb-12">
        <div className="w-full max-w-md rounded-3xl border border-white/10 bg-white/5 p-8 shadow-2xl backdrop-blur-xl">
          <h1 className="font-heading text-2xl font-bold text-white mb-6 text-center">
            Регистрация
          </h1>
          {error && (
            <p className="mb-4 text-sm text-rose-400 text-center">{error}</p>
          )}
          {message && (
            <p className="mb-4 text-sm text-emerald-400 text-center">{message}</p>
          )}
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <label className="block">
              <span className="sr-only">Имя</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Имя"
                required
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
              />
            </label>
            <label className="block">
              <span className="sr-only">Email</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email"
                required
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
              />
            </label>
            <label className="block">
              <span className="sr-only">Пароль</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Пароль"
                required
                minLength={6}
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
              />
            </label>
            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3.5 rounded-xl bg-gradient-to-r from-[#00B4D8] to-cyan-600 text-white font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {submitting ? 'Регистрация...' : 'Зарегистрироваться'}
            </button>
          </form>
          <p className="mt-6 text-center text-sm text-zinc-400">
            Уже есть аккаунт?{' '}
            <Link href="/login" className="text-[#00B4D8] hover:underline">
              Войти
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
