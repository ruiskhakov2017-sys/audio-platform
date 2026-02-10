'use client';

import Link from 'next/link';
import NeonButton from '@/components/ui/NeonButton';

export default function Hero() {
  return (
    <section className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden pt-28 pb-20">
      {/* Волна на всю ширину экрана — визуал «по всему» */}
      <div className="pointer-events-none absolute inset-0 w-full opacity-25">
        <svg
          className="h-full w-full"
          viewBox="0 0 800 200"
          preserveAspectRatio="none"
        >
          <defs>
            <linearGradient id="waveGrad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#00B4D8" stopOpacity="0.2" />
              <stop offset="50%" stopColor="#00B4D8" stopOpacity="0.6" />
              <stop offset="100%" stopColor="#00B4D8" stopOpacity="0.2" />
            </linearGradient>
          </defs>
          <path
            fill="none"
            stroke="url(#waveGrad)"
            strokeWidth="2"
            d="M0,100 Q100,50 200,100 T400,100 T600,100 T800,100"
            className="animate-[wave_8s_linear_infinite]"
          />
          <path
            fill="none"
            stroke="url(#waveGrad)"
            strokeWidth="1.5"
            d="M0,120 Q150,70 300,120 T600,120 T800,120"
            className="animate-[wave_10s_linear_infinite]"
          />
        </svg>
      </div>

      {/* Контент в блоке ~65% ширины, как на референсе */}
      <div className="relative z-10 w-full min-w-0 px-6 text-center sm:px-8 md:max-w-[75%] lg:max-w-[70%] lg:px-10 xl:max-w-[65%]">
        <h1 className="text-4xl font-black leading-[1.15] tracking-tight md:text-5xl lg:text-6xl">
          <span className="bg-gradient-to-r from-white via-[#7dd3fc] to-[#00B4D8] bg-clip-text text-transparent">
            Искусство звука для глубокого погружения
          </span>
        </h1>
        <p className="mt-6 text-lg text-zinc-400 md:text-xl">
          Чувственные аудио: расслабление, атмосфера и живое воображение через голос и звук.
        </p>

        <div className="mt-14 flex flex-wrap items-center justify-center gap-5">
        <Link href="/browse">
          <NeonButton variant="primary">В каталог</NeonButton>
        </Link>
        <Link href="#about">
          <NeonButton variant="glass">Подробнее</NeonButton>
        </Link>
        </div>
      </div>
    </section>
  );
}
