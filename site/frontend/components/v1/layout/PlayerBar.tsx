'use client';

import { usePlayerStore } from '@/store/playerStore';
import useAudioEngine from '@/lib/useAudioEngine';
import Image from 'next/image';
import {
  SkipBack,
  SkipForward,
  Play,
  Pause,
  Volume2,
} from 'lucide-react';
import { useCallback } from 'react';
import NeonWaveform from '@/components/v1/ui/NeonWaveform';

const PLAYER_BAR_HEIGHT = 90;
const CYAN = '#00B4D8';

const formatTime = (sec: number) => {
  if (!isFinite(sec) || sec < 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
};

const SPEEDS = [0.5, 1, 1.5, 2] as const;

export default function PlayerBar() {
  useAudioEngine();
  const {
    currentTrack,
    isPlaying,
    position,
    duration,
    volume,
    playbackRate,
    setVolume,
    setPlaybackRate,
    pause,
    resume,
    toggleExpand,
    next,
    previous,
    seek,
    queue,
  } = usePlayerStore();

  const progress = duration > 0 ? position / duration : 0;

  const handleSeek = useCallback(
    (percent: number) => seek(percent * duration),
    [duration, seek]
  );

  const handleVolumeChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setVolume(Number(e.target.value));
    },
    [setVolume]
  );

  const hasQueue = queue.length > 0;

  return (
    <div
      className="fixed bottom-0 left-0 z-50 w-full border-t border-white/5"
      style={{
        height: PLAYER_BAR_HEIGHT,
        backgroundColor: 'rgba(0, 29, 61, 0.6)',
        backdropFilter: 'blur(40px)',
        WebkitBackdropFilter: 'blur(40px)',
      }}
    >
      <div className="mx-auto flex h-full max-w-7xl items-center gap-6 px-4 md:px-6">
        <div className="flex min-w-0 shrink-0 items-center gap-3 md:w-[30%]">
          {currentTrack ? (
            <>
              <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-[2.5rem] border border-white/5 bg-white/5">
                {currentTrack.coverImage && (
                  <Image
                    src={currentTrack.coverImage}
                    alt={currentTrack.title}
                    fill
                    sizes="56px"
                    className="object-cover"
                  />
                )}
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-bold tracking-tight text-white">
                  {currentTrack.title}
                </div>
              </div>
            </>
          ) : (
            <span className="text-sm text-zinc-500">Выберите трек</span>
          )}
        </div>

        <div className="flex flex-1 flex-col items-center gap-2 md:max-w-[40%]">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={previous}
              disabled={!currentTrack || !hasQueue}
              className="flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 transition-all duration-300 hover:scale-105 hover:text-[#00B4D8] disabled:opacity-40 disabled:hover:scale-100"
              aria-label="Предыдущий"
            >
              <SkipBack className="h-5 w-5" strokeWidth={1.5} />
            </button>

            <button
              type="button"
              onClick={currentTrack ? (isPlaying ? pause : resume) : undefined}
              disabled={!currentTrack}
              className={`relative flex h-14 w-14 items-center justify-center rounded-full transition-all duration-300 hover:scale-105 disabled:scale-100 disabled:opacity-50 ${currentTrack && !isPlaying ? 'animate-neon-pulse' : ''}`}
              style={{
                backgroundColor: currentTrack ? CYAN : 'rgba(0,180,216,0.3)',
                boxShadow: '0 0 20px rgba(0,180,216,0.4), inset 0 0 15px rgba(0,180,216,0.15)',
              }}
              aria-label={isPlaying ? 'Пауза' : 'Воспроизведение'}
            >
              {isPlaying ? (
                <Pause className="h-6 w-6 fill-current" strokeWidth={1.5} />
              ) : (
                <Play className="ml-0.5 h-6 w-6 fill-current" strokeWidth={1.5} />
              )}
            </button>

            <button
              type="button"
              onClick={next}
              disabled={!currentTrack || !hasQueue}
              className="flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 transition-all duration-300 hover:scale-105 hover:text-[#00B4D8] disabled:opacity-40 disabled:hover:scale-100"
              aria-label="Следующий"
            >
              <SkipForward className="h-5 w-5" strokeWidth={1.5} />
            </button>

            <button
              type="button"
              onClick={toggleExpand}
              className="ml-2 rounded-[2.5rem] border border-white/5 px-3 py-1.5 text-xs font-medium text-[#00B4D8] transition-colors hover:bg-[#00B4D8]/10"
            >
              Развернуть
            </button>
          </div>

          <div className="flex w-full items-center gap-2 text-xs text-zinc-400">
            <span className="w-10 shrink-0 text-right">{formatTime(position)}</span>
            <NeonWaveform progress={progress} isPlaying={isPlaying} onSeek={handleSeek} />
            <span className="w-10 shrink-0">{formatTime(duration)}</span>
          </div>
        </div>

        <div className="hidden w-[28%] shrink-0 items-center justify-end gap-4 text-zinc-400 md:flex">
          <div className="flex items-center gap-2">
            <span className="text-xs">Скорость</span>
            <div
              className="flex rounded-[2.5rem] border border-white/5 p-0.5"
              style={{ backgroundColor: 'rgba(255,255,255,0.03)' }}
            >
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setPlaybackRate(s)}
                  className="rounded-full px-2.5 py-1 text-xs font-medium transition-all"
                  style={{
                    backgroundColor: playbackRate === s ? 'rgba(0,180,216,0.3)' : 'transparent',
                    color: playbackRate === s ? '#00B4D8' : 'rgba(255,255,255,0.6)',
                    border: playbackRate === s ? '1px solid rgba(0,180,216,0.5)' : '1px solid transparent',
                  }}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Volume2 className="h-5 w-5 shrink-0" strokeWidth={1.5} />
            <input
              type="range"
              min={0}
              max={100}
              value={volume * 100}
              onChange={(e) => setVolume(Number(e.target.value) / 100)}
              className="h-1.5 w-20 max-w-[80px] cursor-pointer appearance-none rounded-full [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:appearance-none"
              style={{
                background: `linear-gradient(to right, ${CYAN} ${volume * 100}%, rgba(255,255,255,0.1) ${volume * 100}%)`,
              }}
              aria-label="Громкость"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
