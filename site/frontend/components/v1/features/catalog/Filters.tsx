'use client';

type Props = {
  value: 'all' | 'premium' | 'free';
  onChange: (value: 'all' | 'premium' | 'free') => void;
};

const options: Array<Props['value']> = ['all', 'premium', 'free'];

export default function Filters({ value, onChange }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {options.map((option) => {
        const active = option === value;
        return (
          <button
            key={option}
            onClick={() => onChange(option)}
            className={`rounded-full border px-4 py-2 text-xs font-semibold uppercase tracking-wider transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] ${
              active
                ? 'border-white/40 bg-white/10 text-white shadow-[0_0_16px_rgba(168,85,247,0.3)]'
                : 'border-white/10 text-white/60 hover:border-white/30 hover:text-white'
            }`}
          >
            {option}
          </button>
        );
      })}
    </div>
  );
}
