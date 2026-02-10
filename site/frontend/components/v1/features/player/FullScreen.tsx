'use client';

import { usePlayerStore } from '@/store/playerStore';
import Image from 'next/image';

const rates = [0.8, 1, 1.2, 1.5];

export default function FullScreen() {
  const {
    currentTrack,
    isExpanded,
    isPlaying,
    pause,
    resume,
    toggleExpand,
    playbackRate,
    setPlaybackRate,
    position,
    duration,
  } = usePlayerStore();

  if (!isExpanded) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6 backdrop-blur">
      <div className="w-full max-w-2xl rounded-3xl border border-white/10 bg-[#0a0a0a] p-6 shadow-[0_0_30px_rgba(0,0,0,0.4)]">
        <div className="flex items-start justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.3em] text-white/40">Now Playing</div>
            <div className="mt-2 text-2xl font-semibold text-white">{currentTrack?.title || 'No track'}</div>
            <div className="text-sm text-white/60">{currentTrack?.authorName || ''}</div>
          </div>
          <button
            onClick={toggleExpand}
            className="rounded-full border border-white/10 px-4 py-2 text-xs text-white/70 hover:border-white/30 hover:text-white"
          >
            Close
          </button>
        </div>
        <div className="mt-6 grid gap-6 md:grid-cols-[280px_1fr]">
          <div className="relative aspect-square overflow-hidden rounded-2xl bg-white/5">
            {currentTrack?.coverImage && (
              <Image
                src={currentTrack.coverImage}
                alt={currentTrack.title}
                fill
                sizes="(max-width: 768px) 80vw, 280px"
                className="object-cover"
              />
            )}
          </div>
          <div className="space-y-5">
            <div className="flex items-center gap-3">
              <button
                onClick={isPlaying ? pause : resume}
                className="rounded-full bg-white px-6 py-3 text-sm font-semibold text-black transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] hover:scale-[1.03]"
              >
                {isPlaying ? 'Pause' : 'Play'}
              </button>
            </div>
            <div className="space-y-2">
              <div className="h-2 w-full rounded-full bg-white/10">
                <div
                  className="h-2 rounded-full bg-purple-500"
                  style={{ width: duration ? `${(position / duration) * 100}%` : '0%' }}
                />
              </div>
              <div className="text-xs text-white/50">
                {Math.floor(position / 60)}:{Math.floor(position % 60)
                  .toString()
                  .padStart(2, '0')}{' '}
                / {Math.floor(duration / 60)}:{Math.floor(duration % 60).toString().padStart(2, '0')}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {rates.map((rate) => (
                <button
                  key={rate}
                  onClick={() => setPlaybackRate(rate)}
                  className={`rounded-full border px-4 py-2 text-xs transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] ${playbackRate === rate
                      ? 'border-white/40 bg-white/10 text-white'
                      : 'border-white/10 text-white/60 hover:border-white/30 hover:text-white'
                    }`}
                >
                  {rate}x
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
