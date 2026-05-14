/**
 * Все рассказы доступны для прослушивания без подписки (UI + плеер не блокируют).
 * Для выдачи аудио из Supabase всё равно выполни в SQL Editor:
 *   UPDATE public.stories SET is_premium = false;
 * (см. supabase/set_all_stories_free.sql)
 */
export const ALL_STORIES_FREE_TO_LISTEN = true;
