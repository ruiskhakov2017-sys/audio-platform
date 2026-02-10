'use client';

import React from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { Story } from '@/types/story';
import { Play, Crown, ChevronRight, Flame, Sparkles } from 'lucide-react';
import { usePlayerStore } from '@/store/playerStore';
import { motion } from 'framer-motion';

type StoryCardVariant = 'default' | 'catalog';

interface StoryCardProps {
  story: Story;
  variant?: StoryCardVariant;
}

function getCategoryLabel(story: Story): string {
  if (story.tags.some((t) => ['asmr', 'hypnosis'].includes(t.toLowerCase())))
    return 'Гипнозы';
  if (story.tags.some((t) => ['romance', 'voice', 'roleplay'].includes(t.toLowerCase())))
    return 'Ролевые игры';
  if (story.tags.some((t) => ['drama', 'mystery'].includes(t.toLowerCase())))
    return 'Спектакли';
  return 'Аудио-сессия';
}

export const StoryCard = ({ story, variant = 'default' }: StoryCardProps) => {
  const { setTrack, setIsPlaying, currentTrack, isPlaying } = usePlayerStore();

  const isCurrentTrack = currentTrack?.id === story.id;
  const isPlayingCurrent = isCurrentTrack && isPlaying;
  const isPremium =
    story.isPremium || story.tags.some((t) => t.toLowerCase() === 'premium');
  const isNew = story.id >= 6;
  const isHot = story.id <= 3 && story.id >= 1;

  const handlePlay = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (isCurrentTrack) {
      setIsPlaying(!isPlaying);
    } else {
      setTrack(story);
      setIsPlaying(true);
    }
  };

  const categoryLabel = getCategoryLabel(story);

  if (variant === 'catalog') {
    return (
      <motion.div
        whileHover={{ scale: 1.03 }}
        transition={{ duration: 0.2 }}
        className="group relative flex flex-col overflow-hidden rounded-[2.5rem] border border-white/5 bg-[#001d3d]/40 shadow-xl transition-shadow duration-300 hover:shadow-[0_0_40px_rgba(0,180,216,0.12)]"
        style={{ backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)' }}
      >
        <Link
          href={`/story/${story.id}`}
          className="relative block overflow-hidden rounded-t-[2.5rem]"
          style={{ textDecoration: 'none' }}
        >
          <div className="relative aspect-[4/5] w-full overflow-hidden rounded-t-[2.5rem]">
            {story.coverImage ? (
              <Image
                src={story.coverImage}
                alt={story.title}
                fill
                sizes="(max-width: 768px) 50vw, 25vw"
                className="object-cover transition-transform duration-300 group-hover:scale-105"
              />
            ) : (
              <div className="h-full w-full bg-[#001d3d]" />
            )}

            {/* Hover: neon spark border + Quick Play */}
            <div className="absolute inset-0 rounded-t-[2.5rem] ring-2 ring-transparent ring-inset transition-all duration-300 group-hover:ring-[#00B4D8]/40 group-hover:shadow-[0_0_30px_rgba(0,180,216,0.2)]" />
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
              <button
                type="button"
                onClick={handlePlay}
                className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-[#00B4D8] bg-[#00B4D8]/20 text-[#00B4D8] shadow-[0_0_28px_rgba(0,180,216,0.5)] transition-transform hover:scale-105"
                aria-label={isPlayingCurrent ? 'Пауза' : 'Слушать'}
              >
                {isPlayingCurrent ? (
                  <span className="h-4 w-4 rounded-sm bg-[#00B4D8]" />
                ) : (
                  <Play className="ml-0.5 h-6 w-6 fill-[#00B4D8]" strokeWidth={1.5} />
                )}
              </button>
            </div>

            {/* Metal-style badges with neon engraving */}
            <div className="absolute left-2 top-2 flex flex-wrap gap-1.5">
              {isNew && (
                <span
                  className="flex items-center gap-0.5 rounded-lg border px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-white/95"
                  style={{
                    background: 'linear-gradient(180deg, rgba(60,60,70,0.95) 0%, rgba(30,30,35,0.98) 100%)',
                    borderColor: 'rgba(0,180,216,0.5)',
                    boxShadow: '0 0 10px rgba(0,180,216,0.25), inset 0 1px 0 rgba(255,255,255,0.1)',
                  }}
                >
                  <Sparkles className="h-2.5 w-2.5 text-[#00B4D8]" strokeWidth={1.5} />
                  NEW
                </span>
              )}
              {isPremium && (
                <span
                  className="flex items-center gap-0.5 rounded-lg border px-2 py-0.5 text-[10px] font-black uppercase tracking-wider"
                  style={{
                    background: 'linear-gradient(180deg, rgba(180,160,80,0.95) 0%, rgba(120,100,50,0.98) 100%)',
                    borderColor: 'rgba(0,180,216,0.4)',
                    color: 'rgba(10,10,15,0.95)',
                    boxShadow: '0 0 10px rgba(0,180,216,0.2), inset 0 1px 0 rgba(255,255,255,0.3)',
                  }}
                >
                  <Crown className="h-2.5 w-2.5" strokeWidth={1.5} />
                  PREMIUM
                </span>
              )}
              {isHot && (
                <span
                  className="flex items-center gap-0.5 rounded-lg border border-orange-400/50 px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-white"
                  style={{
                    background: 'linear-gradient(180deg, rgba(200,80,40,0.9) 0%, rgba(140,50,20,0.95) 100%)',
                    boxShadow: '0 0 10px rgba(249,115,22,0.3)',
                  }}
                >
                  <Flame className="h-2.5 w-2.5" strokeWidth={1.5} />
                  HOT
                </span>
              )}
            </div>

            <div className="absolute bottom-2 right-2 rounded-xl border border-white/10 bg-black/50 px-2 py-1 text-[10px] font-medium tabular-nums text-white/90">
              {Math.floor(story.durationSec / 60)}:
              {(story.durationSec % 60).toString().padStart(2, '0')}
            </div>
          </div>
        </Link>

        <div className="flex flex-1 flex-col p-4">
          <p className="mb-1 text-xs font-medium uppercase tracking-wider text-white/50">{categoryLabel}</p>
          <Link
            href={`/story/${story.id}`}
            className="mt-2 line-clamp-2 text-lg font-bold leading-tight text-white transition-colors hover:text-[#00B4D8] no-underline"
          >
            {story.title}
          </Link>
          <Link
            href={`/story/${story.id}`}
            className="mt-4 flex w-fit items-center gap-1 rounded-[1rem] border border-white/10 bg-white/5 px-3 py-2 text-sm font-medium text-white/80 no-underline transition-colors hover:border-[#00B4D8]/30 hover:text-[#00B4D8]"
          >
            Слушать
            <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" strokeWidth={1.5} />
          </Link>
        </div>
      </motion.div>
    );
  }

  /* default variant (home, library) */
  return (
    <motion.div
      whileHover={{ scale: 1.03 }}
      transition={{ duration: 0.2 }}
      className="group flex flex-col gap-3 rounded-[2.5rem] border border-white/5 bg-[#001d3d]/40 shadow-lg transition-shadow duration-300 hover:shadow-[0_0_35px_rgba(0,180,216,0.1)]"
      style={{ backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)' }}
    >
      <Link
        href={`/story/${story.id}`}
        className="relative flex flex-col overflow-hidden rounded-[2.5rem] transition-all duration-300"
        style={{ textDecoration: 'none' }}
      >
        <div className="relative aspect-square w-full overflow-hidden rounded-[2.5rem]">
          {story.coverImage ? (
            <Image
              src={story.coverImage}
              alt={story.title}
              fill
              sizes="(max-width: 768px) 50vw, (max-width: 1024px) 33vw, 16vw"
              className="object-cover transition-transform duration-300 group-hover:scale-105"
            />
          ) : (
            <div className="h-full w-full bg-[#001d3d]" />
          )}

          <div className="absolute inset-0 rounded-[2.5rem] ring-2 ring-transparent ring-inset transition-all duration-300 group-hover:ring-[#00B4D8]/30" />
          <div className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
            <button
              type="button"
              onClick={handlePlay}
              className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-[#00B4D8] bg-[#00B4D8]/20 text-[#00B4D8] shadow-[0_0_24px_rgba(0,180,216,0.4)] transition-transform hover:scale-105"
              aria-label={isPlayingCurrent ? 'Пауза' : 'Слушать'}
            >
              {isPlayingCurrent ? (
                <span className="h-4 w-4 rounded-sm bg-[#00B4D8]" />
              ) : (
                <Play className="ml-0.5 h-5 w-5 fill-[#00B4D8]" strokeWidth={1.5} />
              )}
            </button>
          </div>

          {isPremium && (
            <span
              className="absolute left-2 top-2 flex items-center gap-1 rounded-lg border border-[#00B4D8]/30 px-2 py-0.5 text-[10px] font-black uppercase"
              style={{
                background: 'linear-gradient(180deg, rgba(180,160,80,0.9) 0%, rgba(120,100,50,0.95) 100%)',
                color: 'rgba(10,10,15,0.95)',
                boxShadow: '0 0 8px rgba(0,180,216,0.2)',
              }}
            >
              <Crown className="h-3 w-3" strokeWidth={1.5} />
              PREMIUM
            </span>
          )}

          <div className="absolute bottom-2 right-2 rounded-xl border border-white/10 bg-black/50 px-2 py-0.5 text-[10px] font-medium tabular-nums text-white/90">
            {Math.floor(story.durationSec / 60)}:
            {(story.durationSec % 60).toString().padStart(2, '0')}
          </div>
        </div>

        <div className="flex flex-col gap-0.5 p-3">
          <h3 className="line-clamp-1 text-sm font-bold tracking-tight text-white">
            {story.title}
          </h3>
          <p className="line-clamp-1 text-xs text-white/50">
            {story.authorName}
          </p>
        </div>
      </Link>

      {story.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 px-3 pb-3">
          {story.tags.slice(0, 3).map((tag) => (
            <Link
              key={tag}
              href={`/browse?tag=${encodeURIComponent(tag)}`}
              className="rounded-lg border border-[#00B4D8]/20 bg-[#00B4D8]/10 px-1.5 py-0.5 text-[10px] text-[#00B4D8]/90 no-underline transition-opacity hover:opacity-80"
            >
              #{tag}
            </Link>
          ))}
        </div>
      )}
    </motion.div>
  );
};
