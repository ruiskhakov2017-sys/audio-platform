'use client';

export function StoryTileSkeleton() {
  return (
    <div className="rounded-2xl overflow-hidden border border-white/10 bg-white/5 animate-pulse">
      <div className="aspect-[3/4] bg-white/10" />
      <div className="p-4 space-y-2">
        <div className="h-4 bg-white/10 rounded w-3/4" />
        <div className="h-3 bg-white/10 rounded w-1/2" />
      </div>
    </div>
  );
}
