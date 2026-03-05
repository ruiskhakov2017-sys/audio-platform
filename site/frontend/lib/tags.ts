import type { Story } from '@/types/story';
import { getDisplayTags } from '@/lib/stories';

export const getAllTags = (stories: Story[]) => {
  const set = new Set<string>();
  stories.forEach((story) => {
    getDisplayTags(story).forEach((tag) => set.add(tag));
  });
  return Array.from(set);
};

export const getUniqueCategories = (tags: string[]) => {
  // Mock categorization since tags are just strings
  const categories: Record<string, string[]> = {
    'Жанры': [],
    'Настроение': [],
    'Персонажи': []
  };

  tags.forEach(tag => {
    // Randomly assign for mock purposes or based on simple logic
    if (['BDSM', 'Roleplay', 'Hard'].includes(tag)) categories['Жанры'].push(tag);
    else if (['Romance', 'Relax', 'Sleep'].includes(tag)) categories['Настроение'].push(tag);
    else categories['Персонажи'].push(tag);
  });

  return categories;
};

export const filterStoriesByTags = (stories: Story[], selectedTags: string[], tagQuery: string = "") => {
  const query = (tagQuery || "").trim().toLowerCase();
  const hasSelected = selectedTags.length > 0;
  const hasQuery = query.length > 0;

  if (!hasSelected && !hasQuery) return stories;

  return stories.filter((story) => {
    const tagsLower = getDisplayTags(story).map((tag) => tag.toLowerCase());

    // OR logic for tags (if any selected tag is present)
    const matchesSelected = !hasSelected || selectedTags.some((tag) => tagsLower.includes(tag.toLowerCase()));

    // Search in tags
    const matchesQuery = !hasQuery || tagsLower.some((tag) => tag.includes(query));

    return matchesSelected && matchesQuery; // Using AND between filter types if needed, or modify logic based on requirement
  });
};
