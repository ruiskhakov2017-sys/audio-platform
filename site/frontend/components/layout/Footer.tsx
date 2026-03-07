'use client';

import Link from 'next/link';

export function Footer() {
  return (
    <footer className="relative z-10 border-t border-white/10 bg-black/40 backdrop-blur-sm mt-auto">
      <div className="max-w-[1800px] mx-auto px-6 py-4">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="font-heading text-xl text-white">
            Dirty Secrets
          </div>
          <nav className="flex flex-wrap items-center justify-center gap-6 text-sm">
            <Link href="/about" className="text-zinc-400 hover:text-[#00B4D8] transition-colors">
              О нас
            </Link>
            <Link href="/contacts" className="text-zinc-400 hover:text-[#00B4D8] transition-colors">
              Контакты
            </Link>
            <Link href="/terms" className="text-zinc-400 hover:text-[#00B4D8] transition-colors">
              Пользовательское соглашение
            </Link>
            <Link href="/privacy" className="text-zinc-400 hover:text-[#00B4D8] transition-colors">
              Privacy Policy
            </Link>
          </nav>
        </div>
        <p className="mt-3 text-center text-zinc-500 text-xs">
          © 2026 Dirty Secrets. Все права защищены. 18+
        </p>
      </div>
    </footer>
  );
}
