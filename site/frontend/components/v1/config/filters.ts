/**
 * Конфиг фильтров каталога — «Библиотека сессий», «Навигатор по жанрам».
 * Термины: Сессия, Запись, Коллекция (не магазин/корзина).
 */

/** Тип доступа: Все / Бесплатные / Платные */
export const ACCESS_TYPES = [
  { id: 'all', label: 'Все' },
  { id: 'free', label: 'Бесплатные' },
  { id: 'paid', label: 'Платные' },
] as const;

/** Категории контента (тип: гипноз / ролевая / спектакль) */
export const CATEGORIES = [
  { id: 'hypnosis', label: 'Гипнозы' },
  { id: 'roleplay', label: 'Ролевые игры' },
  { id: 'spectacle', label: 'Спектакли' },
] as const;

/** Темы аудио (dropdown) */
export const AUDIO_THEMES = [
  { id: 'domination', label: 'Доминирование' },
  { id: 'romance', label: 'Романтика' },
  { id: 'fantasy', label: 'Фэнтези' },
  { id: 'asmr', label: 'ASMR' },
  { id: 'night', label: 'Ночная атмосфера' },
  { id: 'soft', label: 'Мягкий тон' },
] as const;

/** Целевая аудитория: для кого */
export const TARGET_AUDIENCE = [
  { id: 'him', label: 'Для него' },
  { id: 'her', label: 'Для неё' },
  { id: 'couple', label: 'Для пары' },
] as const;

/** Сортировка */
export const SORT_OPTIONS = [
  { id: 'popular', label: 'По популярности' },
  { id: 'newest', label: 'По новизне' },
  { id: 'duration', label: 'По длительности' },
] as const;

export type AccessTypeId = (typeof ACCESS_TYPES)[number]['id'];
export type CategoryId = (typeof CATEGORIES)[number]['id'];
export type ThemeId = (typeof AUDIO_THEMES)[number]['id'];
export type AudienceId = (typeof TARGET_AUDIENCE)[number]['id'];
export type SortId = (typeof SORT_OPTIONS)[number]['id'];
