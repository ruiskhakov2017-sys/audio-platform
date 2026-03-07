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
import { getStoriesForBrowse, getStoriesForCatalog, type BrowseFilter } from '@/app/actions/catalog';
import { getDisplayTags } from '@/lib/stories';
import { useAuthStore } from '@/store/authStore';
import { useFavoritesStore } from '@/store/favoritesStore';
import type { Story } from '@/types/story';

const SORT_OPTIONS = [
  { key: 'popular', label: 'Популярное', icon: '🔥' },
  { key: 'new', label: 'Новинки', icon: '✨' },
  { key: 'editor', label: 'Выбор редакции', icon: '💎' },
  { key: 'trending', label: 'В тренде', icon: '📈' },
  { key: 'premium', label: 'Премиум', icon: '👑' },
  { key: 'free', label: 'Бесплатно', icon: '🎁' },
  { key: 'mine', label: 'Мои', icon: '❤️' },
] as const;

/** Путь к картинке жанра: есть готовые в public, остальные — заглушка */
function genreImagePath(genre: string): string {
  const map: Record<string, string> = {
    '18 лет': '18-plus',
    'Группа': 'group',
    'Подчинение': 'submission',
    'Измена': 'betrayal',
    'Фантазии': 'fantasies',
    'Служебный роман': 'office',
  };
  const slug = map[genre] || '18-plus';
  return `/images/genres/${slug}.jpg`;
}

type SortKey = (typeof SORT_OPTIONS)[number]['key'];

const ALL_GENRES = 'All';

/** Фиксированный список жанров для сайдбара. При клике в фильтр уходит именно это название (совпадение с полем genre/genres в Supabase). */
const GENRES_LIST: string[] = [
  '18 лет',
  'А в попку лучше',
  'Би',
  'В первый раз',
  'Ваши рассказы',
  'Гетеросексуалы',
  'Группа',
  'Драма',
  'Жена шлюшка',
  'Зрелый возраст',
  'Измена',
  'Инцест',
  'Классика',
  'Кунилингус',
  'Куколд',
  'Лесби',
  'Мастурбация',
  'Минет',
  'Наблюдатели',
  'Непорно',
  'Перевод',
  'Пикап-истории',
  'По принуждению',
  'Подчинение',
  'Романтика',
  'Свингеры',
  'Секс-туризм',
  'Сексвайф',
  'Случайный роман',
  'Служебный роман',
  'Студенты',
  'Фантазии',
  'Фантастика',
];

function sortStoriesClient(list: Story[], sortKey: SortKey): Story[] {
  const arr = [...list];
  switch (sortKey) {
    case 'popular':
    case 'editor':
    case 'new':
    case 'trending':
    case 'mine':
      return arr.sort((a, b) => b.id - a.id);
    case 'free':
      return arr.filter((s) => !s.isPremium).sort((a, b) => b.id - a.id);
    case 'premium':
      return arr.filter((s) => s.isPremium).sort((a, b) => b.id - a.id);
    default:
      return arr;
  }
}

export type AccessFilter = 'all' | 'free' | 'premium';

function filterStories(
  allStories: Story[],
  activeGenre: string,
  selectedTag: string | null,
  searchQuery: string,
  accessFilter: AccessFilter
): Story[] {
  let list = allStories;
  if (accessFilter === 'free') list = list.filter((s) => !s.isPremium);
  if (accessFilter === 'premium') list = list.filter((s) => s.isPremium);
  if (activeGenre !== ALL_GENRES) {
    list = list.filter((s) => getDisplayTags(s).includes(activeGenre));
  }
  if (selectedTag) {
    list = list.filter((s) => getDisplayTags(s).includes(selectedTag));
  }
  const q = searchQuery.trim().toLowerCase();
  if (q) {
    list = list.filter((s) => s.title.toLowerCase().includes(q));
  }
  return list;
}

const GENRE_PARAM = 'genre';
const SEARCH_PARAM = 'search';
const TAG_PARAM = 'tag';

type ViewMode = 'genres' | 'list';

