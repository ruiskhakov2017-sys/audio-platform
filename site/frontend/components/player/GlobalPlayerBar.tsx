'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { usePlayerStore } from '@/store/playerStore';
import { useFavoritesStore } from '@/store/favoritesStore';
import { toggleFavoriteApi } from '@/lib/favoritesApi';
import { Play, Pause, SkipBack, SkipForward, Volume2, Lock, Heart } from 'lucide-react';

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

  return (
    <div
      className={`fixed bottom-0 left-0 right-0 z-50 h-20 bg-black/80 backdrop-blur-xl border-t border-white/10 transition-transform duration-500 ${
        mounted ? 'translate-y-0' : 'translate-y-full'
      }`}
      role="region"
      aria-label="Плеер"
    >
      {/* Прогресс-бар по верхней границе */}
      <div className="absolute top-0 left-0 right-0 h-1 bg-white/10 overflow-hidden">
        <div
          className="h-full bg-cyan-500 transition-all duration-150"
          style={{
            width: `${progress}%`,
            boxShadow: '0 0 10px rgba(6,182,212,0.5)',
          }}
        />
      </div>

      <div className="h-full flex items-center justify-between gap-4 px-4 md:px-6 max-w-7xl mx-auto">
        {/* Слева: обложка + название + автор */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <Link
            href={currentTrack ? `/story/${currentTrack.slug || currentTrack.id}` : '#'}
            className="relative w-12 h-12 shrink-0 rounded-md overflow-hidden bg-white/5 block"
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
          <button
            type="button"
            onClick={handleFavoriteClick}
            className={`p-2 rounded-full transition-colors shrink-0 ${isFavorite ? 'text-rose-500' : 'text-zinc-400 hover:text-white'}`}
            aria-label={isFavorite ? 'Убрать из избранного' : 'В избранное'}
          >
            <Heart className="w-5 h-5" strokeWidth={1.5} fill={isFavorite ? 'currentColor' : 'none'} />
          </button>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-white truncate">
              {currentTrack.title}
            </p>
            <p className="text-xs text-zinc-400 truncate">
              {isLocked ? (
                <span className="inline-flex items-center gap-1 text-amber-400">
                  <Lock className="w-3 h-3 shrink-0" />
                  Доступно только по подписке
                </span>
              ) : (
                currentTrack.authorName
              )}
            </p>
          </div>
        </div>

        {/* Центр: Play/Pause или замок */}
        <div className="flex flex-col items-center gap-1 shrink-0">
          {isLocked && (
            <p className="text-xs text-amber-400/90">Доступно только по подписке</p>
          )}
          <div className="flex items-center gap-2 md:gap-4">
            <button
              type="button"
              onClick={() => (hasMultipleTracks ? previous() : seek(Math.max(0, position - 15)))}
              className="hidden md:flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 hover:text-white transition-colors disabled:opacity-40"
              aria-label={hasMultipleTracks ? 'Предыдущий трек' : '15 секунд назад'}
              disabled={!hasMultipleTracks && position <= 0}
            >
              <SkipBack className="w-5 h-5" strokeWidth={1.5} />
            </button>
            <button
              type="button"
              onClick={() => (isLocked ? router.push('/pricing') : togglePlay())}
              className={`flex h-12 w-12 items-center justify-center rounded-full text-white shadow-[0_0_20px_rgba(6,182,212,0.4)] transition-transform hover:scale-105 ${
                isLocked
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
              onClick={() => (hasMultipleTracks ? next() : seek(Math.min(duration, position + 15)))}
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
        <div className="hidden md:flex items-center gap-2 w-40 shrink-0">
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
  );
}
