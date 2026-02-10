'use client';

import Link from 'next/link';
import Image from 'next/image';
import { Play } from 'lucide-react';
import { usePlayerStore } from '@/store/playerStore';
import type { Story } from '@/types/story';

export default function CatalogStoryTile({ story }: { story: Story }) {
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

  const categoryLabel = story.tags[0] ? story.tags[0].toUpperCase() : 'АУДИО';

  return (
    <Link
      href={`/story/${story.id}`}
      className="group relative block overflow-hidden rounded-[2rem] no-underline"
    >
      <div className="relative aspect-[3/4]">
        <Image
          src={story.coverImage}
          alt={story.title}
          fill
          sizes="(max-width: 768px) 50vw, (max-width: 1280px) 33vw, 25vw"
          className="object-cover transition-transform duration-300 group-hover:scale-105"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-transparent" />
        <div className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity duration-300 group-hover:opacity-100">
          <button
            type="button"
            onClick={handlePlay}
            className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full border-2 border-[#00B4D8] bg-[#00B4D8]/30 text-[#00B4D8] shadow-[0_0_30px_rgba(0,180,216,0.4)] transition-transform hover:scale-105"
            aria-label={isPlayingCurrent ? 'Пауза' : 'Слушать'}
          >
            {isPlayingCurrent ? (
              <span className="h-6 w-6 rounded-sm bg-[#00B4D8]" />
            ) : (
              <Play className="ml-1 h-10 w-10 shrink-0 fill-[#00B4D8]" strokeWidth={1.5} />
            )}
          </button>
        </div>
      </div>
      <div className="absolute bottom-0 left-0 right-0 p-4">
        <p className="text-[10px] font-medium uppercase tracking-wider text-[#00B4D8]">
          {categoryLabel}
        </p>
        <h3 className="mt-0.5 line-clamp-2 text-base font-bold text-white">
          {story.title}
        </h3>
        <p className="mt-1 text-sm text-zinc-400">
          {story.isPremium ? 'По подписке' : 'Бесплатно'}
        </p>
        <span className="mt-3 block w-full rounded-xl border border-white/10 bg-white/5 py-2.5 text-center text-sm font-medium text-white backdrop-blur-sm">
          Подробнее
        </span>
      </div>
    </Link>
  );
}