export default function BrowsePage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [viewMode, setViewMode] = useState<ViewMode>('genres');
  const [accessFilter, setAccessFilter] = useState<AccessFilter>('all');
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
  const [showMineLoginModal, setShowMineLoginModal] = useState(false);

  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const likedIds = useFavoritesStore((s) => s.likedIds);

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
    setLoading(true);
    if (activeSort === 'mine') {
      if (!isAuthenticated) {
        setList([]);
        setLoading(false);
        return;
      }
      if (useDjangoApi()) {
        fetchStoriesFromApi()
          .then((data) => setList(Array.isArray(data) ? data : []))
          .finally(() => setLoading(false));
        return;
      }
      getStoriesForCatalog()
        .then((data) => setList(data ?? []))
        .catch(() => setList([]))
        .finally(() => setLoading(false));
      return;
    }
    if (useDjangoApi()) {
      fetchStoriesFromApi({
        search: searchQuery.trim() || undefined,
        genre: activeGenre !== ALL_GENRES ? activeGenre : undefined,
      })
        .then((data) => setList(Array.isArray(data) ? data : []))
        .finally(() => setLoading(false));
      return;
    }
    getStoriesForBrowse(activeSort as BrowseFilter)
      .then((data) => setList(data ?? []))
      .catch(() => {
        setLoadError('Ошибка загрузки каталога.');
        setList([]);
      })
      .finally(() => setLoading(false));
  }, [activeSort, searchQuery, activeGenre, isAuthenticated]);

  const genres = [ALL_GENRES, ...GENRES_LIST];

  const allTags = useMemo(() => {
    const set = new Set<string>();
    list.forEach((s) => getDisplayTags(s).forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [list.length]);

  const filteredStories = useMemo(() => {
    let filtered = filterStories(list, activeGenre, selectedTag, searchQuery, accessFilter);
    if (activeSort === 'mine') {
      filtered = filtered.filter((s) => likedIds.includes(String(s.id)));
    }
    if (useDjangoApi()) return sortStoriesClient(filtered, activeSort);
    return filtered;
  }, [list, activeGenre, selectedTag, searchQuery, accessFilter, activeSort, likedIds]);

  useEffect(() => {
    setVisibleCount(20);
  }, [activeGenre, selectedTag, searchQuery, activeSort, accessFilter]);

  const handleResetFilters = () => {
    setActiveGenre(ALL_GENRES);
    setSelectedTag(null);
    setSearchQuery('');
    setAccessFilter('all');
  };

  const handleBackToGenres = () => {
    setActiveGenre(ALL_GENRES);
    setViewMode('genres');
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
        category: getDisplayTags(s)[0] || 'Аудио',
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
        <div className="max-w-[1440px] mx-auto px-4">
          <div className="flex flex-col lg:flex-row gap-6 lg:gap-8 items-start">
            <FilterSidebar
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              accessFilter={accessFilter}
              setAccessFilter={setAccessFilter}
              activeGenre={activeGenre}
              setActiveGenre={setActiveGenre}
              genres={genres}
              allTags={allTags}
              selectedTag={selectedTag}
              setSelectedTag={setSelectedTag}
              onReset={handleResetFilters}
              onGenreSelect={() => setViewMode('list')}
            />

            <div className="flex-1 min-w-0 w-full">
              <div className="mb-4">
                <h1 className="font-heading text-2xl font-bold text-white mb-1">
                  Каталог аудио
                </h1>
                {hasActiveFilters && (
                  <p className="text-sm text-cyan-400/90 mb-0.5">
                    Активные фильтры: [{activeFiltersLabel}]
                  </p>
                )}
                <p className="text-zinc-400 text-sm">
                  Найдено {filteredStories.length} записей
                </p>
              </div>

              <button
                type="button"
                onClick={() => setIsFilterOpen(true)}
                className="lg:hidden flex items-center gap-2 px-4 py-2 bg-zinc-900 border border-zinc-800 rounded-full text-sm text-white mb-4"
              >
                <SlidersHorizontal size={16} aria-hidden />
                <span>Фильтры и Жанры</span>
              </button>

              <div className="flex overflow-x-auto gap-2 pb-2 mb-4 lg:grid lg:grid-cols-8 lg:overflow-visible lg:pb-0 lg:gap-2 [&>button]:shrink-0">
                <button
                  type="button"
                  onClick={() => setViewMode('genres')}
                  className={`inline-flex items-center justify-center gap-1.5 sm:gap-2 py-1.5 px-2.5 sm:py-2 sm:px-3.5 rounded-full text-xs sm:text-sm font-medium transition-all ${
                    viewMode === 'genres' || (viewMode === 'list' && (activeGenre !== ALL_GENRES || selectedTag !== null))
                      ? 'bg-[#00B4D8] text-white'
                      : 'bg-white/5 border border-white/10 text-zinc-400 hover:border-[#00B4D8]/40 hover:text-zinc-200'
                  }`}
                >
                  <span aria-hidden>📚</span>
                  Жанры
                </button>
                {SORT_OPTIONS.map((opt) => {
                  const isSidebarFilterActive = activeGenre !== ALL_GENRES || selectedTag !== null;
                  const isActive = viewMode === 'list' && !isSidebarFilterActive && activeSort === opt.key;
                  const isMine = opt.key === 'mine';
                  const isPremium = opt.key === 'premium';
                  const premiumActive = isPremium && isActive;
                  const premiumInactive = isPremium && !isActive;
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => {
                        if (isMine && !isAuthenticated) {
                          setShowMineLoginModal(true);
                          return;
                        }
                        setViewMode('list');
                        setActiveSort(opt.key);
                      }}
                      className={`inline-flex items-center justify-center gap-1.5 sm:gap-2 py-1.5 px-2.5 sm:py-2 sm:px-3.5 rounded-full text-xs sm:text-sm font-medium transition-all ${
                        premiumActive
                          ? 'bg-[#FFD700] text-black border border-[#FFD700] shadow-[0_0_12px_rgba(255,215,0,0.5)]'
                          : premiumInactive
                            ? 'bg-white/5 border border-[#FFD700]/60 text-[#FFD700] hover:border-[#FFD700] hover:shadow-[0_0_10px_rgba(255,215,0,0.3)]'
                            : isActive
                              ? 'bg-[#00B4D8] text-white'
                              : 'bg-white/5 border border-white/10 text-zinc-400 hover:border-[#00B4D8]/40 hover:text-zinc-200'
                      }`}
                    >
                      <span aria-hidden>{opt.icon}</span>
                      {opt.label}
                    </button>
                  );
                })}
              </div>

              {showMineLoginModal && (
                <div
                  className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
                  onClick={() => setShowMineLoginModal(false)}
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="mine-login-title"
                >
                  <div
                    className="bg-zinc-900 border border-white/10 rounded-xl p-6 max-w-sm w-full shadow-xl"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <p id="mine-login-title" className="text-white text-lg font-medium mb-4">
                      Войдите, чтобы сохранять любимые истории
                    </p>
                    <div className="flex gap-3">
                      <a
                        href="/profile"
                        className="flex-1 py-2.5 text-center rounded-lg bg-[#00B4D8] text-white font-medium hover:bg-cyan-600 transition-colors"
                      >
                        Войти
                      </a>
                      <button
                        type="button"
                        onClick={() => setShowMineLoginModal(false)}
                        className="px-4 py-2.5 rounded-lg border border-white/20 text-zinc-300 hover:bg-white/5 transition-colors"
                      >
                        Закрыть
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {viewMode === 'genres' ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {GENRES_LIST.map((genre) => (
                    <button
                      key={genre}
                      type="button"
                      onClick={() => {
                        setActiveGenre(genre);
                        setViewMode('list');
                      }}
                      className="relative aspect-[4/3] sm:aspect-[4/3] rounded-xl overflow-hidden bg-zinc-800 border border-white/10 hover:border-[#00B4D8]/50 transition-all w-full"
                    >
                      <img
                        src={genreImagePath(genre)}
                        alt=""
                        className="absolute inset-0 w-full h-full object-cover"
                        aria-hidden
                      />
                      <span className="absolute inset-0 bg-gradient-to-t from-black/95 via-black/60 to-black/50 sm:from-black/90 sm:via-black/40 sm:to-transparent" aria-hidden />
                      <span className="absolute inset-0 flex items-center justify-center text-center px-4 text-white font-bold text-xl sm:text-base drop-shadow-[0_2px_8px_rgba(0,0,0,0.9)] sm:drop-shadow-lg sm:font-semibold">
                        {genre}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <>
                  {activeGenre !== ALL_GENRES && (
                    <button
                      type="button"
                      onClick={handleBackToGenres}
                      className="mb-4 inline-flex items-center gap-2 text-sm text-[#00B4D8] hover:text-cyan-300 transition-colors"
                    >
                      <span aria-hidden>←</span>
                      Ко всем жанрам
                    </button>
                  )}
                  <div className="text-zinc-500 text-sm mb-4">
                    Показано {Math.min(visibleCount, filteredStories.length)} из {filteredStories.length} историй
                  </div>

                  {loading ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
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
                      ) : activeSort === 'mine' ? (
                        <p className="text-xl text-zinc-400 mb-2">
                          В избранном пока пусто. Отметьте истории сердечком в каталоге.
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
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                        {displayedStories.map((item) => (
                          <StoryTile key={item.id} {...item} />
                        ))}
                      </div>
                      {visibleCount < filteredStories.length && (
                        <div className="mt-8 flex justify-center">
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
                </>
              )}
            </div>
          </div>
        </div>
      </main>

      <MobileFilterDrawer
        isOpen={isFilterOpen}
        onClose={() => setIsFilterOpen(false)}
        accessFilter={accessFilter}
        setAccessFilter={setAccessFilter}
        activeGenre={activeGenre}
        setActiveGenre={setActiveGenre}
        genres={genres}
        allTags={allTags}
        selectedTag={selectedTag}
        setSelectedTag={setSelectedTag}
        onReset={handleResetFilters}
        onGenreSelect={() => { setViewMode('list'); setIsFilterOpen(false); }}
      />
    </div>
  );
}
