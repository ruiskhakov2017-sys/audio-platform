'use client';

import { useEffect, useState, useCallback } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { Pencil, Trash2, Search } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import { mapRowToStory } from '@/lib/stories';
import { updateStoryGenre, deleteStory } from '@/app/actions/upload';
import type { Story } from '@/types/story';

const GENRES = [
  'asmr',
  'romance',
  'sci-fi',
  'city',
  'night',
  'neon',
  'soft',
  'voice',
  'premium',
  'drama',
  'thriller',
  'ambient',
  'mystery',
  'hypnosis',
  'roleplay',
  'domination',
];

export default function AdminStoriesPage() {
  const [list, setList] = useState<Story[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [genreFilter, setGenreFilter] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editGenre, setEditGenre] = useState('');
  const [deletingId, setDeletingId] = useState<number | null>(null);

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
    if (q && !s.title.toLowerCase().includes(q) && !s.authorName.toLowerCase().includes(q)) return false;
    if (genreFilter && !s.tags.includes(genreFilter)) return false;
    return true;
  });

  const handleGenreSave = async (id: number) => {
    const tags = editGenre ? [editGenre] : [];
    const res = await updateStoryGenre(id, editGenre || '', tags);
    if (res.success) {
      setList((prev) =>
        prev.map((s) =>
          s.id === id ? { ...s, tags: tags.length ? tags : s.tags, authorName: s.authorName } : s
        )
      );
      setEditingId(null);
      setEditGenre('');
    } else {
      alert(res.error);
    }
  };

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
          {GENRES.map((g) => (
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
                    <div>
                      <p className="text-white font-medium">{story.title}</p>
                      <p className="text-zinc-500 text-xs">{story.authorName || '—'}</p>
                    </div>
                  </td>
                  <td className="p-4 text-zinc-400">
                    {editingId === story.id ? (
                      <div className="flex items-center gap-1">
                        <select
                          value={editGenre}
                          onChange={(e) => setEditGenre(e.target.value)}
                          className="px-2 py-1 rounded border border-zinc-600 bg-zinc-800 text-white text-xs focus:border-cyan-500 focus:outline-none"
                        >
                          <option value="">—</option>
                          {GENRES.map((g) => (
                            <option key={g} value={g}>
                              {g}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={() => handleGenreSave(story.id)}
                          className="px-2 py-1 rounded bg-cyan-600 text-white text-xs hover:bg-cyan-500"
                        >
                          OK
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingId(null);
                            setEditGenre('');
                          }}
                          className="px-2 py-1 rounded bg-zinc-700 text-zinc-400 text-xs hover:bg-zinc-600"
                        >
                          Отмена
                        </button>
                      </div>
                    ) : (
                      <span>{story.tags[0] ?? '—'}</span>
                    )}
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
                        onClick={() => {
                          setEditingId(story.id);
                          setEditGenre(story.tags[0] ?? '');
                        }}
                        disabled={editingId != null && editingId !== story.id}
                        className="p-1.5 rounded text-zinc-500 hover:text-white hover:bg-zinc-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        aria-label="Редактировать жанр"
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
                        <Trash2 className={`w-4 h-4 ${deletingId === story.id ? 'animate-pulse opacity-50' : ''}`} />
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
    </div>
  );
}
