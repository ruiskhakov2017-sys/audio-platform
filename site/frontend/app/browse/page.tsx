'use client';

import { useEffect, useMemo, useState } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { Header } from '@/components/layout/Header';
import { FilterSidebar } from '@/components/browse/FilterSidebar';
import { MobileFilterDrawer } from '@/components/ui/MobileFilterDrawer';
import { StoryTile } from '@/components/browse/StoryTile';
import { supabase } from '@/lib/supabase';
import { mapRowToStory } from '@/lib/stories';
import type { Story } from '@/types/story';

const SORT_OPTIONS = [
  { key: 'popular', label: 'Популярное', icon: '🔥' },
  { key: 'new', label: 'Новинки', icon: '🆕' },
  { key: 'free', label: 'Бесплатно', icon: '🎁' },
  { key: 'editor', label: 'Выбор редакции', icon: '💎' },
] as const;

type SortKey = (typeof SORT_OPTIONS)[number]['key'];

function sortStories(list: Story[], sortKey: SortKey): Story[] {
  const arr = [...list];
  switch (sortKey) {
    case 'popular':
    case 'editor':
      return arr.sort((a, b) => b.id - a.id);
    case 'new':
      return arr.sort((a, b) => b.id - a.id);
    case 'free':
      return arr.filter((s) => !s.isPremium).sort((a, b) => b.id - a.id);
    default:
      return arr;
  }
}

const ALL_GENRES = 'All';

function filterAndSortStories(
  allStories: Story[],
  activeGenre: string,
  selectedTag: string | null,
  searchQuery: string,
  activeSort: SortKey
): Story[] {
  let list = allStories;

  if (activeGenre !== ALL_GENRES) {
    list = list.filter((s) => s.tags.includes(activeGenre));
  }

  if (selectedTag) {
    list = list.filter((s) => s.tags.includes(selectedTag));
  }

  const q = searchQuery.trim().toLowerCase();
  if (q) {
    list = list.filter(
      (s) =>
        s.title.toLowerCase().includes(q) || s.authorName.toLowerCase().includes(q)
    );
  }

  return sortStories(list, activeSort);
}

