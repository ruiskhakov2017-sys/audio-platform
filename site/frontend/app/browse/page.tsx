'use client';

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { SlidersHorizontal } from 'lucide-react';
import { Header } from '@/components/layout/Header';
import { FilterSidebar } from '@/components/browse/FilterSidebar';
import { MobileFilterDrawer } from '@/components/ui/MobileFilterDrawer';
import { StoryTile } from '@/components/browse/StoryTile';
import { StoryTileSkeleton } from '@/components/browse/StoryTileSkeleton';
import { fetchStoriesFromApi, useDjangoApi } from '@/lib/api';
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
    list = list.filter((s) => s.title.toLowerCase().includes(q));
  }

  return sortStories(list, activeSort);
}

const GENRE_PARAM = 'genre';
const SEARCH_PARAM = 'search';
const TAG_PARAM = 'tag';

export default function BrowsePage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [activeGenre, setActiveGenre] = useState<string>(() =>
    searchParams.get(GENRE_PARAM) || ALL_GENRES
  );
  const [selectedTag, setSelectedTag] = useState<string | null>(() =>
    searchParams.get(TAG_PARAM) || null
  );
  const [searchQuery, setSearchQuery] = useState(() =>
    searchParams.get(SEARCH_PARAM) || ''
  );
  const [activeSort, setActiveSort] = useState<SortKey>('popular');
  const [visibleCount, setVisibleCount] = useState(20);
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [list, setList] = useState<Story[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams();
    if (searchQuery.trim()) params.set(SEARCH_PARAM, searchQuery.trim());
    if (activeGenre !== ALL_GENRES) params.set(GENRE_PARAM, activeGenre);
    if (selectedTag) params.set(TAG_PARAM, selectedTag);
    const qs = params.toString();
    const want = qs ? `?${qs}` : '';
    if (typeof window !== 'undefined' && window.location.search !== want) {
      router.replace(want ? `/browse?${qs}` : '/browse', { scroll: false });
    }
  }, [activeGenre, selectedTag, searchQuery, router]);

  useEffect(() => {
    setLoadError(null);
    if (useDjangoApi()) {
      fetchStoriesFromApi({
        search: searchQuery.trim() || undefined,
        genre: activeGenre !== ALL_GENRES ? activeGenre : undefined,
      })
        .then((data) => setList(Array.isArray(data) ? data : []))
        .finally(() => setLoading(false));
      return;
    }
    fetch('/api/stories', { cache: 'no-store' })
      .then((res) => {
        if (res.ok) return res.json().then((data) => setList(Array.isArray(data) ? data : []));
        if (res.status === 503) {
          setLoadError('В Vercel задайте NEXT_PUBLIC_SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY, затем Redeploy. Или добавьте записи в таблицу stories в Supabase.');
        }
        setList([]);
      })
      .catch(() => {
        setLoadError('Ошибка загрузки каталога.');
        setList([]);
      })
      .finally(() => setLoading(false));
  }, [searchQuery, activeGenre]);

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

  const displayedStories = useMemo(
    () =>
      filteredStories.slice(0, visibleCount).map((s) => ({
        id: s.id,
        title: s.title,
        coverImage: s.coverImage,
        category: s.tags[0] || 'Аудио',
        price: s.isPremium ? 190 : undefined,
        isPremium: s.isPremium,
        story: s,
      })),
    [filteredStories, visibleCount]
  );

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
                  Найдено {filteredStories.length} записей
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

              {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <StoryTileSkeleton key={i} />
                  ))}
                </div>
              ) : filteredStories.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-24 text-center">
                  {loadError ? (
                    <p className="text-xl text-amber-400 mb-2 max-w-xl">{loadError}</p>
                  ) : list.length === 0 ? (
                    <p className="text-xl text-zinc-400 mb-2">
                      Каталог пуст. Загрузите рассказы в админке или проверьте переменные Supabase в Vercel.
                    </p>
                  ) : (
                    <>
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
                    </>
                  )}
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                    {displayedStories.map((item) => (
                      <StoryTile key={item.id} {...item} />
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
