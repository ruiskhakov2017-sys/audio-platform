'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Header } from '@/components/layout/Header';
import { useAuthStore } from '@/store/authStore';
import { useFavoritesStore } from '@/store/favoritesStore';
import { usePlayerStore } from '@/store/playerStore';

export default function ProfilePage() {
  const router = useRouter();
  const { user, profile, isAuthenticated, logout } = useAuthStore();
  const likedIds = useFavoritesStore((s) => s.likedIds);
  const isPremiumUser = usePlayerStore((s) => s.isPremiumUser);
  const setPaywallOpen = usePlayerStore((s) => s.setPaywallOpen);

  const [notifications, setNotifications] = useState(true);
  const [blurExplicit, setBlurExplicit] = useState(false);

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
      return;
    }
  }, [isAuthenticated, router]);

  const handleLogout = () => {
    logout();
    router.push('/');
  };

  if (!isAuthenticated || !user) {
    return null;
  }

  const displayName = profile?.full_name || user.email?.split('@')[0] || '?';
  const initial = displayName.charAt(0).toUpperCase();
  const favoritesCount = likedIds.length;
  const statusLabel = favoritesCount >= 5 ? 'Ценитель' : 'Новичок';

  return (
    <div className="min-h-screen">
      <Header />
      <main className="max-w-4xl mx-auto py-20 px-6">
        {/* Header: Avatar + Name/Email + Logout */}
        <div className="flex flex-wrap items-center justify-between gap-6 mb-12">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-[#00B4D8]/20 border border-[#00B4D8]/50 flex items-center justify-center text-2xl font-heading font-bold text-[#00B4D8]">
              {initial}
            </div>
            <div>
              <h1 className="font-heading text-2xl font-bold text-white">
                {displayName}
              </h1>
              <p className="text-zinc-400 text-sm">{user.email}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            className="px-5 py-2.5 rounded-full border border-white/20 text-zinc-400 hover:text-white hover:bg-white/5 transition-colors"
          >
            Выйти
          </button>
        </div>

        {/* Stats */}
        <section className="mb-12">
          <h2 className="font-heading text-lg font-bold text-white mb-4">
            Статистика
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
              <p className="text-zinc-500 text-sm mb-1">Прослушано</p>
              <p className="text-white text-xl font-semibold">12 часов</p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
              <p className="text-zinc-500 text-sm mb-1">Любимые истории</p>
              <p className="text-white text-xl font-semibold">{favoritesCount}</p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
              <p className="text-zinc-500 text-sm mb-1">Статус</p>
              <p className="text-white text-xl font-semibold">{statusLabel}</p>
            </div>
          </div>
        </section>

        {/* Subscription */}
        <section className="mb-12">
          <h2 className="font-heading text-lg font-bold text-white mb-4">
            Подписка
          </h2>
          <div className="rounded-2xl p-[2px] bg-gradient-to-r from-[#00B4D8]/60 via-amber-500/40 to-[#00B4D8]/60">
            <div className="rounded-2xl bg-[#000814] p-6 border border-white/5">
              {isPremiumUser ? (
                <>
                  <h3 className="font-heading text-xl font-bold text-white mb-2">
                    Текущий план: Premium 💎
                  </h3>
                  <p className="text-zinc-400 mb-6">
                    Доступ ко всему. Никаких границ.
                  </p>
                  <button
                    type="button"
                    className="px-5 py-2.5 rounded-full bg-white/10 border border-white/20 text-zinc-400 hover:text-white transition-colors"
                  >
                    Управление подпиской
                  </button>
                </>
              ) : (
                <>
                  <h3 className="font-heading text-xl font-bold text-white mb-2">
                    Текущий план: Бесплатный
                  </h3>
                  <p className="text-zinc-400 mb-6">
                    Вам доступно только 30% контента. Самое интересное скрыто.
                  </p>
                  <button
                    type="button"
                    onClick={() => setPaywallOpen(true)}
                    className="px-6 py-3 rounded-full bg-gradient-to-r from-[#00B4D8] to-cyan-600 text-white font-semibold hover:opacity-90 transition-opacity shadow-[0_0_20px_rgba(0,180,216,0.4)] animate-pulse"
                  >
                    Оформить Premium за 299₽
                  </button>
                </>
              )}
            </div>
          </div>
        </section>

        {/* Settings */}
        <section>
          <h2 className="font-heading text-lg font-bold text-white mb-4">
            Настройки
          </h2>
          <div className="rounded-2xl border border-white/10 bg-white/5 divide-y divide-white/10 overflow-hidden">
            <label className="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-white/5 transition-colors">
              <span className="text-white">Уведомления о новинках</span>
              <input
                type="checkbox"
                checked={notifications}
                onChange={(e) => setNotifications(e.target.checked)}
                className="w-5 h-5 rounded border-white/20 bg-white/5 text-[#00B4D8] focus:ring-[#00B4D8]"
              />
            </label>
            <div className="flex items-center justify-between px-5 py-4 text-zinc-500">
              <span className="text-zinc-400">Темная тема</span>
              <span className="text-sm">всегда вкл</span>
            </div>
            <label className="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-white/5 transition-colors">
              <span className="text-white">Скрыть откровенный контент</span>
              <input
                type="checkbox"
                checked={blurExplicit}
                onChange={(e) => setBlurExplicit(e.target.checked)}
                className="w-5 h-5 rounded border-white/20 bg-white/5 text-[#00B4D8] focus:ring-[#00B4D8]"
              />
            </label>
          </div>
        </section>
      </main>
    </div>
  );
}
