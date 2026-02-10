'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import { usePlayerStore } from '@/store/playerStore';
import { Play, Pause, SkipBack, SkipForward, Volume2 } from 'lucide-react';

export function GlobalPlayerBar() {
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

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    if (currentTrack) setMounted(true);
    else setMounted(false);
  }, [currentTrack]);

  if (!currentTrack) return null;

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
          <div className="relative w-12 h-12 shrink-0 rounded-md overflow-hidden bg-white/5">
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
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-white truncate">
              {currentTrack.title}
            </p>
            <p className="text-xs text-zinc-400 truncate">
              {currentTrack.authorName}
            </p>
          </div>
        </div>

        {/* Центр: Play/Pause и перемотка ±15с на десктопе */}
        <div className="flex items-center gap-2 md:gap-4 shrink-0">
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
            onClick={togglePlay}
            className="flex h-12 w-12 items-center justify-center rounded-full bg-cyan-500 text-white shadow-[0_0_20px_rgba(6,182,212,0.4)] hover:brightness-110 transition-transform hover:scale-105"
            aria-label={isPlaying ? 'Пауза' : 'Воспроизведение'}
          >
            {isPlaying ? (
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
