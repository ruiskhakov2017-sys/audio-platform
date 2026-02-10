'use client';

import { useCallback, useState } from 'react';

const BAR_COUNT = 32;
const ACCENT = '#00B4D8';

const barHeights = Array.from({ length: BAR_COUNT }, (_, i) => {
  const seed = Math.sin(i * 0.7) * 0.4 + Math.cos(i * 0.3) * 0.3 + 0.5;
  return 24 + Math.floor(seed * 40);
});

type NeonWaveformProps = {
  progressPercent: number;
  onSeek: (percent: number) => void;
  disabled?: boolean;
  className?: string;
};

export default function NeonWaveform({
  progressPercent,
  onSeek,
  disabled = false,
  className = '',
}: NeonWaveformProps) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (disabled) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width;
      onSeek(Math.max(0, Math.min(1, x)));
    },
    [onSeek, disabled]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (disabled) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width;
      const index = Math.floor(x * BAR_COUNT);
      setHoverIndex(Math.max(0, Math.min(BAR_COUNT - 1, index)));
    },
    [disabled]
  );

  const handleMouseLeave = useCallback(() => setHoverIndex(null), []);

  return (
    <div
      role="slider"
      aria-label="Прогресс воспроизведения"
      aria-valuenow={Math.round(progressPercent)}
      aria-valuemin={0}
      aria-valuemax={100}
      tabIndex={0}
      onKeyDown={(e) => {
        if (disabled) return;
        const step = e.key === 'ArrowRight' ? 2 : e.key === 'ArrowLeft' ? -2 : 0;
        if (step) {
          e.preventDefault();
          onSeek(Math.max(0, Math.min(100, progressPercent + step)) / 100);
        }
      }}
      className={`flex h-10 items-end justify-between gap-0.5 ${className}`}
      style={{ minWidth: 160 }}
      onClick={handleClick}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
    >
      {barHeights.map((h, i) => {
        const filled = (i + 1) / BAR_COUNT <= progressPercent / 100;
        const isHover = hoverIndex === i;
        return (
          <div
            key={i}
            className="flex-1 min-w-[3px] max-w-[6px] rounded-sm transition-all duration-150"
            style={{
              height: h,
              background: filled
                ? `linear-gradient(to top, ${ACCENT}, rgba(255,255,255,0.95))`
                : 'rgba(255,255,255,0.12)',
              boxShadow: filled
                ? `0 0 8px ${ACCENT}${isHover ? ', 0 0 14px rgba(0,180,216,0.6)' : ''}`
                : isHover
                  ? `0 0 6px ${ACCENT}`
                  : 'none',
              opacity: isHover ? 1 : filled ? 0.95 : 0.7,
            }}
          />
        );
      })}
    </div>
  );
}
