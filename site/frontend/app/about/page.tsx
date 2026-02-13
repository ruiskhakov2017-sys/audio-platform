'use client';

import { Header } from '@/components/layout/Header';

export default function AboutPage() {
  return (
    <div className="min-h-screen">
      <Header />
      <main className="relative z-10 pt-28 pb-20 px-4 max-w-3xl mx-auto">
        <h1 className="font-heading text-3xl font-bold text-white mb-6">О нас</h1>
        <div className="prose prose-invert prose-zinc max-w-none text-zinc-300 space-y-4">
          <p>
            Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
            Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.
          </p>
          <p>
            Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.
            Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
          </p>
          <p>
            Наша миссия — предоставлять качественный аудиоконтент и создавать комфортную среду для слушателей.
            Мы ценим обратную связь и всегда открыты к диалогу.
          </p>
        </div>
      </main>
    </div>
  );
}
