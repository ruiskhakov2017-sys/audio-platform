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

const PLAYER_BAR_HEIGHT = 100;
const CYAN = '#00B4D8';
const SPEEDS = [0.5, 1, 1.5, 2] as const;

const formatTime = (sec: number) => {
  if (!isFinite(sec) || sec < 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
};

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

  const progressPercent = duration > 0 ? (position / duration) * 100 : 0;

  const handleWaveformSeek = useCallback(
    (percent: number) => {
      seek(percent * duration);
    },
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
      className="fixed bottom-0 left-0 z-50 w-full border-t border-white/5 bg-[#001d3d]/80 backdrop-blur-[40px]"
      style={{ height: PLAYER_BAR_HEIGHT }}
    >
      <div className="mx-auto flex h-full max-w-7xl items-center gap-4 px-4 md:px-6">
        {/* Left: track info */}
        <div className="flex min-w-0 shrink-0 items-center gap-3 md:w-[28%]">
          {currentTrack ? (
            <>
              <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-[1rem] border border-white/5 bg-white/5">
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
                <div className="truncate text-xs text-white/50">
                  {currentTrack.authorName}
                </div>
              </div>
            </>
          ) : (
            <span className="text-sm text-white/40">Выберите трек</span>
          )}
        </div>

        {/* Center: waveform + controls */}
        <div className="flex flex-1 flex-col items-center gap-3 md:max-w-[44%]">
          <div className="flex w-full items-center gap-2">
            <span className="w-10 shrink-0 text-right text-xs text-white/50 tabular-nums">
              {formatTime(position)}
            </span>
            <NeonWaveform
              progressPercent={progressPercent}
              onSeek={handleWaveformSeek}
              disabled={!currentTrack}
              className="flex-1"
            />
            <span className="w-10 shrink-0 text-xs text-white/50 tabular-nums">
              {formatTime(duration)}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={previous}
              disabled={!currentTrack || !hasQueue}
              className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 text-white/60 transition-all duration-300 hover:scale-105 hover:border-[#00B4D8]/40 hover:text-[#00B4D8] disabled:opacity-40"
              aria-label="Предыдущий"
            >
              <SkipBack className="h-5 w-5" strokeWidth={1.5} />
            </button>
            <button
              type="button"
              onClick={currentTrack ? (isPlaying ? pause : resume) : undefined}
              disabled={!currentTrack}
              className={`relative flex h-14 w-14 items-center justify-center rounded-full border-2 transition-all duration-300 disabled:scale-100 disabled:opacity-50 ${
                isPlaying
                  ? 'border-[#00B4D8]/60 bg-[#00B4D8]/20 text-[#00B4D8] shadow-[0_0_24px_rgba(0,180,216,0.4)]'
                  : 'animate-[pulse-glow_2s_ease-in-out_infinite] border-[#00B4D8] bg-[#00B4D8]/10 text-white shadow-[0_0_30px_rgba(0,180,216,0.35),inset_0_0_20px_rgba(0,180,216,0.1)]'
              }`}
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
              className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 text-white/60 transition-all duration-300 hover:scale-105 hover:border-[#00B4D8]/40 hover:text-[#00B4D8] disabled:opacity-40"
              aria-label="Следующий"
            >
              <SkipForward className="h-5 w-5" strokeWidth={1.5} />
            </button>
            <button
              type="button"
              onClick={toggleExpand}
              className="ml-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-xs font-medium text-white/80 backdrop-blur-sm transition-colors hover:border-[#00B4D8]/30 hover:text-[#00B4D8]"
            >
              Развернуть
            </button>
          </div>
        </div>

        {/* Right: speed + volume */}
        <div className="hidden w-[28%] shrink-0 items-center justify-end gap-4 md:flex">
          <div className="flex items-center gap-2 rounded-[1.25rem] border border-white/10 bg-white/5 px-3 py-1.5 backdrop-blur-sm">
            <span className="text-xs text-white/50">Скорость</span>
            <div className="flex gap-1">
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setPlaybackRate(s)}
                  className={`rounded-full px-2.5 py-1 text-xs font-medium transition-all ${
                    playbackRate === s
                      ? 'border border-[#00B4D8]/50 bg-[#00B4D8]/20 text-[#00B4D8] shadow-[0_0_12px_rgba(0,180,216,0.2)]'
                      : 'border border-transparent text-white/60 hover:border-white/10 hover:text-white'
                  }`}
                  aria-label={`Скорость ${s}x`}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2 text-white/60">
            <Volume2 className="h-5 w-5 shrink-0" strokeWidth={1.5} />
            <input
              type="range"
              min={0}
              max={100}
              value={volume * 100}
              onChange={(e) => setVolume(Number(e.target.value) / 100)}
              className="h-1.5 w-20 max-w-[80px] cursor-pointer appearance-none rounded-full bg-white/10 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:bg-[#00B4D8] [&::-webkit-slider-thumb]:shadow-[0_0_8px_#00B4D8]"
              aria-label="Громкость"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
