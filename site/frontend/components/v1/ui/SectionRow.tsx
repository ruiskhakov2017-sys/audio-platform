import type { Story } from '@/types/story';
import StoryCard from '@/components/v1/ui/StoryCard';

type Props = {
  title: string;
  stories: Story[];
};

export default function SectionRow({ title, stories }: Props) {
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white md:text-2xl">{title}</h2>
      </div>
      <div className="flex gap-4 overflow-x-auto pb-2">
        {stories.map((story) => (
          <div key={story.slug} className="min-w-[200px] max-w-[240px] flex-1">
            <StoryCard story={story} />
          </div>
        ))}
      </div>
    </section>
  );
}
