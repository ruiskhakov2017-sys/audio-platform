'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useHistoryStore } from '@/store/historyStore';

export function RecentlyPlayedSection() {
  const history = useHistoryStore((s) => s.history);
  const clearHistory = useHistoryStore((s) => s.clearHistory);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted || history.length === 0) return null;

  return (
    <section className="px-4 sm:px-6 py-12 md:py-16">
      <div className="max-w-[1800px] mx-auto">
        <div className="flex items-center justify-between gap-4 mb-6">
          <h2 className="font-heading text-2xl md:text-3xl font-bold text-white">
            Продолжить прослушивание
          </h2>
          <button
            type="button"
            onClick={clearHistory}
            className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors shrink-0"
          >
            Очистить историю
          </button>
        </div>
        <div className="flex gap-4 overflow-x-auto pb-4 scrollbar-hide -mx-4 px-4 sm:mx-0 sm:px-0">
          {history.map((story) => (
            <Link
              key={`${story.id}-${story.slug}`}
              href={`/story/${story.id}`}
              className="group shrink-0 w-[140px] sm:w-[160px]"
            >
              <div className="relative aspect-[3/4] rounded-2xl overflow-hidden border border-white/10 group-hover:border-[#00B4D8]/40 transition-colors">
                <Image
                  src={story.coverImage}
                  alt={story.title}
                  fill
                  className="object-cover group-hover:scale-105 transition-transform duration-300"
                  unoptimized
                  sizes="160px"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent pointer-events-none" />
                <p className="absolute bottom-2 left-2 right-2 text-white text-sm font-medium line-clamp-2">
                  {story.title}
                </p>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}
