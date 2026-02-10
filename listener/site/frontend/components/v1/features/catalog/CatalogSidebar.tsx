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
      className="flex h-full flex-col overflow-y-auto border-r border-white/5 bg-[#001d3d]/60 px-4 py-6 backdrop-blur-[40px]"
      style={{ width: SIDEBAR_WIDTH }}
    >
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xs font-bold uppercase tracking-widest text-white/50">
          Фильтры
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onReset}
            className="flex items-center gap-1 text-xs text-[#00B4D8] transition-opacity hover:opacity-90"
          >
            <X className="h-3.5 w-3.5" strokeWidth={1.5} />
            Сбросить всё
          </button>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-white/10 text-white/70 transition-colors hover:border-[#00B4D8]/30 hover:text-[#00B4D8] lg:hidden"
              aria-label="Закрыть фильтры"
            >
              <X className="h-5 w-5" strokeWidth={1.5} />
            </button>
          )}
        </div>
      </div>

      {/* Тип доступа */}
      <div className="mb-6">
        <p className="mb-2 text-xs font-bold uppercase tracking-widest text-white/50">
          Тип доступа
        </p>
        <div className="flex flex-wrap gap-2">
          {ACCESS_TYPES.map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => onFiltersChange({ ...filters, access: a.id })}
              className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-all ${
                filters.access === a.id
                  ? 'border-[#00B4D8]/50 bg-[#00B4D8]/20 text-[#00B4D8] shadow-[0_0_14px_rgba(0,180,216,0.2)]'
                  : 'border-white/10 bg-white/5 text-white/70 hover:border-white/20'
              }`}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>

      {/* Категория — Тип аудио */}
      <div className="mb-6">
        <p className="mb-2 text-xs font-bold uppercase tracking-widest text-white/50">
          Тип аудио
        </p>
        <div className="flex flex-col gap-2">
          {CATEGORIES.map((c) => {
            const checked = filters.categories.includes(c.id);
            return (
              <label
                key={c.id}
                className="flex cursor-pointer items-center gap-3 text-sm transition-opacity hover:opacity-90"
              >
                <span className="relative flex h-5 w-5 shrink-0 items-center justify-center rounded-md border border-white/20 bg-white/5 transition-all">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleCategory(c.id)}
                    className="sr-only"
                  />
                  {checked && (
                    <span
                      className="absolute inset-0 rounded-md shadow-[0_0_12px_rgba(0,180,216,0.5)]"
                      style={{
                        backgroundColor: 'rgba(0,180,216,0.25)',
                        border: '1px solid rgba(0,180,216,0.6)',
                        boxShadow: '0 0 12px rgba(0,180,216,0.4), inset 0 0 8px rgba(0,180,216,0.2)',
                      }}
                    />
                  )}
                  {checked && (
                    <svg className="relative h-3 w-3 text-[#00B4D8]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </span>
                <span className={checked ? 'text-[#00B4D8]' : 'text-white/70'}>
                  {c.label}
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {/* Темы аудио (dropdown) */}
      <div className="mb-6">
        <p className="mb-2 text-xs font-bold uppercase tracking-widest text-white/50">
          Темы аудио
        </p>
        <div className="relative">
          <button
            type="button"
            onClick={() => { setThemeOpen(!themeOpen); setNarratorOpen(false); }}
            className="flex w-full items-center justify-between rounded-[1rem] border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/80 transition-colors hover:border-white/20"
          >
            <span>
              {filters.theme
                ? AUDIO_THEMES.find((t) => t.id === filters.theme)?.label ?? filters.theme
                : 'Выберите тему'}
            </span>
            <ChevronDown className={`h-4 w-4 transition-transform ${themeOpen ? 'rotate-180' : ''}`} strokeWidth={1.5} />
          </button>
          {themeOpen && (
            <div className="absolute left-0 right-0 top-full z-10 mt-1 max-h-48 overflow-y-auto rounded-[1rem] border border-white/10 bg-[#001d3d]/95 py-1 backdrop-blur-md">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm text-white/70 hover:bg-white/5 hover:text-[#00B4D8]"
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
                  className="w-full px-3 py-2 text-left text-sm text-white/70 hover:bg-white/5 hover:text-[#00B4D8]"
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

      {/* Цель — Для кого */}
      <div className="mb-6">
        <p className="mb-2 text-xs font-bold uppercase tracking-widest text-white/50">
          Цель
        </p>
        <div className="flex flex-col gap-2">
          {TARGET_AUDIENCE.map((a) => {
            const checked = filters.audience.includes(a.id);
            return (
              <label
                key={a.id}
                className="flex cursor-pointer items-center gap-3 text-sm transition-opacity hover:opacity-90"
              >
                <span className="relative flex h-5 w-5 shrink-0 items-center justify-center rounded-md border border-white/20 bg-white/5 transition-all">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleAudience(a.id)}
                    className="sr-only"
                  />
                  {checked && (
                    <span
                      className="absolute inset-0 rounded-md"
                      style={{
                        backgroundColor: 'rgba(0,180,216,0.25)',
                        border: '1px solid rgba(0,180,216,0.6)',
                        boxShadow: '0 0 12px rgba(0,180,216,0.4), inset 0 0 8px rgba(0,180,216,0.2)',
                      }}
                    />
                  )}
                  {checked && (
                    <svg className="relative h-3 w-3 text-[#00B4D8]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </span>
                <span className={checked ? 'text-[#00B4D8]' : 'text-white/70'}>
                  {a.label}
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {/* Дикторы */}
      <div className="mb-2">
        <p className="mb-2 text-xs font-bold uppercase tracking-widest text-white/50">
          Дикторы
        </p>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/40" strokeWidth={1.5} />
          <input
            type="text"
            placeholder="Поиск по имени..."
            value={narratorQuery || filters.narrator}
            onChange={(e) => {
              setNarratorQuery(e.target.value);
              if (!e.target.value) onFiltersChange({ ...filters, narrator: '' });
            }}
            onFocus={() => setNarratorOpen(true)}
            className="w-full rounded-[1rem] border border-white/10 bg-white/5 py-2 pl-9 pr-3 text-sm text-white outline-none placeholder:text-white/40 focus:border-[#00B4D8]/40 focus:ring-1 focus:ring-[#00B4D8]/20"
          />
          {narratorOpen && filteredNarrators.length > 0 && (
            <div className="absolute left-0 right-0 top-full z-10 mt-1 max-h-40 overflow-y-auto rounded-[1rem] border border-white/10 bg-[#001d3d]/95 py-1 backdrop-blur-md">
              {filteredNarrators.map((n) => (
                <button
                  key={n}
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm text-white/70 hover:bg-white/5 hover:text-[#00B4D8]"
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
          <p className="mt-1 text-xs text-white/50">
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
