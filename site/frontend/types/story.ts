export type Story = {
  id: number;
  slug: string;
  title: string;
  description: string;
  authorName: string;
  coverImage: string;
  audioSrc: string;
  durationSec: number;
  isPremium: boolean;
  /** Жанры (отдельно в БД) */
  genres: string[];
  /** Теги (отдельно в БД). Для отображения используй [...genres, ...tags] */
  tags: string[];
  /** Текст рассказа (если есть в БД) */
  content?: string;
  /** Количество прослушиваний (для Топ-100 и т.п.) */
  listensCount?: number;
}
