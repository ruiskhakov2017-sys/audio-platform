'use client';

import Link from 'next/link';
import Image from 'next/image';
import { motion } from 'framer-motion';

const categories = [
  {
    title: 'Гипноз',
    href: '/browse?category=hypnosis',
    image: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?q=80&w=800&auto=format&fit=crop',
  },
  {
    title: 'Ролевые игры',
    href: '/browse?category=roleplay',
    image: 'https://images.unsplash.com/photo-1529333166437-7750a6dd5a70?q=80&w=800&auto=format&fit=crop',
  },
  {
    title: 'Спектакли',
    href: '/browse?category=spectacle',
    image: 'https://images.unsplash.com/photo-1514306191717-452ec7c83b33?q=80&w=800&auto=format&fit=crop',
  },
];

export default function CategoryChoices() {
  return (
    <section className="py-20">
      <div className="mx-auto w-full max-w-6xl px-6 sm:px-8 lg:px-12">
        <h2 className="mb-2 text-2xl font-black tracking-tight text-white md:text-3xl">
          Что мы предлагаем?
        </h2>
        <p className="mb-12 text-sm text-zinc-400">Жанры и форматы на любой вкус</p>

        <div className="grid gap-8 md:grid-cols-3">
          {categories.map(({ title, href, image }) => (
            <Link key={title} href={href} className="group block no-underline">
              <motion.div
                whileHover={{ scale: 1.02 }}
                className="relative aspect-[3/4] overflow-hidden rounded-[2.5rem] border border-white/10 shadow-xl transition-shadow duration-300 group-hover:border-[#00B4D8]/30 group-hover:shadow-[0_0_40px_rgba(0,180,216,0.15)]"
              >
                <Image
                  src={image}
                  alt={title}
                  fill
                  sizes="(max-width: 768px) 100vw, 33vw"
                  className="object-cover grayscale transition-all duration-500 group-hover:scale-110 group-hover:grayscale-0"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent" />
                <span className="absolute bottom-6 left-6 right-6 text-xl font-bold text-white">
                  {title}
                </span>
              </motion.div>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}
