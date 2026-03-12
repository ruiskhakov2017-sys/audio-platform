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
                    <span className="absolute top-4 left-4 flex items-center justify-center font-bold text-lg w-10 h-10 bg-gradient-to-r from-blue-600/90 to-transparent border-l-2 border-blue-400 text-white shadow-[0_0_10px_rgba(59,130,246,0.3)]">
                      {index + 1}
                    </span>

                    {/* Content Group: Genre + Title */}
                    <div className="absolute bottom-0 left-0 right-0 p-5 flex flex-col gap-2 transform transition-transform duration-300 group-hover:-translate-y-1 z-10">
                      <span className="inline-block w-max px-3 py-1.5 rounded-full bg-black/70 backdrop-blur-md border border-[#00B4D8]/60 text-[#00B4D8] font-bold text-xs uppercase tracking-widest shadow-[0_0_15px_rgba(0,180,216,0.25)] [text-shadow:0_0_8px_rgba(0,180,216,0.6)]">
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
