'use client';

import { Search } from 'lucide-react';
import { GlassCard } from '../ui/GlassCard';

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
    <aside className="lg:col-span-2 flex-shrink-0 sticky top-24 h-[calc(100vh-8rem)] overflow-y-auto pr-4 hidden lg:block">
      <GlassCard className="p-6 bg-[#000814]/80 border border-white/5">
        <h3 className="text-xl font-bold text-white mb-6">Фильтры</h3>

                <div className="mb-6">
                    <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400 shrink-0" />
                        <input
                            type="text"
              placeholder="Поиск по названию или автору..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-3 bg-white/5 border border-white/10 rounded-xl text-base text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30 transition-colors"
              aria-label="Поиск"
                        />
                    </div>
                </div>

                <div className="mb-6">
          <h4 className="text-xl font-bold text-white mb-3">Жанры</h4>
          <div className="flex flex-wrap gap-2">
            {genres.map((genre) => {
              const isActive = activeGenre === genre;
              return (
                <button
                  key={genre}
                  type="button"
                  onClick={() => setActiveGenre(genre)}
                  className={`py-1.5 px-2.5 rounded-full text-xs font-medium transition-all ${
                    isActive
                      ? 'bg-[#00B4D8] text-white shadow-[0_0_12px_rgba(0,180,216,0.4)]'
                      : 'bg-white/5 border border-white/10 text-zinc-300 hover:border-[#00B4D8]/50 hover:text-white'
                  }`}
                >
                  {genreLabel(genre)}
                </button>
              );
            })}
                    </div>
                </div>

        <div className="mt-8">
          <h3 className="text-white font-heading text-lg mb-4">
            Популярные теги
          </h3>
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
          className="w-full py-3 mt-6 text-base text-[#00B4D8] hover:text-cyan-300 transition-colors"
        >
                    Сбросить фильтры
                </button>
            </GlassCard>
        </aside>
    );
}
