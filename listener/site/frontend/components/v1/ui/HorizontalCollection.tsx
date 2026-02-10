'use client';

import Link from 'next/link';
import { StoryCard } from '@/components/v1/ui/StoryCard';
import type { Story } from '@/types/story';

type HorizontalCollectionProps = {
  title: string;
  subtitle?: string;
  stories: Story[];
  href?: string;
};

export default function HorizontalCollection({
  title,
  subtitle,
  stories,
  href = '/browse',
}: HorizontalCollectionProps) {
  return (
    <section className="mb-10">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-xl font-bold md:text-2xl" style={{ color: '#f1f5f9' }}>
            {title}
          </h2>
          {subtitle && (
            <p className="mt-0.5 text-sm" style={{ color: '#94a3b8' }}>
              {subtitle}
            </p>
          )}
        </div>
        {stories.length > 0 && (
          <Link
            href={href}
            className="text-sm font-medium transition-opacity hover:opacity-90"
            style={{ color: '#93c5fd', textDecoration: 'none' }}
          >
            Смотреть всё
          </Link>
        )}
      </div>
      <div className="flex gap-4 overflow-x-auto pb-2 scrollbar-thin md:gap-6">
        {stories.length > 0 ? (
          stories.map((story) => (
            <div key={story.id} className="w-[180px] shrink-0 md:w-[200px]">
              <StoryCard story={story} />
            </div>
          ))
        ) : (
          <p className="py-6 text-sm" style={{ color: '#64748b' }}>
            Пока пусто
          </p>
        )}
      </div>
    </section>
  );
}
