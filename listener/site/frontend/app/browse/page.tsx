'use client';

import { useSearchParams, useRouter } from 'next/navigation';
import { useMemo, useState, Suspense, useCallback, useEffect } from 'react';
import { MOCK_STORIES } from '@/data/mocks';
import CatalogStoryTile from '@/components/ui/CatalogStoryTile';
import CatalogSidebar, {
  getDefaultCatalogFilters,
  type CatalogFiltersState,
} from '@/components/v1/features/catalog/CatalogSidebar';
import type { Story } from '@/types/story';
import { Search, SlidersHorizontal } from 'lucide-react';

function normalize(s: string) {
  return s.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
}

function applyFiltersAndSort(
  stories: Story[],
  filters: CatalogFiltersState,
  q: string,
  sort: string
): Story[] {
  let result = [...stories];

  if (filters.access === 'free') {
    result = result.filter((s) => !s.isPremium);
  } else if (filters.access === 'paid') {
    result = result.filter((s) => s.isPremium);
  }

  if (filters.categories.length > 0) {
    const catToTags: Record<string, string[]> = {
      hypnosis: ['asmr', 'hypnosis'],
      roleplay: ['romance', 'roleplay', 'voice'],
      asmr: ['asmr'],
      spectacle: ['drama', 'mystery'],
    };
    const allowedTags = new Set(
      filters.categories.flatMap((c) => catToTags[c] ?? [c])
    );
    result = result.filter((s) =>
      s.tags.some((t) => allowedTags.has(normalize(t)))
    );
  }

  if (filters.theme) {
    const themeToTag: Record<string, string> = {
      domination: 'domination',
      romance: 'romance',
      fantasy: 'fantasy',
      asmr: 'asmr',
      night: 'night',
      soft: 'soft',
    };
    const tag = themeToTag[filters.theme] ?? filters.theme;
    result = result.filter((s) => s.tags.map(normalize).includes(tag));
  }

  if (filters.audience.length > 0) {
    const audienceToTag: Record<string, string> = {
      him: 'male',
      her: 'female',
      couple: 'couple',
    };
    const allowed = new Set(
      filters.audience.map((a) => audienceToTag[a] ?? a)
    );
    const byAudience = result.filter((s) =>
      s.tags.some((t) => allowed.has(normalize(t)))
    );
    if (byAudience.length > 0) result = byAudience;
  }

  if (filters.narrator) {
    const lower = filters.narrator.toLowerCase();
    result = result.filter((s) =>
      s.authorName.toLowerCase().includes(lower)
    );
  }

  if (q.trim()) {
    const lower = q.toLowerCase();
    result = result.filter(
      (s) =>
        s.title.toLowerCase().includes(lower) ||
        s.authorName.toLowerCase().includes(lower) ||
        s.tags.some((t) => t.toLowerCase().includes(lower))
    );
  }

  if (sort === 'newest') {
    result.sort((a, b) => b.id - a.id);
  } else if (sort === 'duration') {
    result.sort((a, b) => b.durationSec - a.durationSec);
  } else {
    result.sort((a, b) => a.id - b.id);
  }

  return result;
}

function getNarrators(stories: Story[]): string[] {
  const set = new Set(stories.map((s) => s.authorName));
  return Array.from(set).sort();
}

function BrowseContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const qParam = searchParams.get('q') ?? '';
  const sortParam = searchParams.get('sort') ?? 'popular';

  const [searchQuery, setSearchQuery] = useState(qParam);
  const [filters, setFilters] = useState<CatalogFiltersState>(() => ({
    ...getDefaultCatalogFilters(),
  }));
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    setSearchQuery(qParam);
  }, [qParam]);

  const narrators = useMemo(() => getNarrators(MOCK_STORIES), []);

  const filtered = useMemo(
    () => applyFiltersAndSort(MOCK_STORIES, filters, searchQuery, sortParam),
    [filters, searchQuery, sortParam]
  );

  const handleReset = useCallback(() => {
    setFilters(getDefaultCatalogFilters());
    setSearchQuery('');
    setDrawerOpen(false);
    window.history.replaceState(null, '', '/browse');
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-[#000814] pt-24">
      <button
        type="button"
        onClick={() => setDrawerOpen(true)}
        className="fixed left-4 top-24 z-30 flex items-center gap-2 rounded-xl border border-white/10 bg-[#001d3d]/80 px-4 py-2 text-sm font-medium text-white backdrop-blur-xl lg:hidden"
        aria-label="Открыть фильтры"
      >
        <SlidersHorizontal className="h-4 w-4" strokeWidth={1.5} />
        Фильтры
      </button>
      <div className="mx-auto flex w-full max-w-6xl flex-1 gap-6 px-6 pb-12 sm:px-8 lg:gap-8 lg:px-12">
        <aside className="sticky top-24 hidden h-fit w-80 flex-shrink-0 lg:block">
          <div className="rounded-[2.5rem] border border-white/10 bg-[#001d3d]/50 p-6 backdrop-blur-xl">
            <CatalogSidebar
              filters={filters}
              onFiltersChange={setFilters}
              onReset={handleReset}
              narrators={narrators}
            />
          </div>
        </aside>
        <div className="lg:hidden">
          <CatalogSidebar
            filters={filters}
            onFiltersChange={setFilters}
            onReset={handleReset}
            narrators={narrators}
            isOpen={drawerOpen}
            onClose={() => setDrawerOpen(false)}
          />
        </div>

        <div className="min-w-0 flex-1">
          <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <h1 className="text-2xl font-black tracking-tight text-white md:text-3xl">
              Каталог
            </h1>
            <div className="relative flex-1 sm:max-w-md">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" strokeWidth={1.5} />
              <input
                type="search"
                placeholder="Поиск..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-white/5 py-2.5 pl-10 pr-4 text-sm text-white placeholder-zinc-600 outline-none focus:border-[#00B4D8]/50 focus:ring-1 focus:ring-[#00B4D8]/30"
                aria-label="Поиск по каталогу"
              />
            </div>
          </div>

          <p className="mb-6 text-sm text-zinc-400">
            Найдено записей: {filtered.length}
          </p>

          {filtered.length > 0 ? (
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filtered.map((story) => (
                <CatalogStoryTile key={story.id} story={story} />
              ))}
            </div>
          ) : (
            <p className="py-16 text-center text-zinc-500">
              Ничего не найдено. Измените фильтры или поисковый запрос.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function BrowsePage() {
  return (
    <Suspense fallback={<div className="flex min-h-[50vh] items-center justify-center text-zinc-400">Загрузка...</div>}>
      <BrowseContent />
    </Suspense>
  );
}
