'use client';

import { useRouter, usePathname } from 'next/navigation';
import { Search } from 'lucide-react';
import { useRef, useCallback, useState } from 'react';
import { motion } from 'framer-motion';

export default function DiscoveryHeader() {
  const router = useRouter();
  const pathname = usePathname();
  const inputRef = useRef<HTMLInputElement>(null);
  const [hasFocus, setHasFocus] = useState(false);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const q = inputRef.current?.value?.trim();
      if (!q) return;
      router.push(`/browse?q=${encodeURIComponent(q)}`);
      if (inputRef.current) inputRef.current.value = '';
    },
    [router]
  );

  return (
    <header
      className="sticky top-0 z-30 border-b border-white/5 px-4 py-3 md:px-6"
      style={{
        backgroundColor: 'rgba(0, 29, 61, 0.5)',
        backdropFilter: 'blur(40px)',
        WebkitBackdropFilter: 'blur(40px)',
      }}
    >
      <form onSubmit={handleSubmit} className="mx-auto flex max-w-2xl items-center gap-3">
        <div className="relative flex-1">
          <Search
            className={`absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 transition-colors ${
              hasFocus ? 'text-[#00B4D8] drop-shadow-[0_0_8px_rgba(0,180,216,0.6)]' : 'text-zinc-500'
            }`}
            strokeWidth={1.5}
          />
          <input
            ref={inputRef}
            type="search"
            placeholder="Начните вводить название аудио..."
            onFocus={() => setHasFocus(true)}
            onBlur={() => setHasFocus(false)}
            className="w-full rounded-[2.5rem] border border-white/5 py-2.5 pl-11 pr-4 text-sm text-white outline-none transition-all placeholder:text-zinc-500 focus:border-[#00B4D8]/50 focus:ring-2 focus:ring-[#00B4D8]/20"
            style={{ backgroundColor: 'rgba(255,255,255,0.03)' }}
            aria-label="Поиск"
          />
        </div>
        <button
          type="submit"
          className="shrink-0 rounded-[2.5rem] px-4 py-2.5 text-sm font-medium text-white transition-all hover:bg-[#00B4D8]/20"
          style={{
            backgroundColor: 'rgba(0, 180, 216, 0.15)',
            border: '1px solid rgba(0, 180, 216, 0.3)',
          }}
        >
          Найти
        </button>
      </form>
    </header>
  );
}
