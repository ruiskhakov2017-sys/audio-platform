'use client';

import { usePlayerStore } from '@/store/playerStore';
import Image from 'next/image';
import { motion, AnimatePresence } from 'framer-motion';

const rates = [0.5, 1, 1.5, 2];
const CYAN = '#00B4D8';

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

  const progress = duration ? (position / duration) * 100 : 0;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6 backdrop-blur-md"
      >
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          className="w-full max-w-2xl rounded-[2.5rem] border border-white/5 p-6"
          style={{
            backgroundColor: 'rgba(0, 29, 61, 0.9)',
            backdropFilter: 'blur(40px)',
            WebkitBackdropFilter: 'blur(40px)',
            boxShadow: `0 0 60px rgba(0,180,216,0.15)`,
          }}
        >
          <div className="flex items-start justify-between">
            <div>
              <div className="text-xs font-bold uppercase tracking-[0.3em] text-white/40">Now Playing</div>
              <div className="mt-2 text-2xl font-black tracking-tight text-white">{currentTrack?.title || 'No track'}</div>
              <div className="text-sm text-[#00B4D8]/90">{currentTrack?.authorName || ''}</div>
            </div>
            <button
              onClick={toggleExpand}
              className="rounded-full border border-white/5 px-4 py-2 text-xs text-white/70 transition-colors hover:border-[#00B4D8]/40 hover:text-white"
            >
              Закрыть
            </button>
          </div>
          <div className="mt-6 grid gap-6 md:grid-cols-[280px_1fr]">
            <div className="relative aspect-square overflow-hidden rounded-[2.5rem] border border-white/5">
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
              <button
                onClick={isPlaying ? pause : resume}
                className="rounded-full px-8 py-4 text-sm font-bold text-white transition-all hover:scale-[1.03]"
                style={{
                  backgroundColor: CYAN,
                  boxShadow: '0 0 30px rgba(0,180,216,0.4), inset 0 0 20px rgba(255,255,255,0.1)',
                }}
              >
                {isPlaying ? 'Пауза' : 'Слушать'}
              </button>
              <div className="space-y-2">
                <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full transition-all duration-150"
                    style={{
                      width: `${progress}%`,
                      background: `linear-gradient(90deg, #fff, ${CYAN})`,
                      boxShadow: `0 0 10px ${CYAN}`,
                    }}
                  />
                </div>
                <div className="text-xs text-white/50">
                  {Math.floor(position / 60)}:{(position % 60).toFixed(0).padStart(2, '0')} / {Math.floor(duration / 60)}:{(duration % 60).toFixed(0).padStart(2, '0')}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {rates.map((rate) => (
                  <button
                    key={rate}
                    onClick={() => setPlaybackRate(rate)}
                    className={`rounded-full border px-4 py-2 text-xs font-medium transition-all ${
                      playbackRate === rate
                        ? 'border-[#00B4D8]/50 bg-[#00B4D8]/20 text-[#00B4D8] shadow-[0_0_15px_rgba(0,180,216,0.2)]'
                        : 'border-white/5 bg-white/5 text-white/60 hover:border-white/10 hover:text-white'
                    }`}
                  >
                    {rate}x
                  </button>
                ))}
              </div>
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
