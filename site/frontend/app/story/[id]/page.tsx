'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { useParams, useRouter } from 'next/navigation';
import { Header } from '@/components/layout/Header';
import { supabase } from '@/lib/supabase';
import { mapRowToStory, getDisplayTags } from '@/lib/stories';
import { fetchStoriesFromApi, fetchStoryByIdFromApi, fetchRelatedStoriesFromApi, useDjangoApi } from '@/lib/api';
import { incrementListensCount } from '@/app/actions/catalog';
import { usePlayerStore } from '@/store/playerStore';
import { useFavoritesStore } from '@/store/favoritesStore';
import { useAuthStore } from '@/store/authStore';
import { toggleFavoriteApi } from '@/lib/favoritesApi';
import { fetchReviewsByStoryId, submitReviewApi, type ReviewItem } from '@/lib/reviewsApi';
import { toast } from 'sonner';
import { Play, Pause, Heart, Share2, SkipBack, SkipForward, Lock, Star } from 'lucide-react';
import type { Story } from '@/types/story';

const formatDuration = (sec: number) => {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
};

function getSimilarStories(current: Story, all: Story[], limit: number): Story[] {
  const byTag = all.filter(
    (s) => s.id !== current.id && s.tags.some((t) => current.tags.includes(t))
  );
  const rest = all.filter((s) => s.id !== current.id && !byTag.includes(s));
  return [...byTag, ...rest].slice(0, limit);
}

const DESCRIPTION_LINE_CLAMP = 4;
const TEST_AUDIO_SRC = '/audio/test.mp3';

