"use client";

import { useState, useEffect } from "react";
import { fetchStoriesFromApi, useDjangoApi } from "@/lib/api";
import { StoryCard } from "@/components/v1/ui/StoryCard";
import { getAllTags, filterStoriesByTags, getUniqueCategories } from "@/lib/tags";
import { Search, Filter, X } from "lucide-react";
import type { Story } from "@/types/story";

export default function CatalogPage() {
  const [stories, setStories] = useState<Story[]>([]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [isMobileFiltersOpen, setIsMobileFiltersOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    setLoadError(null);
    const hasSupabase = typeof window !== "undefined" && !!process.env.NEXT_PUBLIC_SUPABASE_URL;
    async function load() {
      if (hasSupabase) {
        const res = await fetch("/api/stories", { cache: "no-store" });
        const data = res.ok ? await res.json() : [];
        setStories(Array.isArray(data) ? data : []);
        if (!res.ok) {
          const msg = res.status === 503
            ? "Supabase не настроен. В Vercel задайте NEXT_PUBLIC_SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY, затем Redeploy."
            : `Ошибка загрузки: ${res.status}. Проверьте SUPABASE_SERVICE_ROLE_KEY и таблицу stories.`;
          setLoadError(msg);
        }
        return;
      }
      if (useDjangoApi()) {
        const list = await fetchStoriesFromApi();
        setStories(Array.isArray(list) ? list : []);
        return;
      }
      setLoadError("Задайте NEXT_PUBLIC_SUPABASE_URL в настройках проекта.");
    }
    load().catch(() => setLoadError("Ошибка загрузки каталога."));
  }, []);

  const allTags = getAllTags(stories);
  const categories = getUniqueCategories(allTags);

  const filteredStories = filterStoriesByTags(stories, selectedTags).filter((story) =>
    story.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  return (
    <div className="min-h-screen bg-[#1a0b2e] pt-24 pb-32">
      <div className="container mx-auto px-4">
        <div className="mb-8 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <h1 className="text-3xl font-bold text-white">Каталог</h1>

          <div className="flex gap-2">
            <div className="relative flex-1 md:w-64">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Поиск..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-white/5 py-2 pl-10 pr-4 text-sm text-white placeholder-gray-400 backdrop-blur-md focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500"
              />
            </div>
            <button
              onClick={() => setIsMobileFiltersOpen(!isMobileFiltersOpen)}
              className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-white md:hidden"
            >
              <Filter className="h-4 w-4" />
              Фильтры
            </button>
          </div>
        </div>

        <div className="flex gap-8">
          <aside className={`fixed inset-y-0 left-0 z-40 w-64 transform bg-[#1a0b2e]/95 p-6 backdrop-blur-xl transition-transform duration-300 md:static md:block md:w-64 md:bg-transparent md:p-0 ${isMobileFiltersOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}`}>
            <div className="mb-6 flex items-center justify-between md:hidden">
              <h2 className="text-xl font-bold text-white">Фильтры</h2>
              <button onClick={() => setIsMobileFiltersOpen(false)}>
                <X className="h-6 w-6 text-white" />
              </button>
            </div>

            <div className="space-y-8">
              {Object.entries(categories).map(([category, tags]) => (
                <div key={category} className="space-y-3">
                  <h3 className="text-sm font-bold uppercase tracking-wider text-purple-400">
                    {category === 'undefined' ? 'Теги' : category}
                  </h3>
                  <div className="space-y-2">
                    {tags.map((tag) => (
                      <label key={tag} className="flex items-center gap-3 group cursor-pointer">
                        <div className="relative flex items-center">
                          <input
                            type="checkbox"
                            checked={selectedTags.includes(tag)}
                            onChange={() => toggleTag(tag)}
                            className="peer h-4 w-4 appearance-none rounded border border-white/20 bg-white/5 transition-all checked:border-purple-500 checked:bg-purple-500 hover:border-purple-400"
                          />
                          <svg
                            className="pointer-events-none absolute left-1/2 top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 opacity-0 transition-opacity peer-checked:opacity-100"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="white"
                            strokeWidth="3"
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        </div>
                        <span className={`text-sm transition-colors ${selectedTags.includes(tag) ? 'text-white' : 'text-gray-400 group-hover:text-gray-300'}`}>
                          {tag}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </aside>

          <div className="flex-1 min-w-0">
            {/* Мобилка: горизонтальный свайп тегов над сеткой */}
            <div className="md:hidden overflow-x-auto pb-3 -mx-4 px-4 scrollbar-hide flex gap-2 mb-4 [&>button]:shrink-0">
              {allTags.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  onClick={() => toggleTag(tag)}
                  className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                    selectedTags.includes(tag)
                      ? "bg-purple-500 text-white"
                      : "bg-white/10 text-gray-300 hover:bg-white/20"
                  }`}
                >
                  {tag}
                </button>
              ))}
            </div>
            {filteredStories.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                {filteredStories.map((story) => (
                  <StoryCard key={story.id} story={story} />
                ))}
              </div>
            ) : (
              <div className="flex h-64 flex-col items-center justify-center text-center">
                {loadError ? (
                  <p className="text-lg text-amber-400">{loadError}</p>
                ) : stories.length === 0 ? (
                  <p className="text-lg text-gray-400">Каталог пуст. Загрузите рассказы в админке или проверьте переменные Supabase в Vercel.</p>
                ) : (
                  <>
                    <p className="text-lg text-gray-400">Ничего не найдено</p>
                    <button
                      onClick={() => { setSelectedTags([]); setSearchQuery(""); }}
                      className="mt-4 text-purple-400 hover:text-purple-300"
                    >
                      Сбросить фильтры
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
