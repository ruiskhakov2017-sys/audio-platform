'use client';

import { useCallback, useRef } from 'react';

const CYAN = '#00B4D8';

type NeonWaveformProps = {
  progress: number;
  isPlaying: boolean;
  onSeek: (percent: number) => void;
};

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

export default function NeonWaveform({ progress, isPlaying, onSeek }: NeonWaveformProps) {
  const trackRef = useRef<HTMLDivElement>(null);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const el = trackRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const percent = clamp((e.clientX - rect.left) / rect.width, 0, 1);
      onSeek(percent);
    },
    [onSeek]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Home') {
        e.preventDefault();
        onSeek(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        onSeek(1);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        onSeek(clamp(progress - 0.05, 0, 1));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        onSeek(clamp(progress + 0.05, 0, 1));
      }
    },
    [onSeek, progress]
  );

  return (
    <div
      ref={trackRef}
      role="slider"
      tabIndex={0}
      aria-label="Прогресс воспроизведения"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(progress * 100)}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      className="relative h-2 flex-1 min-w-0 cursor-pointer rounded-full overflow-hidden"
      style={{
        backgroundColor: 'rgba(255,255,255,0.08)',
        boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.2)',
      }}
    >
      <div
        className="absolute inset-y-0 left-0 rounded-full transition-all duration-150"
        style={{
          width: `${progress * 100}%`,
          background: `linear-gradient(90deg, ${CYAN} 0%, rgba(0,180,216,0.85) 100%)`,
          boxShadow: `0 0 12px rgba(0,180,216,0.5)${isPlaying ? ', 0 0 20px rgba(0,180,216,0.25)' : ''}`,
        }}
      />
    </div>
  );
}
