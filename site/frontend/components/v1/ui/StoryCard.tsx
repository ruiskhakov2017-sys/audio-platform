'use client';

import React from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { Story } from '@/types/story';
import { getDisplayTags } from '@/lib/stories';
import { Play, Crown, ChevronRight, Flame, Sparkles } from 'lucide-react';
import { usePlayerStore } from '@/store/playerStore';

type StoryCardVariant = 'default' | 'catalog';

interface StoryCardProps {
  story: Story;
  variant?: StoryCardVariant;
}

function getCategoryLabel(story: Story): string {
  const tags = getDisplayTags(story);
  if (tags.some((t) => ['asmr', 'hypnosis'].includes(t.toLowerCase())))
    return 'Гипнозы';
  if (tags.some((t) => ['romance', 'voice', 'roleplay'].includes(t.toLowerCase())))
    return 'Ролевые игры';
  if (tags.some((t) => ['drama', 'mystery'].includes(t.toLowerCase())))
    return 'Спектакли';
  return 'Аудио-сессия';
}

export const StoryCard = ({ story, variant = 'default' }: StoryCardProps) => {
  const { setTrack, setIsPlaying, currentTrack, isPlaying } = usePlayerStore();

  const isCurrentTrack = currentTrack?.id === story.id;
  const isPlayingCurrent = isCurrentTrack && isPlaying;
  const isPremium =
    story.isPremium || getDisplayTags(story).some((t) => t.toLowerCase() === 'premium');
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
      <div
        className="group flex flex-col overflow-hidden rounded-3xl border border-white/10 bg-white/5 shadow-none transition-all duration-300 hover:-translate-y-1 hover:shadow-[0_12px_40px_-8px_rgba(167,139,250,0.35)]"
        style={{ backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)' }}
      >
        <Link
          href={`/story/${story.id}`}
          className="relative block overflow-hidden rounded-t-3xl"
          style={{ textDecoration: 'none' }}
        >
          <div className="relative aspect-[4/5] w-full overflow-hidden rounded-t-3xl">
            {story.coverImage ? (
              <Image
                src={story.coverImage}
                alt={story.title}
                fill
                sizes="(max-width: 768px) 50vw, 25vw"
                className="object-cover transition-transform duration-300 group-hover:scale-105"
              />
            ) : (
              <div className="h-full w-full bg-[#1a1124]" />
            )}

            <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
              <button
                type="button"
                onClick={handlePlay}
                className="flex h-12 w-12 items-center justify-center rounded-full bg-[#a78bfa] text-white shadow-lg transition-transform hover:scale-105"
                aria-label={isPlayingCurrent ? 'Пауза' : 'Слушать'}
              >
                {isPlayingCurrent ? (
                  <span className="h-4 w-4 rounded-sm bg-white" />
                ) : (
                  <Play className="ml-0.5 h-5 w-5 fill-current" />
                )}
              </button>
            </div>

            {/* Badges: NEW красный градиент, PREMIUM золотая корона */}
            <div className="absolute left-2 top-2 flex flex-wrap gap-1">
              {isNew && (
                <span
                  className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-bold text-white"
                  style={{ background: 'linear-gradient(135deg, #dc2626 0%, #b91c1c 100%)' }}
                >
                  <Sparkles className="h-2.5 w-2.5" />
                  NEW
                </span>
              )}
              {isPremium && (
                <span
                  className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-bold text-zinc-900"
                  style={{ backgroundColor: 'rgba(212, 175, 55, 0.9)' }}
                >
                  <Crown className="h-2.5 w-2.5" />
                  PREMIUM
                </span>
              )}
              {isHot && (
                <span
                  className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-bold text-white"
                  style={{ backgroundColor: 'rgba(249, 115, 22, 0.9)' }}
                >
                  <Flame className="h-2.5 w-2.5" />
                  HOT
                </span>
              )}
            </div>

            <div className="absolute bottom-2 right-2 rounded-lg bg-black/60 px-2 py-0.5 text-[10px] font-medium text-[#e9d5ff]">
              {Math.floor(story.durationSec / 60)}:
              {(story.durationSec % 60).toString().padStart(2, '0')}
            </div>
          </div>
        </Link>

        <div className="flex flex-1 flex-col p-4">
          <p className="mb-1 text-sm text-zinc-500">{categoryLabel}</p>
          <Link
            href={`/story/${story.id}`}
            className="mt-3 line-clamp-2 text-lg font-semibold leading-tight text-white transition-colors hover:text-[#c4b5fd] no-underline"
          >
            {story.title}
          </Link>
          <p className="mt-2 font-bold text-white">
            {story.isPremium ? 'от 530₽' : 'Бесплатно'}
          </p>
          <Link
            href={`/story/${story.id}`}
            className="mt-auto flex w-fit items-center gap-1 rounded-lg border border-white/10 px-3 py-2 text-sm font-medium text-[#c4b5fd] no-underline transition-colors hover:border-[#a78bfa]/30 hover:bg-white/5"
          >
            Подробнее
            <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
      </div>
    );
  }

  /* default variant (home, library) */
  return (
    <div
      className="group flex flex-col gap-3"
      style={{
        borderRadius: 12,
        backgroundColor: 'rgba(30, 58, 95, 0.35)',
        border: '1px solid rgba(59, 130, 246, 0.15)',
      }}
    >
      <Link
        href={`/story/${story.id}`}
        className="relative flex flex-col overflow-hidden transition-all duration-300 hover:scale-[1.02]"
        style={{ textDecoration: 'none' }}
      >
        <div
          className="relative aspect-square w-full overflow-hidden rounded-xl"
          style={{ borderRadius: 12 }}
        >
          {story.coverImage ? (
            <Image
              src={story.coverImage}
              alt={story.title}
              fill
              sizes="(max-width: 768px) 50vw, (max-width: 1024px) 33vw, 16vw"
              className="object-cover transition-transform duration-300 group-hover:scale-105"
            />
          ) : (
            <div className="h-full w-full" style={{ backgroundColor: '#1e3a5f' }} />
          )}

          <div
            className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity duration-300 group-hover:opacity-100"
            style={{ backgroundColor: 'rgba(12, 18, 34, 0.6)' }}
          >
            <button
              type="button"
              onClick={handlePlay}
              className="flex h-12 w-12 items-center justify-center rounded-full shadow-lg transition-all duration-300 hover:scale-105"
              style={{ backgroundColor: '#2563eb', color: '#fff' }}
              aria-label={isPlayingCurrent ? 'Пауза' : 'Воспроизвести'}
            >
              {isPlayingCurrent ? (
                <span className="h-4 w-4 rounded-sm bg-white" />
              ) : (
                <Play className="ml-0.5 h-5 w-5 fill-current" />
              )}
            </button>
          </div>

          {isPremium && (
            <div
              className="absolute left-2 top-2 flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold"
              style={{ backgroundColor: 'rgba(245, 158, 11, 0.95)', color: '#000' }}
            >
              <Crown className="h-3 w-3" />
              PREMIUM
            </div>
          )}

          <div
            className="absolute bottom-2 right-2 rounded-lg px-2 py-0.5 text-[10px] font-medium"
            style={{ backgroundColor: 'rgba(12, 18, 34, 0.8)', color: '#e2e8f0' }}
          >
            {Math.floor(story.durationSec / 60)}:
            {(story.durationSec % 60).toString().padStart(2, '0')}
          </div>
        </div>

        <div className="flex flex-col gap-0.5 p-3">
          <h3 className="line-clamp-1 text-sm font-bold tracking-tight" style={{ color: '#f1f5f9' }}>
            {story.title}
          </h3>
          <p className="line-clamp-1 text-xs" style={{ color: '#94a3b8' }}>
            {story.authorName}
          </p>
        </div>
      </Link>

      {getDisplayTags(story).length > 0 && (
        <div className="flex flex-wrap gap-1 px-3 pb-3">
          {getDisplayTags(story).slice(0, 3).map((tag) => (
            <Link
              key={tag}
              href={`/browse?tag=${encodeURIComponent(tag)}`}
              className="rounded px-1.5 py-0.5 text-[10px] transition-opacity hover:opacity-80"
              style={{
                backgroundColor: 'rgba(59, 130, 246, 0.2)',
                color: '#93c5fd',
                textDecoration: 'none',
              }}
            >
              #{tag}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
};
