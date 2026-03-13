'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { Header } from '@/components/layout/Header';
import { getTop100Stories } from '@/app/actions/catalog';
import { fetchStoriesFromApi, useDjangoApi } from '@/lib/api';
import { getDisplayTags } from '@/lib/stories';
import type { Story } from '@/types/story';

const pageStyles = {
  grain: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
  gradient: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(0,180,216,0.08) 0%, transparent 50%)',
};

export default function TopPage() {
  const [list, setList] = useState<Story[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    if (useDjangoApi()) {
      fetchStoriesFromApi()
        .then((data) => setList((Array.isArray(data) ? data : []).slice(0, 100)))
        .finally(() => setLoading(false));
    } else {
      getTop100Stories()
        .then((data) => setList(data ?? []))
        .finally(() => setLoading(false));
    }
  }, []);

  const getBadgeTestStyle = (index: number) => {
    const baseClasses = "absolute top-4 left-4 flex items-center justify-center font-bold text-lg w-10 h-10 z-10 transition-all duration-300";

    switch (index) {
      case 0: // Вариант 1: Классический синий (Classic Solid)
        return `${baseClasses} bg-blue-600 text-white rounded-lg shadow-md`;
      case 1: // Вариант 2: Стеклянный синий (Glassmorphism)
        return `${baseClasses} bg-blue-500/30 backdrop-blur-md border border-blue-400 text-white rounded-lg`;
      case 2: // Вариант 3: Темный неон (Dark Neon)
        return `${baseClasses} bg-black/80 border border-blue-500 text-blue-400 rounded-lg shadow-[0_0_15px_rgba(59,130,246,0.5)]`;
      case 3: // Вариант 4: Богатый градиент (Rich Gradient)
        return `${baseClasses} bg-gradient-to-br from-blue-900 to-blue-500 text-white border border-blue-400/50 rounded-lg`;
      case 4: // Вариант 5: 3D-эффект с внутренним свечением (3D Inner Glow)
        return `${baseClasses} bg-blue-600 text-white rounded-lg shadow-[inset_0_2px_4px_rgba(255,255,255,0.4),0_4px_6px_rgba(0,0,0,0.3)]`;
      case 5: // Вариант 6: Фирменный Циан (Brand Cyan)
        return `${baseClasses} bg-[#00B4D8] text-black font-black rounded-lg shadow-[0_0_20px_rgba(0,180,216,0.4)]`;
      case 6: // Вариант 7: Растворяющийся градиент (Fade to transparent)
        return `${baseClasses} bg-gradient-to-r from-blue-600 to-transparent border-l-2 border-blue-400 text-white w-14 justify-start pl-3 rounded-l-none`;
      case 7: // Вариант 8: Матовый темный синий (Frosted Dark Blue)
        return `${baseClasses} bg-slate-900/90 border-b-2 border-r-2 border-blue-500 text-blue-400 rounded-tl-lg rounded-br-lg`;
      case 8: // Вариант 9: Асимметричный срез (Asymmetric)
        return `${baseClasses} bg-blue-600 text-white rounded-br-2xl rounded-tl-lg shadow-lg`;
      default: // Для всех остальных карточек (>9) оставляем базовый синий
        return `${baseClasses} bg-blue-600/50 backdrop-blur-sm text-white rounded-lg`;
    }
  };

  return (
    <div className="min-h-screen bg-[#000814] text-white">
      <Header />
      <main className="relative z-10 pt-28 pb-24 px-4 md:px-6">
        <div className="max-w-5xl mx-auto">
          <h1 className="font-heading text-3xl md:text-4xl lg:text-5xl font-bold text-white text-center mb-4">
            Топ-100 запретных хитов
          </h1>
          <p className="text-zinc-400 text-center mb-10 text-sm md:text-base">
            Самые популярные истории по прослушиваниям — по 3 лучших из каждого жанра.
          </p>

          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="rounded-xl bg-white/5 border border-white/10 aspect-[3/4] animate-pulse" />
              ))}
            </div>
          ) : list.length === 0 ? (
            <p className="text-zinc-400 text-center py-12">Пока нет данных для топа.</p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
              {list.map((story, index) => (
                <Link
                  key={`${story.id}-${story.slug}-${index}`}
                  href={`/story/${story.id}`}
                  className="group relative block rounded-2xl overflow-hidden transition-all duration-300 hover:-translate-y-2 hover:shadow-[0_15px_30px_rgba(0,180,216,0.2)] bg-zinc-900"
                >
                  <div className="aspect-[3/4] relative">
                    {story.coverImage ? (
                      <Image
                        src={story.coverImage}
                        alt=""
                        fill
                        className="object-cover transition-transform duration-500 group-hover:scale-105"
                        sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
                        unoptimized={story.coverImage.startsWith('http')}
                      />
                    ) : (
                      <div className="absolute inset-0 bg-zinc-800 flex items-center justify-center text-zinc-500 text-sm">Нет обложки</div>
                    )}

                    {/* Gradient Overlay for Readability */}
                    <div className="absolute inset-0 bg-gradient-to-t from-black/95 via-black/40 to-transparent opacity-80 group-hover:opacity-100 transition-opacity duration-300" />

                    {/* Ranking Badge - Glassmorphism */}
                    <span className={getBadgeTestStyle(index)}>
                      {index + 1}
                    </span>

                    {/* Content Group: Genre + Title */}
                    <div className="absolute bottom-0 left-0 right-0 p-5 flex flex-col gap-2 transform transition-transform duration-300 group-hover:-translate-y-1 z-10">
                      <span className="inline-block w-max bg-blue-600/30 border border-blue-400 text-blue-50 px-2.5 py-1 rounded-md text-[10px] md:text-xs uppercase tracking-wider font-semibold backdrop-blur-md">
                        {getDisplayTags(story)[0] || 'Аудио'}
                      </span>
                      <h2 className="text-xl md:text-2xl font-bold text-white leading-tight line-clamp-2">
                        {story.title}
                      </h2>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
