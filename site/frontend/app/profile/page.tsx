'use client';

import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { Header } from '@/components/layout/Header';
import { useAuthStore } from '@/store/authStore';
import { useFavoritesStore } from '@/store/favoritesStore';
import { Crown, Camera, Heart } from 'lucide-react';
import { usePlayerStore } from '@/store/playerStore';
import { uploadAvatarWithDjango } from '@/lib/authApi';
import { fetchStoriesFromApi, useDjangoApi } from '@/lib/api';
import { StoryTile } from '@/components/browse/StoryTile';
import type { Story } from '@/types/story';

export default function ProfilePage() {
  const router = useRouter();
  const avatarInputRef = useRef<HTMLInputElement>(null);
  const { user, profile, isAuthenticated, logout, setProfileAvatar } = useAuthStore();
  const likedIds = useFavoritesStore((s) => s.likedIds);
  const isPremiumUser = usePlayerStore((s) => s.isPremiumUser);

  const [notifications, setNotifications] = useState(true);
  const [blurExplicit, setBlurExplicit] = useState(false);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const [allStories, setAllStories] = useState<Story[]>([]);

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
      return;
    }
  }, [isAuthenticated, router]);

  useEffect(() => {
    if (!isAuthenticated || !useDjangoApi()) return;
    fetchStoriesFromApi().then(setAllStories);
  }, [isAuthenticated]);

  const favoriteStories = allStories.filter((s) => likedIds.includes(String(s.id)));
  const favoritePreview = favoriteStories.slice(0, 6);

  const handleLogout = () => {
    logout();
    router.push('/');
  };

  const handleAvatarClick = () => {
    if (!process.env.NEXT_PUBLIC_API_URL || typeof window === 'undefined') return;
    const token = localStorage.getItem('auth_access_token');
    if (!token) return;
    avatarInputRef.current?.click();
  };

  const handleAvatarChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !file.type.startsWith('image/')) return;
    e.target.value = '';
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_access_token') : null;
    if (!token) return;
    setUploadingAvatar(true);
    const result = await uploadAvatarWithDjango(token, file);
    setUploadingAvatar(false);
    if ('error' in result) {
      toast.error(result.error);
      return;
    }
    setProfileAvatar(result.avatar_url ?? null);
    toast.success('Аватар обновлён');
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
            <input
              ref={avatarInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={handleAvatarChange}
              aria-label="Загрузить аватар"
            />
            <button
              type="button"
              onClick={handleAvatarClick}
              disabled={uploadingAvatar || !process.env.NEXT_PUBLIC_API_URL}
              className="relative w-16 h-16 rounded-full bg-[#00B4D8]/20 border border-[#00B4D8]/50 flex items-center justify-center text-2xl font-heading font-bold text-[#00B4D8] overflow-hidden hover:ring-2 hover:ring-[#00B4D8]/50 transition-all disabled:opacity-50"
            >
              {profile?.avatar_url ? (
                <span className="absolute inset-0 block">
                  <Image src={profile.avatar_url} alt="" fill className="object-cover" unoptimized sizes="64px" />
                </span>
              ) : (
                <span>{initial}</span>
              )}
              {process.env.NEXT_PUBLIC_API_URL && (
                <span className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 hover:opacity-100 transition-opacity">
                  <Camera className="w-6 h-6 text-white" />
                </span>
              )}
            </button>
            <div>
              <h1 className="font-heading text-2xl font-bold text-white">
                {displayName}
              </h1>
              <p className="text-zinc-400 text-sm">{user.email}</p>
              {isPremiumUser && (
                <span className="inline-flex items-center gap-1.5 mt-2 px-3 py-1 rounded-full bg-amber-500/20 border border-amber-500/50 text-amber-400 text-sm font-medium">
                  <Crown className="w-4 h-4" strokeWidth={2} />
                  Premium
                </span>
              )}
              {!isPremiumUser && (
                <span className="inline-flex items-center gap-1.5 mt-2 px-3 py-1 rounded-full bg-white/10 border border-white/20 text-zinc-400 text-sm">
                  Free Plan
                </span>
              )}
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

        {/* Мое Избранное */}
        <section className="mb-12">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-heading text-lg font-bold text-white flex items-center gap-2">
              <Heart className="w-5 h-5 text-rose-500" strokeWidth={1.5} />
              Мое Избранное
            </h2>
            {favoriteStories.length > 0 && (
              <Link
                href="/favorites"
                className="text-sm text-[#00B4D8] hover:underline"
              >
                Вся коллекция ({favoriteStories.length})
              </Link>
            )}
          </div>
          {favoritePreview.length === 0 ? (
            <p className="text-zinc-500 text-sm py-4">
              Добавьте истории в избранное сердечком — они появятся здесь и на странице «Ваша коллекция».
            </p>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-4">
              {favoritePreview.map((story) => (
                <StoryTile
                  key={story.id}
                  id={story.id}
                  title={story.title}
                  coverImage={story.coverImage}
                  category={story.tags?.[0] || 'Аудио'}
                  isPremium={story.isPremium}
                  story={story}
                />
              ))}
            </div>
          )}
        </section>

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
                  <h3 className="font-heading text-xl font-bold text-white mb-2 flex items-center gap-2">
                    <Crown className="w-6 h-6 text-amber-400" strokeWidth={2} />
                    Текущий план: Premium
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
                  <Link
                    href="/pricing"
                    className="inline-block px-6 py-3 rounded-full bg-gradient-to-r from-[#00B4D8] to-cyan-600 text-white font-semibold hover:opacity-90 transition-opacity shadow-[0_0_20px_rgba(0,180,216,0.4)] animate-pulse"
                  >
                    Оформить Premium за 299₽
                  </Link>
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
