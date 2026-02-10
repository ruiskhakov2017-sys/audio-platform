'use client';

import { useMemo, useState } from 'react';
import { X, Search, ChevronDown } from 'lucide-react';
import {
  ACCESS_TYPES,
  CATEGORIES,
  AUDIO_THEMES,
  TARGET_AUDIENCE,
} from '@/components/v1/config/filters';
import type { AccessTypeId, CategoryId, ThemeId, AudienceId } from '@/components/v1/config/filters';
import type { Story } from '@/types/story';

const SIDEBAR_WIDTH = 300;

export type CatalogFiltersState = {
  access: AccessTypeId;
  categories: CategoryId[];
  theme: ThemeId | '';
  audience: AudienceId[];
  narrator: string;
};

const defaultFilters: CatalogFiltersState = {
  access: 'all',
  categories: [],
  theme: '',
  audience: [],
  narrator: '',
};

type CatalogSidebarProps = {
  filters: CatalogFiltersState;
  onFiltersChange: (f: CatalogFiltersState) => void;
  onReset: () => void;
  narrators: string[];
  isOpen?: boolean;
  onClose?: () => void;
};

export function getDefaultCatalogFilters(): CatalogFiltersState {
  return { ...defaultFilters };
}

export default function CatalogSidebar({
  filters,
  onFiltersChange,
  onReset,
  narrators,
  isOpen = true,
  onClose,
}: CatalogSidebarProps) {
  const [themeOpen, setThemeOpen] = useState(false);
  const [narratorOpen, setNarratorOpen] = useState(false);
  const [narratorQuery, setNarratorQuery] = useState('');

  const filteredNarrators = useMemo(() => {
    if (!narratorQuery.trim()) return narrators;
    const q = narratorQuery.toLowerCase();
    return narrators.filter((n) => n.toLowerCase().includes(q));
  }, [narrators, narratorQuery]);

  const toggleCategory = (id: CategoryId) => {
    const next = filters.categories.includes(id)
      ? filters.categories.filter((c) => c !== id)
      : [...filters.categories, id];
    onFiltersChange({ ...filters, categories: next });
  };

  const toggleAudience = (id: AudienceId) => {
    const next = filters.audience.includes(id)
      ? filters.audience.filter((a) => a !== id)
      : [...filters.audience, id];
    onFiltersChange({ ...filters, audience: next });
  };

  const content = (
    <div
      className="flex h-full flex-col overflow-y-auto border-r border-white/10 bg-white/5 px-4 py-6 backdrop-blur-xl"
      style={{ width: SIDEBAR_WIDTH }}
    >
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-zinc-400 text-xs font-bold uppercase tracking-widest">
          Фильтры
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onReset}
            className="flex items-center gap-1 text-xs text-[#a78bfa] transition-opacity hover:opacity-90"
          >
            <X className="h-3.5 w-3.5" />
            Сбросить всё
          </button>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[#c4b5fd] transition-colors hover:bg-white/10 lg:hidden"
              aria-label="Закрыть фильтры"
            >
              <X className="h-5 w-5" />
            </button>
          )}
        </div>
      </div>

      {/* Тип доступа */}
      <div className="mb-6">
        <p className="mb-2 text-zinc-400 text-xs font-bold uppercase tracking-widest">
          Тип доступа
        </p>
        <div className="flex flex-wrap gap-2">
          {ACCESS_TYPES.map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => onFiltersChange({ ...filters, access: a.id })}
              className="rounded-full px-3 py-1.5 text-xs font-medium transition-all"
              style={{
                backgroundColor: filters.access === a.id ? 'rgba(167, 139, 250, 0.3)' : 'rgba(255,255,255,0.05)',
                color: filters.access === a.id ? '#e9d5ff' : '#c4b5fd',
                border: `1px solid ${filters.access === a.id ? 'rgba(167,139,250,0.5)' : 'rgba(255,255,255,0.1)'}`,
              }}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>

      {/* Категория */}
      <div className="mb-6">
        <p className="mb-2 text-zinc-400 text-xs font-bold uppercase tracking-widest">
          Категория
        </p>
        <div className="flex flex-col gap-2">
          {CATEGORIES.map((c) => (
            <label
              key={c.id}
              className="flex cursor-pointer items-center gap-2 text-sm transition-opacity hover:opacity-90"
              style={{ color: '#c4b5fd' }}
            >
              <input
                type="checkbox"
                checked={filters.categories.includes(c.id)}
                onChange={() => toggleCategory(c.id)}
                className="h-4 w-4 rounded border-gray-500 bg-white/5 accent-violet-500"
              />
              {c.label}
            </label>
          ))}
        </div>
      </div>

      {/* Темы аудио (dropdown) */}
      <div className="mb-6">
        <p className="mb-2 text-zinc-400 text-xs font-bold uppercase tracking-widest">
          Темы аудио
        </p>
        <div className="relative">
          <button
            type="button"
            onClick={() => { setThemeOpen(!themeOpen); setNarratorOpen(false); }}
            className="flex w-full items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors"
            style={{
              borderColor: 'rgba(255,255,255,0.1)',
              backgroundColor: 'rgba(255,255,255,0.05)',
              color: filters.theme ? '#e9d5ff' : '#9ca3af',
            }}
          >
            <span>
              {filters.theme
                ? AUDIO_THEMES.find((t) => t.id === filters.theme)?.label ?? filters.theme
                : 'Выберите тему'}
            </span>
            <ChevronDown className={`h-4 w-4 transition-transform ${themeOpen ? 'rotate-180' : ''}`} />
          </button>
          {themeOpen && (
            <div
              className="absolute left-0 right-0 top-full z-10 mt-1 max-h-48 overflow-y-auto rounded-lg border py-1"
              style={{
                backgroundColor: '#1a1124',
                borderColor: 'rgba(255,255,255,0.1)',
              }}
            >
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-white/5"
                style={{ color: '#c4b5fd' }}
                onClick={() => {
                  onFiltersChange({ ...filters, theme: '' });
                  setThemeOpen(false);
                }}
              >
                Любая
              </button>
              {AUDIO_THEMES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm hover:bg-white/5"
                  style={{ color: '#c4b5fd' }}
                  onClick={() => {
                    onFiltersChange({ ...filters, theme: t.id });
                    setThemeOpen(false);
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Для кого */}
      <div className="mb-6">
        <p className="mb-2 text-zinc-400 text-xs font-bold uppercase tracking-widest">
          Для кого
        </p>
        <div className="flex flex-col gap-2">
          {TARGET_AUDIENCE.map((a) => (
            <label
              key={a.id}
              className="flex cursor-pointer items-center gap-2 text-sm transition-opacity hover:opacity-90"
              style={{ color: '#c4b5fd' }}
            >
              <input
                type="checkbox"
                checked={filters.audience.includes(a.id)}
                onChange={() => toggleAudience(a.id)}
                className="h-4 w-4 rounded border-gray-500 bg-white/5 accent-violet-500"
              />
              {a.label}
            </label>
          ))}
        </div>
      </div>

      {/* Диктор (поиск) */}
      <div className="mb-2">
        <p className="mb-2 text-zinc-400 text-xs font-bold uppercase tracking-widest">
          Диктор
        </p>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: '#6b7280' }} />
          <input
            type="text"
            placeholder="Поиск по имени..."
            value={narratorQuery || filters.narrator}
            onChange={(e) => {
              setNarratorQuery(e.target.value);
              if (!e.target.value) onFiltersChange({ ...filters, narrator: '' });
            }}
            onFocus={() => setNarratorOpen(true)}
            className="w-full rounded-lg border py-2 pl-9 pr-3 text-sm outline-none"
            style={{
              borderColor: 'rgba(255,255,255,0.1)',
              backgroundColor: 'rgba(255,255,255,0.05)',
              color: '#e9d5ff',
            }}
          />
          {narratorOpen && filteredNarrators.length > 0 && (
            <div
              className="absolute left-0 right-0 top-full z-10 mt-1 max-h-40 overflow-y-auto rounded-lg border py-1"
              style={{
                backgroundColor: '#1a1124',
                borderColor: 'rgba(255,255,255,0.1)',
              }}
            >
              {filteredNarrators.map((n) => (
                <button
                  key={n}
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm hover:bg-white/5"
                  style={{ color: '#c4b5fd' }}
                  onClick={() => {
                    onFiltersChange({ ...filters, narrator: n });
                    setNarratorQuery('');
                    setNarratorOpen(false);
                  }}
                >
                  {n}
                </button>
              ))}
            </div>
          )}
        </div>
        {filters.narrator && (
          <p className="mt-1 text-xs" style={{ color: '#9ca3af' }}>
            Выбран: {filters.narrator}
          </p>
        )}
      </div>
    </div>
  );

  if (onClose) {
    return (
      <>
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
          style={{ display: isOpen ? 'block' : 'none' }}
          onClick={onClose}
          aria-hidden
        />
        <aside
          className={`fixed left-0 top-0 z-50 h-full w-[300px] shrink-0 shadow-xl transition-transform duration-200 ease-out lg:static lg:z-0 ${isOpen ? 'translate-x-0' : '-translate-x-full'} lg:translate-x-0`}
        >
          {content}
        </aside>
      </>
    );
  }

  return (
    <aside className="hidden shrink-0 lg:block" style={{ width: SIDEBAR_WIDTH }}>
      {content}
    </aside>
  );
}