export default function StoryPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const idParam = params.id ?? '';
  const [story, setPageStory] = useState<Story | null | undefined>(undefined);
  const [similar, setSimilar] = useState<Story[]>([]);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [reviewForm, setReviewForm] = useState({ rating: 5, text: '' });
  const [submittingReview, setSubmittingReview] = useState(false);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const hasIncrementedView = useRef(false);

  useEffect(() => {
    if (idParam === '') {
      setPageStory(null);
      return;
    }
    if (useDjangoApi()) {
      Promise.all([fetchStoryByIdFromApi(idParam), fetchStoriesFromApi()]).then(
        ([current, all]) => {
          setPageStory(current ?? null);
          if (current) {
            fetchRelatedStoriesFromApi(current.slug).then((related) => {
              setSimilar(related.length > 0 ? related : getSimilarStories(current, all, 8));
            });
          }
        }
      );
      return;
    }
    if (!supabase) {
      setPageStory(null);
      return;
    }
    supabase
      .from('stories')
      .select('*')
      .order('created_at', { ascending: false })
      .then(({ data, error }) => {
        if (error || !data) {
          setPageStory(null);
          return;
        }
        const all = data.map(mapRowToStory);
        const current = all.find((s) => String(s.id) === String(idParam)) ?? null;
        setPageStory(current);
        if (current) setSimilar(getSimilarStories(current, all, 8));
      });
  }, [idParam]);

  useEffect(() => {
    if (!story || typeof story.id !== 'number' || useDjangoApi() || hasIncrementedView.current) return;
    hasIncrementedView.current = true;
    incrementListensCount(story.id).catch(() => {});
  }, [story?.id]);

  useEffect(() => {
    if (!story || typeof story.id !== 'number' || !useDjangoApi() || !process.env.NEXT_PUBLIC_API_URL) return;
    fetchReviewsByStoryId(story.id).then(setReviews);
  }, [story?.id]);

  const {
    currentTrack,
    isPlaying,
    position,
    duration,
    seek,
    togglePlay,
    setStory,
    play,
    next,
    previous,
    isPremiumUser,
  } = usePlayerStore();
  const { toggleLike, isLiked } = useFavoritesStore();
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);

  const isPremiumLocked = story?.isPremium && !isPremiumUser;
  const isFavorite = story ? isLiked(String(story.id)) : false;

  const isCurrentTrack = Boolean(
    currentTrack && story && String(currentTrack.id) === String(story.id)
  );
  const isCurrentStoryPlaying = isCurrentTrack && isPlaying;
  const displayDuration = duration > 0 ? duration : (story?.durationSec ?? 0);
  const progress = displayDuration > 0 ? (position / displayDuration) * 100 : 0;

  const handlePlay = () => {
    if (!story) return;
    if (story.isPremium && !isPremiumUser) {
      router.push('/pricing');
      return;
    }
    const storyWithSrc = {
      ...story,
      audioSrc: story.audioSrc?.trim() || TEST_AUDIO_SRC,
    };
    if (isCurrentTrack) {
      togglePlay();
    } else {
      if (!useDjangoApi() && typeof story.id === 'number') {
        incrementListensCount(story.id).catch(() => {});
      }
      const queue = [
        storyWithSrc,
        ...similar.map((s) => ({
          ...s,
          audioSrc: s.audioSrc?.trim() || TEST_AUDIO_SRC,
        })),
      ];
      play(storyWithSrc, queue);
    }
  };

  const handleSeekRange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const pct = Number(e.target.value) / 100;
    seek(pct * displayDuration);
  };

  if (story === undefined) {
    return (
      <div className="min-h-screen bg-[#000814]">
        <Header />
        <div className="pt-24 flex items-center justify-center min-h-[50vh]">
          <p className="text-zinc-400">Загрузка...</p>
        </div>
      </div>
    );
  }

  if (story === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#000814]">
        <div className="text-center text-white">
          <h1 className="text-2xl font-bold">История не найдена</h1>
          <Link href="/browse" className="mt-4 inline-block text-[#00B4D8] hover:underline">
            Вернуться в каталог
          </Link>
        </div>
      </div>
    );
  }

  const descriptionLong = story.description.length > 200;
  const showExpandButton = descriptionLong && !descriptionExpanded;
  const avgRating = reviews.length > 0
    ? reviews.reduce((s, r) => s + r.rating, 0) / reviews.length
    : 0;
  const handleSubmitReview = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!story || submittingReview || !isAuthenticated) return;
    setSubmittingReview(true);
    const result = await submitReviewApi({
      story: Number(story.id),
      rating: reviewForm.rating,
      text: reviewForm.text.trim(),
    });
    setSubmittingReview(false);
    if ('error' in result) {
      toast.error(result.error);
      return;
    }
    setReviews((prev) => [result, ...prev]);
    setReviewForm({ rating: 5, text: '' });
    toast.success('Отзыв добавлен');
  };

  return (
    <div className="min-h-screen bg-[#000814] text-white">
      <Header />

      <div className="fixed inset-0 z-0">
        <Image
          src={story.coverImage}
          alt=""
          fill
          className="object-cover blur-2xl scale-110 opacity-30"
          priority
          unoptimized
        />
        <div className="absolute inset-0 bg-[#000814]/80" />
      </div>

      <main className="relative z-10 pt-24 pb-20 px-4 sm:px-6">
        <div className="max-w-[95%] mx-auto">
          {/* Split: слева фото, справа текст + Play. Чистый минимализм. */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
            {/* Колонка слева — обложка, острые углы */}
            <div className="w-full max-w-[550px] md:max-w-[650px] mx-auto lg:max-w-none lg:mx-0">
              <div className="relative w-full aspect-[3/4] rounded-sm overflow-hidden">
                <Image
                  src={story.coverImage}
                  alt={story.title}
                  fill
                  className="object-cover"
                  priority
                  unoptimized
                  sizes="(max-width: 1024px) 650px, 50vw"
                />
              </div>
            </div>

            {/* Колонка справа — на чёрном фоне, без карточек */}
            <div>
              <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <div>
                  <h1 className="font-heading text-3xl md:text-4xl font-bold text-white leading-tight">
                    {story.title}
                  </h1>
                  {reviews.length > 0 && (
                    <div className="flex items-center gap-1.5 mt-2" aria-label={`Рейтинг: ${avgRating.toFixed(1)} из 5`}>
                      {[1, 2, 3, 4, 5].map((i) => (
                        <Star
                          key={i}
                          className={`w-5 h-5 ${i <= Math.round(avgRating) ? 'text-amber-400 fill-amber-400' : 'text-zinc-600'}`}
                          strokeWidth={1.5}
                        />
                      ))}
                      <span className="text-sm text-zinc-500 ml-1">
                        {avgRating.toFixed(1)} ({reviews.length})
                      </span>
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={async () => {
                      if (!story) return;
                      const useApi = Boolean(process.env.NEXT_PUBLIC_API_URL && typeof window !== 'undefined' && localStorage.getItem('auth_access_token'));
                      if (useApi) {
                        const res = await toggleFavoriteApi(story.slug);
                        if (res) toggleLike(String(story.id));
                      } else {
                        toggleLike(String(story.id));
                      }
                    }}
                    className={`p-2 transition-colors rounded-full hover:bg-white/10 ${isFavorite ? 'text-cyan-500' : 'text-zinc-500 hover:text-white'}`}
                    aria-label={isFavorite ? 'Убрать из избранного' : 'В избранное'}
                  >
                    <Heart
                      className="w-5 h-5"
                      strokeWidth={1.5}
                      fill={isFavorite ? 'currentColor' : 'none'}
                    />
                  </button>
                  <button
                    type="button"
                    className="p-2 text-zinc-500 hover:text-white transition-colors"
                    aria-label="Поделиться"
                  >
                    <Share2 className="w-5 h-5" strokeWidth={1.5} />
                  </button>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 mb-8">
                {getDisplayTags(story).slice(0, 5).map((tag) => (
                  <Link
                    key={tag}
                    href={`/browse?tag=${encodeURIComponent(tag)}`}
                    className="rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-sm text-zinc-400 no-underline hover:bg-zinc-800 hover:text-zinc-300 transition-colors"
                  >
                    #{tag}
                  </Link>
                ))}
              </div>

              {/* Плеер: глобальный стор (continuous playback при навигации) */}
              <div className="flex justify-between text-xs text-zinc-500 tabular-nums mb-1">
                <span>{formatDuration(Math.floor(position))}</span>
                <span>{formatDuration(displayDuration)}</span>
              </div>
              <div className="relative h-2 w-full rounded-full bg-white/10 overflow-hidden mb-6">
                <div
                  className="absolute inset-y-0 left-0 rounded-full bg-white/60 transition-all duration-150 pointer-events-none"
                  style={{ width: `${progress}%` }}
                />
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={progress}
                  onChange={handleSeekRange}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  aria-label="Перемотка"
                />
              </div>
              <div className="flex items-center justify-center gap-4">
                <button
                  type="button"
                  onClick={() => previous()}
                  className="flex h-10 w-10 items-center justify-center rounded-full text-zinc-500 hover:text-white transition-colors disabled:opacity-40 disabled:pointer-events-none"
                  aria-label="Предыдущий трек"
                  disabled={!currentTrack}
                >
                  <SkipBack className="w-5 h-5" strokeWidth={1.5} />
                </button>
                <button
                  type="button"
                  onClick={handlePlay}
                  disabled={!story}
                  className={`flex h-16 w-16 items-center justify-center rounded-full border shadow-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${isCurrentStoryPlaying ? 'animate-pulse' : ''} ${
                    isPremiumLocked
                      ? 'border-amber-500/50 bg-amber-500/20 text-amber-400 hover:bg-amber-500/30'
                      : 'border-white/20 bg-white/10 text-white hover:bg-white/20'
                  }`}
                  aria-label={isPremiumLocked ? 'Premium — получить доступ' : isCurrentStoryPlaying ? 'Пауза' : 'Воспроизведение'}
                >
                  {isPremiumLocked ? (
                    <Lock className="w-8 h-8" strokeWidth={2} />
                  ) : isCurrentStoryPlaying ? (
                    <Pause className="w-8 h-8" strokeWidth={2} />
                  ) : (
                    <Play className="w-8 h-8 ml-0.5" strokeWidth={2} fill="currentColor" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => next()}
                  className="flex h-10 w-10 items-center justify-center rounded-full text-zinc-500 hover:text-white transition-colors disabled:opacity-40 disabled:pointer-events-none"
                  aria-label="Следующий трек"
                  disabled={!currentTrack}
                >
                  <SkipForward className="w-5 h-5" strokeWidth={1.5} />
                </button>
              </div>

              {/* Описание — Montserrat, отступ сверху */}
              <div className="mt-12">
                <p
                  className={`font-sans text-zinc-400 text-lg leading-relaxed ${showExpandButton ? 'line-clamp-4' : ''}`}
                >
                  {story.description}
                </p>
                {showExpandButton && (
                  <button
                    type="button"
                    onClick={() => setDescriptionExpanded(true)}
                    className="mt-2 text-sm text-zinc-500 hover:text-white transition-colors"
                  >
                    Развернуть
                  </button>
                )}
              </div>

              {/* Отзывы — только при наличии API */}
              {process.env.NEXT_PUBLIC_API_URL && (
                <section className="mt-12">
                  <h2 className="font-heading text-xl font-bold text-white mb-4">Отзывы</h2>
                  {isAuthenticated && (
                    <form onSubmit={handleSubmitReview} className="mb-8">
                      <div className="flex flex-wrap gap-4 items-center mb-3">
                        <label className="text-zinc-400 text-sm">Оценка:</label>
                        <div className="flex gap-1">
                          {[1, 2, 3, 4, 5].map((i) => (
                            <button
                              key={i}
                              type="button"
                              onClick={() => setReviewForm((f) => ({ ...f, rating: i }))}
                              className={`p-1 rounded ${reviewForm.rating >= i ? 'text-amber-400' : 'text-zinc-500 hover:text-zinc-400'}`}
                              aria-label={`Оценка ${i}`}
                            >
                              <Star className="w-5 h-5" strokeWidth={1.5} fill={reviewForm.rating >= i ? 'currentColor' : 'none'} />
                            </button>
                          ))}
                        </div>
                      </div>
                      <textarea
                        value={reviewForm.text}
                        onChange={(e) => setReviewForm((f) => ({ ...f, text: e.target.value }))}
                        placeholder="Текст отзыва (необязательно)"
                        className="w-full max-w-md rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none resize-y min-h-[80px]"
                        rows={3}
                      />
                      <button
                        type="submit"
                        disabled={submittingReview}
                        className="mt-3 px-5 py-2.5 rounded-full bg-[#00B4D8] text-black font-medium hover:bg-[#00B4D8]/90 disabled:opacity-50"
                      >
                        {submittingReview ? 'Отправка...' : 'Отправить отзыв'}
                      </button>
                    </form>
                  )}
                  <ul className="space-y-4">
                    {reviews.map((r) => (
                      <li key={r.id} className="border-b border-white/10 pb-4 last:border-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-zinc-400 text-sm">{r.user_email}</span>
                          <span className="flex gap-0.5">
                            {[1, 2, 3, 4, 5].map((i) => (
                              <Star key={i} className={`w-4 h-4 ${i <= r.rating ? 'text-amber-400 fill-amber-400' : 'text-zinc-600'}`} strokeWidth={1.5} />
                            ))}
                          </span>
                          <span className="text-zinc-500 text-xs">{new Date(r.created_at).toLocaleDateString('ru-RU')}</span>
                        </div>
                        {r.text && <p className="text-zinc-300 text-sm">{r.text}</p>}
                      </li>
                    ))}
                  </ul>
                  {reviews.length === 0 && <p className="text-zinc-500 text-sm">Пока нет отзывов.</p>}
                </section>
              )}
            </div>
          </div>

          {/* Похожие истории — на всю ширину под гридом */}
          <section className="mt-32 px-4 md:px-8 lg:px-12 xl:px-16">
            <h2 className="font-heading text-3xl md:text-4xl text-white text-center mb-12">
              Вам может понравиться
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
              {similar.map((s) => (
                <Link key={s.id} href={`/story/${s.id}`} className="block group w-full">
                  <div className="relative w-full aspect-[3/4] rounded-xl overflow-hidden border border-white/10 group-hover:border-[#00B4D8]/40 transition-colors bg-black">
                    <Image
                      src={s.coverImage}
                      alt={s.title}
                      fill
                      className="object-cover group-hover:scale-105 transition-transform duration-300"
                      unoptimized
                      sizes="(max-width: 768px) 50vw, 25vw"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent pointer-events-none" />
                    <p className="absolute bottom-2 left-2 right-2 text-white text-sm font-medium line-clamp-2">
                      {s.title}
                    </p>
                  </div>
                </Link>
              ))}
            </div>
            <div className="mt-16 mb-24 flex flex-col items-center justify-center text-center">
              <p className="text-zinc-500 mb-6 text-sm">Не нашли то, что искали?</p>
              <Link
                href="/browse"
                className="group flex items-center gap-2 px-8 py-4 rounded-full border border-zinc-800 bg-zinc-900/50 hover:bg-zinc-800 hover:border-zinc-600 transition-all duration-300"
              >
                <span className="text-white font-medium">Смотреть весь каталог</span>
                <svg className="w-4 h-4 text-zinc-400 group-hover:text-white group-hover:translate-x-1 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 8l4 4m0 0l-4 4m4-4H3" /></svg>
              </Link>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
