'use client';

import { Search } from 'lucide-react';

type FilterSidebarProps = {
  searchQuery: string;
  setSearchQuery: (v: string) => void;
  activeGenre: string;
  setActiveGenre: (v: string) => void;
  genres: string[];
  allTags: string[];
  selectedTag: string | null;
  setSelectedTag: (v: string | null) => void;
  onReset: () => void;
};

const ALL_GENRES = 'All';

function genreLabel(genre: string): string {
  if (genre === ALL_GENRES) return 'Все';
  return genre.charAt(0).toUpperCase() + genre.slice(1).toLowerCase();
}

function tagLabel(tag: string): string {
  return tag.charAt(0).toUpperCase() + tag.slice(1).toLowerCase();
}

const tagBase =
  'py-1.5 px-2.5 rounded-lg text-sm font-medium transition-colors border min-w-0';

export function FilterSidebar({
  searchQuery,
  setSearchQuery,
  activeGenre,
  setActiveGenre,
  genres,
  allTags,
  selectedTag,
  setSelectedTag,
  onReset,
}: FilterSidebarProps) {
  const handleTagClick = (tag: string) => {
    setSelectedTag(selectedTag === tag ? null : tag);
  };

  return (
    <aside className="w-72 min-w-[18rem] flex-shrink-0 sticky top-24 self-start max-h-[calc(100vh-6rem)] overflow-y-auto hidden lg:block">
      <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <h3 className="text-sm font-semibold text-white mb-3">Фильтры</h3>

        <div className="mb-4">
          <div className="relative w-full min-w-0">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 pointer-events-none" />
            <input
              type="text"
              placeholder="Поиск..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full min-w-0 pl-9 pr-3 py-2 text-sm bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30 transition-colors"
              aria-label="Поиск"
            />
          </div>
        </div>

        <div className="mb-4">
          <h4 className="text-sm font-medium text-zinc-400 mb-2">Жанры</h4>
          <div className="flex flex-wrap gap-2">
            {genres.map((genre) => {
              const isActive = activeGenre === genre;
              return (
                <button
                  key={genre}
                  type="button"
                  onClick={() => setActiveGenre(genre)}
                  className={`${tagBase} ${
                    isActive
                      ? 'bg-white/10 border-white/20 text-white'
                      : 'bg-transparent border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200'
                  }`}
                >
                  {genreLabel(genre)}
                </button>
              );
            })}
          </div>
        </div>

        <div className="mb-4">
          <h4 className="text-sm font-medium text-zinc-400 mb-2">Теги</h4>
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => {
              const isActive = selectedTag === tag;
              return (
                <button
                  key={tag}
                  type="button"
                  onClick={() => handleTagClick(tag)}
                  className={`${tagBase} ${
                    isActive
                      ? 'bg-white/10 border-white/20 text-white'
                      : 'bg-transparent border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200'
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
          className="w-full py-2 text-sm text-[#00B4D8] hover:text-cyan-300 transition-colors"
        >
          Сбросить фильтры
        </button>
      </div>
    </aside>
  );
}
