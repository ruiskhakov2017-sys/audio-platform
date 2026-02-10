'use client';

import { useRouter, usePathname } from 'next/navigation';
import { Search } from 'lucide-react';
import { useRef, useCallback, useState } from 'react';

export default function DiscoveryHeader() {
  const router = useRouter();
  const pathname = usePathname();
  const inputRef = useRef<HTMLInputElement>(null);
  const [hasFocus, setHasFocus] = useState(false);
  const [hasValue, setHasValue] = useState(false);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const q = inputRef.current?.value?.trim();
      if (!q) return;
      router.push(`/browse?q=${encodeURIComponent(q)}`);
      if (inputRef.current) inputRef.current.value = '';
      setHasValue(false);
    },
    [router]
  );

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setHasValue(!!e.target.value.trim());
  }, []);

  const showGlow = hasFocus || hasValue;

  return (
    <header className="sticky top-0 z-30 border-b border-white/5 bg-[#001d3d]/50 px-4 py-3 backdrop-blur-[40px] md:px-6">
      <form onSubmit={handleSubmit} className="mx-auto flex max-w-2xl items-center gap-3">
        <div className="relative flex-1">
          <Search
            className={`absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 transition-all duration-200 ${
              showGlow ? 'text-[#00B4D8] drop-shadow-[0_0_8px_rgba(0,180,216,0.6)]' : 'text-white/40'
            }`}
            strokeWidth={1.5}
          />
          <input
            ref={inputRef}
            type="search"
            placeholder="Начните вводить название аудио..."
            onFocus={() => setHasFocus(true)}
            onBlur={() => setHasFocus(false)}
            onChange={handleInputChange}
            className="w-full rounded-[1.25rem] border border-white/10 bg-white/5 py-2.5 pl-11 pr-4 text-sm text-white outline-none placeholder:text-white/40 transition-all duration-200 focus:border-[#00B4D8]/50 focus:ring-1 focus:ring-[#00B4D8]/30 focus:shadow-[0_0_20px_rgba(0,180,216,0.15)]"
            aria-label="Поиск"
          />
        </div>
        <button
          type="submit"
          className="shrink-0 rounded-[1.25rem] border border-white/10 bg-white/5 px-4 py-2.5 text-sm font-medium text-white/90 transition-all hover:border-[#00B4D8]/30 hover:text-[#00B4D8]"
        >
          Найти
        </button>
      </form>
    </header>
  );
}
