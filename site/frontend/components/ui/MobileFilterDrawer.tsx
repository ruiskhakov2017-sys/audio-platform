'use client';

import type { AccessFilter } from '@/app/browse/page';

const ALL_GENRES = 'All';

function genreLabel(genre: string): string {
  if (genre === ALL_GENRES) return 'Все';
  return genre.charAt(0).toUpperCase() + genre.slice(1).toLowerCase();
}

function tagLabel(tag: string): string {
  return tag.charAt(0).toUpperCase() + tag.slice(1).toLowerCase();
}

const tagBase = 'py-2 px-3 rounded-full text-sm font-medium transition-all';

type MobileFilterDrawerProps = {
  isOpen: boolean;
  onClose: () => void;
  accessFilter: AccessFilter;
  setAccessFilter: (v: AccessFilter) => void;
  activeGenre: string;
  setActiveGenre: (v: string) => void;
  genres: string[];
  allTags: string[];
  selectedTag: string | null;
  setSelectedTag: (v: string | null) => void;
  onReset: () => void;
  onGenreSelect?: () => void;
};

export function MobileFilterDrawer({
  isOpen,
  onClose,
  accessFilter,
  setAccessFilter,
  activeGenre,
  setActiveGenre,
  genres,
  allTags,
  selectedTag,
  setSelectedTag,
  onReset,
  onGenreSelect,
}: MobileFilterDrawerProps) {
  if (!isOpen) return null;

  const handleTagClick = (tag: string) => {
    setSelectedTag(selectedTag === tag ? null : tag);
  };

  const handleGenreClick = (genre: string) => {
    setActiveGenre(genre);
    if (genre !== ALL_GENRES) onGenreSelect?.();
  };

  return (
    <div
      className="fixed inset-0 z-50 lg:hidden"
      role="dialog"
      aria-modal="true"
      aria-labelledby="mobile-filter-title"
    >
      <button
        type="button"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Закрыть"
      />
      <div className="absolute bottom-0 left-0 right-0 flex max-h-[70vh] flex-col rounded-t-2xl border-t border-white/10 bg-zinc-900">
        <div className="flex shrink-0 items-center justify-between border-b border-white/5 p-4">
          <h2 id="mobile-filter-title" className="font-heading text-lg font-bold text-white">
            Фильтры и жанры
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-2 text-zinc-500 hover:text-white transition-colors rounded-full hover:bg-white/10"
            aria-label="Закрыть"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-2"
          style={{ WebkitOverflowScrolling: 'touch' }}
        >
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-zinc-400 mb-2">Доступ</h3>
            <div className="flex flex-wrap gap-2 mb-4">
              <button
                type="button"
                onClick={() => setAccessFilter(accessFilter === 'free' ? 'all' : 'free')}
                className={`${tagBase} ${
                  accessFilter === 'free' ? 'bg-[#00B4D8] text-white' : 'bg-white/5 border border-white/10 text-zinc-300 hover:border-[#00B4D8]/50'
                }`}
              >
                🆓 Бесплатно
              </button>
              <button
                type="button"
                onClick={() => setAccessFilter(accessFilter === 'premium' ? 'all' : 'premium')}
                className={`${tagBase} ${
                  accessFilter === 'premium' ? 'bg-[#00B4D8] text-white' : 'bg-white/5 border border-white/10 text-zinc-300 hover:border-[#00B4D8]/50'
                }`}
              >
                💎 Премиум
              </button>
            </div>
          </div>
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-zinc-400 mb-2">Жанры</h3>
            <div className="flex flex-wrap gap-2">
              {genres.map((genre) => {
                const isActive = activeGenre === genre;
                return (
                  <button
                    key={genre}
                    type="button"
                    onClick={() => handleGenreClick(genre)}
                    className={`py-2 px-3 rounded-full text-sm font-medium transition-all ${
                      isActive
                        ? 'bg-[#00B4D8] text-white'
                        : 'bg-white/5 border border-white/10 text-zinc-300 hover:border-[#00B4D8]/50 hover:text-white'
                    }`}
                  >
                    {genreLabel(genre)}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mb-6">
            <h3 className="text-sm font-semibold text-zinc-400 mb-2">Популярные теги</h3>
            <div className="flex flex-wrap gap-2">
              {allTags.map((tag) => {
                const isActive = selectedTag === tag;
                return (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => handleTagClick(tag)}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors border ${
                      isActive
                        ? 'bg-cyan-500/10 border-cyan-500 text-cyan-400'
                        : 'bg-transparent border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-white'
                    }`}
                  >
                    {tagLabel(tag)}
                  </button>
                );
              })}
            </div>
          </div>

          <button
            type="button"
            onClick={onReset}
            className="w-full py-2.5 text-sm text-[#00B4D8] hover:text-cyan-300 transition-colors"
          >
            Сбросить фильтры
          </button>
        </div>

        <div className="shrink-0 border-t border-white/5 p-4 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="w-full py-4 rounded-full bg-[#00B4D8] text-white font-semibold hover:bg-[#00B4D8]/90 transition-colors"
          >
            Показать результаты
          </button>
        </div>
      </div>
    </div>
  );
}
