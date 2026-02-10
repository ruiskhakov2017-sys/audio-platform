'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import Image from 'next/image';
import { MOCK_STORIES } from '@/data/mocks';
import { usePlayerStore } from '@/store/playerStore';
import { StoryCard } from '@/components/v1/ui/StoryCard';
import Breadcrumbs from '@/components/v1/ui/Breadcrumbs';
import { Play, Clock, User, Crown } from 'lucide-react';
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

export default function StoryPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const story = MOCK_STORIES.find((s) => s.id === id);
  const { setTrack, setIsPlaying, currentTrack, isPlaying } = usePlayerStore();

  if (!story) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#000814]">
        <div className="text-center text-white">
          <h1 className="text-2xl font-black tracking-tight">История не найдена</h1>
          <Link
            href="/browse"
            className="mt-4 inline-block text-[#00B4D8] no-underline hover:underline"
          >
            Вернуться в обзор
          </Link>
        </div>
      </div>
    );
  }

  const isCurrentTrack = currentTrack?.id === story.id;
  const isPlayingCurrent = isCurrentTrack && isPlaying;
  const similar = getSimilarStories(story, MOCK_STORIES, 4);

  const handlePlay = () => {
    if (isCurrentTrack) {
      setIsPlaying(!isPlaying);
    } else {
      setTrack(story);
      setIsPlaying(true);
    }
  };

  return (
    <div className="min-h-screen bg-[#000814] pb-24 pt-4 text-white">
      <div className="mx-auto w-full max-w-6xl px-6 sm:px-8 lg:px-12">
        <Breadcrumbs
          items={[
            { href: '/', label: 'Главная' },
            { href: '/browse', label: 'Каталог' },
            { label: story.title },
          ]}
        />
      </div>

      {/* Hero: full-bleed blurred background */}
      <div className="relative mx-auto w-full max-w-6xl px-6 py-8 sm:px-8 lg:px-12">
        <div className="relative overflow-hidden rounded-[2.5rem] border border-white/5">
          <div className="absolute inset-0">
            <Image
              src={story.coverImage}
              alt=""
              fill
              className="object-cover opacity-40 blur-2xl scale-110"
              sizes="(max-width: 1024px) 100vw, 1152px"
              priority
            />
            <div
              className="absolute inset-0"
              style={{
                background: 'linear-gradient(to top, #000814 0%, transparent 35%, rgba(0,8,20,0.6) 70%, #000814 100%)',
              }}
            />
          </div>

          <div className="relative grid gap-8 lg:grid-cols-[minmax(280px,400px)_1fr] lg:gap-12 p-6 md:p-10">
            <div className="relative aspect-[3/4] w-full max-w-md overflow-hidden rounded-[2.5rem] border border-white/5 lg:aspect-square">
              <Image
                src={story.coverImage}
                alt={story.title}
                fill
                className="object-cover"
                priority
              />
              {story.isPremium && (
                <div
                  className="absolute left-3 top-3 flex items-center gap-1.5 rounded-xl border border-[#00B4D8]/30 px-2.5 py-1 text-xs font-black uppercase text-zinc-900"
                  style={{
                    background: 'linear-gradient(180deg, rgba(212,175,55,0.95) 0%, rgba(160,130,40,0.98) 100%)',
                    boxShadow: '0 0 12px rgba(0,180,216,0.2)',
                  }}
                >
                  <Crown className="h-3.5 w-3.5" strokeWidth={1.5} />
                  PREMIUM
                </div>
              )}
            </div>

            <div className="flex flex-col justify-center">
              <h1 className="text-3xl font-black tracking-tight text-white md:text-4xl lg:text-5xl">
                {story.title}
              </h1>
              <p className="mt-3 flex items-center gap-2 text-sm text-white/60">
                <User className="h-4 w-4" strokeWidth={1.5} />
                Диктор: {story.authorName}
              </p>
              <ul className="mt-4 flex flex-wrap gap-4 text-sm text-white/50">
                <li className="flex items-center gap-2">
                  <Clock className="h-4 w-4" strokeWidth={1.5} />
                  Длительность: {formatDuration(story.durationSec)}
                </li>
              </ul>

              {/* Светящиеся теги-пузырьки */}
              <div className="mt-6 flex flex-wrap gap-2">
                {story.tags.slice(0, 6).map((tag) => (
                  <Link
                    key={tag}
                    href={`/browse?tag=${encodeURIComponent(tag)}`}
                    className="rounded-full border border-[#00B4D8]/40 bg-[#00B4D8]/15 px-4 py-2 text-sm font-medium text-[#00B4D8] no-underline shadow-[0_0_20px_rgba(0,180,216,0.2)] transition-all hover:border-[#00B4D8]/60 hover:shadow-[0_0_28px_rgba(0,180,216,0.35)]"
                  >
                    #{tag}
                  </Link>
                ))}
              </div>

              <div className="mt-8 rounded-[2.5rem] border border-white/5 bg-[#001d3d]/60 p-6 backdrop-blur-[40px]">
                <p className="mb-4 text-sm text-white/50">
                  {story.isPremium ? 'Премиум-запись' : 'Бесплатная сессия'}
                </p>
                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                    onClick={handlePlay}
                    className="flex items-center justify-center gap-2 rounded-2xl border-2 border-[#00B4D8] bg-[#00B4D8]/20 px-8 py-4 font-black text-[#00B4D8] shadow-[0_0_30px_rgba(0,180,216,0.3)] transition-all hover:shadow-[0_0_40px_rgba(0,180,216,0.45)]"
                  >
                    {isPlayingCurrent ? (
                      <>
                        <span className="h-3 w-3 rounded-full bg-[#00B4D8]" />
                        Пауза
                      </>
                    ) : (
                      <>
                        <Play className="h-5 w-5 fill-[#00B4D8]" strokeWidth={1.5} />
                        Слушать
                      </>
                    )}
                  </button>
                  <Link
                    href="/library"
                    className="flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-8 py-4 font-bold text-white/90 no-underline transition-colors hover:border-[#00B4D8]/30 hover:text-[#00B4D8]"
                  >
                    В коллекцию
                  </Link>
                </div>
                <p className="mt-3 text-xs text-white/40">
                  После добавления запись появится в разделе «Моя коллекция».
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="mx-auto w-full max-w-6xl px-6 sm:px-8 lg:px-12">
        <section className="mt-12 max-w-3xl">
          <h2 className="mb-3 text-lg font-black tracking-tight text-white">
            Описание
          </h2>
          <p className="leading-relaxed text-white/70">
            {story.description}
          </p>
        </section>

        <section className="mt-8 max-w-3xl">
          <h2 className="mb-2 text-lg font-black tracking-tight text-white">
            Об авторе
          </h2>
          <p className="text-sm leading-relaxed text-white/50">
            {story.authorName} — автор аудио-повествований в жанрах {story.tags.join(', ')}.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="mb-6 text-xl font-black tracking-tight text-white">
            Похожие записи
          </h2>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {similar.map((s) => (
              <StoryCard key={s.id} story={s} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
