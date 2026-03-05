'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { Pencil, Trash2, Search } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import { mapRowToStory, getDisplayTags } from '@/lib/stories';
import { deleteStory } from '@/app/actions/upload';
import { EditStoryDrawer } from './EditStoryDrawer';
import type { Story } from '@/types/story';

export default function AdminStoriesPage() {
  const [list, setList] = useState<Story[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [genreFilter, setGenreFilter] = useState('');
  const [editStory, setEditStory] = useState<Story | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const uniqueTags = useMemo(() => {
    const set = new Set<string>();
    list.forEach((s) => getDisplayTags(s).forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [list]);

  const fetchStories = useCallback(async () => {
    if (!supabase) {
      setLoading(false);
      return;
    }
    const { data, error } = await supabase
      .from('stories')
      .select('*')
      .order('created_at', { ascending: false });
    setLoading(false);
    if (error) return;
    setList((data ?? []).map(mapRowToStory));
  }, []);

  useEffect(() => {
    fetchStories();
  }, [fetchStories]);

  const filtered = list.filter((s) => {
    const q = search.trim().toLowerCase();
    if (q && !s.title.toLowerCase().includes(q)) return false;
    if (genreFilter && !getDisplayTags(s).includes(genreFilter)) return false;
    return true;
  });

  const handleDelete = async (id: number) => {
    if (!confirm('Удалить эту историю? Файл в R2 тоже будет удалён.')) return;
    setDeletingId(id);
    const res = await deleteStory(id);
    setDeletingId(null);
    if (res.success) {
      setList((prev) => prev.filter((s) => s.id !== id));
    } else {
      alert(res.error);
    }
  };

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
            placeholder="Поиск по названию..."
            className="w-full pl-9 pr-4 py-2 rounded-lg border border-zinc-700 bg-zinc-900 text-white placeholder:text-zinc-500 focus:border-cyan-600 focus:outline-none text-sm"
          />
        </div>
        <select
          value={genreFilter}
          onChange={(e) => setGenreFilter(e.target.value)}
          className="px-4 py-2 rounded-lg border border-zinc-700 bg-zinc-900 text-white focus:border-cyan-600 focus:outline-none text-sm min-w-[160px]"
        >
          <option value="">Все теги</option>
          {uniqueTags.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="py-12 text-center text-zinc-500">Загрузка...</div>
      ) : (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-zinc-500">
                <th className="p-4 font-medium w-20">Обложка</th>
                <th className="p-4 font-medium">Название</th>
                <th className="p-4 font-medium">Теги / Жанры</th>
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
                      {story.coverImage ? (
                        <Image
                          src={story.coverImage}
                          alt=""
                          fill
                          className="object-cover"
                          unoptimized
                          sizes="48px"
                        />
                      ) : (
                        <div className="absolute inset-0 flex items-center justify-center text-zinc-500 text-xs">
                          —
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="p-4">
                    <p className="text-white font-medium">{story.title}</p>
                  </td>
                  <td className="p-4 text-zinc-400">
                    <span className="text-xs">
                      {getDisplayTags(story).slice(0, 3).join(', ') || '—'}
                    </span>
                  </td>
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
                        onClick={() => setEditStory(story)}
                        className="p-1.5 rounded text-zinc-500 hover:text-white hover:bg-zinc-700 transition-colors"
                        aria-label="Редактировать"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(story.id)}
                        disabled={deletingId === story.id}
                        className="p-1.5 rounded text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        aria-label="Удалить"
                      >
                        <Trash2
                          className={`w-4 h-4 ${deletingId === story.id ? 'animate-pulse opacity-50' : ''}`}
                        />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length === 0 && (
            <div className="p-8 text-center text-zinc-500">
              Ничего не найдено. Загрузите истории в разделе «Загрузить историю».
            </div>
          )}
        </div>
      )}

      {editStory && (
        <EditStoryDrawer
          story={editStory}
          onClose={() => setEditStory(null)}
          onSaved={fetchStories}
        />
      )}
    </div>
  );
}
