'use client';

import { useEffect, useState, useRef } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { usePlayerStore } from '@/store/playerStore';
import { useFavoritesStore } from '@/store/favoritesStore';
import { toggleFavoriteApi } from '@/lib/favoritesApi';
import { Play, Pause, SkipBack, SkipForward, Volume2, Lock, Heart, Info } from 'lucide-react';

export function GlobalPlayerBar() {
  const router = useRouter();
  const currentTrack = usePlayerStore((s) => s.currentTrack);
  const isPlaying = usePlayerStore((s) => s.isPlaying);
  const position = usePlayerStore((s) => s.position);
  const duration = usePlayerStore((s) => s.duration);
  const volume = usePlayerStore((s) => s.volume);
  const togglePlay = usePlayerStore((s) => s.togglePlay);
  const next = usePlayerStore((s) => s.next);
  const previous = usePlayerStore((s) => s.previous);
  const setVolume = usePlayerStore((s) => s.setVolume);
  const seek = usePlayerStore((s) => s.seek);
  const queue = usePlayerStore((s) => s.queue);
  const hasMultipleTracks = queue.length > 1;
  const isPremiumUser = usePlayerStore((s) => s.isPremiumUser);
  const playbackRate = usePlayerStore((s) => s.playbackRate);
  const setPlaybackRate = usePlayerStore((s) => s.setPlaybackRate);
  const { toggleLike, isLiked } = useFavoritesStore();

  const [mounted, setMounted] = useState(false);
  const isFavorite = currentTrack ? isLiked(String(currentTrack.id)) : false;
  const handleFavoriteClick = async () => {
    if (!currentTrack) return;
    const slug = currentTrack.slug;
    const useApi = Boolean(slug && process.env.NEXT_PUBLIC_API_URL && typeof window !== 'undefined' && localStorage.getItem('auth_access_token'));
    if (useApi) {
      const res = await toggleFavoriteApi(slug);
      if (res) toggleLike(String(currentTrack.id));
    } else {
      toggleLike(String(currentTrack.id));
    }
  };
  const SPEEDS = [1, 1.25, 1.5, 2] as const;
  const currentSpeedIndex = SPEEDS.indexOf(playbackRate as 1 | 1.25 | 1.5 | 2) >= 0
    ? SPEEDS.indexOf(playbackRate as 1 | 1.25 | 1.5 | 2)
    : 0;
  const handleSpeedClick = () => {
    const next = (currentSpeedIndex + 1) % SPEEDS.length;
    setPlaybackRate(SPEEDS[next]);
  };
  useEffect(() => {
    if (currentTrack) setMounted(true);
    else setMounted(false);
  }, [currentTrack]);

  if (!currentTrack) return null;

  const isLocked =
    currentTrack.isPremium &&
    (!currentTrack.audioSrc?.trim() || !isPremiumUser);
  const progress = duration > 0 ? (position / duration) * 100 : 0;

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newPosition = (Number(e.target.value) / 100) * duration;
    seek(newPosition);
  };

  return (
    <div
      className={`fixed bottom-0 left-0 right-0 z-50 h-20 bg-black/60 backdrop-blur-xl border-t border-white/10 transition-transform duration-500 ${mounted ? 'translate-y-0' : 'translate-y-full'
        }`}
      role="region"
      aria-label="Плеер"
    >
      {/* Прогресс-бар по верхней границе */}
      <div className="absolute top-0 left-0 right-0 h-1 bg-white/10 group cursor-pointer">
        <input
          type="range"
          min={0}
          max={100}
          value={progress || 0}
          onChange={handleSeek}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-20"
          aria-label="Перемотка"
        />
        <div
          className="h-full bg-cyan-500 transition-all duration-150 group-hover:h-2"
          style={{
            width: `${progress}%`,
            boxShadow: '0 0 10px rgba(6,182,212,0.5)',
          }}
        />
      </div>

      <div className="h-full flex items-center justify-between gap-4 px-4 md:px-10 w-full">
        {/* Слева: обложка + название + автор */}
        <div className="flex items-center gap-4 min-w-0 w-full md:w-[45%]">
          <Link
            href={currentTrack ? `/story/${currentTrack.id}` : '#'}
            className="relative w-12 h-12 shrink-0 rounded-md overflow-hidden bg-white/5 block hover:ring-2 hover:ring-cyan-500/50 transition-all"
          >
            {currentTrack.coverImage && (
              <Image
                src={currentTrack.coverImage}
                alt=""
                fill
                className="object-cover"
                unoptimized
                sizes="48px"
              />
            )}
          </Link>

          <div className="flex flex-col flex-1 min-w-0 w-full justify-center gap-1">
            <div className="flex items-center gap-2 mb-0.5">
              <Link
                href={currentTrack ? `/story/${currentTrack.id}` : '#'}
                className="text-base md:text-lg font-bold text-white truncate hover:text-cyan-400 transition-colors"
              >
                {currentTrack.title}
              </Link>

              <Link
                href={currentTrack ? `/story/${currentTrack.id}` : '#'}
                className="hidden sm:flex items-center justify-center w-5 h-5 rounded-full bg-white/10 hover:bg-cyan-500/20 text-zinc-400 hover:text-cyan-400 transition-colors shrink-0 group relative"
              >
                <Info size={12} strokeWidth={2} />
                <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 text-[10px] text-white bg-black/90 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap pointer-events-none border border-white/10">
                  Подробнее о рассказе
                </span>
              </Link>
            </div>

            <div className="relative w-full overflow-hidden h-5 flex items-center">
              {isLocked ? (
                <span className="text-xs text-amber-400 inline-flex items-center gap-1">
                  <Lock className="w-3 h-3" />
                  Доступно по подписке
                </span>
              ) : currentTrack.description ? (
                <div
                  className="whitespace-nowrap animate-marquee flex"
                  style={{ animationDuration: '75s' }}
                >
                  <span className="text-sm text-white/70 mr-8">
                    {currentTrack.description}
                  </span>
                  <span className="text-sm text-white/70 mr-8">
                    {currentTrack.description}
                  </span>
                </div>
              ) : (
                <p className="text-sm text-zinc-400 truncate">
                  {currentTrack.authorName || 'Неизвестный автор'}
                </p>
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={handleFavoriteClick}
            className={`p-2 rounded-full transition-colors shrink-0 ${isFavorite ? 'text-rose-500' : 'text-zinc-400 hover:text-white'}`}
            aria-label={isFavorite ? 'Убрать из избранного' : 'В избранное'}
          >
            <Heart className="w-5 h-5" strokeWidth={1.5} fill={isFavorite ? 'currentColor' : 'none'} />
          </button>
        </div>

        {/* Центр: Play/Pause или замок */}
        <div className="flex flex-col items-center justify-center gap-1 shrink-0 md:w-[10%]">
          {isLocked && (
            <p className="text-xs text-amber-400/90">Доступно только по подписке</p>
          )}
          <div className="flex items-center justify-center gap-2 md:gap-4">
            <button
              type="button"
              onClick={() => {
                if (hasMultipleTracks) {
                  previous();
                } else {
                  seek(Math.max(0, position - 15));
                }
              }}
              className="hidden md:flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 hover:text-white transition-colors"
              aria-label={hasMultipleTracks ? 'Предыдущий трек' : '15 секунд назад'}
            >
              <SkipBack className="w-5 h-5" strokeWidth={1.5} />
            </button>
            <button
              type="button"
              onClick={() => (isLocked ? router.push('/pricing') : togglePlay())}
              className={`flex h-12 w-12 items-center justify-center rounded-full text-white shadow-[0_0_20px_rgba(6,182,212,0.4)] transition-transform hover:scale-105 ${isLocked
                ? 'bg-amber-500/80 hover:brightness-110'
                : 'bg-cyan-500 hover:brightness-110'
                }`}
              aria-label={isLocked ? 'Доступ по подписке' : isPlaying ? 'Пауза' : 'Воспроизведение'}
            >
              {isLocked ? (
                <Lock className="w-6 h-6" strokeWidth={2} />
              ) : isPlaying ? (
                <Pause className="w-6 h-6" strokeWidth={2} />
              ) : (
                <Play className="w-6 h-6 ml-0.5" strokeWidth={2} fill="currentColor" />
              )}
            </button>
            <button
              type="button"
              onClick={() => {
                if (hasMultipleTracks) {
                  next();
                } else {
                  seek(Math.min(duration, position + 15));
                }
              }}
              className="hidden md:flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 hover:text-white transition-colors"
              aria-label={hasMultipleTracks ? 'Следующий трек' : '15 секунд вперёд'}
            >
              <SkipForward className="w-5 h-5" strokeWidth={1.5} />
            </button>
            <button
              type="button"
              onClick={handleSpeedClick}
              className="flex h-9 min-w-[2.5rem] md:min-w-[3rem] items-center justify-center rounded-full text-zinc-400 hover:text-white transition-colors text-xs md:text-sm font-medium"
              aria-label={`Скорость воспроизведения ${playbackRate}x`}
              title={`Скорость ${playbackRate}x`}
            >
              {playbackRate}x
            </button>
          </div>
        </div>

        {/* Справа: громкость на десктопе */}
        <div className="hidden md:flex items-center justify-end gap-2 w-full md:w-[45%] shrink-0">
          <div className="flex items-center gap-2 w-40">
            <Volume2 className="w-5 h-5 text-zinc-400 shrink-0" strokeWidth={1.5} />
            <input
              type="range"
              min={0}
              max={100}
              value={volume * 100}
              onChange={(e) => setVolume(Number(e.target.value) / 100)}
              className="w-full h-1.5 rounded-full appearance-none bg-white/10 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-cyan-500 [&::-webkit-slider-thumb]:cursor-pointer"
              aria-label="Громкость"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
