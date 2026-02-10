'use client';

import Link from 'next/link';
import Image from 'next/image';
import NeonButton from '@/components/ui/NeonButton';

const customImage = 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=800&auto=format&fit=crop';

export default function CustomExperience() {
  return (
    <section className="py-20">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-12 px-6 sm:px-8 lg:flex-row lg:items-center lg:gap-16 lg:px-12">
        <div className="flex-1">
          <h2 className="text-2xl font-black tracking-tight text-white md:text-3xl">
            Озвучка на заказ
          </h2>
          <p className="mt-4 text-zinc-400">
            Ваши фантазии — наш сценарий. Персональное аудио с нужной атмосферой, голосом и сюжетом.
          </p>
          <Link href="/browse" className="mt-6 inline-block">
            <NeonButton variant="primary">Заказать озвучку</NeonButton>
          </Link>
        </div>
        <div className="relative aspect-[4/5] w-full max-w-md overflow-hidden rounded-[2.5rem] lg:max-w-sm">
          <Image
            src={customImage}
            alt=""
            fill
            sizes="(max-width: 1024px) 100vw, 400px"
            className="object-cover"
          />
          <div
            className="absolute inset-0"
            style={{
              background: 'linear-gradient(to right, transparent 50%, #000814 100%), linear-gradient(to bottom, transparent 70%, #000814 100%)',
            }}
          />
        </div>
      </div>
    </section>
  );
}
