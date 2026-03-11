'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Play } from 'lucide-react';
import { usePlayerStore } from '@/store/playerStore';
import type { Story } from '@/types/story';

type HeroStoryOfDayProps = {
  story: Story;
};

export default function HeroStoryOfDay({ story }: HeroStoryOfDayProps) {
  const { setTrack, setIsPlaying, currentTrack, isPlaying } = usePlayerStore();
  const storyHref = `/story/${story.slug || story.id}`;
  const isCurrent = currentTrack?.id === story.id;
  const isPlayingCurrent = isCurrent && isPlaying;

  const handlePlay = (e: React.MouseEvent) => {
    e.preventDefault();
    if (isCurrent) {
      setIsPlaying(!isPlaying);
    } else {
      setTrack(story);
      setIsPlaying(true);
    }
  };

  return (
    <Link
      href={storyHref}
      className="group relative block overflow-hidden rounded-2xl"
      style={{
        minHeight: 320,
        backgroundColor: '#1e3a5f',
        textDecoration: 'none',
      }}
    >
      <div className="absolute inset-0">
        <Image
          src={story.coverImage}
          alt=""
          fill
          className="object-cover opacity-70 transition-opacity duration-300 group-hover:opacity-85"
          sizes="(max-width: 1024px) 100vw, 1200px"
        />
        <div
          className="absolute inset-0"
          style={{
            background: 'linear-gradient(to top, #0c1222 0%, transparent 50%, rgba(12,18,34,0.4) 100%)',
          }}
        />
      </div>
      <div className="relative flex min-h-[320px] flex-col justify-end p-6 md:p-10">
        <span className="mb-2 text-xs font-semibold uppercase tracking-wider" style={{ color: '#93c5fd' }}>
          История дня
        </span>
        <h2 className="text-3xl font-bold tracking-tight md:text-4xl lg:text-5xl" style={{ color: '#f1f5f9' }}>
          {story.title}
        </h2>
        <p className="mt-2 max-w-2xl text-sm md:text-base" style={{ color: '#94a3b8' }}>
          {story.description}
        </p>
        <div className="mt-6 flex items-center gap-4">
          <button
            type="button"
            onClick={handlePlay}
            className="flex h-14 w-14 items-center justify-center rounded-full shadow-lg transition-transform hover:scale-105"
            style={{ backgroundColor: '#2563eb', color: '#fff' }}
            aria-label={isPlayingCurrent ? 'Пауза' : 'Слушать'}
          >
            {isPlayingCurrent ? (
              <span className="h-5 w-5 rounded-sm bg-white" />
            ) : (
              <>
                <span className="text-sm" style={{ color: '#94a3b8' }}>
                  {story.authorName}
                </span>
                <Play className="ml-1 h-6 w-6 fill-current" />
              </>
            )}
          </button>
        </div>
      </div>
    </Link>
  );
}
