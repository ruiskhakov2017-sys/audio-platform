'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Search, User, Heart } from 'lucide-react';

export default function Header() {
  const pathname = usePathname();

  const navItems = [
    { href: '/browse', label: 'КАТАЛОГ' },
    { href: '/browse?premium=1', label: 'ПРЕМИУМ' },
    { href: '/#about', label: 'О НАС' },
  ] as const;

  return (
    <header className="fixed left-0 right-0 top-0 z-50 border-b border-white/5 bg-[#000814]/80 backdrop-blur-xl">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4 sm:px-8 lg:px-12">
        <Link
          href="/"
          className="shrink-0 text-xl font-bold tracking-tight no-underline transition-opacity hover:opacity-90"
          aria-label="На главную"
        >
          <span className="text-white">Аудио</span>
          <span className="text-[#00B4D8]">Портал</span>
        </Link>

        <nav className="absolute left-1/2 flex -translate-x-1/2 shrink-0 gap-8 sm:gap-10" aria-label="Главное меню">
          {navItems.map(({ href, label }) => {
            const isActive = href === '/browse' ? pathname === '/browse' : pathname === '/';
            return (
              <Link
                key={label}
                href={href}
                className={`text-[10px] font-semibold uppercase tracking-[0.35em] no-underline transition-colors duration-200 hover:text-[#00B4D8] ${
                  isActive ? 'text-[#00B4D8]' : 'text-white/85'
                }`}
              >
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-5 sm:gap-6">
          <Link
            href="/browse"
            className="flex shrink-0 items-center justify-center text-zinc-400 transition-colors duration-200 hover:text-[#00B4D8]"
            aria-label="Поиск"
          >
            <Search className="h-5 w-5 shrink-0" strokeWidth={1.5} />
          </Link>
          <Link
            href="/library"
            className="flex shrink-0 items-center justify-center text-zinc-400 transition-colors duration-200 hover:text-[#00B4D8]"
            aria-label="Моя коллекция"
          >
            <Heart className="h-5 w-5 shrink-0" strokeWidth={1.5} />
          </Link>
          <Link
            href="/library"
            className="flex shrink-0 items-center justify-center text-zinc-400 transition-colors duration-200 hover:text-[#00B4D8]"
            aria-label="Профиль"
          >
            <User className="h-5 w-5 shrink-0" strokeWidth={1.5} />
          </Link>
        </div>
      </div>
    </header>
  );
}
