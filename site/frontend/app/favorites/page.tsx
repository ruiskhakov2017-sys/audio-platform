'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { Header } from '@/components/layout/Header';
import { StoryTile } from '@/components/browse/StoryTile';
import { MOCK_STORIES } from '@/data/mockData';
import { useFavoritesStore } from '@/store/favoritesStore';
import type { Story } from '@/types/story';

export default function FavoritesPage() {
  const likedIds = useFavoritesStore((s) => s.likedIds);

  const allStories = useMemo(() => {
    const raw = Array.isArray(MOCK_STORIES) ? MOCK_STORIES : [];
    const duplicated = [...raw, ...raw, ...raw, ...raw];
    return duplicated.map((story, index) => ({ ...story, id: index + 1 })) as Story[];
  }, []);

  const favoriteStories = useMemo(
    () => allStories.filter((s) => likedIds.includes(String(s.id))),
    [allStories, likedIds]
  );

  const storiesForGrid = useMemo(
    () =>
      favoriteStories.map((story) => ({
        id: story.id,
        title: story.title,
        coverImage: story.coverImage,
        category: story.tags[0] || 'Аудио',
        price: story.isPremium ? 190 : undefined,
        isPremium: story.isPremium,
      })),
    [favoriteStories]
  );

  return (
    <div className="min-h-screen">
      <Header />

      <main className="pt-24 pb-12">
        <div className="max-w-[95%] mx-auto px-6">
          <div className="mb-8">
            <h1 className="font-heading text-3xl font-bold text-white mb-2">
              Ваша коллекция
            </h1>
            <p className="text-zinc-400">
              {favoriteStories.length === 0
                ? 'Любимые истории появятся здесь'
                : `Избранного: ${favoriteStories.length}`}
            </p>
          </div>

          {storiesForGrid.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-24 text-center max-w-md mx-auto">
              <p className="text-xl text-zinc-400 mb-6 leading-relaxed">
                Здесь пока пусто. Отметьте истории сердечком, чтобы не потерять их.
              </p>
              <Link
                href="/browse"
                className="inline-flex items-center gap-2 px-6 py-3 rounded-full bg-[#00B4D8] text-white font-medium hover:bg-[#00B4D8]/90 transition-colors"
              >
                Перейти в каталог
              </Link>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
              {storiesForGrid.map((story) => (
                <StoryTile key={story.id} {...story} />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
