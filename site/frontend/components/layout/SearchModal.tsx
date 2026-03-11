'use client';

import { useState, useEffect, useCallback, useMemo, useRef, type FormEvent } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { X } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import { mapRowToStory } from '@/lib/stories';
import { rankStoryMatch, storyMatchesQuery } from '@/lib/search';
import type { Story } from '@/types/story';

const DEBOUNCE_MS = 300;
const MIN_QUERY_LENGTH = 3;
const MAX_RESULTS = 8;
const PRELOAD_LIMIT = 250;

type SearchModalProps = {
  isOpen: boolean;
  onClose: () => void;
};

export function SearchModal({ isOpen, onClose }: SearchModalProps) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Story[]>([]);
  const [loading, setLoading] = useState(false);
  const storyCacheRef = useRef<Story[] | null>(null);
  const hasEnoughChars = query.trim().length >= MIN_QUERY_LENGTH;

  const runSearch = useCallback(async (q: string) => {
    const term = q.trim();
    if (term.length < MIN_QUERY_LENGTH) {
      setResults([]);
      return;
    }
    if (!supabase) {
      setResults([]);
      return;
    }

    setLoading(true);
    if (!storyCacheRef.current) {
      const { data, error } = await supabase
        .from('stories')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(PRELOAD_LIMIT);
      if (error) {
        setLoading(false);
        setResults([]);
        return;
      }
      storyCacheRef.current = (data ?? []).map((row) =>
        mapRowToStory(row as Parameters<typeof mapRowToStory>[0])
      );
    }

    const ranked = (storyCacheRef.current ?? [])
      .filter((story) => storyMatchesQuery(story, term))
      .map((story) => ({ story, score: rankStoryMatch(story, term) }))
      .sort((a, b) => b.score - a.score || b.story.id - a.story.id)
      .slice(0, MAX_RESULTS)
      .map((item) => item.story);
    setResults(ranked);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const t = setTimeout(() => {
      void runSearch(query);
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query, isOpen, runSearch]);

  const handleSelect = (story: Story) => {
    onClose();
    setQuery('');
    router.push(`/story/${story.id}`);
  };

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const term = query.trim();
    if (term.length < MIN_QUERY_LENGTH) return;
    onClose();
    setQuery('');
    router.push(`/browse?search=${encodeURIComponent(term)}`);
  };

  const statusText = useMemo(() => {
    if (!query.trim()) return 'Введите минимум 3 символа';
    if (!hasEnoughChars) return 'Введите еще символы для поиска';
    if (loading) return 'Поиск...';
    if (results.length === 0) return 'Ничего не найдено';
    return '';
  }, [query, hasEnoughChars, loading, results.length]);

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
        <form onSubmit={handleSubmit} className="flex items-center gap-2 p-3 border-b border-white/10">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск по названию, тегам и описанию..."
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
        </form>
        <div className="max-h-[60vh] overflow-y-auto">
          {statusText ? (
            <p className="p-4 text-zinc-500 text-sm text-center">{statusText}</p>
          ) : (
            <ul className="py-2">
              {results.map((story) => (
                <li key={`${story.id}-${story.slug}`}>
                  <button
                    type="button"
                    onClick={() => handleSelect(story)}
                    className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/10 transition-colors"
                  >
                    <div className="relative w-12 h-16 shrink-0 rounded overflow-hidden bg-white/10">
                      {story.coverImage ? (
                        <Image
                          src={story.coverImage}
                          alt=""
                          fill
                          className="object-cover"
                          unoptimized
                          sizes="48px"
                        />
                      ) : null}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-white truncate">{story.title}</p>
                      {story.authorName && (
                        <p className="text-sm text-zinc-400 truncate">{story.authorName}</p>
                      )}
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
