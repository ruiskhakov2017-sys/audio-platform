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
import { ALL_STORIES_FREE_TO_LISTEN } from '@/config/access';
import { toast } from 'sonner';
import { Play, Pause, Heart, Share2, SkipBack, SkipForward, Lock, Star, ArrowLeft, ThumbsUp, Flag, X } from 'lucide-react';
import type { Story } from '@/types/story';

const formatDuration = (value: number) => {
  const totalSeconds = Math.max(0, Math.floor(value || 0));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  if (hours > 0) {
    return `${hours} ч. ${minutes} мин.`;
  }
  return `${Math.max(1, minutes)} мин.`;
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
const AUTH_REQUIRED_MSG =
  'Пожалуйста, войдите в аккаунт или зарегистрируйтесь, чтобы оставлять отзывы и оценивать рассказы';

const MOCK_REVIEWS = [
  {
    id: 'mock-1',
    name: 'Анна К.',
    initials: 'АК',
    gradient: 'from-violet-500 to-purple-600',
    rating: 5,
    text: 'Атмосфера просто невероятная! Голос рассказчика затягивает с первых секунд. Слушала поздно вечером — мурашки по коже. Один из лучших рассказов на платформе.',
    date: '14 марта 2026',
  },
  {
    id: 'mock-2',
    name: 'Михаил В.',
    initials: 'МВ',
    gradient: 'from-cyan-500 to-blue-600',
    rating: 4,
    text: 'Хорошая озвучка, сюжет держит интригу до конца. Немного не хватило финала, но в целом очень достойно. Рекомендую любителям жанра.',
    date: '10 марта 2026',
  },
  {
    id: 'mock-3',
    name: 'Елена Р.',
    initials: 'ЕР',
    gradient: 'from-rose-500 to-pink-600',
    rating: 5,
    text: 'Переслушала уже трижды. Потрясающая работа! Хочется, чтобы таких рассказов было больше.',
    date: '5 марта 2026',
  },
];

const REPORT_REASONS = [
  { value: 'spam', label: 'Спам' },
  { value: 'inappropriate', label: 'Неприемлемый контент' },
  { value: 'audio_error', label: 'Ошибка озвучки' },
  { value: 'other', label: 'Другое' },
];

export default function StoryPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const idParam = params.id ?? '';
  const [story, setPageStory] = useState<Story | null | undefined>(undefined);
  const [similar, setSimilar] = useState<Story[]>([]);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [reviewForm, setReviewForm] = useState({ rating: 5, text: '' });
  const [submittingReview, setSubmittingReview] = useState(false);
  const [likeCount, setLikeCount] = useState(245);
  const [liked, setLiked] = useState(false);
  const [reportModalOpen, setReportModalOpen] = useState(false);
  const [reportForm, setReportForm] = useState({ reason: 'spam', details: '' });
  const [submittingReport, setSubmittingReport] = useState(false);
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
      .from('secure_stories_view')
      .select('*')
      .order('created_at', { ascending: false })
      .then(({ data, error }) => {
        if (error || !data) {
          setPageStory(null);
          return;
        }
        const all = data.map(mapRowToStory);
        const current = all.find((s) => String(s.id) === String(idParam) || String(s.slug) === String(idParam)) ?? null;
        setPageStory(current);
        if (current) setSimilar(getSimilarStories(current, all, 8));
      });
  }, [idParam]);

  useEffect(() => {
    if (!story || useDjangoApi() || hasIncrementedView.current) return;
    hasIncrementedView.current = true;
    const dbId = story.rawId ?? story.id;
    incrementListensCount(dbId).catch(() => {});
  }, [story?.id, story?.rawId]);

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

  const isPremiumLocked = !ALL_STORIES_FREE_TO_LISTEN && story?.isPremium && !isPremiumUser;
  const isFavorite = story ? isLiked(String(story.id)) : false;

  const isCurrentTrack = Boolean(
    currentTrack && story && String(currentTrack.id) === String(story.id)
  );
  const isCurrentStoryPlaying = isCurrentTrack && isPlaying;
  const displayDuration = duration > 0 ? duration : (story?.durationSec ?? 0);
  const progress = displayDuration > 0 ? (position / displayDuration) * 100 : 0;

  const handlePlay = () => {
    if (!story) return;
    if (!ALL_STORIES_FREE_TO_LISTEN && story.isPremium && !isPremiumUser) {
      router.push('/pricing');
      return;
    }
    const fallbackAudio = ALL_STORIES_FREE_TO_LISTEN ? '' : TEST_AUDIO_SRC;
    const storyWithSrc = {
      ...story,
      audioSrc: story.audioSrc?.trim() || fallbackAudio,
    };
    if (isCurrentTrack) {
      togglePlay();
    } else {
      if (!useDjangoApi() && typeof story.id === 'number') {
        incrementListensCount(story.id).catch(() => { });
      }
      const queue = [
        storyWithSrc,
        ...similar.map((s) => ({
          ...s,
          audioSrc: s.audioSrc?.trim() || fallbackAudio,
        })),
      ];
      play(storyWithSrc, queue);
    }
  };

  const handleSeekRange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const pct = Number(e.target.value) / 100;
    seek(pct * displayDuration);
  };

  const handleLike = () => {
    if (!isAuthenticated) {
      toast.error(AUTH_REQUIRED_MSG);
      return;
    }
    setLiked((prev) => !prev);
    setLikeCount((prev) => (liked ? prev - 1 : prev + 1));
  };

  const handleReport = () => {
    if (!isAuthenticated) {
      toast.error(AUTH_REQUIRED_MSG);
      return;
    }
    setReportModalOpen(true);
  };

  const handleSubmitReport = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmittingReport(true);
    console.log('Жалоба:', { story: story?.id, ...reportForm });
    setTimeout(() => {
      setSubmittingReport(false);
      setReportModalOpen(false);
      setReportForm({ reason: 'spam', details: '' });
      toast.success('Жалоба отправлена модераторам');
    }, 600);
  };

  const handleShare = async () => {
    const shareUrl = typeof window !== 'undefined' ? window.location.href : '';
    const shareData = {
      title: story?.title ?? '',
      text: `Слушай рассказ: ${story?.title ?? ''}`,
      url: shareUrl,
    };

    try {
      if (typeof navigator !== 'undefined' && navigator.share) {
        await navigator.share(shareData);
        return;
      }
      if (typeof navigator !== 'undefined' && navigator.clipboard && shareUrl) {
        await navigator.clipboard.writeText(shareUrl);
        toast.success('Ссылка скопирована');
      }
    } catch {
      toast.error('Не удалось поделиться');
    }
  };

  const handleSubmitReview = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isAuthenticated) {
      toast.error(AUTH_REQUIRED_MSG);
      return;
    }
    if (!story || submittingReview) return;
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

  const displayedReviews = reviews.length > 0 ? reviews : null;

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
        <button
          type="button"
          onClick={() => router.back()}
          className="absolute top-6 left-6 md:top-24 md:left-8 z-50 flex items-center gap-2 text-zinc-400 hover:text-white transition-colors group"
        >
          <div className="w-10 h-10 rounded-full bg-black/40 backdrop-blur-md flex items-center justify-center border border-white/10 group-hover:border-white/30 transition-all">
            <ArrowLeft className="w-5 h-5" />
          </div>
          <span className="text-sm font-medium hidden md:inline-block opacity-0 group-hover:opacity-100 -translate-x-2 group-hover:translate-x-0 transition-all duration-300">Назад</span>
        </button>

        <div className="max-w-[95%] mx-auto mt-8 md:mt-0">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-start">
            {/* Колонка слева — обложка */}
            <div className="hidden lg:block w-full max-w-[550px] md:max-w-[650px] mx-auto lg:max-w-none lg:mx-0 sticky top-32">
              <div className="relative w-full aspect-[3/4] rounded-sm overflow-hidden shadow-2xl">
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

            {/* Колонка справа */}
            <div className="flex flex-col gap-6 pt-4">
              {/* Заголовок */}
              <h1 className="font-heading text-4xl md:text-5xl lg:text-6xl font-bold text-white leading-tight">
                {story.title}
              </h1>

              {/* Жанры и Теги */}
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap gap-2">
                  {getDisplayTags(story).slice(0, 3).map((tag) => (
                    <Link
                      key={tag}
                      href={`/browse?genre=${encodeURIComponent(tag)}`}
                      className="bg-blue-600/20 text-blue-400 border border-blue-500/30 px-3 py-1 rounded-full uppercase text-xs tracking-wider font-semibold hover:bg-blue-600/30 transition-colors"
                    >
                      {tag}
                    </Link>
                  ))}
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {getDisplayTags(story).slice(3).map((tag) => (
                    <Link
                      key={tag}
                      href={`/browse?tag=${encodeURIComponent(tag)}`}
                      className="text-white/60 hover:text-white transition-colors text-sm"
                    >
                      #{tag}
                    </Link>
                  ))}
                </div>
              </div>

              {/* Мобильная обложка */}
              <div className="block lg:hidden w-full max-w-[550px] mx-auto mb-2">
                <div className="relative w-full aspect-[3/4] rounded-sm overflow-hidden shadow-2xl">
                  <Image
                    src={story.coverImage}
                    alt={story.title}
                    fill
                    className="object-cover"
                    priority
                    unoptimized
                    sizes="(max-width: 1024px) 100vw, 50vw"
                  />
                </div>
              </div>

              {/* Рейтинг + Action Bar */}
              <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <div>
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

                {/* Кнопки действий */}
                <div className="flex items-center gap-1">
                  {/* В избранное */}
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

                  {/* Лайк */}
                  <button
                    type="button"
                    onClick={handleLike}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded-full transition-all ${
                      liked
                        ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/40'
                        : 'text-zinc-500 hover:text-white hover:bg-white/10'
                    }`}
                    aria-label="Лайк"
                  >
                    <ThumbsUp
                      className="w-4 h-4"
                      strokeWidth={1.5}
                      fill={liked ? 'currentColor' : 'none'}
                    />
                    <span className="text-xs font-medium tabular-nums">{likeCount}</span>
                  </button>

                  {/* Поделиться */}
                  <button
                    type="button"
                    onClick={handleShare}
                    className="p-2 text-zinc-500 hover:text-[#00B4D8] transition-colors cursor-pointer rounded-full hover:bg-white/10"
                    aria-label="Поделиться"
                  >
                    <Share2 className="w-5 h-5" strokeWidth={1.5} />
                  </button>

                  {/* Пожаловаться — менее броская */}
                  <button
                    type="button"
                    onClick={handleReport}
                    className="ml-2 flex items-center gap-1.5 px-2.5 py-2 rounded-full text-zinc-600 hover:text-red-400 hover:bg-red-400/10 transition-all"
                    aria-label="Пожаловаться"
                    title="Пожаловаться"
                  >
                    <Flag className="w-4 h-4" strokeWidth={1.5} />
                    <span className="text-xs hidden sm:inline">Жалоба</span>
                  </button>
                </div>
              </div>

              {/* Кнопка Play */}
              <div className="flex gap-4 mt-2">
                <button
                  onClick={handlePlay}
                  className="flex items-center justify-center w-16 h-16 md:w-20 md:h-20 rounded-full bg-cyan-500 hover:bg-cyan-400 text-white shadow-[0_0_40px_rgba(6,182,212,0.4)] hover:shadow-[0_0_60px_rgba(6,182,212,0.6)] hover:scale-105 transition-all duration-300 group"
                >
                  {isCurrentStoryPlaying ? (
                    <Pause className="w-8 h-8 md:w-10 md:h-10 fill-current" />
                  ) : (
                    <Play className="w-8 h-8 md:w-10 md:h-10 fill-current ml-1" />
                  )}
                </button>
                <div className="flex flex-col justify-center">
                  <span className="text-sm text-zinc-400 font-medium uppercase tracking-wider">Слушать рассказ</span>
                  <span className="text-white font-bold text-lg">{formatDuration(story.durationSec || 0)}</span>
                </div>
              </div>

              {/* Разделитель */}
              <div className="h-px bg-white/10 my-8" />

              {/* Описание */}
              <div className="prose prose-invert max-w-none text-zinc-300 leading-relaxed text-lg">
                {descriptionExpanded ? (
                  <p className="whitespace-pre-wrap">{story.description}</p>
                ) : (
                  <p className="whitespace-pre-wrap line-clamp-4">{story.description}</p>
                )}
                {showExpandButton && (
                  <button
                    onClick={() => setDescriptionExpanded(true)}
                    className="mt-2 text-[#00B4D8] hover:text-cyan-300 font-medium text-sm flex items-center gap-1"
                  >
                    Читать полностью
                  </button>
                )}
              </div>

              {/* ────────── ОТЗЫВЫ ────────── */}
              <div className="mt-4">
                <div className="h-px bg-white/10 mb-8" />
                <h2 className="font-heading text-2xl font-bold text-white mb-6">Отзывы</h2>

                {/* Форма — всегда видна */}
                <form onSubmit={handleSubmitReview} className="mb-10">
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
                    placeholder={isAuthenticated ? 'Напишите отзыв...' : 'Войдите, чтобы оставить отзыв...'}
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none resize-y min-h-[90px] transition-colors"
                    rows={3}
                  />
                  <button
                    type="submit"
                    disabled={submittingReview}
                    className="mt-3 px-6 py-2.5 rounded-full bg-[#00B4D8] text-black font-semibold hover:bg-[#00B4D8]/90 disabled:opacity-50 transition-all"
                  >
                    {submittingReview ? 'Отправка...' : 'Отправить отзыв'}
                  </button>
                </form>

                {/* Список отзывов */}
                <ul className="space-y-5">
                  {displayedReviews
                    ? displayedReviews.map((r) => (
                        <li key={r.id} className="flex gap-4 pb-5 border-b border-white/10 last:border-0">
                          <div className="flex-shrink-0 w-10 h-10 rounded-full bg-gradient-to-br from-zinc-600 to-zinc-700 flex items-center justify-center text-xs font-bold text-white">
                            {r.user_email.slice(0, 2).toUpperCase()}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                              <span className="text-white text-sm font-medium">{r.user_email}</span>
                              <span className="flex gap-0.5">
                                {[1, 2, 3, 4, 5].map((i) => (
                                  <Star key={i} className={`w-3.5 h-3.5 ${i <= r.rating ? 'text-amber-400 fill-amber-400' : 'text-zinc-600'}`} strokeWidth={1.5} />
                                ))}
                              </span>
                              <span className="text-zinc-500 text-xs">{new Date(r.created_at).toLocaleDateString('ru-RU')}</span>
                            </div>
                            {r.text && <p className="text-zinc-300 text-sm leading-relaxed">{r.text}</p>}
                          </div>
                        </li>
                      ))
                    : MOCK_REVIEWS.map((r) => (
                        <li key={r.id} className="flex gap-4 pb-5 border-b border-white/10 last:border-0">
                          <div className={`flex-shrink-0 w-10 h-10 rounded-full bg-gradient-to-br ${r.gradient} flex items-center justify-center text-xs font-bold text-white`}>
                            {r.initials}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                              <span className="text-white text-sm font-medium">{r.name}</span>
                              <span className="flex gap-0.5">
                                {[1, 2, 3, 4, 5].map((i) => (
                                  <Star key={i} className={`w-3.5 h-3.5 ${i <= r.rating ? 'text-amber-400 fill-amber-400' : 'text-zinc-600'}`} strokeWidth={1.5} />
                                ))}
                              </span>
                              <span className="text-zinc-500 text-xs">{r.date}</span>
                            </div>
                            <p className="text-zinc-300 text-sm leading-relaxed">{r.text}</p>
                          </div>
                        </li>
                      ))
                  }
                </ul>
              </div>
              {/* ────────── /ОТЗЫВЫ ────────── */}
            </div>
          </div>
        </div>

        {/* Похожие истории */}
        <section className="mt-32 px-4 md:px-8 lg:px-12 xl:px-16">
          <h2 className="font-heading text-3xl md:text-4xl text-white text-center mb-12">
            Вам может понравиться
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            {similar.map((s) => (
              <Link
                key={s.id}
                href={`/story/${s.id}`}
                className="relative overflow-hidden rounded-2xl group cursor-pointer transition-all duration-300 hover:-translate-y-2 hover:shadow-[0_15px_30px_rgba(0,180,216,0.15)] aspect-[3/4] block"
              >
                <Image
                  src={s.coverImage}
                  alt={s.title}
                  fill
                  className="object-cover transition-transform duration-500 group-hover:scale-105"
                  unoptimized
                  sizes="(max-width: 768px) 50vw, 25vw"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-black/95 via-black/50 to-transparent opacity-80 group-hover:opacity-100 transition-opacity duration-300" />
                <div className="absolute bottom-0 left-0 right-0 p-4 flex flex-col gap-2 transform transition-transform duration-300 group-hover:-translate-y-1">
                  <span className="inline-flex w-max px-2.5 py-1 rounded-md bg-white/10 backdrop-blur-md border border-white/20 text-[#00B4D8] text-[10px] md:text-xs uppercase tracking-wider font-semibold">
                    {getDisplayTags(s)[0] || 'Аудио'}
                  </span>
                  <h3 className="text-white font-bold text-lg leading-tight line-clamp-2">
                    {s.title}
                  </h3>
                </div>
              </Link>
            ))}
          </div>
          <div className="mt-16 mb-24 flex flex-col items-center justify-center text-center">
            <p className="text-zinc-500 mb-6 text-sm">Не нашли то, что искали?</p>
            <Link
              href="/browse"
              className="group inline-flex items-center justify-center gap-3 px-8 py-3 rounded-full bg-white/[0.03] backdrop-blur-md border border-white/10 transition-all duration-300 hover:bg-[#00B4D8]/10 hover:border-[#00B4D8]/50 hover:shadow-[0_0_20px_rgba(0,180,216,0.3)] hover:-translate-y-0.5"
            >
              <span className="text-white/80 font-semibold uppercase tracking-widest text-sm transition-colors duration-300 group-hover:text-[#00B4D8]">Смотреть весь каталог</span>
              <svg
                className="w-4 h-4 text-white/80 transition-all duration-300 group-hover:translate-x-1.5 group-hover:text-[#00B4D8]"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                aria-hidden
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 8l4 4m0 0l-4 4m4-4H3" />
              </svg>
            </Link>
          </div>
        </section>
      </main>

      {/* ────────── МОДАЛЬНОЕ ОКНО ЖАЛОБЫ ────────── */}
      {reportModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="report-modal-title"
        >
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            onClick={() => setReportModalOpen(false)}
          />

          {/* Modal */}
          <div className="relative z-10 w-full max-w-md bg-[#0d1b2a] border border-white/10 rounded-2xl shadow-2xl p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 id="report-modal-title" className="text-white font-bold text-lg">
                Жалоба на материал
              </h2>
              <button
                type="button"
                onClick={() => setReportModalOpen(false)}
                className="p-1.5 rounded-full text-zinc-500 hover:text-white hover:bg-white/10 transition-colors"
                aria-label="Закрыть"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSubmitReport} className="flex flex-col gap-4">
              <div>
                <label htmlFor="report-reason" className="block text-zinc-400 text-sm mb-2">
                  Причина жалобы
                </label>
                <select
                  id="report-reason"
                  value={reportForm.reason}
                  onChange={(e) => setReportForm((f) => ({ ...f, reason: e.target.value }))}
                  className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white focus:border-[#00B4D8]/50 focus:outline-none appearance-none cursor-pointer"
                >
                  {REPORT_REASONS.map((r) => (
                    <option key={r.value} value={r.value} className="bg-[#0d1b2a]">
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="report-details" className="block text-zinc-400 text-sm mb-2">
                  Подробности <span className="text-zinc-600">(необязательно)</span>
                </label>
                <textarea
                  id="report-details"
                  value={reportForm.details}
                  onChange={(e) => setReportForm((f) => ({ ...f, details: e.target.value }))}
                  placeholder="Опишите проблему подробнее..."
                  className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none resize-none min-h-[100px] transition-colors"
                  rows={4}
                />
              </div>

              <div className="flex gap-3 mt-1">
                <button
                  type="button"
                  onClick={() => setReportModalOpen(false)}
                  className="flex-1 px-4 py-2.5 rounded-full border border-white/10 text-zinc-400 hover:text-white hover:border-white/30 transition-all text-sm font-medium"
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  disabled={submittingReport}
                  className="flex-1 px-4 py-2.5 rounded-full bg-red-500/80 hover:bg-red-500 text-white font-semibold text-sm disabled:opacity-50 transition-all"
                >
                  {submittingReport ? 'Отправка...' : 'Отправить'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