export default function BrowsePage() {
  const [activeGenre, setActiveGenre] = useState<string>(ALL_GENRES);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSort, setActiveSort] = useState<SortKey>('popular');
  const [visibleCount, setVisibleCount] = useState(20);
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [list, setList] = useState<Story[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!supabase) {
      setLoading(false);
      return;
    }
    supabase
      .from('stories')
      .select('*')
      .order('created_at', { ascending: false })
      .then(({ data, error }) => {
        setLoading(false);
        if (error) return;
        setList((data ?? []).map(mapRowToStory));
      });
  }, []);

  const genres = useMemo(() => {
    const set = new Set<string>();
    list.forEach((s) => s.tags.forEach((t) => set.add(t)));
    return [ALL_GENRES, ...Array.from(set).sort()];
  }, [list.length]);

  const allTags = useMemo(() => {
    const set = new Set<string>();
    list.forEach((s) => s.tags.forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [list.length]);

  const filteredStories = useMemo(
    () =>
      filterAndSortStories(list, activeGenre, selectedTag, searchQuery, activeSort),
    [list, activeGenre, selectedTag, searchQuery, activeSort]
  );

  useEffect(() => {
    setVisibleCount(20);
  }, [activeGenre, selectedTag, searchQuery]);

  const handleResetFilters = () => {
    setActiveGenre(ALL_GENRES);
    setSelectedTag(null);
    setSearchQuery('');
  };

  const hasActiveFilters =
    activeGenre !== ALL_GENRES || selectedTag !== null;
  const activeFiltersLabel = [
    activeGenre !== ALL_GENRES && (activeGenre.charAt(0).toUpperCase() + activeGenre.slice(1).toLowerCase()),
    selectedTag && (selectedTag.charAt(0).toUpperCase() + selectedTag.slice(1).toLowerCase()),
  ]
    .filter(Boolean)
    .join(' + ');

  const storiesForGrid = useMemo(
    () =>
      filteredStories.map((story) => ({
        id: story.id,
        title: story.title,
        coverImage: story.coverImage,
        category: story.tags[0] || 'Аудио',
        price: story.isPremium ? 190 : undefined,
        isPremium: story.isPremium,
      })),
    [filteredStories]
  );

  const displayedStories = storiesForGrid.slice(0, visibleCount);

  return (
    <div className="min-h-screen">
      <Header />

      <main className="pt-24 pb-12">
        <div className="max-w-[95%] mx-auto px-6">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
            <FilterSidebar
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              activeGenre={activeGenre}
              setActiveGenre={setActiveGenre}
              genres={genres}
              allTags={allTags}
              selectedTag={selectedTag}
              setSelectedTag={setSelectedTag}
              onReset={handleResetFilters}
            />

            <div className="lg:col-span-10 min-w-0">
              <div className="mb-6">
                <h1 className="font-heading text-3xl font-bold text-white mb-2">
                  Каталог аудио
                </h1>
                {hasActiveFilters && (
                  <p className="text-sm text-cyan-400/90 mb-1">
                    Активные фильтры: [{activeFiltersLabel}]
                  </p>
                )}
                <p className="text-zinc-400">
                  Найдено {storiesForGrid.length} записей
                </p>
              </div>

              <button
                type="button"
                onClick={() => setIsFilterOpen(true)}
                className="lg:hidden flex items-center gap-2 px-4 py-2 bg-zinc-900 border border-zinc-800 rounded-full text-sm text-white mb-6"
              >
                <SlidersHorizontal size={16} aria-hidden />
                <span>Фильтры и Жанры</span>
              </button>

              <div className="flex flex-wrap gap-2 mb-8">
                {SORT_OPTIONS.map((opt) => {
                  const isActive = activeSort === opt.key;
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => setActiveSort(opt.key)}
                      className={`inline-flex items-center gap-2 py-2.5 px-4 rounded-full text-sm font-medium transition-all ${
                        isActive
                          ? 'bg-[#00B4D8] text-white shadow-[0_0_16px_rgba(0,180,216,0.5)]'
                          : 'bg-white/5 border border-white/10 text-zinc-400 hover:border-[#00B4D8]/40 hover:text-zinc-200'
                      }`}
                    >
                      <span aria-hidden>{opt.icon}</span>
                      {opt.label}
                    </button>
                  );
                })}
              </div>

              <div className="text-zinc-500 text-sm mb-6">
                Показано {Math.min(visibleCount, filteredStories.length)} из {filteredStories.length} историй
              </div>

              {storiesForGrid.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-24 text-center">
                  <p className="text-xl text-zinc-400 mb-2">
                    Ничего не найдено, попробуйте другой запрос
                  </p>
                  <button
                    type="button"
                    onClick={handleResetFilters}
                    className="text-[#00B4D8] hover:text-cyan-300 transition-colors"
                  >
                    Сбросить фильтры
                  </button>
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                    {displayedStories.map((story) => (
                      <StoryTile key={story.id} {...story} />
                    ))}
                  </div>
                  {visibleCount < filteredStories.length && (
                    <div className="mt-12 flex justify-center">
                      <button
                        type="button"
                        onClick={() => setVisibleCount((prev) => prev + 20)}
                        className="w-full max-w-md py-4 text-lg font-medium text-white rounded-full border border-white/20 hover:bg-white/10 transition"
                      >
                        Показать еще ({filteredStories.length - visibleCount})
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      </main>

      <MobileFilterDrawer
        isOpen={isFilterOpen}
        onClose={() => setIsFilterOpen(false)}
        activeGenre={activeGenre}
        setActiveGenre={setActiveGenre}
        genres={genres}
        allTags={allTags}
        selectedTag={selectedTag}
        setSelectedTag={setSelectedTag}
        onReset={handleResetFilters}
      />
    </div>
  );
}
