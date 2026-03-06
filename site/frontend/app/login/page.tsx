'use client';

import { useState, useEffect, Suspense } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { toast } from 'sonner';
import { Header } from '@/components/layout/Header';
import { useAuthStore } from '@/store/authStore';

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const login = useAuthStore((s) => s.login);
  const loginWithOAuth = useAuthStore((s) => s.loginWithOAuth);
  const error = useAuthStore((s) => s.error);
  const setError = useAuthStore((s) => s.setError);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [oauthLoading, setOauthLoading] = useState(false);

  useEffect(() => {
    const err = searchParams.get('error');
    if (err) {
      setError(decodeURIComponent(err));
      if (typeof window !== 'undefined') console.error('[Login redirect error]', err);
    }
  }, [searchParams, setError]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    const { error: err } = await login(email, password);
    setSubmitting(false);
    if (err) {
      if (typeof window !== 'undefined') console.error('[Login] Supabase error:', err);
      toast.error(err.message || 'Ошибка входа');
      return;
    }
    toast.success('Добро пожаловать!');
    router.push('/');
  };

  const handleGoogleLogin = async () => {
    setError(null);
    setOauthLoading(true);
    const { error: err } = await loginWithOAuth('google');
    setOauthLoading(false);
    if (err) toast.error(err.message || 'Ошибка входа через Google');
  };

  return (
    <div className="min-h-screen">
      <Header />
      <div className="fixed inset-0 z-0">
        <Image
          src="/bg3.jpg"
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
            Вход
          </h1>
          {error && (
            <p className="mb-4 text-sm text-rose-400 text-center">{error}</p>
          )}
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
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
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
              />
            </label>
            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3.5 rounded-xl bg-gradient-to-r from-[#00B4D8] to-cyan-600 text-white font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {submitting ? 'Вход...' : 'Войти'}
            </button>
            <p className="text-center text-sm text-zinc-400">
              <Link href="/forgot-password" className="text-[#00B4D8] hover:underline">
                Забыли пароль?
              </Link>
            </p>
            <button
              type="button"
              onClick={handleGoogleLogin}
              disabled={oauthLoading}
              className="flex items-center justify-center gap-2 w-full py-3 rounded-xl border border-white/20 bg-white/5 text-white hover:bg-white/10 transition-colors disabled:opacity-50"
              aria-label="Войти через Google"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" aria-hidden>
                <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
              </svg>
              Войти через Google
            </button>
          </form>
          <p className="mt-6 text-center text-sm text-zinc-400">
            Нет аккаунта?{' '}
            <Link href="/signup" className="text-[#00B4D8] hover:underline">
              Зарегистрироваться
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center bg-[#000814]">
        <p className="text-zinc-400">Загрузка...</p>
      </div>
    }>
      <LoginForm />
    </Suspense>
  );
}
