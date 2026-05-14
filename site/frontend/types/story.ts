export type Story = {
  id: number;
  rawId?: string;
  slug: string;
  title: string;
  description: string;
  authorName: string;
  coverImage: string;
  audioSrc: string;
  durationSec: number;
  isPremium: boolean;
  genres: string[];
  tags: string[];
  listensCount?: number;
}
