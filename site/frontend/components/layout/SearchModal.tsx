'use client';

import { useState, useEffect, useCallback } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { X } from 'lucide-react';
import { fetchStoriesFromApi, useDjangoApi } from '@/lib/api';
import type { Story } from '@/types/story';

const DEBOUNCE_MS = 300;

type SearchModalProps = {
  isOpen: boolean;
  onClose: () => void;
};

export function SearchModal({ isOpen, onClose }: SearchModalProps) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Story[]>([]);
  const [loading, setLoading] = useState(false);

  const runSearch = useCallback(async (q: string) => {
    if (!q.trim() || !useDjangoApi()) {
      setResults([]);
      return;
    }
    setLoading(true);
    const list = await fetchStoriesFromApi({ search: q.trim() });
    setResults(list.slice(0, 8));
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const t = setTimeout(() => runSearch(query), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query, isOpen, runSearch]);

  const handleSelect = (story: Story) => {
    onClose();
    setQuery('');
    router.push(`/story/${story.slug || story.id}`);
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center pt-24 px-4"
      role="dialog"
      aria-modal="true"
      aria-label="Поиск историй"
    >
      <button
        type="button"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Закрыть"
      />
      <div className="relative w-full max-w-xl rounded-2xl border border-white/10 bg-[#000814]/95 backdrop-blur-xl shadow-2xl overflow-hidden">
        <div className="flex items-center gap-2 p-3 border-b border-white/10">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск по названию..."
            className="flex-1 bg-transparent text-white placeholder-zinc-500 px-3 py-2 focus:outline-none focus:ring-0"
            autoFocus
            aria-label="Поиск"
          />
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-full text-zinc-400 hover:text-white hover:bg-white/10 transition-colors"
            aria-label="Закрыть"
          >
            <X className="w-5 h-5" strokeWidth={2} />
          </button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto">
          {!process.env.NEXT_PUBLIC_API_URL || !useDjangoApi() ? (
            <p className="p-4 text-zinc-500 text-sm text-center">Поиск доступен при подключённом API.</p>
          ) : loading ? (
            <p className="p-4 text-zinc-500 text-sm text-center">Поиск...</p>
          ) : results.length === 0 && query.trim() ? (
            <p className="p-4 text-zinc-500 text-sm text-center">Ничего не найдено.</p>
          ) : (
            <ul className="py-2">
              {results.map((story) => (
                <li key={story.id}>
                  <button
                    type="button"
                    onClick={() => handleSelect(story)}
                    className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/10 transition-colors"
                  >
                    <div className="relative w-12 h-16 shrink-0 rounded overflow-hidden bg-white/10">
                      {story.coverImage && (
                        <Image
                          src={story.coverImage}
                          alt=""
                          fill
                          className="object-cover"
                          unoptimized
                          sizes="48px"
                        />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-white truncate">{story.title}</p>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
