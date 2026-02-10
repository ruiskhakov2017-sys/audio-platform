'use client';

type Props = {
  tags: string[];
  selectedTags: string[];
  onToggle: (tag: string) => void;
  onClear: () => void;
  query: string;
  onQueryChange: (value: string) => void;
};

export default function TagPanel({
  tags,
  selectedTags,
  onToggle,
  onClear,
  query,
  onQueryChange,
}: Props) {
  return (
    <aside className="w-full max-w-xs space-y-4 rounded-2xl border border-white/10 bg-white/5 p-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-white">Tags</div>
        <button
          onClick={onClear}
          className="rounded-full border border-white/10 px-3 py-1 text-[10px] uppercase tracking-wider text-white/70 hover:border-white/30 hover:text-white"
        >
          Clear
        </button>
      </div>
      <input
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="Search tags"
        className="h-9 w-full rounded-full border border-white/10 bg-white/5 px-3 text-sm text-white/80 outline-none placeholder:text-white/40"
      />
      {selectedTags.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selectedTags.map((tag) => (
            <button
              key={tag}
              onClick={() => onToggle(tag)}
              className="rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs text-white"
            >
              {tag}
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {tags.map((tag) => {
          const active = selectedTags.includes(tag);
          return (
            <button
              key={tag}
              onClick={() => onToggle(tag)}
              className={`rounded-full border px-3 py-1 text-xs transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] ${
                active
                  ? 'border-white/40 bg-white/10 text-white shadow-[0_0_12px_rgba(168,85,247,0.3)]'
                  : 'border-white/10 text-white/60 hover:border-white/30 hover:text-white'
              }`}
            >
              {tag}
            </button>
          );
        })}
      </div>
    </aside>
  );
}
