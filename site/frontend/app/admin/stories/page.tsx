'use client';

import { useMemo, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { MOCK_STORIES } from '@/data/mockData';
import { Pencil, Trash2, Search } from 'lucide-react';

const GENRES = ['', 'asmr', 'romance', 'sci-fi', 'city', 'night', 'neon', 'soft', 'voice', 'premium', 'drama', 'thriller'];

export default function AdminStoriesPage() {
  const [search, setSearch] = useState('');
  const [genreFilter, setGenreFilter] = useState('');

  const filtered = useMemo(() => {
    let list = MOCK_STORIES;
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (s) =>
          s.title.toLowerCase().includes(q) ||
          s.authorName.toLowerCase().includes(q)
      );
    }
    if (genreFilter) {
      list = list.filter((s) => s.tags.includes(genreFilter));
    }
    return list;
  }, [search, genreFilter]);

  return (
    <div className="p-8">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <h1 className="text-2xl font-semibold text-white">Контент</h1>
        <Link
          href="/admin/upload"
          className="px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 transition-colors"
        >
          Загрузить историю
        </Link>
      </div>

      <div className="flex flex-wrap gap-4 mb-6">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по названию или автору..."
            className="w-full pl-9 pr-4 py-2 rounded-lg border border-zinc-700 bg-zinc-900 text-white placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none text-sm"
          />
        </div>
        <select
          value={genreFilter}
          onChange={(e) => setGenreFilter(e.target.value)}
          className="px-4 py-2 rounded-lg border border-zinc-700 bg-zinc-900 text-white focus:border-cyan-600 focus:outline-none text-sm min-w-[160px]"
        >
          <option value="">Все жанры</option>
          {GENRES.filter(Boolean).map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
      </div>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-zinc-500">
              <th className="p-4 font-medium w-20">Обложка</th>
              <th className="p-4 font-medium">Название / Автор</th>
              <th className="p-4 font-medium">Жанр</th>
              <th className="p-4 font-medium">Статус</th>
              <th className="p-4 font-medium w-28">Действия</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((story) => (
              <tr
                key={story.id}
                className="border-b border-zinc-800/50 hover:bg-zinc-800/30"
              >
                <td className="p-4">
                  <div className="relative w-12 h-12 rounded overflow-hidden bg-zinc-800">
                    <Image
                      src={story.coverImage}
                      alt=""
                      fill
                      className="object-cover"
                      unoptimized
                      sizes="48px"
                    />
                  </div>
                </td>
                <td className="p-4">
                  <div>
                    <p className="text-white font-medium">{story.title}</p>
                    <p className="text-zinc-500 text-xs">{story.authorName}</p>
                  </div>
                </td>
                <td className="p-4 text-zinc-400">{story.tags[0] ?? '—'}</td>
                <td className="p-4">
                  <span
                    className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                      story.isPremium
                        ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                        : 'bg-zinc-700 text-zinc-400 border border-zinc-600'
                    }`}
                  >
                    {story.isPremium ? 'Premium' : 'Free'}
                  </span>
                </td>
                <td className="p-4">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="p-1.5 rounded text-zinc-500 hover:text-white hover:bg-zinc-700 transition-colors"
                      aria-label="Редактировать"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      className="p-1.5 rounded text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
                      aria-label="Удалить"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="p-8 text-center text-zinc-500">
            Ничего не найдено
          </div>
        )}
      </div>
    </div>
  );
}
