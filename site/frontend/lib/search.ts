import type { Story } from '@/types/story';

const EN_LAYOUT = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./";
const RU_LAYOUT = "ёйцукенгшщзхъфывапролджэячсмитьбю.";

const EN_TO_RU = new Map<string, string>();
const RU_TO_EN = new Map<string, string>();

for (let i = 0; i < EN_LAYOUT.length; i += 1) {
  EN_TO_RU.set(EN_LAYOUT[i], RU_LAYOUT[i]);
  RU_TO_EN.set(RU_LAYOUT[i], EN_LAYOUT[i]);
}

function mapChar(ch: string, map: Map<string, string>): string {
  const lower = ch.toLowerCase();
  const mapped = map.get(lower);
  if (!mapped) return ch;
  return ch === lower ? mapped : mapped.toUpperCase();
}

export function convertKeyboardLayout(value: string): string {
  return Array.from(value)
    .map((ch) => {
      if (EN_TO_RU.has(ch.toLowerCase())) return mapChar(ch, EN_TO_RU);
      if (RU_TO_EN.has(ch.toLowerCase())) return mapChar(ch, RU_TO_EN);
      return ch;
    })
    .join('');
}

function normalize(value: string): string {
  return value.trim().toLowerCase();
}

export function getSearchVariants(query: string): string[] {
  const base = normalize(query);
  if (!base) return [];
  const alt = normalize(convertKeyboardLayout(base));
  const set = new Set<string>([base]);
  if (alt && alt !== base) set.add(alt);
  return Array.from(set);
}

function getStorySearchText(story: Story): string {
  return [
    story.title,
    story.description,
    story.authorName,
    ...(story.tags ?? []),
    ...(story.genres ?? []),
  ]
    .join(' ')
    .toLowerCase();
}

export function storyMatchesQuery(story: Story, query: string): boolean {
  const variants = getSearchVariants(query);
  if (variants.length === 0) return true;
  const haystack = getStorySearchText(story);
  return variants.some((variant) => haystack.includes(variant));
}

export function rankStoryMatch(story: Story, query: string): number {
  const variants = getSearchVariants(query);
  if (variants.length === 0) return 0;
  const title = story.title.toLowerCase();
  const tags = [...(story.tags ?? []), ...(story.genres ?? [])]
    .join(' ')
    .toLowerCase();
  const description = story.description.toLowerCase();

  let score = 0;
  for (const variant of variants) {
    if (title.startsWith(variant)) score += 120;
    else if (title.includes(variant)) score += 80;
    if (tags.includes(variant)) score += 50;
    if (description.includes(variant)) score += 20;
  }
  return score;
}
