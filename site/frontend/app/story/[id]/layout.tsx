import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { fetchStoryBySlugFromApi } from '@/lib/api';

const BRAND = 'Dirty Secrets';

type Props = {
  children: ReactNode;
  params: Promise<{ id: string }>;
};

function absoluteUrl(path: string): string {
  if (typeof path !== 'string' || !path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  const base = process.env.NEXT_PUBLIC_SITE_URL
    || (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : '');
  if (!base) return path;
  const origin = base.startsWith('http') ? base : `https://${base}`;
  return path.startsWith('/') ? `${origin}${path}` : `${origin}/${path}`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const story = await fetchStoryBySlugFromApi(id);
  if (!story) {
    return { title: `История | ${BRAND}` };
  }
  const description = story.description?.slice(0, 160).trim() || undefined;
  const ogImage = story.coverImage ? absoluteUrl(story.coverImage) : undefined;
  return {
    title: `${story.title} | ${BRAND}`,
    description,
    openGraph: {
      title: `${story.title} | ${BRAND}`,
      description,
      images: ogImage ? [{ url: ogImage, width: 1200, height: 630, alt: story.title }] : undefined,
    },
    twitter: {
      card: 'summary_large_image',
      title: `${story.title} | ${BRAND}`,
      description,
      images: ogImage ? [ogImage] : undefined,
    },
  };
}

export default function StoryIdLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
