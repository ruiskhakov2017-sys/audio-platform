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
import { motion, AnimatePresence } from 'framer-motion';
import NeonWaveform from '@/components/ui/NeonWaveform';

const PLAYER_BAR_HEIGHT = 100;
const ACCENT = '#00B4D8';
const SPEEDS = [1, 1.5, 2] as const;

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
    <AnimatePresence>
      {currentTrack && (
        <motion.div
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={{ type: 'spring', damping: 28, stiffness: 300 }}
          className="fixed bottom-0 left-0 right-0 z-50 border-t border-white/5 bg-[#000814]/90 backdrop-blur-xl"
          style={{ height: PLAYER_BAR_HEIGHT }}
        >
          <div className="mx-auto flex h-full w-full max-w-6xl items-center gap-4 px-6 md:px-8 lg:px-12">
            <div className="flex min-w-0 shrink-0 items-center gap-3 md:w-[28%]">
              <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-xl border border-white/10 bg-white/5">
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
                <div className="truncate text-sm font-bold text-white">
                  {currentTrack.title}
                </div>
                <div className="truncate text-xs text-zinc-400">
                  {currentTrack.authorName}
                </div>
              </div>
            </div>

            <div className="flex flex-1 flex-col items-center gap-3 md:max-w-[44%]">
              <div className="flex w-full items-center gap-2">
                <span className="w-10 shrink-0 text-right text-xs text-zinc-400 tabular-nums">
                  {formatTime(position)}
                </span>
                <NeonWaveform
                  progressPercent={progressPercent}
                  onSeek={handleWaveformSeek}
                  className="flex-1"
                />
                <span className="w-10 shrink-0 text-xs text-zinc-400 tabular-nums">
                  {formatTime(duration)}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={previous}
                  disabled={!hasQueue}
                  className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 text-zinc-400 transition-colors hover:border-[#00B4D8]/40 hover:text-[#00B4D8] disabled:opacity-40"
                  aria-label="Предыдущий"
                >
                  <SkipBack className="h-5 w-5 shrink-0" strokeWidth={1.5} />
                </button>
                <button
                  type="button"
                  onClick={isPlaying ? pause : resume}
                  className="flex h-12 w-12 items-center justify-center rounded-full border-2 border-[#00B4D8] bg-[#00B4D8]/20 text-[#7dd3fc] shadow-[0_0_24px_rgba(0,180,216,0.3)] transition-all hover:shadow-[0_0_36px_rgba(0,180,216,0.4)]"
                  aria-label={isPlaying ? 'Пауза' : 'Воспроизведение'}
                >
                  {isPlaying ? (
                    <Pause className="h-6 w-6 shrink-0 fill-current" strokeWidth={1.5} />
                  ) : (
                    <Play className="ml-0.5 h-6 w-6 shrink-0 fill-current" strokeWidth={1.5} />
                  )}
                </button>
                <button
                  type="button"
                  onClick={next}
                  disabled={!hasQueue}
                  className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 text-zinc-400 transition-colors hover:border-[#00B4D8]/40 hover:text-[#00B4D8] disabled:opacity-40"
                  aria-label="Следующий"
                >
                  <SkipForward className="h-5 w-5 shrink-0" strokeWidth={1.5} />
                </button>
                <button
                  type="button"
                  onClick={toggleExpand}
                  className="ml-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs font-medium text-zinc-400 transition-colors hover:border-[#00B4D8]/30 hover:text-[#00B4D8]"
                >
                  Развернуть
                </button>
              </div>
            </div>

            <div className="hidden w-[28%] shrink-0 items-center justify-end gap-4 md:flex">
              <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-1.5">
                {SPEEDS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setPlaybackRate(s)}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium transition-all ${
                      playbackRate === s
                        ? 'bg-[#00B4D8]/20 text-[#00B4D8] shadow-[0_0_12px_rgba(0,180,216,0.2)]'
                        : 'text-zinc-400 hover:text-white'
                    }`}
                  >
                    {s}x
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 text-zinc-400">
                <Volume2 className="h-5 w-5 shrink-0" strokeWidth={1.5} />
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={volume * 100}
                  onChange={handleVolumeChange}
                  className="h-1.5 w-20 max-w-[80px] cursor-pointer appearance-none rounded-full bg-white/10 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:bg-[#00B4D8]"
                  aria-label="Громкость"
                />
              </div>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
