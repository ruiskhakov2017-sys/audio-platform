-- ============================================================
-- secure_stories_view.sql
-- Выполнить в Supabase Dashboard → SQL Editor → Run
-- ============================================================
-- Колонки таблицы stories (факт):
--   id, title, author, description, genre, tags, image_url,
--   audio_url, duration, is_premium, plays_count, created_at,
--   genres, listens_count, is_editors_choice, text_url
-- ============================================================

-- 1. Хелпер: текущий пользователь — премиум?
CREATE OR REPLACE FUNCTION public.is_premium_user()
RETURNS boolean
LANGUAGE sql STABLE
AS $$
  SELECT coalesce(
    (
      SELECT
        (claims -> 'user_metadata' ->> 'is_premium')::boolean = true
        AND (
          (claims -> 'user_metadata' ->> 'premiumUntil') IS NULL
          OR (claims -> 'user_metadata' ->> 'premiumUntil')::timestamptz > now()
        )
      FROM (
        SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb AS claims
      ) t
      WHERE claims IS NOT NULL
    ),
    false
  );
$$;

-- 2. Безопасное представление
CREATE OR REPLACE VIEW public.secure_stories_view AS
SELECT
  id,
  title,
  author,
  description,
  genre,
  tags,
  image_url,
  duration,
  is_premium,
  plays_count,
  created_at,
  genres,
  listens_count,
  is_editors_choice,
  text_url,
  CASE
    WHEN NOT is_premium           THEN audio_url
    WHEN public.is_premium_user() THEN audio_url
    ELSE NULL
  END AS audio_url
FROM public.stories;

-- 3. Доступ к view для anon / authenticated
GRANT SELECT ON public.secure_stories_view TO anon, authenticated;

-- 4. Отозвать прямой SELECT на stories (service_role обходит это)
REVOKE SELECT ON public.stories FROM anon, authenticated;

-- 5. RLS как дополнительный барьер
ALTER TABLE public.stories ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS deny_public_select ON public.stories;
CREATE POLICY deny_public_select ON public.stories
  FOR SELECT
  TO anon, authenticated
  USING (false);
