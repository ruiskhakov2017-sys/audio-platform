/**
 * Все рассказы доступны для прослушивания без подписки (UI + плеер не блокируют).
 * Если в БД у части строк всё ещё is_premium = true, secure_stories_view отдаёт audio_url = null.
 * Тогда: либо UPDATE public.stories SET is_premium = false (см. supabase/set_all_stories_free.sql),
 * либо на сервере задан SUPABASE_SERVICE_ROLE_KEY — клиент догружает URL через GET /api/story-audio.
 */
export const ALL_STORIES_FREE_TO_LISTEN = true;
