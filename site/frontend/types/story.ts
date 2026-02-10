export type Story = {
  id: number;
  slug: string;
  title: string;
  description: string;
  authorName: string;
  coverImage: string;
  audioSrc: string;
  durationSec: number;
  isPremium: boolean;
  tags: string[];
};
