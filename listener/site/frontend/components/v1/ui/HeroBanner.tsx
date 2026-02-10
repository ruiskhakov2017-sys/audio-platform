'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Play } from 'lucide-react';

const HERO_IMAGE =
  'https://images.unsplash.com/photo-1493225255756-d9584f8606e9?q=80&w=1600&auto=format&fit=crop';

export default function HeroBanner() {
  return (
    <section
      className="relative min-h-[50vh] w-full overflow-hidden rounded-2xl"
      style={{
        minHeight: 420,
        backgroundColor: '#0f172a',
        backgroundImage: 'linear-gradient(135deg, #0c1222 0%, #1e3a5f 50%, #0f172a 100%)',
      }}
    >
      <div className="absolute inset-0">
        <Image
          src={HERO_IMAGE}
          alt=""
          fill
          className="object-cover opacity-30"
          priority
          sizes="100vw"
        />
        <div
          className="absolute inset-0"
          style={{
            background: 'linear-gradient(to top, #0c1222 0%, transparent 50%, rgba(12,18,34,0.6) 100%)',
          }}
          aria-hidden
        />
      </div>
      <div className="relative flex min-h-[420px] flex-col justify-end px-6 pb-12 md:px-10 md:pb-16 lg:px-14">
        <h1
          className="text-4xl font-bold leading-tight tracking-tight md:text-5xl lg:text-6xl"
          style={{ color: '#f1f5f9' }}
        >
          EroticAudio — искусство звука
          <br />
          для вашего наслаждения
        </h1>
        <p className="mt-4 max-w-2xl text-base md:text-lg" style={{ color: '#94a3b8' }}>
          Мы создаём чувственные аудио для взрослых: расслабление, вдохновение и
          пробуждение желания через мягкие, глубокие и эстетичные звуковые образы.
        </p>
        <div className="mt-8 flex flex-wrap gap-4">
          <Link
            href="/catalog"
            className="inline-flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-bold transition-all duration-300 hover:opacity-90"
            style={{
              backgroundColor: '#2563eb',
              color: '#fff',
              textDecoration: 'none',
            }}
          >
            <Play className="h-5 w-5 fill-current" />
            Перейти в каталог
          </Link>
          <Link
            href="/"
            className="rounded-xl border px-6 py-3 text-sm font-medium transition-all duration-300 hover:opacity-90"
            style={{
              borderColor: 'rgba(59, 130, 246, 0.5)',
              color: '#93c5fd',
              textDecoration: 'none',
            }}
          >
            Читать полностью
          </Link>
        </div>
      </div>
    </section>
  );
}
