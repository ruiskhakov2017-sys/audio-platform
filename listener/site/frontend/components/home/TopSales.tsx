'use client';

import Link from 'next/link';
import Image from 'next/image';
import { Play, Crown } from 'lucide-react';
import { usePlayerStore } from '@/store/playerStore';
import { MOCK_STORIES } from '@/data/mocks';
import type { Story } from '@/types/story';

function StoryTile({ story }: { story: Story }) {
  const { setTrack, setIsPlaying, currentTrack, isPlaying } = usePlayerStore();
  const isCurrent = currentTrack?.id === story.id;
  const isPlayingCurrent = isCurrent && isPlaying;

  const handlePlay = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (isCurrent) {
      setIsPlaying(!isPlaying);
    } else {
      setTrack(story);
      setIsPlaying(true);
    }
  };

  return (
    <Link
      href={`/story/${story.id}`}
      className="group relative block overflow-hidden rounded-[2rem] border border-[#00B4D8]/40 no-underline shadow-lg transition-all duration-300 hover:border-[#00B4D8]/60 hover:shadow-[0_0_30px_rgba(0,180,216,0.2)]"
    >
      <div className="relative aspect-square">
        <Image
          src={story.coverImage}
          alt={story.title}
          fill
          sizes="(max-width: 768px) 50vw, 25vw"
          className="object-cover transition-transform duration-300 group-hover:scale-105"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-transparent" />
        {story.isPremium && (
          <div className="absolute left-2 top-2 flex items-center gap-1 rounded-lg bg-amber-400/90 px-2 py-0.5 text-[10px] font-bold text-zinc-900">
            <Crown className="h-3 w-3" strokeWidth={1.5} />
            Premium
          </div>
        )}
        <div className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity duration-300 group-hover:opacity-100">
          <button
            type="button"
            onClick={handlePlay}
            className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border-2 border-[#00B4D8] bg-[#00B4D8]/25 text-[#7dd3fc] shadow-[0_0_28px_rgba(0,180,216,0.4)] transition-transform hover:scale-105"
            aria-label={isPlayingCurrent ? 'Пауза' : 'Слушать'}
          >
            {isPlayingCurrent ? (
              <span className="h-5 w-5 rounded-sm bg-[#00B4D8]" />
            ) : (
              <Play className="ml-0.5 h-7 w-7 shrink-0 fill-[#00B4D8]" strokeWidth={1.5} />
            )}
          </button>
        </div>
      </div>
      <div className="absolute bottom-0 left-0 right-0 p-4">
        <p className="text-[10px] font-medium uppercase tracking-wider text-[#00B4D8]">
          Аудио
        </p>
        <h3 className="mt-0.5 line-clamp-2 text-sm font-bold text-white">
          {story.title}
        </h3>
        <p className="mt-1 text-xs text-zinc-400">
          {story.isPremium ? 'Доступ по подписке' : 'Бесплатно'}
        </p>
        <span className="mt-2 inline-block w-full rounded-xl border border-white/10 bg-white/5 py-2 text-center text-xs font-medium text-white backdrop-blur-sm">
          Подробнее
        </span>
      </div>
    </Link>
  );
}

export default function TopSales() {
  const featured = MOCK_STORIES.slice(0, 4);

  return (
    <section className="py-20">
      <div className="mx-auto w-full max-w-6xl px-6 sm:px-8 lg:px-12">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-2xl font-black tracking-tight text-white md:text-3xl">
              Популярные записи
            </h2>
            <p className="mt-1 text-sm text-zinc-400">
              То, что слушают чаще всего
            </p>
          </div>
          <Link
            href="/browse"
            className="text-sm font-medium text-[#00B4D8] no-underline hover:underline"
          >
            Смотреть всё
          </Link>
        </div>
        <div className="grid grid-cols-2 gap-6 lg:grid-cols-4">
          {featured.map((story) => (
            <StoryTile key={story.id} story={story} />
          ))}
        </div>
      </div>
    </section>
  );
}
