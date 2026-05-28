-- English site initial schema bootstrap for Supabase
-- Target: https://umamgfcdgqgwebhqkntj.supabase.co
-- Source stories/data are NOT copied from old project.
-- This script creates structures only (tables/views/functions/policies).

begin;

create extension if not exists pgcrypto;

-- 1) Core content table expected by frontend + legacy autopublisher payload
create table if not exists public.stories (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  author text,
  description text,
  genre text,
  tags text[] default array[]::text[],
  image_url text,
  audio_url text,
  duration integer default 0,
  is_premium boolean not null default false,
  plays_count integer not null default 0,
  created_at timestamptz not null default now(),
  genres text[] default array[]::text[],
  listens_count integer not null default 0,
  is_editors_choice boolean not null default false,
  text_url text
);

create index if not exists stories_created_at_idx on public.stories (created_at desc);
create index if not exists stories_listens_count_idx on public.stories (listens_count desc, created_at desc);
create index if not exists stories_is_premium_idx on public.stories (is_premium);
create index if not exists stories_is_editors_choice_idx on public.stories (is_editors_choice);
create index if not exists stories_genres_gin_idx on public.stories using gin (genres);
create index if not exists stories_tags_gin_idx on public.stories using gin (tags);

alter table public.stories enable row level security;

-- NOTE:
-- Frontend currently has a few direct client-side reads from public.stories
-- (admin/search modal). Keep read policy open to avoid runtime breakage.
-- If you harden this later, migrate those reads to secure_stories_view first.
drop policy if exists stories_select_public on public.stories;
create policy stories_select_public
  on public.stories
  for select
  to anon, authenticated
  using (true);

-- Service role is used for server-side writes (autopublisher/admin actions).
-- Service role bypasses RLS by default, but we keep explicit policies for clarity.
drop policy if exists stories_insert_service on public.stories;
drop policy if exists stories_update_service on public.stories;
drop policy if exists stories_delete_service on public.stories;

create policy stories_insert_service
  on public.stories
  for insert
  to service_role
  with check (true);

create policy stories_update_service
  on public.stories
  for update
  to service_role
  using (true)
  with check (true);

create policy stories_delete_service
  on public.stories
  for delete
  to service_role
  using (true);

grant select on public.stories to anon, authenticated;
grant insert, update, delete on public.stories to service_role;

-- 2) Premium-aware secure view used by frontend catalog/story endpoints
create or replace function public.is_premium_user()
returns boolean
language sql
stable
as $$
  select coalesce(
    (
      select
        (claims -> 'user_metadata' ->> 'is_premium')::boolean = true
        and (
          (claims -> 'user_metadata' ->> 'premiumUntil') is null
          or (claims -> 'user_metadata' ->> 'premiumUntil')::timestamptz > now()
        )
      from (
        select nullif(current_setting('request.jwt.claims', true), '')::jsonb as claims
      ) t
      where claims is not null
    ),
    false
  );
$$;

create or replace view public.secure_stories_view as
select
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
  case
    when not is_premium then audio_url
    when public.is_premium_user() then audio_url
    else null
  end as audio_url
from public.stories;

grant select on public.secure_stories_view to anon, authenticated, service_role;

-- 3) Favorites table + RLS (required by get_most_saved_stories RPC)
create table if not exists public.user_favorites (
  user_id uuid not null references auth.users(id) on delete cascade,
  story_id uuid not null references public.stories(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, story_id)
);

create index if not exists user_favorites_user_idx on public.user_favorites (user_id);
create index if not exists user_favorites_story_idx on public.user_favorites (story_id);

alter table public.user_favorites enable row level security;

drop policy if exists user_favorites_select_own on public.user_favorites;
drop policy if exists user_favorites_insert_own on public.user_favorites;
drop policy if exists user_favorites_delete_own on public.user_favorites;

create policy user_favorites_select_own
  on public.user_favorites
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy user_favorites_insert_own
  on public.user_favorites
  for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy user_favorites_delete_own
  on public.user_favorites
  for delete
  to authenticated
  using (auth.uid() = user_id);

grant select, insert, delete on public.user_favorites to authenticated;

-- 4) RPCs referenced by frontend

create or replace function public.increment_story_listens(p_story_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.stories
     set listens_count = coalesce(listens_count, 0) + 1
   where id = p_story_id;
end;
$$;

revoke all on function public.increment_story_listens(uuid) from public;
grant execute on function public.increment_story_listens(uuid) to anon, authenticated, service_role;

create or replace function public.get_genre_rows(
  p_genres text[],
  p_limit int default 8
)
returns jsonb
language sql
stable
as $$
  select coalesce(
    jsonb_object_agg(g, coalesce(rows_json, '[]'::jsonb)),
    '{}'::jsonb
  )
  from unnest(p_genres) as g
  left join lateral (
    select jsonb_agg(to_jsonb(s.*) order by (s.listens_count) desc nulls last, s.created_at desc) as rows_json
    from (
      select *
      from public.secure_stories_view v
      where v.genres @> array[g]
      order by v.listens_count desc nulls last, v.created_at desc
      limit p_limit
    ) s
  ) t on true;
$$;

revoke all on function public.get_genre_rows(text[], int) from public;
grant execute on function public.get_genre_rows(text[], int) to anon, authenticated, service_role;

create or replace function public.get_most_saved_stories(p_limit int default 12)
returns setof public.secure_stories_view
language sql
stable
security definer
set search_path = public
as $$
  with counts as (
    select story_id, count(*)::int as saves
    from public.user_favorites
    group by story_id
    order by saves desc
    limit greatest(p_limit, 1)
  )
  select v.*
  from counts c
  join public.secure_stories_view v
    on v.id = c.story_id
  order by c.saves desc;
$$;

grant execute on function public.get_most_saved_stories(int) to anon, authenticated, service_role;

create or replace function public.get_catalog_scale()
returns table (
  stories_total int,
  premium_total int,
  free_total int,
  genres_total int,
  total_duration_sec bigint,
  added_last_week int
)
language sql
stable
security definer
set search_path = public
as $$
  with base as (
    select *
    from public.secure_stories_view
  ),
  genres_flat as (
    select distinct unnest(coalesce(genres, array[]::text[])) as genre
    from base
  )
  select
    (select count(*) from base)::int as stories_total,
    (select count(*) from base where is_premium = true)::int as premium_total,
    (select count(*) from base where is_premium = false)::int as free_total,
    (select count(*) from genres_flat where genre is not null and genre <> '')::int as genres_total,
    (select coalesce(sum(nullif(duration, 0)), 0)::bigint from base) as total_duration_sec,
    (select count(*) from base where created_at >= (now() - interval '7 days'))::int as added_last_week;
$$;

grant execute on function public.get_catalog_scale() to anon, authenticated, service_role;

commit;

-- Post-apply smoke check (run manually):
-- select * from public.secure_stories_view limit 5;
-- select * from public.get_catalog_scale();
-- select * from public.get_genre_rows(array['Romance'], 3);
